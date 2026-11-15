# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""4.11 Образец справочника пластов: расчёт без QGIS.

Полей нет, кроме выхода. Образец читается из поставки
(templates/plast_reference_vkmks.csv) терпимым читателем
plast_reference и выдаётся таблицей без геометрии (geometry="none"),
строки по порядку залегания. Вид тела («пласт», «междупластье») -
слово интерфейса: в QGIS на языке окна, без него русское.
"""

import os

from .. import plast_reference
from ..toolspec import N_, Result, ToolError
from .vario import word

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "templates", "plast_reference_vkmks.csv")


def run(values, report):
    path = TEMPLATE
    if not os.path.exists(path):
        raise ToolError(N_("Образец справочника не найден в поставке: %s"),
                        path)
    rsum = plast_reference.ReadSummary()
    ref = plast_reference.Reference.from_csv(path, summary=rsum)
    for ln in rsum.lines(lambda s: word(report, s)):
        report.info("%s", ln)
    out = Result([("code", "string"), ("order", "int"), ("body", "string"),
                  ("color", "string"), ("strata", "string"),
                  ("note", "string")], geometry="none")
    for b in sorted(ref.beds, key=lambda x: x.order):
        out.add(None, [b.code, int(b.order),
                       word(report, N_("междупластье")) if b.is_interbed
                       else word(report, N_("пласт")),
                       b.color or "", getattr(b, "strata", "") or "",
                       getattr(b, "note", "") or ""])
    report.info(N_("Образец положен в проект. Подайте его в 4.01 и 4.02 как "
                   "«Справочник пластов (таблица)»."))
    return {"OUTPUT": out}
