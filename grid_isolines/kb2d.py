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

# Гауссова модель без наггета даёт плохо обусловленную систему (осцилляции,
# отрицательные веса). Держим минимальный наггет как долю структурного силла.
GAUSS_MIN_NUGGET_FRAC = 0.01


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
        # Гауссова модель численно неустойчива при нулевом наггете -
        # принудительно держим минимальный наггет (доля структурного силла).
        self.nugget_raised_from = None
        gauss_sill = sum(c for t, c in zip(self.it, self.cc) if t == 3)
        if gauss_sill > 0.0:
            min_c0 = GAUSS_MIN_NUGGET_FRAC * gauss_sill
            if self.c0 < min_c0:
                self.nugget_raised_from = self.c0
                self.c0 = min_c0
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

    def cova2_array(self, dx, dy):
        """Ковариация для массивов разностей (dx, dy) - векторный двойник cova2.

        Нужна для блочного кригинга, где на каждую ячейку приходится суммировать
        ковариацию по точкам дискретизации. Результат совпадает с cova2 поэлементно,
        включая значение maxcov при нулевой разности (точечная C(0))."""
        dx = np.asarray(dx, float)
        dy = np.asarray(dy, float)
        cov = np.zeros(np.broadcast(dx, dy).shape, float)
        for i in range(self.nst):
            r0, r1, r2, r3 = self.rotmat[i]
            dx1 = dx * r0 + dy * r1
            dy1 = (dx * r2 + dy * r3) / self.anis[i]
            h = np.sqrt(np.maximum(dx1 * dx1 + dy1 * dy1, 0.0))
            t = self.it[i]
            if t == 1:                       # spherical
                hr = h / self.aa[i]
                cov += np.where(hr < 1.0,
                                self.cc[i] * (1.0 - hr * (1.5 - 0.5 * hr * hr)),
                                0.0)
            elif t == 2:                     # exponential
                cov += self.cc[i] * np.exp(-h / self.aa[i])
            elif t == 3:                     # gaussian
                cov += self.cc[i] * np.exp(-(h * h) / (self.aa[i] * self.aa[i]))
            else:                            # power
                cov += self.pmx - self.cc[i] * (h ** self.aa[i])
        zero = (dx * dx + dy * dy) < EPS
        if np.any(zero):
            cov = np.where(zero, self.maxcov, cov)
        return cov


def block_offsets(cell, nxdis, nydis):
    """Смещения точек дискретизации блока относительно центра ячейки (GSLIB kb2d).

    Блок - квадратная ячейка грида размером cell. Делится на nxdis×nydis
    подъячеек, точки берутся в их центрах. Возвращает (bdx, bdy) - два массива
    длины nxdis*nydis. При 1×1 - единственная точка (0, 0): точечный кригинг."""
    nxdis = max(int(nxdis), 1)
    nydis = max(int(nydis), 1)
    xdis = cell / nxdis
    ydis = cell / nydis
    xs = (np.arange(nxdis) + 0.5) * xdis - 0.5 * cell
    ys = (np.arange(nydis) + 0.5) * ydis - 0.5 * cell
    gx, gy = np.meshgrid(xs, ys)
    return gx.ravel(), gy.ravel()


