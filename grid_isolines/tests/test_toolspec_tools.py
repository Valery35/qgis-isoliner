# -*- coding: utf-8 -*-
"""Инструменты-объекты: описание из класса и расчёт run в согласии.

Описание для tools.json строится разбором класса QGIS
(tools/classparams.py в корне репозитория), расчёт лежит в
grid_isolines/tools. Сторож держит их вместе:

- каждый инструмент RUNS описывается без ошибок, виды полей из
  контракта toolspec, у подписей, пунктов и сообщений есть английский;
- run читает только ключи, которые есть у класса, и сведения @КЛЮЧ
  только у полей-слоёв;
- name() класса совпадает с именем в RUNS.

Исходники выгрузки в плагин не входят, поэтому в установленном модуле
сторож пропускается.
"""

import ast
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)
TOOLS_DIR = os.path.join(ROOT, "tools")

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(TOOLS_DIR, "export_tools.py")),
    reason="исходники выгрузки tools.json не входят в установленный модуль")


def _export():
    for p in (ROOT, TOOLS_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    import export_tools
    return export_tools


def _runs():
    from grid_isolines.tools import RUNS
    return RUNS


def test_every_tool_is_described_from_its_class():
    ex = _export()
    data = ex.build()
    names = [t["name"] for t in data["tools"]]
    assert names == [r[0] for r in _runs()]
    from grid_isolines import toolspec
    for t in data["tools"]:
        assert t["code"] and t["title"]["ru"] and t["help"]["ru"], t["name"]
        assert t["title"]["en"] != t["title"]["ru"], t["name"]
        for p in t["params"]:
            assert p["kind"] in toolspec.KINDS, (t["name"], p["key"])
            assert p["label"]["en"] != p["label"]["ru"], (t["name"], p["key"])
            for o in p.get("options", []):
                assert o["en"] != o["ru"], (t["name"], p["key"], o["ru"])
        for m in t["messages"]:
            assert m["en"] != m["ru"], (t["name"], m["ru"])


def _keys_read(path):
    """Ключи values["..."] и values.get("...") в модуле расчёта."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.value, ast.Name)
                and node.value.id == "values"
                and isinstance(node.slice, ast.Constant)):
            keys.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "values" and node.args
                and isinstance(node.args[0], ast.Constant)):
            keys.add(node.args[0].value)
    return keys


def test_run_reads_only_keys_of_the_class():
    ex = _export()
    from grid_isolines import toolspec
    for name, mod, _cls in _runs():
        tool = ex.spec(name)
        own = set(p.key for p in tool.params)
        layers = set("@" + p.key for p in tool.params
                     if p.kind in toolspec.SOURCE_KINDS)
        path = os.path.join(PKG, "tools", mod + ".py")
        extra = _keys_read(path) - own - layers
        assert not extra, (name, sorted(extra))


def test_declustering_run_matches_the_core():
    """run 1.05 даёт те же веса, что ядро declus на тех же точках."""
    import numpy as np
    from grid_isolines import declus as dc
    from grid_isolines import toolspec
    from grid_isolines.tools import declustering as spec
    pts = [(0, 0, 1.0), (100, 0, 1.2), (0, 100, 1.1), (100, 100, 1.3),
           (10, 10, 5.0), (12, 8, 5.5), (8, 12, 4.5), (200, 200, 2.0)]
    items = [{"id": i, "xyz": np.array([x, y, np.nan]), "attrs": {"au": v}}
             for i, (x, y, v) in enumerate(pts)]
    rep = toolspec.Report()
    vals = {"INPUT": items, "ZFIELD": "au", "MODE": 1, "CELL_SIZE": 50.0,
            "OBJECTIVE": 0, "NCELL": 24, "ASPECT": 1.0, "NOFF": 4}
    out = spec.run(vals, rep)["OUTPUT"]
    xs, ys, vs = (np.array([p[i] for p in pts], float) for i in range(3))
    # совпадающих точек нет, порядок слоя сохраняется
    w, decl = dc.cell_declus(xs, ys, vs, 50.0, 50.0, 4)
    got = np.array([row[1] for _g, row in out.features])
    assert np.allclose(got, w)
    assert out.fields == [("au", "double"), ("wt", "double")]
    assert any("Декластеризованное среднее" in s for _k, s in rep.lines)


def test_geophys_profiles_fill_the_extent_of_a_layer():
    """1.11: охват - общий охват объектов, пустой охват - отказ."""
    import numpy as np
    from grid_isolines import toolspec
    from grid_isolines.tools import geophys_profiles as spec
    nan = np.nan
    area = [{"id": 1, "attrs": {}, "parts": [np.array(
        [[0, 0, nan], [1000, 0, nan], [1000, 800, nan], [0, 0, nan]])]}]
    vals = {"EXTENT": area, "MODE": 0, "N_PROFILES": 4, "PICKET_STEP": 20.0,
            "Z_BASE": 120.0, "Z_AMP": 15.0, "SEED": 7, "RHO_BG": 60.0,
            "RHO_MIN": 10.0, "SP_AMP": -100.0, "VP_AMP": 15.0, "NOISE": 0.06,
            "SUBS_MAX": 400.0, "N_TOURS": 2, "SUBS_SIGN": 0}
    out = spec.run(vals, toolspec.Report())["OUTPUT"]
    xy = np.array([g["xyz"][:2] for g, _r in out.features])
    assert xy[:, 0].min() >= 0 and xy[:, 0].max() <= 1000
    assert xy[:, 1].min() >= 0 and xy[:, 1].max() <= 800
    assert len(set(r[0] for _g, r in out.features)) == 4
    flat = [{"id": 1, "attrs": {}, "parts": [np.array([[5, 5, nan]])]}]
    with pytest.raises(toolspec.ToolError):
        spec.run(dict(vals, EXTENT=flat), toolspec.Report())


def test_bed_grades_weight_by_overlap_and_skip_missing():
    """4.13: проба на границе весит длиной перекрытия, пустое значение
    выпадает вместе с весом, а не идёт нулём."""
    import numpy as np
    from grid_isolines import toolspec
    from grid_isolines.tools import bed_grades as spec
    collars = [{"id": 1, "xyz": np.array([10.0, 20.0, np.nan]),
                "attrs": {"hole_id": "A"}}]
    iv = [{"id": 1, "xyz": None, "attrs": {"hole_id": "A", "from": 10.0,
                                          "to": 14.0, "code": "КрII"}}]
    ch = [{"id": 1, "xyz": None, "attrs": {"hole_id": "A", "from": 8.0,
                                          "to": 12.0, "kcl": 30.0,
                                          "nacl": None}},
          {"id": 2, "xyz": None, "attrs": {"hole_id": "A", "from": 12.0,
                                          "to": 14.0, "kcl": 20.0,
                                          "nacl": 60.0}}]
    vals = {"COLLAR": collars, "INTERVAL": iv, "CHEM": ch,
            "COMPONENTS": ["kcl", "nacl"], "BED": "КрII", "MIN_COVER": 0.5}
    rep = toolspec.Report()
    out = spec.run(vals, rep)["OUTPUT"]
    (geom, row), = out.features
    names = [f[0] for f in out.fields]
    rec = dict(zip(names, row))
    assert rec["hole_id"] == "A" and rec["len_m"] == 4.0
    assert abs(rec["kcl"] - (2 * 30.0 + 2 * 20.0) / 4.0) < 1e-9
    assert rec["nacl"] == 60.0 and rec["cov_nacl"] == 0.5
    assert list(geom["xyz"][:2]) == [10.0, 20.0]
    with pytest.raises(toolspec.ToolError):
        spec.run(dict(vals, BED="XX"), toolspec.Report())


def test_line_dimension_keeps_source_and_measures_longest_part():
    """7.03: прямая даёт D около единицы, короткая линия - пусто, выход
    ссылается на объекты источника."""
    import numpy as np
    from grid_isolines import toolspec
    from grid_isolines.tools import line_dimension as spec
    nan = np.nan
    xs = np.linspace(0, 1000, 200)
    straight = np.column_stack([xs, np.zeros_like(xs), np.full_like(xs, nan)])
    short = np.array([[0, 0, nan], [5, 0, nan]])
    items = [{"id": 11, "attrs": {}, "parts": [short, straight]},
             {"id": 12, "attrs": {}, "parts": [short]}]
    out = spec.run({"LINES": items}, toolspec.Report())["OUTPUT"]
    assert out.keep_source == "LINES" and out.geometry == "source"
    (g1, r1), (g2, r2) = out.features
    assert g1 == {"source": 11} and g2 == {"source": 12}
    assert abs(r1[0] - 1.0) < 0.05 and r2 == [None, 0]


def test_declustering_refuses_without_value():
    import numpy as np
    from grid_isolines import toolspec
    from grid_isolines.tools import declustering as spec
    items = [{"id": 1, "xyz": np.array([0.0, 0.0, np.nan]), "attrs": {}},
             {"id": 2, "xyz": np.array([1.0, 1.0, np.nan]), "attrs": {}}]
    with pytest.raises(toolspec.ToolError) as exc:
        spec.run({"INPUT": items, "ZFIELD": "", "MODE": 0, "ASPECT": 1.0,
                  "NOFF": 4, "NCELL": 24,
                  "@INPUT": {"hasz": False, "fields": []}},
                 toolspec.Report())
    assert "без Z" in exc.value.template
