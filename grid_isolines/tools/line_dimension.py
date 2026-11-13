# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""7.03 Размерность линий и границ: расчёт без QGIS.

Поле LINES - объекты (features): части линии или внешние кольца
полигона, меряется самая длинная часть по числу вершин. Выход OUTPUT -
те же объекты (geometry="source") с полями D и steps. Размерность
считается методом циркуля (fractal.divider_dimension) в единицах
координат входа, поэтому градусы перед расчётом переводятся в метры.
"""

import numpy as np

from ..fractal import divider_dimension
from ..toolspec import N_, Result


def run(values, report):
    feats = values.get("LINES") or []
    out = Result([("D", "double"), ("steps", "int")], keep_source="LINES",
                 geometry="source")
    total = len(feats) or 1
    dsum, dcnt = 0.0, 0
    for k, it in enumerate(feats):
        if report.canceled():
            break
        report.progress(float(k) / total)
        parts = [np.asarray(p, dtype=float).reshape(-1, 3)
                 for p in (it.get("parts") or [])]
        parts = [p for p in parts if len(p)]
        # самая длинная часть по числу вершин, при равенстве первая
        best = max(parts, key=len) if parts else np.zeros((0, 3))
        pts = np.ascontiguousarray(best[:, :2])
        D, steps = float("nan"), 0
        if len(pts) >= 3:
            D, _r, ss = divider_dimension(pts)
            steps = int(ss[-1]) if len(ss) else 0
        out.add({"source": it.get("id")},
                [None if D != D else round(D, 4), steps])
        if D == D:
            dsum += D
            dcnt += 1
    if dcnt:
        report.info(N_("Среднее D по %d линиям: %.3f."), dcnt, dsum / dcnt)
    return {"OUTPUT": out}
