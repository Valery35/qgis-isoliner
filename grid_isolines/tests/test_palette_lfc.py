# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты чтения палитры Leapfrog. Работают с настоящим кодом плагина, QGIS
# не нужен:
#     python grid_isolines/tests/test_palette_lfc.py
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import palette_lfc as pl  # noqa: E402

# Заголовок настоящих файлов Leapfrog: encoding='utf8' без дефиса.
HEAD = "<?xml version='1.0' encoding='utf8'?>\n"

SAMPLE = HEAD + """<LeapfrogColourPalette type="legend" version="1.0">
  <Entry>
    <Code>Q</Code>
    <Colour>1.0 1.0 1.0</Colour>
  </Entry>
  <Entry>
    <Code>АБ</Code>
    <Colour>0.0 0.501960784314 0.0</Colour>
  </Entry>
  <Entry>
    <Code>В</Code>
    <Colour>1.0 0.647058823529 0.0</Colour>
  </Entry>
  <Entry>
    <Code>Б-В</Code>
    <Colour>0.678431372549 0.847058823529 0.901960784314</Colour>
  </Entry>
</LeapfrogColourPalette>
"""


def _tmp(text, suffix=".lfc"):
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


# --- разбор цвета ---------------------------------------------------------

def test_parse_colour_fractions():
    assert pl.parse_colour("0.0 0.501960784314 0.0") == "#008000"
    assert pl.parse_colour("1.0 1.0 1.0") == "#ffffff"
    assert pl.parse_colour("1.0 0.647058823529 0.0") == "#ffa500"


def test_parse_colour_bytes_and_separators():
    # тройка с числом больше единицы читается как байты
    assert pl.parse_colour("255 165 0") == "#ffa500"
    assert pl.parse_colour("  1.0   1.0   0.45  ") == "#ffff73"


def test_parse_colour_comma_two_meanings():
    # три куска по пробелам - запятая десятичная
    assert pl.parse_colour("0,0 0,501960784314 0,0") == "#008000"
    # иначе запятая это разделитель между числами
    assert pl.parse_colour("255,165,0") == "#ffa500"


def test_parse_colour_bad():
    for bad in (None, "", "   ", "красный", "1.0 1.0", "a b c"):
        assert pl.parse_colour(bad) is None


def test_parse_colour_clamped_in_its_scale():
    # байтовая шкала: за границами зажимается
    assert pl.parse_colour("-5 128 300") == "#0080ff"
    # долевая шкала: всё в пределах единицы
    assert pl.parse_colour("0 0.5 0") == "#008000"
    assert pl.parse_colour("0 0.5 1") == "#0080ff"


# --- разбор файла ---------------------------------------------------------

def test_parse_palette_basic():
    s = pl.ReadSummary()
    colours, order = pl.parse_palette(SAMPLE, s)
    assert colours["Q"] == "#ffffff"
    assert colours["АБ"] == "#008000"
    assert order == ["Q", "АБ", "В", "Б-В"]     # порядок файла сохранён
    assert (s.total, s.kept) == (4, 4)
    assert s.lines() == ["Палитра: принято 4 из 4 записей."]


def test_utf8_without_hyphen_is_read_from_file():
    """Главная ловушка формата: encoding='utf8' в заголовке. Читаем файл
    именно с диска, как это будет делать инструмент."""
    path = _tmp(SAMPLE)
    try:
        p = pl.Palette.from_file(path)
        assert len(p) == 4 and p.get("АБ") == "#008000"
    finally:
        os.unlink(path)


def test_tolerant_entries_counted():
    text = HEAD + """<LeapfrogColourPalette>
  <Entry><Code> </Code><Colour>1 1 1</Colour></Entry>
  <Entry><Code>X</Code><Colour>мусор</Colour></Entry>
  <Entry><Code>АБ</Code><Colour>0 0.5 0</Colour></Entry>
  <Entry><Code>АБ</Code><Colour>1 0 0</Colour></Entry>
</LeapfrogColourPalette>"""
    s = pl.ReadSummary()
    colours, order = pl.parse_palette(text, s)
    assert colours == {"АБ": "#008000"}          # взят первый из повторов
    assert (s.total, s.kept) == (4, 1)
    assert (s.no_code, s.bad_colour, s.dup) == (1, 1, 1)
    lines = s.lines()
    assert len(lines) == 2 and "без кода: 1" in lines[1]


