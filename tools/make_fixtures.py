#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Проверочные данные для расширения Isoliner на Rust.

Пишет в папку два файла того же вида, что у Isoliner3D:

- tools.json - описания инструментов-объектов (tools/export_tools.py);
- tool_runs.json - прогоны: значения полей, вход, выходы, строки отчёта,
  отказ шаблоном с аргументами и допуски.

Значения выходов записаны так, как их пишет модуль: после округления
по подписи поля (grid_isolines/field_format.py, как _RoundingSink).
Неокруглённые числа лежат рядом в "raw". Пустое число и NaN - null.
Точки без Z несут в xyz третьим числом null.

Входы не случайные: выход демо 1.11 взят с заданным зерном и записан
как вход, поэтому Rust не обязан повторять генератор NumPy.

Запуск: python tools/make_fixtures.py <папка>
"""

import json
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "qgis_check"))

import classparams  # noqa: E402
import datasets  # noqa: E402
import export_tools  # noqa: E402
from grid_isolines import field_format, toolspec  # noqa: E402


def _plain(x):
    if isinstance(x, np.ndarray):
        return [_plain(v) for v in x.tolist()]
    if isinstance(x, (np.floating, np.integer, np.bool_)):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    return x


def points_items(rows, with_z=False):
    """Строки datasets в поле points: JSON и то же для расчёта (NumPy)."""
    js, feed = [], []
    for i, r in enumerate(rows):
        if r["x"] is None:
            xyz = None
        else:
            z = r.get("z") if with_z else None
            xyz = [r["x"], r["y"], z]
        js.append({"id": i + 1, "xyz": xyz, "attrs": dict(r["attrs"])})
        feed.append({"id": i + 1, "attrs": dict(r["attrs"]),
                     "xyz": None if xyz is None else np.array(
                         [np.nan if c is None else c for c in xyz],
                         dtype=float)})
    return js, feed


def layer_info(name, rows, geometry, hasz, numeric):
    fields = list(rows[0]["attrs"].keys()) if rows else []
    return {"name": name, "geometry": geometry, "hasz": hasz,
            "fields": fields, "numeric": [f for f in fields if f in numeric]}


def _rows(res, cls_name, skip):
    """Выход Result в JSON: поля, геометрия, значения после округления."""
    names = [f[0] for f in res.fields]
    plan = field_format.plan_for(
        names, classparams.class_dict(cls_name, "FIELD_LABELS"),
        classparams.class_dict(cls_name, "FIELD_DECIMALS"), skip=skip)
    feats = []
    for g, row in res.features:
        raw = [_plain(x) for x in row]
        vals = [_plain(field_format.round_value(x, plan.get(n)))
                if isinstance(x, float) else _plain(x)
                for n, x in zip(names, row)]
        item = {"values": vals, "raw": raw}
        if res.geometry == "point":
            item["xyz"] = _plain(np.asarray(g["xyz"], dtype=float))
        elif res.geometry == "line":
            item["points"] = _plain(np.asarray(g["points"], dtype=float))
        elif res.geometry in ("source",):
            item["source"] = g.get("source")
        feats.append(item)
    out = {"fields": res.fields, "geometry": res.geometry, "features": feats}
    if res.keep_source:
        out["keep_source"] = res.keep_source
    if res.name:
        out["name"] = res.name
    return out


# Числа из result.stats, которые пишутся в прогон полем "stats":
# naive - среднее без весов, decl - декластеризованное среднее,
# best_cell - размер ячейки, sizes и means - перебор (null в ручном режиме)
STATS_KEYS = {"declustering": ("naive", "decl", "best_cell", "sizes",
                               "means"),
              # 7.04: размерность всего слоя, её R² и ряд размер - счёт
              "minkowski_dimension": ("D_layer", "R2_layer", "sizes",
                                      "counts")}


def spec_run(tool, cls_name, vals, inputs, feed, meta, tol=None):
    """Прогон: входы, выходы или отказ, отчёт."""
    rep = toolspec.Report()
    run = {"tool": tool.name, "values": dict(vals, **meta), "input": inputs}
    skip = set()
    for m in meta.values():
        skip |= set(m.get("fields", []))
    try:
        res = tool.run(dict(vals, **dict(feed, **meta)), rep)
        run["output"] = dict((k, _rows(r, cls_name, skip))
                             for k, r in res.items())
        # итоговые числа расчёта, которые иначе видны только строкой
        # отчёта: для 1.05 средние и размер ячейки, без округления
        keep = STATS_KEYS.get(tool.name)
        if keep:
            st = getattr(res.get("OUTPUT"), "stats", {}) or {}
            run["stats"] = dict((k, _plain(st[k])) for k in keep if k in st)
    except toolspec.ToolError as exc:
        run["error"] = {"template": exc.template,
                        "args": [_plain(a) for a in exc.args_]}
    run["report"] = rep.lines
    if tol:
        run["tolerance"] = tol
    return run


def _defaults(tool):
    vals = tool.defaults()
    for p in tool.params:
        if p.kind in toolspec.SOURCE_KINDS or p.kind == "sink":
            vals.pop(p.key, None)
    return vals


# --- 1.05 Декластеризация --------------------------------------------------

def declustering_runs():
    tool = export_tools.spec("declustering")
    cls = "DeclusteringAlgorithm"
    demo = datasets.demo_profiles()
    sample = datasets.clustered()
    zsample = datasets.clustered(with_z=True)
    single = [{"x": 0.0, "y": 0.0, "attrs": {"v": 1.0}},
              {"x": 5.0, "y": 5.0, "attrs": {"v": None}}]
    cases = [
        (demo, False, "demo_1_11", dict(ZFIELD="rho_k")),
        (demo, False, "demo_1_11", dict(ZFIELD="rho_k", OBJECTIVE=1)),
        (demo, False, "demo_1_11", dict(ZFIELD="sp", NCELL=10, NOFF=1)),
        (demo, False, "demo_1_11", dict(ZFIELD="rho_k", MODE=1)),
        (demo, False, "demo_1_11", dict(ZFIELD="vp", MODE=1, CELL_SIZE=60.0,
                                        ASPECT=2.0, NOFF=8)),
        (sample, False, "clustered", dict(ZFIELD="au")),
        (sample, False, "clustered", dict(ZFIELD="au", OBJECTIVE=1,
                                          ASPECT=0.5)),
        (sample, False, "clustered", dict(ZFIELD="au", MODE=1,
                                          CELL_SIZE=120.0)),
        (zsample, True, "clustered_z", dict(ZFIELD="")),
        (sample, False, "clustered", dict(ZFIELD="")),
        (single, False, "single", dict(ZFIELD="v")),
        (sample, False, "clustered", dict(ZFIELD="missing")),
    ]
    runs = []
    for rows, hasz, name, kw in cases:
        vals = dict(_defaults(tool), **kw)
        js, feed = points_items(rows, hasz)
        numeric = {"au", "v", "rho_k", "sp", "vp", "z", "profile"}
        meta = {"@INPUT": layer_info(name, rows, "point", hasz, numeric)}
        runs.append(spec_run(tool, cls, vals, {"INPUT": js},
                             {"INPUT": feed}, meta,
                             {"values_rel": 1e-9, "rounded": "g4 for wt"}))
    return runs


# --- 1.11 Пример геофизических профилей ------------------------------------

def geophys_runs():
    """Охват - слой: прямоугольник участка полигоном и две точки-угла.

    Числа идут от генератора NumPy, поэтому допуск задаёт сверку по
    количеству точек, полям и пикетажу, а не по значениям."""
    tool = export_tools.spec("geophysprofiles")
    cls = "GeophysProfilesDemoAlgorithm"
    x0, y0 = datasets.X0, datasets.Y0
    poly = [{"id": 1, "attrs": {"name": "участок"},
             "parts": [[[x0, y0, None], [x0 + 1000.0, y0, None],
                        [x0 + 1000.0, y0 + 800.0, None],
                        [x0, y0 + 800.0, None], [x0, y0, None]]]}]
    corners = [{"id": 1, "attrs": {}, "parts": [[[x0, y0, None]]]},
               {"id": 2, "attrs": {}, "parts": [[[x0 + 600.0, y0 + 400.0,
                                                  None]]]}]
    flat = [{"id": 1, "attrs": {}, "parts": [[[x0, y0, None]]]}]
    cases = [
        (poly, "polygon", dict(SEED=7)),
        (poly, "polygon", dict(SEED=3, N_PROFILES=6, PICKET_STEP=10.0)),
        (poly, "polygon", dict(SEED=7, RHO_BG=120.0, RHO_MIN=5.0,
                               SP_AMP=-50.0, VP_AMP=30.0, NOISE=0.0,
                               Z_BASE=50.0, Z_AMP=0.0)),
        (corners, "point", dict(SEED=7, N_PROFILES=1)),
        (poly, "polygon", dict(SEED=7, MODE=1)),
        (poly, "polygon", dict(SEED=7, MODE=1, N_TOURS=5, SUBS_SIGN=1,
                               SUBS_MAX=800.0)),
        (flat, "point", dict(SEED=7)),
    ]
    runs = []
    for feats, geom, kw in cases:
        vals = dict(_defaults(tool), **kw)
        feed = [dict(f, parts=[np.array([[np.nan if c is None else c
                                          for c in p] for p in part],
                                        dtype=float)
                               for part in f["parts"]]) for f in feats]
        meta = {"@EXTENT": {"name": "area", "geometry": geom,
                            "hasz": False,
                            "fields": list(feats[0]["attrs"].keys()),
                            "numeric": []}}
        runs.append(spec_run(tool, cls, vals, {"EXTENT": feats},
                             {"EXTENT": feed}, meta,
                             {"random": "numpy default_rng: compare the "
                              "number of points, fields, profile, picket_m, "
                              "pk and xyz exactly, other values by range"}))
    return runs


# --- 4.13 Содержания по пласту в устьях ------------------------------------

def table_items(rows):
    """Строки datasets в поле table: таблица без геометрии."""
    return [{"id": i + 1, "xyz": None, "attrs": dict(r["attrs"])}
            for i, r in enumerate(rows)]


def bed_grades_runs():
    tool = export_tools.spec("bed_grades_at_collars")
    cls = "BedGradesAtCollarsAlgorithm"
    c, iv, ch = datasets.drill_model()
    cjs, cfeed = points_items(c)
    ijs, hjs = table_items(iv), table_items(ch)
    meta = {"@COLLAR": layer_info("collar", c, "point", False, {"z"}),
            "@INTERVAL": layer_info("interval", iv, "none", False,
                                    {"from", "to"}),
            "@CHEM": layer_info("chem", ch, "none", False,
                                {"from", "to", "kcl", "nacl"})}
    base = {"COMPONENTS": ["kcl", "nacl"], "BED": "КрII", "MIN_COVER": 0.5}
    cases = [dict(base), dict(base, MIN_COVER=0.9),
             dict(base, COMPONENTS=["nacl"]), dict(base, BED="Сл"),
             dict(base, BED="  "), dict(base, BED="XX"),
             dict(base, BED="Карн"), dict(base, COMPONENTS=[])]
    runs = []
    for kw in cases:
        vals = dict(_defaults(tool), **kw)
        runs.append(spec_run(tool, cls, vals,
                             {"COLLAR": cjs, "INTERVAL": ijs, "CHEM": hjs},
                             {"COLLAR": cfeed, "INTERVAL": ijs, "CHEM": hjs},
                             meta, {"values_rel": 1e-9}))
    return runs


# --- 7.03 Размерность линий и границ ---------------------------------------

def feature_items(rows, outer_only=False):
    """[(имя, части)] в поле features: JSON и NumPy. У полигона частью идёт
    только внешнее кольцо, как велит контракт."""
    js, feed = [], []
    for i, (name, parts) in enumerate(rows):
        use = parts[:1] if outer_only else parts
        js.append({"id": i + 1, "attrs": {"name": name},
                   "parts": [[[x, y, None] for x, y in p] for p in use]})
        feed.append({"id": i + 1, "attrs": {"name": name},
                     "parts": [np.array([[x, y, np.nan] for x, y in p],
                                        dtype=float) for p in use]})
    return js, feed


def line_dimension_runs():
    tool = export_tools.spec("line_dimension")
    cls = "LineDimensionAlgorithm"
    runs = []
    for rows, geom, outer in ((datasets.dimension_lines(), "line", False),
                              (datasets.dimension_polygons(), "polygon",
                               True)):
        js, feed = feature_items(rows, outer)
        meta = {"@LINES": {"name": geom + "s", "geometry": geom,
                           "hasz": False, "fields": ["name"], "numeric": []}}
        runs.append(spec_run(tool, cls, _defaults(tool), {"LINES": js},
                             {"LINES": feed}, meta, {"values_rel": 1e-9}))
    return runs


# --- 1.06 Вариограмма -------------------------------------------------------

def variogram_runs():
    """Пары считаются целиком (точек меньше порога прореживания), поэтому
    генератор случайных чисел в расчёт не входит. Поле fitted таблицы
    модели - дата прогона, его не сверяют."""
    tool = export_tools.spec("experimental_variogram")
    cls = "ExperimentalVariogramAlgorithm"
    demo = datasets.demo_profiles()
    grp = datasets.clustered()
    for i, r in enumerate(grp):
        hole = r["attrs"]["hole"]
        r["attrs"]["kind"] = "сеть" if hole.startswith("G") else (
            "куст" if hole.startswith("C") else None)
        r["attrs"]["wt"] = 0.5 + (i % 3) * 0.25
    numeric = {"rho_k", "sp", "vp", "z", "profile", "au", "wt"}
    cases = [
        (demo, "demo_1_11", dict(ZFIELD="rho_k")),
        (demo, "demo_1_11", dict(ZFIELD="sp", GROUP_FIELD="profile")),
        (demo, "demo_1_11", dict(ZFIELD="vp", ROBUST=True, N_LAGS=8)),
        (demo, "demo_1_11", dict(ZFIELD="rho_k", FIT_MODEL=1)),
        (demo, "demo_1_11", dict(ZFIELD="rho_k", FIT_MODEL=3,
                                 SAVE_PROFILE="КрII кровля")),
        (demo, "demo_1_11", dict(ZFIELD="z", MAXLAG=5.0)),
        (demo, "demo_1_11", dict(ZFIELD="rho_k", VAL_PCT=5.0)),
        (demo, "demo_1_11", dict(ZFIELD="rho_k", VAL_PCT=5.0, VAL_CAP=True)),
        (demo, "demo_1_11", dict(ZFIELD="rho_k", FIT=False)),
        (grp, "grouped", dict(ZFIELD="au", GROUP_FIELD="kind")),
        (grp, "grouped", dict(ZFIELD="au", WEIGHT_FIELD="wt")),
        (grp, "grouped", dict(ZFIELD="")),
    ]
    runs = []
    for rows, name, kw in cases:
        vals = dict(_defaults(tool), OUT_MODEL=True, **kw)
        js, feed = points_items(rows)
        meta = {"@INPUT": layer_info(name, rows, "point", False, numeric)}
        runs.append(spec_run(tool, cls, vals, {"INPUT": js},
                             {"INPUT": feed}, meta,
                             {"values_rel": 1e-6,
                              "skip_fields": ["fitted"]}))
    return runs


# --- 1.08 Кросс-валидация вариограммы -------------------------------------

MODEL_ROWS = [
    {"profile": "rho", "field": "rho_k", "struct": 1, "model": 1,
     "sill": 300.0, "range": 250.0, "azimuth": 0.0, "anis": 1.0,
     "nugget": 20.0, "val_pct": 0.0, "val_cap": False,
     "fitted": "2026-11-13", "author": "", "note": ""},
    {"profile": "rho_aniso", "field": "rho_k", "struct": 1, "model": 2,
     "sill": 280.0, "range": 300.0, "azimuth": 45.0, "anis": 0.6,
     "nugget": 30.0, "val_pct": 0.0, "val_cap": False,
     "fitted": "2026-11-13", "author": "", "note": ""}]


def crossval_runs():
    tool = export_tools.spec("crossvalidation")
    cls = "CrossValidationAlgorithm"
    demo = datasets.demo_profiles()
    numeric = {"rho_k", "sp", "vp", "z", "profile", "au", "wt"}
    table = [{"id": i + 1, "xyz": None, "attrs": dict(r)}
             for i, r in enumerate(MODEL_ROWS)]
    vg_meta = {"name": "vg_models", "geometry": "none", "hasz": False,
               "fields": list(MODEL_ROWS[0].keys()),
               "numeric": ["struct", "model", "sill", "range", "azimuth",
                           "anis", "nugget", "val_pct"]}
    cases = [
        dict(ZFIELD="rho_k"),
        dict(ZFIELD="rho_k", NUGGET=20.0, S1_SILL=300.0, S1_RANGE=250.0),
        dict(ZFIELD="rho_k", KTYPE=1, SKMEAN=40.0, S1_SILL=300.0,
             S1_RANGE=250.0),
        dict(ZFIELD="sp", RADIUS=150.0, MIN_POINTS=3, MAX_POINTS=8),
        dict(ZFIELD="z", S1_MODEL=2, S1_SILL=50.0, S1_RANGE=400.0),
        dict(ZFIELD="z", S1_MODEL=3, S1_RANGE=5.0),
        dict(ZFIELD="rho_k", S1_SILL=300.0, S1_RANGE=300.0, S1_AZIMUTH=30.0,
             S1_ANIS=0.5),
        dict(ZFIELD="z", DETREND=True, DETREND_DEG=1),
        dict(ZFIELD="rho_k", IDFIELD="profile"),
        dict(ZFIELD="rho_k", VG_NAME="rho_aniso", _table=True),
        dict(ZFIELD="rho_k", _table=True),
    ]
    runs = []
    for kw in cases:
        with_table = kw.pop("_table", False)
        vals = dict(_defaults(tool), **kw)
        js, feed = points_items(demo)
        meta = {"@INPUT": layer_info("demo_1_11", demo, "point", False,
                                     numeric)}
        inputs, feeds = {"INPUT": js}, {"INPUT": feed}
        if with_table:
            inputs["VG_TABLE"] = feeds["VG_TABLE"] = table
            meta["@VG_TABLE"] = vg_meta
        runs.append(spec_run(tool, cls, vals, inputs, feeds, meta,
                             {"values_rel": 1e-6}))
    return runs


# --- 1.09 Кросс-валидация метода ------------------------------------------

def method_cv_runs():
    """N не меньше числа точек: проверяются все, генератор случайных
    чисел в выбор не входит."""
    tool = export_tools.spec("method_crossvalidation")
    cls = "MethodCrossValidationAlgorithm"
    demo = datasets.demo_profiles()
    numeric = {"rho_k", "sp", "vp", "z", "profile"}
    x0, y0 = datasets.X0, datasets.Y0
    rect = [{"id": 1, "attrs": {}, "parts": [[[x0 + 100.0, y0 + 100.0, None],
                                             [x0 + 800.0, y0 + 700.0, None]]]}]
    rect_feed = [{"id": 1, "attrs": {}, "parts": [np.array(
        [[x0 + 100.0, y0 + 100.0, np.nan], [x0 + 800.0, y0 + 700.0, np.nan]])]}]
    table = [{"id": i + 1, "xyz": None, "attrs": dict(r)}
             for i, r in enumerate(MODEL_ROWS)]
    base = dict(N_VALIDATE=200, S1_SILL=300.0, S1_RANGE=250.0)
    cases = [
        (dict(base, ZFIELD="rho_k"), {}),
        (dict(base, ZFIELD="rho_k", EXCL_X=30.0, EXCL_Y=30.0), {}),
        (dict(base, ZFIELD="rho_k", ZMIN=8.0, ZMAX=60.0),
         {"FILTER_EXTENT": True}),
        (dict(base, ZFIELD="rho_k", VG_NAME="rho_aniso"),
         {"VG_TABLE": True}),
        (dict(base, ZFIELD="rho_k", METHOD=1), {}),
        (dict(base, ZFIELD="z", METHOD=1, CELL_SIZE=20.0, TENSION=0.3),
         {"EXTENT": True}),
        (dict(base, ZFIELD="rho_k", ZMIN=1e9), {}),
    ]
    runs = []
    for kw, extra in cases:
        vals = dict(_defaults(tool), **kw)
        js, feed = points_items(demo)
        meta = {"@INPUT": layer_info("demo_1_11", demo, "point", False,
                                     numeric)}
        inputs, feeds = {"INPUT": js}, {"INPUT": feed}
        for key in ("FILTER_EXTENT", "EXTENT"):
            if extra.get(key):
                inputs[key], feeds[key] = rect, rect_feed
                meta["@" + key] = {"name": "area", "geometry": "line",
                                   "hasz": False, "fields": [],
                                   "numeric": []}
        if extra.get("VG_TABLE"):
            inputs["VG_TABLE"] = feeds["VG_TABLE"] = table
        runs.append(spec_run(tool, cls, vals, inputs, feeds, meta,
                             {"values_rel": 1e-6}))
    return runs


# --- 7.04 Размерность Минковского ------------------------------------------

def minkowski_runs():
    """У полигона частями идут все кольца, и внутренние тоже."""
    tool = export_tools.spec("minkowski_dimension")
    cls = "MinkowskiDimensionAlgorithm"
    runs = []
    cases = [(datasets.dimension_lines(), "line", {}),
             (datasets.dimension_polygons(), "polygon", {}),
             (datasets.dimension_lines(), "line",
              dict(N_SIZES=4, OFFSETS=1, DENSIFY=0.0)),
             (datasets.dimension_lines(), "line",
              dict(N_SIZES=12, OFFSETS=5, DENSIFY=2.0))]
    for rows, geom, kw in cases:
        js, feed = feature_items(rows)
        meta = {"@FEATURES": {"name": geom + "s", "geometry": geom,
                              "hasz": False, "fields": ["name"],
                              "numeric": []}}
        runs.append(spec_run(tool, cls, dict(_defaults(tool), **kw),
                             {"FEATURES": js}, {"FEATURES": feed}, meta,
                             {"values_rel": 1e-9,
                              "polygon_parts": "all rings, holes too"}))
    return runs


# --- 2.08 Разделить горизонтали --------------------------------------------

def contour_split_runs():
    tool = export_tools.spec("contoursplit")
    cls = "ContourSplitAlgorithm"
    rows = datasets.contours()
    js, feed = [], []
    for i, r in enumerate(rows):
        js.append({"id": i + 1, "attrs": dict(r["attrs"]),
                   "parts": [[[x, y, None] for x, y in p] for p in r["parts"]]})
        feed.append({"id": i + 1, "attrs": dict(r["attrs"]),
                     "parts": [np.array([[x, y, np.nan] for x, y in p])
                               for p in r["parts"]]})
    meta = {"@INPUT": {"name": "contours", "geometry": "line", "hasz": False,
                       "fields": ["elev", "src"], "numeric": ["elev"]}}
    runs = []
    for kw in (dict(FIELD="elev", EVERY=4), dict(FIELD="elev", EVERY=2,
                                                 OFFSET=1),
               dict(FIELD="elev", EVERY=5, OFFSET=2),
               dict(FIELD="elev", EVERY=50), dict(FIELD="src", EVERY=4)):
        runs.append(spec_run(tool, cls, dict(_defaults(tool), **kw),
                             {"INPUT": js}, {"INPUT": feed}, meta))
    return runs


# --- 1.10 Пример скважин ----------------------------------------------------

def tool_runs(out):
    runs = []
    runs += declustering_runs()
    runs += geophys_runs()
    runs += bed_grades_runs()
    runs += line_dimension_runs()
    runs += variogram_runs()
    runs += crossval_runs()
    runs += method_cv_runs()
    runs += minkowski_runs()
    runs += contour_split_runs()
    _dump(out, "tool_runs.json", {"runs": runs})
    return runs


def _dump(out, name, data):
    with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")


def main(argv):
    # образцы русские: без QGIS i18n выбрал бы английский, и подписи,
    # которые ядро переводит само (пикет «ПК5+20» в 1.11), ушли бы по-английски
    from grid_isolines import i18n
    i18n.set_language("ru", "ru")
    out = argv[1] if len(argv) > 1 else os.path.join(ROOT, "fixtures")
    os.makedirs(out, exist_ok=True)
    _dump(out, "tools.json", export_tools.build())
    runs = tool_runs(out)
    by_tool = {}
    for r in runs:
        by_tool[r["tool"]] = by_tool.get(r["tool"], 0) + 1
    for name in sorted(os.listdir(out)):
        sys.stderr.write("%-20s %8d\n" % (name, os.path.getsize(
            os.path.join(out, name))))
    sys.stderr.write("runs: %s\n" % by_tool)


if __name__ == "__main__":
    main(sys.argv)
