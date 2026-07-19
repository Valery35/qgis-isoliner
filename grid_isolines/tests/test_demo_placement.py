# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Размещение демо-рельефа в пространстве:
#     python grid_isolines/tests/test_demo_placement.py
import ast
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import demo_relief  # noqa: E402

try:
    from osgeo import gdal, osr
    HAVE_GDAL = True
except ImportError:
    HAVE_GDAL = False

ALG = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "algorithms.py")


def _seg():
    with open(ALG, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    cls = [n for n in tree.body if isinstance(n, ast.ClassDef)
           and n.name == "TopoDemoReliefAlgorithm"][0]
    return ast.get_source_segment(src, cls)


def test_tool_has_extent_parameter():
    """Демо должно уметь ложиться туда, где работает человек.

    Зашитое начало координат нормально смотрится в UTM и уезжает далеко в
    местных системах, например рудничных. Человек при этом видит пустую
    карту и думает, что инструмент сломан.
    """
    seg = _seg()
    assert 'EXTENT = "EXTENT"' in seg
    assert "QgsProcessingParameterExtent(" in seg
    assert "parameterAsExtent(" in seg


def test_origin_is_passed_to_writer():
    seg = _seg()
    assert "origin_x=origin_x" in seg and "origin_y=origin_y" in seg


def test_tool_warns_when_extent_is_not_set():
    seg = _seg()
    assert "Охват не задан" in seg


def test_size_is_derived_from_extent():
    seg = _seg()
    assert "ext.width() / cell" in seg and "ext.height() / cell" in seg


def test_writer_accepts_origin():
    import inspect
    sig = inspect.signature(demo_relief.write_geotiff)
    assert "origin_x" in sig.parameters and "origin_y" in sig.parameters


def test_written_raster_lands_at_given_origin():
    if not HAVE_GDAL:
        print("   (gdal нет, проверка записи пропущена)")
        return
    z = demo_relief.generate(40, 30, 25.0, seed=1)
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "demo.tif")
        demo_relief.write_geotiff(z, p, gdal, osr, cell=25.0, epsg=32640,
                                  origin_x=123456.0, origin_y=654321.0)
        ds = gdal.Open(p)
        gt = ds.GetGeoTransform()
        assert abs(gt[0] - 123456.0) < 1e-6, gt
        assert abs(gt[3] - 654321.0) < 1e-6, gt
        assert abs(gt[1] - 25.0) < 1e-9
        ds = None


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
