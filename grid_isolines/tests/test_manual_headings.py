# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Заголовки руководства обязаны совпадать с именами в панели.

Правило принято давно: заголовок главы это префикс `displayName` слово в
слово. Проверять его было нечем, и расхождения копились молча - 2.09 в
панели стал «Вершины и ямы», а в руководстве остался «Вершины», в
английском разошлись пять глав из шестидесяти семи. Читатель ищет главу по
имени из панели и не находит её.

Исходники руководства лежат в `manual/` в корне репозитория и в плагин не
входят. При запуске тестов из установленного плагина папки нет, и проверка
пропускается: она стережёт репозиторий, а не поставку.

Имена берутся разбором AST, без запуска QGIS. Английские - через словарь
`i18n`, тем же путём, каким их получает панель при английской локали.
"""
import ast
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(PKG)
MANUAL = os.path.join(REPO, "manual")


def _display_names_ru():
    """Префикс -> русский displayName, разбором algorithms.py."""
    src = open(os.path.join(PKG, "algorithms.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    def literal(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Call) and node.args:      # tr("...")
            return literal(node.args[0])
        return None

    names = {}
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in cls.body:
            if not (isinstance(fn, ast.FunctionDef)
                    and fn.name == "displayName"):
                continue
            for st in ast.walk(fn):
                if isinstance(st, ast.Return):
                    val = literal(st.value)
                    if val and re.match(r"\d\.\d\d ", val):
                        names[val[:4]] = val
    return names


def _display_names_en(ru):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_isoliner_i18n", os.path.join(PKG, "i18n.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out, missing = {}, []
    for key, value in ru.items():
        english = mod.TRANSLATIONS.get(value)
        if english:
            out[key] = english
        else:
            missing.append(value)
    return out, missing


def _headings(path):
    text = open(path, encoding="utf-8").read()
    found = {}
    for m in re.finditer(r"^#+ (\d\.\d\d .+)$", text, re.M):
        found[m.group(1)[:4]] = m.group(1).strip()
    return found


def _check(manual_name, names):
    path = os.path.join(MANUAL, manual_name)
    if not os.path.isfile(path):
        pytest.skip("исходник руководства доступен только в репозитории")
    heads = _headings(path)
    bad = []
    for key, expected in sorted(names.items()):
        got = heads.get(key)
        if got is None:
            bad.append("%s: главы нет, ожидалось «%s»" % (key, expected))
        elif got != expected:
            bad.append("%s: в руководстве «%s», в панели «%s»"
                       % (key, got, expected))
    assert not bad, ("%s разошлось с панелью:\n" % manual_name
                     + "\n".join(bad))


def test_russian_headings_match_panel():
    _check("manual.md", _display_names_ru())


def test_english_headings_match_panel():
    english, missing = _display_names_en(_display_names_ru())
    assert not missing, "нет английского имени для: %s" % ", ".join(missing)
    _check("manual_en.md", english)
