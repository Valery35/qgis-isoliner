# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Smoke-тесты движка кригинга kb2d. Движок не зависит от QGIS, поэтому тесты
# запускаются без QGIS:
#     python -m pytest grid_isolines/tests/         (если есть pytest)
#     python grid_isolines/tests/test_kb2d.py       (запуск напрямую)
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402


def _grid(xs, ys, vs, cell=10.0, ktype=1, nugget=0.0, rad=1e18):
    vg = kb2d.Variogram(nugget, [{"it": 1, "cc": 1.0, "aa": 15.0,
                                   "ang": 0.0, "anis": 1.0}])
    xmin, ymin = float(xs.min()), float(ys.min())
    w, h = float(xs.max()) - xmin, float(ys.max()) - ymin
    nx = max(int(math.ceil(w / cell)), 1) + 1
    ny = max(int(math.ceil(h / cell)), 1) + 1
    return kb2d.build_grid(xs, ys, vs, vg, ktype, 0.0, 1, 24, rad, -9999.0,
                           xmin, ymin, cell, nx, ny)


def test_exact_reproduction_at_nodes():
    """Ординарный кригинг (наггет 0) точно воспроизводит значения в узлах."""
    xs = np.array([0., 10., 0., 10.]); ys = np.array([0., 0., 10., 10.])
    vs = np.array([1., 2., 3., 4.])
    g = _grid(xs, ys, vs, cell=10.0)
    assert np.allclose(g, [[3., 4.], [1., 2.]], atol=1e-6), g


def test_no_overshoot_on_smooth_field():
    """Оценка не выходит за диапазон данных на гладком наборе."""
    rng = np.random.default_rng(0)
    xs = rng.uniform(0, 100, 60); ys = rng.uniform(0, 100, 60)
    vs = 0.05 * xs + 0.03 * ys
    g = _grid(xs, ys, vs, cell=5.0)
    v = g[g != -9999.0]
    assert v.min() >= vs.min() - 1e-6
    assert v.max() <= vs.max() + 1e-6


def test_nodata_when_too_far():
    """Если в радиусе поиска нет ни одной точки - узел остаётся nodata."""
    xs = np.array([0., 1.]); ys = np.array([0., 1.]); vs = np.array([5., 6.])
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 5.0,
                               "ang": 0.0, "anis": 1.0}])
    # узел в (1000,1000), радиус поиска маленький -> нет соседей -> nodata
    g = kb2d.build_grid(xs, ys, vs, vg, 1, 0.0, 1, 24, 10.0 * 10.0, -9999.0,
                        1000.0, 1000.0, 10.0, 1, 1)
    assert g[0, 0] == -9999.0


def test_duplicate_points_are_handled():
    """Совпадающие точки усредняются заранее (в algorithms); движок не должен
    падать на близких точках и давать конечный результат."""
    xs = np.array([0., 0., 20.]); ys = np.array([0., 0., 0.])
    vs = np.array([10., 20., 30.])  # дубль (0,0) усреднён до 15 вызывающим кодом
    xs2 = np.array([0., 20.]); ys2 = np.array([0., 0.]); vs2 = np.array([15., 30.])
    g = _grid(xs2, ys2, vs2, cell=10.0)
    assert np.isfinite(g[g != -9999.0]).all()


def test_variogram_spherical_monotonic():
    """Сферическая ковариация убывает с расстоянием и зануляется за радиусом."""
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 10.0,
                               "ang": 0.0, "anis": 1.0}])
    c0 = vg.cova2(0.0, 0.0)
    c5 = vg.cova2(5.0, 0.0)
    c10 = vg.cova2(10.0, 0.0)
    c20 = vg.cova2(20.0, 0.0)
    assert c0 > c5 > c10
    assert abs(c20) < 1e-9          # за радиусом корреляции - ноль


def test_variance_zero_at_node_and_nonneg():
    """Дисперсия кригинга = 0 в узле-пробе и неотрицательна везде."""
    rng = np.random.default_rng(1)
    xs = rng.uniform(0, 100, 40); ys = rng.uniform(0, 100, 40)
    vs = 0.04 * xs - 0.02 * ys
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 30.0,
                               "ang": 0.0, "anis": 1.0}])
    # узел точно на первой пробе -> дисперсия 0
    e, v = kb2d.krige_point(xs[0], ys[0], xs, ys, vs, vg, 1, 0.0,
                            1, 24, 1e18, -9999.0, return_var=True)
    assert abs(v) < 1e-9 and abs(e - vs[0]) < 1e-6
    # по всей сетке дисперсия (через стд.ошибку) неотрицательна
    g, se = kb2d.build_grid(xs, ys, vs, vg, 1, 0.0, 1, 24, 1e18, -9999.0,
                            0.0, 0.0, 5.0, 21, 21, with_variance=True)
    sev = se[se != -9999.0]
    assert (sev >= 0).all()


def test_stderr_grows_away_from_data():
    """Стандартная ошибка дальше от данных не меньше, чем вблизи."""
    xs = np.array([40., 50., 60., 50.]); ys = np.array([50., 40., 50., 60.])
    vs = np.array([1., 2., 3., 2.])
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 20.0,
                               "ang": 0.0, "anis": 1.0}])
    se_near = kb2d.krige_point(50., 50., xs, ys, vs, vg, 1, 0.0,
                               1, 24, 1e18, -9999.0, return_var=True)[1]
    se_far = kb2d.krige_point(50., 120., xs, ys, vs, vg, 1, 0.0,
                              1, 24, 1e18, -9999.0, return_var=True)[1]
    assert se_far >= se_near


def test_clip_outliers_cut_and_cap():
    """Отсев удаляет точки вне границ; срезка прижимает к границе."""
    v = np.array([-35.0, 1.0, 2.0, 3.0, 4.0, 122.0])
    # отсев по абсолютным границам [0; 30]
    out, keep, lo, hi = kb2d.clip_outliers(v, vmin=0.0, vmax=30.0, cap=False)
    assert lo == 0.0 and hi == 30.0
    assert keep.tolist() == [False, True, True, True, True, False]
    # срезка к [0; 30]
    out, keep, lo, hi = kb2d.clip_outliers(v, vmin=0.0, vmax=30.0, cap=True)
    assert keep.all()
    assert out.min() == 0.0 and out.max() == 30.0
    # без фильтра - ничего не меняется
    out, keep, lo, hi = kb2d.clip_outliers(v)
    assert keep.all() and np.array_equal(out, v)
    assert lo == float("-inf") and hi == float("inf")


def test_clip_outliers_percentile():
    """Перцентильная обрезка симметрична: pct=10 -> [p10; p90]."""
    v = np.arange(0.0, 101.0)               # 0..100
    out, keep, lo, hi = kb2d.clip_outliers(v, pct=10.0, cap=False)
    assert abs(lo - 10.0) < 1e-6 and abs(hi - 90.0) < 1e-6
    assert keep.sum() == 81                 # 10..90 включительно


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
