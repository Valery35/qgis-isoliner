# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Общее для расчётов вариограмм без QGIS: сообщения о данных и подборе.

То же, что _warn_data, _warn_fit_quality и _report_nugget_pairs в
algorithms.py, но через отчёт toolspec. Шаблоны сообщений те же слово в
слово, поэтому журнал в QGIS не меняется.
"""

import numpy as np

from ..kb2d import data_warnings, nugget_pairs
from ..toolspec import N_

# Названия моделей подбора по коду с нуля, как MODEL_LABELS в algorithms.py
MODEL_NAMES = (N_("Сферическая"), N_("Экспоненциальная"), N_("Гауссова"),
               N_("Степенная"))


def word(report, text):
    """Слово, вставленное внутрь собранного аргумента: «(пусто)».

    Отчёт переводит шаблон и целые аргументы. Слово внутри списка
    («A (40), (пусто) (12)») он не видит, поэтому отчёт QGIS даёт метод
    translate, а без него слово остаётся русским, как в образцах."""
    tr = getattr(report, "translate", None)
    return tr(text) if tr is not None else text


def warn_data(report, xs, ys, vs):
    for code, val in data_warnings(xs, ys, vs):
        if code == "few_points":
            report.warn(N_("Мало точек (%d): оценка кригинга и вариограммы "
                           "неустойчива."), val)
        elif code == "duplicates":
            report.warn(N_("Точек с совпадающими координатами: %d. Частая "
                           "причина вырожденной матрицы и артефактов. Уберите "
                           "дубли или усредните пробы в одной точке."), val)
        elif code == "constant":
            report.warn(N_("Все значения одинаковы. Кригинг вырождается, "
                           "вариограмма нулевая. Проверьте выбранное поле."))


def warn_fit_quality(report, fit, r2_min=0.1, nug_max=0.5):
    if not fit:
        return
    try:
        r2 = float(fit.get("r2", 0.0))
        c0 = float(fit.get("nugget", 0.0))
        total = c0 + float(fit.get("sill", 0.0))
    except (TypeError, ValueError):
        return
    if r2 < float(r2_min):
        report.warn(N_("Качество подгонки R²=%.3f ниже %.2g: модель почти "
                       "ничего не объясняет. Пользоваться её параметрами как "
                       "есть нельзя - проверьте выбросы и парные близкие "
                       "точки."), r2, r2_min)
    if total > 0.0 and c0 / total > float(nug_max):
        report.warn(N_("Доля наггета %.0f%% от суммарного порога. Связь на "
                       "коротких расстояниях не разрешена. Кригинг по такой "
                       "модели сглаживает оценку до среднего, а на карте "
                       "дают «бычьи глаза»."), 100.0 * c0 / total)


def report_nugget_pairs(report, xs, ys, vs, ev, data_var, top=5):
    def _arr(key):
        raw = ev.get(key)
        return np.asarray([] if raw is None else raw, dtype=float)

    lag, gam, npr = _arr("lag"), _arr("gamma"), _arr("npairs")
    if not len(lag) or not len(gam):
        return
    h1, g1 = float(lag[0]), float(gam[0])
    n1 = int(npr[0]) if len(npr) else 0
    report.info(N_("Первый шаг: h=%.4g, пар %d, γ=%.4g при дисперсии данных "
                   "%.4g."), h1, n1, g1, data_var)
    if data_var > 0.0 and g1 > data_var:
        report.warn(N_("На первом шаге разброс уже выше общей дисперсии. "
                       "Описать это можно только наггетом, и кригинг после "
                       "такого подбора будет возвращать среднее вместо "
                       "карты."))
    try:
        pairs = nugget_pairs(xs, ys, vs, h1, top=int(top))
    except (ValueError, TypeError, IndexError, MemoryError):
        return
    if not pairs:
        return
    report.info(N_("Пары, формирующие наггет (внутри первого шага):"))
    for _i, _j, dist, vi, vj, g in pairs:
        report.info(N_("  расстояние %.4g, значения %.4g и %.4g, вклад "
                       "γ=%.4g (x %.6g y %.6g / x %.6g y %.6g)"),
                    dist, vi, vj, g, float(xs[_i]), float(ys[_i]),
                    float(xs[_j]), float(ys[_j]))
