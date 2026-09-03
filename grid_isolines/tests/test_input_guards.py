# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Неподходящий вход отвечает текстом, а не трейсбеком Python.

Развёртка стенки ствола (4.09) брала первую геометрию слоя оси и звала
asPoint(). Линейный слой в этом поле ронял расчёт исключением Qt с
английским текстом про LineString вместо объяснения, что нужна точка
устья. Через окно параметров линию не подать, ограничение типа стоит,
но вызов из модели или из скрипта его не проходит.

Проверка идёт по исходнику разбором AST, без запуска QGIS.
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "algorithms.py")


def _class_source(name):
    with open(SRC, encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError("класс %s пропал из algorithms.py" % name)


def test_shaft_axis_checks_geometry_type():
    body = _class_source("ShaftUnwrapAlgorithm")
    at = body.find("for ft in asrc.getFeatures():")
    assert at != -1, "обход слоя оси пропал"
    block = body[at:at + 900]
    call = block.find("asPoint()")
    assert call != -1, "разбор точки устья пропал"
    guard = block.find("PointGeometry")
    assert guard != -1, (
        "4.09 больше не проверяет тип геометрии оси: линейный слой снова "
        "уронит расчёт трейсбеком")
    assert guard < call, "проверка типа стоит после asPoint()"


def test_section_vectors_reserve_service_fid():
    """Служебный fid исходного слоя не должен ехать на разрез своим именем.

    В GeoPackage колонка fid это первичный ключ. Перенесённая под тем же
    именем, она сталкивается с ключом выходного слоя, и запись падает на
    UNIQUE constraint failed. Инструмент при этом рапортовал успех, а
    полосы в файл не ложились.
    """
    body = _class_source("SectionVectorIntersectAlgorithm")
    start = body.find("reserved = (")
    assert start != -1, "список зарезервированных имён пропал"
    tail = body[start:body.find(")", start) + 1]
    assert '"fid"' in tail, (
        "fid снова не зарезервирован: служебный ключ поедет на разрез "
        "своим именем и уронит запись в GeoPackage")
