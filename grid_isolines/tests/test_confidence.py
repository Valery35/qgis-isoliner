# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Уверенность горизонтали (перепад на ячейку против шума матрицы):
#     python grid_isolines/tests/test_confidence.py
import ast
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import validate_core as vc  # noqa: E402


# --- перепад на ячейку ---------------------------------------------------

def test_drop_is_metres_not_slope():
    """Уклон 0.02 при ячейке 5 м даёт перепад 0.1 м на ячейку.

    Размерность высоты принципиальна: только так величину можно сравнивать с
    сечением рельефа и с точностью данных.
    """
    n = 40
    _j, i = np.mgrid[0:n, 0:n]
    z = 100.0 + 0.02 * i * 5.0
    d = vc.drop_per_cell(z, 5.0)
    # Допуск под float32: перепад считается в одинарной точности ради памяти,
    # на рабочих матрицах это сотни мегабайт разницы. 1e-5 м это сотая доля
    # миллиметра, для рельефа абсолютный ноль.
    assert abs(np.median(d[2:-2, 2:-2]) - 0.1) < 1e-5, np.median(d)


def test_drop_zero_on_flat():
    assert np.allclose(vc.drop_per_cell(np.full((20, 20), 7.0), 5.0), 0.0)


def test_drop_does_not_depend_on_declared_cell_size():
    """Неочевидное, но верное свойство.

    Перепад на ячейку это разность высот между соседними ячейками, и она не
    зависит от того, каким числом объявлен размер ячейки: деление на шаг при
    взятии градиента и умножение на шаг сокращаются. Величина остаётся
    свойством данных, а не системы координат, и потому её можно сравнивать с
    сечением рельефа.
    """
    n = 30
    _j, i = np.mgrid[0:n, 0:n]
    z = 100.0 + 0.1 * i
    d5 = vc.drop_per_cell(z, 5.0)[2:-2, 2:-2]
    d10 = vc.drop_per_cell(z, 10.0)[2:-2, 2:-2]
    assert np.allclose(d5, d10)
    assert abs(np.median(d5) - 0.1) < 1e-5, np.median(d5)


# --- разрезание по вершинам ---------------------------------------------

def test_clean_line_stays_whole():
    keep, cut = vc.confident_runs([False] * 6)
    assert keep == [(0, 5)] and cut == []


def test_single_weak_vertex_does_not_break_line():
    """Одна шумная ячейка не повод рвать горизонталь."""
    keep, cut = vc.confident_runs([False, False, True, False, False])
    assert keep == [(0, 4)] and cut == []


def test_two_weak_vertices_do_not_break_line():
    keep, cut = vc.confident_runs([False, True, True, False, False])
    assert keep == [(0, 4)] and cut == []


def test_three_weak_vertices_break_line():
    keep, cut = vc.confident_runs(
        [False, False, True, True, True, False, False])
    assert keep == [(0, 1), (5, 6)], keep
    assert cut == [(1, 5)], cut


def test_parts_share_boundary_vertex_so_no_gap_appears():
    """Куски делят граничную вершину: в геометрии не должно быть дырки."""
    keep, cut = vc.confident_runs(
        [False, False, True, True, True, False, False])
    assert keep[0][1] == cut[0][0], (keep, cut)
    assert cut[0][1] == keep[1][0], (keep, cut)


def test_weak_tail_is_cut_off():
    keep, cut = vc.confident_runs([False, False, False, True, True, True])
    assert keep == [(0, 2)] and cut == [(2, 5)]


def test_fully_weak_line_keeps_nothing():
    keep, cut = vc.confident_runs([True] * 5)
    assert keep == [] and cut == [(0, 4)]


def test_min_run_defaults_to_three():
    flags = [False, True, True, False]
    assert vc.confident_runs(flags)[1] == []
    assert vc.confident_runs(flags, min_run=2)[1] != []


def test_empty_input():
    assert vc.confident_runs([]) == ([], [])


# --- сводка по линии -----------------------------------------------------

def test_summary_numbers():
    info = vc.line_confidence([0.05, 0.0004, 0.05], 0.005)
    assert abs(info["drop_min"] - 0.0004) < 1e-12
    assert info["n_low"] == 1


def test_summary_ignores_nan():
    info = vc.line_confidence([0.2, float("nan"), 0.4], 0.005)
    assert abs(info["drop_mean"] - 0.3) < 1e-12


def test_summary_none_when_no_data():
    assert vc.line_confidence([float("nan")] * 3, 0.005) is None
    assert vc.line_confidence([], 0.005) is None


# --- сквозной сюжет ------------------------------------------------------

def test_gentle_slope_survives_while_water_is_cut():
    """Ради этого порог и задан долей сечения, а не абсолютным уклоном.

    Числа с натуры: пологий склон даёт на ячейке сантиметры, водная гладь
    миллиметры. При сечении 0.5 м порог составляет 5 мм.
    """
    thr = 0.01 * 0.5
    slope = np.full(8, 0.02)
    water = np.full(8, 0.0004)
    assert vc.line_confidence(slope, thr)["n_low"] == 0
    assert vc.line_confidence(water, thr)["n_low"] == 8
    keep_s, cut_s = vc.confident_runs(slope < thr)
    keep_w, cut_w = vc.confident_runs(water < thr)
    assert keep_s and not cut_s
    assert not keep_w and cut_w


def test_contour_entering_a_lake_is_split():
    drops = np.array([0.05] * 6 + [0.0004] * 10 + [0.05] * 6)
    thr = 0.01 * 0.5
    keep, cut = vc.confident_runs(drops < thr)
    assert len(keep) == 2, keep
    assert len(cut) == 1, cut


# --- обвязка -------------------------------------------------------------

def test_marking_runs_before_short_line_filter():
    """Порядок важен: иначе обрывки от разрезания попадут в фильтр длин."""
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "isolines.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
          and n.name == "_contour_lines"][0]
    body = ast.get_source_segment(src, fn)
    i_conf = body.find("_mark_confidence(")
    i_short = body.find("Фильтр коротких линий")
    assert i_conf > -1, "разметка уверенности не вызывается"
    assert i_short > -1
    assert i_conf < i_short, "разметка должна идти до фильтра коротких линий"


def test_nothing_is_deleted_silently():
    """Слабые куски помечаются, а не выбрасываются."""
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "isolines.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert "lowconf" in src
    tree = ast.parse(src)
    fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
          and n.name == "_mark_confidence"][0]
    body = ast.get_source_segment(src, fn)
    assert "drop_min" in body and "drop_mean" in body


def test_tool_exposes_option_and_defaults_to_off():
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "algorithms.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    assert 'CONFID, CONF_FRAC = "CONFID", "CONF_FRAC"' in src
    i = src.index("self.CONFID, self.tr(")
    seg = src[i:i + 400]
    assert "_dv(self, self.CONFID, 0)" in seg, "по умолчанию должно быть выключено"


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
