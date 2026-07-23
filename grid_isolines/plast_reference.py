# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Справочник пластов: порядок, тело и цвет из данных, а не из имён слоёв.

Зачем. Раньше плагин угадывал стратиграфию по именам поверхностей: снимал
хвост роли, складывал латиницу в кириллицу, разбирал пары. Работало, пока
имена аккуратны, и тихо врало, когда нет. Разрез ВКМКС показал предел
догадок: пласт АБ по коду выглядит как «А + Б», но это цельный пласт;
междупластье Б-В не выводится из кода, потому что пласта Б в списке нет,
есть АБ. Определить тело по коду нельзя - это знает только геолог.

Справочник переносит это знание в данные. Одна строка на пласт или
междупластье, сверху вниз по разрезу:

    code     код тела, как в проекте (В, АБ, КрII, Б-В, КрII-КрIII)
    order    номер сверху вниз, целое; задаёт залегание
    body     «пласт» или «междупластье» (bed / interbed); закон, не догадка
    color    цвет «#rrggbb»

Необязательные поля, под будущее развитие, читаются но пока не требуются:

    strata   толща или состав словами (СКЗ, каменная соль, глина)
    hatch    имя штриховки породы (крап); пусто, пока не заведём породы
    note     примечание

Формат. Основной GeoPackage (надёжен с кириллицей, родной для QGIS), также
читается CSV. Данные разбираются одинаково, разница только в источнике
строк, поэтому здесь чистый разбор списка словарей: слой QGIS или CSV
превращаются в него на стороне инструмента.

