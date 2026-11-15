# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""3.08 Пример для плотности (демо): расчёт без QGIS.

Поля - ключи параметров класса DensityDemoAlgorithm. Охват EXTENT
приходит объектами (features), как у 1.11. Выходы OUT_POINTS (точки с
mass и prec), OUT_LINES (линии с mass, prec, from_m, to_m) и
OUT_POLYGONS (полигоны с mass и dasy, geometry="polygon"). Данные даёт
density.demo_dataset с зерном SEED.

Вспомогательный растр для дазиметрии (OUT_AUX, его ячейка CELL_AUX) в
описание для RustGIS не входит: растров у расширений нет. Его пишет
класс QGIS сам, по охвату и ячейке, от расчёта он не зависит.
"""

import numpy as np

from .. import density as D
from ..toolspec import N_, Result, ToolError
from .geophys_profiles import bbox

SPEC_OVERRIDES = {
    "EXTENT": {"kind": "features",
               "label": "Слой, по охвату которого строить",
               "hint": "Демо-объекты ложатся в охват объектов этого слоя."},
    "CELL_AUX": {"_drop": True},
    "OUT_AUX": {"_drop": True},
}


def _xyz(pts):
    a = np.asarray(pts, dtype=float).reshape(-1, 2)
    return np.column_stack([a, np.full(len(a), np.nan)])


def run(values, report):
    ext = bbox(values.get("EXTENT"))
    if ext is None or not (ext[2] > ext[0] and ext[3] > ext[1]):
        raise ToolError(N_("Не задан охват."))
    ds = D.demo_dataset(ext[0], ext[1], ext[2], ext[3],
                        seed=int(values.get("SEED") or 0))

    points = Result([("mass", "double"), ("prec", "double")],
                    geometry="point")
    for x, y, m, s in ds["points"]:
        points.add({"xyz": np.array([float(x), float(y), np.nan])},
                   [float(m), float(s)])

    lines = Result([("mass", "double"), ("prec", "double"),
                    ("from_m", "double"), ("to_m", "double")],
                   geometry="line")
    for ln in ds["lines"]:
        lines.add({"points": _xyz(ln["verts"])},
                  [float(ln["mass"]), float(ln["half"]),
                   None if ln["from_m"] is None else float(ln["from_m"]),
                   None if ln["to_m"] is None else float(ln["to_m"])])

    polys = Result([("mass", "double"), ("dasy", "int")], geometry="polygon")
    for pg in ds["polygons"]:
        polys.add({"rings": [_xyz(r) for r in pg["rings"]]},
                  [float(pg["mass"]), 1 if pg["dasy"] else 0])

    report.info(N_("Демо создано. Масса: точки 500, линии 200, полигоны 300, "
                   "итого 1000. Прогоните 3.07 на каждом слое - интеграл "
                   "плотности должен совпасть."))
    return {"OUT_POINTS": points, "OUT_LINES": lines, "OUT_POLYGONS": polys}
