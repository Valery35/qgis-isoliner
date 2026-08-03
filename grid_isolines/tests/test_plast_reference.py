# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты чтения справочника пластов. QGIS не нужен:
#     python grid_isolines/tests/test_plast_reference.py
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import plast_reference as pr  # noqa: E402


# Небольшой кусок реального справочника ВКМКС, как он приходит из
# GeoConstructor/Excel: с человеческими именами столбцов.
ROWS = [
    {"№ (сверху вниз)": 1, "Код": "В", "Тело (пласт / междупластье)": "пласт",
     "Цвет (из палитры)": "#ffa500"},
    {"№ (сверху вниз)": 2, "Код": "Б-В",
     "Тело (пласт / междупластье)": "междупластье", "Цвет (из палитры)": "#add8e6"},
    {"№ (сверху вниз)": 3, "Код": "АБ", "Тело (пласт / междупластье)": "пласт",
     "Цвет (из палитры)": "#008000"},
    {"№ (сверху вниз)": 4, "Код": "А'-КрI",
     "Тело (пласт / междупластье)": "междупластье", "Цвет (из палитры)": "#add8e6"},
    {"№ (сверху вниз)": 5, "Код": "КрI", "Тело (пласт / междупластье)": "пласт",
     "Цвет (из палитры)": "#ffc0cb"},
]


# --- сопоставление столбцов ----------------------------------------------

def test_map_columns_synonyms():
    m = pr.map_columns(["№ (сверху вниз)", "Код",
                        "Тело (пласт / междупластье)", "Цвет (из палитры)"])
    assert m["order"] == "№ (сверху вниз)"
    assert m["code"] == "Код"
    assert m["body"] == "Тело (пласт / междупластье)"
    assert m["color"] == "Цвет (из палитры)"


def test_map_columns_english_and_extra():
    m = pr.map_columns(["code", "ORDER", "Body", "хлам", "note"])
    assert m["code"] == "code" and m["order"] == "ORDER"
    assert m["body"] == "Body" and m["note"] == "note"
    assert "color" not in m               # не было - и нет


# --- канон тела -----------------------------------------------------------

def test_norm_body_two_languages():
    assert pr._norm_body("пласт") == "bed"
    assert pr._norm_body("Пласт") == "bed"
    assert pr._norm_body("bed") == "bed"
    assert pr._norm_body("междупластье") == "interbed"
    assert pr._norm_body("межпластье") == "interbed"     # оба написания
    assert pr._norm_body("interbed") == "interbed"
    assert pr._norm_body("") is None
    assert pr._norm_body("ерунда") is None


def test_norm_color():
    assert pr._norm_color("#ffa500") == "#ffa500"
    assert pr._norm_color("FFA500") == "#ffa500"
    assert pr._norm_color("#0f0") == "#00ff00"
    assert pr._norm_color("нет") is None
    assert pr._norm_color(None) is None


# --- разбор строк ---------------------------------------------------------

def test_parse_rows_basic():
    s = pr.ReadSummary()
    beds = pr.parse_rows(ROWS, s)
    assert [b.code for b in beds] == ["В", "Б-В", "АБ", "А'-КрI", "КрI"]
    assert beds[0].body == "bed" and beds[1].body == "interbed"
    assert beds[2].color == "#008000"
    assert (s.total, s.kept) == (5, 5)
    assert s.lines() == ["Справочник: принято 5 из 5 строк."]


def test_parse_rows_sorts_by_order():
    rows = [
        {"code": "низ", "order": 3, "body": "пласт"},
        {"code": "верх", "order": 1, "body": "пласт"},
        {"code": "середина", "order": 2, "body": "междупластье"},
    ]
    beds = pr.parse_rows(rows)
    assert [b.code for b in beds] == ["верх", "середина", "низ"]


def test_parse_rows_counts_bad():
    rows = [
        {"code": "", "order": 1, "body": "пласт"},          # без кода
        {"code": "A", "order": "ху", "body": "пласт"},      # плохой порядок
        {"code": "B", "order": 2, "body": "мусор"},         # плохое тело
        {"code": "C", "order": 3, "body": "пласт"},
        {"code": "C", "order": 4, "body": "пласт"},         # повтор
    ]
    s = pr.ReadSummary()
    beds = pr.parse_rows(rows, s)
    assert [b.code for b in beds] == ["C"]
    assert (s.no_code, s.bad_order, s.bad_body, s.dup) == (1, 1, 1, 1)
    assert len(s.lines()) == 2


def test_missing_required_columns():
    for rows in ([{"code": "A", "body": "пласт"}],         # нет order
                 [{"order": 1, "body": "пласт"}]):         # нет code
        try:
            pr.parse_rows(rows)
        except pr.ReferenceError:
            continue
        raise AssertionError("ожидалась ReferenceError")


