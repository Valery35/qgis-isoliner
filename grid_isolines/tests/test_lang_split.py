# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Язык интерфейса и язык справки разведены и не должны сойтись обратно.

Просьба пользователя: интерфейс QGIS у него английский, а справку он читает
по-русски. Справка это боковой текст инструмента, подсказки полей и PDF.
Сойтись языки могут тихо, одной строкой: новый инструмент с привычным
``self.tr(...)`` в shortHelpString получит справку на языке интерфейса, и
никто этого не заметит, пока пользователь не откроет именно его. Отсюда
статические проверки по исходнику, а не только по поведению.
"""
import ast
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)


def _load(name):
    spec = importlib.util.spec_from_file_location(
        "iso_" + name, os.path.join(PKG, name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _src(name):
    with open(os.path.join(PKG, name), encoding="utf-8") as fh:
        return fh.read()


def _calls(node, names):
    """Имена функций, которыми внутри узла что-то переводится."""
    found = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            nm = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else None)
            if nm in names:
                found.add(nm)
    return found


# --- поведение слоя перевода ---------------------------------------------

def test_help_language_is_independent():
    i18n = _load("i18n")
    i18n.set_language("en", help="ru")
    assert i18n.tr("Точечный слой") == "Point layer"
    assert i18n.tr_help("Точечный слой") == "Точечный слой"
    i18n.set_language("ru", help="en")
    assert i18n.tr("Точечный слой") == "Точечный слой"
    assert i18n.tr_help("Точечный слой") == "Point layer"


def test_one_call_still_switches_both():
    """Старый вызов с одним языком переключает всё, как раньше."""
    i18n = _load("i18n")
    i18n.set_language("en")
    assert i18n.language() == "en" and i18n.help_language() == "en"
    i18n.set_language("ru")
    assert i18n.language() == "ru" and i18n.help_language() == "ru"


def test_help_can_be_set_alone():
    i18n = _load("i18n")
    i18n.set_language("en")
    i18n.set_help_language("ru")
    assert i18n.language() == "en" and i18n.help_language() == "ru"


def test_init_does_not_overwrite_explicit_choice():
    """Перечитывание настроек не затирает язык, заданный явно."""
    i18n = _load("i18n")
    i18n.set_language("ru", help="ru")
    i18n.init_from_qgis()
    assert i18n.language() == "ru" and i18n.help_language() == "ru"


def test_auto_follows_qgis():
    i18n = _load("i18n")
    assert i18n.resolve("auto", "ru") == "ru"
    assert i18n.resolve("auto", "en") == "en"
    assert i18n.resolve("en", "ru") == "en"
    assert i18n.resolve("мусор", "ru") == "ru"


def test_without_qgis_choice_is_auto():
    i18n = _load("i18n")
    assert i18n.stored_choices() == ("auto", "auto")


def test_settings_keys_are_the_shared_contract():
    """Ключи общие на все двуязычные модули Информ++.

    Переименование в одном модуле молча разорвёт связь: пользователь выставит
    язык в Isoliner, а Topoliner его не увидит.
    """
    i18n = _load("i18n")
    assert i18n.SETTINGS_UI == "informpp/language_ui"
    assert i18n.SETTINGS_HELP == "informpp/language_help"
    assert i18n.CHOICES == ("auto", "ru", "en")


# --- кто каким переводом пользуется --------------------------------------

def test_every_tool_help_goes_through_help_language():
    tree = ast.parse(_src("algorithms.py"))
    bad, seen = [], 0
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == "shortHelpString":
                seen += 1
                used = _calls(fn, {"tr", "_tr", "_trh", "tr_help"})
                if "_trh" not in used or used & {"tr", "_tr"}:
                    bad.append(cls.name)
    assert seen >= 74, "справок найдено %d" % seen
    assert not bad, ("справка идёт через перевод интерфейса, а не справки: "
                     "%s" % bad)


def test_every_tool_help_carries_version():
    """Все справки кончаются версией и приглашением, 4.13 этого не делала."""
    tree = ast.parse(_src("algorithms.py"))
    bad = []
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if isinstance(fn, ast.FunctionDef) and fn.name == "shortHelpString":
                if "_help_version" not in _calls(fn, {"_help_version"}):
                    bad.append(cls.name)
    assert not bad, "справка без версии: %s" % bad


def test_parameter_hints_follow_help_language():
    tree = ast.parse(_src("algorithms.py"))
    bad, seen = [], 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "setHelp":
            seen += 1
            used = _calls(node, {"tr", "_tr", "_trh", "tr_help"})
            if "_trh" not in used:
                bad.append(node.lineno)
    assert seen >= 13
    assert not bad, "подсказка поля на языке интерфейса, строки %s" % bad


def test_shared_help_blocks_follow_help_language():
    """Общие блоки, дописываемые к справке, идут тем же языком, что и она."""
    tree = ast.parse(_src("algorithms.py"))
    want = {"_seed_help", "_sampling_help", "_demo_grid_help", "_fill_help",
            "_credit", "_help_version"}
    bad = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        if fn.name in want:
            used = _calls(fn, {"tr", "_tr", "_trh", "tr_help"})
            if used & {"tr", "_tr"} or "_trh" not in used:
                bad.append(fn.name)
    assert not bad, "общий блок справки на языке интерфейса: %s" % bad


def test_pdf_follows_help_language():
    """PDF руководства открывается на языке справки, а не интерфейса."""
    for fname, func in (("algorithms.py", "_help_url"),
                        ("about.py", "manual_path")):
        tree = ast.parse(_src(fname))
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == func][0]
        used = _calls(fn, {"language", "help_language", "_lang"})
        body = ast.get_source_segment(_src(fname), fn)
        assert "help_language" in body, "%s.%s без языка справки" % (fname,
                                                                       func)
        assert "language" not in used, ("%s.%s берёт язык интерфейса"
                                        % (fname, func))


def test_collector_sees_help_text():
    """Сборщик строк видит текст справки, иначе покрытие переводом молчит."""
    t18 = _load_test_i18n()
    keys = t18.collect_keys()
    assert any(k.startswith("Уклон в градусах и экспозиция") for k in keys)
    assert any("**Зерно ГСЧ** делает прогон" in k for k in keys)


def _load_test_i18n():
    spec = importlib.util.spec_from_file_location(
        "t18_split", os.path.join(HERE, "test_i18n.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_language_button_on_toolbar():
    """Пункт «Язык…» стоит и в меню, и на панели инструментов, с иконкой.

    Кнопку на панели Валерий попросил после первой живой проверки окна.
    """
    src = _src("plugin.py")
    assert 'QAction(icon_lang, tr("Язык…")' in src
    i = src.index('QAction(icon_lang, tr("Язык…")')
    assert "self._add(a_lang, toolbar=True)" in src[i:i + 400]
    assert os.path.exists(os.path.join(PKG, "icon_language.svg"))
