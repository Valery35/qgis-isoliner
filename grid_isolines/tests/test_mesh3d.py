# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Headless-тесты записи 2DM (mesh3d.grid_to_2dm), без QGIS.

Запуск:  python grid_isolines/tests/test_mesh3d.py
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))
from grid_isolines.mesh3d import grid_to_2dm  # noqa: E402

GT = (0.0, 10.0, 0.0, 100.0, 0.0, -10.0)  # ячейка 10x10, origin (0,100)


def _write(arr, **kw):
    fd, fn = tempfile.mkstemp(suffix=".2dm")
    os.close(fd)
    nv, nt = grid_to_2dm(arr, GT, fn, **kw)
    with open(fn) as f:
        text = f.read()
    os.unlink(fn)
    return nv, nt, text


def test_full_quad():
    arr = np.array([[1.0, 2.0], [3.0, 4.0]])
    nv, nt, text = _write(arr)
    assert nv == 4 and nt == 2
    assert text.startswith("MESH2D\n")
    # центры ячеек: x = 5 и 15, y = 95 и 85
    assert "ND 1 5.000000 95.000000 1.000000" in text
    assert "ND 4 15.000000 85.000000 4.000000" in text
    assert text.count("\nE3T ") == 2


def test_nodata_skips_vertex_and_triangles():
    arr = np.array([[1.0, 2.0], [3.0, np.nan]])
    nv, nt, text = _write(arr)
    assert nv == 3 and nt == 0
    assert "nan" not in text.lower()


def test_vertical_transform():
    arr = np.array([[1.0, 2.0], [3.0, 4.0]])
    nv, nt, text = _write(arr, zscale=2.0, zoffset=5.0)
    assert "ND 1 5.000000 95.000000 7.000000" in text     # 1*2+5
    assert "ND 4 15.000000 85.000000 13.000000" in text   # 4*2+5


def test_thinning():
    arr = np.arange(16, dtype=float).reshape(4, 4)
    nv, nt, text = _write(arr, step=2)
    assert nv == 4 and nt == 2
    # берутся столбцы 0 и 2, ряды 0 и 2: x = 5 и 25, y = 95 и 75
    assert "ND 2 25.000000 95.000000 2.000000" in text
    assert "ND 3 5.000000 75.000000 8.000000" in text


def test_too_small_raises():
    try:
        _write(np.array([[1.0, 2.0]]))
    except ValueError:
        return
    raise AssertionError("expected ValueError for 1-row grid")


def test_all_nan_raises():
    try:
        _write(np.full((3, 3), np.nan))
    except ValueError:
        return
    raise AssertionError("expected ValueError for empty grid")


def test_sample_bilinear():
    from grid_isolines.mesh3d import sample_bilinear
    arr = np.array([[0.0, 10.0], [20.0, 30.0]])
    # центры: (5,95)=0 (15,95)=10 (5,85)=20 (15,85)=30
    v = sample_bilinear(arr, GT, [5.0, 15.0, 10.0, 10.0],
                        [95.0, 85.0, 90.0, 200.0])
    assert v[0] == 0.0 and v[1] == 30.0
    assert abs(v[2] - 15.0) < 1e-9        # центр квадрата
    assert v[3] != v[3]                    # вне грида - NaN


def test_bed_body_watertight():
    from collections import Counter
    from grid_isolines.mesh3d import bed_to_mesh_arrays
    top = np.arange(9, dtype=float).reshape(3, 3) + 50.0
    verts, faces = bed_to_mesh_arrays(top, top - 3.0, GT)
    assert verts.shape == (18, 3) and len(faces) == 32
    c = Counter()
    for tri in faces:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            c[frozenset((int(a), int(b)))] += 1
    # замкнутое тело: каждое ребро ровно в двух гранях
    assert set(c.values()) == {2}


def test_bed_body_zoffset():
    from grid_isolines.mesh3d import bed_to_mesh_arrays
    top = np.full((2, 2), 10.0)
    verts, _ = bed_to_mesh_arrays(top, top - 4.0, GT, zscale=2.0,
                                  zoffset=-1.0)
    assert sorted(set(np.round(verts[:, 2], 6))) == [11.0, 19.0]


def test_polygon_mask():
    from grid_isolines.mesh3d import polygon_mask
    # грид 4x4, ячейка 10, центры 5..35; квадрат покрывает центры (15,85)-(25,75)
    ring = [(10.0, 90.0), (30.0, 90.0), (30.0, 70.0), (10.0, 70.0)]
    m = polygon_mask([ring], GT, (4, 4))
    assert m.sum() == 4
    assert m[1, 1] and m[1, 2] and m[2, 1] and m[2, 2]
    # дырка вторым кольцом выключает центр
    hole = [(18.0, 88.0), (22.0, 88.0), (22.0, 82.0), (18.0, 82.0)]
    m2 = polygon_mask([ring, hole], GT, (4, 4))
    assert m2.sum() == 4  # дырка мала, центры не задевает
    hole2 = [(12.0, 88.0), (28.0, 88.0), (28.0, 82.0), (12.0, 82.0)]
    m3 = polygon_mask([ring, hole2], GT, (4, 4))
    assert m3.sum() == 2 and not m3[1, 1] and not m3[1, 2]


def test_thin_labels_xy():
    from grid_isolines.mesh3d import thin_labels_xy
    # кучка из трёх близких + одна дальняя: из кучки остаётся первая
    pts = [(0, 0), (1, 0), (0, 1), (100, 100)]
    keep = thin_labels_xy(pts, min_dist=10)
    assert keep == [True, False, False, True]
    # нулевая дистанция - подписываются все
    assert all(thin_labels_xy(pts, min_dist=0))
    # пусто - пусто
    assert thin_labels_xy([], min_dist=5) == []


<<<<<<< HEAD
def test_cylinder():
    import numpy as np
    from grid_isolines.mesh3d import cylinder
    v, f = cylinder((0, 0, 0), (0, 0, 10), radius=2.0, sides=12)
    assert v.shape == (24, 3) and f.shape == (24, 3)   # 2 кольца, боковина
    # все вершины на радиусе 2 от оси Z
    r = np.hypot(v[:, 0], v[:, 1])
    assert np.allclose(r, 2.0, atol=1e-6)
    # высоты - только 0 и 10
    assert set(np.round(v[:, 2], 6)) == {0.0, 10.0}
    # нулевая длина - пустой меш
    ve, fe = cylinder((1, 1, 5), (1, 1, 5), radius=1.0)
    assert len(ve) == 0 and len(fe) == 0


=======
>>>>>>> 2dc53d180ea0f6df46ef6849379c188869000699
if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("all mesh3d tests passed")
