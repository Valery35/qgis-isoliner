# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты ядра разреза. Ядро не тянет QGIS, поэтому тест работает с настоящим
# кодом плагина, а не с копией помощников:
#     python grid_isolines/tests/test_section_core.py
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import section_core as sc  # noqa: E402


# --- вспомогательные данные ---------------------------------------------

def _plane_grid(a, b, c, nx=40, ny=30, cell=5.0, x0=1000.0, y0=2000.0):
    """f(x, y) = a*x + b*y + c в центрах ячеек, gt с верхним левым углом."""
    gt = (x0, cell, 0.0, y0 + ny * cell, 0.0, -cell)
    arr = np.empty((ny, nx))
    for j in range(ny):
        for i in range(nx):
            cx = x0 + (i + 0.5) * cell
            cy = y0 + ny * cell - (j + 0.5) * cell
            arr[j, i] = a * cx + b * cy + c
    return arr, gt


def _stack(consts, **kw):
    """Стопка горизонтальных поверхностей с заданными отметками, сверху вниз."""
    out = []
    for k, c in enumerate(consts):
        arr, gt = _plane_grid(0.0, 0.0, c, **kw)
        out.append((arr, gt, "S%d" % (k + 1)))
    return out


def _line():
    """Прямая линия внутри охвата сеток по умолчанию."""
    return [(1010.0, 2050.0), (1180.0, 2050.0)]


# --- выборка растра ------------------------------------------------------

def test_bilinear_exact_on_linear_field():
    arr, gt = _plane_grid(0.3, -0.7, 12.0)
    xs = np.array([1030.0, 1077.5, 1120.25])
    ys = np.array([2040.0, 2061.5, 2100.0])
    got = sc.sample_grid_points(arr, gt, xs, ys, True)
    want = 0.3 * xs - 0.7 * ys + 12.0
    assert np.allclose(got, want), (got, want)


def test_nearest_returns_cell_value():
    arr, gt = _plane_grid(0.0, 0.0, 7.0)
    got = sc.sample_grid_points(arr, gt, np.array([1033.0]),
                                np.array([2044.0]), False)
    assert np.allclose(got, 7.0), got


def test_sampling_outside_is_nan():
    arr, gt = _plane_grid(0.0, 0.0, 7.0)
    got = sc.sample_grid_points(arr, gt, np.array([-500.0]),
                                np.array([-500.0]), True)
    assert np.isnan(got[0]), got


def test_valid_runs_splits_and_drops_singletons():
    m = np.array([1, 1, 0, 1, 1, 1, 0, 1], dtype=bool)
    assert sc.valid_runs(m) == [(0, 1), (3, 5)]


# --- геометрия ломаной ---------------------------------------------------

def test_polyline_length_and_azimuth():
    v = [(0.0, 0.0), (0.0, 100.0), (100.0, 100.0)]
    assert abs(sc.polyline_length(v) - 200.0) < 1e-9
    az = sc.segment_azimuths(v)
    assert abs(az[0] - 0.0) < 1e-9, az      # на север
    assert abs(az[1] - 90.0) < 1e-9, az     # на восток


def test_interpolate_on_bend_is_exact():
    v = [(0.0, 0.0), (0.0, 100.0), (100.0, 100.0)]
    d = np.array([0.0, 50.0, 100.0, 150.0, 200.0])
    xs, ys = sc.interpolate_polyline(v, d)
    assert np.allclose(xs, [0.0, 0.0, 0.0, 50.0, 100.0]), xs
    assert np.allclose(ys, [0.0, 50.0, 100.0, 100.0, 100.0]), ys


def test_interpolate_clamps_outside():
    v = [(0.0, 0.0), (10.0, 0.0)]
    xs, ys = sc.interpolate_polyline(v, np.array([-5.0, 15.0]))
    assert np.allclose(xs, [0.0, 10.0]), xs
    assert np.allclose(ys, [0.0, 0.0]), ys


def test_stations_include_vertices():
    v = [(0.0, 0.0), (0.0, 33.0), (0.0, 100.0)]
    d = sc.stations(v, 10.0)
    assert abs(d[0]) < 1e-12 and abs(d[-1] - 100.0) < 1e-9
    assert np.any(np.abs(d - 33.0) < 1e-9), d
    assert np.all(np.diff(d) > 0), d        # строго возрастает, без дублей


def test_stations_dense_step_is_capped_by_length():
    v = _line()
    d = sc.stations(v, 1.0)
    assert abs(d[-1] - 170.0) < 1e-9, d[-1]


# --- вертикальный масштаб ------------------------------------------------

def test_vex_factor_and_scales_are_direct():
    assert abs(sc.vex_from_mode(sc.VMODE_FACTOR, 20.0, 1000.0, 5.0) - 20.0) < 1e-9
    # Г:В = 1:50 значит вертикальный масштаб крупнее в 50 раз
    assert abs(sc.vex_from_mode(sc.VMODE_SCALES, 50.0, 1000.0, 5.0) - 50.0) < 1e-9


