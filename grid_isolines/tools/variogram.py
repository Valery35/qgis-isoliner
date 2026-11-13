# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""1.06 Вариограмма (экспериментальная): расчёт без QGIS.

Поля - ключи параметров класса ExperimentalVariogramAlgorithm. Расчёт
идёт до подбора модели включительно: точки и веса декластеризации,
предупреждения о данных, экспериментальная вариограмма, кривые по
группам, подбор модели. Выходы:

- OUTPUT - таблица без геометрии (geometry="none"): серия, расстояние,
  γ, количество пар;
- OUT_MODEL - таблица модели вариограммы, строка на структуру, те же
  поля, что пишет класс QGIS (variogram_table.rows_from_model).

Чего здесь нет. Запись в уже существующую таблицу моделей
(TARGET_TABLE), наложение заданной модели и облако пар (SHOW_MODEL,
SHOW_CLOUD) и HTML-отчёт - работа окна QGIS, класс делает их сам по
числам из result.stats. В описании для RustGIS этих полей нет
(SPEC_OVERRIDES).

Подписи серий в таблице OUTPUT здесь русские. Класс QGIS пишет свою
таблицу по stats["series"] с переводом подписей, как раньше.
"""

import datetime

import numpy as np

from .. import variogram_table
from ..kb2d import experimental_variogram, fit_variogram
from ..toolspec import N_, Result, ToolError
from . import vario
from .points import read_points

DROP = {"_drop": True}
# заданная модель (NUGGET, S1_*) нужна только наложению на HTML-отчёт
SPEC_OVERRIDES = dict((k, DROP) for k in (
    "SHOW_CLOUD", "SHOW_MODEL", "TARGET_TABLE", "NUGGET", "S1_MODEL",
    "S1_SILL", "S1_RANGE", "S1_AZIMUTH", "S1_ANIS"))

MODEL_NOTE = N_("автоподбор 1.06, изотропная. Анизотропию проставляет "
                "1.07 по вариограммной карте")


def build_model(name, fit, field, val_pct=0.0, val_cap=False, today=None,
                note=MODEL_NOTE):
    """Модель из результата подбора: то же, что build_model_from_fit."""
    model = variogram_table.Model((name or "").strip() or N_("без имени"))
    model.field = field or ""
    model.nugget = float(fit["nugget"])
    model.val_pct = float(val_pct)
    model.val_cap = bool(val_cap)
    model.structs = [dict(it=int(fit["model"]) + 1, cc=float(fit["sill"]),
                          aa=float(fit["range"]), ang=0.0, anis=1.0,
                          struct=1)]
    model.fitted = today or datetime.date.today().isoformat()
    model.note = note
    return model


MODEL_FIELDS = (("profile", "string"), ("field", "string"),
                ("struct", "int"), ("model", "int"), ("sill", "double"),
                ("range", "double"), ("azimuth", "double"),
                ("anis", "double"), ("nugget", "double"),
                ("val_pct", "double"), ("val_cap", "int"),
                ("fitted", "string"), ("author", "string"),
                ("note", "string"))


def _opt(values, key):
    v = values.get(key)
    if v is None or v == "":
        return None
    return float(v)


def _group_key(v):
    return "" if v is None else str(v)


def run(values, report):
    items = values["INPUT"]
    layer = values.get("@INPUT")
    zfield = values.get("ZFIELD") or ""
    gfield = values.get("GROUP_FIELD") or None
    min_group_pct = float(values.get("MIN_GROUP_PCT") or 0.0)
    n_lags = int(values["N_LAGS"])
    maxlag = float(values.get("MAXLAG") or 0.0)
    robust = bool(values.get("ROBUST"))
    show_cloud = bool(values.get("SHOW_CLOUD"))
    do_fit = bool(values.get("FIT"))
    fit_choice = int(values.get("FIT_MODEL") or 0)
    pct = float(values.get("VAL_PCT") or 0.0)
    cap = bool(values.get("VAL_CAP"))
    vmin = _opt(values, "VAL_MIN")
    vmax = _opt(values, "VAL_MAX")

    wfield = values.get("WEIGHT_FIELD") or None
    wts = None
    if wfield:
        xs, ys, vs, wids = read_points(items, zfield, layer, report,
                                       vmin=vmin, vmax=vmax, pct=pct,
                                       cap=cap, id_field=wfield,
                                       return_ids=True)
        try:
            wv = np.array([float(w) if w is not None else np.nan
                           for w in wids], float)
            if np.all(np.isfinite(wv)) and np.all(wv > 0):
                wts = wv
                report.info(N_("Пары взвешены декластеризацией (поле "
                               "«%s»)."), wfield)
            else:
                report.warn(N_("Поле весов содержит пустые или "
                               "неположительные значения - веса "
                               "игнорируются."))
        except (TypeError, ValueError):
            report.warn(N_("Не удалось прочитать поле весов - веса "
                           "игнорируются."))
    else:
        xs, ys, vs = read_points(items, zfield, layer, report, vmin=vmin,
                                 vmax=vmax, pct=pct, cap=cap)
    vario.warn_data(report, xs, ys, vs)
    data_var = float(np.var(vs))
    report.info(N_("Точек: %d. Дисперсия данных: %.4g (ориентир для "
                   "суммарного порога)."), len(xs), data_var)
    group_min = max(int(round(min_group_pct / 100.0 * len(xs))), 30)

    ev = experimental_variogram(xs, ys, vs, n_lags=n_lags, maxlag=maxlag,
                                robust=robust, wts=wts,
                                cloud_max=(20000 if show_cloud else 0))
    if ev["subsampled"]:
        report.info(N_("Точек много - для расчёта пар использована "
                       "случайная подвыборка %d точек."), ev["n_used"])
    vario.report_nugget_pairs(report, xs, ys, vs, ev, data_var)
    if maxlag and maxlag > 0:
        W = float(xs.max() - xs.min())
        H = float(ys.max() - ys.min())
        spacing = (W * H / max(len(xs), 1)) ** 0.5 if W > 0 and H > 0 \
            else 0.0
        if spacing > 0 and maxlag < spacing:
            report.warn(N_("Максимальное расстояние (%.4g) меньше типичного "
                           "шага между точками (~%.4g) - пар почти нет. "
                           "Значение задаётся в единицах слоя (обычно "
                           "метры)."), maxlag, spacing)
    # k - номер серии для цвета: 0 общая, у группы - номер значения поля
    # среди всех значений плюс один, пропущенные группы номер не отдают
    series = [{"all": True, "group": None, "k": 0, "lag": ev["lag"],
               "gamma": ev["gamma"], "npairs": ev["npairs"]}]

    # группировка: отдельная кривая на каждое значение поля
    if gfield is not None:
        vals, seen = [], set()
        for it in items:
            g = (it.get("attrs") or {}).get(gfield)
            key = _group_key(g)
            if key not in seen:
                seen.add(key)
                vals.append(g)
            if len(seen) > 12:
                break
        if len(vals) > 12:
            report.warn(N_("Групп больше 12 - группировка пропущена."))
        elif len(vals) >= 2:
            skipped = []
            for k, g in enumerate(vals):
                if g is None:
                    sub = [it for it in items
                           if (it.get("attrs") or {}).get(gfield) is None]
                else:
                    sub = [it for it in items
                           if (it.get("attrs") or {}).get(gfield) is not None
                           and str(it["attrs"][gfield]) == str(g)]
                try:
                    gx, gy, gv = read_points(sub, zfield, layer, None,
                                             vmin=vmin, vmax=vmax, pct=pct,
                                             cap=cap)
                except ToolError:
                    continue
                if len(gx) < group_min:
                    skipped.append((g, len(gx)))
                    continue
                gev = experimental_variogram(gx, gy, gv, n_lags=n_lags,
                                             maxlag=ev["maxlag"],
                                             robust=robust, cloud_max=0)
                series.append({"all": False, "group": g, "k": k + 1,
                               "lag": gev["lag"],
                               "gamma": gev["gamma"],
                               "npairs": gev["npairs"]})
            if skipped:
                txt = ", ".join("%s (%d)" % (
                    (vario.word(report, N_("(пусто)")) if g is None else g),
                    n) for g, n in skipped)
                report.info(N_("Группы меньше %d точек пропущены: %s."),
                            group_min, txt)
        else:
            report.info(N_("В поле группировки меньше 2 значений - строю "
                           "только общую кривую."))

    # подбор модели по общей кривой (рекомендация)
    fit = None
    if do_fit:
        model_arg = "auto" if fit_choice == 0 else (fit_choice - 1)
        fit = fit_variogram(ev["lag"], ev["gamma"], ev["npairs"],
                            model=model_arg)
        if fit:
            report.info(N_("Подбор: модель %s, C0=%.4g, C=%.4g, a=%.4g, "
                           "R²=%.3f"), vario.MODEL_NAMES[fit["model"]],
                        fit["nugget"], fit["sill"], fit["range"], fit["r2"])
            vario.warn_fit_quality(report, fit)

    out = Result([("series", "string"), ("lag", "double"),
                  ("gamma", "double"), ("npairs", "int")], geometry="none")
    for s in series:
        label = N_("все точки") if s["all"] else "%s = %s" % (
            gfield, N_("(пусто)") if s["group"] is None else s["group"])
        for i in range(len(s["lag"])):
            out.add(None, [label, float(s["lag"][i]), float(s["gamma"][i]),
                           int(s["npairs"][i])])
    out.stats = {"xs": xs, "ys": ys, "vs": vs, "data_var": data_var,
                 "ev": ev, "series": series, "fit": fit,
                 "group_field": gfield}
    res = {"OUTPUT": out}
    if fit and values.get("OUT_MODEL", True) is not False:
        model = build_model(values.get("SAVE_PROFILE") or "", fit, zfield,
                            val_pct=pct, val_cap=cap)
        mres = Result(list(MODEL_FIELDS), geometry="none")
        for row in variogram_table.rows_from_model(model):
            mres.add(None, [int(row[n]) if t == "int" and row[n] is not None
                            else row[n] for n, t in MODEL_FIELDS])
        res["OUT_MODEL"] = mres
    return res
