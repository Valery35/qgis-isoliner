# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тела из поясов. Пояс сам по себе поверхность: контур есть, объёма нет.
# Для объёма, для обрезки сцены с закрытым срезом и для обмена с
# программами, понимающими только замкнутые тела, нужна оболочка: крышка
# снизу, крышка сверху, стенки по всем кольцам, включая дыры.
#
# Критерий один и от способа сборки не зависит: в замкнутой оболочке
# каждое ребро принадлежит ровно двум граням. Ребро с одной гранью это
# дыра, с тремя и более склейка или дубль.
#
#     python grid_isolines/tests/test_solids.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

from grid_isolines import solids as S       # noqa: E402

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 6.0), (0.0, 6.0)]
HOLE = [(3.0, 2.0), (3.0, 4.0), (6.0, 4.0), (6.0, 2.0)]


def test_simple_belt_is_watertight():
    parts = S.shell_faces([SQUARE], 100.0, 110.0)
    n_faces, _n_edges, loose, many = S.edge_report(parts)
    assert n_faces == 6, n_faces          # две крышки и четыре стенки
    assert loose == 0 and many == 0
    assert abs(S.volume(parts) - 10.0 * 6.0 * 10.0) < 1e-6


def test_hole_gets_its_own_wall():
    """Без стенки по внутреннему кольцу оболочка не замкнётся."""
    parts = S.shell_faces([SQUARE, HOLE], 100.0, 110.0)
    _n, _e, loose, many = S.edge_report(parts)
    assert loose == 0 and many == 0
    # объём считается за вычетом отверстия
    assert abs(S.volume(parts) - (10.0 * 6.0 - 3.0 * 2.0) * 10.0) < 1e-6
    walls = [p for p in parts if len(p) == 1 and len(p[0]) == 5]
    assert len(walls) == 8, "по стенке на ребро внешнего и внутреннего"


def test_wall_left_out_shows_up_as_loose_edges():
    """Сторож самого критерия: неполная оболочка обязана валиться."""
    parts = S.shell_faces([SQUARE], 100.0, 110.0)
    broken = parts[:-1]
    _n, _e, loose, many = S.edge_report(broken)
    assert loose == 4 and many == 0


def test_doubled_face_shows_up_as_multiple_edges():
    parts = S.shell_faces([SQUARE], 100.0, 110.0)
    _n, _e, loose, many = S.edge_report(parts + [parts[0]])
    assert many > 0


def test_caps_face_opposite_ways():
    """Крышки обходятся встречно, иначе объём выйдет нулевым."""
    parts = S.shell_faces([SQUARE], 100.0, 110.0)
    bottom, top = parts[0][0], parts[1][0]
    assert abs(bottom[0][2] - 100.0) < 1e-9
    assert abs(top[0][2] - 110.0) < 1e-9
    assert (S.ring_area(bottom) > 0) != (S.ring_area(top) > 0)


def test_hole_ring_runs_against_the_outer_one():
    parts = S.shell_faces([SQUARE, HOLE], 100.0, 110.0)
    cap = parts[0]
    assert len(cap) == 2, "у крышки внешнее кольцо и дыра"
    assert (S.ring_area(cap[0]) > 0) != (S.ring_area(cap[1]) > 0)


def test_zero_thickness_gives_nothing():
    """У пояса нулевой мощности тела нет: стенка выродилась бы в линию."""
    assert S.shell_faces([SQUARE], 105.0, 105.0) == []


def test_degenerate_ring_is_skipped():
    assert S.shell_faces([[(0.0, 0.0), (1.0, 1.0)]], 1.0, 2.0) == []


def test_levels_in_any_order():
    a = S.shell_faces([SQUARE], 110.0, 100.0)
    b = S.shell_faces([SQUARE], 100.0, 110.0)
    assert abs(S.volume(a) - S.volume(b)) < 1e-9


def test_closing_vertex_is_not_doubled_into_an_edge():
    """Кольцо, пришедшее с повтором первой вершины, не даёт лишних рёбер."""
    closed = SQUARE + [SQUARE[0]]
    parts = S.shell_faces([closed], 100.0, 110.0)
    _n, n_edges, loose, many = S.edge_report(parts)
    assert loose == 0 and many == 0
    assert n_edges == 12


def test_triangulated_caps_stay_watertight():
    """С разбивкой крышек оболочка остаётся замкнутой.

    Разбивка нужна потребителю, который не умеет вогнутый контур. Рёбра
    разбивки лежат внутри крышки и попадают в две грани, а рёбра контура
    по-прежнему замыкаются стенками.
    """
    def fan(rings):
        pts = rings[0]
        return [(pts[0], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1)]

    parts = S.shell_faces([SQUARE], 100.0, 110.0, triangulate=fan)
    _n, _e, loose, many = S.edge_report(parts)
    assert loose == 0 and many == 0
    assert abs(S.volume(parts) - 10.0 * 6.0 * 10.0) < 1e-6


def _run():
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok:", name)
            except AssertionError as e:
                fails += 1
                print("СБОЙ:", name, e)
    if fails:
        sys.exit(1)
    print("all solids tests passed")


if __name__ == "__main__":
    _run()
