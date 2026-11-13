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


def tool_runs(out):
    runs = []
    runs += declustering_runs()
    _dump(out, "tool_runs.json", {"runs": runs})
    return runs


def _dump(out, name, data):
    with open(os.path.join(out, name), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, allow_nan=False)
        fh.write("\n")


def main(argv):
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
