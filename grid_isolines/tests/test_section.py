# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты разреза по линии. Чистая геометрия/выборка, без QGIS:
#     python grid_isolines/tests/test_section.py
#
# Помощники _sample_grid_points и _valid_runs продублированы здесь один в один
# с algorithms.py (там их нельзя импортировать без QGIS), чтобы проверить
# математику выборки и разбиения независимо.
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sample_grid_points(arr, gt, xs, ys, bilinear=True):
    ny, nx = arr.shape
    fx = (xs - gt[0]) / gt[1] - 0.5
    fy = (ys - gt[3]) / gt[5] - 0.5

    def gather(ix, iy):
        out = np.full(len(xs), np.nan)
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        out[ok] = arr[iy[ok], ix[ok]]
        return out

    if not bilinear:
        return gather(np.round(fx).astype(int), np.round(fy).astype(int))
    x0 = np.floor(fx).astype(int); y0 = np.floor(fy).astype(int)
    tx = fx - x0; ty = fy - y0
    v00 = gather(x0, y0); v10 = gather(x0 + 1, y0)
    v01 = gather(x0, y0 + 1); v11 = gather(x0 + 1, y0 + 1)
    return (v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
            + v01 * (1 - tx) * ty + v11 * tx * ty)


def _valid_runs(mask):
    out = []; i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= 2:
                out.append((i, j - 1))
            i = j
        else:
            i += 1
    return out


def _linear_grid(a, b, c, nx=20, ny=15, cell=5.0, x0=1000.0, y0=2000.0):
    """f(x,y)=a*x+b*y+c в центрах ячеек. gt с верхним левым углом."""
    gt = (x0, cell, 0.0, y0 + ny * cell, 0.0, -cell)
    arr = np.empty((ny, nx))
    for r in range(ny):
        for col in range(nx):
            x = gt[0] + (col + 0.5) * gt[1]
            y = gt[3] + (r + 0.5) * gt[5]
            arr[r, col] = a * x + b * y + c
    return arr, gt


def test_bilinear_exact_on_linear_field():
    a, b, c = 0.3, -0.2, 50.0
    arr, gt = _linear_grid(a, b, c)
    xs = np.array([1023.0, 1041.7, 1060.0])
    ys = np.array([2033.0, 2050.5, 2061.0])
    z = _sample_grid_points(arr, gt, xs, ys, bilinear=True)
    exp = a * xs + b * ys + c
    assert np.allclose(z, exp, atol=1e-9)        # билинейно точно для линейной


def test_nearest_returns_cell_value():
    arr, gt = _linear_grid(1.0, 0.0, 0.0)
    # центр ячейки (col=3,row=2): x=gt0+3.5*cell
    x = np.array([gt[0] + 3.5 * gt[1]]); y = np.array([gt[3] + 2.5 * gt[5]])
    z = _sample_grid_points(arr, gt, x, y, bilinear=False)
    assert abs(float(z[0]) - arr[2, 3]) < 1e-9


def test_sampling_outside_is_nan():
    arr, gt = _linear_grid(1.0, 1.0, 0.0)
    xs = np.array([gt[0] - 100.0]); ys = np.array([gt[3] + 100.0])
    z = _sample_grid_points(arr, gt, xs, ys, bilinear=True)
    assert np.isnan(z[0])


def test_valid_runs_splits_on_gaps():
    mask = np.array([1, 1, 1, 0, 0, 1, 1, 0, 1, 1, 1], dtype=bool)
    assert _valid_runs(mask) == [(0, 2), (5, 6), (8, 10)]


def test_valid_runs_drops_singletons():
    mask = np.array([1, 0, 1, 1, 0, 1], dtype=bool)   # одиночки 0 и 5 отброшены
    assert _valid_runs(mask) == [(2, 3)]


def test_bed_polygon_ring_closes():
    """Кольцо полосы пласта: верх вперёд + низ назад + замыкание."""
    d = np.array([0.0, 10.0, 20.0, 30.0])
    ztop = np.array([100.0, 101.0, 99.0, 100.0])
    zbot = np.array([90.0, 91.0, 89.0, 90.0])
    idx = list(range(4)); ridx = list(range(3, -1, -1))
    ring = [(d[i], ztop[i]) for i in idx] + [(d[i], zbot[i]) for i in ridx]
    ring.append(ring[0])
    assert ring[0] == ring[-1]                   # замкнуто
    assert len(ring) == 4 + 4 + 1
    tmean = float(np.mean(ztop - zbot))
    assert abs(tmean - 10.0) < 1e-9              # средняя мощность


def _beds_from_levels(values):
    """Логика 3.3: отметки сортируются по убыванию, соседние пары - пласты.
    Возвращает список (top, bot) сверху вниз. NULL (None) отбрасываются."""
    vals = sorted((float(v) for v in values if v is not None), reverse=True)
    return [(vals[k], vals[k + 1]) for k in range(len(vals) - 1)]


def test_levels_sorted_to_bed_pairs():
    beds = _beds_from_levels([90.0, 100.0, 75.0])     # порядок выбора любой
    assert beds == [(100.0, 90.0), (90.0, 75.0)]      # сверху вниз, 2 пласта


def test_levels_with_null_skipped():
    beds = _beds_from_levels([100.0, None, 80.0])
    assert beds == [(100.0, 80.0)]                    # один пласт, NULL выкинут


def test_levels_single_value_no_bed():
    assert _beds_from_levels([100.0, None]) == []     # пласт не построить


def _class_zones(valid, cls):
    """Логика 3.4 (категориальный): смежные валидные точки одного класса в зону.
    Возвращает список (i0, i1, class)."""
    out = []; i = 0; n = len(valid)
    while i < n:
        if valid[i]:
            j = i
            while j + 1 < n and valid[j + 1] and cls[j + 1] == cls[i]:
                j += 1
            if j > i:
                out.append((i, j, cls[i]))
            i = j + 1
        else:
            i += 1
    return out


def test_categorical_zones_merge_runs():
    valid = [True, True, True, True, True, True]
    cls = [1, 1, 2, 2, 2, 1]
    # смежные одинаковые сливаются; одиночный класс-1 в конце (1 точка) отброшен
    assert _class_zones(valid, cls) == [(0, 1, 1), (2, 4, 2)]


def test_categorical_zones_break_on_gap():
    valid = [True, True, False, True, True]
    cls = [1, 1, 1, 1, 1]
    assert _class_zones(valid, cls) == [(0, 1, 1), (3, 4, 1)]   # разрыв делит


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
