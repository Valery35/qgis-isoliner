# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты ядра отчёта по створу. Синтетический рельеф, эталонные цифры
# посчитаны руками:
#     python grid_isolines/tests/test_topo_gauge.py
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import topo_flow, topo_gauge as tg  # noqa: E402

CELL = 10.0  # м


def _ramp():
    """Наклонная плоскость: вода течёт строго на восток вдоль строк.

    z падает с ростом столбца, ряды независимы. Аккумуляция в ячейке
    (r, c) равна c + 1, главный водоток от восточного края - вся строка.
    """
    ny, nx = 4, 6
    z = np.tile(np.arange(nx, 0, -1, dtype=np.float64), (ny, 1))  # 6..1
    _, down = topo_flow.d8_directions(z)
    acc = topo_flow.flow_accumulation(down, (ny, nx))
    return z, down, acc, (ny, nx)


def _valley():
    """V-долина с осевым водотоком по среднему столбцу, сток на юг.

    z = |c - 2| * 10 - r: бока круче оси, ось падает к югу. Весь грид
    стекает в низ среднего столбца.
    """
    ny, nx = 5, 5
    r = np.arange(ny, dtype=np.float64)[:, None]
    c = np.arange(nx, dtype=np.float64)[None, :]
    z = np.abs(c - 2) * 10.0 - r
    _, down = topo_flow.d8_directions(z)
    acc = topo_flow.flow_accumulation(down, (ny, nx))
    return z, down, acc, (ny, nx)


# --- притяжка ------------------------------------------------------------

def test_snap_to_max_acc():
    z, down, acc, shape = _ramp()
    # точка на середине строки 1, радиус 2 ячейки: максимум в окне - самый
    # восточный столбец. Ряды одинаковы, максимумы равны, берётся первый по
    # обходу окна - верхняя строка окна (документированный детерминизм)
    r, c = tg.snap_to_max_acc(acc, 1, 3, 2)
    assert (r, c) == (0, 5)
    # радиус 0: без притяжки
    assert tg.snap_to_max_acc(acc, 1, 3, 0) == (1, 3)
    # выход за край зажимается
    assert tg.snap_to_max_acc(acc, -5, 99, 0) == (0, 5)


def test_snap_deterministic_on_ties():
    acc = np.zeros((3, 3))
    acc[0, 1] = 7.0
    acc[2, 1] = 7.0
    # равные максимумы: берётся первый по обходу окна (верхний)
    assert tg.snap_to_max_acc(acc, 1, 1, 1) == (0, 1)


# --- бассейн и зональная статистика --------------------------------------

def test_basin_mask_ramp():
    z, down, acc, shape = _ramp()
    ny, nx = shape
    # створ на восточном краю строки 2: бассейн - ровно эта строка
    seed = 2 * nx + (nx - 1)
    mask = tg.basin_mask(down, shape, seed)
    assert int(mask.sum()) == nx
    assert mask[2].all() and not mask[1].any() and not mask[3].any()


def test_basin_mask_valley_full_grid():
    z, down, acc, shape = _valley()
    ny, nx = shape
    seed = (ny - 1) * nx + 2  # низ оси долины
    mask = tg.basin_mask(down, shape, seed)
    assert int(mask.sum()) == ny * nx  # весь грид стекает через створ
    assert int(acc[ny - 1, 2]) == ny * nx


def test_zonal_stats_nodata_and_empty():
    v = np.array([[1.0, 2.0], [3.0, float("nan")]])
    mask = np.ones((2, 2), dtype=bool)
    mean, vmin, vmax = tg.zonal_stats(v, mask)
    assert (mean, vmin, vmax) == (2.0, 1.0, 3.0)  # nan исключён
    nodata = np.array([[False, True], [False, True]])
    mean, vmin, vmax = tg.zonal_stats(v, mask, nodata)
    assert (mean, vmin, vmax) == (2.0, 1.0, 3.0)
    assert tg.zonal_stats(v, np.zeros((2, 2), dtype=bool)) == (
        None, None, None)


# --- главный водоток ------------------------------------------------------

def test_trace_ramp_row():
    z, down, acc, shape = _ramp()
    ny, nx = shape
    seed = 1 * nx + (nx - 1)
    length, path = tg.trace_main_stream(down, acc, shape, seed, CELL)
    # вся строка: 5 шагов по 10 м, путь от створа к истоку
    assert abs(length - (nx - 1) * CELL) < 1e-9
    assert path[0] == seed and path[-1] == 1 * nx + 0
    assert len(path) == nx


