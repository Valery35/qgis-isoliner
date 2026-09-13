# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Имена `l`, `I` и `O` под запретом.

Строчная `l` неотличима от единицы, прописная `I` от неё же, а `O` от нуля.
В журнале, в присланном куске кода и в письме читатель видит не то, что
написано. Правило известное (flake8 зовёт его E741), и внешние проверки
кода о нём напоминают, но напоминают уже по факту.

Имя ловится там, где оно заводится: присваивание, аргумент, цикл, включение,
`with ... as`, `except ... as`, `import ... as`, имя функции или класса.
Использование при этом не проверяется отдельно: без заведения его не бывает.

Разбор по AST, без запуска QGIS и без внешних библиотек: проверка обязана
работать в любом окружении, иначе её однажды отключат и забудут.
"""
import ast
import os

BAD = {"l", "I", "O"}

PKG = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sources():
    for root, dirs, files in os.walk(PKG):
        dirs[:] = [d for d in dirs
                   if d not in ("__pycache__", ".pytest_cache")]
        for fn in sorted(files):
            if fn.endswith(".py"):
                path = os.path.join(root, fn)
                with open(path, encoding="utf-8") as fh:
                    yield os.path.relpath(path, PKG), fh.read()


def _bindings(tree):
    """Пары (имя, строка) для каждого места, где имя заводится."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            yield node.id, node.lineno
        elif isinstance(node, ast.arg):
            yield node.arg, node.lineno
        elif isinstance(node, ast.alias):
            name = node.asname or node.name.split(".")[0]
            yield name, getattr(node, "lineno", 0)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            yield node.name, node.lineno
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            yield node.name, node.lineno
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            for name in node.names:
                yield name, node.lineno


def test_no_ambiguous_names():
    bad = []
    for rel, code in _sources():
        for name, line in _bindings(ast.parse(code)):
            if name in BAD:
                bad.append("%s:%d имя «%s»" % (rel, line, name))
    assert not bad, (
        "имена, неотличимые от цифр, читаются неверно:\n  "
        + "\n  ".join(sorted(bad)))


def test_the_check_actually_catches_it():
    """Контроль: сторож, который ничего не ловит, хуже отсутствующего."""
    sample = ("def f(a):\n"
              "    return [l for l in a if l]\n")
    found = [n for n, _ in _bindings(ast.parse(sample)) if n in BAD]
    assert found == ["l"], found
