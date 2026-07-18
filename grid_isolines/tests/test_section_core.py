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
    try:
        sc.build_section(_line(), surfs[:1], step=10.0)
    except ValueError:
        pass
    else:
        raise AssertionError("ожидалась ValueError на одной поверхности")
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
