# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Расчёты инструментов без QGIS: run(значения, отчёт) по контракту
toolspec. Класс QGIS в algorithms.py переводит слои в данные для run и
пишет результат в слои, описание для tools.json строится из того же
класса (tools/export_tools.py в корне репозитория).

RUNS - (имя инструмента, модуль расчёта, класс QGIS). Порядок задаёт
порядок в tools.json.
"""

RUNS = (
    ("declustering", "declustering", "DeclusteringAlgorithm"),
)


def by_name(name):
    """Модуль расчёта по имени инструмента."""
    import importlib
    for tool, mod, _cls in RUNS:
        if tool == name:
            return importlib.import_module("." + mod, __name__)
    raise KeyError(name)
