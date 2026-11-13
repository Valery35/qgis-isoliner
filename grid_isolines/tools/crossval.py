# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""1.08 Кросс-валидация вариограммы: расчёт без QGIS.

Поля - ключи параметров класса CrossValidationAlgorithm, в том числе
общие поля кригинга (_add_cv_params): KTYPE, RADIUS, MIN_POINTS,
MAX_POINTS, SKMEAN, NUGGET, S1_*, отсев проб. Вариограмма берётся из
таблицы моделей VG_TABLE (таблица, вид table), если она подана, иначе
из полей окна. Выход OUTPUT - точки остатков. Числа для HTML-отчёта
класс QGIS берёт из result.stats.
"""

import math
import re

import numpy as np

from ..kb2d import PolyTrend, cross_validate, cross_validate_detrend
from ..toolspec import N_, Result, ToolError
from . import cv, vario
from .declustering import value_field
from .points import read_points


def san(name):
    """Имя поля из текста: как _san в algorithms.py."""
    s = re.sub(r"\W+", "_", (name or "").strip(), flags=re.UNICODE)
    return s.strip("_")[:40]


def _opt(values, key):
    v = values.get(key)
    if v is None or v == "":
        return None
    return float(v)


def run(values, report):
    items = values["INPUT"]
    layer = values.get("@INPUT")
    zfield = values.get("ZFIELD") or ""
    idfield = values.get("IDFIELD") or None
    pct = float(values.get("VAL_PCT") or 0.0)
    cap = bool(values.get("VAL_CAP"))
    vmin, vmax = _opt(values, "VAL_MIN"), _opt(values, "VAL_MAX")
    xd, yd, vrd, ids = read_points(items, zfield, layer, report, vmin=vmin,
                                   vmax=vmax, pct=pct, cap=cap,
                                   id_field=idfield, return_ids=True)
    vario.warn_data(report, xd, yd, vrd)
    wfield = values.get("WEIGHT_FIELD") or None
    cvw = None
    if wfield:
        _wx, _wy, _wv, wids = read_points(items, zfield, layer, report,
                                          vmin=vmin, vmax=vmax, pct=pct,
                                          cap=cap, id_field=wfield,
                                          return_ids=True)
        cvw, ok_w = cv.read_weights(report, wids, len(xd))
        if ok_w:
            report.info(N_("Метрики взвешены декластеризацией (поле "
                           "«%s»)."), wfield)

    ktype = 1 if int(values.get("KTYPE") or 0) == 0 else 0
    skmean = float(values.get("SKMEAN") or 0.0)
    nugget = float(values.get("NUGGET") or 0.0)
    radius = float(values.get("RADIUS") or 0.0)
    ndmin = int(values.get("MIN_POINTS") or 1)
    ndmax = int(values.get("MAX_POINTS") or 24)
    width = float(xd.max() - xd.min())
    height = float(yd.max() - yd.min())
    auto_range = max(width, height) / 3.0 or 1.0
    if radius <= 0:
        radius = math.hypot(width, height) or 1e12
    rad2 = radius * radius
    vg = cv.build_variogram(values, report, nugget, auto_range)
    nodata = -9999.0

    dvar = float(np.var(vrd))
    report.info(N_("Дисперсия данных: %.4g. Ориентир: суммарный силл (C0 + "
                   "вклады C) задавайте близким к ней. Наггет и силл - в "
                   "абсолютных единицах дисперсии, не 0-1."), dvar)
    report.info(N_("Кросс-валидация по %d точкам…"), len(xd))

    def prog(done, total):
        if report.canceled():
            raise ToolError(N_("Прервано пользователем."))
        report.progress(0.95 * done / total)

    detrend = bool(values.get("DETREND"))
    degree = 1
    if detrend:
        degree = int(values.get("DETREND_DEG") or 0) + 1
        need = PolyTrend.n_terms(degree)
        if len(vrd) <= need:
            report.warn(N_("Снятие тренда отключено: точек %d, для степени %d "
                           "нужно больше %d."), len(vrd), degree, need)
            detrend = False
        else:
            var0 = float(np.var(vrd))
            _r = PolyTrend.fit(xd, yd, vrd, degree).residuals(xd, yd, vrd)
            share = 100.0 * (1.0 - float(np.var(_r)) / var0) if var0 > 0 \
                else 0.0
            report.info(N_("Снят тренд степени %d: убрано %.1f%% дисперсии. "
                           "Тренд переподбирается на каждом шаге LOO по "
                           "остальным точкам, вариограмму задавайте по "
                           "остаткам."), degree, share)
    if detrend:
        est, var = cross_validate_detrend(xd, yd, vrd, degree, vg, ktype,
                                          skmean, ndmin, ndmax, rad2, nodata,
                                          progress=prog)
    else:
        est, var = cross_validate(xd, yd, vrd, vg, ktype, skmean, ndmin,
                                  ndmax, rad2, nodata, progress=prog)

    ok = est != nodata
    nvalid = int(ok.sum())
    if nvalid < 2:
        raise ToolError(N_("Слишком мало оценённых точек."))
    err = est[ok] - vrd[ok]
    me, mae, rmse, msdr, r = cv.weighted_cv_metrics(
        vrd[ok], est[ok], err, var[ok],
        (cvw[ok] if cvw is not None else None))

    report.info(N_("== Кросс-валидация (leave-one-out) =="))
    report.info(N_("Точек оценено: %d из %d"), nvalid, len(xd))
    report.info(N_("ME (смещение):   %+.4g   (ближе к 0 - лучше)"), me)
    report.info("MAE:             %.4g", mae)
    report.info(N_("RMSE:            %.4g   (меньше - лучше)"), rmse)
    report.info(N_("MSDR:            %.3f   (ближе к 1 - лучше)"), msdr)
    report.info(N_("R (оценка/факт): %.3f"), r)
    advice = cv.cv_advice(report, me, mae, rmse, msdr, r)
    for a in advice:
        report.info("%s", "• " + a)

    valname = value_field(zfield)
    idname = (san(idfield) or "well_id") if idfield else None
    fields = []
    if idname:
        fields.append((idname, "string"))
    fields += [(valname, "double"), ("z_est", "double"), ("error", "double"),
               ("abs_error", "double"), ("std_resid", "double")]
    out = Result(fields, geometry="point")
    for i in np.where(ok)[0]:
        e = float(est[i] - vrd[i])
        s = float(np.sqrt(max(var[i], 0.0)))
        row = []
        if idname:
            row.append(None if ids[i] is None else str(ids[i]))
        row += [float(vrd[i]), float(est[i]), e, abs(e),
                (e / s) if s > 0 else None]
        out.add({"xyz": np.array([float(xd[i]), float(yd[i]), np.nan])}, row)
    out.stats = {"me": me, "mae": mae, "rmse": rmse, "msdr": msdr, "r": r,
                 "dvar": dvar, "nvalid": nvalid, "n": len(xd),
                 "advice": advice, "fact": vrd[ok], "est": est[ok],
                 "err": err, "ids_ok": [ids[i] for i in np.where(ok)[0]]}
    return {"OUTPUT": out}
