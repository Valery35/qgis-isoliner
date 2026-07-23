# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Чтение палитры кодов Leapfrog (.lfc). Чистый разбор, без QGIS и Qt.

Формат простой и плоский:

    <LeapfrogColourPalette type="legend" version="1.0">
      <Entry><Code>АБ</Code><Colour>0.0 0.501960784314 0.0</Colour></Entry>
      ...
    </LeapfrogColourPalette>

Код это строка (индекс пласта, номер слоя, название породы), цвет - три
числа RGB от 0 до 1 через пробел. Ни штриховок, ни порядка, ни вложенности
в файле нет, поэтому и модуль отвечает ровно за одно: дать словарь
код -> «#rrggbb».

ВАЖНО о кодировке. В заголовке настоящих файлов Leapfrog стоит
`encoding='utf8'` без дефиса, и стандартный разбор по имени файла на этом
падает: такого имени кодировки разборщик не знает. Поэтому файл читается
текстом в UTF-8 и разбирается уже из строки.

ВАЖНО о разборе. Модули xml здесь не используются намеренно: чужой XML
может нести раздутые сущности и внешние ссылки, а defusedxml в поставке
QGIS нет. Формат плоский, поэтому применяется свой сканер полей Entry,
который объявления DOCTYPE и ссылки на сущности просто не понимает.

