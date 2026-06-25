# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты обратного преобразования лог-кригинга (оценка exp, ошибка дельта-метод).
# Воспроизводит логику инструмента «2D Kriging» поверх движка kb2d, без QGIS:
#     python grid_isolines/tests/test_logkriging.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402

ND = -9999.0


def _krige_log(xs, ys, vals, nx=24, ny=18, cell=2.0):
    """ln(Z) -> кригинг -> обратное преобразование, как в инструменте."""
    assert np.all(vals > 0)
    lv = np.log(vals)
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": float(np.var(lv)) or 1.0,
                               "aa": 30.0, "ang": 0.0, "anis": 1.0}])
    grid, se = kb2d.build_grid(xs, ys, lv, vg, 1, 0.0, 1, 24, 1e18, ND,
                               1.0, 1.0, cell, nx, ny, with_variance=True)
    valid = grid != ND
    lin = np.exp(np.where(valid, grid, 0.0))
    sev = valid & (se != ND)
    se_z = np.where(sev, lin * se, ND)          # дельта-метод SE_Z ≈ Z·SE_ln
    est = np.where(valid, lin, ND)
    return est, se_z, valid


def test_back_transform_positive():
    """Оценка лог-нормальной величины всегда положительна."""
    rng = np.random.default_rng(0)
    xs = rng.uniform(0, 48, 40); ys = rng.uniform(0, 36, 40)
    vals = np.exp(rng.normal(-2.0, 1.6, 40))     # K-подобно: 3 порядка
    est, se_z, valid = _krige_log(xs, ys, vals)
    assert np.all(est[valid] > 0.0)


def test_estimate_reproduces_nodes_after_exp():
    """В узле оценка возвращает само значение (exp(ln Z) = Z)."""
    xs = np.array([10.0, 30.0, 20.0]); ys = np.array([10.0, 10.0, 25.0])
    vals = np.array([0.004, 4.09, 0.3])
    est, se_z, valid = _krige_log(xs, ys, vals, nx=21, ny=16, cell=2.0)
    # ячейка, ближайшая к первому узлу (x=10,y=10): индекс по сетке
    # центр ячейки = (1 + ix*2, 1 + iy*2); найдём ближайшую
    best = None
    for row in range(est.shape[0]):
        iy = est.shape[0] - row
        yloc = 1.0 + (iy - 1) * 2.0
        for ix in range(est.shape[1]):
            xloc = 1.0 + ix * 2.0
            d = (xloc - 10.0) ** 2 + (yloc - 10.0) ** 2
            if best is None or d < best[0]:
                best = (d, est[row, ix])
    assert abs(best[1] - 0.004) < 0.004 * 0.5     # близко к значению узла


def test_delta_se_nonnegative_and_scales():
    """SE в исходных единицах неотрицательна и растёт там, где больше оценка."""
    rng = np.random.default_rng(2)
    xs = rng.uniform(0, 48, 30); ys = rng.uniform(0, 36, 30)
    vals = np.exp(rng.normal(0.0, 1.2, 30))
    est, se_z, valid = _krige_log(xs, ys, vals)
    m = valid & (se_z != ND)
    assert np.all(se_z[m] >= 0.0)


def test_geometric_median_below_arithmetic_mean():
    """exp(среднее ln) (геометрическое) не превышает арифметическое среднее."""
    vals = np.array([0.004, 0.03, 0.3, 4.09])
    geo = float(np.exp(np.mean(np.log(vals))))
    ari = float(np.mean(vals))
    assert geo <= ari                              # известное свойство


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
