# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Таблица моделей вариограмм: разбор строк.

Вариограмма переезжает из реестра QGIS в обычный табличный слой. Половина
смысла затеи в том, что таблицу можно править руками, а значит её откроют
в Excel и сохранят CSV. Отсюда мягкий разбор чисел и безразличие к
регистру столбцов.

Терпимость при этом узкая намеренно: таблицу пишет наш же инструмент, и
лестницы синонимов, как в справочнике пластов, здесь не нужно.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import variogram_table as vt  # noqa: E402


def _row(**kw):
    row = dict(profile="П", struct=1, model="Сферическая", sill=10.0,
               range=500.0)
    row.update(kw)
    return row


def test_plain_row_gives_a_model():
    models, notes = vt.parse_rows([_row()])
    assert not notes and list(models) == ["П"]
    s = models["П"].structs[0]
    assert (s["it"], s["cc"], s["aa"]) == (1, 10.0, 500.0)


def test_numbers_survive_excel():
    """Запятая как разделитель и пробел в разряде не роняют строку.

    Таблицу правят руками, и путь через Excel неизбежен.
    """
    models, notes = vt.parse_rows([_row(sill="27,37", range="10 000",
                                        nugget="2,589")])
    m = models["П"]
    assert abs(m.structs[0]["cc"] - 27.37) < 1e-9
    assert abs(m.structs[0]["aa"] - 10000.0) < 1e-9
    assert abs(m.nugget - 2.589) < 1e-9
    assert not notes


def test_column_case_does_not_matter():
    models, _ = vt.parse_rows([{"PROFILE": "П", "Struct": 1,
                                "MODEL": "sph", "Sill": 1.0,
                                "RANGE": 100.0}])
    assert list(models) == ["П"]


def test_model_names_in_both_languages_and_by_code():
    for value, code in (("Сферическая", 1), ("spherical", 1), ("1", 1),
                        ("Экспоненциальная", 2), ("exp", 2),
                        ("Гауссова", 3), ("Степенная", 4)):
        models, _ = vt.parse_rows([_row(model=value)])
        assert models["П"].structs[0]["it"] == code, value


def test_unknown_model_is_named_not_guessed():
    models, notes = vt.parse_rows([_row(model="неведомая")])
    assert not models
    assert any("модель не распознана" in x for x in notes)


def test_zero_range_is_rejected():
    """Радиус корреляции ноль это не вариограмма, а ошибка ввода."""
    models, notes = vt.parse_rows([_row(range=0)])
    assert not models and any("радиус" in x for x in notes)


def test_nested_structures_are_kept_in_order():
    models, notes = vt.parse_rows([
        _row(struct=2, model="exp", sill=5.0, range=3000.0),
        _row(struct=1, model="sph", sill=10.0, range=500.0)])
    assert not notes
    got = [s["struct"] for s in models["П"].structs]
    assert got == [1, 2], "структуры не упорядочены"


def test_duplicate_structure_is_reported_and_skipped():
    models, notes = vt.parse_rows([_row(struct=1), _row(struct=1, sill=99.0)])
    assert len(models["П"].structs) == 1
    assert any("уже была" in x for x in notes)


def test_per_profile_field_mismatch_is_reported():
    """Наггет один на профиль: расхождение это ошибка ввода.

    Молча взять первую строку значило бы посчитать по числу, которого
    человек не видел.
    """
    models, notes = vt.parse_rows([_row(struct=1, nugget=2.0),
                                   _row(struct=2, nugget=5.0)])
    assert any("nugget" in x and "отличается" in x for x in notes)
    assert abs(models["П"].nugget - 2.0) < 1e-9, "взято не первое значение"


def test_row_without_a_profile_name_is_skipped():
    models, notes = vt.parse_rows([_row(profile="  ")])
    assert not models and any("пустое имя" in x for x in notes)