Имена столбцов сопоставляются терпимо (регистр, синонимы), значения тела
приводятся к bed или interbed по набору синонимов на двух языках. Плохие
строки не роняют чтение, а считаются и попадают в сводку.
"""
import io
import re


class ReferenceError(Exception):
    """Справочник не удалось прочитать или он пуст."""


# Синонимы столбцов: как поле может называться в файле -> наше имя.
# Сопоставление по приведённому виду (нижний регистр, без пробелов и
# знаков), поэтому «Код», «code», «CODE» - одно и то же.
COLUMN_SYNONYMS = {
    "code": ("code", "код", "кодтела", "индекс", "пласт", "plast"),
    "order": ("order", "порядок", "n", "номер", "нn", "сверхувниз",
              "номерсверхувниз"),
    "body": ("body", "тело", "телопластмеждупластье", "тип", "kind"),
    "color": ("color", "colour", "цвет", "цветизпалитры", "rgb", "hex"),
    "strata": ("strata", "толща", "состав", "толщасостав",
               "толщасоставзаполнить", "зона"),
    "hatch": ("hatch", "крап", "штриховка", "pattern"),
    "note": ("note", "примечание", "примечаниезаполнить", "коммент",
             "comment"),
}

REQUIRED = ("code", "order", "body")

# Значения тела -> канон «bed» / «interbed». Двуязычно и терпимо к
# написанию (междупластье и межпластье, дефисы и пробелы).
BODY_BED = ("пласт", "bed", "seam", "layer")
BODY_INTERBED = ("междупластье", "межпластье", "interbed", "interlayer",
                 "между")


def _norm_key(text):
    """Имя столбца к сопоставимому виду: нижний регистр, только буквы и
    цифры. «Толща / состав (заполнить)» -> «толщасоставзаполнить»."""
    return re.sub(r"[^0-9a-zа-яё]+", "", str(text).strip().lower())


def _norm_body(text):
    """Значение тела к «bed» / «interbed» или None."""
    t = re.sub(r"[^a-zа-яё]+", "", str(text or "").strip().lower())
    if not t:
        return None
    for kw in BODY_INTERBED:            # проверяем interbed первым:
        if t.startswith(kw):            # «междупластье» содержит «между»
            return "interbed"
    for kw in BODY_BED:
        if t.startswith(kw):
            return "bed"
    return None


def _norm_color(text):
    """Цвет к «#rrggbb» или None. Принимает #rgb, #rrggbb, с решёткой и
    без. Тройки чисел здесь не разбираем: в справочнике цвет уже hex."""
    if text is None:
        return None
    t = str(text).strip().lstrip("#").lower()
    if re.fullmatch(r"[0-9a-f]{3}", t):
        t = "".join(ch * 2 for ch in t)
    if re.fullmatch(r"[0-9a-f]{6}", t):
        return "#" + t
    return None


def map_columns(field_names):
    """Соответствие наших имён столбцам файла: {наше: имя в файле}.

    Берётся первый подходящий синоним. Столбцы, которые не распознаны,
    просто игнорируются - лишние поля в файле не мешают.
    """
    by_norm = {}
    for name in field_names:
        by_norm.setdefault(_norm_key(name), name)
    out = {}
    for ours, syns in COLUMN_SYNONYMS.items():
        for syn in syns:
            if syn in by_norm:
                out[ours] = by_norm[syn]
                break
    return out


class ReadSummary:
    """Счётчики чтения справочника. Одна сводка на файл."""

    def __init__(self):
        self.total = 0
        self.kept = 0
        self.no_code = 0
        self.bad_order = 0
        self.bad_body = 0
        self.dup = 0

    def lines(self, tr=None):
        t = tr if tr is not None else (lambda s: s)
        out = [t("Справочник: принято %d из %d строк.")
               % (self.kept, self.total)]
        parts = [
            (self.no_code, "без кода: %d"),
            (self.bad_order, "без номера порядка: %d"),
            (self.bad_body, "без вида тела: %d"),
            (self.dup, "повторов кода (взят первый): %d"),
        ]
        skipped = [t(tpl) % n for n, tpl in parts if n]
        if skipped:
            out.append(t("Пропущено: %s.") % ", ".join(skipped))
        return out


class Bed:
    """Одна запись справочника."""

    __slots__ = ("code", "order", "body", "color", "strata", "hatch", "note")

    def __init__(self, code, order, body, color=None, strata="", hatch="",
                 note=""):
        self.code = code
        self.order = order
        self.body = body            # «bed» / «interbed»
        self.color = color          # «#rrggbb» или None
        self.strata = strata
        self.hatch = hatch
        self.note = note

    @property
    def is_interbed(self):
        return self.body == "interbed"

    def __repr__(self):
        return "Bed(%r, %d, %s)" % (self.code, self.order, self.body)


def parse_rows(rows, summary=None):
    """Разбор списка словарей (по строке справочника) в список Bed.

    Каждый словарь - строка файла: ключи это имена столбцов файла. Сначала
    столбцы сопоставляются с нашими именами, затем читаются значения.
    Возвращает записи, упорядоченные по полю order.
    """
    s = summary if summary is not None else ReadSummary()
    rows = list(rows)
    if not rows:
        raise ReferenceError("в справочнике нет строк")
    colmap = map_columns(rows[0].keys())
    missing = [c for c in REQUIRED if c not in colmap]
    if missing:
        raise ReferenceError(
            "в справочнике не найдены столбцы: %s" % ", ".join(missing))

    beds, seen = [], set()
    for row in rows:
        s.total += 1
        code = str(row.get(colmap["code"], "") or "").strip()
        if not code:
            s.no_code += 1
            continue
        try:
            order = int(float(str(row.get(colmap["order"], "")).strip()))
        except (TypeError, ValueError):
            s.bad_order += 1
            continue
        body = _norm_body(row.get(colmap["body"]))
        if body is None:
            s.bad_body += 1
            continue
        if code in seen:
            s.dup += 1
            continue
        seen.add(code)
        color = _norm_color(row.get(colmap["color"])) if "color" in colmap \
            else None
        beds.append(Bed(
            code, order, body, color,
            strata=str(row.get(colmap.get("strata", ""), "") or "").strip(),
            hatch=str(row.get(colmap.get("hatch", ""), "") or "").strip(),
            note=str(row.get(colmap.get("note", ""), "") or "").strip()))
        s.kept += 1
    if not beds:
        raise ReferenceError("в справочнике не нашлось пригодных строк")
    beds.sort(key=lambda b: b.order)
    return beds


def read_csv(path, summary=None):
    """Справочник из CSV. Разделитель определяется сам, кодировка UTF-8 с
    запасом на cp1251."""
    import csv
    for enc in ("utf-8-sig", "cp1251"):
        try:
            with io.open(path, encoding=enc, newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample, ";,\t")
                except csv.Error:
                    dialect = csv.excel
                    dialect.delimiter = ";" if ";" in sample else ","
                rows = list(csv.DictReader(f, dialect=dialect))
            return parse_rows(rows, summary)
        except UnicodeDecodeError:
            continue
    raise ReferenceError("не удалось прочитать CSV (кодировка)")


# Терпимость к написанию кодов берём из модуля палитры: там уже отлажены
# снятие хвоста роли, латинские двойники букв и апострофы. Справочник и
# данные должны сходиться по коду, но апостроф в А'Б или латинская B в
# имени слоя не должны мешать - это те же коды.
from . import palette_lfc as _plf


class Reference:
    """Справочник с поиском по коду и порядку.

    Поиск терпимый той же лестницей, что была у палитры: точный код,
    без регистра и крайних пробелов. Дальше плагин полагается на код как
    он есть - справочник и данные должны говорить на одном языке кодов,
    в этом и смысл справочника.
    """

    __slots__ = ("beds", "_by_code", "_ci", "_norm", "_loose", "order")

    def __init__(self, beds):
        self.beds = list(beds)
        self.order = [b.code for b in self.beds]
        self._by_code, self._ci = {}, {}
        self._norm, self._loose = {}, {}
        for b in self.beds:
            self._by_code.setdefault(b.code, b)
            self._ci.setdefault(b.code.strip().lower(), b)
            self._norm.setdefault(_plf.normalize_code(b.code), b)
            self._loose.setdefault(_plf.loose_code(b.code), b)

    @classmethod
    def from_rows(cls, rows, summary=None):
        return cls(parse_rows(rows, summary))

    @classmethod
    def from_csv(cls, path, summary=None):
        return cls(read_csv(path, summary))

    def get(self, code):
        """Запись по коду или None. Лестница поиска, как у палитры: точный
        код, без регистра и пробелов, приведённый вид (снят хвост роли,
        латиница сложена в кириллицу), тот же вид без апострофов. Так имя
        слоя «A'Б_top» находит запись «АБ», а «KpII» находит «КрII»."""
        if code is None:
            return None
        b = self._by_code.get(code)
        if b is not None:
            return b
        b = self._ci.get(str(code).strip().lower())
        if b is not None:
            return b
        b = self._norm.get(_plf.normalize_code(code))
        if b is not None:
            return b
        return self._loose.get(_plf.loose_code(code))

    def color(self, code):
        b = self.get(code)
        return b.color if b is not None else None

    def rank(self, code):
        """Позиция кода в порядке залегания или len при отсутствии."""
        b = self.get(code)
        if b is None:
            return len(self.beds)
        return self.beds.index(b)

    def between(self, upper_code, lower_code):
        """Тело между двумя телами по залеганию: запись, «many» или None.

        Опора справочника вместо догадки по именам. Между кровлей upper и
        подошвой lower по списку стоит:
          ровно одно тело - возвращаем эту запись (обычно междупластье);
          несколько тел - возвращаем строку «many»: на разрезе показаны не
            все пласты, и что именно лежит в зазоре, из двух границ не
            понять честно;
          ничего или коды не найдены - None.
        """
        iu, il = self.rank(upper_code), self.rank(lower_code)
        if iu >= len(self.beds) or il >= len(self.beds):
            return None
        if il - iu == 2:
            return self.beds[iu + 1]
        if il - iu > 2:
            return "many"
        return None

    def span_codes(self, upper_code, lower_code):
        """Коды тел строго между upper и lower по залеганию, сверху вниз.
        Пусто, если границы не найдены или идут не по порядку."""
        iu, il = self.rank(upper_code), self.rank(lower_code)
        if iu >= len(self.beds) or il >= len(self.beds) or il <= iu:
            return []
        return [b.code for b in self.beds[iu + 1:il]]

    def __len__(self):
        return len(self.beds)

    def __contains__(self, code):
        return self.get(code) is not None
