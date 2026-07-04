# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Headless-тесты 3D-просмотра: импорт без Qt, геометрия, автопрореживание.

Запуск:  python grid_isolines/tests/test_viewer3d.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

GT = (0.0, 10.0, 0.0, 100.0, 0.0, -10.0)


def test_import_headless():
    """Модуль должен импортироваться без Qt и pyqtgraph."""
    import grid_isolines.viewer3d as v3
    assert callable(v3.show_viewer)


def test_mesh_arrays_shapes():
    from grid_isolines.mesh3d import grid_to_mesh_arrays
    arr = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, np.nan]])
    verts, faces = grid_to_mesh_arrays(arr, GT)
    assert verts.shape == (5, 3)
    assert faces.shape == (2, 3)          # один валидный квадрат
    assert faces.min() >= 0 and faces.max() < 5
    # первая вершина: центр ячейки (0,0)
    assert np.allclose(verts[0], [5.0, 95.0, 1.0])


def test_mesh_arrays_transform():
    from grid_isolines.mesh3d import grid_to_mesh_arrays
    arr = np.array([[1.0, 2.0], [3.0, 4.0]])
    verts, _ = grid_to_mesh_arrays(arr, GT, zscale=2.0, zoffset=-10.0)
    assert np.allclose(sorted(verts[:, 2]), [-8.0, -6.0, -4.0, -2.0])


def test_auto_step():
    import grid_isolines.viewer3d as v3
    assert v3._auto_step(np.zeros((100, 100))) == 1
    big = np.zeros((1000, 1000))
    s = v3._auto_step(big)
    assert (1000 // s) * (1000 // s) <= v3.MAX_VERTS * 1.1
    assert s > 1


def test_palette_cycles():
    import grid_isolines.viewer3d as v3
    assert len(v3.PALETTE) >= 6
    for c in v3.PALETTE:
        assert len(c) == 4 and all(0 <= x <= 1 for x in c)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("OK", name)
    print("all viewer3d tests passed")
