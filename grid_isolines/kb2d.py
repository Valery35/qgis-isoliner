# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), - либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии - в файле LICENSE (на английском, юридически значим).
"""
KB2D engine - pure Python/NumPy, no QGIS dependency (headless-testable).

Faithful port of the ArcGIS Isoliner add-in 2D kriging core
(GlobalOperation.vb: cova2_2D / Kriging2DInPoint), itself a port of the
GSLIB kb2d routine.

GSLIB conventions are preserved:
  it (model)  : 1 spherical, 2 exponential, 3 gaussian, 4 power
  ktype       : 0 = simple kriging (uses skmean), 1 = ordinary kriging
  ang         : azimuth, clockwise degrees from Y (north)
  anis        : minor-axis range / major-axis range  (<=1)
"""
import math
import numpy as np

DTOR = math.pi / 180.0
EPS = 1e-11

# UI model codes (0-based) -> GSLIB it (1-based)
MODEL_SPHERICAL = 0
MODEL_EXPONENTIAL = 1
MODEL_GAUSSIAN = 2
MODEL_POWER = 3


class Variogram:
    """Nugget + one or more nested structures (cova2_2D port)."""

    def __init__(self, nugget, structures):
        # structures: list of dict(it=1..4, cc, aa, ang, anis)
        self.c0 = float(nugget)
        self.it = [int(s["it"]) for s in structures]
        self.cc = [float(s["cc"]) for s in structures]
        self.aa = [max(float(s["aa"]), EPS) for s in structures]
        self.ang = [float(s["ang"]) for s in structures]
        self.anis = [max(float(s["anis"]), EPS) for s in structures]
        self.nst = len(structures)
        self.pmx = 9999.0
        self.rotmat = []
        self.maxcov = self.c0
        for i in range(self.nst):
            az = (90.0 - self.ang[i]) * DTOR
            ca, sa = math.cos(az), math.sin(az)
            self.rotmat.append((ca, sa, -sa, ca))
            self.maxcov += self.pmx if self.it[i] == 4 else self.cc[i]

    def cova2(self, dx, dy):
        """Covariance for a separation vector (dx, dy)."""
        if (dx * dx + dy * dy) < EPS:
            return self.maxcov
        cov = 0.0
        for i in range(self.nst):
            r0, r1, r2, r3 = self.rotmat[i]
            dx1 = dx * r0 + dy * r1
            dy1 = (dx * r2 + dy * r3) / self.anis[i]
            h = math.sqrt(max(dx1 * dx1 + dy1 * dy1, 0.0))
            t = self.it[i]
            if t == 1:                       # spherical
                hr = h / self.aa[i]
                if hr < 1.0:
                    cov += self.cc[i] * (1.0 - hr * (1.5 - 0.5 * hr * hr))
            elif t == 2:                     # exponential
                cov += self.cc[i] * math.exp(-h / self.aa[i])
            elif t == 3:                     # gaussian
                cov += self.cc[i] * math.exp(-(h * h) / (self.aa[i] * self.aa[i]))
            else:                            # power
                cov += self.pmx - self.cc[i] * (h ** self.aa[i])
        return cov


def clip_outliers(values, vmin=None, vmax=None, pct=0.0, cap=False):
    """Отсев/срезка ураганных проб. Возвращает (values_out, keep_mask, lo, hi).

    Границы: явные vmin/vmax имеют приоритет; иначе - перцентильные [pct;
    100-pct] (если pct>0); иначе границ нет. Режимы:
      cap=False - отсеять: keep_mask=False у проб вне [lo; hi];
      cap=True  - срезать: значения вне [lo; hi] прижимаются к границе,
                  keep_mask остаётся True для всех.
    Универсальное применение: xs, ys, v = xs[keep], ys[keep], out[keep].
    """
    v = np.asarray(values, float)
    if vmin is None and vmax is None and (pct is None or pct <= 0):
        return v, np.ones(len(v), bool), float("-inf"), float("inf")
    lo, hi = float("-inf"), float("inf")
    if pct and pct > 0 and len(v):
        p = min(max(float(pct), 0.0), 49.0)
        lo = float(np.percentile(v, p))
        hi = float(np.percentile(v, 100.0 - p))
    if vmin is not None:
        lo = float(vmin)
    if vmax is not None:
        hi = float(vmax)
    if lo > hi:
        lo, hi = hi, lo
    if cap:
        return np.clip(v, lo, hi), np.ones(len(v), bool), lo, hi
    return v, (v >= lo) & (v <= hi), lo, hi


