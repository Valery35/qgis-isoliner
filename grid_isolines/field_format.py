# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Разумное количество знаков в числовых полях выходных слоёв.

Без QGIS, чтобы проверяться обычным тестом.

Точность выбирается по подписи поля (FIELD_LABELS), то есть по единицам:
отметка в метрах пишется до сантиметра, наклон в мм/м до тысячной, угол
до десятой градуса. Двенадцать знаков после запятой у отметки кровли
точности не прибавляют, а таблицу делают нечитаемой, и в Excel такие
числа уходят как есть.

Спецификация точности:
    int  - знаков после запятой;
    "gN" - N значащих цифр, для величин без естественного масштаба
           (коэффициент фильтрации от тысячных до единиц, силл, вес);
    None - поле не трогать.
"""
import math
import re

# Порядок важен: первое совпадение решает. Составные единицы стоят раньше
# простых (мм/м раньше мм, м³/с раньше м³), доли раньше процентов.
_RULES = (
    (r"10⁻⁶ 1/м", 2),
    (r"мм/м\b", 3),
    (r"мм/год", 1),
    (r", мм\b", 1),
    (r"м/сут|м²/сут", "g4"),
    (r"м³/с", 3),
    (r"м/с\b", 3),
    (r"км²", 3),
    (r", км\b|, км$", 3),
    (r"м³", 1),
    (r"м²", 1),
    (r"Ом·м", 2),
    (r"мВ/В", 2),
    (r"мВ\b", 1),
    (r"доля|Вероятност|R²|Плоскостность|Шероховатость", 4),
    (r"градус|°", 1),
    (r"%", 2),
    (r"м/м|[Уу]клон$|Градиент|Удельный расход|перепад на ячейку", "g4"),
    (r", м\b|, м$", 2),
    (r"Размерность", 3),
    (r"^Вес", "g4"),
    (r"Станд\. остаток", 3),
    (r"Полувариограмма|Силл|Наггет|Радиус влияния|Лаг", "g5"),
    (r"Анизотропия|Вертикальное преувеличение", 3),
    (r"Масса", "g6"),
    (r"координата z", 4),
    (r"Амплитуда", 2),
    (r"Значение для дрейфа", 3),
    (r"^X$|^Y$|Сдвиг чертежа", 2),
    (r"Зазор", 3),
    (r"^Площадь сечения$", 2),
    (r"^Пикет", 3),
    (r"Среднее значение", "g5"),
)


def decimals_for(label):
    """Точность по подписи поля или None, если единиц в подписи нет."""
    if not label:
        return None
    for pat, spec in _RULES:
        if re.search(pat, label):
            return spec
    return None


def round_value(value, spec):
    """Округлить число по спецификации. Не число, пустое, бесконечность и
    NaN возвращаются как есть: округление не должно ни ронять запись, ни
    превращать пропуск в ноль."""
    if spec is None or isinstance(value, bool):
        return value
    if not isinstance(value, float):
        return value
    if not math.isfinite(value):
        return value
    if isinstance(spec, int):
        return round(value, spec) + 0.0
    if isinstance(spec, str) and spec.startswith("g"):
        n = int(spec[1:])
        if value == 0.0:
            return 0.0
        return float("%.*g" % (n, value)) + 0.0
    return value


def plan_for(names, labels, overrides=None, skip=()):
    """Точность по именам полей: {имя: спецификация}.

    names - имена полей приёмника, labels - FIELD_LABELS инструмента,
    overrides - FIELD_DECIMALS (явная точность сильнее подписи),
    skip - поля входа, их модуль не округляет: это данные пользователя."""
    overrides = overrides or {}
    out = {}
    for nm in names:
        if nm in skip:
            continue
        if nm in overrides:
            spec = overrides[nm]
        else:
            label = labels.get(nm)
            if label is None:
                for key, lab in labels.items():
                    if key.endswith("*") and nm.startswith(key[:-1]):
                        label = lab
                        break
            spec = decimals_for(label)
        if spec is not None:
            out[nm] = spec
    return out
