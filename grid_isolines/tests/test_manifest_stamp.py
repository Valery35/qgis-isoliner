# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Поверхность, сделанная сборкой пачки, знает свою роль сама.

5.03 выкладывает поверхности тел в папку файлами вида «01_КОД.tif» и
больше о них ничего не сообщала. 5.05 размечала проект по именам слоёв, а
на имя «01_КОД» не ложится ни одна подсказка, и собственные поверхности
модуля оставались нераспознанными.

Теперь 5.03 пишет роль в метаданные самого файла, а 5.05 читает её
раньше догадки по имени. Клеймо едет вместе с файлом: слой можно
переименовать и перенести в другой проект, роль останется при нём.
Манифест проекта по-прежнему главнее обоих - решение человека не
перебивается ни файлом, ни догадкой.

Проверка идёт без QGIS и без gdal: чтение подменяется заглушкой.
"""
import ast
import os
import sys
import types
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(PKG))

from grid_isolines import manifest  # noqa: E402


class _Dataset(object):
    def __init__(self, meta):
        self._meta = meta
        self.opened_with = None

    def GetMetadataItem(self, key):
        return self._meta.get(key)


def _fake_gdal(meta, seen=None):
    """Подставной osgeo.gdal: Open возвращает набор метаданных."""
    gdal = types.ModuleType("osgeo.gdal")

    def _open(path):
        if seen is not None:
            seen.append(path)
        return _Dataset(meta) if meta is not None else None

    gdal.Open = _open
    osgeo = types.ModuleType("osgeo")
    osgeo.gdal = gdal
    return {"osgeo": osgeo, "osgeo.gdal": gdal}


def test_role_is_read_from_the_file():
    with mock.patch.dict(sys.modules, _fake_gdal(
            {manifest.META_ROLE: manifest.ROLE_CONTACT})):
        assert manifest.role_from_source("/data/01_sylvite.tif") \
            == manifest.ROLE_CONTACT


def test_qgis_source_tail_is_cut_off():
    """QGIS добавляет к источнику хвост после «|», файла с ним нет."""
    seen = []
    with mock.patch.dict(sys.modules, _fake_gdal(
            {manifest.META_ROLE: manifest.ROLE_CONTACT}, seen)):
        manifest.role_from_source("/data/01_sylvite.tif|layername=x")
    assert seen == ["/data/01_sylvite.tif"]


def test_file_without_stamp_gives_nothing():
    with mock.patch.dict(sys.modules, _fake_gdal({})):
        assert manifest.role_from_source("/data/чужой.tif") is None


def test_unreadable_file_gives_nothing_not_an_error():
    """Чужой файл не обязан открываться, и разметка должна идти дальше."""
    with mock.patch.dict(sys.modules, _fake_gdal(None)):
        assert manifest.role_from_source("/data/нет такого.tif") is None


def test_empty_source_gives_nothing():
    assert manifest.role_from_source("") is None
    assert manifest.role_from_source(None) is None


def test_name_hints_still_miss_the_assembly_naming():
    """Контроль: имя «01_КОД» подсказками не распознаётся и сейчас.

    Если этот тест когда-нибудь упадёт, значит подсказку по имени
    расширили, и клеймо в файле перестало быть единственным способом
    узнать свою же поверхность. Тогда решать надо заново, а не молча.
    """
    assert manifest.guess_role("01_sylvite") is None
    assert manifest.guess_role("02_карналлит") is None


def test_assembly_stamps_the_file():
    """5.03 обязана ставить клеймо там же, где пишет грид."""
    with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == "StackBuildAlgorithm":
            body = ast.get_source_segment(text, node) or ""
            break
    else:
        raise AssertionError("StackBuildAlgorithm пропала из algorithms.py")
    at = body.find("def _emit(")
    assert at != -1, "запись поверхности в файл пропала"
    block = body[at:at + 1600]
    assert "manifest.META_ROLE" in block, (
        "5.03 снова выкладывает поверхности без роли, и 5.05 их не узнает")
    assert "manifest.ROLE_CONTACT" in block


def test_manifest_tool_reads_the_stamp_before_guessing_by_name():
    with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == "ManifestAlgorithm":
            body = ast.get_source_segment(text, node) or ""
            break
    else:
        raise AssertionError("инструмент манифеста пропал из algorithms.py")
    from_file = body.find("role_from_source(")
    by_name = body.find("guess_role(")
    from_manifest = body.find("old.get(lid)")
    assert from_file != -1, "5.05 не читает роль из файла поверхности"
    assert from_manifest < from_file < by_name, (
        "порядок источников роли нарушен: манифест, потом файл, потом имя")