def _solve_point(xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                 ndmin, ndmax, rad2, nodata):
    """Точечный кригинг в одной точке. Возвращает (оценка, дисперсия).

    Дисперсия - это дисперсия ошибки кригинга:
        SK:  σ² = C(0) − Σ λ_i C(x_i, x0)
        OK:  σ² = C(0) − Σ λ_i C(x_i, x0) − μ   (μ - множитель Лагранжа)
    При совпадении узла с пробой σ² = 0. Если оценка не получена (вырожденная
    система) - дисперсия принимается равной априорной (силл). Когда соседей
    меньше ndmin, и оценка, и дисперсия = nodata."""
    dx = xd - xloc
    dy = yd - yloc
    h2 = dx * dx + dy * dy
    order = np.argsort(h2)
    sel = order[h2[order] <= rad2][:ndmax]
    na = len(sel)
    if na < ndmin:
        return nodata, nodata

    xa = xd[sel]
    ya = yd[sel]
    vra = vrd[sel]
    h2sel = h2[sel]
    cbb = vg.maxcov                          # point support: C(0)

    # совпадение узла с пробой -> возвращаем значение пробы, дисперсия 0
    if h2sel[0] < EPS:
        return float(vra[0]), 0.0

    if na == 1:
        cb = vg.cova2(xa[0] - xloc, ya[0] - yloc)
        if ktype == 0:                       # simple
            s = cb / cbb
            return s * vra[0] + (1.0 - s) * skmean, max(cbb - s * cb, 0.0)
        # ordinary, единственная проба: λ=1, μ=cb−C(0) -> σ²=2(C(0)−cb)
        return float(vra[0]), max(2.0 * (cbb - cb), 0.0)

    # допустимый разброс оценки (защита от «разлёта» весов)
    vmin = float(vra.min()); vmax = float(vra.max())
    span = (vmax - vmin) or (abs(vmax) + 1.0)
    lo, hi = vmin - 3.0 * span, vmax + 3.0 * span
    jitter = cbb * 1e-9                       # микро-регуляризация диагонали

    neq = na + ktype                         # +1 row for OK unbiasedness
    A = np.empty((neq, neq))
    r = np.empty(neq)
    for i in range(na):
        A[i, i] = cbb + jitter
        for j in range(i + 1, na):
            c = vg.cova2(xa[i] - xa[j], ya[i] - ya[j])
            A[i, j] = c
            A[j, i] = c
        r[i] = vg.cova2(xa[i] - xloc, ya[i] - yloc)
    if ktype == 1:                           # ordinary kriging
        A[na, :na] = vg.maxcov
        A[:na, na] = vg.maxcov
        A[na, na] = 0.0
        r[na] = vg.maxcov

    est = None
    var = None
    try:
        s = np.linalg.solve(A, r)
        if np.all(np.isfinite(s)):
            w = s[:na]
            e = float(np.dot(w, vra))
            if ktype == 0:                   # simple kriging mean term
                e += (1.0 - w.sum()) * skmean
            v = cbb - float(np.dot(w, r[:na]))
            if ktype == 1:                   # минус множитель Лагранжа
                v -= vg.maxcov * float(s[na])   # (масштабирован на C(0))
            if math.isfinite(e) and lo <= e <= hi:
                est = e                      # принимаем только разумную оценку
                var = max(v, 0.0)
    except np.linalg.LinAlgError:
        pass

    if est is None:                          # запас: обратные расстояния соседей
        wts = 1.0 / np.maximum(h2sel, EPS)
        est = float(np.dot(wts, vra) / wts.sum())
        var = cbb                            # априорная (максимальная) дисперсия
    return est, var


def krige_point(xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                ndmin, ndmax, rad2, nodata, return_var=False):
    """Точечный кригинг (обёртка). По умолчанию возвращает только оценку;
    при return_var=True - кортеж (оценка, дисперсия)."""
    est, var = _solve_point(xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                            ndmin, ndmax, rad2, nodata)
    return (est, var) if return_var else est


def build_grid(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
               rad2, nodata, xmn, ymn, cell, nx, ny, progress=None,
               with_variance=False):
    """Sweep the grid, GSLIB order (north row first). Returns float32 (ny,nx).

    При with_variance=True возвращает кортеж (оценка, стд.ошибка), где второй
    грид - стандартная ошибка кригинга = sqrt(дисперсия) (nodata там же, где
    nodata у оценки)."""
    grid = np.full((ny, nx), nodata, dtype=np.float32)
    sgrid = np.full((ny, nx), nodata, dtype=np.float32) if with_variance else None
    total = nx * ny
    done = 0
    for row in range(ny):                    # row 0 = north
        iy = ny - row                        # 1..ny counted from south
        yloc = ymn + (iy - 1) * cell
        for ix in range(nx):
            xloc = xmn + ix * cell
            e, v = _solve_point(
                xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                ndmin, ndmax, rad2, nodata)
            grid[row, ix] = e
            if with_variance and e != nodata:
                sgrid[row, ix] = math.sqrt(max(v, 0.0))
            done += 1
        if progress is not None:
            progress(done, total)
    return (grid, sgrid) if with_variance else grid
