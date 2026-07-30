# -*- coding: utf-8 -*-
"""Тесты снятия отметок с примыкающих горизонталей (topo_snapz).

Запуск: python test_topo_snapz.py.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import topo_snapz as sz  # noqa: E402


def test_crossing_contours_give_profile():
    """Бровка поперёк горизонталей: профиль линеен между пересечениями."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(20.0, -10.0), (20.0, 10.0)], "z": 110.0},
                {"pts": [(60.0, -10.0), (60.0, 10.0)], "z": 112.0},
                {"pts": [(90.0, -10.0), (90.0, 10.0)], "z": 113.5}]
    done, skipped = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert not skipped and len(done) == 1
    zs = done[0]["zs"]
    assert done[0]["n_samples"] == 3
    assert abs(zs[0] - 110.0) < 1e-9          # до первой точки константа
    assert abs(zs[-1] - 113.5) < 1e-9         # после последней константа
    # в середине линейная интерполяция: вершин у линии две, проверим
    # плотнее через профиль по дуге
    dense = [(x, 0.0) for x in range(0, 101, 10)]
    done2, _ = sz.snap_elevations([{"pts": dense}], contours, tol=0.5)
    zs2 = done2[0]["zs"]
    assert abs(zs2[4] - 111.0) < 1e-9         # x=40: середина 110..112


def test_node_ends_within_tolerance():
    """Горизонтали, доведённые до линии узлами: концы в допуске работают."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(30.0, 0.3), (30.0, 20.0)], "z": 105.0},
                {"pts": [(70.0, -0.4), (70.0, -20.0)], "z": 107.0}]
    done, skipped = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert not skipped
    assert done[0]["n_samples"] == 2
    dense = [(x, 0.0) for x in range(0, 101, 5)]
    done2, _ = sz.snap_elevations([{"pts": dense}], contours, tol=0.5)
    zs = done2[0]["zs"]
    assert abs(zs[10] - 106.0) < 1e-9         # x=50 между 105 и 107


def test_out_of_tolerance_is_ignored():
    """Конец горизонтали дальше допуска не считается примыканием."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(30.0, 2.0), (30.0, 20.0)], "z": 105.0}]
    done, skipped = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert not done and len(skipped) == 1
    assert "не примыкает" in skipped[0]["reason"]


def test_single_contour_gives_constant():
    """Одна примкнувшая горизонталь - постоянная отметка по всей линии."""
    line = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(40.0, -5.0), (40.0, 5.0)], "z": 108.0}]
    done, _ = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert done[0]["n_samples"] == 1
    assert np.allclose(done[0]["zs"], 108.0)


def test_extra_fields_preserved():
    """Служебные поля линии (link, kind) проходят сквозь ядро."""
    line = {"pts": [(0.0, 0.0), (10.0, 0.0)], "link": "a", "kind": "brow"}
    contours = [{"pts": [(5.0, -1.0), (5.0, 1.0)], "z": 100.0}]
    done, _ = sz.snap_elevations([line], contours, tol=0.5)
    assert done[0]["link"] == "a" and done[0]["kind"] == "brow"


def test_contour_without_z_skipped():
    """Горизонталь без отметки не участвует."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(30.0, -5.0), (30.0, 5.0)], "z": None},
                {"pts": [(60.0, -5.0), (60.0, 5.0)], "z": 111.0}]
    done, _ = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert done and done[0]["n_samples"] == 1
    assert np.allclose(done[0]["zs"], 111.0)


def test_curved_line_arc_parametrisation():
    """Г-образная бровка: интерполяция идёт по дуге, а не по прямой."""
    contours = [{"pts": [(10.0, -5.0), (10.0, 5.0)], "z": 100.0},
                {"pts": [(45.0, 40.0), (55.0, 40.0)], "z": 109.0}]
    dense = ([(x, 0.0) for x in range(0, 51, 5)] +
             [(50.0, y) for y in range(5, 51, 5)])
    done, _ = sz.snap_elevations([{"pts": dense}], contours, tol=0.5)
    zs = done[0]["zs"]
    # дуга до угла 50, до второй точки 90; точка s=50 (угол):
    # (50-10)/(90-10) = 0.5 пути, отметка 104.5
    i_corner = 10
    assert abs(zs[i_corner] - 104.5) < 1e-6


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print("ok:", n)
        except AssertionError as ex:
            failed += 1
            print("FAIL:", n, "-", ex)
    print("\n%d тестов, ошибок %d" % (len(fns), failed))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run()