def test_summary_translator():
    s = pl.ReadSummary()
    pl.parse_palette(SAMPLE, s)
    seen = []
    assert s.lines(lambda t: (seen.append(t) or t)) == s.lines()
    assert "Палитра: принято %d из %d записей." in seen


def test_broken_files_raise():
    for text in ("не xml вовсе",
                 HEAD + "<LeapfrogColourPalette></LeapfrogColourPalette>"):
        try:
            pl.parse_palette(text)
        except pl.PaletteError:
            continue
        raise AssertionError("ожидалась PaletteError")


def test_all_entries_unusable_raises():
    text = HEAD + ("<LeapfrogColourPalette><Entry><Code>A</Code>"
                   "<Colour>плохо</Colour></Entry></LeapfrogColourPalette>")
    try:
        pl.parse_palette(text)
    except pl.PaletteError:
        return
    raise AssertionError("ожидалась PaletteError")


# --- поиск цвета ----------------------------------------------------------

def test_palette_lookup_tolerant():
    p = pl.Palette(*pl.parse_palette(SAMPLE))
    assert p.get("АБ") == "#008000"
    assert p.get(" АБ ") == "#008000"            # крайние пробелы
    assert p.get("аб") == "#008000"              # регистр
    assert p.get("нет такого") is None
    assert p.get(None) is None
    assert "В" in p and "Ж" not in p
    assert len(p) == 4


def test_exact_code_wins_over_case_insensitive():
    text = HEAD + """<LeapfrogColourPalette>
  <Entry><Code>AB</Code><Colour>1 0 0</Colour></Entry>
  <Entry><Code>ab</Code><Colour>0 0 1</Colour></Entry>
</LeapfrogColourPalette>"""
    p = pl.Palette(*pl.parse_palette(text))
    assert p.get("AB") == "#ff0000"
    assert p.get("ab") == "#0000ff"
    # приведённый поиск отдаёт первый по файлу
    assert p.get(" Ab ") == "#ff0000"


def test_rank_gives_file_order():
    p = pl.Palette(*pl.parse_palette(SAMPLE))
    assert p.rank("Q") == 0
    assert p.rank("Б-В") == 3
    assert p.rank(" аб ") == 1                   # терпимо
    assert p.rank("нет такого") == len(p.order)  # неизвестные в конец


# --- имена слоёв: роль, латиница, апострофы -------------------------------

def test_strip_role():
    assert pl.strip_role("KpII_top") == "KpII"
    assert pl.strip_role("B_bottom") == "B"
    assert pl.strip_role("КрII_кровля") == "КрII"
    assert pl.strip_role("top_В") == "В"
    assert pl.strip_role("АБ") == "АБ"          # без роли не трогаем
    assert pl.strip_role("_top") == "_top"      # имя из одной роли не режем


def test_fold_homoglyphs():
    # латинские B, K, p из имён слоёв в кириллицу
    assert pl.fold_homoglyphs("B") == "В"
    assert pl.fold_homoglyphs("KpII") == "КрII"
    assert pl.fold_homoglyphs("КрII") == "КрII"   # уже кириллица


def test_normalize_and_loose():
    assert pl.normalize_code("KpII_top") == "крii"
    assert pl.normalize_code(" B_bottom ") == "в"
    # дефис различает пласт и межпластье, апостроф снимается только в loose
    assert pl.normalize_code("КрI-КрII") == "крi-крii"
    assert pl.loose_code("A'Б_top") == "аб"
    assert pl.loose_code("КрI-КрII") == "крi-крii"


def test_layer_names_find_palette_codes():
    """Имена слоёв разреза должны находить коды пластов: это и есть смысл
    палитры в 4.01."""
    text = HEAD + """<LeapfrogColourPalette>
  <Entry><Code>В</Code><Colour>1 0.647058823529 0</Colour></Entry>
  <Entry><Code>АБ</Code><Colour>0 0.501960784314 0</Colour></Entry>
  <Entry><Code>КрII</Code><Colour>1 0 0</Colour></Entry>
</LeapfrogColourPalette>"""
    p = pl.Palette(*pl.parse_palette(text))
    assert p.get("B_top") == "#ffa500"        # латинское B, хвост роли
    assert p.get("B_bottom") == "#ffa500"
    assert p.get("KpII_top") == "#ff0000"     # латинские K и p
    assert p.get("A'Б_top") == "#008000"      # апостроф снят
    assert p.get("Неведомый_top") is None
    # порядок берётся тот же, что у найденного кода
    assert p.rank("KpII_top") == 2


