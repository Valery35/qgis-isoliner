# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Список инструментов в AGENTS.md обязан совпадать с панелью.

AGENTS.md это первое, что читает рабочая сессия перед правкой, и список
инструментов там подробнее панели: рядом с номером стоит класс, ядро и
принятые решения. Пока список сверяли глазами, он расходился молча. К
5.13.1 накопилось: снятый инструмент 1.09 остался в списке и сдвинул
нумерацию демо на единицу, врезки 1.11 и MBA 1.12 не было вовсе, как и
2.17, 2.23 и 4.13, а класс 4.02 переименовали в коде и не переименовали
здесь. Итого список знал 66 инструментов из 71, и каждая правка по нему
начиналась с неверной карты.

Файл лежит в корне репозитория и в плагин не входит. При запуске из
установленного плагина его нет, и проверка пропускается.

Разбор идёт по исходнику, без запуска QGIS.
"""
import ast
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(PKG)
AGENTS = os.path.join(REPO, "AGENTS.md")

# Строка списка: «- 4.13 `BedGradesAtCollarsAlgorithm` (ядро) - ...»
ENTRY = re.compile(r"^- (\d\.\d\d) `(\w+)`", re.M)


def _panel():
    """Префикс displayName -> имя класса, как их видит панель."""
    src = open(os.path.join(PKG, "algorithms.py"), encoding="utf-8").read()
    out = {}
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.ClassDef):
            continue
        body = ast.get_source_segment(src, node) or ""
        m = re.search(
            r'def displayName\(self\):\s*return self\.tr\(\s*"(\d\.\d\d)', body)
        if m:
            out[m.group(1)] = node.name
    return out


def _listed():
    if not os.path.exists(AGENTS):
        pytest.skip("AGENTS.md рядом не лежит, проверять нечего")
    with open(AGENTS, encoding="utf-8") as fh:
        return dict((num, cls) for num, cls in ENTRY.findall(fh.read()))


def test_every_tool_of_the_panel_is_described():
    missing = sorted(set(_panel()) - set(_listed()))
    assert not missing, (
        "в AGENTS.md нет инструментов, которые есть в панели: %s"
        % ", ".join(missing))


def test_no_tool_lingers_after_removal():
    """Снятый инструмент уводит нумерацию соседей, это дороже пропуска."""
    extra = sorted(set(_listed()) - set(_panel()))
    assert not extra, (
        "в AGENTS.md описаны инструменты, которых в панели нет: %s"
        % ", ".join(extra))


def test_class_names_match():
    panel, listed = _panel(), _listed()
    wrong = ["%s: в списке %s, в коде %s" % (n, listed[n], panel[n])
             for n in sorted(set(panel) & set(listed))
             if listed[n] != panel[n]]
    assert not wrong, "класс назван неверно - " + "; ".join(wrong)
