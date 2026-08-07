# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Smoke-тест импорта без QGIS.

Ловит ошибки времени загрузки модуля (например, использование _tr на уровне
модуля до его импорта), которые тест переводов не видит. QGIS/osgeo
подменяются заглушками, поэтому достаточно обычного Python.

Запуск:  python grid_isolines/tests/test_import.py
"""
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))

# Заглушки живут в sys.modules и переживают файл, если их не убрать. Без
# уборки подставной «osgeo» доставался тестам, идущим следом по алфавиту,
# и их пропуск по ImportError не срабатывал. Тот же приём, что в
# tests/test_algorithms_init.py.
_SAVED_MODULES = None


def setup_module(module):
    """Снимок sys.modules до первого теста файла."""
    global _SAVED_MODULES
    _SAVED_MODULES = dict(sys.modules)


def teardown_module(module):
    """Вернуть sys.modules в исходное состояние."""
    if _SAVED_MODULES is None:
        return
    for name in list(sys.modules):
        if name not in _SAVED_MODULES:
            del sys.modules[name]
    sys.modules.update(_SAVED_MODULES)

PKG = os.path.dirname(HERE)
ROOT = os.path.dirname(PKG)


class _StubMod(types.ModuleType):
    """Любой запрошенный атрибут - реальный класс (годится как базовый)."""
    def __getattr__(self, name):
        cls = type(name, (object,), {})
        setattr(self, name, cls)
        return cls


def main():
    for modname in ["qgis", "qgis.core", "qgis.gui", "qgis.PyQt",
                    "qgis.PyQt.QtCore", "qgis.PyQt.QtGui",
                    "qgis.PyQt.QtWidgets", "osgeo", "osgeo.gdal",
                    "osgeo.ogr", "processing", "processing.gui",
                    "processing.gui.wrappers"]:
        sys.modules.setdefault(modname, _StubMod(modname))

    sys.path.insert(0, ROOT)
    mods = ["grid_isolines.i18n", "grid_isolines.kb2d",
            "grid_isolines.isolines", "grid_isolines.widgets",
            "grid_isolines.algorithms", "grid_isolines.provider"]
    for m in mods:
        importlib.import_module(m)
        print("[ok] import", m)
    print("ALL IMPORTS OK")


def test_modules_import_under_pytest():
    """Обёртка для pytest, см. пояснение в tests/test_i18n.py."""
    main()


if __name__ == "__main__":
    main()