def test_bad_row_does_not_take_the_whole_table_down():
    """Негодная строка пропускается, годные считаются."""
    models, notes = vt.parse_rows([_row(profile="хороший"),
                                   _row(profile="плохой", model="???"),
                                   _row(profile="второй", model="exp")])
    assert sorted(models) == ["второй", "хороший"]
    assert len(notes) == 1


def test_flags_are_read_in_both_languages():
    for value in (True, "да", "1", "true", "Yes"):
        models, _ = vt.parse_rows([_row(val_cap=value)])
        assert models["П"].val_cap is True, value
    for value in (False, "нет", "0", "", None):
        models, _ = vt.parse_rows([_row(val_cap=value)])
        assert models["П"].val_cap is False, value


def test_round_trip_through_rows():
    """Модель, записанная строками и прочитанная обратно, та же самая."""
    models, _ = vt.parse_rows([
        _row(struct=1, sill=10.0, range=500.0, azimuth=35.0, anis=0.6,
             nugget=2.5, val_pct=1.0, val_cap="да"),
        _row(struct=2, model="exp", sill=5.0, range=3000.0, nugget=2.5,
             val_pct=1.0, val_cap="да")])
    back, notes = vt.parse_rows(vt.rows_from_model(models["П"]))
    assert not notes
    a, b = models["П"], back["П"]
    assert a.structs == b.structs
    assert (a.nugget, a.val_pct, a.val_cap) == (b.nugget, b.val_pct, b.val_cap)


# --- запись из 1.05 и круг «записал - прочитал» ----------------------------

def test_written_model_is_read_back_identically():
    """Модель, записанная 1.05, читается 1.02 без потерь.

    Проверка стыка: пишет один инструмент, читает другой, и формат у них
    один. Расхождение здесь означало бы, что подобранная вариограмма
    молча теряется по дороге.
    """
    models, _ = vt.parse_rows([_row(profile="Кр2", struct=1,
                                    model="Сферическая", sill=27.37,
                                    range=10000.0, azimuth=35.0, anis=0.6,
                                    nugget=2.589, val_pct=1.0,
                                    val_cap="да")])
    rows = vt.rows_from_model(models["Кр2"])
    assert len(rows) == 1
    keys = set(rows[0])
    assert {"profile", "field", "struct", "model", "sill", "range",
            "azimuth", "anis", "nugget", "val_pct", "val_cap"} <= keys
    back, notes = vt.parse_rows(rows)
    assert not notes
    assert back["Кр2"].structs == models["Кр2"].structs
    assert abs(back["Кр2"].nugget - 2.589) < 1e-9
    assert back["Кр2"].val_cap is True


def test_model_column_written_as_code_is_read_back():
    """Модель пишется кодом, а читается и кодом, и словом."""
    rows = vt.rows_from_model(vt.parse_rows([_row(model="Гауссова")])[0]["П"])
    assert rows[0]["model"] == 3
    back, notes = vt.parse_rows(rows)
    assert not notes and back["П"].structs[0]["it"] == 3


def test_to_variogram_builds_the_core_object():
    """Собранная модель превращается в объект ядра."""
    class FakeVariogram(object):
        def __init__(self, c0, structs):
            self.c0 = c0
            self.structs = structs

    models, _ = vt.parse_rows([_row(nugget=2.0, sill=10.0, range=500.0)])
    vg = vt.to_variogram(models["П"], FakeVariogram)
    assert abs(vg.c0 - 2.0) < 1e-9
    assert vg.structs[0]["cc"] == 10.0 and vg.structs[0]["aa"] == 500.0
    assert "struct" not in vg.structs[0], "служебный номер уехал в ядро"


# --- куда пишется подобранная модель ---------------------------------------

def _load_choice():
    """Достаём решающую функцию без импорта QGIS."""
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "algorithms.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "choose_model_sink":
            mod = ast.Module(body=[node], type_ignores=[])
            ns = {}
            exec(compile(mod, "<choice>", "exec"), ns)
            return ns["choose_model_sink"]
    raise AssertionError("choose_model_sink не найдена")


CHOOSE = _load_choice()


