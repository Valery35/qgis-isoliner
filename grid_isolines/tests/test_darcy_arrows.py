# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Слой стрелок 3.05 должен говорить, что в нём лежит.

В слое векторов Дарси было два поля: «az» и «grad». Число в «grad» было
верное, это расход, а не градиент, но по таблице атрибутов это не
читалось никак. Хуже другое: в одном поле оказывались две разные
величины. Скорость фильтрации меряется в м/сут и получается при заданном
растре K, расход через ширину меряется в м²/сут и получается при
заданном T. Какая из них попала в слой, сказать было нечем, и два
запуска на одной площади давали внешне одинаковые слои с
несопоставимыми числами.

Отдельно: оба растра необязательные, а проверки, что задан хотя бы один,
не было. Запуск с одним напором ронял расчёт трейсбеком на None.

Проверка идёт разбором исходника и стиля, без запуска QGIS.
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)


def _class_source(name):
    with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError("класс %s пропал из algorithms.py" % name)


def _fields_block():
    body = _class_source("DarcyFluxAlgorithm")
    at = body.find("fields.append(QgsField(\"az\"")
    assert at != -1, "объявление полей слоя стрелок пропало"
    return body[at:at + 600]


def test_the_value_field_is_named_q():
    block = _fields_block()
    assert '"q"' in block, (
        "поле расхода снова называется не «q»: по таблице атрибутов не "
        "отличить расход от градиента")
    assert '"grad"' not in block, (
        "старое имя «grad» вернулось, а в нём лежит расход")


def test_the_kind_and_units_are_carried_with_the_value():
    block = _fields_block()
    for name in ('"kind"', '"units"'):
        assert name in block, (
            "поле %s пропало: в «q» оказываются и м/сут, и м²/сут, и "
            "различить их нечем" % name)


def test_both_kinds_are_labelled_at_their_source():
    """Вид величины проставляется там же, где выбирается сама величина."""
    body = _class_source("DarcyFluxAlgorithm")
    assert '"flux", "m/day"' in body, "скорость фильтрации не помечается"
    assert '"flux_width", "m2/day"' in body, "расход через ширину не помечается"


def test_labels_are_not_translated():
    """Значения полей это данные, а не надпись в окне.

    Перевод сделал бы проект нечитаемым при другом языке интерфейса.
    """
    body = _class_source("DarcyFluxAlgorithm")
    for bad in ('self.tr("flux")', '_tr("flux")', 'self.tr("m/day")',
                '_tr("m/day")'):
        assert bad not in body, "значение поля переводится: %s" % bad


def test_missing_k_and_t_is_refused_with_text():
    body = _class_source("DarcyFluxAlgorithm")
    at = body.find("if kgrid is None and tgrid is None:")
    assert at != -1, (
        "проверка «не задан ни K, ни T» пропала: расчёт снова упадёт "
        "трейсбеком на None")
    tail = body[at:at + 700]
    assert "QgsProcessingException" in tail, "отказ идёт не текстом"
    assert "Гидравлический градиент" in tail, (
        "в отказе не сказано, чем считать один градиент напора")


def test_refusal_stands_before_the_arrows_are_built():
    body = _class_source("DarcyFluxAlgorithm")
    guard = body.find("if kgrid is None and tgrid is None:")
    arrows = body.find("hydro.flow_samples(")
    assert guard != -1 and arrows != -1
    assert guard < arrows, "проверка стоит после сборки стрелок"


def test_darcy_has_its_own_style():
    """У 3.05 свой стиль, общий остаётся за 3.04.

    Стрелки рисуют два инструмента. У гидравлического градиента поле
    действительно называется «grad» и действительно держит градиент, и
    его стиль трогать нельзя: перевод общего стиля на «q» оставил бы
    градиентные стрелки без размера вовсе.
    """
    common = os.path.join(PKG, "styles", "flow_arrows.qml")
    own = os.path.join(PKG, "styles", "flow_arrows_q.qml")
    assert os.path.exists(own), "стиль стрелок расхода пропал"
    with open(common, encoding="utf-8") as fh:
        base = fh.read()
    with open(own, encoding="utf-8") as fh:
        qml = fh.read()
    assert "scale_linear(&quot;grad&quot;" in base, (
        "общий стиль перестал масштабировать по «grad», а 3.04 других полей "
        "не отдаёт")
    assert "scale_linear(&quot;q&quot;" in qml, (
        "стиль расхода перестал масштабировать размер по «q»")
    assert "grad" not in qml, "в стиле расхода осталась ссылка на «grad»"
    assert "&quot;az&quot;" in qml, "поворот стрелки по азимуту пропал"


def test_the_two_tools_use_different_styles():
    body = _class_source("DarcyFluxAlgorithm")
    assert '_style_path("flow_arrows_q")' in body, (
        "3.05 снова цепляет общий стиль, а поля «grad» у неё нет")
    grad_tool = _class_source("FlowGradientAlgorithm")
    assert '_style_path("flow_arrows")' in grad_tool, (
        "3.04 перестала цеплять общий стиль")


def test_log_names_the_quantity_and_the_unit():
    body = _class_source("DarcyFluxAlgorithm")
    assert "скорость фильтрации q, м/сут" in body
    assert "расход через ширину Q, м²/сут" in body
    at = body.find("Векторов потока: %d")
    assert at != -1, "сообщение о векторах потока пропало"
    assert "«q»" in body[at:at + 400], (
        "журнал не называет поле, по которому масштабируется стрелка")