def test_vex_aspect_gives_requested_drawing_shape():
    length, dz, ratio = 2000.0, 8.0, 4.0
    vex = sc.vex_from_mode(sc.VMODE_ASPECT, ratio, length, dz)
    assert abs(length / (dz * vex) - ratio) < 1e-9


def test_vex_aspect_degenerate_dz_is_one():
    assert sc.vex_from_mode(sc.VMODE_ASPECT, 10.0, 100.0, 0.0) == 1.0


def test_scale_caption_parts_kind():
    kind, val, vex = sc.scale_caption_parts(sc.VMODE_SCALES, 50.0, 50.0)
    assert kind == "scales" and val == 50.0 and vex == 50.0


# --- отметки осей --------------------------------------------------------

def test_nice_ticks_are_round_and_inside():
    t = sc.nice_ticks(103.0, 187.0, 5)
    assert t and all(103.0 <= v <= 187.0 for v in t), t
    steps = {round(b - a, 6) for a, b in zip(t, t[1:])}
    assert len(steps) == 1, steps


def test_nice_ticks_empty_on_flat():
    assert sc.nice_ticks(5.0, 5.0, 5) == []


# --- кольца --------------------------------------------------------------

def test_bed_ring_closes_and_has_area():
    d = np.array([0.0, 10.0, 20.0])
    ztop = np.array([100.0, 101.0, 102.0])
    zbot = np.array([90.0, 91.0, 92.0])
    ring = sc.bed_ring_2d(d, ztop, zbot, 2.0, 0, 2)
    assert ring[0] == ring[-1], ring
    assert len(ring) == 7, len(ring)
    # ширина 20, мощность 10, vex 2: площадь чертежа 20 * 10 * 2
    assert abs(sc.ring_area(ring) - 400.0) < 1e-6, sc.ring_area(ring)
    assert not sc.ring_is_degenerate(ring)


def test_bed_ring_3d_closes_in_real_coords():
    xs = np.array([0.0, 5.0]); ys = np.array([0.0, 0.0])
    ztop = np.array([10.0, 12.0]); zbot = np.array([0.0, 1.0])
    pts = sc.bed_ring_3d(xs, ys, ztop, zbot, 0, 1)
    assert pts[0] == pts[-1] and len(pts) == 5, pts
    assert pts[0][2] == 10.0 and pts[2][2] == 1.0, pts


def test_degenerate_ring_detected():
    flat = [(0.0, 5.0), (10.0, 5.0), (10.0, 5.0), (0.0, 5.0), (0.0, 5.0)]
    assert sc.ring_is_degenerate(flat)
    nan = [(0.0, 0.0), (float("nan"), 1.0), (1.0, 1.0), (0.0, 0.0)]
    assert sc.ring_is_degenerate(nan)
    assert sc.ring_is_degenerate([(0.0, 0.0), (1.0, 1.0)])


# --- сборка разреза ------------------------------------------------------

def test_build_section_bed_count_and_thickness():
    surfs = _stack([100.0, 80.0, 55.0])
    r = sc.build_section(_line(), surfs, step=10.0,
                         vmode=sc.VMODE_FACTOR, vscale=1.0)
    assert len(r.beds) == 2, r.beds
    assert abs(r.beds[0]["t_mean"] - 20.0) < 1e-6
    assert abs(r.beds[1]["t_mean"] - 25.0) < 1e-6
    assert r.beds[0]["top"] == "S1" and r.beds[0]["bot"] == "S2"
    assert r.n_degenerate == 0


def test_build_section_inverted_pair_gives_no_bed():
    # подошва выше кровли: пласт не строится
    surfs = _stack([50.0, 90.0])
    r = sc.build_section(_line(), surfs, step=10.0,
                         vmode=sc.VMODE_FACTOR, vscale=1.0)
    assert r.beds == [], r.beds


def test_build_section_applies_vex_to_drawing_only():
    surfs = _stack([100.0, 80.0])
    r1 = sc.build_section(_line(), surfs, step=10.0,
                          vmode=sc.VMODE_FACTOR, vscale=1.0)
    r5 = sc.build_section(_line(), surfs, step=10.0,
                          vmode=sc.VMODE_FACTOR, vscale=5.0)
    a1 = sc.ring_area(r1.beds[0]["runs"][0]["ring2d"])
    a5 = sc.ring_area(r5.beds[0]["runs"][0]["ring2d"])
    assert abs(a5 / a1 - 5.0) < 1e-6, (a1, a5)
    # 3D-стенка в реальных координатах, преувеличение её не трогает
    p1 = r1.beds[0]["runs"][0]["ring3d"][0]
    p5 = r5.beds[0]["runs"][0]["ring3d"][0]
    assert p1 == p5, (p1, p5)


