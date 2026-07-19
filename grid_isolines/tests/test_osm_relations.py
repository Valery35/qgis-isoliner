# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Водоёмы-отношения из OSM:
#     python grid_isolines/tests/test_osm_relations.py
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import osm_overpass as osm  # noqa: E402


def _seg(pts):
    return {"type": "way", "role": "outer",
            "geometry": [{"lon": x, "lat": y} for x, y in pts]}


def test_query_asks_for_relations():
    """Без этого крупные озёра не приходят вовсе.

    Водоём в OSM рисуется одиночным контуром только пока он мал. Озёра,
    водохранилища и широкие реки почти всегда отношения-мультиполигоны.
    """
    q = osm.build_query(56.0, 58.0, 56.5, 58.5, [osm.LAYER_WATERBODIES])
    assert 'relation["natural"="water"]' in q, q
    assert 'way["natural"="water"]' in q, q


def test_relation_split_into_parts_becomes_one_polygon():
    """Внешняя граница обычно нарезана на несколько way."""
    data = {"elements": [{
        "type": "relation", "id": 7,
        "tags": {"natural": "water", "name": "Озеро", "water": "lake"},
        "members": [
            _seg([(0.0, 0.0), (1.0, 0.0)]),
            _seg([(1.0, 0.0), (1.0, 1.0)]),
            _seg([(1.0, 1.0), (0.0, 1.0)]),
            _seg([(0.0, 1.0), (0.0, 0.0)]),
        ],
    }]}
    got = osm.parse_elements(data)[osm.LAYER_WATERBODIES]
    assert len(got) == 1, got
    assert got[0]["geom"] == "polygon"
    assert got[0]["coords"][0] == got[0]["coords"][-1], "кольцо не замкнуто"
    assert got[0]["attrs"]["name"] == "Озеро"
    assert got[0]["attrs"]["osm_id"] == 7


def test_parts_in_any_direction_are_stitched():
    """Куски контура в OSM лежат как попало, направление не гарантировано."""
    data = {"elements": [{
        "type": "relation", "id": 8, "tags": {"natural": "water"},
        "members": [
            _seg([(0.0, 0.0), (1.0, 0.0)]),
            _seg([(1.0, 1.0), (1.0, 0.0)]),      # задом наперёд
            _seg([(0.0, 1.0), (1.0, 1.0)]),
            _seg([(0.0, 0.0), (0.0, 1.0)]),      # задом наперёд
        ],
    }]}
    got = osm.parse_elements(data)[osm.LAYER_WATERBODIES]
    assert len(got) == 1, got
    assert osm._is_closed(got[0]["coords"])


def test_inner_rings_are_ignored():
    """Острова пока не поддерживаем, но и не ломаемся на них."""
    data = {"elements": [{
        "type": "relation", "id": 9, "tags": {"natural": "water"},
        "members": [
            _seg([(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]),
            {"type": "way", "role": "inner",
             "geometry": [{"lon": x, "lat": y} for x, y in
                          [(0.5, 0.5), (1.0, 0.5), (1.0, 1.0), (0.5, 0.5)]]},
        ],
    }]}
    got = osm.parse_elements(data)[osm.LAYER_WATERBODIES]
    assert len(got) == 1
    assert len(got[0]["coords"]) == 5


def test_unclosed_leftovers_are_dropped():
    """Из обрывка полигон не построить, рисовать его хуже, чем не рисовать."""
    data = {"elements": [{
        "type": "relation", "id": 10, "tags": {"natural": "water"},
        "members": [_seg([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)])],
    }]}
    assert osm.parse_elements(data)[osm.LAYER_WATERBODIES] == []


def test_two_separate_rings_give_two_polygons():
    data = {"elements": [{
        "type": "relation", "id": 11, "tags": {"natural": "water"},
        "members": [
            _seg([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 0.0)]),
            _seg([(5.0, 5.0), (6.0, 5.0), (6.0, 6.0), (5.0, 5.0)]),
        ],
    }]}
    got = osm.parse_elements(data)[osm.LAYER_WATERBODIES]
    assert len(got) == 2, got


def test_ways_still_work():
    """Старое поведение не должно пострадать."""
    data = {"elements": [{
        "type": "way", "id": 1, "tags": {"natural": "water"},
        "geometry": [{"lon": x, "lat": y} for x, y in
                     [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]],
    }]}
    got = osm.parse_elements(data)[osm.LAYER_WATERBODIES]
    assert len(got) == 1 and got[0]["geom"] == "polygon"


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
