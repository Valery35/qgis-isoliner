# -*- coding: utf-8 -*-
"""Тесты ядра загрузчика GLO-30. Без сети и без QGIS."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import dem_glo30 as dem  # noqa: E402


class TestTileName(unittest.TestCase):

    def test_northeast(self):
        self.assertEqual(dem.tile_name(58.3, 56.7),
                         "Copernicus_DSM_COG_10_N58_00_E056_00_DEM")

    def test_zero_padding(self):
        self.assertEqual(dem.tile_name(5.5, 7.5),
                         "Copernicus_DSM_COG_10_N05_00_E007_00_DEM")

    def test_southwest(self):
        self.assertEqual(dem.tile_name(-33.9, -70.7),
                         "Copernicus_DSM_COG_10_S34_00_W071_00_DEM")

    def test_negative_fraction_floors_down(self):
        # lat=-0.5 лежит в плитке S01, lon=-0.5 в W001.
        self.assertEqual(dem.tile_name(-0.5, -0.5),
                         "Copernicus_DSM_COG_10_S01_00_W001_00_DEM")

    def test_integer_boundary_belongs_to_own_tile(self):
        self.assertEqual(dem.tile_name(58.0, 56.0),
                         "Copernicus_DSM_COG_10_N58_00_E056_00_DEM")

    def test_url(self):
        name = dem.tile_name(58.0, 56.0)
        url = dem.tile_url(name)
        self.assertTrue(url.startswith("https://copernicus-dem-30m"))
        self.assertTrue(url.endswith(name + ".tif"))
        self.assertIn("/" + name + "/", url)

    def test_vsicurl(self):
        self.assertTrue(dem.vsicurl_path("https://x/y.tif")
                        .startswith("/vsicurl/https://"))


class TestTilesForBbox(unittest.TestCase):

    def test_single_tile(self):
        names = dem.tiles_for_bbox(56.1, 58.1, 56.9, 58.9)
        self.assertEqual(names,
                         ["Copernicus_DSM_COG_10_N58_00_E056_00_DEM"])

    def test_crossing_degree_lines(self):
        names = dem.tiles_for_bbox(56.9, 58.9, 57.1, 59.1)
        self.assertEqual(len(names), 4)
        self.assertIn("Copernicus_DSM_COG_10_N58_00_E056_00_DEM", names)
        self.assertIn("Copernicus_DSM_COG_10_N59_00_E057_00_DEM", names)

    def test_exact_integer_max_not_extra_tile(self):
        # Рамка до 57.0 ровно не должна тянуть плитку E057.
        names = dem.tiles_for_bbox(56.1, 58.1, 57.0, 59.0)
        self.assertEqual(names,
                         ["Copernicus_DSM_COG_10_N58_00_E056_00_DEM"])

    def test_max_tiles_guard(self):
        with self.assertRaises(dem.DemSourceError):
            dem.tiles_for_bbox(50.0, 50.0, 60.0, 60.0, max_tiles=25)

    def test_empty_bbox(self):
        with self.assertRaises(dem.DemSourceError):
            dem.tiles_for_bbox(56.0, 58.0, 56.0, 59.0)

    def test_out_of_range(self):
        with self.assertRaises(dem.DemSourceError):
            dem.tiles_for_bbox(179.5, 58.0, 180.5, 59.0)


class TestUtm(unittest.TestCase):

    def test_perm(self):
        # Пермь, 56.2 в.д., зона 40 северная.
        self.assertEqual(dem.utm_epsg_for(56.2, 58.0), 32640)

    def test_southern_hemisphere(self):
        self.assertEqual(dem.utm_epsg_for(-70.7, -33.9), 32719)

    def test_zone_boundary(self):
        self.assertEqual(dem.utm_epsg_for(0.0, 10.0), 32631)
        self.assertEqual(dem.utm_epsg_for(-0.001, 10.0), 32630)




class TestSources(unittest.TestCase):
    """3.1.0: выбор источника GLO-30 или GEDTM30."""

    def test_source_constants(self):
        self.assertEqual(dem.SOURCE_GLO30, "glo30")
        self.assertEqual(dem.SOURCE_GEDTM30, "gedtm30")

    def test_gedtm30_cog_url(self):
        self.assertTrue(dem.GEDTM30_COG.startswith(
            "https://s3.opengeohub.org/global/edtm/"))
        self.assertTrue(dem.GEDTM30_COG.endswith("v20250611.tif"))
        self.assertIn("gedtm_rf_m_30m", dem.GEDTM30_COG)

    def test_gedtm30_nodata(self):
        # сырой Int32 no-data COG (GDAL применит scale/unscale сам)
        self.assertEqual(dem.GEDTM30_NODATA, -2147483647)
        self.assertFalse(hasattr(dem, "GEDTM30_SCALE"),
                         "деление на scale вручную убрано, unscale делает GDAL")

    def test_fetch_dem_has_source(self):
        import inspect
        params = inspect.signature(dem.fetch_dem).parameters
        self.assertIn("source", params)
        self.assertEqual(params["source"].default, dem.SOURCE_GLO30)

    def test_vsicurl_wrap(self):
        p = dem.vsicurl_path(dem.GEDTM30_COG)
        self.assertTrue(p.startswith("/vsicurl/https://"))


if __name__ == "__main__":
    unittest.main()