def test_build_section_explicit_vex_overrides_mode():
    surfs = _stack([100.0, 80.0])
    r = sc.build_section(_line(), surfs, step=10.0,
                         vmode=sc.VMODE_ASPECT, vscale=10.0, vex=3.0)
    assert r.vex == 3.0


def test_build_section_step_zero_uses_cell():
    surfs = _stack([100.0, 80.0])
    r = sc.build_section(_line(), surfs, step=0.0,
                         vmode=sc.VMODE_FACTOR, vscale=1.0)
    assert abs(r.step - 5.0) < 1e-9, r.step


def test_build_section_gap_splits_into_runs():
    # дыра nodata в середине кровли даёт два участка полосы
    arr_t, gt = _plane_grid(0.0, 0.0, 100.0)
    arr_t[:, 15:20] = np.nan
    arr_b, _gt = _plane_grid(0.0, 0.0, 80.0)
    surfs = [(arr_t, gt, "top"), (arr_b, gt, "bot")]
    r = sc.build_section(_line(), surfs, step=5.0,
                         vmode=sc.VMODE_FACTOR, vscale=1.0)
    assert len(r.beds) == 1
    assert len(r.beds[0]["runs"]) == 2, len(r.beds[0]["runs"])


def test_build_section_corners_and_table():
    v = [(1010.0, 2050.0), (1100.0, 2050.0), (1100.0, 2120.0)]
    surfs = _stack([100.0, 80.0])
    r = sc.build_section(v, surfs, step=10.0,
                         vmode=sc.VMODE_FACTOR, vscale=1.0)
    assert len(r.corners) == 3
    assert abs(r.corners[1]["d"] - 90.0) < 1e-6
    assert abs(r.corners[0]["az"] - 90.0) < 1e-6
    # две строки подписей плюс две строки на каждый из двух отрезков
    assert len(r.table) == 2 + 2 * 2, len(r.table)


def test_build_section_bbox_covers_table_and_frame():
    v = [(1010.0, 2050.0), (1100.0, 2050.0), (1100.0, 2120.0)]
    surfs = _stack([100.0, 80.0])
    r = sc.build_section(v, surfs, step=10.0,
                         vmode=sc.VMODE_FACTOR, vscale=1.0)
    assert r.bbox_full[0] < 0.0            # столбец подписей слева от нуля
    assert r.bbox_full[1] < r.bbox_frame[1]  # таблица ниже рамки
    assert r.bbox_full[3] == r.bbox_frame[3]
    for (_txt, ring) in r.table:
        for (x, y) in ring:
            assert r.bbox_full[0] - 1e-6 <= x <= r.bbox_full[2] + 1e-6
            assert r.bbox_full[1] - 1e-6 <= y <= r.bbox_full[3] + 1e-6


def test_build_section_rejects_bad_input():
    surfs = _stack([100.0, 80.0])
    for bad in ([(0.0, 0.0)], []):
        try:
            sc.build_section(bad, surfs, step=10.0)
        except ValueError:
            pass
        else:
            raise AssertionError("ожидалась ValueError на вершинах %r" % (bad,))
    # Одна поверхность больше не ошибка: это разрез по рельефу без пластов,
    # см. test_single_surface_gives_line_without_beds.
    try:
        sc.build_section(_line(), [], step=10.0)
    except ValueError:
        pass
    else:
        raise AssertionError("ожидалась ValueError на пустом списке")
    try:
        sc.build_section([(5.0, 5.0), (5.0, 5.0)], surfs, step=10.0)
    except ValueError:
        pass
    else:
        raise AssertionError("ожидалась ValueError на нулевой длине")


# --- раскладка -----------------------------------------------------------

def _boxes(n, w=100.0, h=40.0):
    return [(0.0, -h, w, 0.0) for _ in range(n)]


def _overlap(a, b):
    return (a[0] < b[2] - 1e-9 and b[0] < a[2] - 1e-9
            and a[1] < b[3] - 1e-9 and b[1] < a[3] - 1e-9)


def test_layout_first_is_at_origin():
    for mode in (sc.LAYOUT_STACK, sc.LAYOUT_ROW, sc.LAYOUT_GRID):
        off = sc.layout_offsets(_boxes(5), mode, ncols=2)
        assert off[0] == (0.0, 0.0), (mode, off[0])


def test_layout_stack_keeps_common_distance_zero():
    off = sc.layout_offsets(_boxes(4), sc.LAYOUT_STACK)
    assert all(o[0] == 0.0 for o in off), off
    assert all(a[1] > b[1] for a, b in zip(off, off[1:])), off


def test_layout_row_keeps_common_elevation():
    off = sc.layout_offsets(_boxes(4), sc.LAYOUT_ROW)
    assert all(o[1] == 0.0 for o in off), off
    assert all(a[0] < b[0] for a, b in zip(off, off[1:])), off


def test_layout_grid_wraps_by_columns():
    off = sc.layout_offsets(_boxes(5), sc.LAYOUT_GRID, ncols=2)
    assert off[0][1] == off[1][1], off      # строка держит общую отметку
    assert off[0][0] == off[2][0], off      # столбец держит общий ноль
    assert off[2][1] < off[0][1], off