def test_trace_valley_axis():
    z, down, acc, shape = _valley()
    ny, nx = shape
    seed = (ny - 1) * nx + 2
    length, path = tg.trace_main_stream(down, acc, shape, seed, CELL)
    # вверх по оси долины 4 шага, а с верха оси трасса правильно уходит в
    # сильнейший приток (боковые ячейки строки 0) до ячейки без притоков:
    # водоток ведётся до истока, всего 6 шагов по 10 м
    assert abs(length - 6 * CELL) < 1e-9
    assert [p % nx for p in path] == [2, 2, 2, 2, 2, 3, 4]
    assert path[-1] == 4  # исток: угловая ячейка без притоков


def test_trace_stops_at_source():
    z, down, acc, shape = _ramp()
    ny, nx = shape
    # створ в истоке (западный столбец): пути нет, длина ноль
    length, path = tg.trace_main_stream(down, acc, shape, 0, CELL)
    assert length == 0.0 and path == [0]


# --- сборный отчёт --------------------------------------------------------

def test_gauge_report_ramp_hand_numbers():
    z, down, acc, shape = _ramp()
    ny, nx = shape
    seed = 2 * nx + (nx - 1)
    rep = tg.gauge_report(z, down, acc, shape, seed, CELL)
    # площадь: 6 ячеек по 100 м² = 600 м² = 0.0006 км²
    assert abs(rep["area_km2"] - 6 * CELL * CELL / 1e6) < 1e-12
    assert rep["cells"] == nx
    # высоты строки: 6..1
    assert (rep["z_min"], rep["z_max"]) == (1.0, 6.0)
    assert abs(rep["z_mean"] - 3.5) < 1e-12
    assert rep["z_gauge"] == 1.0
    # водоток: 50 м, падение 6 - 1 = 5 м, уклон 100 промилле
    assert abs(rep["stream_km"] - 0.05) < 1e-12
    assert abs(rep["stream_fall_m"] - 5.0) < 1e-12
    assert abs(rep["stream_ppm"] - 100.0) < 1e-9
    # уклон бассейна не подавали - None, а не ноль
    assert rep["slope_mean"] is None


def test_gauge_report_valley_with_slope():
    z, down, acc, shape = _valley()
    ny, nx = shape
    seed = (ny - 1) * nx + 2
    slope = np.full(shape, 2.5)
    rep = tg.gauge_report(z, down, acc, shape, seed, CELL, slope=slope)
    assert rep["cells"] == ny * nx
    assert abs(rep["slope_mean"] - 2.5) < 1e-12
    # трасса до истока (0, 4): z от -4 (створ) до 20, падение 24 м на 60 м
    assert abs(rep["stream_fall_m"] - 24.0) < 1e-12
    assert abs(rep["stream_km"] - 0.06) < 1e-12
    assert abs(rep["stream_ppm"] - 400.0) < 1e-9
    assert rep["z_gauge"] == -4.0


def test_gauge_report_nodata_excluded():
    z, down, acc, shape = _ramp()
    ny, nx = shape
    nodata = np.zeros(shape, dtype=bool)
    nodata[2, 0] = True  # исток строки выключен из статистики
    seed = 2 * nx + (nx - 1)
    rep = tg.gauge_report(z, down, acc, shape, seed, CELL,
                          nodata_mask=nodata)
    assert rep["cells"] == nx - 1
    assert rep["z_max"] == 5.0  # ячейка с z=6 в nodata


def test_report_lines():
    z, down, acc, shape = _ramp()
    seed = 2 * shape[1] + (shape[1] - 1)
    rep = tg.gauge_report(z, down, acc, shape, seed, CELL)
    lines = tg.report_lines(rep)
    assert len(lines) == 2
    assert "0.001" in lines[0] and "1.00...6.00" in lines[0]
    assert "100.0" in lines[1]
    # переводчику отдаются шаблоны, тождественный ничего не меняет
    seen = []
    assert tg.report_lines(rep, lambda t: (seen.append(t) or t)) == lines
    assert any("%s" in t for t in seen)


def test_report_keys_order_stable():
    assert tg.REPORT_KEYS[0] == "area_km2"
    assert tg.REPORT_KEYS[-1] == "cells"
    assert len(set(tg.REPORT_KEYS)) == len(tg.REPORT_KEYS)


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL %s: %s" % (name, exc))
    print("%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
