# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Сопоставление кодов пластов и разбор имён поверхностей. Без QGIS и Qt.

Данные и имена слоёв редко говорят одним и тем же написанием. Слой зовётся
«KpII_top», а код в справочнике «КрII»: латинские двойники кириллицы, хвост
роли, апострофы, регистр. Модуль приводит написание к сопоставимому виду и
разбирает роль поверхности, кровля это или подошва.

Здесь только приведение кода. Цвет, порядок залегания и тело берутся из
справочника пластов (plast_reference), он же единственный источник цвета
для разрезов. Раньше эту роль играла палитра кодов Leapfrog (.lfc), её
чтение и запись жили в этом модуле - от неё остались лишь правила
сопоставления, потому что написание кодов от смены источника не изменилось.
"""


# --- приведение кода к сопоставимому виду --------------------------------

# Роль поверхности в имени слоя: кровля или подошва. Имя вида «KpII_top»
# должно находить в палитре код пласта «КрII», иначе полосы чертежа
# останутся без цвета, хотя палитра подана.
ROLE_SUFFIXES = (
    "_top", "_bottom", "_base", "_roof", "_floor",
    "_кровля", "_подошва", "_кров", "_под", "_верх", "_низ",
    "-top", "-bottom", " top", " bottom",
)
ROLE_PREFIXES = ("top_", "bottom_", "кровля_", "подошва_")

# Латинские двойники кириллических букв. В именах слоёв они встречаются
# постоянно: «B_top» набрано латиницей, а в палитре стоит кириллическое
# «В». Складываем латиницу в кириллицу только на последнем шаге поиска,
# точные совпадения от этого не страдают.
HOMOGLYPHS = {
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "x": "х", "y": "у",
}


def fold_homoglyphs(text):
    """Латинские двойники кириллицы в кириллицу."""
    return "".join(HOMOGLYPHS.get(ch, ch) for ch in str(text))


def strip_role(text):
    """Имя слоя без хвоста роли: «KpII_top» в «KpII», «top_В» в «В»."""
    t = str(text).strip()
    low = t.lower()
    for suf in ROLE_SUFFIXES:
        if low.endswith(suf) and len(t) > len(suf):
            return t[:len(t) - len(suf)].strip(" _-")
    for pre in ROLE_PREFIXES:
        if low.startswith(pre) and len(t) > len(pre):
            return t[len(pre):].strip(" _-")
    return t


# Апострофы в написании пластов ставят не всегда: слой «A\'Б_top» и код
# палитры «АБ» это один пласт. Снимаем их только на последнем шаге поиска.
APOSTROPHES = "'\u2019\u02bc\u0060\u00b4\""


ROLE_TOP = ("_top", "_\u043a\u0440\u043e\u0432\u043b\u044f", "_\u0432\u0435\u0440\u0445", "-top", " top")
ROLE_BOTTOM = ("_bottom", "_base", "_floor", "_\u043f\u043e\u0434\u043e\u0448\u0432\u0430",
               "_\u043d\u0438\u0437", "-bottom", " bottom")
ROLE_TOP_PREFIX = ("top_",)
ROLE_BOTTOM_PREFIX = ("bottom_",)


def surface_role(name):
    """\u0420\u043e\u043b\u044c \u043f\u043e\u0432\u0435\u0440\u0445\u043d\u043e\u0441\u0442\u0438 \u0438 \u0435\u0451 \u043e\u0441\u043d\u043e\u0432\u0430: (top, bottom \u0438\u043b\u0438 None, \u043e\u0441\u043d\u043e\u0432\u0430).

    KpII_top \u0434\u0430\u0451\u0442 (top, KpII), KpII_bottom \u0434\u0430\u0451\u0442 (bottom, KpII),
    \u0438\u043c\u044f \u0431\u0435\u0437 \u043f\u0440\u0438\u0437\u043d\u0430\u043a\u0430 \u0440\u043e\u043b\u0438 \u0434\u0430\u0451\u0442 (None, \u0438\u043c\u044f \u043a\u0430\u043a \u0435\u0441\u0442\u044c).
    """
    t = str(name).strip()
    low = t.lower()
    for suf in ROLE_TOP:
        if low.endswith(suf) and len(t) > len(suf):
            return "top", t[:len(t) - len(suf)].strip(" _-")
    for suf in ROLE_BOTTOM:
        if low.endswith(suf) and len(t) > len(suf):
            return "bottom", t[:len(t) - len(suf)].strip(" _-")
    for pre in ROLE_TOP_PREFIX:
        if low.startswith(pre) and len(t) > len(pre):
            return "top", t[len(pre):].strip(" _-")
    for pre in ROLE_BOTTOM_PREFIX:
        if low.startswith(pre) and len(t) > len(pre):
            return "bottom", t[len(pre):].strip(" _-")
    return None, t


def body_from_pair(top_name, bottom_name):
    """\u0422\u0435\u043b\u043e \u043c\u0435\u0436\u0434\u0443 \u0434\u0432\u0443\u043c\u044f \u043f\u043e\u0432\u0435\u0440\u0445\u043d\u043e\u0441\u0442\u044f\u043c\u0438: (\u043a\u043e\u0434, \u0432\u0438\u0434) \u0438\u043b\u0438 (None, None).

    \u041f\u0440\u0430\u0432\u0438\u043b\u043e \u0434\u0435\u0440\u0436\u0438\u0442\u0441\u044f \u043d\u0430 \u043a\u043e\u043d\u0432\u0435\u043d\u0446\u0438\u0438 \u0438\u043c\u0451\u043d \u043f\u043e\u0432\u0435\u0440\u0445\u043d\u043e\u0441\u0442\u0435\u0439:
      \u043a\u0440\u043e\u0432\u043b\u044f X_top \u0438 \u043f\u043e\u0434\u043e\u0448\u0432\u0430 X_bottom - \u044d\u0442\u043e \u043f\u043b\u0430\u0441\u0442 X, \u0432\u0438\u0434 bed;
      \u043a\u0440\u043e\u0432\u043b\u044f X_bottom \u0438 \u043f\u043e\u0434\u043e\u0448\u0432\u0430 Y_top - \u044d\u0442\u043e \u043c\u0435\u0436\u043f\u043b\u0430\u0441\u0442\u044c\u0435 X-Y, \u0432\u0438\u0434 interbed.

    \u041f\u043e\u0440\u044f\u0434\u043e\u043a \u0432 \u0441\u043e\u0441\u0442\u0430\u0432\u043d\u043e\u043c \u043a\u043e\u0434\u0435 \u0442\u043e\u0442 \u0436\u0435, \u0447\u0442\u043e \u0432 \u043f\u0430\u043b\u0438\u0442\u0440\u0430\u0445 Leapfrog: \u0432\u0435\u0440\u0445\u043d\u0438\u0439
    \u044d\u043b\u0435\u043c\u0435\u043d\u0442 \u043f\u0435\u0440\u0432\u044b\u043c. \u0415\u0441\u043b\u0438 \u043f\u0430\u0440\u0430 \u043d\u0435 \u0441\u043a\u043b\u0430\u0434\u044b\u0432\u0430\u0435\u0442\u0441\u044f \u043f\u043e \u043f\u0440\u0430\u0432\u0438\u043b\u0443, \u0432\u043e\u0437\u0432\u0440\u0430\u0449\u0430\u0435\u0442\u0441\u044f
    (None, None): \u043d\u0438\u0447\u0435\u0433\u043e \u043d\u0435 \u0434\u043e\u0434\u0443\u043c\u044b\u0432\u0430\u0435\u043c.
    """
    rt, bt = surface_role(top_name)
    rb, bb = surface_role(bottom_name)
    if rt == "top" and rb == "bottom" and \
            normalize_code(bt) == normalize_code(bb):
        return bt, "bed"
    if rt == "bottom" and rb == "top" and \
            normalize_code(bt) != normalize_code(bb):
        return "%s-%s" % (bt, bb), "interbed"
    return None, None


def normalize_code(text):
    """Код в вид для терпимого сравнения: без роли, без регистра, с
    латиницей, сложенной в кириллицу. Знаки кода (дефис, плюс) остаются:
    они различают «КрI» и «КрI-КрII»."""
    return fold_homoglyphs(strip_role(text)).strip().lower()


def loose_code(text):
    """Приведённый код без апострофов - самый терпимый вид сравнения."""
    norm = normalize_code(text)
    return "".join(ch for ch in norm if ch not in APOSTROPHES)
