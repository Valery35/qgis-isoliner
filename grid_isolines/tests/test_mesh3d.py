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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("all mesh3d tests passed")