def block_block_cov(vg, bdx, bdy):
    """Средняя ковариация «блок-блок» Cbb - по всем парам точек дискретизации.

    Это дисперсионный член блочного кригинга: средняя точечная ковариация внутри
    блока. На диагонали (точка сама с собой) по правилу GSLIB вычитается наггет:
    короткомасштабный наггет внутри блока усредняется. Для блока 1×1 возвращает
    точечную C(0) = maxcov, и блочный кригинг вырождается в точечный."""
    ndb = len(bdx)
    if ndb <= 1:
        return vg.maxcov
    dx = bdx[:, None] - bdx[None, :]
    dy = bdy[:, None] - bdy[None, :]
    cov = vg.cova2_array(dx, dy)
    cov = cov - np.eye(ndb) * vg.c0          # снять наггет с диагонали (GSLIB)
    return float(cov.sum() / (ndb * ndb))


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
                 ndmin, ndmax, rad2, nodata, bdx=None, bdy=None, cbb=None):
    """Кригинг в одной точке (или в блоке). Возвращает (оценка, дисперсия).

    Точечный кригинг (bdx=None или один узел дискретизации) воспроизводит пробу
    в совпадающем узле и даёт нулевую дисперсию. Блочный кригинг (bdx/bdy -
    смещения точек дискретизации от центра ячейки, cbb - блок-блок ковариация)
    оценивает СРЕДНЕЕ по блоку: правые ковариации усредняются по точкам блока,
    дисперсионный член - блочный (cbb < C(0)). Блок не воспроизводит пробы точно,
    зато дисперсия ниже точечной - это и есть смысл оценки запасов по блоку.

    Дисперсия - это дисперсия ошибки кригинга:
        SK:  σ² = Cbb − Σ λ_i C(x_i, блок)
        OK:  σ² = Cbb − Σ λ_i C(x_i, блок) − μ   (μ - множитель Лагранжа)
    Cbb = C(0) для точки, средняя внутриблочная ковариация для блока. Если оценка
    не получена (вырожденная система) - дисперсия принимается равной Cbb. Когда
    соседей меньше ndmin, и оценка, и дисперсия = nodata."""
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
    c0pt = vg.maxcov                         # точечная C(0): диагональ данных
    block = bdx is not None and len(bdx) > 1
    if cbb is None:                          # дисперсионный член
        cbb = c0pt                           # точка по умолчанию

    # точечный кригинг: совпадение узла с пробой -> значение пробы, дисперсия 0
    if not block and h2sel[0] < EPS:
        return float(vra[0]), 0.0

    # правые ковариации: точка-точка либо среднее точка-блок
    if block:
        bx = xloc + bdx
        by = yloc + bdy
        ddx = xa[:, None] - bx[None, :]
        ddy = ya[:, None] - by[None, :]
        cb_arr = vg.cova2_array(ddx, ddy)
        coin = (ddx * ddx + ddy * ddy) < EPS
        if np.any(coin):                     # проба на узле блока: снять наггет
            cb_arr = np.where(coin, cb_arr - vg.c0, cb_arr)
        rhs = cb_arr.mean(axis=1)
    else:
        rhs = np.array([vg.cova2(xa[i] - xloc, ya[i] - yloc) for i in range(na)])

    if na == 1:
        cb = float(rhs[0])
        if ktype == 0:                       # simple
            s = cb / c0pt
            return s * vra[0] + (1.0 - s) * skmean, max(cbb - s * cb, 0.0)
        # ordinary, единственная проба: λ=1, μ=cb−C(0) -> σ²=Cbb−2cb+C(0)
        return float(vra[0]), max(cbb - 2.0 * cb + c0pt, 0.0)

    # допустимый разброс оценки (защита от «разлёта» весов)
    vmin = float(vra.min()); vmax = float(vra.max())
    span = (vmax - vmin) or (abs(vmax) + 1.0)
    lo, hi = vmin - 3.0 * span, vmax + 3.0 * span
    jitter = c0pt * 1e-9                       # микро-регуляризация диагонали

    neq = na + ktype                         # +1 row for OK unbiasedness
    A = np.empty((neq, neq))
    r = np.empty(neq)
    for i in range(na):
        A[i, i] = c0pt + jitter
        for j in range(i + 1, na):
            c = vg.cova2(xa[i] - xa[j], ya[i] - ya[j])
            A[i, j] = c
            A[j, i] = c
        r[i] = rhs[i]
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


def cross_validate(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
                   rad2, nodata, progress=None):
    """Скользящий контроль (leave-one-out). Для каждой точки оценка строится
    по всем ОСТАЛЬНЫМ точкам, затем сравнивается с фактическим значением.
    Возвращает (est, var) той же длины, что и входные данные (nodata там, где
    оценка не получена). Это основа подбора вариограммы по ошибке."""
    n = len(xd)
    est = np.full(n, nodata, float)
    var = np.full(n, nodata, float)
    keep = np.ones(n, bool)
    for i in range(n):
        keep[i] = False                      # исключаем саму точку
        e, v = _solve_point(xd[i], yd[i], xd[keep], yd[keep], vrd[keep],
                            vg, ktype, skmean, ndmin, ndmax, rad2, nodata)
        keep[i] = True
        est[i] = e
        var[i] = v
        if progress is not None and (i & 255) == 0:
            progress(i, n)
    return est, var


def cross_validate_detrend(xd, yd, vrd, degree, vg, ktype, skmean,
                           ndmin, ndmax, rad2, nodata, progress=None):
    """Скользящий контроль для регрессии-кригинга. На каждом шаге исключённая
    точка не участвует в подборе тренда: тренд переподбирается по n-1 точкам,
    кригуются их остатки, тренд добавляется к оценке обратно. Так LOO остаётся
    добросовестным, без утечки исключённой точки ни в тренд, ни в кригинг.

    Вариограмма vg задаётся по остаткам (фиксирована). Возвращает (est, var) в
    исходных единицах значения: var - дисперсия ошибки кригинга остатков, тренд
    детерминирован и своей погрешности не добавляет. PolyTrend берётся при
    вызове (определён ниже в модуле)."""
    n = len(xd)
    est = np.full(n, nodata, float)
    var = np.full(n, nodata, float)
    keep = np.ones(n, bool)
    for i in range(n):
        keep[i] = False
        xi, yi, zi = xd[keep], yd[keep], vrd[keep]
        tr = PolyTrend.fit(xi, yi, zi, degree)
        ri = zi - tr(xi, yi)
        e, v = _solve_point(xd[i], yd[i], xi, yi, ri, vg, ktype, skmean,
                            ndmin, ndmax, rad2, nodata)
        keep[i] = True
        if e != nodata:
            est[i] = float(tr(xd[i:i + 1], yd[i:i + 1])[0]) + e
            var[i] = v
        if progress is not None and (i & 255) == 0:
            progress(i, n)
    return est, var