def test_layout_never_overlaps_mixed_sizes():
    boxes = [(-6.0, -50.0, 100.0, 0.0), (-3.0, -20.0, 40.0, 0.0),
             (-9.0, -80.0, 250.0, 0.0), (0.0, -30.0, 70.0, 0.0),
             (-1.0, -10.0, 15.0, 0.0)]
    for mode, ncols in ((sc.LAYOUT_STACK, 1), (sc.LAYOUT_ROW, 1),
                        (sc.LAYOUT_GRID, 2), (sc.LAYOUT_GRID, 3)):
        off = sc.layout_offsets(boxes, mode, ncols=ncols, gap_frac=0.15)
        placed = [sc.offset_bbox(b, o) for b, o in zip(boxes, off)]
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                assert not _overlap(placed[i], placed[j]), \
                    (mode, ncols, i, j, placed[i], placed[j])


def test_layout_gap_zero_still_touches_not_crosses():
    boxes = _boxes(3)
    off = sc.layout_offsets(boxes, sc.LAYOUT_ROW, gap_frac=0.0)
    placed = [sc.offset_bbox(b, o) for b, o in zip(boxes, off)]
    assert not _overlap(placed[0], placed[1])
    assert abs(placed[1][0] - placed[0][2]) < 1e-9, placed


def test_layout_empty_and_union():
    assert sc.layout_offsets([], sc.LAYOUT_STACK) == []
    assert sc.union_bbox([]) is None
    assert sc.union_bbox([(0.0, 0.0, 1.0, 2.0), (-1.0, 5.0, 0.5, 6.0)]) == \
        (-1.0, 0.0, 1.0, 6.0)


def test_layout_of_real_sections_is_clean():
    surfs = _stack([100.0, 80.0])
    lines = [[(1010.0, 2050.0), (1180.0, 2050.0)],
             [(1010.0, 2060.0), (1090.0, 2060.0)],
             [(1020.0, 2070.0), (1195.0, 2110.0)]]
    secs = [sc.build_section(v, surfs, step=5.0,
                             vmode=sc.VMODE_FACTOR, vscale=10.0)
            for v in lines]
    boxes = [s.bbox_full for s in secs]
    off = sc.layout_offsets(boxes, sc.LAYOUT_STACK, gap_frac=0.15)
    placed = [sc.offset_bbox(b, o) for b, o in zip(boxes, off)]
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            assert not _overlap(placed[i], placed[j]), (i, j)
    total = sc.union_bbox(placed)
    assert total[3] - total[1] > max(b[3] - b[1] for b in boxes)


# --- порядок слоёв в группе ---------------------------------------------

def test_order_key_numbers_first():
    assert sc.order_key(0) < sc.order_key(1) < sc.order_key(10)
    assert sc.order_key(5) < sc.order_key(None)
    assert sc.order_key("3") == sc.order_key(3)


def test_order_key_broken_values_go_last():
    for bad in (None, "", "abc", object()):
        assert sc.order_key(bad) > sc.order_key(999999)


def test_order_sorted_is_stable_for_unnumbered():
    items = [("a", 2), ("b", None), ("c", 0), ("d", None), ("e", 1)]
    got = [n for (n, _o) in sc.order_sorted(items, lambda it: it[1])]
    assert got == ["c", "e", "a", "b", "d"], got


def test_order_sorted_idempotent():
    items = [("a", 2), ("b", None), ("c", 0)]
    once = sc.order_sorted(items, lambda it: it[1])
    twice = sc.order_sorted(once, lambda it: it[1])
    assert once == twice


def test_order_sorted_empty():
    assert sc.order_sorted([], lambda it: it) == []


# --- пакет: выборка, общий масштаб, раскладка ---------------------------

def _three_lines():
    return [[(1010.0, 2050.0), (1180.0, 2050.0)],
            [(1010.0, 2060.0), (1090.0, 2060.0)],
            [(1020.0, 2070.0), (1100.0, 2070.0), (1190.0, 2105.0)]]


def test_sample_section_matches_build():
    surfs = _stack([100.0, 80.0])
    sm = sc.sample_section(_line(), surfs, 5.0, True)
    a = sc.build_section(_line(), surfs, step=5.0,
                         vmode=sc.VMODE_FACTOR, vscale=3.0)
    b = sc.build_section(None, surfs, vex=a.vex, samples=sm)
    assert np.allclose(a.d, b.d) and a.length == b.length
    assert len(a.beds) == len(b.beds)
    assert a.beds[0]["runs"][0]["ring2d"] == b.beds[0]["runs"][0]["ring2d"]
    assert a.beds[0]["top"] == b.beds[0]["top"] == "S1"


