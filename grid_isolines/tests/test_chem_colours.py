# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Раскраска колонок 4.02 по содержанию.

Колонка скважины красилась только по коду пласта. С таблицей проб
появляется вторая раскраска, по значению, и вместе с ней два решения,
которые легко потерять при правке.

Первое: при заданной химии колонка режется ПРОБАМИ, а не пластами. Это
другая нарезка. Пробу берут по керну, границы пластов проводят по
описанию, и одна полоса не может быть одновременно и тем, и другим.

Второе: проба без значения остаётся серой полосой. Градуированный
рендерер такие объекты не рисует вовсе, и полоса пропала бы с чертежа,
как будто керна там не было. Поэтому рендерер собирается правилами, и
последним стоит правило «нет данных».

Разбор идёт по исходнику, без запуска QGIS.
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)


def _text():
    with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
        return fh.read()


def _class_source(name):
    text = _text()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError("класс %s пропал из algorithms.py" % name)


def _tool():
    return _class_source("DrillholesOnSectionAlgorithm")


def test_chem_input_and_grade_field_exist():
    body = _tool()
    assert "self.CHEM" in body, "вход химии пропал"
    assert "self.GRADE" in body, "выбор поля раскраски пропал"
    assert 'self.tr("Химия (chem, таблица)")' in body


def test_chem_is_optional():
    """Без химии всё работает как раньше, это условие несломанной ветки."""
    body = _tool()
    at = body.find('self.CHEM, self.tr("Химия')
    assert at != -1
    assert "optional=True" in body[at:at + 260], (
        "вход химии стал обязательным, прежние прогоны сломаются")


def test_samples_replace_intervals_for_the_drawing():
    body = _tool()
    at = body.find("if hsrc is not None and gfield:")
    assert at != -1, "подмена интервалов пробами пропала"
    block = body[at:at + 1800]
    assert "irows = []" in block, "старые интервалы не сбрасываются"
    assert "isrc = hsrc" in block, (
        "поля выходного слоя останутся от литологии, а полосы будут от проб")
    assert "icode = None" in block, (
        "код пласта остался, и раскраска по коду поспорит с раскраской по "
        "содержанию")


def test_the_substitution_is_announced():
    body = _tool()
    assert "колонка режется пробами, а не пластами" in body, (
        "подмена нарезки проходит молча, а это не мелочь")


def test_no_data_stays_grey_and_visible():
    body = _class_source("_GradedPostProcessor")
    assert "is null" in body, (
        "правило «нет данных» пропало: полосы без значения исчезнут "
        "с чертежа")
    assert "#969696" in body, "серый цвет для «нет данных» пропал"
    assert "нет данных" in body


def test_renderer_is_built_in_code_not_in_qml():
    """Урок бергштрихов: data-defined цвет из QML на QGIS 4 не работает."""
    body = _class_source("_GradedPostProcessor")
    assert "QgsRuleBasedRenderer" in body
    assert "setRenderer" in body


def test_ramp_is_monotone_and_not_empty():
    text = _text()
    at = text.find("GRADE_RAMP = (")
    assert at != -1, "шкала раскраски пропала"
    ramp = ast.literal_eval(text[text.find("(", at):text.find(")", at) + 1])
    assert len(ramp) >= 5, "ступеней шкалы слишком мало"
    assert all(c.startswith("#") and len(c) == 7 for c in ramp)
    # густота растёт: сумма каналов падает от бледного к тёмному
    weight = [sum(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in ramp]
    assert weight == sorted(weight, reverse=True), (
        "шкала перестала быть последовательной, по цвету не понять, где "
        "богаче")


def test_attribute_colour_matches_the_screen():
    body = _tool()
    assert "_grade_colour(it) if grade" in body, (
        "в ccolor снова уходит цвет кода, а на экране шкала содержания")
    at = body.find("def _grade_colour(")
    assert at != -1
    block = body[at:at + 900]
    assert "GRADE_RAMP" in block, "цвет атрибута считается не по той шкале"
    assert '"#969696"' in block, "полоса без значения получает не серый цвет"


def test_range_goes_to_the_log():
    body = _tool()
    assert "Значения от %.4g до" in body, (
        "размах значений не печатается, и густой цвет нечем истолковать")
    assert "медиана" in body


def test_empty_grade_field_is_a_warning_not_a_crash():
    body = _tool()
    at = body.find("нет ни одного числового значения")
    assert at != -1, "пустое поле содержания больше не обрабатывается"
    assert "pushWarning" in body[max(at - 300, 0):at], (
        "пустое поле проходит молча")
