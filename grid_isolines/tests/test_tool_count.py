# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Число инструментов в тексте руководства обязано быть одно.

Дерево инструментов собирается из кода генератором и всегда верно. А рядом
в тексте живут фразы вида «инструментов в модуле столько-то», написанные
руками. Они гниют молча: за последние выпуски в приложении «за 15 минут»
осталось 48 при 71, а на странице быстрого старта 70. Читатель считает
такую цифру обещанием и по ней решает, стоит ли вообще открывать список.

Правило простое: цифру называет только генератор. В остальном тексте её
нет вовсе, потому что любая записанная руками устареет на следующем
инструменте.

Исходники руководства лежат в `manual/` в корне репозитория и в плагин не
входят. При запуске из установленного плагина папки нет, и проверка
пропускается: она стережёт репозиторий, а не поставку.
"""
import ast
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(PKG)
MANUAL = os.path.join(REPO, "manual")

# Строка генератора, единственное место, где цифра уместна.
GENERATED = (re.compile(r"_Всего инструментов: (\d+)_"),
             re.compile(r"_Tools in total: (\d+)_"))

# Цифра, приписанная к слову «инструмент» где угодно ещё.
HANDWRITTEN = (
    re.compile(r"нструментов в модуле (\d+)"),
    re.compile(r"(\d+) инструмент(?:ов|а)?\b"),
    re.compile(r"holds (\d+) tools"),
    re.compile(r"(\d+) tools\b"),
)

PAGES = ("manual.md", "manual_en.md", "quickstart_ru.md", "quickstart_en.md")


def _registered():
    """Сколько инструментов зарегистрировано, разбором AST."""
    src = open(os.path.join(PKG, "algorithms.py"), encoding="utf-8").read()
    for node in ast.parse(src).body:
        if (isinstance(node, ast.Assign)
                and getattr(node.targets[0], "id", "") == "ALGORITHMS"):
            return len(node.value.elts)
    raise AssertionError("список ALGORITHMS пропал из algorithms.py")


def _page(name):
    path = os.path.join(MANUAL, name)
    if not os.path.exists(path):
        pytest.skip("руководство рядом не лежит, проверять нечего")
    return open(path, encoding="utf-8").read()


def test_generated_tree_matches_the_panel():
    """Дерево собрано генератором, но собрать его могли и давно."""
    n = _registered()
    for name, rx in (("manual.md", GENERATED[0]),
                     ("manual_en.md", GENERATED[1])):
        text = _page(name)
        m = rx.search(text)
        assert m, "итоговая строка дерева пропала из %s" % name
        assert int(m.group(1)) == n, (
            "%s: дерево собрано при %s инструментах, сейчас их %d - "
            "перегенерировать manual/gen_tree.py" % (name, m.group(1), n))


@pytest.mark.parametrize("name", PAGES)
def test_no_handwritten_count_anywhere_else(name):
    text = _page(name)
    for line in text.split("\n"):
        if GENERATED[0].search(line) or GENERATED[1].search(line):
            continue
        for rx in HANDWRITTEN:
            m = rx.search(line)
            assert not m, (
                "%s: число инструментов записано руками (%s). Цифру называет "
                "только дерево из генератора, в тексте её быть не должно:\n%s"
                % (name, m.group(1), line.strip()))