def test_sample_section_rejects_bad_input():
    surfs = _stack([100.0, 80.0])
    for bad in ([(0.0, 0.0)], [(5.0, 5.0), (5.0, 5.0)]):
        try:
            sc.sample_section(bad, surfs, 5.0, True)
        except ValueError:
            pass
        else:
            raise AssertionError("ожидалась ValueError на %r" % (bad,))


def test_common_vex_is_single_number_for_scales():
    surfs = _stack([100.0, 80.0])
    sms = [sc.sample_section(v, surfs, 5.0, True) for v in _three_lines()]
    assert sc.common_vex(sms, sc.VMODE_SCALES, 50.0) == 50.0
    assert sc.common_vex(sms, sc.VMODE_FACTOR, 20.0) == 20.0


def test_common_vex_aspect_uses_longest_and_full_range():
    surfs = _stack([100.0, 80.0])
    sms = [sc.sample_section(v, surfs, 5.0, True) for v in _three_lines()]
    got = sc.common_vex(sms, sc.VMODE_ASPECT, 8.0)
    length = max(s.length for s in sms)
    dz = max(s.zmax for s in sms) - min(s.zmin for s in sms)
    assert abs(got - sc.vex_from_mode(sc.VMODE_ASPECT, 8.0, length, dz)) < 1e-9


def test_common_vex_empty_is_one():
    assert sc.common_vex([], sc.VMODE_ASPECT, 10.0) == 1.0


def test_batch_sections_share_one_vex():
    surfs = _stack([100.0, 80.0, 60.0])
    sms = [sc.sample_section(v, surfs, 5.0, True) for v in _three_lines()]
    vex = sc.common_vex(sms, sc.VMODE_SCALES, 50.0)
    secs = [sc.build_section(None, surfs, vex=vex, samples=sm) for sm in sms]
    assert len({s.vex for s in secs}) == 1, [s.vex for s in secs]
    assert all(len(s.beds) == 2 for s in secs)


def test_batch_layout_places_first_at_zero_and_no_overlap():
    surfs = _stack([100.0, 80.0])
    sms = [sc.sample_section(v, surfs, 5.0, True) for v in _three_lines()]
    vex = sc.common_vex(sms, sc.VMODE_SCALES, 50.0)
    secs = [sc.build_section(None, surfs, vex=vex, samples=sm) for sm in sms]
    for mode in (sc.LAYOUT_STACK, sc.LAYOUT_ROW, sc.LAYOUT_GRID):
        off = sc.layout_offsets([s.bbox_full for s in secs], mode, ncols=2)
        assert off[0] == (0.0, 0.0)
        placed = [sc.offset_bbox(s.bbox_full, o) for s, o in zip(secs, off)]
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                assert not _overlap(placed[i], placed[j]), (mode, i, j)


def test_single_section_layout_is_identity():
    surfs = _stack([100.0, 80.0])
    sm = sc.sample_section(_line(), surfs, 5.0, True)
    s1 = sc.build_section(None, surfs, vex=10.0, samples=sm)
    off = sc.layout_offsets([s1.bbox_full], sc.LAYOUT_STACK)
    assert off == [(0.0, 0.0)]


# --- одна поверхность и низ рамки ---------------------------------------

def _relief():
    """Одна наклонная поверхность: рельеф с настоящим размахом высот.

    Ровная плоскость для этих проверок не годится: у неё zmin равен zmax,
    размах нулевой, и отметок на осях законно не возникает.
    """
    arr, gt = _plane_grid(0.2, 0.0, 0.0)
    return [(arr, gt, "Рельеф")]


def test_single_surface_gives_line_without_beds():
    """Одна поверхность это законный разрез: пластов нет, рамка и оси есть.

    Геологический разрез почти всегда начинается с линии рельефа, а пласты
    появляются позже. Раньше инструмент на этом месте отказывался работать.
    """
    r = sc.build_section(_line(), _relief(), step=10.0,
                         vmode=sc.VMODE_FACTOR, vscale=5.0)
    assert r.beds == []
    assert len(r.corners) >= 2
    assert r.ticks
    assert r.bbox_full is not None


def test_zbase_lowers_frame():
    """Низ рамки опускается до заданной отметки, чтобы было где рисовать."""
    plain = sc.build_section(_line(), _relief(), step=10.0,
                             vmode=sc.VMODE_FACTOR, vscale=1.0)
    deep = sc.build_section(_line(), _relief(), step=10.0,
                            vmode=sc.VMODE_FACTOR, vscale=1.0, zbase=50.0)
    assert min(deep.ticks) < min(plain.ticks)
    assert deep.bbox_full[1] < plain.bbox_full[1]


def test_zbase_above_data_ignored():
    """Отметка выше данных рамку не поднимает: это низ, а не обрезка."""
    plain = sc.build_section(_line(), _relief(), step=10.0,
                             vmode=sc.VMODE_FACTOR, vscale=1.0)
    high = sc.build_section(_line(), _relief(), step=10.0,
                            vmode=sc.VMODE_FACTOR, vscale=1.0, zbase=1e6)
    assert high.bbox_full == plain.bbox_full