def test_exact_code_still_wins_over_relaxed():
    """Строгие шаги идут первыми: если в палитре есть и «АБ», и «А'Б»,
    каждый получает свой цвет, а терпимый поиск не вмешивается."""
    text = HEAD + """<LeapfrogColourPalette>
  <Entry><Code>АБ</Code><Colour>0 0.5 0</Colour></Entry>
  <Entry><Code>А'Б</Code><Colour>1 0 0</Colour></Entry>
</LeapfrogColourPalette>"""
    p = pl.Palette(*pl.parse_palette(text))
    assert p.get("АБ") == "#008000"
    assert p.get("А'Б") == "#ff0000"
    assert p.get("А'Б_top") == "#ff0000"      # роль снята, апостроф цел


# --- безопасность разбора -------------------------------------------------

def test_no_xml_module_used():
    """Разбор идёт своим сканером. Модули xml запрещены: чужой XML может
    нести раздутые сущности и внешние ссылки, а defusedxml в поставке QGIS
    нет. Проверка каталога это тоже ловит."""
    src = io.open(pl.__file__.replace(".pyc", ".py"), encoding="utf-8").read()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    for bad in ("import xml", "xml.etree", "ElementTree", "minidom",
                "xml.sax", "expat"):
        assert bad not in code, "запрещённый разборщик: %s" % bad


def test_billion_laughs_not_expanded():
    """Раздутые сущности не раскрываются: сканер их просто не понимает."""
    text = (HEAD + "<!DOCTYPE lolz [<!ENTITY lol 'lol'>"
            "<!ENTITY lol2 '&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;'>]>"
            "<LeapfrogColourPalette>"
            "<Entry><Code>&lol2;</Code><Colour>1 0 0</Colour></Entry>"
            "<Entry><Code>АБ</Code><Colour>0 0.5 0</Colour></Entry>"
            "</LeapfrogColourPalette>")
    colours, order = pl.parse_palette(text)
    assert "АБ" in colours
    # ссылка осталась текстом, никакого размножения не произошло
    assert "&lol2;" in colours
    assert all(len(c) < 40 for c in colours)


def test_external_entity_is_not_fetched():
    """Внешняя ссылка остаётся текстом, файл с диска не читается."""
    text = (HEAD + "<!DOCTYPE f [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
            "<LeapfrogColourPalette>"
            "<Entry><Code>&xxe;</Code><Colour>1 1 1</Colour></Entry>"
            "</LeapfrogColourPalette>")
    colours, _ = pl.parse_palette(text)
    assert list(colours) == ["&xxe;"]
    assert "root:" not in " ".join(colours)


def test_oversized_text_rejected():
    try:
        pl.parse_palette("x" * (pl.MAX_TEXT + 1))
    except pl.PaletteError:
        return
    raise AssertionError("ожидалась PaletteError на слишком большом файле")


def test_entities_and_cdata_in_fields():
    text = HEAD + """<LeapfrogColourPalette>
  <Entry><Code>А&amp;Б</Code><Colour>0 0.5 0</Colour></Entry>
  <Entry><Code><![CDATA[КрII]]></Code><Colour>1 0 0</Colour></Entry>
</LeapfrogColourPalette>"""
    colours, order = pl.parse_palette(text)
    assert colours["А&Б"] == "#008000"
    assert colours["КрII"] == "#ff0000"


def test_attributes_and_spacing_tolerated():
    """Атрибуты в тегах и лишние пробелы не мешают."""
    text = (HEAD + "<LeapfrogColourPalette type='legend'>"
            "<Entry id='1' > <Code lang='ru' >АБ</Code >"
            "<Colour space='rgb'>0 0.5 0</Colour ></Entry >"
            "</LeapfrogColourPalette>")
    colours, _ = pl.parse_palette(text)
    assert colours == {"АБ": "#008000"}


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL %s: %s" % (name, exc))
    print("%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