def build_grid(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
               rad2, nodata, xmn, ymn, cell, nx, ny, progress=None,
               with_variance=False, ndisc=1):
    """Sweep the grid, GSLIB order (north row first). Returns float32 (ny,nx).

    При with_variance=True возвращает кортеж (оценка, стд.ошибка), где второй
    грид - стандартная ошибка кригинга = sqrt(дисперсия) (nodata там же, где
    nodata у оценки).

    ndisc - дискретизация блока N×N на ячейку: 1 (по умолчанию) - точечный
    кригинг, >1 - блочный (оценка среднего по ячейке, дисперсия блочная). Блок
    равен ячейке грида; смещения дискретизации и блок-блок ковариация считаются
    один раз до прохода (вариограмма стационарна)."""
    grid = np.full((ny, nx), nodata, dtype=np.float32)
    sgrid = np.full((ny, nx), nodata, dtype=np.float32) if with_variance else None
    bdx = bdy = None
    cbb = None
    if ndisc and int(ndisc) > 1:
        bdx, bdy = block_offsets(cell, int(ndisc), int(ndisc))
        cbb = block_block_cov(vg, bdx, bdy)
    total = nx * ny
    done = 0
    for row in range(ny):                    # row 0 = north
        iy = ny - row                        # 1..ny counted from south
        yloc = ymn + (iy - 1) * cell
        for ix in range(nx):
            xloc = xmn + ix * cell
            e, v = _solve_point(
                xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                ndmin, ndmax, rad2, nodata, bdx=bdx, bdy=bdy, cbb=cbb)
            grid[row, ix] = e
            if with_variance and e != nodata:
                sgrid[row, ix] = math.sqrt(max(v, 0.0))
            done += 1
        if progress is not None:
            progress(done, total)
    return (grid, sgrid) if with_variance else grid


# ===========================================================================
#  Экспериментальная вариограмма и подбор модели (чистый NumPy, без QGIS).
#  Параметризация моделей совпадает с cova2 выше: радиус a здесь - это тот же
#  параметр aa структуры, поэтому подобранный a подставляется в кригинг 1:1.
# ===========================================================================
def variogram_shape(model, h, a):
    """Форма вариограммы γ/C (от 0 до 1) для одной структуры, без наггета.

    model: 0 сферическая, 1 экспоненциальная, 2 гауссова (как MODEL_*).
    Совпадает с cova2: для сферической при h>=a даёт 1; для экспоненциальной
    1-exp(-h/a); для гауссовой 1-exp(-(h/a)^2). Параметр a = aa структуры.
    """
    h = np.asarray(h, float)
    a = max(float(a), EPS)
    hr = h / a
    if model == MODEL_SPHERICAL:
        s = 1.5 * hr - 0.5 * hr ** 3
        return np.where(hr < 1.0, s, 1.0)
    if model == MODEL_EXPONENTIAL:
        return 1.0 - np.exp(-hr)
    if model == MODEL_GAUSSIAN:
        return 1.0 - np.exp(-(hr * hr))
    raise ValueError("fit поддерживает только модели 0/1/2 (sph/exp/gauss)")


