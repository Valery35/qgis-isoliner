# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Общее для кросс-валидаций 1.08 и 1.09 без QGIS.

Сборка вариограммы из полей окна или из таблицы моделей, метрики
кросс-валидации с весами и советы по ним. То же, что _build_variogram,
_variogram_from_table, _weighted_cv_metrics и _cv_advice в algorithms.py,
с теми же сообщениями слово в слово, но через отчёт toolspec.
"""

import numpy as np

from .. import variogram_table
from ..kb2d import GAUSS_MIN_NUGGET_FRAC, Variogram
from ..toolspec import N_, ToolError
from .vario import word

NSTRUCT = 1   # как в algorithms.py: структур вариограммы в окне


def variogram_from_table(values, report):
    """Вариограмма из таблицы моделей VG_TABLE, если она подана. Иначе None."""
    table = values.get("VG_TABLE")
    if table is None:
        return None
    name = (values.get("VG_NAME") or "").strip()
    rows = [dict(it.get("attrs") or {}) for it in table]
    models, notes = variogram_table.parse_rows(rows)
    for note in notes:
        report.warn(N_("Таблица вариограмм: %s"), note)
    if not models:
        raise ToolError(N_("В таблице вариограмм нет ни одной годной модели."))
    if not name:
        if len(models) > 1:
            raise ToolError(N_("В таблице несколько профилей (%s). Укажите, "
                               "какой брать."), ", ".join(sorted(models)))
        name = list(models)[0]
    if name not in models:
        raise ToolError(N_("Профиль «%s» в таблице не найден. Есть: %s."),
                        name, ", ".join(sorted(models)))
    m = models[name]
    report.info(N_("Вариограмма из таблицы, профиль «%s»: наггет %.4g, "
                   "структур %d. Поля вариограммы в окне при этом не "
                   "читаются."), m.profile, m.nugget, len(m.structs))
    return variogram_table.to_variogram(m, Variogram)


def _sk(i, suffix):
    return "S%d_%s" % (i, suffix)


def build_variogram(values, report, nugget, auto_range):
    """Вариограмма из таблицы или из полей S1_* окна."""
    from_table = variogram_from_table(values, report)
    if from_table is not None:
        return from_table
    POWER = 4
    structures = []
    sill_total = 0.0
    for i in range(1, NSTRUCT + 1):
        sill = float(values.get(_sk(i, "SILL")) or 0.0)
        if i > 1 and sill <= 0:
            continue
        model = int(values.get(_sk(i, "MODEL")) or 0) + 1
        rng = float(values.get(_sk(i, "RANGE")) or 0.0)
        if model == POWER:
            if rng <= 0:
                rng = 1.0
                report.warn(N_("Структура %d: степенная модель - поле «радиус "
                               "a» это показатель ω (0<ω<2), а не радиус. "
                               "Задан 0, взят ω=1."), i)
            elif not (0.0 < rng < 2.0):
                report.warn(N_("Структура %d: показатель степенной модели "
                               "ω=%.3g вне (0; 2) - приведён к диапазону."),
                            i, rng)
                rng = min(max(rng, 0.05), 1.999)
        elif rng <= 0:
            rng = auto_range
        az = float(values.get(_sk(i, "AZIMUTH")) or 0.0)
        anis = values.get(_sk(i, "ANIS"))
        anis = 1.0 if anis is None else float(anis)
        sill_total += max(sill, 0.0)
        structures.append({"it": model, "cc": max(sill, 0.0),
                           "aa": rng, "ang": az, "anis": anis})
    if not structures:
        structures.append({"it": 1, "cc": 0.0, "aa": auto_range,
                           "ang": 0.0, "anis": 1.0})
    if nugget > 0 and sill_total <= 0:
        report.warn(N_("Задан только наггет (структурный вклад C = 0): "
                       "кригинг выродится в локальное среднее, поверхность "
                       "будет почти плоской."))
    vg = Variogram(nugget, structures)
    if getattr(vg, "power_clamped", None):
        for num, was in vg.power_clamped:
            report.warn(N_("Структура %d: у степенной модели поле «радиус a» "
                           "это показатель ω (0 < ω < 2), а не расстояние. "
                           "Задано %.4g, взято %.3f. При показателе в метрах "
                           "ковариация переполняется, и оценка выходит "
                           "мусором."), num, was, vg.aa[num - 1])
    if vg.nugget_raised_from is not None:
        report.info(N_("Гауссова модель: наггет повышен с %.4g до %.4g для "
                       "устойчивости (минимум %g%% структурного силла)."),
                    vg.nugget_raised_from, vg.c0,
                    GAUSS_MIN_NUGGET_FRAC * 100)
    return vg


def weighted_cv_metrics(fact, est, err, var, w):
    """Метрики кросс-валидации, взвешенные декластеризацией. w=None -
    обычные. Возвращает (me, mae, rmse, msdr, r)."""
    fact = np.asarray(fact, float)
    est = np.asarray(est, float)
    err = np.asarray(err, float)
    wv = np.ones(err.shape) if w is None else np.asarray(w, float)
    W = float(np.sum(wv)) or 1.0
    me = float(np.sum(wv * err) / W)
    mae = float(np.sum(wv * np.abs(err)) / W)
    rmse = float(np.sqrt(np.sum(wv * err * err) / W))
    msdr = float("nan")
    if var is not None:
        sd = np.sqrt(np.maximum(np.asarray(var, float), 0.0))
        good = sd > 0
        if good.any():
            gw = wv[good]
            msdr = float(np.sum(gw * (err[good] / sd[good]) ** 2)
                         / (float(np.sum(gw)) or 1.0))
    with np.errstate(all="ignore"):
        mf = np.sum(wv * fact) / W
        me_ = np.sum(wv * est) / W
        cov = np.sum(wv * (fact - mf) * (est - me_)) / W
        vf = np.sum(wv * (fact - mf) ** 2) / W
        ve = np.sum(wv * (est - me_) ** 2) / W
        r = float(cov / np.sqrt(vf * ve)) if vf > 0 and ve > 0 \
            else float("nan")
    return me, mae, rmse, msdr, r


def cv_advice(report, me, mae, rmse, msdr, r):
    """Советы по метрикам: готовые строки на языке отчёта."""
    def t(template, *args):
        s = word(report, template)
        return s % args if args else s

    tips = []
    rr = rmse if rmse and rmse == rmse else 1.0
    good = True
    if msdr == msdr:
        if msdr > 1.3:
            tips.append(t(N_("MSDR заметно больше 1 (%.3g): карта стандартной "
                             "ошибки занижена. Умножьте наггет C0 и вклады C "
                             "на MSDR (радиус и модель не трогайте) и "
                             "пересчитайте - сами оценки не изменятся, "
                             "поправится только дисперсия кригинга."), msdr))
            good = False
        elif msdr < 0.7:
            tips.append(t(N_("MSDR меньше 1 (%.3g): неопределённость "
                             "завышена. Разделите наггет C0 и вклады C на "
                             "MSDR (радиус и модель не трогайте) и "
                             "пересчитайте - оценки не изменятся."), msdr))
            good = False
        else:
            tips.append(t(N_("MSDR близок к 1 (%.3g, полоса от 0.7 до 1.3): "
                             "масштаб вариограммы подобран адекватно."),
                          msdr))
    if abs(me) > 0.1 * rr:
        tips.append(t(N_("ME заметно отличается от 0 (%+.3g): возможен "
                         "систематический сдвиг - проверьте данные и тип "
                         "кригинга (для простого - заданное среднее)."), me))
        good = False
    else:
        tips.append(t(N_("ME близок к 0: систематического смещения нет.")))
    if r == r:
        if r < 0.5:
            tips.append(t(N_("Низкая корреляция (R=%.2f): модель слабо "
                             "предсказывает - попробуйте другой радиус, "
                             "модель или анизотропию. Либо это предел данных "
                             "(короткомасштабная изменчивость, зоны "
                             "замещения)."), r))
            good = False
        elif r >= 0.8:
            tips.append(t(N_("Высокая корреляция (R=%.2f): оценки хорошо "
                             "согласуются с фактом."), r))
    if good:
        tips.insert(0, t(N_("Итог: параметры можно утверждать - перенесите "
                            "ту же вариограмму и настройки поиска в «2D "
                            "Kriging».")))
    else:
        tips.insert(0, t(N_("Итог: параметры стоит подправить (см. ниже) и "
                            "повторить кросс-валидацию перед финальным "
                            "кригингом.")))
    return tips


def read_weights(report, wids, n):
    """Веса декластеризации по значениям поля или (None, False) с
    предупреждением. Второе значение - веса приняты."""
    try:
        wv = np.array([float(w) if w is not None else np.nan for w in wids],
                      float)
        if wv.size == n and np.all(np.isfinite(wv)) and np.all(wv > 0):
            return wv, True
        report.warn(N_("Поле весов содержит пустые или неположительные "
                       "значения - веса игнорируются."))
    except (TypeError, ValueError):
        report.warn(N_("Не удалось прочитать поле весов - веса "
                       "игнорируются."))
    return None, False
