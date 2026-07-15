# -*- coding: utf-8 -*-
"""Тесты ядра загрузчика топоосновы OSM. Без сети и без QGIS."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import osm_overpass as osm  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures",
                       "overpass_sample.json")


def load_fixture():
    with open(FIXTURE, "r", encoding="utf-8") as f:
        return json.load(f)


class TestBboxGuard(unittest.TestCase):

    def test_ok(self):
        osm.check_bbox(56.0, 58.0, 56.5, 58.5)

    def test_too_big(self):
        with self.assertRaises(osm.OsmSourceError):
            osm.check_bbox(50.0, 50.0, 55.0, 55.0)

    def test_empty(self):
        with self.assertRaises(osm.OsmSourceError):
            osm.check_bbox(56.0, 58.0, 56.0, 58.5)


class TestBuildQuery(unittest.TestCase):

    def test_all_layers(self):
        q = osm.build_query(56.0, 58.0, 56.5, 58.5, set(osm.ALL_LAYERS))
        self.assertIn("[out:json]", q)
        self.assertIn('waterway"~"^(river|stream|canal)$', q)
        self.assertIn('"natural"="water"', q)
        self.assertIn('node["natural"="peak"]', q)
        self.assertIn('"natural"="cliff"', q)
        self.assertIn('"man_made"="embankment"', q)
        self.assertIn('"natural"="coastline"', q)
        self.assertIn("out geom", q)
        # Порядок bbox у Overpass: юг, запад, север, восток.
        self.assertIn("(58.0000000,56.0000000,58.5000000,56.5000000)", q)

    def test_subset(self):
        q = osm.build_query(56.0, 58.0, 56.5, 58.5, {osm.LAYER_PEAKS})
        self.assertIn("peak", q)
        self.assertNotIn("waterway", q)
        self.assertNotIn("cliff", q)

    def test_no_layers(self):
        with self.assertRaises(osm.OsmSourceError):
            osm.build_query(56.0, 58.0, 56.5, 58.5, set())


class TestParse(unittest.TestCase):

    def setUp(self):
        self.parsed = osm.parse_elements(load_fixture())

    def test_watercourses(self):
        wc = self.parsed[osm.LAYER_WATERCOURSES]
        self.assertEqual(len(wc), 2)  # drain отфильтрован
        kinds = {f["attrs"]["waterway"] for f in wc}
        self.assertEqual(kinds, {"river", "stream"})
        kama = [f for f in wc if f["attrs"]["name"] == "Кама"][0]
        self.assertEqual(kama["geom"], "line")
        self.assertEqual(len(kama["coords"]), 3)
        # Координаты в порядке (lon, lat).
        self.assertAlmostEqual(kama["coords"][0][0], 56.20)
        self.assertAlmostEqual(kama["coords"][0][1], 58.01)

    def test_waterbodies_only_closed(self):
        wb = self.parsed[osm.LAYER_WATERBODIES]
        self.assertEqual(len(wb), 1)  # незамкнутый way 2002 отброшен
        self.assertEqual(wb[0]["geom"], "polygon")
        self.assertEqual(wb[0]["attrs"]["water"], "lake")

    def test_peaks_ele_parsing(self):
        peaks = self.parsed[osm.LAYER_PEAKS]
        self.assertEqual(len(peaks), 3)
        by_id = {f["attrs"]["osm_id"]: f for f in peaks}
        self.assertEqual(by_id[3001]["attrs"]["ele"], 381.0)
        self.assertEqual(by_id[3002]["attrs"]["ele"], 409.5)
        self.assertIsNone(by_id[3003]["attrs"]["ele"])

    def test_breaks(self):
        brk = self.parsed[osm.LAYER_BREAKS]
        self.assertEqual(len(brk), 2)
        kinds = {f["attrs"]["kind"] for f in brk}
        self.assertEqual(kinds, {"cliff", "embankment"})

    def test_coastline(self):
        self.assertEqual(len(self.parsed[osm.LAYER_COASTLINE]), 1)

    def test_foreign_tags_ignored(self):
        total = sum(len(v) for v in self.parsed.values())
        self.assertEqual(total, 9)  # highway 6001 никуда не попал


class TestClip(unittest.TestCase):

    def test_line_partly_outside(self):
        coords = [(0.0, 0.0), (1.0, 1.0), (2.0, 2.0),
                  (5.0, 5.0), (6.0, 6.0), (7.0, 7.0)]
        pieces = osm.clip_line_to_bbox(coords, 0.5, 0.5, 2.5, 2.5)
        self.assertEqual(len(pieces), 1)
        # Внутренние вершины плюс по одной соседней снаружи.
        self.assertEqual(pieces[0][0], (0.0, 0.0))
        self.assertEqual(pieces[0][-1], (5.0, 5.0))

    def test_line_fully_inside(self):
        coords = [(1.0, 1.0), (1.5, 1.5)]
        pieces = osm.clip_line_to_bbox(coords, 0.0, 0.0, 2.0, 2.0)
        self.assertEqual(pieces, [coords])

    def test_line_fully_outside(self):
        coords = [(10.0, 10.0), (11.0, 11.0)]
        pieces = osm.clip_line_to_bbox(coords, 0.0, 0.0, 2.0, 2.0)
        self.assertEqual(pieces, [])


if __name__ == "__main__":
    unittest.main()
