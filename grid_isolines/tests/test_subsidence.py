# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Ядро группы «9. Сдвижения» против Указаний и базы наблюдений.

Эталонов три.
1. Таблица 2 Указаний редакции 2014 года (L = 500 м, l₀ = 50 м, ηₘ = 1 м).
   Наклон, mₑ и ε сходятся со всеми строками. Кривизна сходится везде, кроме
   z = 0.20: там в таблице -0.630·10⁻⁴, а по формуле 4.22 из соседних
   наклонов той же таблицы выходит -0.450·10⁻⁴. Тест держит формулу.
2. Ведомость «Наклоны и кривизна» базы наблюдений УКК (профиль 1, репер
   1_13): база считает без приведения к 15 м, и ядро со снятым приведением
   обязано дать её числа.
3. Грид, у которого узлы совпадают с реперами: по гриду и по реперам
   должно выйти одно и то же, иначе грид и профиль несравнимы.
"""
import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from grid_isolines import subsidence as S  # noqa: E402

# таблица 2 редакции 2014 года: z -> (i·1e3, K·1e4, mₑ, ε·1e4)
TABLE2 = {
    0.10: (1.920, -0.360, 0.0665, -1.197),
    0.15: (2.640, -0.480, 0.0575, -1.380),
    0.25: (4.440, -0.240, 0.0817, -0.980),
    0.30: (4.800, -0.180, 0.0923, -0.831),
    0.35: (5.160, 0.120, 0.1054, 0.632),
    0.40: (4.320, 0.390, 0.0638, 1.244),
    0.45: (3.600, 0.330, 0.0696, 1.148),
    0.50: (3.000, 0.288, 0.0748, 1.077),
    0.55: (2.448, 0.240, 0.0817, 0.980),
    0.60: (2.040, 0.204, 0.0878, 0.896),
    0.65: (1.632, 0.168, 0.0947, 0.795),
    0.70: (1.368, 0.120, 0.1054, 0.632),
    0.75: (1.152, 0.096, 0.1113, 0.534),
    0.80: (0.984, 0.060, 0.1211, 0.363),
    0.85: (0.912, 0.036, 0.1283, 0.231),
    0.90: (0.840, 0.024, 0.1321, 0.159),
}


def test_reduction_coefficients():
    assert S.q_tilt(10) == 1.0 and S.q_tilt(15) == 1.0
    assert S.q_tilt(45) == pytest.approx(1.2, abs=1e-3)
    assert S.q_tilt(100) == pytest.approx(1.2, abs=1e-3)
    assert S.q_curv(50) == pytest.approx(1.25, abs=1e-3)
    assert S.q_eps(45) == pytest.approx(1.5, abs=1e-3)
    assert S.q_eps(30) == pytest.approx(1.3535, abs=1e-4)


def test_s_function_nodes_and_monotony():
    assert np.allclose(S.s_func(S.S_Z), S.S_TABLE)
    zz = np.linspace(0, 1, 2001)
    s = S.s_func(zz)
    assert np.all(np.diff(s) <= 1e-12)
    assert S.s_func(-0.3) == 1.0 and S.s_func(1.4) == 0.0


def test_table2_tilt_every_row():
    """Наклон по формуле 4.22: точки через 25 м, интервал j..j+2 = 50 м."""
    L, lo = 500.0, 50.0
    for z, (i_ref, _k, _m, _e) in TABLE2.items():
        a = S.s_func(round(z - 0.05, 2))
        b = S.s_func(round(z + 0.05, 2))
        tilt = (a - b) / lo * S.q_tilt(lo)
        assert tilt * 1e3 == pytest.approx(i_ref, abs=1e-3), z


def test_table2_curvature_and_its_misprint():
    lo = 50.0
    z = np.round(np.arange(0.0, 1.0001, 0.1), 2)
    t, k = S.profile_deformations(z * 500.0, S.s_func(z))
    got = dict(zip(z, k * 1e4))
    for zz in (0.1, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
        assert got[zz] == pytest.approx(TABLE2[zz][1], abs=1e-3), zz
    # строка z = 0.20: в таблице -0.630, по формуле -0.450
    assert got[0.2] == pytest.approx(-0.450, abs=1e-3)
    # наклоны интервалов: рост оседания к оси мульды, знак минус по ходу
    assert t[1] * 1e3 == pytest.approx(-2.640, abs=1e-3)
    assert lo == 50.0


def test_table2_me_and_strain_by_the_2014_formula():
    for z, (_i, k_ref, m_ref, e_ref) in TABLE2.items():
        k = k_ref * 1e-4
        assert S.m_e(k) == pytest.approx(m_ref, abs=2e-4), z
        e = S.eps_from_curvature(k, 50.0)   # редакция 2014 года: l₀
        assert e * 1e4 == pytest.approx(e_ref, abs=3e-3), z


def test_current_formula_is_ten_times_larger_at_l0_equal_l_over_10():
    k = 0.39e-4
    assert S.eps_from_curvature(k, 500.0) == pytest.approx(
        10 * S.eps_from_curvature(k, 50.0))


def test_observation_database_numbers_without_reduction():
    """Профиль 1, реперы 1_12, 1_13, Рп. 63 из ведомости базы УКК."""
    dist = [0.0, 29.78, 29.78 + 22.99]
    eta = [0.0, 2.4e-3, -0.3e-3]
    t, k = S.profile_deformations(dist, eta, reduce=False)
    assert t[0] * 1e3 == pytest.approx(0.081, abs=5e-4)
    assert t[1] * 1e3 == pytest.approx(-0.117, abs=5e-4)
    assert k[1] * 1e6 == pytest.approx(-7.5061, abs=2e-3)


def _extruded(fn, nx, ny, cell):
    x = np.arange(nx) * cell
    row = fn(x)
    return np.tile(row, (ny, 1)), x


def test_grid_equals_benchmarks_on_nodes():
    L, cell, base = 500.0, 5.0, 50.0

    def fn(x):
        return S.s_func(np.abs(x - 600.0) / L)

    eta, x = _extruded(fn, 241, 21, cell)
    res = S.grid_deformations(eta, cell, base)
    nodes = np.arange(600.0, 1101.0, base)
    t, k = S.profile_deformations(nodes, fn(nodes))
    r = 10
    for j in range(1, len(nodes) - 1):
        c = int(round(nodes[j] / cell))
        assert res["k_dir"][r, c] == pytest.approx(k[j], rel=1e-9, abs=1e-12)
        assert res["k_max"][r, c] >= res["k_min"][r, c]


def test_linear_field_tilt_and_azimuth():
    cell = 2.0
    ny, nx = 50, 60
    yy, xx = np.mgrid[0:ny, 0:nx] * cell
    # оседание растёт на восток на 3 мм/м, на север (вверх по строкам) на 4
    eta = 0.003 * xx - 0.004 * yy
    res = S.grid_deformations(eta, cell, 10.0, reduce=False)
    assert np.nanmax(np.abs(res["tilt"] - 0.005)) < 1e-12
    az = res["azimuth"][25, 30]
    assert az == pytest.approx(math.degrees(math.atan2(3, 4)), abs=1e-9)
    assert np.nanmax(np.abs(res["k_dir"])) < 1e-12


def test_demo_trough_main_section_is_the_table():
    H = 500.0 / (2.0 / math.tan(math.radians(55.0)))
    eta, info = S.demo_trough(440, 320, 5.0, H, 1500.0, 1100.0, 1.0)
    assert info["L"] == pytest.approx(500.0)
    assert info["plateau_x"] == pytest.approx(1500.0 - 2 * H / math.tan(
        math.radians(55.0)))
    assert eta.max() == pytest.approx(1.0)
    assert eta.min() >= 0.0
    # в центре дна оседание максимально, у края растра нулевое
    assert eta[160, 220] == pytest.approx(1.0)
    assert eta[0, 0] == pytest.approx(0.0)
