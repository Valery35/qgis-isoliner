# -*- coding: utf-8 -*-
"""Тесты демо-рельефа. GDAL нужен только тесту записи GeoTIFF."""

import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import demo_relief  # noqa: E402
from grid_isolines.hydro_fill import fill_depressions  # noqa: E402

try:
    from osgeo import gdal, osr
    HAS_GDAL = True
except ImportError:
    HAS_GDAL = False


class TestGenerate(unittest.TestCase):

    def test_shape_and_dtype(self):
        z = demo_relief.generate(nx=120, ny=80, seed=1)
        self.assertEqual(z.shape, (80, 120))
        self.assertEqual(z.dtype, np.float32)
        self.assertTrue(np.all(np.isfinite(z)))

    def test_deterministic(self):
        a = demo_relief.generate(seed=42)
        b = demo_relief.generate(seed=42)
        self.assertTrue(np.array_equal(a, b))

    def test_seed_changes_terrain(self):
        a = demo_relief.generate(seed=1)
        b = demo_relief.generate(seed=2)
        self.assertFalse(np.array_equal(a, b))

    def test_relief_is_lively(self):
        # Не плоскость: разброс высот заметный, долина врезана.
        z = demo_relief.generate(seed=42)
        self.assertGreater(float(z.max() - z.min()), 40.0)

    def test_moderate_pits_by_design(self):
        # Общий уклон держит долину проточной, но между холмами
        # остаются локальные ямы. Это осознанно: инструменту
        # заполнения понижений нужно что показывать на демо.
        z = demo_relief.generate(nx=150, ny=150, seed=42).astype(np.float64)
        _, n_raised, _ = fill_depressions(z, epsilon=0.001)
        self.assertGreater(n_raised, 0)
        self.assertLess(n_raised / z.size, 0.10)

    def test_min_size_guard(self):
        with self.assertRaises(ValueError):
            demo_relief.generate(nx=10, ny=10)


@unittest.skipUnless(HAS_GDAL, "GDAL недоступен")
class TestWriteGeotiff(unittest.TestCase):

    def test_roundtrip_int16(self):
        z = demo_relief.generate(nx=60, ny=50, seed=3)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "demo.tif")
            demo_relief.write_geotiff(z, path, gdal, osr, as_int16=True)
            ds = gdal.Open(path)
            self.assertEqual(ds.RasterXSize, 60)
            self.assertEqual(ds.RasterYSize, 50)
            band = ds.GetRasterBand(1)
            self.assertEqual(gdal.GetDataTypeName(band.DataType), "Int16")
            self.assertEqual(band.GetNoDataValue(), -32768)
            back = band.ReadAsArray().astype(np.float64)
            self.assertTrue(np.all(np.abs(back - z) <= 0.5 + 1e-6))
            srs = osr.SpatialReference(wkt=ds.GetProjection())
            self.assertEqual(srs.GetAuthorityCode(None), "32640")
            gt = ds.GetGeoTransform()
            self.assertEqual(gt[1], 30.0)
            self.assertEqual(gt[5], -30.0)
            ds = None

    def test_compact_enough_for_delivery(self):
        # Демо в поставке должно оставаться маленьким.
        z = demo_relief.generate(nx=300, ny=300, seed=42)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "demo.tif")
            demo_relief.write_geotiff(z, path, gdal, osr, as_int16=True)
            self.assertLess(os.path.getsize(path), 512 * 1024)




class TestWriteGeotiffUserCrs(unittest.TestCase):
    """Регресс 3.0.1: пользовательская СК без кода EPSG уходит в WKT."""

    LOCAL_WKT = (
        'PROJCS["Local mine grid",GEOGCS["WGS 84",DATUM["WGS_1984",'
        'SPHEROID["WGS 84",6378137,298.257223563]],'
        'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],'
        'PROJECTION["Transverse_Mercator"],'
        'PARAMETER["latitude_of_origin",0],'
        'PARAMETER["central_meridian",56.5],'
        'PARAMETER["scale_factor",1],'
        'PARAMETER["false_easting",250000],'
        'PARAMETER["false_northing",-6300000],'
        'UNIT["metre",1]]')

    def test_wkt_roundtrip(self):
        try:
            from osgeo import gdal, osr
        except ImportError:
            self.skipTest("GDAL недоступен")
        import tempfile, os as _os
        z = demo_relief.generate(nx=30, ny=25, seed=3)
        fd, path = tempfile.mkstemp(suffix=".tif")
        _os.close(fd)
        try:
            demo_relief.write_geotiff(z, path, gdal, osr,
                                      wkt=self.LOCAL_WKT)
            ds = gdal.Open(path)
            proj = ds.GetProjection()
            ds = None
            self.assertIn("Local mine grid", proj)
        finally:
            if _os.path.exists(path):
                _os.remove(path)


class RavineMode(unittest.TestCase):
    """Овражно-балочный режим демо-генератора."""

    def test_ravine_mode_cuts_relief(self):
        """Овраги режут рельеф вниз, а не поднимают его."""
        a = demo_relief.generate(120, 120, 30.0, seed=11)
        b = demo_relief.generate(120, 120, 30.0, seed=11, ravine=True)
        d = a - b
        self.assertTrue(np.all(d >= -1e-6),
                        "овраги не должны поднимать поверхность")
        self.assertGreater(d.max(), 10.0)

    def test_ravine_mode_is_deterministic(self):
        a = demo_relief.generate(100, 100, 30.0, seed=3, ravine=True)
        b = demo_relief.generate(100, 100, 30.0, seed=3, ravine=True)
        self.assertTrue(np.array_equal(a, b))

    def test_ravine_is_narrow_not_a_basin(self):
        """Врез должен быть узким: доля глубоко срезанных ячеек невелика."""
        a = demo_relief.generate(150, 150, 30.0, seed=4)
        b = demo_relief.generate(150, 150, 30.0, seed=4, ravine=True)
        deep = float(((a - b) > 5.0).mean())
        self.assertTrue(0.005 < deep < 0.25, deep)

    def test_ravine_off_by_default(self):
        a = demo_relief.generate(80, 80, 30.0, seed=9)
        b = demo_relief.generate(80, 80, 30.0, seed=9, ravine=False)
        self.assertTrue(np.array_equal(a, b))


if __name__ == "__main__":
    unittest.main()