def experimental_variogram(xs, ys, vs, n_lags=15, maxlag=None, robust=False,
                           max_pairs=6_000_000, cloud_max=20000, seed=0):
    """Омнинаправленная экспериментальная полувариограмма (оценка Матерона).

    Делит расстояния между всеми парами на n_lags интервалов до maxlag и
    усредняет полудисперсию 0.5*(z_i - z_j)^2 по каждому интервалу.
      maxlag=None/<=0 -> половина диагонали охвата (классический ориентир);
      robust=True     -> устойчивая оценка Кресси-Хокинса (гасит выбросы);
      max_pairs       -> при превышении точки прореживаются (пар ~ n^2/2),
                         чтобы расчёт оставался быстрым;
      cloud_max       -> сколько пар вернуть для облака рассеяния (предпросмотр).
    Возвращает dict: lag, gamma, npairs (по непустым лагам), maxlag, width,
    n_used, subsampled, cloud_h, cloud_g (полудисперсия пары).
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = len(xs)
    rng = np.random.default_rng(seed)
    subsampled = False
    if n > 2 and n * (n - 1) // 2 > max_pairs:
        m = int((1.0 + math.sqrt(1.0 + 8.0 * max_pairs)) / 2.0)
        m = max(2, min(n, m))
        idx = rng.choice(n, m, replace=False)
        xs, ys, vs = xs[idx], ys[idx], vs[idx]
        n = m
        subsampled = True

    if maxlag is None or maxlag <= 0:
        dx = float(xs.max() - xs.min())
        dy = float(ys.max() - ys.min())
        maxlag = 0.5 * math.hypot(dx, dy)
    if maxlag <= 0:
        maxlag = 1.0
    n_lags = max(int(n_lags), 1)
    width = maxlag / n_lags

    cnt = np.zeros(n_lags)
    s_h = np.zeros(n_lags)
    s_d2 = np.zeros(n_lags)
    s_sqrt = np.zeros(n_lags)
    cloud_h, cloud_g = [], []
    budget = int(cloud_max)

    for i in range(n - 1):
        hx = xs[i + 1:] - xs[i]
        hy = ys[i + 1:] - ys[i]
        h = np.sqrt(hx * hx + hy * hy)
        d = vs[i + 1:] - vs[i]
        sel = (h > 0.0) & (h <= maxlag)
        if not sel.any():
            continue
        h = h[sel]
        d = d[sel]
        b = np.minimum((h / width).astype(np.intp), n_lags - 1)
        np.add.at(cnt, b, 1.0)
        np.add.at(s_h, b, h)
        np.add.at(s_d2, b, d * d)
        if robust:
            np.add.at(s_sqrt, b, np.sqrt(np.abs(d)))
        if budget > 0:
            k = len(h)
            take = min(k, budget)
            if take < k:
                pick = rng.choice(k, take, replace=False)
                cloud_h.append(h[pick]); cloud_g.append(0.5 * d[pick] * d[pick])
            else:
                cloud_h.append(h); cloud_g.append(0.5 * d * d)
            budget -= take

    safe = np.maximum(cnt, 1.0)
    lag = np.where(cnt > 0, s_h / safe, (np.arange(n_lags) + 0.5) * width)
    if robust:
        mean_sqrt = np.where(cnt > 0, s_sqrt / safe, 0.0)
        denom = 0.457 + 0.494 / safe + 0.045 / (safe * safe)
        gamma = 0.5 * (mean_sqrt ** 4) / denom
    else:
        gamma = 0.5 * np.where(cnt > 0, s_d2 / safe, np.nan)

    valid = cnt > 0
    return {
        "lag": lag[valid],
        "gamma": gamma[valid],
        "npairs": cnt[valid].astype(int),
        "maxlag": float(maxlag),
        "width": float(width),
        "n_used": int(n),
        "subsampled": bool(subsampled),
        "cloud_h": (np.concatenate(cloud_h) if cloud_h else np.array([])),
        "cloud_g": (np.concatenate(cloud_g) if cloud_g else np.array([])),
    }


def _wls_c0_c(s, y, w):
    """Взвешенный МНК для γ = c0 + C*s при фикс. форме s, c0>=0, C>=0.
    Возвращает (c0, C, sse)."""
    sw = float(np.sum(w))
    ss = float(np.sum(w * s))
    sss = float(np.sum(w * s * s))
    sy = float(np.sum(w * y))
    ssy = float(np.sum(w * s * y))
    det = sw * sss - ss * ss
    c0 = c = 0.0
    if abs(det) > 1e-30:
        c0 = (sy * sss - ss * ssy) / det
        c = (sw * ssy - ss * sy) / det
    if c0 < 0.0 or c < 0.0 or abs(det) <= 1e-30:
        # граница допустимой области: перебираем варианты с занулением
        cands = []
        # c0=0 -> C по МНК
        cc = ssy / sss if sss > 1e-30 else 0.0
        cands.append((0.0, max(cc, 0.0)))
        # C=0 -> c0 = взвешенное среднее
        cands.append((sy / sw if sw > 0 else 0.0, 0.0))
        # оба >=0 (если вышло)
        if c0 >= 0.0 and c >= 0.0:
            cands.append((c0, c))
        best = None
        for a0, a1 in cands:
            r = y - a0 - a1 * s
            e = float(np.sum(w * r * r))
            if best is None or e < best[2]:
                best = (a0, a1, e)
        return best
    r = y - c0 - c * s
    return c0, c, float(np.sum(w * r * r))


def fit_variogram(lag, gamma, npairs, model="auto", n_a=80, sill_cap=None):
    """Подбор модели вариограммы по экспериментальным точкам (рекомендация).

    γ(h) = c0 + C*shape(h; a). При фиксированном a задача линейна по (c0, C),
    поэтому a сканируется по сетке (лог-шаг) с локальным уточнением, а (c0, C)
    на каждом шаге решаются взвешенным МНК (веса = число пар в лаге; так точнее
    оценённые лаги весомее). Без scipy/skgstat.

    Плато модели c0+C ограничивается сверху (sill_cap), чтобы модель не
    «убегала» в несуществующий силл за счёт длинного радиуса и экстраполяции
    выше наблюдённой вариограммы. По умолчанию cap = 1.15*max(γ). Благодаря
    этому auto не выбирает гауссову там, где она выигрывает лишь экстраполяцией.

    model: 'auto' (выбрать sph/exp/gauss по лучшему R^2) либо 0/1/2.
    Возвращает dict: model, nugget, sill, range, r2, npts. None при нехватке точек.
    """
    lag = np.asarray(lag, float)
    gamma = np.asarray(gamma, float)
    w = np.asarray(npairs, float)
    ok = np.isfinite(lag) & np.isfinite(gamma) & (lag > 0)
    lag, gamma, w = lag[ok], gamma[ok], np.maximum(w[ok], 1.0)
    if len(lag) < 3:
        return None
    if sill_cap is None:
        sill_cap = 1.15 * float(np.max(gamma))

    L = float(lag.max())
    a_lo = max(0.05 * L, float(lag.min()) * 0.5, EPS)
    a_hi = 1.5 * L
    grid = np.geomspace(a_lo, a_hi, max(int(n_a), 8))
    models = [MODEL_SPHERICAL, MODEL_EXPONENTIAL, MODEL_GAUSSIAN] \
        if model == "auto" else [int(model)]

    sw = float(np.sum(w))
    ybar = float(np.sum(w * gamma) / sw)
    sst = float(np.sum(w * (gamma - ybar) ** 2)) or 1.0

    def _eval(m, a):
        s = variogram_shape(m, lag, a)
        c0, c, sse = _wls_c0_c(s, gamma, w)
        total = c0 + c
        if sill_cap and total > sill_cap and total > 0:
            f = sill_cap / total                 # прижать плато к cap
            c0, c = c0 * f, c * f
            r = gamma - c0 - c * s
            sse = float(np.sum(w * r * r))
        return c0, c, sse

    best = None
    for m in models:
        coarse = [(a, _eval(m, a)) for a in grid]
        a_star, (c0, c, sse) = min(coarse, key=lambda t: t[1][2])
        j = [i for i, (a, _) in enumerate(coarse) if a == a_star][0]
        alo = coarse[max(j - 1, 0)][0]
        ahi = coarse[min(j + 1, len(coarse) - 1)][0]
        for _ in range(40):
            a1 = alo * (ahi / alo) ** (1.0 / 3.0)
            a2 = alo * (ahi / alo) ** (2.0 / 3.0)
            if _eval(m, a1)[2] < _eval(m, a2)[2]:
                ahi = a2
            else:
                alo = a1
            if ahi / alo < 1.0001:
                break
        a_ref = math.sqrt(alo * ahi)
        c0, c, sse = _eval(m, a_ref)
        r2 = 1.0 - sse / sst
        cand = {"model": m, "nugget": float(max(c0, 0.0)),
                "sill": float(max(c, 0.0)), "range": float(a_ref),
                "r2": float(r2), "npts": int(len(lag))}
        if best is None or cand["r2"] > best["r2"]:
            best = cand
    return best


def model_curve(vg, hmax, ndir=None, npts=120):
    """Кривая γ(h) заданной модели для наложения на экспериментальную.

    Возвращает (h, gamma_major[, gamma_minor]). По умолчанию вдоль главной оси
    первой структуры (азимут структуры 1); если анизотропия != 1, второй
    кривой добавляется малая ось - так видно обе ветви. Изотропная модель даёт
    одну кривую (ветви совпадают, малая не возвращается).
    """
    h = np.linspace(0.0, float(hmax), int(npts))
    az = vg.ang[0] if vg.nst else 0.0
    ar = az * DTOR
    ux, uy = math.sin(ar), math.cos(ar)          # главная ось (азимут от севера)
    g_major = np.array([vg.maxcov - vg.cova2(hi * ux, hi * uy) for hi in h])
    anis_min = min(vg.anis) if vg.nst else 1.0
    if anis_min < 0.999:
        vx, vy = math.cos(ar), -math.sin(ar)     # перпендикуляр (малая ось)
        g_minor = np.array([vg.maxcov - vg.cova2(hi * vx, hi * vy) for hi in h])
        return h, g_major, g_minor
    return h, g_major


# ===========================================================================
#  Вариограммная карта (поверхность) и оценка анизотропии
# ===========================================================================
def _vmap_bilinear(grid, cell, n_bins, hx, hy):
    """Билинейная выборка карты в точке лага (hx, hy). NaN вне диапазона.
    Если часть углов пуста - среднее по валидным углам."""
    size = grid.shape[0]
    fx = hx / cell + n_bins
    fy = hy / cell + n_bins
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    if x0 < 0 or x0 + 1 >= size or y0 < 0 or y0 + 1 >= size:
        return float("nan")
    tx, ty = fx - x0, fy - y0
    v00, v10 = grid[y0, x0], grid[y0, x0 + 1]
    v01, v11 = grid[y0 + 1, x0], grid[y0 + 1, x0 + 1]
    corners = [v00, v10, v01, v11]
    if not all(np.isfinite(c) for c in corners):
        valid = [c for c in corners if np.isfinite(c)]
        return float(np.mean(valid)) if valid else float("nan")
    a = v00 * (1 - tx) + v10 * tx
    b = v01 * (1 - tx) + v11 * tx
    return float(a * (1 - ty) + b * ty)


def _estimate_anisotropy(grid, cell, n_bins, sill, maxlag, n_az=36,
                         reach=0.95):
    """Главная ось и коэффициент анизотропии по направленным радиусам.
    Азимут геогр. (0=С, по часовой); направление (E=x, N=y) = (sin A, cos A).
    Радиус - лаг, где γ впервые достигает reach*sill (иначе maxlag). Радиусы
    сглаживаются по азимуту (период 180°), главная ось = макс. сглаженного
    радиуса, малая ось берётся ПЕРПЕНДИКУЛЯРНО главной (устойчивее минимума).
    Возвращает ..., capped: главная ось упёрлась в окно (γ не вышла на полку)."""
    if not (sill > 0):
        return 0.0, 1.0, maxlag, maxlag, [], False, False
    target = reach * sill
    step = max(cell * 0.5, maxlag / 200.0)
    n_az = int(n_az)
    raw = []
    reached = []                                 # дошла ли γ до полки в окне
    for k in range(n_az):
        a_deg = 180.0 * k / n_az
        a = math.radians(a_deg)
        ux, uy = math.sin(a), math.cos(a)
        rng_found = maxlag
        hit = False
        r = step
        while r <= maxlag:
            g = _vmap_bilinear(grid, cell, n_bins, r * ux, r * uy)
            if np.isfinite(g) and g >= target:
                rng_found = r
                hit = True
                break
            r += step
        raw.append(rng_found)
        reached.append(hit)
    raw = np.asarray(raw, float)
    # циклическое сглаживание (период n_az), окно ~ n_az/12
    w = max(1, n_az // 12)
    sm = np.array([raw[(np.arange(k - w, k + w + 1)) % n_az].mean()
                   for k in range(n_az)])
    k_major = int(np.argmax(sm))
    r_major = float(sm[k_major])
    k_minor = (k_major + n_az // 2) % n_az      # перпендикуляр
    r_minor = float(sm[k_minor])
    az_major = 180.0 * k_major / n_az
    ranges = [(180.0 * k / n_az, float(raw[k])) for k in range(n_az)]
    # структура разрешима, только если главный радиус заметно больше ячейки;
    # иначе радиусы «на уровне сетки» и анизотропия недостоверна (шум/нет
    # структуры) - корректнее сообщить «изотропно / не определено».
    resolved = r_major >= 3.0 * cell
    if not resolved:
        return 0.0, 1.0, r_major, r_major, ranges, False, False
    # радиус упёрся в край окна, если вдоль главной оси γ до полки не дошла:
    # порог получен экстраполяцией, радиус - нижняя оценка, анизотропия по
    # выраженности занижена (истинный r_major может быть больше окна).
    capped = not bool(reached[k_major])
    anis = (r_minor / r_major) if r_major > 0 else 1.0
    return (float(az_major), float(min(max(anis, 1e-3), 1.0)),
            r_major, r_minor, ranges, True, capped)


def variogram_map(xs, ys, vs, n_bins=15, maxlag=None, min_pairs=5,
                  max_pairs=4_000_000, seed=0, n_az=36):
    """Вариограммная карта γ(h_x, h_y) и оценка анизотропии.

    Для всех пар берётся вектор разноса (dx, dy) и полудисперсия
    0.5*(z_i - z_j)^2; усредняется по 2D-сетке лагов в [-maxlag; maxlag].
    Карта симметрична (учитываем пару и зеркало). Возвращает dict: grid
    (квадрат 2*n_bins+1, NaN в пустых), counts, cell, extent (=maxlag), sill,
    azimuth (геогр.), anis (0..1), range_major/minor, range_capped (радиус
    упёрся в окно), n_used, subsampled."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = len(xs)
    rng = np.random.default_rng(seed)
    subsampled = False
    if n > 2 and n * (n - 1) // 2 > max_pairs:
        m = int((1.0 + math.sqrt(1.0 + 8.0 * max_pairs)) / 2.0)
        m = max(2, min(n, m))
        idx = rng.choice(n, m, replace=False)
        xs, ys, vs = xs[idx], ys[idx], vs[idx]
        n = m
        subsampled = True

    if maxlag is None or maxlag <= 0:
        dx = float(xs.max() - xs.min())
        dy = float(ys.max() - ys.min())
        maxlag = 0.5 * math.hypot(dx, dy)
    if maxlag <= 0:
        maxlag = 1.0
    n_bins = max(int(n_bins), 1)
    cell = maxlag / n_bins
    size = 2 * n_bins + 1
    s_g = np.zeros((size, size))
    s_c = np.zeros((size, size))

    for i in range(n - 1):
        dx = xs[i + 1:] - xs[i]
        dy = ys[i + 1:] - ys[i]
        g = 0.5 * (vs[i + 1:] - vs[i]) ** 2
        for sx, sy in ((dx, dy), (-dx, -dy)):
            ix = np.round(sx / cell).astype(int) + n_bins
            iy = np.round(sy / cell).astype(int) + n_bins
            ok = (ix >= 0) & (ix < size) & (iy >= 0) & (iy < size)
            np.add.at(s_g, (iy[ok], ix[ok]), g[ok])
            np.add.at(s_c, (iy[ok], ix[ok]), 1.0)

    grid = np.full((size, size), np.nan)
    mask = s_c >= float(min_pairs)
    grid[mask] = s_g[mask] / s_c[mask]
    grid[n_bins, n_bins] = 0.0

    sill = float(np.var(vs)) if n > 1 else 0.0
    az, anis, rmaj, rmin, dranges, resolved, capped = _estimate_anisotropy(
        grid, cell, n_bins, sill, maxlag, n_az)

    return {"grid": grid, "counts": s_c, "cell": cell, "extent": maxlag,
            "sill": sill, "azimuth": az, "anis": anis, "resolved": resolved,
            "range_major": rmaj, "range_minor": rmin, "dir_ranges": dranges,
            "range_capped": capped,
            "n_used": n, "subsampled": subsampled, "maxlag": maxlag,
            "n_bins": n_bins}