def test_empty_raises():
    try:
        pr.parse_rows([])
    except pr.ReferenceError:
        return
    raise AssertionError("ожидалась ReferenceError")


# --- CSV ------------------------------------------------------------------

def test_read_csv_semicolon_utf8():
    text = ("Код;№ (сверху вниз);Тело (пласт / междупластье);Цвет (из палитры)\n"
            "В;1;пласт;#ffa500\n"
            "Б-В;2;междупластье;#add8e6\n")
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    try:
        ref = pr.Reference.from_csv(path)
        assert len(ref) == 2
        assert ref.get("В").color == "#ffa500"
        assert ref.get("Б-В").is_interbed
    finally:
        os.unlink(path)


# --- поиск и порядок ------------------------------------------------------

def test_reference_lookup():
    ref = pr.Reference(pr.parse_rows(ROWS))
    assert ref.get("АБ").color == "#008000"
    assert ref.get(" аб ").color == "#008000"      # регистр и пробелы
    assert ref.color("КрI") == "#ffc0cb"
    assert ref.get("нет") is None
    assert "В" in ref and "Ж" not in ref
    assert len(ref) == 5


def test_reference_rank_is_geological():
    ref = pr.Reference(pr.parse_rows(ROWS))
    assert ref.rank("В") == 0
    assert ref.rank("КрI") == 4
    assert ref.rank("нет") == len(ref)


def test_between_finds_interbed():
    """Тело между двумя соседями берётся из порядка, а не из имён: между В
    и АБ по списку стоит Б-В."""
    ref = pr.Reference(pr.parse_rows(ROWS))
    body = ref.between("В", "АБ")
    assert body is not None and body.code == "Б-В"
    assert body.is_interbed
    # между несоседними (пропущены пласты) - «many», в неверном порядке
    # или при отсутствии кода - None
    assert ref.between("В", "КрI") == "many"
    assert ref.between("АБ", "В") is None      # снизу вверх
    assert ref.between("В", "нет") is None


# --- терпимость к написанию (апострофы, латиница) -------------------------

def test_lookup_tolerant_apostrophe_and_latin():
    """Имя слоя может отличаться от кода написанием: A'Б_top это АБ,
    латинское B это В. Справочник сводит их той же лестницей, что палитра."""
    ref = pr.Reference(pr.parse_rows(ROWS))
    assert ref.get("A'Б").code == "АБ"       # апостроф
    assert ref.get("аб").code == "АБ"        # регистр
    assert ref.get("B").code == "В"          # латинское B -> В


# --- несколько тел между границами (пропущенные пласты) -------------------

FULL = [
    {"code": "АБ", "order": 23, "body": "пласт"},
    {"code": "А'-КрI", "order": 24, "body": "междупластье"},
    {"code": "КрI", "order": 25, "body": "пласт"},
    {"code": "КрI-КрII", "order": 26, "body": "междупластье"},
    {"code": "КрII", "order": 27, "body": "пласт"},
]


def test_between_returns_many_when_beds_skipped():
    """Между АБ и КрII по справочнику лежат три тела: на разрезе показаны
    не все пласты, и between честно говорит «many»."""
    ref = pr.Reference(pr.parse_rows(FULL))
    assert ref.between("АБ", "КрII") == "many"
    assert ref.span_codes("АБ", "КрII") == ["А'-КрI", "КрI", "КрI-КрII"]


def test_between_single_still_returns_record():
    ref = pr.Reference(pr.parse_rows(FULL))
    b = ref.between("КрI", "КрII")
    assert b is not None and b != "many" and b.code == "КрI-КрII"


def test_span_codes_empty_when_bad_order():
    ref = pr.Reference(pr.parse_rows(FULL))
    assert ref.span_codes("КрII", "АБ") == []      # снизу вверх
    assert ref.span_codes("АБ", "нет") == []


def test_demo_reference_is_readable():
    """Справочник, который пишет 5.02, читается разрезами.

    Столбцы те же, что ставит демо-пачка. На прогоне 4.01 отказался от
    справочника целиком, потому что вида тела в нём не было, и разрез
    строился без цветов и имён.
    """
    rows = [{"code": "B", "order": 1, "body": "пласт", "color": "#9fb59a"},
            {"code": "AB", "order": 2, "body": "пласт", "color": "#c0504d"},
            {"code": "KpII", "order": 3, "body": "пласт", "color": "#8fa88b"}]
    beds = pr.parse_rows(rows)
    assert [b.code for b in beds] == ["B", "AB", "KpII"]
    assert all(not b.is_interbed for b in beds)
    assert beds[0].color is not None


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
