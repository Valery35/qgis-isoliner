# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""1.09 Кросс-валидация метода (LOO): расчёт без QGIS.

Поля - ключи параметров класса MethodCrossValidationAlgorithm. Два
охвата окна QGIS (EXTENT - сетка минимальной кривизны, FILTER_EXTENT -
область проверки) приходят объектами (features), охват - их общий
охват. Класс QGIS подаёт прямоугольник двумя углами, RustGIS - слой
(SPEC_OVERRIDES). Без EXTENT сетка строится по охвату всех точек слоя.

Когда проверяемых точек меньше, чем прошло фильтры, выборка случайная
(NumPy default_rng с зерном SEED). Перенос на другой язык этот выбор
повторит только с тем же генератором, поэтому образцы прогонов берут
N не больше числа точек: тогда проверяются все.
"""

import math

import numpy as np

from ..toolspec import N_, Result, ToolError
from . import cv
from .crossval import san
from .declustering import value_field
from .geophys_profiles import bbox
from .points import read_points

METHODS = ("kriging", "mincurv")

SPEC_OVERRIDES = {
    "EXTENT": {"kind": "features", "optional": True,
               "label": "Мин. кривизна: слой, по охвату которого строить "
                        "сетку (пусто = по точкам)",
               "hint": "Сетка минимальной кривизны ложится в охват "
                       "объектов этого слоя."},
    "FILTER_EXTENT": {"kind": "features", "optional": True,
                      "label": "Проверять только в охвате слоя (пусто = "
                               "везде)",
                      "hint": "Проверяются только точки внутри охвата "
                              "объектов этого слоя."},
}


def _opt(values, key):
    v = values.get(key)
    if v is None or v == "":
        return None
    return float(v)


def _points_bbox(items):
    pts = [it["xyz"] for it in items if it.get("xyz") is not None]
    if not pts:
        return None
    a = np.asarray(pts, dtype=float)[:, :2]
    return (float(a[:, 0].min()), float(a[:, 1].min()),
            float(a[:, 0].max()), float(a[:, 1].max()))


def run(values, report):
    items = values["INPUT"]
    layer = values.get("@INPUT")
    zfield = values.get("ZFIELD") or ""
    idfield = values.get("IDFIELD") or None
    method = METHODS[int(values.get("METHOD") or 0)]
    pct = float(values.get("VAL_PCT") or 0.0)
    cap = bool(values.get("VAL_CAP"))
    vmin, vmax = _opt(values, "VAL_MIN"), _opt(values, "VAL_MAX")
    xs, ys, vs, ids = read_points(items, zfield, layer, report, vmin=vmin,
                                  vmax=vmax, pct=pct, cap=cap,
                                  id_field=idfield, return_ids=True)
    n = len(xs)
    wfield = values.get("WEIGHT_FIELD") or None
    mcw = None
    if wfield:
        _wx, _wy, _wv, wids = read_points(items, zfield, layer, report,
                                          vmin=vmin, vmax=vmax, pct=pct,
                                          cap=cap, id_field=wfield,
                                          return_ids=True)
        mcw, ok_w = cv.read_weights(report, wids, n)
        if ok_w:
            report.info(N_("Метрики взвешены декластеризацией (поле "
                           "«%s»)."), wfield)

    # отбор проверяемых точек: фильтр области и по значению
    cand = np.ones(n, dtype=bool)
    frect = bbox(values.get("FILTER_EXTENT"))
    if frect is not None and frect[2] > frect[0] and frect[3] > frect[1]:
        cand &= (xs >= frect[0]) & (xs <= frect[2]) \
            & (ys >= frect[1]) & (ys <= frect[3])
    zmin, zmax = _opt(values, "ZMIN"), _opt(values, "ZMAX")
    if zmin is not None:
        cand &= vs >= zmin
    if zmax is not None:
        cand &= vs <= zmax
    cand_idx = np.where(cand)[0]
    if len(cand_idx) < 2:
        raise ToolError(N_("После фильтров осталось меньше двух проверяемых "
                           "точек."))

    nreq = int(values.get("N_VALIDATE") or 0)
    if nreq <= 0:
        nreq = min(len(cand_idx), 100)
    nreq = min(nreq, len(cand_idx))
    seed = int(values.get("SEED") or 0)
    rng = np.random.default_rng(seed if seed > 0 else None)
    if nreq < len(cand_idx):
        val_idx = np.sort(rng.choice(cand_idx, size=nreq, replace=False))
    else:
        val_idx = cand_idx
    excl_x = float(values.get("EXCL_X") or 0.0)
    excl_y = float(values.get("EXCL_Y") or 0.0)
    nodata = -9999.0
    report.info(N_("Метод: %s. Проверяем %d из %d точек."),
                N_("кригинг") if method == "kriging"
                else N_("минимальная кривизна"), len(val_idx), n)

    est = np.full(len(val_idx), np.nan)
    var = None
    grid = None
    if method == "kriging":
        from ..kb2d import krige_point
        ktype = 1 if int(values.get("KTYPE") or 0) == 0 else 0
        skmean = float(values.get("SKMEAN") or 0.0)
        nugget = float(values.get("NUGGET") or 0.0)
        radius = float(values.get("RADIUS") or 0.0)
        ndmin = int(values.get("MIN_POINTS") or 1)
        ndmax = int(values.get("MAX_POINTS") or 24)
        width = float(xs.max() - xs.min())
        height = float(ys.max() - ys.min())
        auto_range = max(width, height) / 3.0 or 1.0
        if radius <= 0:
            radius = math.hypot(width, height) or 1e12
        rad2 = radius * radius
        vg = cv.build_variogram(values, report, nugget, auto_range)
        var = np.full(len(val_idx), np.nan)
        idxall = np.arange(n)
        for k, i in enumerate(val_idx):
            if report.canceled():
                raise ToolError(N_("Прервано."))
            drop = idxall == i
            if excl_x > 0 or excl_y > 0:
                drop = drop | ((np.abs(xs - xs[i]) <= excl_x)
                               & (np.abs(ys - ys[i]) <= excl_y))
            keep = ~drop
            if int(keep.sum()) < max(1, ndmin):
                continue
            e, v = krige_point(float(xs[i]), float(ys[i]), xs[keep],
                               ys[keep], vs[keep], vg, ktype, skmean, ndmin,
                               ndmax, rad2, nodata, return_var=True)
            if e != nodata:
                est[k] = e
                var[k] = v
            report.progress(0.95 * (k + 1) / len(val_idx))
    else:
        from .. import mincurv as mc
        rect = bbox(values.get("EXTENT"))
        if rect is None or not (rect[2] > rect[0] and rect[3] > rect[1]):
            rect = _points_bbox(items)
        xmin, ymin, xmax, ymax = rect
        width, height = xmax - xmin, ymax - ymin
        cell = float(values.get("CELL_SIZE") or 0.0)
        if cell <= 0:
            cell = (min(width, height) / 50.0) or 1.0
        nx = max(int(math.ceil(width / cell)), 2)
        ny = max(int(math.ceil(height / cell)), 2)
        tension = float(values.get("TENSION") or 0.0)
        btens = float(values.get("BOUNDARY_TENSION") or 0.0)
        relax = float(values.get("RELAX") or 1.85)
        aniso = float(values.get("ANISO") or 1.0)
        max_iter = int(values.get("MAX_ITER") or 100000)
        max_res = float(values.get("MAX_RESIDUAL") or 0.0)
        zr = float(np.nanmax(vs) - np.nanmin(vs)) or 1.0
        tol = max_res if max_res > 0 else max(1e-6, 1e-4 * zr)
        report.info(N_("Сетка %d x %d, ячейка %.4g."), nx, ny, cell)

        def prog(done, total):
            if report.canceled():
                raise ToolError(N_("Прервано."))
            report.progress(0.95 * done / max(total, 1))

        est, _zf = mc.loo_estimates(
            xs, ys, vs, xmin, ymin, cell, nx, ny, val_idx,
            tension=tension, boundary_tension=btens, relax=relax,
            aniso=aniso, tol=tol, base_iter=max_iter,
            loo_iter=max(2000, max_iter // 20),
            excl_x=excl_x, excl_y=excl_y, progress=prog)
        grid = {"nx": nx, "ny": ny, "cell": cell, "tension": tension}

    fact = vs[val_idx]
    ok = np.isfinite(est)
    nvalid = int(ok.sum())
    if nvalid < 2:
        raise ToolError(N_("Слишком мало оценённых точек."))
    err = est[ok] - fact[ok]
    w_ok = mcw[val_idx][ok] if mcw is not None else None
    me, mae, rmse, msdr, r = cv.weighted_cv_metrics(
        fact[ok], est[ok], err, (var[ok] if var is not None else None), w_ok)

    report.info(N_("== Кросс-валидация метода (LOO) =="))
    report.info(N_("Точек оценено: %d из %d"), nvalid, len(val_idx))
    report.info(N_("ME (смещение):   %+.4g"), me)
    report.info("MAE:             %.4g", mae)
    report.info(N_("RMSE:            %.4g"), rmse)
    if var is not None:
        report.info(N_("MSDR:            %.3f"), msdr)
    report.info(N_("R (оценка/факт): %.3f"), r)
    advice = cv.cv_advice(report, me, mae, rmse, msdr, r)
    for a in advice:
        report.info("%s", "• " + a)

    fields = []
    idname = (san(idfield) or "well_id") if idfield else None
    if idname:
        fields.append((idname, "string"))
    fields += [(value_field(zfield), "double"), ("z_est", "double"),
               ("error", "double"), ("abs_error", "double")]
    if var is not None:
        fields.append(("std_resid", "double"))
    out = Result(fields, geometry="point")
    for k, i in enumerate(val_idx):
        if not ok[k]:
            continue
        e = float(est[k] - fact[k])
        row = []
        if idname:
            row.append(None if ids[i] is None else str(ids[i]))
        row += [float(fact[k]), float(est[k]), e, abs(e)]
        if var is not None:
            s = float(np.sqrt(max(var[k], 0.0)))
            row.append((e / s) if s > 0 else None)
        out.add({"xyz": np.array([float(xs[i]), float(ys[i]), np.nan])}, row)
    out.stats = {"me": me, "mae": mae, "rmse": rmse, "msdr": msdr, "r": r,
                 "nvalid": nvalid, "n_val": len(val_idx), "advice": advice,
                 "fact": fact[ok], "est": est[ok], "err": err,
                 "ids_ok": [ids[i] for k, i in enumerate(val_idx) if ok[k]],
                 "has_var": var is not None, "grid": grid}
    return {"OUTPUT": out}
