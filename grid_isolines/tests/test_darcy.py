# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты удельного расхода по Дарси. Движок не зависит от QGIS:
#     python grid_isolines/tests/test_darcy.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hydro  # noqa: E402

ND = -9999.0


def _linear_head(slope, nx=40, ny=30, cell=10.0):
    """Напор с постоянным уклоном по X: h = slope*x. Тогда |∇h| = |slope|."""
    x = np.arange(nx) * cell
    return np.tile(slope * x, (ny, 1)), cell


def test_gradient_of_linear_is_slope():
    h, cell = _linear_head(0.02)
    mag, az = hydro.head_gradient(h, cell, cell, ND)
    m = mag[mag != ND]
    assert np.allclose(m, 0.02, atol=1e-9)         # |∇h| = уклон


def test_darcy_q_equals_K_times_grad():
    """q = K·|∇h|: на линейном напоре с уклоном s и постоянном K даёт K·s."""
    slope = 0.015
    h, cell = _linear_head(slope)
    mag, az = hydro.head_gradient(h, cell, cell, ND)
    gvalid = mag != ND
    K = np.full_like(mag, 3.0)                      # м/сут
    q = np.where(gvalid, mag * K, ND)
    qq = q[q != ND]
    assert np.allclose(qq, 3.0 * slope, atol=1e-9)  # q = K·s


def test_darcy_Q_equals_T_times_grad():
    slope = 0.03
    h, cell = _linear_head(slope)
    mag, az = hydro.head_gradient(h, cell, cell, ND)
    gvalid = mag != ND
    T = np.full_like(mag, 50.0)                      # м²/сут
    Qw = np.where(gvalid, mag * T, ND)
    qq = Qw[Qw != ND]
    assert np.allclose(qq, 50.0 * slope, atol=1e-7)  # Q = T·s


def test_log_input_exponentiation():
    """Лог-вход: exp(ln K) восстанавливает K, q считается по нему."""
    K = np.array([0.004, 0.03, 4.09])
    lnK = np.log(K)
    assert np.allclose(np.exp(lnK), K, rtol=1e-12)
    slope = 0.01
    q = slope * np.exp(lnK)
    assert np.allclose(q, slope * K, rtol=1e-12)


def test_flow_direction_down_gradient():
    """Поток направлен вниз по градиенту: напор растёт на восток -> поток на запад (270°)."""
    h, cell = _linear_head(0.02)                    # h растёт с x (на восток)
    mag, az = hydro.head_gradient(h, cell, cell, ND)
    a = az[(az != ND)]
    assert np.allclose(a, 270.0, atol=1e-6)         # на запад


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