def test_two_surfaces_still_give_bed():
    """Обычный случай не сломан: две поверхности дают пласт."""
    r = sc.build_section(_line(), _stack([200.0, 150.0]), step=10.0,
                         vmode=sc.VMODE_FACTOR, vscale=5.0)
    assert len(r.beds) == 1


# --- сведение полей нескольких слоёв ------------------------------------

def test_merge_fields_union_and_maps():
    """Общие колонки собираются по всем слоям, карта указывает на источник."""
    names, maps = sc.merge_field_names([["age", "type"], ["age", "note"]])
    assert names == ["age", "type", "note"]
    assert maps[0] == [0, 1, -1]
    assert maps[1] == [0, -1, 1]


def test_merge_fields_renames_reserved():
    """Имя, совпадающее со служебным, переименовывается, а не затирает его.

    Это не косметика: колонка d несёт расстояние вдоль разреза, и атрибут
    объекта с тем же именем молча подменил бы координату.
    """
    names, maps = sc.merge_field_names(
        [["d", "z", "age"]], reserved=("sec", "d", "z"))
    assert names[0] != "d" and names[1] != "z"
    assert names[0].startswith("d") and names[1].startswith("z")
    assert names[2] == "age"
    assert maps[0] == [0, 1, 2]


def test_merge_fields_same_name_is_one_column():
    """Одинаковое имя в разных слоях это одна колонка, а не две."""
    names, _ = sc.merge_field_names([["age"], ["age"], ["age"]])
    assert names == ["age"]


def test_merge_fields_empty_layer():
    """Слой без полей не ломает карту и не добавляет колонок."""
    names, maps = sc.merge_field_names([[], ["age"]])
    assert names == ["age"]
    assert maps[0] == [-1] and maps[1] == [0]


# --- обрезка по линии рельефа -------------------------------------------

def test_profile_merges_parts_and_sorts():
    """Части сводятся в одну ломаную, возрастающую по X."""
    prof = sc.profile_from_lines([([20.0, 10.0], [95.0, 105.0]),
                                  ([0.0], [100.0])])
    px, py = prof
    assert list(px) == [0.0, 10.0, 20.0]
    assert list(py) == [100.0, 105.0, 95.0]


def test_profile_takes_higher_on_duplicate_x():
    """На вертикальном участке берётся верхняя точка.

    Профиль нужен для обрезки сверху, и занизить кромку хуже, чем завысить:
    в первом случае объект молча срежется, во втором останется видимым.
    """
    prof = sc.profile_from_lines([([5.0, 5.0], [90.0, 110.0])])
    assert list(prof[1]) == [110.0]


def test_profile_y_holds_at_edges():
    """За краем профиля держится крайнее значение, а не пустота."""
    prof = sc.profile_from_lines([([10.0, 20.0], [100.0, 90.0])])
    assert sc.profile_y_at(prof, 0.0) == 100.0
    assert sc.profile_y_at(prof, 99.0) == 90.0


def test_profile_takes_lower_on_duplicate_x_for_floor():
    """Для нижней кромки на том же участке берётся нижняя точка.

    Правило зеркальное верхнему: срезать лишнее хуже, чем оставить лишнее
    видимым. Тем же ходом слой из нескольких поверхностей сводится к нижней
    огибающей.
    """
    prof = sc.profile_from_lines([([5.0, 5.0], [90.0, 110.0])],
                                 keep_high=False)
    assert list(prof[1]) == [90.0]


def test_clip_top_follows_relief():
    """Кромка идёт по рельефу и повторяет его переломы."""
    prof = sc.profile_from_lines([([0.0, 10.0, 20.0], [100.0, 105.0, 95.0])])
    xs = sc.band_nodes((prof, None), 2.0, 18.0)
    edge = sc.edge_along(prof, xs, 110.0, True)
    assert edge[0][0] == 2.0 and edge[-1][0] == 18.0
    assert any(abs(x - 10.0) < 1e-9 for x, _y in edge)
    assert all(y <= 110.0 for _x, y in edge)


def test_clip_top_keeps_frame_when_relief_above():
    """Где рельеф выше рамки, кромка остаётся по рамке."""
    prof = sc.profile_from_lines([([0.0, 20.0], [200.0, 200.0])])
    xs = sc.band_nodes((prof,), 5.0, 15.0)
    edge = sc.edge_along(prof, xs, 100.0, True)
    assert all(abs(y - 100.0) < 1e-9 for _x, y in edge)


def test_clip_bottom_follows_floor():
    """Нижняя кромка идёт по линии низа там, где она выше рамки."""
    prof = sc.profile_from_lines([([0.0, 10.0, 20.0], [40.0, 60.0, 30.0])],
                                 keep_high=False)
    xs = sc.band_nodes((prof,), 2.0, 18.0)
    edge = sc.edge_along(prof, xs, 20.0, False)
    assert any(abs(x - 10.0) < 1e-9 for x, _y in edge)
    assert all(y >= 20.0 for _x, y in edge)
    assert max(y for _x, y in edge) > 20.0