Сопоставление кода терпимое: крайние пробелы отбрасываются, регистр не
важен, для точного кода приоритет выше, чем для приведённого. Порядок
записей файла сохраняется - он пригодится легенде.
"""
import io
import os
import re


class PaletteError(Exception):
    """Файл не похож на палитру Leapfrog или не читается."""


class ReadSummary:
    """Счётчики чтения палитры. Одна сводка на файл, на экран."""

    def __init__(self):
        self.total = 0        # записей встречено
        self.kept = 0         # принято
        self.no_code = 0      # пропущено: пустой код
        self.bad_colour = 0   # пропущено: цвет не разобран
        self.dup = 0          # пропущено: повтор кода (взят первый)

    def lines(self, tr=None):
        t = tr if tr is not None else (lambda s: s)
        out = [t("Палитра: принято %d из %d записей.") % (self.kept,
                                                          self.total)]
        parts = [
            (self.no_code, "без кода: %d"),
            (self.bad_colour, "с нечитаемым цветом: %d"),
            (self.dup, "повторов кода (взят первый): %d"),
        ]
        skipped = [t(tpl) % n for n, tpl in parts if n]
        if skipped:
            out.append(t("Пропущено: %s.") % ", ".join(skipped))
        return out


def parse_colour(text):
    """«0.0 0.501 0.0» в «#rrggbb» или None.

    Три числа через пробел, как пишет Leapfrog. Запятая понимается двояко и
    решается по числу полей: если пробелы уже дают три куска, запятая
    считается десятичной («0,5»), иначе разделителем между числами.

    Шкала выбирается по наибольшему значению: все числа в пределах единицы -
    доли, иначе байты 0..255. Значения за границами зажимаются в своей
    шкале, любой мусор даёт None.
    """
    if not text:
        return None
    raw = str(text).strip()
    tokens = raw.split()
    if "," in raw and len(tokens) == 3:
        raw = raw.replace(",", ".")          # десятичная запятая
    else:
        raw = raw.replace(",", " ")          # запятая как разделитель
    nums = re.findall(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", raw)
    if len(nums) < 3:
        return None
    try:
        vals = [float(x) for x in nums[:3]]
    except ValueError:
        return None
    scale = 1.0 if max(vals) <= 1.0 else 255.0
    out = []
    for v in vals:
        v = v / scale
        v = 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)
        out.append(round(v * 255))
    return "#%02x%02x%02x" % tuple(out)


def _read_text(path):
    """Текст файла в UTF-8 с обходом заголовка `encoding='utf8'`."""
    try:
        with io.open(path, encoding="utf-8-sig") as f:
            return f.read()
    except UnicodeDecodeError:
        with io.open(path, encoding="cp1251") as f:  # nosec - запасной путь
            return f.read()
    except OSError as exc:
        raise PaletteError(str(exc))


# Разбор ведётся своим сканером, без модулей xml. Причина не в удобстве:
# стандартный разборщик уязвим к раздутым сущностям и внешним ссылкам в
# чужом файле, а тянуть в плагин defusedxml нельзя, его нет в поставке
# QGIS. Формат палитры плоский - список Entry с двумя полями, - поэтому
# своё чтение и проще, и безопаснее по построению: объявления DOCTYPE и
# ссылки на сущности не раскрываются, а просто не понимаются.

_RE_ENTRY = re.compile(r"<\s*Entry\b[^>]*>(.*?)<\s*/\s*Entry\s*>",
                       re.S | re.I)
_RE_CODE = re.compile(r"<\s*Code\b[^>]*>(.*?)<\s*/\s*Code\s*>", re.S | re.I)
_RE_COLOUR = re.compile(r"<\s*Colou?r\b[^>]*>(.*?)<\s*/\s*Colou?r\s*>",
                        re.S | re.I)
_RE_CDATA = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.S)
_RE_TAG = re.compile(r"<[^>]*>")

# только стандартные ссылки XML: собственные сущности файла не раскрываем
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
             ("&apos;", "'"), ("&#39;", "'"), ("&#34;", '"'))

# защита от заведомо неразумного входа: палитра это единицы килобайт
MAX_TEXT = 8 * 1024 * 1024


def _unescape(text):
    """Текст поля: CDATA, стандартные ссылки, обрезка."""
    out = _RE_CDATA.sub(lambda m: m.group(1), text or "")
    out = _RE_TAG.sub("", out)
    for src, dst in _ENTITIES:
        out = out.replace(src, dst)
    return out.replace("&amp;", "&").strip()


def parse_palette(text, summary=None):
    """Разбор текста палитры: (словарь код -> «#rrggbb», список кодов).

    Список кодов идёт в порядке файла: в нём геологический порядок, и он
    пригодится для легенды. Повтор кода отбрасывается, берётся первый.
    """
    s = summary if summary is not None else ReadSummary()
    if text is None:
        raise PaletteError("пустой файл")
    if len(text) > MAX_TEXT:
        raise PaletteError("файл слишком велик для палитры")
    entries = _RE_ENTRY.findall(text)
    if not entries:
        raise PaletteError("в файле нет записей Entry")
    colours, order = {}, []
    for body in entries:
        s.total += 1
        m = _RE_CODE.search(body)
        code = _unescape(m.group(1)) if m else ""
        if not code:
            s.no_code += 1
            continue
        m = _RE_COLOUR.search(body)
        col = parse_colour(_unescape(m.group(1))) if m else None
        if col is None:
            s.bad_colour += 1
            continue
        if code in colours:
            s.dup += 1
            continue
        colours[code] = col
        order.append(code)
        s.kept += 1
    if not colours:
        raise PaletteError("в файле не нашлось ни одной пригодной записи")
    return colours, order


def load_palette(path, summary=None):
    """Палитра из файла: (словарь код -> «#rrggbb», список кодов)."""
    return parse_palette(_read_text(path), summary)


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


class Palette:
    """Палитра с терпимым поиском цвета по коду.

    Поиск идёт от строгого к терпимому и останавливается на первом успехе:
    точное совпадение кода, совпадение без регистра и крайних пробелов,
    приведённый вид (снят хвост роли вроде _top, латиница сложена в
    кириллицу), и наконец тот же вид без апострофов. Ничего не нашлось -
    None, и решение остаётся за вызывающим (обычно свой детерминированный
    цвет). Такая лестница нужна именам слоёв: «KpII_top» должен находить
    код пласта «КрII», а «A'Б_top» - код «АБ».
    """

    __slots__ = ("colours", "order", "_ci", "_norm", "_loose", "_canon")

    def __init__(self, colours, order=None):
        self.colours = dict(colours)
        self.order = list(order) if order else list(colours)
        self._ci, self._norm, self._loose = {}, {}, {}
        self._canon = {}
        for code in self.order:
            col = self.colours[code]
            self._ci.setdefault(str(code).strip().lower(), col)
            self._norm.setdefault(normalize_code(code), col)
            self._loose.setdefault(loose_code(code), col)
            for key in (str(code).strip().lower(), normalize_code(code),
                        loose_code(code)):
                self._canon.setdefault(key, code)

    @classmethod
    def from_file(cls, path, summary=None):
        colours, order = load_palette(path, summary)
        return cls(colours, order)

    def get(self, code):
        """Цвет кода или None. Три шага, от строгого к терпимому: точное
        совпадение, совпадение без регистра и крайних пробелов, и наконец
        приведённый вид (без хвоста роли, латиница сложена в кириллицу).
        Последний шаг нужен именам слоёв вида «KpII_top»."""
        if code is None:
            return None
        col = self.colours.get(code)
        if col is not None:
            return col
        col = self._ci.get(str(code).strip().lower())
        if col is not None:
            return col
        col = self._norm.get(normalize_code(code))
        if col is not None:
            return col
        return self._loose.get(loose_code(code))

    def canonical(self, code):
        """Написание кода так, как оно стоит в палитре, или None.

        Нужно подписям легенды: в данных лежит «KpII_top», а на чертеже
        читатель должен видеть «КрII». Значение категории при этом не
        меняется, иначе рендерер перестанет попадать в данные.
        """
        if code is None:
            return None
        if code in self.colours:
            return code
        for key in (str(code).strip().lower(), normalize_code(code),
                    loose_code(code)):
            got = self._canon.get(key)
            if got is not None:
                return got
        return None

    def rank(self, code):
        """Позиция кода в файле или len(order), если кода нет. Нужна для
        порядка категорий в легенде: геологический порядок палитры важнее
        порядка появления в данных."""
        try:
            return self.order.index(code)
        except ValueError:
            pass
        low = str(code).strip().lower()
        for i, c in enumerate(self.order):
            if str(c).strip().lower() == low:
                return i
        norm = normalize_code(code)
        for i, c in enumerate(self.order):
            if normalize_code(c) == norm:
                return i
        loose = loose_code(code)
        for i, c in enumerate(self.order):
            if loose_code(c) == loose:
                return i
        return len(self.order)

    def __len__(self):
        return len(self.colours)

    def __contains__(self, code):
        return self.get(code) is not None


# --- запись палитры ------------------------------------------------------
#
# Обратный ход нужен, чтобы палитру можно было родить прямо в QGIS: человек
# раскрасил слой категориями, инструмент вынул пары код-цвет и записал .lfc.
# Тогда для стыковки чертежа с 3D и с чужими пакетами не нужен Leapfrog.

PALETTE_HEADER = ("<?xml version='1.0' encoding='utf-8'?>\n"
                  "<LeapfrogColourPalette type=\"legend\" version=\"1.0\">\n")
PALETTE_FOOTER = "</LeapfrogColourPalette>\n"


def _escape(text):
    """Знаки XML в тексте кода. Свои сущности не изобретаем."""
    out = str(text)
    for src, dst in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
        out = out.replace(src, dst)
    return out


def hex_to_fractions(colour):
    """«#rrggbb» в три доли 0..1 или None."""
    if not colour:
        return None
    t = str(colour).strip().lstrip("#")
    if len(t) == 3:
        t = "".join(ch * 2 for ch in t)
    if len(t) not in (6, 8):
        return None
    try:
        r = int(t[0:2], 16) / 255.0
        g = int(t[2:4], 16) / 255.0
        b = int(t[4:6], 16) / 255.0
    except ValueError:
        return None
    return r, g, b


def dump_palette(pairs):
    """Текст палитры из пар (код, «#rrggbb»).

    Порядок пар сохраняется: в нём геологический смысл. Повтор кода и
    нечитаемый цвет пропускаются, пустой список даёт PaletteError - файл
    без записей никому не нужен.
    """
    out, seen = [PALETTE_HEADER], set()
    for code, colour in pairs or ():
        code = ("" if code is None else str(code)).strip()
        fr = hex_to_fractions(colour)
        if not code or fr is None or code in seen:
            continue
        seen.add(code)
        out.append("  <Entry>\n    <Code>%s</Code>\n"
                   "    <Colour>%.12g %.12g %.12g</Colour>\n  </Entry>\n"
                   % ((_escape(code),) + fr))
    if len(out) == 1:
        raise PaletteError("нечего записывать: ни одной пригодной пары")
    out.append(PALETTE_FOOTER)
    return "".join(out)


def save_palette(path, pairs):
    """Записать палитру в файл. Возвращает число записанных пар."""
    text = dump_palette(pairs)
    with io.open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return text.count("<Entry>")


def bundled_dir():
    """Папка палитр, поставляемых с плагином."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "palettes")


def bundled_palettes():
    """Список поставляемых палитр: (имя файла, полный путь)."""
    folder = bundled_dir()
    try:
        names = sorted(n for n in os.listdir(folder)
                       if n.lower().endswith(".lfc"))
    except OSError:
        return []
    return [(n, os.path.join(folder, n)) for n in names]
