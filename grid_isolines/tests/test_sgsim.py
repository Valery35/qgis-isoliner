# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты последовательной гауссовой симуляции. Движок не зависит от QGIS:
#     python grid_isolines/tests/test_sgsim.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402


def test_norm_ppf_inverse_of_cdf():
    """Φ⁻¹ обращает Φ: ppf(cdf(z)) ≈ z на разумном диапазоне."""
    z = np.linspace(-3.5, 3.5, 71)
    assert np.allclose(kb2d._norm_ppf(kb2d.norm_cdf(z)), z, atol=1e-3)


def test_norm_ppf_known_quantiles():
    p = np.array([0.025, 0.5, 0.975])
    exp = np.array([-1.959964, 0.0, 1.959964])
    assert np.allclose(kb2d._norm_ppf(p), exp, atol=1e-4)


def test_nscore_roundtrip():
    """Обратное преобразование восстанавливает исходные значения в узлах."""
    rng = np.random.default_rng(3)
    v = rng.lognormal(0.0, 0.7, 200)            # скошенные данные
    ns, sv, sns = kb2d.nscore_transform(v)
    back = kb2d.nscore_back(ns, sv, sns)
    assert np.allclose(np.sort(back), np.sort(v), atol=1e-9)
    # баллы примерно стандартно-нормальны
    assert abs(ns.mean()) < 0.05 and abs(ns.std() - 1.0) < 0.1


def _demo(seed=0, n=120):
    rng = np.random.default_rng(seed)
    xi = rng.uniform(0, 100, n)
    yi = rng.uniform(0, 100, n)
    zi = 50 + 20 * np.sin(xi / 18) + 15 * np.cos(yi / 22) + rng.normal(0, 1.5, n)
    ns, _, _ = kb2d.nscore_transform(zi)
    ev = kb2d.experimental_variogram(xi, yi, ns, n_lags=12)
    fit = kb2d.fit_variogram(ev["lag"], ev["gamma"], ev["npairs"],
                             model="auto", sill_cap=1.2)
    vg = kb2d.Variogram(fit["nugget"], [{
        "it": fit["model"] + 1, "cc": fit["sill"], "aa": fit["range"],
        "ang": 0.0, "anis": 1.0}])
    return xi, yi, zi, vg, fit


def test_sgsim_shape_and_finite():
    xi, yi, zi, vg, fit = _demo()
    real = kb2d.sgsim(xi, yi, zi, vg, 0.0, 0.0, 2.0, 51, 51, nreal=20,
                      ndmin=1, ndmax=16, rad2=(3 * fit["range"]) ** 2, seed=1)
    assert real.shape == (20, 51, 51)
    assert np.isfinite(real).all()
    # значения не выходят за диапазон данных (обратное преобразование зажимает)
    assert real.min() >= zi.min() - 1e-6 and real.max() <= zi.max() + 1e-6


def test_sgsim_uncertainty_grows_away_from_data():
    """Разброс реализаций мал у скважин и больше вдали от них."""
    xi, yi, zi, vg, fit = _demo()
    cell, nx, ny = 2.0, 51, 51
    real = kb2d.sgsim(xi, yi, zi, vg, 0.0, 0.0, cell, nx, ny, nreal=30,
                      ndmin=1, ndmax=16, rad2=(3 * fit["range"]) ** 2, seed=2)
    std = real.std(axis=0)
    ix = int(round(xi[0] / cell)); iy = int(round(yi[0] / cell))
    row = ny - 1 - iy                            # north-first
    assert std[row, ix] < 0.5                    # узел у данных - почти заморожен
    assert std[ny // 2, nx // 2] > std[row, ix]  # вдали разброс больше


def test_sgsim_reproduces_data_variability():
    """Реализация не сглажена: её разброс близок к разбросу данных,
    в отличие от кригинга, который занижает дисперсию."""
    xi, yi, zi, vg, fit = _demo()
    real = kb2d.sgsim(xi, yi, zi, vg, 0.0, 0.0, 2.0, 51, 51, nreal=10,
                      ndmin=1, ndmax=16, rad2=(3 * fit["range"]) ** 2, seed=4)
    assert real[0].std() > 0.6 * zi.std()


def test_sgsim_seed_reproducible():
    xi, yi, zi, vg, fit = _demo()
    kw = dict(ndmin=1, ndmax=16, rad2=(3 * fit["range"]) ** 2, seed=7)
    a = kb2d.sgsim(xi, yi, zi, vg, 0.0, 0.0, 4.0, 26, 26, nreal=5, **kw)
    b = kb2d.sgsim(xi, yi, zi, vg, 0.0, 0.0, 4.0, 26, 26, nreal=5, **kw)
    assert np.array_equal(a, b)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
