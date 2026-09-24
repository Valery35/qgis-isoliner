# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Псевдонимы полей демо-слоёв (FIELD_LABELS), без QGIS.

Имена полей остаются латиницей: на них ссылаются выражения, подписи и
стили, и проект, собранный в русском QGIS, обязан открываться в
английском. Русское название поле получает псевдонимом слоя, английское
в английском интерфейсе. Тест держит четыре вещи.

1. У каждого демо-инструмента есть FIELD_LABELS.
2. У каждой подписи есть английский перевод, иначе в английском QGIS
   поле покажется по-русски.
3. Базовый класс вешает подписи на выходы после расчёта.
4. Все постпроцессоры доносят подписи до слоя. Постпроцессор у выхода
   один, и тот, что подписи теряет, оставил бы слой с голыми именами.

Запуск:  python grid_isolines/tests/test_field_labels.py
"""
import ast
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)
import i18n  # noqa: E402

SRC = os.path.join(PKG, "algorithms.py")
with open(SRC, encoding="utf-8") as f:
    CODE = f.read()
TREE = ast.parse(CODE)


def _classes():
    for node in TREE.body:
        if isinstance(node, ast.ClassDef):
            yield node


def _display_name(cls):
    for f in cls.body:
        if isinstance(f, ast.FunctionDef) and f.name == "displayName":
            for c in ast.walk(f):
                if isinstance(c, ast.Constant) and isinstance(c.value, str):
                    return c.value
    return None


def _field_labels(cls):
    for f in cls.body:
        if isinstance(f, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "FIELD_LABELS"
                for t in f.targets):
            return ast.literal_eval(f.value)
    return None


def _demo_classes():
    for cls in _classes():
        name = _display_name(cls) or ""
        if "(демо)" in name or "Демо-рельеф" in name:
            if cls.name == "LandXmlDemoAlgorithm":
                continue      # пишет файлы LandXML, слоёв не выдаёт
            yield cls


def test_every_demo_has_labels():
    missing = [c.name for c in _demo_classes() if not _field_labels(c)]
    assert not missing, "демо без FIELD_LABELS: %s" % missing


def test_labels_are_translated():
    bad = []
    for cls in _classes():
        labels = _field_labels(cls) or {}
        for field, label in labels.items():
            assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*\*?$", field), field
            # поле с переменным хвостом подставляет хвост в подпись
            assert field.endswith("*") == ("%s" in label), field
            assert field != "fid", cls.name
            if label not in i18n.TRANSLATIONS:
                bad.append("%s.%s: %s" % (cls.name, field, label))
    assert not bad, "нет перевода подписи поля: %s" % bad


def test_base_class_attaches_labels():
    body = CODE[CODE.index("class IsolinerAlgorithm("):]
    body = body[:body.index("\nclass ", 10)]
    flat = " ".join(body.split())
    assert "_attach_field_labels( self, parameters, context, result" in flat


def test_postprocessors_carry_labels():
    # каждый вызов _finalize_layer из постпроцессора передаёт подписи
    for m in re.finditer(r"\n\s+_finalize_layer\(layer, ([^\n]*)", CODE):
        tail = CODE[m.start():m.start() + 200]
        assert "field_labels" in tail, tail[:120]
    # постпроцессоры без _finalize_layer ставят подписи сами
    for cls in ("_CollapseNodePostProcessor", "_OrderInGroupPostProcessor"):
        body = CODE[CODE.index("class %s(" % cls):]
        body = body[:body.index("\nclass ", 10)] if "\nclass " in body[10:] \
            else body
        assert "_apply_field_labels(layer" in body, cls


def test_user_fields_are_protected():
    """Поле с именем, как у входного слоя, подпись не получает."""
    fn = CODE[CODE.index("def _attach_field_labels("):]
    fn = fn[:fn.index("\ndef ", 5)]
    assert "_input_field_names(" in fn and "not in inputs" in fn
    # поле входа с псевдонимом передаёт его выходу, без псевдонима - ничего
    assert "table.update({k: a for k, a in inputs.items() if a})" in fn


def test_labels_are_baked_into_geopackage():
    """Подписи пишутся и в сам GeoPackage (gpkg_data_columns), чтобы файл,
    открытый в другом проекте, показывал их без проекта. Запись идёт в
    postProcessAlgorithm, когда приёмники закрыты."""
    body = CODE[CODE.index("class IsolinerAlgorithm("):]
    body = body[:body.index("\nclass ", 10)]
    assert "def postProcessAlgorithm(" in body
    assert "_bake_labels(ref[0], ref[1], table)" in body
    assert "self._bake_targets = _attach_field_labels(" in body
    fn = CODE[CODE.index("def _bake_labels("):]
    fn = fn[:fn.index("\ndef ", 5)]
    # GDAL старше 3.2 альтернативных имён не знает: тихо пропускаем
    assert 'getattr(ogr, "ALTER_ALTERNATIVE_NAME_FLAG", None)' in fn
    assert "SetAlternativeName" in fn


def test_outputs_outside_results_are_labelled():
    """Выход, не попавший в словарь результатов, тоже получает подписи:
    очередь загрузки сравнивается до и после расчёта."""
    body = CODE[CODE.index("class IsolinerAlgorithm("):]
    body = body[:body.index("\nclass ", 10)]
    assert "pending = _pending_loads(context)" in body
    flat = " ".join(body.split())
    assert "_attach_field_labels( self, parameters, context, result, pending)" \
        in flat


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