# ===========================================================================
#  Полиномиальный тренд (регрессия-кригинг)
# ===========================================================================
def data_warnings(xs, ys, vs, min_points=8):
    """Дешёвые проверки кондиционности входных точек перед кригингом. Возвращает
    список кодов с деталью: каждый элемент это (код, значение). Сообщения и их
    перевод формируются на стороне алгоритмов, здесь только обнаружение.

    Коды:
      "few_points" - точек меньше порога, оценка неустойчива (деталь n);
      "duplicates" - есть точки с совпадающими координатами, частая причина
                     вырожденной матрицы (деталь - число лишних совпадений);
      "constant"   - все значения одинаковы, кригинг вырождается (деталь None).
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = len(vs)
    out = []
    if n < int(min_points):
        out.append(("few_points", n))
    if n > 1:
        uniq = len(set(zip(xs.tolist(), ys.tolist())))
        dup = n - uniq
        if dup > 0:
            out.append(("duplicates", dup))
    if n > 0 and float(np.ptp(vs)) == 0.0:
        out.append(("constant", None))
    return out


def _auto_indicator_variogram(xd, yd, ind, n_lags=15, maxlag=None):
    """Авто-вариограмма индикатора 0/1: сферическая модель по экспериментальной.
    Сферическая, потому что индикаторные вариограммы устойчивее и не уводят в
    численно капризную гауссову."""
    ev = experimental_variogram(xd, yd, ind, n_lags=n_lags, maxlag=maxlag,
                                robust=False)
    fit = fit_variogram(ev["lag"], ev["gamma"], ev["npairs"], model=0)
    if fit is None:
        p = float(np.mean(ind))
        sill = max(p * (1.0 - p), 1e-6)
        aa = max((ev.get("maxlag") or 1.0) * 0.25, EPS)
        return Variogram(0.0, [{"it": 1, "cc": sill, "aa": aa,
                                "ang": 0.0, "anis": 1.0}])
    return Variogram(fit["nugget"], [{"it": fit["model"] + 1,
            "cc": fit["sill"], "aa": fit["range"], "ang": 0.0, "anis": 1.0}])


def categorical_indicator_grids(xd, yd, labels, classes, xmn, ymn, cell, nx, ny,
                                ndmin=4, ndmax=24, radius=None, nodata=-9999.0,
                                models=None, progress=None):
    """Категориальный индикаторный кригинг.

    На каждый класс из classes строит индикатор 0/1, кригует ординарным
    кригингом (ktype=0) и обрезает оценку в [0, 1]. Затем вероятности по классам
    нормируются к сумме 1 в каждой ячейке. Кодом класса НЕ кригуем: у категорий
    нет порядка, поэтому только раздельные индикаторы.

    Возвращает (probs, zone, conf):
      probs - float32 ny×nx×K, нормированные вероятности (nodata вне области),
      zone  - int32 ny×nx, индекс самого вероятного класса (-1 = нет оценки),
      conf  - float32 ny×nx, максимум нормированной вероятности (nodata вне).

    radius - радиус поиска (по умолчанию по размеру грида). models - список
    готовых Variogram по классам или None (тогда авто-подбор по индикатору)."""
    xd = np.asarray(xd, float)
    yd = np.asarray(yd, float)
    labels = np.asarray(labels, dtype=object)
    K = len(classes)
    if not radius or radius <= 0:
        radius = max(nx * cell, ny * cell)
    rad2 = float(radius) * float(radius)
    raw = np.empty((ny, nx, K), dtype=np.float32)
    for k, c in enumerate(classes):
        ind = (labels == c).astype(float)
        vg = (models[k] if (models and k < len(models) and models[k] is not None)
              else _auto_indicator_variogram(xd, yd, ind))
        prog = (lambda d, t, _k=k: progress(_k, K, d, t)) if progress else None
        raw[:, :, k] = build_grid(xd, yd, ind, vg, 0, 0.0, ndmin, ndmax, rad2,
                                  nodata, xmn, ymn, cell, nx, ny, progress=prog)
    valid = np.all(raw != nodata, axis=2)
    clipped = np.clip(raw, 0.0, 1.0)
    s = clipped.sum(axis=2, keepdims=True)
    s[s == 0] = 1.0
    probs = (clipped / s).astype(np.float32)
    am = np.argmax(probs, axis=2)
    zone = np.full((ny, nx), -1, dtype=np.int32)
    zone[valid] = am[valid].astype(np.int32)
    conf = np.where(valid, probs.max(axis=2), nodata).astype(np.float32)
    probs[~valid] = nodata
    return probs, zone, conf


class PolyTrend:
    """Полиномиальный тренд m(x, y) степени 1 или 2, подобранный МНК.

    Назначение - снять региональную составляющую перед кригингом. При наличии
    тренда (падение пласта, общий уклон поля) экспериментальная вариограмма
    сырого значения раздувается: радиус завышен, порога нет, форма как у
    степенной модели. Снятие тренда возвращает вариограмму остатков к виду с
    наггетом и порогом, кригинг идёт по остаткам, а тренд добавляется к оценке
    обратно:  оценка = m(x, y) + кригинг_остатков(x, y).

    Координаты центрируются и масштабируются на собственное стандартное
    отклонение, иначе матрица плана для степени 2 плохо обусловлена в метровых
    координатах. Чистый NumPy, как и всё ядро.

    Степень 1: m = b0 + b1*x + b2*y.
    Степень 2: m = b0 + b1*x + b2*y + b3*x^2 + b4*x*y + b5*y^2.
    """

    def __init__(self, beta, x0, y0, sx, sy, degree):
        self.beta = np.asarray(beta, float)
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.sx = float(sx) or 1.0
        self.sy = float(sy) or 1.0
        self.degree = int(degree)

    @staticmethod
    def n_terms(degree):
        return 3 if int(degree) == 1 else 6

    def _design(self, x, y):
        X = (np.asarray(x, float) - self.x0) / self.sx
        Y = (np.asarray(y, float) - self.y0) / self.sy
        cols = [np.ones_like(X), X, Y]
        if self.degree >= 2:
            cols += [X * X, X * Y, Y * Y]
        return np.column_stack(cols)

    @classmethod
    def fit(cls, x, y, z, degree):
        """Подбор тренда по точкам. degree приводится к 1 или 2."""
        degree = 2 if int(degree) >= 2 else 1
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        z = np.asarray(z, float)
        x0, y0 = float(np.mean(x)), float(np.mean(y))
        sx = float(np.std(x)) or 1.0
        sy = float(np.std(y)) or 1.0
        self = cls(np.zeros(cls.n_terms(degree)), x0, y0, sx, sy, degree)
        B = self._design(x, y)
        beta, *_ = np.linalg.lstsq(B, z, rcond=None)
        self.beta = beta
        return self

    def __call__(self, x, y):
        """Значение тренда в точке/массиве точек."""
        return self._design(x, y) @ self.beta

    def residuals(self, x, y, z):
        return np.asarray(z, float) - self(x, y)

class ExternalDrift:
    """Линейный дрейф по внешней переменной s, известной всюду (растр).

    Брат PolyTrend для кригинга с внешним дрейфом (External Drift). Если поле
    закономерно связано с уже известной всюду величиной s (соседний пласт,
    структурная поверхность, сейсмический атрибут, грубая модель), эту связь
    снимают регрессией перед кригингом. Дальше кригуются остатки, а дрейф
    добавляется к оценке обратно из растра s:
        оценка = m(s) + кригинг_остатков.

    Дрейф здесь не функция координат, как у PolyTrend, а функция стороннего
    значения s в той же точке. Математика кригинга при этом не меняется: ровно
    та же схема регрессия-кригинг, что и у снятия полиномиального тренда.

    Степень 1: m = a0 + a1*s (линейный дрейф, обычный выбор для External Drift).
    Степень 2: m = a0 + a1*s + a2*s^2 (если связь явно изогнута).

    s центрируется и масштабируется на своё стандартное отклонение, иначе план
    для степени 2 плохо обусловлен. Чистый NumPy, как и всё ядро.
    """

    def __init__(self, beta, s0, ss, degree):
        self.beta = np.asarray(beta, float)
        self.s0 = float(s0)
        self.ss = float(ss) or 1.0
        self.degree = int(degree)

    @staticmethod
    def n_terms(degree):
        return 2 if int(degree) == 1 else 3

    def _design(self, s):
        S = (np.asarray(s, float) - self.s0) / self.ss
        cols = [np.ones_like(S), S]
        if self.degree >= 2:
            cols.append(S * S)
        return np.column_stack(cols)

    @classmethod
    def fit(cls, s, z, degree):
        """Подбор дрейфа по точкам (s, z). degree приводится к 1 или 2."""
        degree = 2 if int(degree) >= 2 else 1
        s = np.asarray(s, float)
        z = np.asarray(z, float)
        s0 = float(np.mean(s))
        ss = float(np.std(s)) or 1.0
        self = cls(np.zeros(cls.n_terms(degree)), s0, ss, degree)
        B = self._design(s)
        beta, *_ = np.linalg.lstsq(B, z, rcond=None)
        self.beta = beta
        return self

    def __call__(self, s):
        """Значение дрейфа по внешнему значению s (точка или массив)."""
        return self._design(s) @ self.beta

    def residuals(self, s, z):
        return np.asarray(z, float) - self(s)
