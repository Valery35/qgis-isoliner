# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""1.10 Пример скважин (демо): расчёт без QGIS.

Поля - ключи параметров класса ExampleWellsAlgorithm. Охват EXTENT
приходит объектами (features), как у 1.11. Выходы OUTPUT (точки скважин)
и OUTPUT_FAULT (линия разлома, необязательный выход).

Растр дрейфа (OUTPUT_DRIFT) в описание для RustGIS не входит: растров у
расширений нет. Класс QGIS заказывает его признаком OUTPUT_DRIFT=True,
тогда run добавляет поле dz и отдаёт сетку дрейфа в result.stats.
Генератор NumPy default_rng с зерном SEED (0 - случайно), порядок
обращений к нему тот же, что был в классе.

Значения mintype и имя разлома - слова интерфейса: в QGIS они на языке
окна, без него русские (tools/vario.word).
"""

import math
from collections import Counter

import numpy as np

from ..toolspec import N_, Result, ToolError
from .demo_fields import demo_field, demo_sample, demo_values
from .geophys_profiles import bbox
from .vario import word

SPEC_OVERRIDES = {
    "EXTENT": {"kind": "features",
               "label": "Слой, по охвату которого строить",
               "hint": "Скважины ложатся в охват объектов этого слоя."},
    "OUTPUT_DRIFT": {"_drop": True},
}

G = 200


def run(values, report):
    ext4 = bbox(values.get("EXTENT"))
    if ext4 is None or not (ext4[2] > ext4[0] and ext4[3] > ext4[1]):
        raise ToolError(N_("Не задана корректная область (экстент)."))
    xmin, ymin, xmax, ymax = ext4
    n = int(values["N_POINTS"])
    vmin, vmax = float(values["VMIN"]), float(values["VMAX"])
    if vmax <= vmin:
        raise ToolError(N_("Максимум значения должен быть больше минимума."))
    smooth = float(values["SMOOTH"])
    nug = float(values["NUGGET_FRAC"])
    seed = int(values.get("SEED") or 0)
    rng = np.random.default_rng(seed if seed > 0 else None)
    ext = (xmin, xmax, ymin, ymax)
    roof_min, roof_max = float(values["ROOF_MIN"]), float(values["ROOF_MAX"])
    thick_min = float(values["THICK_MIN"])
    thick_max = float(values["THICK_MAX"])

    w = max(1, int(round(smooth * G)))
    xs = rng.uniform(xmin, xmax, n)
    ys = rng.uniform(ymin, ymax, n)
    vals_x = demo_values(rng, G, w, xs, ys, ext, vmin, vmax, nug)
    roof = demo_values(rng, G, w, xs, ys, ext, min(roof_min, roof_max),
                       max(roof_min, roof_max), 0.05)
    thick = demo_values(rng, G, w, xs, ys, ext, min(thick_min, thick_max),
                        max(thick_min, thick_max), min(nug * 0.6, 0.5))

    want_mt = bool(values.get("MINTYPE"))
    mt = None
    if want_mt:
        repl = demo_values(rng, G, w, xs, ys, ext, 0.0, 1.0, 0.12)
        mt = np.where(repl > 0.72, word(report, N_("Каменная соль замещения")),
             np.where(repl > 0.55, word(report, N_("Частичное замещение кс")),
                      word(report, N_("Сильвинит"))))

    want_hydro = bool(values.get("HYDRO"))
    want_head = bool(values.get("HEAD")) or want_hydro
    head = None
    if want_head:
        ang = rng.uniform(0.0, 2.0 * np.pi)
        ux, uy = math.cos(ang), math.sin(ang)
        proj = (xs - xmin) * ux + (ys - ymin) * uy
        lo, hi = float(proj.min()), float(proj.max())
        proj = (proj - lo) / ((hi - lo) or 1.0)
        head = 100.0 + 20.0 * (1.0 - proj) + \
            demo_values(rng, G, w, xs, ys, ext, -2.0, 2.0, 0.05)

    k_k = k_t = None
    if want_hydro:
        kf = demo_field(rng, G, max(1, int(round(
            min(max(smooth * 1.2, 0.06), 0.5) * G))))
        z_at = demo_sample(kf, xs, ys, xmin, xmax, ymin, ymax)
        z_at = (z_at - float(z_at.mean())) / (float(z_at.std()) or 1.0)
        z_at = np.clip(z_at, -2.5, 2.5)
        k_k = np.exp(-1.8 + 1.3 * z_at)
        k_t = k_k * np.maximum(thick, 0.1)

    want_drift = bool(values.get("OUTPUT_DRIFT"))
    drift_grid = dz = None
    if want_drift:
        wd = max(1, int(round(min(max(smooth * 1.4, 0.08), 0.6) * G)))
        sfield = demo_field(rng, G, wd)
        s_lo, s_hi = -320.0, -120.0
        drift_grid = 0.5 * (s_lo + s_hi) + sfield * (0.25 * (s_hi - s_lo))
        s_at_well = demo_sample(drift_grid, xs, ys, xmin, xmax, ymin, ymax)
        local = demo_values(rng, G, w, xs, ys, ext, -4.0, 4.0, 0.12)
        dz = 30.0 + 0.9 * s_at_well + local

    fields = [("well", "string"), ("roof", "double"), ("thick", "double"),
              ("X", "double")]
    if want_head:
        fields.append(("head", "double"))
    if want_hydro:
        fields += [("K", "double"), ("T", "double")]
    if want_mt:
        fields.append(("mintype", "string"))
    if want_drift:
        fields.append(("dz", "double"))

    # Разлом: линия поперёк площади, не доходящая до краёв, и сдвиг
    # значений по одну сторону от неё.
    throw = float(values.get("THROW") or 0.0)
    fx = xmin + 0.5 * (xmax - xmin)
    fy0 = ymin + 0.12 * (ymax - ymin)
    fy1 = ymin + 0.72 * (ymax - ymin)
    res = {}
    if values.get("OUTPUT_FAULT", True) is not False:
        fault = Result([("name", "string"), ("throw", "double")],
                       geometry="line")
        fault.add({"points": np.array([[fx, fy0, np.nan],
                                       [fx, fy1, np.nan]])},
                  [word(report, N_("Разлом (демо)")), throw])
        res["OUTPUT_FAULT"] = fault
    if abs(throw) > 1e-12:
        side = (xs > fx) & (ys >= fy0) & (ys <= fy1)
        vals_x = np.asarray(vals_x, float).copy()
        vals_x[side] += throw
        report.info(N_("Разлом с амплитудой %.3g: значения сдвинуты по одну "
                       "сторону от линии. Линия не доходит до краёв площади, "
                       "и выше её конца поверхность обязана смыкаться."),
                    throw)

    wells = Result(fields, geometry="point")
    for i in range(n):
        row = ["SK-%04d" % (i + 1), float(roof[i]), float(thick[i]),
               float(vals_x[i])]
        if want_head:
            row.append(float(head[i]))
        if want_hydro:
            row += [float(k_k[i]), float(k_t[i])]
        if want_mt:
            row.append(str(mt[i]))
        if want_drift:
            row.append(float(dz[i]))
        wells.add({"xyz": np.array([float(xs[i]), float(ys[i]), np.nan])},
                  row)
    res["OUTPUT"] = wells

    rng_m = 2.0 * smooth * max(xmax - xmin, ymax - ymin)
    var = float(np.var(vals_x))
    report.info(N_("Сгенерировано скважин: %d. Поля: кровля (roof), мощность "
                   "(thick), содержание X. Дисперсия X ≈ %.4g."), n, var)
    report.info(N_("Стартовая вариограмма для X (кригинг/кросс-валидация): "
                   "суммарный силл ≈ %.4g, наггет C0 ≈ %.4g, радиус ≈ %.4g "
                   "(в единицах координат). Уточните наггет по "
                   "кросс-валидации до MSDR ≈ 1."), var, nug * var, rng_m)
    if want_mt:
        cc = Counter(mt.tolist())
        report.info("%s%s", N_("Поле mintype (демо): "),
                    ", ".join("%s=%d" % (k, v) for k, v in cc.items()))
    if want_head:
        report.info(N_("Поле напора (head): региональный уклон + локальная "
                       "вариация. Кригуйте head, затем подайте растр в "
                       "«Гидравлический градиент и направление потока»."))
    if want_hydro:
        report.info(N_("Поля K и T (демо): K лог-нормально (K ≈ %.4g…%.4g "
                       "м/сут), T = K·мощность. Для удельного расхода "
                       "создайте калькулятором поля ln(K) и ln(T), кригуйте "
                       "их, а при подаче в «Удельный расход (Дарси)» включите "
                       "галку «Растры заданы как ln». Напор (head) кригуйте "
                       "как обычно."), float(k_k.min()), float(k_k.max()))
    wells.stats = {"drift_grid": drift_grid, "G": G}
    return res
