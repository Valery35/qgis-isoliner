# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Headless-тесты генератора геофизических профилей (без QGIS)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from grid_isolines import geodemo as G          # noqa: E402


def test_pk_label():
    assert G.pk_label(0) == "ПК0+00"
    assert G.pk_label(20) == "ПК0+20"
    assert G.pk_label(100) == "ПК1+00"
    assert G.pk_label(520) == "ПК5+20"


def test_electro_fields_and_shape():
    d = G.gen_profiles(0, 0, 1000, 800, n_profiles=4, picket_step=20.0, seed=1)
    for k in ("profile", "picket_m", "x", "y", "z", "rho_k", "rho_true",
              "sp", "vp"):
        assert k in d, k
    assert set(np.unique(d["profile"])) == {1, 2, 3, 4}
    p1 = np.sort(d["picket_m"][d["profile"] == 1])
    assert abs(p1[0]) < 1e-9 and abs((p1[1] - p1[0]) - 20.0) < 1e-6
    assert (d["rho_k"] > 0).all()
    assert (d["vp"] > 0).all()
    assert 50.0 < d["z"].mean() < 200.0             # отметки реалистичны


def test_low_resistivity_spot():
    d = G.gen_profiles(0, 0, 1000, 800, n_profiles=5, picket_step=20.0,
                       rho_bg=60.0, rho_min=10.0, seed=2)
    assert float(d["rho_k"].min()) < 30.0            # провал есть
    assert float(d["rho_k"].max()) > 40.0
    k = int(np.argmin(d["rho_k"]))
    assert d["sp"][k] < 0.0                          # ЕП в минус над аномалией


def test_anomaly_is_spot_not_stripe():
    # пятно: минимум ρк по профилям должен заметно различаться между профилями
    # (у полосы все профили были бы одинаковы)
    d = G.gen_profiles(0, 0, 1000, 800, n_profiles=5, picket_step=20.0,
                       rho_bg=60.0, rho_min=10.0, noise=0.0, seed=3)
    mins = [d["rho_true"][d["profile"] == p].min()
            for p in np.unique(d["profile"])]
    mins = np.array(mins)
    # хотя бы один профиль глубоко в аномалии, хотя бы один почти на фоне
    assert mins.min() < 25.0
    assert mins.max() > 45.0                          # не полоса


def test_truth_no_noise():
    d = G.gen_profiles(0, 0, 1000, 800, noise=0.0, seed=4)
    # при нулевом шуме rho_k совпадает с истинным
    assert np.allclose(d["rho_k"], d["rho_true"], rtol=1e-9)


def test_subsidence_mulda_and_tours():
    d = G.gen_subsidence(0, 0, 1000, 800, n_profiles=4, picket_step=20.0,
                         subs_max=400.0, n_tours=2, seed=5)
    for k in ("profile", "picket_m", "z", "tour", "settle", "settle_true"):
        assert k in d, k
    assert set(np.unique(d["tour"])) == {1, 2}
    m1 = d["settle_true"][d["tour"] == 1].min()
    m2 = d["settle_true"][d["tour"] == 2].min()
    assert m1 < 0 and m2 < m1                          # тур 2 глубже
    assert m2 < -0.6 * 400.0


def test_subsidence_single_sign_and_edges():
    # вниз: все значения <= 0, по краям строго нули, шум там не создаёт плюсов
    d = G.gen_subsidence(0, 0, 1000, 800, subs_max=400.0, positive=False,
                         seed=6)
    assert d["settle_true"].max() <= 0.0
    assert d["settle"].max() <= 1e-9                   # нет положительных
    edge = d["settle_true"] == 0.0
    assert edge.any()                                  # края есть
    assert np.allclose(d["settle"][edge], 0.0)         # на краях строго ноль
    # величина (положительное): все >= 0
    dp = G.gen_subsidence(0, 0, 1000, 800, subs_max=400.0, positive=True,
                          seed=6)
    assert dp["settle"].min() >= -1e-9


def test_subsidence_cap_2m():
    d = G.gen_subsidence(0, 0, 1000, 800, subs_max=5000.0, n_tours=1, seed=7)
    assert d["settle_true"].min() >= -2000.0           # не больше 2 м


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("all %d tests passed" % len(fns))


if __name__ == "__main__":
    _run()
