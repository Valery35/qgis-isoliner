# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Точность числовых полей выходных слоёв (field_format), без QGIS.

Отметка кровли выходила с двенадцатью знаками после запятой. Округление
идёт в приёмнике базового класса по подписи поля. Тест держит правила
единиц, само округление и то, что поле входа не трогается.

Запуск:  python grid_isolines/tests/test_field_format.py
"""
import ast
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, PKG)
import field_format as F  # noqa: E402

with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
    CODE = fh.read()


def test_units_pick_precision():
    cases = {
        "Отметка кровли, м": 2, "Мощность, м": 2, "Содержание X, %": 2,
        "Оседание, мм": 1, "Скорость оседания, мм/год": 1,
        "Наклон по реперам, мм/м": 3, "Кривизна по гриду, 10⁻⁶ 1/м": 2,
        "Азимут падения, °": 1, "Средний уклон водосбора, градусы": 1,
        "Площадь, м²": 1, "Объём насыпи, м³": 1, "Расход, м³/с": 3,
        "Скорость, м/с": 3, "Километраж, км": 3, "Площадь приёмника, км²": 3,
        "Коэффициент фильтрации K, м/сут": "g4", "Уклон": "g4",
        "Градиент, м/м": "g4", "Вес декластеризации": "g4",
        "Охват по %s, доля": 4, "Вероятность до": 4, "Шероховатость n": 4,
        "Относительная координата z": 4, "Силл": "g5",
    }
    for label, want in cases.items():
        assert F.decimals_for(label) == want, (label, F.decimals_for(label))


def test_no_unit_no_rounding():
    for label in ("Скважина", "Код пласта", "Номер разреза", "Значение изолинии",
                  "Порядок Стралера", None, ""):
        assert F.decimals_for(label) is None, label


def test_round_value():
    assert F.round_value(-65.68763256516, 2) == -65.69
    assert F.round_value(2.704586232893, 2) == 2.7
    assert F.round_value(0.0123456, "g4") == 0.01235
    assert F.round_value(1.23456e-5, "g4") == 1.235e-05
    assert str(F.round_value(-0.0001, 2)) == "0.0"       # без «-0.0»
    assert F.round_value(None, 2) is None
    assert F.round_value(7, 2) == 7 and isinstance(F.round_value(7, 2), int)
    assert F.round_value("12.3456", 2) == "12.3456"
    assert math.isnan(F.round_value(float("nan"), 2))
    assert F.round_value(True, 2) is True
    assert F.round_value(3.14159, None) == 3.14159


def test_plan_skips_input_fields_and_takes_overrides():
    labels = {"roof": "Отметка кровли, м", "X": "Содержание X, %",
              "cov_*": "Охват по %s, доля", "well": "Скважина"}
    plan = F.plan_for(["well", "roof", "X", "cov_kcl", "z_est"], labels,
                      overrides={"z_est": "g6"}, skip={"X"})
    assert plan == {"roof": 2, "cov_kcl": 4, "z_est": "g6"}


def test_base_class_wraps_sinks():
    tree = ast.parse(CODE)
    base = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                and n.name == "IsolinerAlgorithm")
    names = [f.name for f in base.body if isinstance(f, ast.FunctionDef)]
    assert "parameterAsSink" in names
    assert "_rounding_sink(self, parameters, context, fields, res)" in CODE


def test_every_numeric_label_is_known():
    """Подпись с единицами, для которой правило не нашлось, значит поле
    уйдёт с двенадцатью знаками. Единицы в подписи стоят после запятой."""
    tree = ast.parse(CODE)
    missed = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for f in node.body:
            if isinstance(f, ast.Assign) and any(
                    isinstance(t, ast.Name) and t.id == "FIELD_LABELS"
                    for t in f.targets):
                for fld, label in ast.literal_eval(f.value).items():
                    unit = label.rsplit(", ", 1)[-1] if ", " in label else ""
                    if unit and unit not in ("доля",) and \
                            any(u in unit for u in ("м", "%", "°", "мм",
                                                    "км", "Ом")) \
                            and F.decimals_for(label) is None:
                        missed.append("%s.%s: %s" % (node.name, fld, label))
    assert not missed, missed


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
