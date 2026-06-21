# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты полиномиального тренда (регрессия-кригинг). Движок не зависит от QGIS:
#     python -m pytest grid_isolines/tests/
#     python grid_isolines/tests/test_detrend.py
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402


def _scatter(n=400, L=1000.0, seed=1):
    rng = np.random.default_rng(seed)
    return rng.uniform(0, L, n), rng.uniform(0, L, n), rng


def test_fit_recovers_exact_plane():
    """Степень 1 точно восстанавливает плоскость: остатки нулевые."""
    x, y, _ = _scatter()
    z = 5.0 + 0.3 * x - 0.7 * y
    tr = kb2d.PolyTrend.fit(x, y, z, 1)
    assert np.allclose(tr(x, y), z, atol=1e-6)
    assert np.allclose(tr.residuals(x, y, z), 0.0, atol=1e-6)


def test_fit_recovers_exact_quadratic():
    """Степень 2 точно восстанавливает квадратичную поверхность."""
    x, y, _ = _scatter(seed=2)
    z = 2.0 + 0.1 * x - 0.2 * y + 3e-4 * x * x - 1e-4 * x * y + 2e-4 * y * y
    tr = kb2d.PolyTrend.fit(x, y, z, 2)
    assert np.allclose(tr(x, y), z, atol=1e-4)
    assert np.allclose(tr.residuals(x, y, z), 0.0, atol=1e-4)


def test_degree1_cannot_fit_quadratic_but_degree2_can():
    """Плоскость не описывает кривизну (остаток есть), степень 2 описывает."""
    x, y, _ = _scatter(seed=3)
    z = 5e-4 * x * x + 4e-4 * y * y           # чистая кривизна
    r1 = kb2d.PolyTrend.fit(x, y, z, 1).residuals(x, y, z)
    r2 = kb2d.PolyTrend.fit(x, y, z, 2).residuals(x, y, z)
    assert np.var(r1) > 1e-6                  # плоскость не справилась
    assert np.allclose(r2, 0.0, atol=1e-4)    # квадратичная справилась


def test_detrend_retrend_roundtrip():
    """Снятие и возврат тренда тождественны: m(x,y) + (z - m(x,y)) == z."""
    x, y, rng = _scatter(seed=4)
    z = 10.0 + 0.2 * x - 0.5 * y + rng.normal(0, 3.0, len(x))
    for deg in (1, 2):
        tr = kb2d.PolyTrend.fit(x, y, z, deg)
        r = tr.residuals(x, y, z)
        assert np.allclose(tr(x, y) + r, z, atol=1e-8)


def test_detrend_reduces_variance_on_trend_neutral_on_flat():
    """На трендовых данных остаток заметно меньше по дисперсии, на данных без
    тренда снятие тренда почти ничего не меняет."""
    x, y, rng = _scatter(seed=5)
    z_trend = 0.4 * x - 0.6 * y + rng.normal(0, 2.0, len(x))
    z_flat = 100.0 + rng.normal(0, 2.0, len(x))
    r_trend = kb2d.PolyTrend.fit(x, y, z_trend, 1).residuals(x, y, z_trend)
    r_flat = kb2d.PolyTrend.fit(x, y, z_flat, 1).residuals(x, y, z_flat)
    assert np.var(r_trend) < 0.2 * np.var(z_trend)      # тренд снят
    assert np.var(r_flat) > 0.9 * np.var(z_flat)        # ничего лишнего не убрано


