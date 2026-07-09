# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Headless-тесты полиэдрального ядра (polyhedral.py), без QGIS.

Запуск:  python grid_isolines/tests/test_polyhedral.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))
from grid_isolines import polyhedral as P  # noqa: E402


def test_cube_watertight_and_faces():
    patches = P.cube(sx=100, sy=100, sz=40)
    assert len(patches) == 6                      # 6 четырёхугольных граней
    assert all(len(r) == 4 for r in patches)
    n_edges, n_open = P.edge_audit(patches)
    assert n_open == 0                            # оболочка замкнута
    assert n_edges == 12                          # у куба 12 рёбер


def test_tetrahedron_watertight():
    patches = P.tetrahedron(size=80)
    assert len(patches) == 4 and all(len(r) == 3 for r in patches)
    assert P.is_watertight(patches)


def test_bed_body_watertight():
    patches, verts, faces = P.bed_body(nx=6, ny=6, size=150.0)
    assert len(patches) == len(faces) and len(faces) > 0
    assert all(len(r) == 3 for r in patches)      # треугольные грани
    assert P.is_watertight(patches)               # кровля+подошва+юбка


def test_ring_wkt_closes_and_formats():
    w = P.ring_wkt([(0, 0, 0), (10, 0, 5), (10, 10, 5)])
    assert w.startswith("(") and w.endswith(")")
    # кольцо замкнулось: первая точка повторена в конце
    assert w.count("0 0 0") == 2
    # компактный формат без лишних нулей
    assert "10 0 5" in w


def test_patches_to_wkt_kinds():
    patches = P.cube()
    wp = P.patches_to_wkt(patches, "POLYHEDRALSURFACE")
    assert wp.startswith("POLYHEDRALSURFACE Z (((")
    wm = P.patches_to_wkt(patches, "MULTIPOLYGON")
    assert wm.startswith("MULTIPOLYGON Z (((")
    # тело одинаково, различается только ключевое слово
    assert wp.split(" Z ", 1)[1] == wm.split(" Z ", 1)[1]
    assert P.patches_to_wkt([], "TIN") == "TIN Z EMPTY"


def test_build_example_meta_and_tin():
    patches, kind, meta = P.build_example("bed", nx=5, ny=5, size=120.0)
    assert kind == "POLYHEDRALSURFACE"
    assert meta["watertight"] is True and meta["open_edges"] == 0
    assert meta["name"] == "bed_body" and meta["patches"] == len(patches)

    tri, kind_t, meta_t = P.build_example("cube", as_tin=True)
    assert kind_t == "TIN"
    assert all(len(r) == 3 for r in tri)          # куб триангулирован
    assert meta_t["watertight"] is True           # замкнутость сохранилась


def test_wkt_to_tris_cube_roundtrip():
    # куб -> WKT -> треугольники: 6 четырёхугольников веером = 12 граней
    patches = P.cube(cx=0, cy=0, cz=0, sx=10, sy=10, sz=4)
    wkt = P.patches_to_wkt(patches, "POLYHEDRALSURFACE")
    v, f = P.wkt_to_tris(wkt)
    assert f.shape == (12, 3)
    assert v.shape[1] == 3
    zmin, zmax = v[:, 2].min(), v[:, 2].max()
    assert abs(zmin - (-2.0)) < 1e-6 and abs(zmax - 2.0) < 1e-6


def test_wkt_to_tris_tetra_and_empty():
    v, f = P.wkt_to_tris(P.patches_to_wkt(P.tetrahedron(size=10), "TIN"))
    assert f.shape == (4, 3)                       # 4 треугольные грани
    ve, fe = P.wkt_to_tris("POLYHEDRALSURFACE Z EMPTY")
    assert ve.shape == (0, 3) and fe.shape == (0, 3)


def test_z_range_bed():
    patches, _, _ = P.bed_body(base=140.0, thickness=25.0)
    zmin, zmax = P.z_range(patches)
    assert zmax > zmin and zmin > 0.0             # тело поднято, Z не нулевой


def test_folded_bed_watertight():
    patches, _, _ = P.folded_bed(nx=14, ny=14, size=200.0,
                                 base=120.0, thickness=20.0, folds=3)
    assert P.is_watertight(patches)               # оболочка замкнута
    zmin, zmax = P.z_range(patches)
    assert zmax - zmin > 20.0                      # складки дают рельеф


def test_suite_stack_watertight():
    patches = P.suite(n=3, nx=8, ny=8, size=200.0,
                      base=100.0, thickness=20.0)
    # три отдельные замкнутые оболочки: рёбер с кратностью != 2 нет
    assert P.is_watertight(patches)
    v, f = P.wkt_to_tris(P.patches_to_wkt(patches, "POLYHEDRALSURFACE"))
    assert len(f) > 0
    zmin, zmax = P.z_range(patches)
    assert zmax - zmin > 2 * 20.0                  # стопка выше одного пласта


def test_suite_beds_separate():
    beds = P.suite_beds(n=4, nx=8, ny=8, size=200.0,
                        base=100.0, thickness=20.0)
    assert len(beds) == 4                          # свита отдельными пластами
    for bp in beds:
        assert P.is_watertight(bp)                 # каждый пласт замкнут
    zmaxs = [P.z_range(bp)[1] for bp in beds]
    assert zmaxs == sorted(zmaxs)                  # снизу вверх


def test_slice_triangles_cube():
    import numpy as np
    patches = P.cube(cx=0, cy=0, cz=0, sx=10, sy=10, sz=10)
    v, f = P.wkt_to_tris(P.patches_to_wkt(patches, "POLYHEDRALSURFACE"))
    # горизонтальная плоскость z=0 через центр: след замкнут вокруг сечения
    segs = P.slice_triangles(v, f, (0, 0, 0), (0, 0, 1))
    assert len(segs) >= 4
    assert np.allclose(segs[:, :, 2], 0.0, atol=1e-6)   # весь след на z=0
    # плоскость выше тела - пустой след
    empty = P.slice_triangles(v, f, (0, 0, 100), (0, 0, 1))
    assert len(empty) == 0


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("all %d tests passed" % len(fns))


if __name__ == "__main__":
    _run()
