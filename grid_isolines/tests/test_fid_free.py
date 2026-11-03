# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Поле fid исходного слоя не выносится в выход под своим именем.

fid в GeoPackage - ключ таблицы. Когда инструмент пишет один исходный
объект несколько раз (4.07 на каждый разрез, части составного объекта), у
второго экземпляра тот же fid, GeoPackage его отвергает (UNIQUE constraint
failed), и объект пропадает без ошибки в окне. До 5.13.47 4.07 с тремя
разрезами записал в файл 27 объектов вместо 81.

Тест держит два правила: копия полей источника в алгоритмах идёт только
через fid_free_fields, и сама функция переименовывает fid в fid_2, не
задевая остальные поля.

Запуск:  python grid_isolines/tests/test_fid_free.py
"""
import os
import re
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)


class _Field:
    def __init__(self, other):
        self._name = other if isinstance(other, str) else other.name()

    def name(self):
        return self._name

    def setName(self, nm):
        self._name = nm


class _Fields(list):
    def append(self, f):
        list.append(self, f)


def _fid_free():
    core = types.ModuleType("qgis.core")
    core.QgsField, core.QgsFields = _Field, _Fields
    core.QgsProcessingException = Exception
    qgis = types.ModuleType("qgis")
    qgis.core = core
    saved = {k: sys.modules.get(k) for k in ("qgis", "qgis.core")}
    sys.modules.update({"qgis": qgis, "qgis.core": core})
    root = os.path.dirname(PKG)
    sys.path.insert(0, root)
    try:
        sys.modules.pop("grid_isolines.isolines", None)
        from grid_isolines import isolines
        sys.modules.pop("grid_isolines.isolines", None)
        return isolines.fid_free_fields
    finally:
        sys.path.remove(root)
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def _names(fields):
    return [f.name() for f in fields]


def test_fid_renamed_others_kept():
    ff = _fid_free()
    src = [_Field(n) for n in ("fid", "hole_id", "z")]
    assert _names(ff(src)) == ["fid_2", "hole_id", "z"]
    # исходные поля не тронуты
    assert _names(src) == ["fid", "hole_id", "z"]
    # fid_2 уже занят: берётся следующий номер
    src = [_Field(n) for n in ("FID", "fid_2", "x")]
    assert _names(ff(src)) == ["fid_3", "fid_2", "x"]
    src = [_Field(n) for n in ("hole_id", "z")]
    assert _names(ff(src)) == ["hole_id", "z"]


def test_algorithms_copy_fields_without_fid():
    bare = re.compile(r"QgsFields\(\s*\w+(\.fields\(\))?\s*\)")
    for fn in ("algorithms.py", "isolines.py"):
        text = open(os.path.join(PKG, fn), encoding="utf-8").read()
        bad = [m.group(0) for m in bare.finditer(text)]
        assert not bad, (fn, bad)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
