# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Точки со значениями из поля points: то же, что _read_points в
algorithms.py, но без QGIS.

Вход - список {"id", "xyz": (x, y, z) или None, "attrs"}, как его
даёт контракт toolspec. Значение берётся из поля zfield, а при пустом
поле из Z геометрии. Порядок шагов и сообщения те же, что у
_read_points: пропуск точек без значения и без геометрии, отсев или
срезка ураганных проб, усреднение совпадающих точек. Если совпадающие
точки нашлись, после усреднения все точки идут в порядке сортировки
координат (np.unique), а не в порядке слоя, так было и в классе QGIS.
Без совпадений порядок слоя сохраняется.
"""

import math

import numpy as np

from ..kb2d import clip_outliers
from ..toolspec import N_, ToolError


def _num(v):
    if v is None or isinstance(v, bool):
        return None if v is None else float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_points(items, zfield, layer=None, report=None, vmin=None,
                vmax=None, pct=0.0, cap=False, id_field=None,
                return_ids=False, outliers_out=None):
    """xs, ys, vs (и ids при return_ids) по точкам items.

    layer - сведения о слое values["@КЛЮЧ"] (fields, hasz), без них
    наличие поля и Z проверяются по самим точкам."""
    zfield = (zfield or "").strip()
    use_geom_z = not zfield
    layer = layer or {}
    if not use_geom_z:
        fields = layer.get("fields")
        if fields is not None and zfield not in fields:
            raise ToolError(N_(
                "Поле «%s» не найдено в слое. Выберите поле значения Z."),
                zfield)
    else:
        hasz = layer.get("hasz")
        if hasz is None:
            hasz = any(it.get("xyz") is not None and len(it["xyz"]) > 2
                       and _num(it["xyz"][2]) is not None
                       and math.isfinite(_num(it["xyz"][2]))
                       for it in items)
        if not hasz:
            raise ToolError(N_(
                "Поле значения не задано, а геометрия слоя без Z. Укажите "
                "поле или подайте слой с отметкой в геометрии."))
    xs, ys, vs = [], [], []
    ids = [] if return_ids else None
    skipped_geom = 0
    skipped_value = 0
    for it in items:
        xyz = it.get("xyz")
        if xyz is None:
            skipped_geom += 1
            continue
        attrs = it.get("attrs") or {}
        if use_geom_z:
            v = _num(xyz[2]) if len(xyz) > 2 else None
            if v is None or v != v:
                skipped_value += 1
                continue
        else:
            v = _num(attrs.get(zfield))
            if v is None:
                skipped_value += 1
                continue
        xs.append(float(xyz[0]))
        ys.append(float(xyz[1]))
        vs.append(float(v))
        if return_ids:
            ids.append(attrs.get(id_field) if id_field else it.get("id"))
    if report is not None and (skipped_value or skipped_geom):
        what = zfield or N_("Z геометрии")
        if skipped_geom:
            report.info(N_("Пропущено точек: %d без значения «%s» и %d без "
                           "геометрии. Прочитано: %d."),
                        skipped_value, what, skipped_geom, len(xs))
        else:
            report.info(N_("Пропущено точек: %d без значения «%s». "
                           "Прочитано: %d."), skipped_value, what, len(xs))
    if len(xs) < 2:
        raise ToolError(N_(
            "Недостаточно валидных точек с числовым значением."))

    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    if return_ids:
        ids = np.asarray(ids, dtype=object)

    out, keep, lo, hi = clip_outliers(vs, vmin, vmax, pct, cap)
    if not (lo == float("-inf") and hi == float("inf")):
        if cap:
            nch = int(np.count_nonzero(out != vs))
            vs = out
            if report is not None:
                report.info(N_("Ураганные пробы: срезано %d значений к "
                               "[%.4g; %.4g]."), nch, lo, hi)
        else:
            ncut = int(np.count_nonzero(~keep))
            if outliers_out is not None and ncut:
                bad = ~keep
                for x, y, v in zip(xs[bad], ys[bad], vs[bad]):
                    outliers_out.append((float(x), float(y), float(v),
                                         "lo" if v < lo else "hi", lo, hi))
            xs, ys, vs = xs[keep], ys[keep], vs[keep]
            if return_ids:
                ids = ids[keep]
            if report is not None:
                report.info(N_("Ураганные пробы: удалено %d точек вне "
                               "[%.4g; %.4g], осталось %d."),
                            ncut, lo, hi, len(xs))
        if len(xs) < 2:
            raise ToolError(N_(
                "После отсева ураганных проб осталось < 2 точек."))

    span = max(float(xs.max() - xs.min()), float(ys.max() - ys.min()), 1e-9)
    tol = span * 1e-7
    key = np.round(np.column_stack([xs, ys]) / tol) * tol
    uniq, first, inv = np.unique(key, axis=0, return_index=True,
                                 return_inverse=True)
    inv = np.asarray(inv).reshape(-1)
    if len(uniq) < len(xs):
        sums = np.zeros(len(uniq))
        cnts = np.zeros(len(uniq))
        np.add.at(sums, inv, vs)
        np.add.at(cnts, inv, 1.0)
        vs = sums / cnts
        xs = uniq[:, 0]
        ys = uniq[:, 1]
        if return_ids:
            ids = ids[first]
        if report is not None:
            report.info(N_("Совпадающих точек усреднено: %d (осталось %d)"),
                        len(inv) - len(uniq), len(uniq))
    if len(xs) < 2:
        raise ToolError(N_(
            "После усреднения совпадающих точек осталось < 2 узлов."))
    if return_ids:
        return xs, ys, vs, ids
    return xs, ys, vs
