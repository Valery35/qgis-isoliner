# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""2.08 Разделить горизонтали для проверки: расчёт без QGIS.

Поле INPUT - линии горизонталей, FIELD - поле отметки. Горизонталь без
геометрии (без частей) или без числовой отметки пропускается. Каждая
EVERY-я отметка со сдвигом OFFSET уходит в проверку целиком
(validate_core.split_levels). Выходы OUTPUT_BUILD и OUTPUT_CHECK - те же
объекты (geometry="source") с полем hold: 0 построение, 1 проверка.
"""

from .. import validate_core as _vc
from ..toolspec import N_, Result, ToolError


def run(values, report):
    items = values.get("INPUT") or []
    field = values.get("FIELD") or ""
    every = int(values.get("EVERY") or 0) or 4
    offset = int(values.get("OFFSET") or 0)

    feats, levels = [], []
    for it in items:
        if not any(len(p) for p in (it.get("parts") or [])):
            continue
        try:
            lv = float((it.get("attrs") or {})[field])
        except (TypeError, ValueError, KeyError):
            continue
        if lv != lv:      # nan
            continue
        feats.append((it.get("id"), lv))
        levels.append(lv)
    if not feats:
        raise ToolError(N_("В слое нет горизонталей с числовой отметкой."))

    build_lv, check_lv = _vc.split_levels(levels, every, offset)
    check_set = set(check_lv)
    if not check_set:
        raise ToolError(N_("Проверочный набор пуст, уровней слишком мало для "
                           "такого шага."))

    build = Result([("hold", "int")], keep_source="INPUT", geometry="source")
    check = Result([("hold", "int")], keep_source="INPUT", geometry="source")
    for fid, lv in feats:
        if lv in check_set:
            check.add({"source": fid}, [1])
        else:
            build.add({"source": fid}, [0])

    iv = _vc.contour_interval(levels)
    report.info(N_("Уровней всего %d: в построение %d, в проверку %d. "
                   "Объектов %d и %d."), len(build_lv) + len(check_lv),
                len(build_lv), len(check_lv), len(build.features),
                len(check.features))
    if iv:
        report.info(N_("Сечение рельефа по набору: %.4g."), iv)
    return {"OUTPUT_BUILD": build, "OUTPUT_CHECK": check}
