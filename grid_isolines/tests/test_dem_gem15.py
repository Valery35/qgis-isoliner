# -*- coding: utf-8 -*-
"""GEM-15 в 2.01: имена плиток, выбор по рамке из STAC, отказ вне покрытия.

Сеть подменена: каталог STAC отдаёт словари из теста. Живая загрузка
проверяется отдельно, tools/gem15_live.py.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import dem_glo30 as dem  # noqa: E402

STAC = dem.GEM15_STAC


def test_cell_names_follow_the_catalog():
    """Имена сверены с каталогом: w70s40 в p40_p70, wm61sm1 в m10_m70."""
    assert dem.gem15_cell(70.2, 40.7) == ("p40_p70", "w70s40")
    assert dem.gem15_cell(-60.5, -0.5) == ("m10_m70", "wm61sm1")
    assert dem.gem15_cell(-0.5, 42.3) == ("p40_m10", "wm1s42")
    assert dem.gem15_cell(0.0, 0.0) == ("p0_p0", "w0s0")


def _fake_catalog(cells_with_data):
    """Каталог: группы и плитки, у каждой плитки свой bbox и два канала."""
    groups = {}
    for lo, la in cells_with_data:
        g, c = dem.gem15_cell(lo, la)
        groups.setdefault(g, []).append((c, lo, la))
    docs = {STAC + "collection.json": {"links": [
        {"rel": "child", "href": "./%s/catalog.json" % g, "title": g}
        for g in groups]}}
    for g, cells in groups.items():
        docs[STAC + g + "/catalog.json"] = {"links": [
            {"rel": "item", "href": "./%s/%s.json" % (c, c), "title": c}
            for c, _lo, _la in cells]}
        for c, lo, la in cells:
            docs[STAC + g + "/%s/%s.json" % (c, c)] = {
                "bbox": [lo, la, lo + 1, la + 1],
                "properties": {"proj:epsg": 32642},
                "assets": {b: {"href": "https://x/%s/%s.tif" % (c, b),
                               "proj:epsg": 32642}
                           for b in ("dtm", "dsm")}}
    seen = []

    def fetch(url):
        seen.append(url)
        return docs.get(url)
    return fetch, seen


def test_plan_takes_only_tiles_present_in_the_catalog():
    """Ячейка без плитки в каталоге (вода, вне набора) пропускается."""
    fetch, seen = _fake_catalog([(70, 40), (71, 40)])
    tiles, n = dem.gem15_plan((70.5, 40.2, 72.5, 40.8), "dtm", fetch=fetch)
    assert n == 3
    assert [t["cell"] for t in tiles] == ["w70s40", "w71s40"]
    assert tiles[0]["href"].endswith("/w70s40/dtm.tif")
    assert tiles[0]["epsg"] == 32642
    # каталог группы читается один раз на все её плитки
    assert seen.count(STAC + "p40_p70/catalog.json") == 1


def test_plan_band_dsm_and_unknown_band():
    fetch, _ = _fake_catalog([(70, 40)])
    tiles, _n = dem.gem15_plan((70.1, 40.1, 70.2, 40.2), "dsm", fetch=fetch)
    assert tiles[0]["href"].endswith("/dsm.tif")
    with pytest.raises(dem.DemSourceError):
        dem.gem15_plan((70.1, 40.1, 70.2, 40.2), "dsm_std", fetch=fetch)


def test_plan_outside_coverage_is_empty():
    """Группы нет в каталоге (Россия) - плиток нет."""
    fetch, _ = _fake_catalog([(70, 40)])
    tiles, n = dem.gem15_plan((37.5, 55.7, 37.7, 55.8), "dtm", fetch=fetch)
    assert tiles == [] and n == 1


def test_plan_respects_max_tiles():
    fetch, _ = _fake_catalog([(70, 40)])
    with pytest.raises(dem.DemSourceError):
        dem.gem15_plan((60.0, 40.0, 70.0, 50.0), "dtm", max_tiles=25,
                       fetch=fetch)


def test_fetch_dem_refuses_outside_coverage_with_advice():
    """Рамка целиком вне покрытия - отказ с советом взять GLO-30, а не
    пустой растр."""
    osgeo = pytest.importorskip("osgeo")
    from osgeo import gdal, osr
    assert osgeo
    fetch, _ = _fake_catalog([(70, 40)])
    with pytest.raises(dem.DemSourceError) as exc:
        dem.fetch_dem((37.5, 55.7, 37.7, 55.8), "/vsimem/none.tif", gdal,
                      osr, cell=15.0, source=dem.SOURCE_GEM15, fetch=fetch)
    assert "GLO-30" in str(exc.value)


def test_network_errors_become_source_errors(monkeypatch):
    """Обрыв сети - понятная ошибка источника, 404 - пустое место."""
    import urllib.error
    import urllib.request

    def boom(*a, **kw):
        raise urllib.error.URLError("no route")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(dem.DemSourceError):
        dem._fetch_json("https://example.invalid/x.json")

    def nf(*a, **kw):
        raise urllib.error.HTTPError("u", 404, "nf", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", nf)
    assert dem._fetch_json("https://example.invalid/x.json") is None
