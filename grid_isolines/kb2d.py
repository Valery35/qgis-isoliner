# -*- coding: utf-8 -*-
#
# Isoliner — грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), — либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии — в файле LICENSE (на английском, юридически значим).
"""
KB2D engine — pure Python/NumPy, no QGIS dependency (headless-testable).

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


def krige_point(xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                ndmin, ndmax, rad2, nodata):
    """Point kriging at one location (Kriging2DInPoint, point-support)."""
    dx = xd - xloc
    dy = yd - yloc
    h2 = dx * dx + dy * dy
    order = np.argsort(h2)
    sel = order[h2[order] <= rad2][:ndmax]
    na = len(sel)
    if na < ndmin:
        return nodata

    xa = xd[sel]
    ya = yd[sel]
    vra = vrd[sel]
    h2sel = h2[sel]
    cbb = vg.maxcov                          # point support: C(0)

    # совпадение узла с пробой -> возвращаем значение пробы
    if h2sel[0] < EPS:
        return float(vra[0])

    if na == 1:
        cb = vg.cova2(xa[0] - xloc, ya[0] - yloc)
        if ktype == 0:                       # simple
            s = cb / cbb
            return s * vra[0] + (1.0 - s) * skmean
        return float(vra[0])                 # ordinary, single sample

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
    try:
        s = np.linalg.solve(A, r)
        if np.all(np.isfinite(s)):
            w = s[:na]
            e = float(np.dot(w, vra))
            if ktype == 0:                   # simple kriging mean term
                e += (1.0 - w.sum()) * skmean
            if math.isfinite(e) and lo <= e <= hi:
                est = e                      # принимаем только разумную оценку
    except np.linalg.LinAlgError:
        pass

    if est is None:                          # запас: обратные расстояния соседей
        wts = 1.0 / np.maximum(h2sel, EPS)
        est = float(np.dot(wts, vra) / wts.sum())
    return est


def build_grid(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
               rad2, nodata, xmn, ymn, cell, nx, ny, progress=None):
    """Sweep the grid, GSLIB order (north row first). Returns float32 (ny,nx)."""
    grid = np.full((ny, nx), nodata, dtype=np.float32)
    total = nx * ny
    done = 0
    for row in range(ny):                    # row 0 = north
        iy = ny - row                        # 1..ny counted from south
        yloc = ymn + (iy - 1) * cell
        for ix in range(nx):
            xloc = xmn + ix * cell
            grid[row, ix] = krige_point(
                xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                ndmin, ndmax, rad2, nodata)
            done += 1
        if progress is not None:
            progress(done, total)
    return grid
