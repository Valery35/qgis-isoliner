# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Headless-тесты декластеризации и взвешенного normal-score (без QGIS)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from grid_isolines import declus as D          # noqa: E402
from grid_isolines import kb2d                  # noqa: E402


def test_regular_grid_preserves_mean():
    # регулярная сеть: скученности нет, декластеризованное среднее совпадает
    # с наивным (веса по краям чуть пляшут - это нормальный краевой эффект)
    gx, gy = np.meshgrid(np.arange(10), np.arange(10))
    xs = gx.ravel().astype(float); ys = gy.ravel().astype(float)
    vs = xs + ys
    w, m = D.cell_declus(xs, ys, vs, 2.0, 2.0, noff=4)
    assert abs(m - float(vs.mean())) < 1e-6      # среднее сохранено
    assert abs(w.mean() - 1.0) < 1e-9            # нормировка
    assert (w > 0).all()


def test_cluster_downweighted():
    # разрежённый фон низких значений + плотный кластер высоких.
    # наивное среднее завышено, декластеризованное ниже, кластер придавлен.
    rng = np.random.default_rng(0)
    bx = rng.uniform(0, 100, 40); by = rng.uniform(0, 100, 40)
    bv = np.full(40, 10.0)
    cx = rng.uniform(48, 52, 60); cy = rng.uniform(48, 52, 60)
    cv = np.full(60, 30.0)
    xs = np.concatenate([bx, cx]); ys = np.concatenate([by, cy])
    vs = np.concatenate([bv, cv])
    res = D.declus_sweep(xs, ys, vs, 2.0, 60.0, ncell=20, noff=4)
    assert res["decl_mean"] < res["naive_mean"]         # перекос снят
    wc = res["weights"][40:].mean()                      # веса кластера
    wb = res["weights"][:40].mean()                      # веса фона
    assert wc < wb                                        # кластер придавлен


def test_weights_normalized():
    rng = np.random.default_rng(1)
    xs = rng.uniform(0, 100, 50); ys = rng.uniform(0, 100, 50)
    vs = rng.normal(20, 3, 50)
    w, m = D.cell_declus(xs, ys, vs, 15.0, 15.0)
    assert abs(w.sum() - 50.0) < 1e-6                     # sum(w)=n
    assert (w > 0).all()


def test_suggest_range():
    xs = np.array([0.0, 100.0]); ys = np.array([0.0, 50.0])
    lo, hi = D.suggest_range(xs, ys)
    assert 0 < lo < hi


def test_weighted_nscore_matches_when_uniform():
    v = np.array([3.0, 1.0, 2.0, 5.0, 4.0])
    ns0, sv0, sns0 = kb2d.nscore_transform(v)
    ns1, sv1, sns1 = kb2d.nscore_transform(v, wts=np.ones(v.size))
    assert np.allclose(ns0, ns1, atol=1e-9)              # равные веса = обычное
    assert np.allclose(sv0, sv1)


def test_weighted_nscore_monotonic_and_differs():
    v = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    w = np.array([3.0, 3.0, 3.0, 1.0, 1.0])
    ns, _, _ = kb2d.nscore_transform(v, wts=w)
    assert np.all(np.diff(ns[np.argsort(v)]) > 0)       # порядок сохранён
    ns0, _, _ = kb2d.nscore_transform(v)
    assert not np.allclose(ns, ns0)                      # веса что-то меняют
    wn = w / w.sum()
    assert abs(float(np.sum(wn * ns))) < 0.5             # взвеш. среднее ~0


def test_weighted_variogram_backcompat_and_effect():
    rng = np.random.default_rng(0)
    n = 120
    xs = rng.uniform(0, 100, n); ys = rng.uniform(0, 100, n)
    vs = rng.normal(20, 4, n)
    a = kb2d.experimental_variogram(xs, ys, vs, n_lags=12, seed=1)
    b = kb2d.experimental_variogram(xs, ys, vs, n_lags=12, seed=1,
                                    wts=np.ones(n))
    assert np.allclose(a["gamma"], b["gamma"])          # ones == unweighted
    assert np.array_equal(a["npairs"], b["npairs"])
    w = np.ones(n); w[:30] = 0.1
    c = kb2d.experimental_variogram(xs, ys, vs, n_lags=12, seed=1, wts=w)
    assert not np.allclose(a["gamma"], c["gamma"])       # веса влияют
    assert np.array_equal(a["npairs"], c["npairs"])      # счёт пар сохранён


def test_weighted_variogram_map_backcompat():
    rng = np.random.default_rng(2)
    n = 100
    xs = rng.uniform(0, 100, n); ys = rng.uniform(0, 100, n)
    vs = rng.normal(10, 2, n)
    m0 = kb2d.variogram_map(xs, ys, vs, n_bins=8, seed=1)
    m1 = kb2d.variogram_map(xs, ys, vs, n_bins=8, seed=1, wts=np.ones(n))
    assert np.allclose(np.nan_to_num(m0["grid"]),
                       np.nan_to_num(m1["grid"]))


def test_weighted_indicator_proportion():
    rng = np.random.default_rng(0)
    n = 60
    xd = rng.uniform(0, 100, n); yd = rng.uniform(0, 100, n)
    labels = np.array((['A'] * 35 + ['B'] * 25), dtype=object)
    classes = ['A', 'B']
    # придавим класс B весами -> его декластеризованная доля падает,
    # и результат отличается от невзвешенного
    w = np.ones(n); w[35:] = 0.1
    p0, _, _ = kb2d.categorical_indicator_grids(
        xd, yd, labels, classes, 0, 0, 4.0, 25, 25, ndmin=1, ndmax=12)
    pw, _, _ = kb2d.categorical_indicator_grids(
        xd, yd, labels, classes, 0, 0, 4.0, 25, 25, ndmin=1, ndmax=12, wts=w)
    a = np.where(p0 == -9999, np.nan, p0)
    b = np.where(pw == -9999, np.nan, pw)
    assert np.nanmax(np.abs(a - b)) > 1e-2
    propB = float(np.sum(w * (labels == 'B')) / w.sum())
    assert propB < float((labels == 'B').mean())      # доля B придавлена


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("all %d tests passed" % len(fns))


if __name__ == "__main__":
    _run()
