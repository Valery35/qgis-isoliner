# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Тесты проектной площадки демо-рельефа.

Площадка нужна для примерки 2.18, и от неё требуется немного, но твёрдо:
снаружи рельеф не тронут, внутри ровно, и работы получаются с обоих
знаков, иначе демонстрировать баланс не на чем.

    python grid_isolines/tests/test_demo_pad.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(MODULE))

from grid_isolines import demo_relief as D  # noqa: E402
from grid_isolines import volumes as V      # noqa: E402


def _relief():
    return D.generate(nx=120, ny=90, cell=10.0, seed=7)


def test_outside_untouched():
    """За областью работ рельеф не меняется ни в одной ячейке."""
    z = _relief()
    design, (r0, r1, c0, c1), _pz, _zb = D.design_pad(z)
    mask = np.ones(z.shape, dtype=bool)
    mask[r0:r1, c0:c1] = False
    assert np.array_equal(design[mask], z.astype(float)[mask])


def test_pad_is_flat():
    """Внутри области площадка ровная."""
    z = _relief()
    design, (r0, r1, c0, c1), pad_z, _zb = D.design_pad(z)
    inside = design[r0:r1, c0:c1]
    assert np.all(inside == pad_z)
    assert np.ptp(inside) == 0.0


def test_both_signs_present():
    """Работы получаются и в насыпь, и в выемку, иначе баланс не показать."""
    z = _relief()
    design, _b, _pz, _zb = D.design_pad(z)
    st = V.cutfill_stats(V.difference(design, z), area=100.0)
    assert st["fill_volume"] > 0.0
    assert st["cut_volume"] > 0.0


def test_mean_gives_exact_balance():
    """Среднее как отметка площадки обнуляет нетто, и это точное равенство.

    Именно поэтому взято среднее, а не медиана: объём есть сумма разностей,
    и она равна нулю ровно при отметке, равной среднему. Медиана делит
    пополам ячейки, а не кубометры.
    """
    z = _relief()
    design, _b, _pz, _zb = D.design_pad(z)
    st = V.cutfill_stats(V.difference(design, z), area=100.0)
    turn = st["fill_volume"] + st["cut_volume"]
    assert turn > 0
    assert abs(st["net_volume"]) / turn < 1e-9
    assert V.balance_verdict(st) == "balanced"


def test_offset_moves_balance_both_ways():
    """Сдвиг площадки вверх требует привозного грунта, вниз - вывозного."""
    z = _relief()
    up, _b, _pz, _zb = D.design_pad(z, dz=5.0)
    down, _b2, _pz2, _zb2 = D.design_pad(z, dz=-5.0)
    su = V.cutfill_stats(V.difference(up, z), area=100.0)
    sd = V.cutfill_stats(V.difference(down, z), area=100.0)
    assert V.balance_verdict(su) == "import"
    assert V.balance_verdict(sd) == "export"


def test_zones_tile_the_area():
    """Участки покрывают область работ без нахлёстов и без дыр."""
    z = _relief()
    _d, (r0, r1, c0, c1), _pz, zb = D.design_pad(z, zones=3)
    assert len(zb) == 3
    assert zb[0][2] == c0 and zb[-1][3] == c1
    for a, b in zip(zb, zb[1:]):
        assert a[3] == b[2]
    for zr0, zr1, _a, _b in zb:
        assert (zr0, zr1) == (r0, r1)


def test_deterministic():
    """Одно зерно - одна площадка."""
    a = D.design_pad(_relief())
    b = D.design_pad(_relief())
    assert np.array_equal(a[0], b[0])
    assert a[1] == b[1] and a[2] == b[2]


def test_bad_fraction_rejected():
    """Нелепая доля области отвергается с внятным сообщением."""
    z = _relief()
    for bad in (0.0, 1.5):
        try:
            D.design_pad(z, frac=bad)
        except ValueError:
            continue
        raise AssertionError("доля %r должна быть отвергнута" % bad)


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t()
            print("[ok]", t.__name__)
        except AssertionError as e:
            bad += 1
            print("FAIL", t.__name__, e)
    print("%d тестов, ошибок %d" % (len(TESTS), bad))
    sys.exit(1 if bad else 0)
