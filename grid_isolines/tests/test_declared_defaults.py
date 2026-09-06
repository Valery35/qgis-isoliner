# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Вызов с неполным набором параметров обязан быть воспроизводимым.

Инструменты запоминают введённые значения и подставляют их в окно
параметров при следующем запуске: это удобно и остаётся. Но подстановка
шла через defaultValue самого параметра, а туда же смотрит Processing,
когда параметр не задан вызовом. Поэтому вызов из модели или из скрипта
с частью параметров брал недостающие из памяти прошлого запуска: тот же
скрипт на другой машине, в другом профиле QGIS или просто на следующий
день считал по другим значениям, и по журналу это не читалось.

Теперь недостающие параметры берутся из объявления инструмента, а память
остаётся только окну, которое всё равно подаёт полный набор.

QGIS в окружении тестов нет, qgis.* подменяется заглушкой тем же
способом, что в test_algorithms_init.
"""
import importlib
import os
import sys
import types

_SAVED_MODULES = None


def setup_module(module):
    global _SAVED_MODULES
    _SAVED_MODULES = dict(sys.modules)


def teardown_module(module):
    if _SAVED_MODULES is None:
        return
    for name in list(sys.modules):
        if name not in _SAVED_MODULES:
            del sys.modules[name]
    sys.modules.update(_SAVED_MODULES)


class _Stub(object):
    def __init__(self, *a, **k):
        pass

    def __call__(self, *a, **k):
        return _Stub()

    def __getattr__(self, name):
        return _Stub()

    def __or__(self, other):
        return _Stub()
    __ror__ = __or__

    def __int__(self):
        return 0


def _algorithms():
    # Модуль защищён от повторного импорта в одном процессе, а до нас его
    # мог уже поднять соседний файл тестов. Тогда берём поднятый, иначе
    # второй импорт упирается в этот же сторож.
    ready = sys.modules.get("grid_isolines.algorithms")
    if ready is not None:
        ready._tr = lambda s: s
        return ready
    here = os.path.dirname(os.path.abspath(__file__))
    pkg_dir = os.path.dirname(here)
    parent = os.path.dirname(pkg_dir)
    m = types.ModuleType("grid_isolines")
    m.__path__ = [pkg_dir]
    sys.modules["grid_isolines"] = m
    if parent not in sys.path:
        sys.path.insert(0, parent)
    for mod in ("qgis", "qgis.core", "qgis.gui", "qgis.PyQt",
                "qgis.PyQt.QtCore", "qgis.PyQt.QtGui",
                "qgis.PyQt.QtWidgets", "processing",
                "osgeo", "osgeo.gdal", "osgeo.osr"):
        stub = types.ModuleType(mod)
        stub.__getattr__ = lambda name: _Stub
        sys.modules[mod] = stub
    a = importlib.import_module("grid_isolines.algorithms")
    a._tr = lambda s: s
    return a


class _Alg(object):
    """Пустышка вместо инструмента: только то, чего касается подстановка."""

    def __init__(self, memory):
        self._defaults = dict(memory)


class _Feedback(object):
    def __init__(self):
        self.warnings = []

    def pushWarning(self, text):
        self.warnings.append(text)


def _declare(A, alg, declared):
    """Повторяет объявление параметров: каждый _dv пишет заявленное."""
    for key, fallback in declared.items():
        A._dv(alg, key, fallback)


def test_declared_value_wins_over_memory_for_a_missing_parameter():
    A = _algorithms()
    alg = _Alg({"MAX_POINTS": 40, "NUGGET": 7.5})
    _declare(A, alg, {"MAX_POINTS": 24, "NUGGET": 0.0})
    out = A._declared_for_missing(alg, {"INPUT": "точки"}, _Feedback())
    assert out["MAX_POINTS"] == 24, (
        "недостающий параметр снова взят из памяти: %r" % out["MAX_POINTS"])
    assert out["NUGGET"] == 0.0
    assert out["INPUT"] == "точки"


def test_given_parameters_are_never_touched():
    A = _algorithms()
    alg = _Alg({"MAX_POINTS": 40})
    _declare(A, alg, {"MAX_POINTS": 24})
    out = A._declared_for_missing(alg, {"MAX_POINTS": 8}, _Feedback())
    assert out["MAX_POINTS"] == 8, "заданное вызовом значение подменено"


def test_full_call_returns_the_same_object():
    """Окно подаёт весь набор, и трогать там нечего.

    Возврат того же словаря - признак, что путь окна не изменился вовсе.
    """
    A = _algorithms()
    alg = _Alg({"MAX_POINTS": 40})
    _declare(A, alg, {"MAX_POINTS": 24})
    given = {"MAX_POINTS": 8}
    assert A._declared_for_missing(alg, given, _Feedback()) is given


def test_the_substitution_is_reported_when_memory_differs():
    A = _algorithms()
    alg = _Alg({"MAX_POINTS": 40})
    _declare(A, alg, {"MAX_POINTS": 24})
    fb = _Feedback()
    A._declared_for_missing(alg, {}, fb)
    assert fb.warnings, "подстановка прошла молча"
    assert "MAX_POINTS" in fb.warnings[0]


def test_silent_when_memory_equals_the_declared_value():
    """Память, совпадающая с объявлением, ничего не меняет и молчит."""
    A = _algorithms()
    alg = _Alg({"MAX_POINTS": 24})
    _declare(A, alg, {"MAX_POINTS": 24})
    fb = _Feedback()
    out = A._declared_for_missing(alg, {}, fb)
    assert out["MAX_POINTS"] == 24
    assert not fb.warnings, "сообщение без повода: подставилось то же самое"


def test_two_sessions_with_different_memory_give_the_same_parameters():
    """Тот самый критерий: разная память - одинаковый набор параметров."""
    A = _algorithms()
    declared = {"MAX_POINTS": 24, "NUGGET": 0.0, "KTYPE": 1}
    first = _Alg({"MAX_POINTS": 40, "NUGGET": 7.5, "KTYPE": 0})
    second = _Alg({})
    _declare(A, first, declared)
    _declare(A, second, declared)
    call = {"INPUT": "точки", "ZFIELD": "v"}
    a = A._declared_for_missing(first, call, _Feedback())
    b = A._declared_for_missing(second, call, _Feedback())
    assert a == b, "один и тот же вызов дал разные наборы параметров"


def test_base_class_applies_it_before_the_body():
    """Подстановка должна стоять в базовом классе, до вызова _process.

    Иначе каждый из семидесяти инструментов пришлось бы править отдельно,
    и новый инструмент забыли бы. Разбор по исходнику.
    """
    import ast
    here = os.path.dirname(os.path.abspath(__file__))
    src = os.path.join(os.path.dirname(here), "algorithms.py")
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == "IsolinerAlgorithm":
            body = ast.get_source_segment(text, node) or ""
            break
    else:
        raise AssertionError("базовый класс IsolinerAlgorithm пропал")
    at = body.find("_declared_for_missing(")
    assert at != -1, "базовый класс больше не подставляет заявленные значения"
    assert at < body.find("self._process("), (
        "подстановка стоит после тела инструмента")
