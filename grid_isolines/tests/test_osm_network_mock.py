# -*- coding: utf-8 -*-
"""Тесты сетевого слоя Overpass. Сеть подменена моками."""

import json
import os
import sys
import unittest
import unittest.mock as mock
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import osm_overpass as osm  # noqa: E402


def _ok_response(payload):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")
    return R()


class TestRunQuery(unittest.TestCase):

    def test_primary_endpoint_used_first(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(req.full_url)
            return _ok_response({"elements": []})

        with mock.patch("urllib.request.urlopen", fake):
            data = osm.run_query("[out:json];out;")
        self.assertEqual(data, {"elements": []})
        self.assertEqual(len(calls), 1)
        self.assertIn("overpass-api.de", calls[0])

    def test_fallback_to_mirror(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.URLError("основной недоступен")
            return _ok_response({"elements": [{"type": "node"}]})

        with mock.patch("urllib.request.urlopen", fake):
            data = osm.run_query("[out:json];out;")
        self.assertEqual(len(calls), 2)
        self.assertIn("kumi", calls[1])
        self.assertEqual(len(data["elements"]), 1)

    def test_all_endpoints_down(self):
        def fake(req, timeout=None):
            raise urllib.error.URLError("сеть недоступна")

        with mock.patch("urllib.request.urlopen", fake):
            with self.assertRaises(osm.OsmSourceError) as ctx:
                osm.run_query("[out:json];out;")
        self.assertIn("Overpass", str(ctx.exception))

    def test_broken_json_falls_to_mirror(self):
        calls = []

        def fake(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                class Bad:
                    def __enter__(self):
                        return self

                    def __exit__(self, *args):
                        return False

                    def read(self):
                        return b"<html>509 limit</html>"
                return Bad()
            return _ok_response({"elements": []})

        with mock.patch("urllib.request.urlopen", fake):
            data = osm.run_query("[out:json];out;")
        self.assertEqual(len(calls), 2)
        self.assertEqual(data, {"elements": []})

    def test_user_agent_present(self):
        seen = {}

        def fake(req, timeout=None):
            seen["ua"] = req.get_header("User-agent")
            return _ok_response({"elements": []})

        with mock.patch("urllib.request.urlopen", fake):
            osm.run_query("[out:json];out;")
        self.assertIn("Isoliner", seen["ua"])


if __name__ == "__main__":
    unittest.main()