def test_target_table_wins_and_no_new_layer_is_made():
    """Задана таблица - пишем в неё, нового слоя не создаём вовсе.

    Именно на этом решении я ошибался трижды, и каждый прогон плодил
    слой. Правило вынесено в отдельную функцию, чтобы его можно было
    проверить без QGIS.
    """
    assert CHOOSE(has_target=True, has_output=True) == "target"
    assert CHOOSE(has_target=True, has_output=False) == "target"


def test_without_target_a_new_layer_is_made():
    assert CHOOSE(has_target=False, has_output=True) == "sink"


def test_when_there_is_nowhere_to_write_it_is_said_aloud():
    """Ни таблицы, ни выхода - не пишем, но говорим об этом.

    Прежде функция уходила молча в первой же строке: человек подбирал
    модель и не видел её нигде, а почему - не догадывался.
    """
    assert CHOOSE(has_target=False, has_output=False) == "nowhere"


def test_write_path_warns_when_nowhere():
    """В коде записи ветка «некуда» действительно предупреждает."""
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "algorithms.py"), encoding="utf-8") as fh:
        src = fh.read()
    i = src.index("def _write_model_table")
    seg = src[i:src.index("\ndef ", i + 10)]
    assert 'where == "nowhere"' in seg
    j = seg.index('where == "nowhere"')
    tail = seg[j:j + 400]
    assert "pushWarning" in tail, "ветка «некуда» молчит"
    assert seg.index("pushWarning") < seg.index("parameterAsSink"), (
        "предупреждение стоит после запроса приёмника")


def _load_builder():
    """Сборка модели из результата подбора, без импорта QGIS."""
    import ast
    import os
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "algorithms.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    body = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "build_model_from_fit"]
    assert body, "build_model_from_fit не найдена"
    ns = {"variogram_table": vt, "_tr": lambda s: s}
    exec(compile(ast.Module(body=body, type_ignores=[]), "<b>", "exec"), ns)
    return ns["build_model_from_fit"]


BUILD = _load_builder()


def test_fit_becomes_a_model_with_the_right_numbers():
    """Результат автоподбора ложится в модель без искажений.

    Числа взяты из живого прогона: гауссова модель, C0=117.8, C=2042,
    a=3518. Код модели в подборе нумеруется с нуля, а в таблице с
    единицы, и именно на таком сдвиге легко ошибиться молча.
    """
    fit = {"nugget": 117.8, "model": 2, "sill": 2042.0, "range": 3518.0}
    m = BUILD("Кр2", fit, "roof", val_pct=1.0, val_cap=True,
              today="2026-08-10")
    assert m.profile == "Кр2" and m.field == "roof"
    assert abs(m.nugget - 117.8) < 1e-9
    s = m.structs[0]
    assert s["it"] == 3, "гауссова модель уехала в другой код"
    assert abs(s["cc"] - 2042.0) < 1e-9 and abs(s["aa"] - 3518.0) < 1e-9
    assert s["ang"] == 0.0 and s["anis"] == 1.0, "подбор изотропный"
    assert m.fitted == "2026-08-10"
    assert m.note, "примечание пустое"


def test_built_model_survives_the_round_trip():
    fit = {"nugget": 2.5, "model": 0, "sill": 10.0, "range": 500.0}
    m = BUILD("П", fit, "thick", val_pct=0.0, val_cap=False,
              today="2026-08-10")
    back, notes = vt.parse_rows(vt.rows_from_model(m))
    assert not notes and back["П"].structs == m.structs


def test_empty_name_does_not_produce_a_nameless_row():
    """Пустое имя профиля заменяется, а не уходит пустым в таблицу.

    Строка без имени профиля потом не читается: разбор её пропустит и
    скажет «пустое имя профиля».
    """
    fit = {"nugget": 1.0, "model": 0, "sill": 5.0, "range": 100.0}
    m = BUILD("   ", fit, "roof", today="2026-08-10")
    assert m.profile.strip(), "имя профиля осталось пустым"
    back, notes = vt.parse_rows(vt.rows_from_model(m))
    assert back and not notes