def test_rk_grid_reproduces_pure_trend():
    """Сквозной путь регрессии-кригинга на чисто трендовых данных: остатки ~0,
    кригинг остатков ~0, и грид после возврата тренда совпадает со значением
    тренда в узлах. Координаты ячеек строго как в build_grid и в обвязке
    _run_kriging_to_tiff."""
    rng = np.random.default_rng(6)
    x = rng.uniform(0, 1000.0, 200)
    y = rng.uniform(0, 1000.0, 200)
    z = 3.0 + 0.25 * x - 0.4 * y                    # чистая плоскость

    tr = kb2d.PolyTrend.fit(x, y, z, 1)
    resid = tr.residuals(x, y, z)
    assert np.allclose(resid, 0.0, atol=1e-6)

    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 300.0,
                               "ang": 0.0, "anis": 1.0}])
    nodata = -9999.0
    cell = 100.0
    xmin, ymin = 0.0, 0.0
    nx = ny = 10
    xmn, ymn = xmin + 0.5 * cell, ymin + 0.5 * cell

    grid = kb2d.build_grid(x, y, resid, vg, 1, 0.0, 1, 24, 1e18, nodata,
                           xmn, ymn, cell, nx, ny)

    # возврат тренда теми же формулами координат, что в обвязке
    xs_cells = xmn + np.arange(nx) * cell
    out = grid.copy()
    for row in range(ny):
        iy = ny - row
        yloc = ymn + (iy - 1) * cell
        m = out[row] != nodata
        out[row, m] = out[row, m] + tr(xs_cells[m],
                                       np.full(int(m.sum()), yloc)).astype(out.dtype)

    # эталон: тренд напрямую в тех же узлах
    ref = np.empty((ny, nx))
    for row in range(ny):
        iy = ny - row
        yloc = ymn + (iy - 1) * cell
        ref[row] = tr(xs_cells, np.full(nx, yloc))

    valid = grid != nodata
    assert valid.any()
    assert np.allclose(out[valid], ref[valid], atol=1e-3)


def test_cv_detrend_matches_plane_loo():
    """Кросс-валидация с детрендом на чисто трендовых данных: LOO-оценка близка
    к факту (остатки нулевые, тренд переподбирается без исключённой точки)."""
    rng = np.random.default_rng(7)
    x = rng.uniform(0, 1000.0, 150)
    y = rng.uniform(0, 1000.0, 150)
    z = 3.0 + 0.25 * x - 0.4 * y                    # чистая плоскость
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 300.0,
                               "ang": 0.0, "anis": 1.0}])
    est, var = kb2d.cross_validate_detrend(x, y, z, 1, vg, 1, 0.0, 2, 24,
                                           1e18, -9999.0)
    ok = est != -9999.0
    assert ok.sum() > 100
    err = est[ok] - z[ok]
    assert np.sqrt(np.mean(err ** 2)) < 1e-3        # плоскость снимается точно


def test_cv_detrend_beats_plain_on_trended_field():
    """На поле тренд + коррелированный остаток LOO-ошибка регрессии-кригинга
    меньше, чем у обычной кросс-валидации."""
    rng = np.random.default_rng(11)
    n = 600
    x = rng.uniform(0, 4000.0, n)
    y = rng.uniform(0, 4000.0, n)
    trend = 0.05 * x - 0.04 * y                     # выраженное падение
    resid = np.zeros(n)
    for _ in range(20):
        wl = rng.uniform(800, 1800); k = 2 * np.pi / wl
        a = rng.uniform(0, np.pi); ph = rng.uniform(0, 2 * np.pi)
        resid += rng.normal() * np.cos(k * (x * np.cos(a) + y * np.sin(a)) + ph)
    resid *= 6.0 / (np.std(resid) or 1.0)
    z = trend + resid + rng.normal(0, 1.0, n)

    nodata = -9999.0
    # вариограмма по остаткам (для RK) и по сырому z (для обычной CV)
    def _vg(v):
        ev = kb2d.experimental_variogram(x, y, v)
        f = kb2d.fit_variogram(ev["lag"], ev["gamma"], ev["npairs"])
        it = {0: 1, 1: 2, 2: 3}[f["model"]]
        return kb2d.Variogram(f["nugget"], [dict(it=it, cc=f["sill"],
                              aa=f["range"], ang=0.0, anis=1.0)])
    r = kb2d.PolyTrend.fit(x, y, z, 1).residuals(x, y, z)
    e_ok, _ = kb2d.cross_validate(x, y, z, _vg(z), 1, 0.0, 2, 24, 1e18, nodata)
    e_rk, _ = kb2d.cross_validate_detrend(x, y, z, 1, _vg(r), 1, 0.0, 2, 24,
                                          1e18, nodata)
    m = (e_ok != nodata) & (e_rk != nodata)
    rmse_ok = np.sqrt(np.mean((e_ok[m] - z[m]) ** 2))
    rmse_rk = np.sqrt(np.mean((e_rk[m] - z[m]) ** 2))
    assert rmse_rk < rmse_ok                        # детренд помогает на тренде


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