def test_clip_bottom_keeps_frame_when_floor_below():
    """Где линия низа ниже рамки, кромка остаётся по рамке."""
    prof = sc.profile_from_lines([([0.0, 20.0], [-50.0, -50.0])],
                                 keep_high=False)
    xs = sc.band_nodes((prof,), 5.0, 15.0)
    edge = sc.edge_along(prof, xs, 0.0, False)
    assert all(abs(y) < 1e-9 for _x, y in edge)


def test_edges_without_profile_are_flat():
    """Без профиля поведение прежнее: прямые кромки по рамке."""
    xs = sc.band_nodes((None, None), 1.0, 4.0)
    assert sc.edge_along(None, xs, 50.0, True) == [(1.0, 50.0), (4.0, 50.0)]
    assert sc.edge_along(None, xs, 10.0, False) == [(1.0, 10.0), (4.0, 10.0)]


def test_band_nodes_are_common_to_both_edges():
    """Станции общие на верх и низ, иначе кольцо завязалось бы бантиком.

    Узлы обеих ломаных попадают в один набор, и кромки считаются в одних и
    тех же станциях. Между общими узлами обе кромки прямые, поэтому пересечься
    незамеченно они не могут.
    """
    ptop = sc.profile_from_lines([([0.0, 7.0, 20.0], [100.0, 90.0, 100.0])])
    pbot = sc.profile_from_lines([([0.0, 13.0, 20.0], [10.0, 20.0, 10.0])],
                                 keep_high=False)
    xs = sc.band_nodes((ptop, pbot), 2.0, 18.0)
    assert xs[0] == 2.0 and xs[-1] == 18.0
    assert any(abs(x - 7.0) < 1e-9 for x in xs)
    assert any(abs(x - 13.0) < 1e-9 for x in xs)
    assert xs == sorted(xs)
    top = sc.edge_along(ptop, xs, 200.0, True)
    bot = sc.edge_along(pbot, xs, 0.0, False)
    assert [x for x, _y in top] == [x for x, _y in bot]


def test_band_ring_is_closed_and_has_area():
    """Кольцо замкнуто и имеет площадь, обход идёт низ-верх."""
    xs = sc.band_nodes((None, None), 0.0, 10.0)
    top = sc.edge_along(None, xs, 100.0, True)
    bot = sc.edge_along(None, xs, 60.0, False)
    ring = sc.band_ring(bot, top)
    assert ring[0] == ring[-1]
    assert abs(abs(sc.ring_area(ring)) - 400.0) < 1e-6


def test_band_collapses_instead_of_bowtie():
    """Линия низа выше рельефа схлопывает полосу, а не выворачивает её.

    Без зажима кромки перехлестнулись бы, и кольцо вышло бы бантиком:
    геометрия с самопересечением, площадь которой ничего не значит.
    """
    ptop = sc.profile_from_lines([([0.0, 10.0], [50.0, 50.0])])
    pbot = sc.profile_from_lines([([0.0, 10.0], [80.0, 80.0])],
                                 keep_high=False)
    xs = sc.band_nodes((ptop, pbot), 1.0, 9.0)
    top = sc.edge_along(ptop, xs, 100.0, True)
    bot = sc.clamp_below(sc.edge_along(pbot, xs, 0.0, False), top)
    assert all(y <= ty for (_x, y), (_tx, ty) in zip(bot, top))
    assert sc.band_is_flat(bot, top)


def test_band_stays_when_floor_below_relief():
    """Обычный случай: низ ниже верха, полоса остаётся и стоит на линии низа."""
    ptop = sc.profile_from_lines([([0.0, 10.0], [90.0, 70.0])])
    pbot = sc.profile_from_lines([([0.0, 10.0], [20.0, 30.0])],
                                 keep_high=False)
    xs = sc.band_nodes((ptop, pbot), 0.0, 10.0)
    top = sc.edge_along(ptop, xs, 100.0, True)
    bot = sc.clamp_below(sc.edge_along(pbot, xs, 0.0, False), top)
    assert not sc.band_is_flat(bot, top)
    assert abs(bot[0][1] - 20.0) < 1e-9 and abs(bot[-1][1] - 30.0) < 1e-9
    assert abs(sc.ring_area(sc.band_ring(bot, top))) > 0.0


def test_apparent_dip_across_strike():
    """Разрез вкрест простирания: видимый угол равен истинному."""
    m, ap = sc.apparent_dip(30.0, 90.0, 90.0)
    assert abs(ap - 30.0) < 1e-9
    assert m < 0                       # след идёт вниз по ходу разреза


