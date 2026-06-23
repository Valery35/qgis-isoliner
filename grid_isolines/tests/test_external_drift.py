# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты внешнего дрейфа (External Drift Kriging). Движок не зависит от QGIS:
#     python -m pytest grid_isolines/tests/
#     python grid_isolines/tests/test_external_drift.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402


def _scatter(n=400, L=1000.0, seed=1):
    rng = np.random.default_rng(seed)
    return rng.uniform(0, L, n), rng.uniform(0, L, n), rng


def test_n_terms():
    assert kb2d.ExternalDrift.n_terms(1) == 2
    assert kb2d.ExternalDrift.n_terms(2) == 3
    assert kb2d.ExternalDrift.n_terms(5) == 3   # >=2 -> квадратичный


def test_fit_recovers_linear_drift():
    """Степень 1 точно восстанавливает линейный дрейф по s: остатки нулевые."""
    _, _, rng = _scatter()
    s = rng.uniform(0, 100, 400)
    z = 3.0 + 1.7 * s
    d = kb2d.ExternalDrift.fit(s, z, 1)
    assert np.allclose(d(s), z, atol=1e-6)
    assert np.allclose(d.residuals(s, z), 0.0, atol=1e-6)


def test_fit_recovers_quadratic_drift():
    """Степень 2 точно восстанавливает квадратичную связь m = a0+a1 s+a2 s^2."""
    _, _, rng = _scatter(seed=2)
    s = rng.uniform(0, 100, 400)
    z = 2.0 - 0.5 * s + 0.01 * s * s
    d = kb2d.ExternalDrift.fit(s, z, 2)
    assert np.allclose(d(s), z, atol=1e-4)
    assert np.allclose(d.residuals(s, z), 0.0, atol=1e-4)


def test_eval_passes_through_nan():
    """nan во внешней величине даёт nan в дрейфе (ячейки вне покрытия растра)."""
    _, _, rng = _scatter(seed=3)
    s = rng.uniform(0, 100, 200)
    z = 1.0 + 2.0 * s
    d = kb2d.ExternalDrift.fit(s, z, 1)
    out = d(np.array([10.0, np.nan, 90.0]))
    assert np.isfinite(out[0]) and np.isfinite(out[2])
    assert np.isnan(out[1])


def test_residual_variogram_has_sill():
    """Дрейф снимает региональную составляющую: вариограмма остатков выходит
    на порог, а сырого значения раздута. Сравниваем разброс на дальнем лаге."""
    x, y, rng = _scatter(seed=4)
    s = 0.05 * x - 0.03 * y                      # внешняя величина = плоскость
    z = 10.0 + 2.0 * s + rng.normal(0, 1.0, len(x))
    d = kb2d.ExternalDrift.fit(s, z, 1)
    r = d.residuals(s, z)
    assert np.var(r) < 0.2 * np.var(z)           # дрейф объясняет большую часть


def test_rk_grid_reproduces_pure_drift():
    """Чистый линейный дрейф без структуры: после снятия остатки нулевые,
    кригинг остатков даёт нули, возврат дрейфа воспроизводит поле в узлах."""
    x, y, _ = _scatter(seed=5, n=300)
    s = 0.04 * x - 0.02 * y + 50.0
    z = 4.0 + 1.5 * s
    d = kb2d.ExternalDrift.fit(s, z, 1)
    r = d.residuals(s, z)
    assert np.allclose(r, 0.0, atol=1e-6)
    nodata = -9999.0
    vg = kb2d.Variogram(1.0, [{"it": 1, "cc": 1.0, "aa": 300.0,
                               "ang": 0.0, "anis": 1.0}])
    cell, nx, ny = 50.0, 20, 20
    xmn = ymn = 25.0
    grid = kb2d.build_grid(x, y, r, vg, 1, 0.0, 1, 24, 1e18, nodata,
                           xmn, ymn, cell, nx, ny)
    m = grid != nodata
    assert np.allclose(grid[m], 0.0, atol=1e-3)  # кригинг нулей = нули
    # возврат дрейфа в узлах сетки воспроизводит исходную плоскость
    for row in range(ny):
        iy = ny - row
        yloc = ymn + (iy - 1) * cell
        for ix in range(nx):
            if grid[row, ix] == nodata:
                continue
            xloc = xmn + ix * cell
            sval = 0.04 * xloc - 0.02 * yloc + 50.0
            est = grid[row, ix] + float(d(np.array([sval]))[0])
            assert abs(est - (4.0 + 1.5 * sval)) < 1e-2


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
