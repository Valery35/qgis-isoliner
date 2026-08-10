# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Таблица моделей вариограмм: разбор строк.

Зачем таблица. Подобранная вариограмма это знание геолога о пласте, а не
настройка программы. Пока она лежала профилем в реестре QGIS, она не
уезжала с проектом, не попадала в репозиторий, не переносилась между
машинами и ни к чему не была привязана внутри проекта: имя это всё, что
у неё было. В таблице она становится данными.

Строка на структуру, а не на профиль. Вложенные структуры сейчас
недоступны, и широкая таблица закрепила бы это ограничение навсегда.

    profile   имя набора, по нему подставляется в расчёт
    field     поле значения, к которому подобрана модель
    struct    номер структуры, целое от 1
    model     модель: сферическая, экспоненциальная, гауссова, степенная
    sill      вклад C структуры
    range     радиус корреляции a
    azimuth   азимут главной оси, градусы
    anis      анизотропия, малая ось на главную
    nugget    наггет C0, один на профиль, повторяется в каждой строке
    val_pct   перцентиль обрезки ураганных проб, один на профиль
    val_cap   срезка к границе вместо удаления, один на профиль
    fitted    дата подбора
    author    кто подобрал
    note      примечание: чем получено, что смущает, что проверено

Терпимость намеренно узкая. Таблицу пишет наш же инструмент, имена
столбцов наши, поэтому лестницы синонимов, как в справочнике пластов,
здесь не нужно: там таблицу составляет геолог с нуля и как умеет.

Но правка руками - половина смысла затеи, а значит кто-то откроет
таблицу в Excel, сохранит CSV и получит запятую вместо точки или лишние
пробелы. Поэтому регистр столбцов игнорируется, а числа читаются мягко.
Синонимы имён - нет.
"""

MODEL_CODES = {
    "сферическая": 1, "spherical": 1, "sph": 1, "1": 1,
    "экспоненциальная": 2, "exponential": 2, "exp": 2, "2": 2,
    "гауссова": 3, "gaussian": 3, "gau": 3, "3": 3,
    "степенная": 4, "power": 4, "pow": 4, "4": 4,
}

REQUIRED = ("profile", "struct", "model", "sill", "range")
KNOWN = REQUIRED + ("field", "azimuth", "anis", "nugget", "val_pct",
                    "val_cap", "fitted", "author", "note")

# Поля, общие на весь профиль: расхождение внутри профиля это ошибка
# ввода, и о ней надо сказать, а не молча взять первую строку.
PER_PROFILE = ("nugget", "val_pct", "val_cap")


class TableError(Exception):
    """Таблица непригодна целиком."""


def _num(value, default=None):
    """Мягкий разбор числа: запятая как точка, пробелы, пустое."""
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return default
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return default


def _flag(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if not text:
        return default
    return text in ("1", "true", "да", "yes", "y", "истина", "t")


def _model_code(value):
    if value is None:
        return None
    key = str(value).strip().lower()
    if not key:
        return None
    return MODEL_CODES.get(key)


def normalise_keys(row):
    """Ключи строки к нижнему регистру, без хвостовых пробелов."""
    return {str(k).strip().lower(): v for k, v in row.items()}


class Model(object):
    """Одна вариограмма: наггет, структуры и настройки отсева."""

    def __init__(self, profile):
        self.profile = profile
        self.field = ""
        self.nugget = 0.0
        self.val_pct = 0.0
        self.val_cap = False
        self.fitted = ""
        self.author = ""
        self.note = ""
        self.structs = []          # список словарей it, cc, aa, ang, anis

    def __repr__(self):
        return "<Model %s: наггет %.4g, структур %d>" % (
            self.profile, self.nugget, len(self.structs))


def parse_rows(rows):
    """Строки таблицы -> ({имя профиля: Model}, список замечаний).

    Негодная строка не роняет разбор: она пропускается, а причина
    попадает в замечания. Профиль без единой годной структуры в результат
    не попадает.
    """
    notes = []
    drafts = {}
    for n, raw in enumerate(rows, 1):
        row = normalise_keys(raw)
        name = str(row.get("profile", "") or "").strip()
        if not name:
            notes.append("строка %d: пустое имя профиля" % n)
            continue
        code = _model_code(row.get("model"))
        if code is None:
            notes.append("строка %d (%s): модель не распознана: %r"
                         % (n, name, row.get("model")))
            continue
        sill = _num(row.get("sill"))
        rng = _num(row.get("range"))
        if sill is None or sill < 0:
            notes.append("строка %d (%s): вклад C не число или отрицателен"
                         % (n, name))
            continue
        if rng is None or rng <= 0:
            notes.append("строка %d (%s): радиус корреляции должен быть "
                         "больше нуля" % (n, name))
            continue
        struct = int(_num(row.get("struct"), 1) or 1)
        anis = _num(row.get("anis"), 1.0)
        if anis is None or anis <= 0:
            notes.append("строка %d (%s): анизотропия должна быть больше "
                         "нуля, взята единица" % (n, name))
            anis = 1.0
        m = drafts.get(name)
        if m is None:
            m = drafts[name] = Model(name)
            m.field = str(row.get("field", "") or "").strip()
            m.nugget = _num(row.get("nugget"), 0.0) or 0.0
            m.val_pct = _num(row.get("val_pct"), 0.0) or 0.0
            m.val_cap = _flag(row.get("val_cap"))
            m.fitted = str(row.get("fitted", "") or "").strip()
            m.author = str(row.get("author", "") or "").strip()
            m.note = str(row.get("note", "") or "").strip()
            m._seen = {}
        else:
            for key, got, want in (
                    ("nugget", _num(row.get("nugget"), m.nugget), m.nugget),
                    ("val_pct", _num(row.get("val_pct"), m.val_pct),
                     m.val_pct)):
                if got is not None and abs(float(got) - float(want)) > 1e-9:
                    notes.append(
                        "строка %d (%s): %s отличается от заданного в первой "
                        "строке профиля (%.4g против %.4g), взято первое"
                        % (n, name, key, got, want))
        if struct in m._seen:
            notes.append("строка %d (%s): структура %d уже была, строка "
                         "пропущена" % (n, name, struct))
            continue
        m._seen[struct] = True
        m.structs.append(dict(it=code, cc=float(sill), aa=float(rng),
                              ang=float(_num(row.get("azimuth"), 0.0) or 0.0),
                              anis=float(anis), struct=struct))

    out = {}
    for name, m in drafts.items():
        if not m.structs:
            notes.append("профиль %s: ни одной годной структуры" % name)
            continue
        m.structs.sort(key=lambda s: s["struct"])
        del m._seen
        out[name] = m
    return out, notes


def to_variogram(model, variogram_cls):
    """Model -> объект вариограммы ядра."""
    return variogram_cls(model.nugget,
                         [dict(it=s["it"], cc=s["cc"], aa=s["aa"],
                               ang=s["ang"], anis=s["anis"])
                          for s in model.structs])


def rows_from_model(model):
    """Model -> строки таблицы. Обратная операция, для записи."""
    out = []
    for s in model.structs:
        out.append({
            "profile": model.profile, "field": model.field,
            "struct": s["struct"], "model": s["it"],
            "sill": s["cc"], "range": s["aa"],
            "azimuth": s["ang"], "anis": s["anis"],
            "nugget": model.nugget, "val_pct": model.val_pct,
            "val_cap": model.val_cap, "fitted": model.fitted,
            "author": model.author, "note": model.note,
        })
    return out
