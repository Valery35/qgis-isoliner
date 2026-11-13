# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""4.13 Содержания по пласту в устьях: расчёт без QGIS.

Поля - ключи параметров класса BedGradesAtCollarsAlgorithm. COLLAR -
точки устьев, INTERVAL и CHEM - таблицы (table: строки с атрибутами).
Имена полей таблиц ищутся терпимо (drillhole_core.resolve_field) по
сведениям @КЛЮЧ, а без них - по атрибутам строк. Выход OUTPUT - точки
устьев с содержаниями и охватом по каждому компоненту.
"""

import numpy as np

from .. import drillhole_core as _dh
from ..toolspec import N_, Result, ToolError


def _names(values, key):
    info = values.get("@" + key) or {}
    if info.get("fields") is not None:
        return list(info["fields"])
    out = []
    for it in values.get(key) or []:
        for k in (it.get("attrs") or {}):
            if k not in out:
                out.append(k)
    return out


def _val(row, field):
    return (row.get("attrs") or {}).get(field)


def run(values, report):
    collars = values.get("COLLAR")
    intervals = values.get("INTERVAL")
    chem = values.get("CHEM")
    if collars is None or intervals is None or chem is None:
        raise ToolError(N_("Нужны три таблицы: устья, интервалы и пробы."))
    bed = (values.get("BED") or "").strip()
    if not bed:
        raise ToolError(N_("Не задан код пласта. Он берётся из поля кода в "
                           "таблице интервалов, например КрII."))
    comps = list(values.get("COMPONENTS") or [])
    if not comps:
        raise ToolError(N_("Не выбрано ни одного поля содержаний. Набор "
                           "компонентов у разных предприятий разный, поэтому "
                           "задаётся полями самой таблицы проб."))
    min_cover = float(values.get("MIN_COVER") or 0.0)

    cnames = _names(values, "COLLAR")
    inames = _names(values, "INTERVAL")
    hnames = _names(values, "CHEM")
    cid = _dh.resolve_field(cnames, values.get("CID") or "", "hole_id")
    iid = _dh.resolve_field(inames, values.get("IID") or "", "hole_id")
    ifrom = _dh.resolve_field(inames, values.get("IFROM") or "", "from")
    ito = _dh.resolve_field(inames, values.get("ITO") or "", "to")
    icode = _dh.resolve_field(inames, values.get("ICODE") or "", "code")
    hid = _dh.resolve_field(hnames, values.get("HID") or "", "hole_id")
    hfrom = _dh.resolve_field(hnames, values.get("HFROM") or "", "from")
    hto = _dh.resolve_field(hnames, values.get("HTO") or "", "to")
    missing = [n for n, v in (("устья: идентификатор", cid),
                              ("интервалы: идентификатор", iid),
                              ("интервалы: кровля", ifrom),
                              ("интервалы: подошва", ito),
                              ("интервалы: код", icode),
                              ("пробы: идентификатор", hid),
                              ("пробы: кровля", hfrom),
                              ("пробы: подошва", hto)) if not v]
    if missing:
        raise ToolError(N_("Не найдены поля: %s. Укажите их явно."),
                        ", ".join(missing))

    # Целевые интервалы: все интервалы заданного кода, по скважинам.
    targets = {}
    n_int = 0
    for row in intervals:
        code = _val(row, icode)
        if code is None or str(code).strip() != bed:
            continue
        hole = _dh.parse_id(_val(row, iid))
        span = _dh._clean_span(_val(row, ifrom), _val(row, ito))
        if not hole or span is None:
            continue
        targets.setdefault(hole, []).append(span)
        n_int += 1
    if not targets:
        raise ToolError(N_("Пласт «%s» не встретился ни в одной скважине. "
                           "Проверьте код и поле кода."), bed)

    # Пробы по скважинам. Пустые значения не подставляются нулём: их
    # просто нет в словаре, и ядро исключит их из среднего вместе с весом.
    samples = {}
    n_samp = 0
    for row in chem:
        hole = _dh.parse_id(_val(row, hid))
        if not hole or hole not in targets:
            continue
        vals = {}
        for c in comps:
            num = _dh.parse_num(_val(row, c))
            if num is not None:
                vals[c] = num
        samples.setdefault(hole, []).append((_val(row, hfrom),
                                             _val(row, hto), vals))
        n_samp += 1
    report.info(N_("Химия: проб %d по %d скважинам, компоненты %s."),
                n_samp, len(samples), ", ".join(comps))

    fields = [("hole_id", "string"), ("bed", "string"), ("len_m", "double"),
              ("cover", "double"), ("n_samp", "int")]
    for c in comps:
        fields.append((c, "double"))
        # Охват по каждому компоненту отдельно: у одного он полный, у
        # другого половина, и одно общее число это скрыло бы.
        fields.append((("cov_" + c)[:60], "double"))
    out = Result(fields, geometry="point")

    n_out = n_empty = n_low = n_over = 0
    cover_sum = 0.0
    for col in collars:
        hole = _dh.parse_id(_val(col, cid))
        if not hole or hole not in targets:
            continue
        if col.get("xyz") is None:
            continue
        rows = _dh.composite(samples.get(hole, []), targets[hole],
                             components=comps, min_cover=min_cover)
        total = sum(r["length"] for r in rows)
        if total <= 0:
            continue
        # Несколько интервалов одного пласта в стволе сводятся в одно
        # число: вес каждого это длина, обеспеченная данными.
        vals, covs = {}, {}
        for c in comps:
            wsum = sum(r["length"] * r["cover"][c] for r in rows)
            if wsum > 0:
                vsum = sum(r["length"] * r["cover"][c] * r["values"][c]
                           for r in rows if r["values"][c] is not None)
                vals[c] = vsum / wsum
            else:
                vals[c] = None
            covs[c] = wsum / total
        cover_any = sum(r["length"] * r["cover_any"] for r in rows) / total
        hits = sum(r["n_samples"] for r in rows)
        if any(r["overlap"] > 1e-9 for r in rows):
            n_over += 1
        if hits == 0:
            n_empty += 1
        elif cover_any < min_cover:
            n_low += 1
        cover_sum += cover_any
        attrs = [hole, bed, round(total, 3), round(cover_any, 4), hits]
        for c in comps:
            attrs.append(None if vals[c] is None else round(vals[c], 6))
            attrs.append(round(covs[c], 4))
        out.add({"xyz": np.asarray(col["xyz"], dtype=float)}, attrs)
        n_out += 1

    if not n_out:
        raise ToolError(N_("Ни одна скважина не получила значений. Устья не "
                           "стыкуются с интервалами по идентификатору."))
    report.info(N_("Химия: приведено к %d интервалам пласта «%s» в %d "
                   "скважинах, средний охват %.0f процентов."),
                n_int, bed, n_out, 100.0 * cover_sum / n_out)
    report.info(N_("Химия: скважин без проб по пласту %d, с охватом ниже "
                   "порога %d, с перекрытием проб %d."),
                n_empty, n_low, n_over)
    if n_low or n_empty:
        report.warn(N_("Значения с низким охватом остаются в слое. "
                       "Выбрасывать ли их, решает пользователь. Отбирайте "
                       "по полю «cover» перед кригингом."))
    return {"OUTPUT": out}