def test_apparent_dip_along_strike_is_flat():
    """Разрез по простиранию: пласт ложится горизонтально.

    Это не ошибка, а правда о геометрии, и на неё сразу указали геологи:
    видимый угол зависит от того, под каким углом разрез сечёт простирание.
    """
    m, ap = sc.apparent_dip(30.0, 90.0, 0.0)
    assert abs(ap) < 1e-9
    assert abs(m) < 1e-9


def test_apparent_dip_oblique_is_smaller():
    """Косой разрез: видимый угол меньше истинного, но не ноль."""
    _m, ap = sc.apparent_dip(30.0, 90.0, 45.0)
    assert 0.0 < ap < 30.0
    assert abs(ap - 22.2077) < 1e-3


def test_apparent_dip_sign_flips_with_direction():
    """Встречное падение меняет сторону наклона следа, не его величину.

    Отдельного параметра стороны не нужно: знак косинуса решает сам.
    """
    m1, ap1 = sc.apparent_dip(30.0, 90.0, 90.0)
    m2, ap2 = sc.apparent_dip(30.0, 270.0, 90.0)
    assert abs(ap1 - ap2) < 1e-9
    assert m1 * m2 < 0


def test_apparent_dip_edges():
    """Горизонтальный пласт даёт ноль, вертикальный - бесконечный уклон."""
    assert sc.apparent_dip(0.0, 123.0, 45.0) == (0.0, 0.0)
    m, ap = sc.apparent_dip(90.0, 123.0, 45.0)
    assert ap == 90.0 and m == float("inf")


def test_azimuth_at_distance_follows_the_bend():
    """Азимут берётся по звену у точки, а не по общему направлению.

    На ломаном профиле видимый угол в разных пересечениях разный, и это
    правильно: считать по общей линии значило бы нарисовать одинаковый
    наклон там, где он обязан отличаться.
    """
    verts = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    assert abs(sc.azimuth_at_distance(verts, 50.0) - 90.0) < 1e-9
    assert abs(sc.azimuth_at_distance(verts, 150.0) - 0.0) < 1e-9


def test_merge_renames_service_dip_columns():
    """Поле dip самого слоя не должно спорить со служебной колонкой dip.

    Углы падения добавили служебные колонки dip, dip_az и app_dip. Если не
    внести их в список служебных имён, поле слоя с тем же именем совпадёт
    с колонкой, QGIS дубликат не создаст, и значения съедут в соседние: на
    живом прогоне app_exp показывал 25 у всех объектов, потому что туда
    попадало значение dip.
    """
    per = [["name", "dip", "dip_az", "app_exp"]]
    reserved = ("sec", "sec_id", "src", "label", "d", "z", "d1", "d2",
                "dip", "dip_az", "app_dip")
    names, maps = sc.merge_field_names(per, reserved)
    assert "dip" not in names and "dip_az" not in names
    assert "dip_2" in names and "dip_az_2" in names and "app_exp" in names
    assert maps[0][names.index("dip_2")] == 1
    assert maps[0][names.index("app_exp")] == 3


def test_thin_band_keeps_nodes_paired():
    """Прореживание выбрасывает узлы парами: кромки остаются в одних станциях.

    Прорядить верх и низ порознь нельзя: узлы разъедутся, и там, где
    кромки почти сходятся, кольцо завяжется бантиком. Просьба практика с
    производства была про вес чертежа, а цена ошибки - вывернутая полоса.
    """
    bot = [(float(i), 0.0) for i in range(120)]
    top = [(float(i), 20.0) for i in range(120)]
    b2, t2 = sc.thin_band(bot, top, 0.5)
    assert len(b2) == len(t2) == 2          # прямая полоса сводится к концам
    assert [p[0] for p in b2] == [p[0] for p in t2]


def test_thin_band_respects_the_shape():
    """Ступенька на кромке переживает прореживание."""
    bot = [(float(i), 5.0 if i > 60 else 0.0) for i in range(120)]
    top = [(float(i), 20.0) for i in range(120)]
    b2, t2 = sc.thin_band(bot, top, 0.5)
    assert 3 <= len(b2) <= 8
    assert max(p[1] for p in b2) == 5.0     # ступенька не срезана
    assert [p[0] for p in b2] == [p[0] for p in t2]


def test_thin_band_sees_both_edges():
    """Узел остаётся, если излом есть хотя бы у одной кромки."""
    bot = [(float(i), 0.0) for i in range(60)]
    top = [(float(i), 20.0 + (4.0 if i > 30 else 0.0)) for i in range(60)]
    b2, t2 = sc.thin_band(bot, top, 0.5)
    assert len(b2) > 2                      # излом верха удержал станцию
    assert [p[0] for p in b2] == [p[0] for p in t2]


def test_thin_band_off_by_zero():
    """Ноль отключает прореживание, полоса возвращается как есть."""
    bot = [(float(i), 0.0) for i in range(10)]
    top = [(float(i), 5.0) for i in range(10)]
    b2, t2 = sc.thin_band(bot, top, 0.0)
    assert b2 == bot and t2 == top


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
