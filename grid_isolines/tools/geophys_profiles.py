# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""1.11 Пример геофизических профилей (демо): расчёт без QGIS.

Поля - ключи параметров класса GeophysProfilesDemoAlgorithm. Охват
EXTENT приходит объектами (features): область - их общий охват. Класс
QGIS подаёт свой прямоугольник охвата двумя углами, а RustGIS - слой,
по охвату которого строить (SPEC_OVERRIDES). Выход OUTPUT - точки
профилей.

Значения зависят от генератора NumPy (geodemo), поэтому перенос на
другой язык сверяется по количеству точек, полям и диапазонам, а не
по числам.
"""

import numpy as np

from .. import geodemo
from ..toolspec import N_, Result, ToolError

MODES = ("electro", "subsidence")

SPEC_OVERRIDES = {
    "EXTENT": {"kind": "features",
               "label": "Слой, по охвату которого строить",
               "hint": "Профили ложатся в охват объектов этого слоя."},
}


def bbox(features):
    """Общий охват объектов features (части и точки): x0, y0, x1, y1."""
    pts = []
    for f in features or []:
        for part in f.get("parts") or []:
            a = np.asarray(part, dtype=float).reshape(-1, 3)[:, :2]
            pts.append(a)
        if f.get("xyz") is not None:
            pts.append(np.asarray(f["xyz"], dtype=float)[:2].reshape(1, 2))
    if not pts:
        return None
    a = np.vstack(pts)
    a = a[np.isfinite(a).all(axis=1)]
    if not len(a):
        return None
    return (float(a[:, 0].min()), float(a[:, 1].min()),
            float(a[:, 0].max()), float(a[:, 1].max()))


def run(values, report):
    ext = bbox(values.get("EXTENT"))
    if ext is None or not (ext[2] > ext[0] and ext[3] > ext[1]):
        raise ToolError(N_("Не задан охват."))
    mode = MODES[int(values.get("MODE") or 0)]
    args = dict(n_profiles=int(values["N_PROFILES"]),
                picket_step=float(values["PICKET_STEP"]),
                z_base=float(values["Z_BASE"]), z_amp=float(values["Z_AMP"]),
                seed=int(values["SEED"]))
    fields = [("profile", "int"), ("picket_m", "double"), ("pk", "string")]
    if mode == "subsidence":
        subs = float(values["SUBS_MAX"])
        d = geodemo.gen_subsidence(
            *ext, subs_max=subs, n_tours=int(values["N_TOURS"]),
            positive=int(values.get("SUBS_SIGN") or 0) == 1,
            noise=max(subs * 0.01, 1.0), **args)
        fields += [("tour", "int"), ("z", "double"), ("settle", "double"),
                   ("settle_true", "double")]
    else:
        d = geodemo.gen_profiles(
            *ext, rho_bg=float(values["RHO_BG"]),
            rho_min=float(values["RHO_MIN"]),
            sp_amp=float(values["SP_AMP"]), vp_amp=float(values["VP_AMP"]),
            noise=float(values["NOISE"]), **args)
        fields += [("z", "double"), ("rho_k", "double"),
                   ("rho_true", "double"), ("sp", "double"),
                   ("vp", "double")]
    out = Result(fields, geometry="point")
    xs, ys, pkm = d["x"], d["y"], d["picket_m"]
    n = len(d["profile"])
    for i in range(n):
        row = []
        for name, _t in fields:
            if name == "pk":
                row.append(geodemo.pk_label(pkm[i]))
            elif name in ("profile", "tour"):
                row.append(int(d[name][i]))
            else:
                row.append(float(d[name][i]))
        out.add({"xyz": np.array([float(xs[i]), float(ys[i]), np.nan])}, row)

    n_prof = int(len(set(d["profile"].tolist())))
    if mode == "subsidence":
        n_tours = int(len(set(d["tour"].tolist())))
        report.info(N_("Оседания: профилей %d, туров %d, точек %d. Мульда до "
                       "%.1f мм. Разность settle между турами по одним "
                       "пикетам даёт скорость оседания."),
                    n_prof, n_tours, n, float(d["settle"].min()))
        out.stats = {"mode": mode, "n_tours": n_tours}
    else:
        rho, sp = d["rho_k"], d["sp"]
        report.info(N_("Электроразведка: профилей %d, точек %d. ρк "
                       "%.4g..%.4g Ом·м, ЕП %.1f..%.1f мВ."),
                    n_prof, n, float(rho.min()), float(rho.max()),
                    float(sp.min()), float(sp.max()))
        report.info(N_("Интерполируйте rho_k (2D Kriging или минимальная "
                       "кривизна) и постройте изолинии - аномалия-пятно "
                       "оконтурится. Поле rho_true - эталон без шума для "
                       "проверки точности."))
        out.stats = {"mode": mode}
    return {"OUTPUT": out}
