# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""7.04 Размерность Минковского (векторы): расчёт без QGIS.

Поле FEATURES - объекты (features). У полигона частями идут все кольца,
и внешнее, и внутренние: граница водосбора с озером меряется целиком, так
считал и класс QGIS. Части короче двух вершин отбрасываются. Выход
OUTPUT - те же объекты (geometry="source") с полями D_mink и D_r2.
Размерность всего слоя, R² и ряд «размер ячейки - количество ячеек»
идут в отчёт и в result.stats (класс QGIS отдаёт их выходами OUT_D и
OUT_R2). Сдвиги сетки берутся генератором с зерном 0, расчёт
детерминирован.
"""

import numpy as np

from ..fractal import minkowski_dimension
from ..toolspec import N_, Result


def _parts(it):
    out = []
    for p in it.get("parts") or []:
        a = np.asarray(p, dtype=float).reshape(-1, 3)[:, :2]
        if len(a) >= 2:
            out.append(np.ascontiguousarray(a))
    return out


def run(values, report):
    feats = values.get("FEATURES") or []
    n_sizes = int(values["N_SIZES"])
    offsets = int(values["OFFSETS"])
    densify = float(values["DENSIFY"])
    out = Result([("D_mink", "double"), ("D_r2", "double")],
                 keep_source="FEATURES", geometry="source")
    total = len(feats) or 1
    all_parts = []
    for k, it in enumerate(feats):
        if report.canceled():
            break
        report.progress(0.9 * k / total)
        parts = _parts(it)
        all_parts.extend(parts)
        D = r2 = float("nan")
        if parts:
            D, r2, _s, _c = minkowski_dimension(
                parts, n_sizes=n_sizes, offsets=offsets, densify=densify)
        out.add({"source": it.get("id")},
                [None if D != D else round(D, 4),
                 None if r2 != r2 else round(r2, 4)])
    d_layer = r2_layer = float("nan")
    sizes, counts = [], []
    if all_parts:
        d_layer, r2_layer, sizes, counts = minkowski_dimension(
            all_parts, n_sizes=n_sizes, offsets=offsets, densify=densify)
        for s, c in zip(sizes, counts):
            report.info("  %.6g -> N = %d", s, c)
    report.info(N_("Размерность Минковского слоя: D = %.3f (R² = %.3f)."),
                d_layer, r2_layer)
    if r2_layer == r2_layer and r2_layer < 0.85:
        report.warn(N_("R² ниже 0.85: степенной закон не выдержан, оценке "
                       "доверять нельзя (уменьшите K или проверьте данные)."))
    out.stats = {"D_layer": float(d_layer), "R2_layer": float(r2_layer),
                 "sizes": [float(s) for s in sizes],
                 "counts": [int(c) for c in counts]}
    return {"OUTPUT": out}
