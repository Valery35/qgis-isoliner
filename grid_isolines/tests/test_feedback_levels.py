# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Уровень сообщения должен отвечать его смыслу.

Два места разъехались в разные стороны. Сообщение о том, что заполнение
понижений не сошлось, шло обычной строкой сводки и терялось среди чисел.
Подсказка врезки поверхностей про отсутствующую полосу перекрытия,
наоборот, шла через reportError и краснела в журнале как ошибка, хотя
расчёт при этом успешно заканчивался.

Проверка идёт по исходнику разбором AST, без запуска QGIS.
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)


def _text(name):
    with open(os.path.join(PKG, name), encoding="utf-8") as fh:
        return fh.read()


def _func(text, name):
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError("функция %s пропала" % name)


def test_fill_limit_goes_as_warning():
    """Предел проходов это предупреждение, а не строка сводки."""
    text = _text("hydro_fill.py")
    src = ast.get_source_segment(text, _func(text, "fill_depressions"))
    assert "Достигнут предел" in src, "сообщение о пределе пропало"
    _, marker, tail = src.partition("if not converged")
    assert marker, "ветка «не сошлось» пропала"
    # ищем сам вызов, а не слово: слово стоит и в комментарии рядом
    assert 'getattr(feedback, "pushWarning"' in tail, (
        "сообщение о недостигнутой сходимости больше не идёт "
        "предупреждением")


def test_graft_overlap_note_is_not_an_error():
    """Подсказка врезки не должна краснеть в журнале как ошибка."""
    text = _text("algorithms.py")
    key = 'rep.get("warning")'
    at = text.find(key)
    assert at != -1, "проверка подсказки врезки пропала"
    tail = text[at:at + 400]
    assert "reportError" not in tail, (
        "подсказка врезки снова идёт через reportError")
    assert "pushWarning" in tail, "подсказка врезки идёт не предупреждением"
