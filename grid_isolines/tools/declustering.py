# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""1.05 Декластеризация (веса): расчёт без QGIS.

Поля - ключи параметров класса DeclusteringAlgorithm: INPUT (points),
ZFIELD, MODE (0 авто, 1 ручной), CELL_SIZE, OBJECTIVE (0 минимум
среднего, 1 максимум), NCELL, ASPECT, NOFF. Выход OUTPUT - точки с
полями значения и wt. Цифры для HTML-отчёта класс QGIS берёт из
result.stats, в tools.json и tool_runs.json они не идут.
"""

import re

import numpy as np

from .. import declus as dc
from ..toolspec import N_, Result, ToolError
from .points import read_points

MODES = ("auto", "manual")


def value_field(zfield):
    """Имя поля значения на выходе: как _san в algorithms.py."""
    s = re.sub(r"\W+", "_", (zfield or "").strip(), flags=re.UNICODE)
    return s.strip("_")[:40] or "z"


def run(values, report):
    xs, ys, vs = read_points(values["INPUT"], values.get("ZFIELD") or "",
                             values.get("@INPUT"), report)
    n = len(xs)
    if n < 2:
        raise ToolError(N_("Слишком мало точек."))
    mode = MODES[int(values.get("MODE") or 0)]
    aspect = float(values["ASPECT"])
    noff = int(values["NOFF"])
    sizes = means = None
    if mode == "manual":
        cell = float(values.get("CELL_SIZE") or 0.0)
        if cell <= 0:
            lo, hi = dc.suggest_range(xs, ys)
            cell = float(np.sqrt(lo * hi))
            report.info(N_("Размер ячейки не задан - взят %.4g."), cell)
        weights, decl = dc.cell_declus(xs, ys, vs, cell, cell * aspect, noff)
        best_cell = cell
        naive = float(np.mean(vs))
    else:
        obj = "max" if int(values.get("OBJECTIVE") or 0) == 1 else "min"
        ncell = int(values["NCELL"])
        lo, hi = dc.suggest_range(xs, ys)
        res = dc.declus_sweep(xs, ys, vs, lo, hi, ncell=ncell, noff=noff,
                              aspect=aspect, objective=obj)
        weights = res["weights"]
        decl = res["decl_mean"]
        naive = res["naive_mean"]
        best_cell = res["best_cell"]
        sizes = res["sizes"]
        means = res["means"]

    pct = (100.0 * (decl - naive) / naive) if naive else 0.0
    report.info(N_("== Декластеризация =="))
    report.info(N_("Точек: %d, размер ячейки: %.4g"), n, best_cell)
    report.info(N_("Среднее без весов:          %.4g"), naive)
    report.info(N_("Декластеризованное среднее: %.4g (%+.2f%%)"), decl, pct)
    report.info(N_("Это среднее ставьте в «Среднее» простого кригинга, а "
                   "поле wt - в поле весов SGS."))

    out = Result([(value_field(values.get("ZFIELD")), "double"),
                  ("wt", "double")], geometry="point")
    for i in range(n):
        out.add({"xyz": np.array([xs[i], ys[i], np.nan])},
                [float(vs[i]), float(weights[i])])
    out.stats = {"naive": naive, "decl": decl, "best_cell": best_cell,
                 "sizes": sizes, "means": means, "values": vs,
                 "weights": weights}
    return {"OUTPUT": out}
