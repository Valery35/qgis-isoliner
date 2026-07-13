# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Плотность по замерам с переменной опорой (инструмент 2.07).

Замер задан не точкой, а носителем конечного размера: точка с сигмой
неопределённости, отрезок линии (коридор полуширины), полигон. Единичная масса
замера размазывается по носителю. Масса постоянна, плотность обратна площади
носителя, поэтому грубые привязки самоослабляются геометрически.

Это оценка плотности (сколько и где), не интерполяция значения. Накопление
аддитивное в одну сетку float64. Инвариант: интеграл плотности по растру равен
сумме масс входа.

Чистый NumPy, без QGIS. Полигон подаётся уже растеризованной маской, линия -
массивом вершин (при необходимости уже вырезанным по from_m/to_m). Под
headless-тестом (tests/test_density.py).
"""
import numpy as np


class GridSpec:
    """Сетка накопления. Начало в (xmin, ymin), строка 0 снизу; при записи в
    GeoTIFF слой переворачивается и geotransform ставит отрицательный dy."""

    def __init__(self, xmin, ymin, cell, nx, ny):
        self.xmin = float(xmin)
        self.ymin = float(ymin)
        self.cell = float(cell)
        self.nx = int(nx)
        self.ny = int(ny)

    @classmethod
    def from_extent(cls, xmin, ymin, xmax, ymax, cell):
        cell = float(cell)
        nx = max(1, int(np.ceil((xmax - xmin) / cell)))
        ny = max(1, int(np.ceil((ymax - ymin) / cell)))
        return cls(xmin, ymin, cell, nx, ny)

    def new_acc(self):
        """Три аккумулятора: масса, Σ(масса·сигма), Σ(масса). Два последних -
        для карты эффективной сигмы, корректной и при дописывании сериями."""
        z = lambda: np.zeros((self.ny, self.nx), float)     # noqa: E731
        return z(), z(), z()

    def col_centers(self):
        return self.xmin + (np.arange(self.nx) + 0.5) * self.cell

    def row_centers(self):
        return self.ymin + (np.arange(self.ny) + 0.5) * self.cell

    def cell_area_km2(self):
        return (self.cell * self.cell) / 1.0e6


def add_point(acc, snum, wsum, gs, x, y, mass, sigma, renorm_inside=True,
              log=None):
    """Гауссово пятно с дискретной нормировкой: ядро считается по центрам ячеек
    и делится на свою сумму, поэтому масса на сетке сходится точно. sigma ниже
    полуячейки поднимается до полуячейки. Возвращает фактически размещённую
    массу (меньше mass при renorm_inside=False и обрезке краем)."""
    if mass is None or not np.isfinite(mass) or mass <= 0:
        if log is not None:
            log.append("масса <= 0 или пуста: объект пропущен")
        return 0.0
    half = 0.5 * gs.cell
    if sigma is None or not np.isfinite(sigma) or sigma < half:
        if sigma is not None and np.isfinite(sigma) and sigma < half and \
                log is not None:
            log.append("сигма %.3g поднята до полуячейки %.3g" % (sigma, half))
        sigma = half
    rad = 3.0 * sigma
    c0 = int(np.floor((x - rad - gs.xmin) / gs.cell))
    c1 = int(np.ceil((x + rad - gs.xmin) / gs.cell))
    r0 = int(np.floor((y - rad - gs.ymin) / gs.cell))
    r1 = int(np.ceil((y + rad - gs.ymin) / gs.cell))
    cc0 = max(c0, 0); cc1 = min(c1, gs.nx)
    rr0 = max(r0, 0); rr1 = min(r1, gs.ny)
    if cc0 >= cc1 or rr0 >= rr1:
        if log is not None:
            log.append("носитель точки вне сетки: масса потеряна")
        return 0.0
    cx = gs.xmin + (np.arange(cc0, cc1) + 0.5) * gs.cell
    cy = gs.ymin + (np.arange(rr0, rr1) + 0.5) * gs.cell
    dx = (cx - x)[None, :]
    dy = (cy - y)[:, None]
    k = np.exp(-0.5 * (dx * dx + dy * dy) / (sigma * sigma))
    ktot_inside = k.sum()
    if ktot_inside <= 0:
        return 0.0
    if renorm_inside:
        k_norm = k / ktot_inside                    # вся масса внутри сетки
        placed = mass
    else:
        # полная сумма ядра без обрезки (аналитически) -> часть массы теряется
        full = 2.0 * np.pi * sigma * sigma / (gs.cell * gs.cell)
        k_norm = k / full
        placed = mass * (ktot_inside / full)
    contrib = mass * k_norm
    acc[rr0:rr1, cc0:cc1] += contrib
    snum[rr0:rr1, cc0:cc1] += contrib * sigma
    wsum[rr0:rr1, cc0:cc1] += contrib
    return placed


def _densify(verts, step):
    """Уплотнить полилинию до шага не крупнее step. Возвращает точки и их
    веса-доли (пропорционально длине сегментов, сумма долей = 1)."""
    verts = np.asarray(verts, float)
    seg = np.diff(verts, axis=0)
    seglen = np.hypot(seg[:, 0], seg[:, 1])
    total = seglen.sum()
    if total <= 0:
        return verts[:1], np.array([1.0])
    pts = []
    wts = []
    for i in range(len(seg)):
        L = seglen[i]
        if L <= 0:
            continue
        n = max(1, int(np.ceil(L / step)))
        ts = (np.arange(n) + 0.5) / n
        p = verts[i][None, :] + ts[:, None] * seg[i][None, :]
        pts.append(p)
        wts.append(np.full(n, (L / total) / n))
    return np.vstack(pts), np.concatenate(wts)


def add_line(acc, snum, wsum, gs, verts, mass, halfwidth, renorm_inside=True,
             log=None):
    """Линия как коридор мягких краёв: полилиния уплотняется до полуячейки,
    масса делится по подточкам пропорционально длине сегментов, каждая подточка
    - гауссово пятно с сигмой-полушириной. Код общий с точечным случаем."""
    pts, frac = _densify(verts, 0.5 * gs.cell)
    placed = 0.0
    for (px, py), fr in zip(pts, frac):
        placed += add_point(acc, snum, wsum, gs, px, py, mass * fr, halfwidth,
                            renorm_inside=renorm_inside, log=log)
    return placed


def add_polygon(acc, snum, wsum, gs, mask, mass, aux=None, log=None):
    """Полигон, поданный булевой маской на сетке. Равномерно: масса делится по
    ячейкам маски. Дазиметрия: пропорционально aux внутри маски; при пустом или
    неположительном aux - откат на равномерное с записью в лог. Эффективная
    сигма полигона - радиус равновеликого круга sqrt(S/π)."""
    if mass is None or not np.isfinite(mass) or mass <= 0:
        if log is not None:
            log.append("масса <= 0 или пуста: полигон пропущен")
        return 0.0
    m = np.asarray(mask, bool)
    ncell = int(m.sum())
    if ncell == 0:
        if log is not None:
            log.append("полигон не покрыл ни одной ячейки: масса потеряна")
        return 0.0
    area = ncell * gs.cell * gs.cell
    sig_poly = np.sqrt(area / np.pi)
    if aux is not None:
        w = np.asarray(aux, float)[m]
        w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
        if w.sum() <= 0:
            if log is not None:
                log.append("дазиметрия: вспомогательный растр пуст внутри "
                           "полигона - откат на равномерное")
            share = np.full(ncell, mass / ncell)
        else:
            share = mass * (w / w.sum())
    else:
        share = np.full(ncell, mass / ncell)
    acc[m] += share
    snum[m] += share * sig_poly
    wsum[m] += share
    return float(share.sum())


def finalize(acc, snum, wsum, gs, nodata=-9999.0):
    """Плотность (масса на км²) и карта эффективной сигмы. Плотность не зависит
    от размера ячейки. Возвращает (density, eff_sigma, total_mass)."""
    density = acc / gs.cell_area_km2()
    eff = np.where(wsum > 0, snum / np.maximum(wsum, 1e-300), nodata)
    total_mass = float(acc.sum())
    return density, eff, total_mass


def cut_polyline(verts, from_m=None, to_m=None):
    """Вырезать интервал полилинии по линейной привязке (метры вдоль линии).
    from_m/to_m = None/NaN - соответствующий край не режется. Возвращает
    массив вершин обрезанного участка (>=2 точки) или None, если пусто."""
    v = np.asarray(verts, float)
    if v.ndim != 2 or len(v) < 2:
        return None
    seg = np.hypot(np.diff(v[:, 0]), np.diff(v[:, 1]))
    s = np.concatenate([[0.0], np.cumsum(seg)])
    total = float(s[-1])
    if total <= 0:
        return None
    a = 0.0 if from_m is None or not np.isfinite(from_m) else max(0.0, from_m)
    b = total if to_m is None or not np.isfinite(to_m) else min(total, to_m)
    if b <= a:
        return None
    ss = np.unique(np.concatenate([[a, b], s[(s > a) & (s < b)]]))
    xs = np.interp(ss, s, v[:, 0])
    ys = np.interp(ss, s, v[:, 1])
    return np.column_stack([xs, ys])


def rasterize_polygon(gs, rings):
    """Булева маска центров ячеек внутри полигона (even-odd по всем кольцам,
    дыры учитываются). rings - список колец, каждое - массив вершин."""
    px = gs.col_centers()[None, :] + np.zeros((gs.ny, 1))
    py = gs.row_centers()[:, None] + np.zeros((1, gs.nx))
    inside = np.zeros((gs.ny, gs.nx), dtype=bool)
    for ring in rings:
        r = np.asarray(ring, float)
        if len(r) < 3:
            continue
        j = len(r) - 1
        for i in range(len(r)):
            xi, yi = r[i]
            xj, yj = r[j]
            cond = (yi > py) != (yj > py)
            with np.errstate(divide="ignore", invalid="ignore"):
                xint = (xj - xi) * (py - yi) / (yj - yi) + xi
            inside ^= cond & (px < xint)
            j = i
    return inside


def demo_dataset(xmin, ymin, xmax, ymax, seed=1):
    """Учебный набор с круглой суммарной массой для проверки инварианта 2.07.
    10 точек с разными сигмами (масса 500), 2 линии (масса 200, у одной вырезка
    интервала), 2 полигона (масса 300, один под дазиметрию). Итого 1000."""
    rng = np.random.default_rng(seed if seed and seed > 0 else None)
    w = float(xmax - xmin); h = float(ymax - ymin)
    d = min(w, h)
    pts = []
    for i in range(10):
        x = xmin + rng.uniform(0.15, 0.85) * w
        y = ymin + rng.uniform(0.15, 0.85) * h
        sigma = (0.004 + 0.06 * (i / 9.0)) * d       # от долей ячейки до крупной
        pts.append((x, y, 50.0, sigma))              # 10 x 50 = 500
    l1 = [(xmin + 0.10 * w, ymin + 0.30 * h),
          (xmin + 0.50 * w, ymin + 0.36 * h),
          (xmin + 0.90 * w, ymin + 0.30 * h)]
    l2 = [(xmin + 0.20 * w, ymin + 0.70 * h),
          (xmin + 0.80 * w, ymin + 0.74 * h)]
    len2 = float(np.hypot(l2[1][0] - l2[0][0], l2[1][1] - l2[0][1]))
    lines = [dict(verts=l1, mass=100.0, half=0.03 * d, from_m=None, to_m=None),
             dict(verts=l2, mass=100.0, half=0.03 * d,
                  from_m=0.25 * len2, to_m=0.75 * len2)]      # 2 x 100 = 200
    def box(x0, x1, y0, y1):
        return [[(xmin + x0 * w, ymin + y0 * h), (xmin + x1 * w, ymin + y0 * h),
                 (xmin + x1 * w, ymin + y1 * h), (xmin + x0 * w, ymin + y1 * h),
                 (xmin + x0 * w, ymin + y0 * h)]]
    polys = [dict(rings=box(0.12, 0.40, 0.10, 0.42), mass=150.0, dasy=False),
             dict(rings=box(0.58, 0.90, 0.55, 0.88), mass=150.0, dasy=True)]
    return dict(points=pts, lines=lines, polygons=polys, total=1000.0,
                total_points=500.0, total_lines=200.0, total_polygons=300.0)
