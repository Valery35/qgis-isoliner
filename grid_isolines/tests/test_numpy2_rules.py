# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Совместимость с NumPy 2: запрещённые вызовы в исходниках.

Зачем этот тест. QGIS 4 идёт с NumPy 2, где часть привычного API убрана
насовсем. Ошибка при этом тихая: код компилируется, тесты ядра проходят,
падение случается только у пользователя и только на той строке, которая
исполняется в теле алгоритма QGIS. Безголовые тесты туда не заходят, потому
что для этого нужен живой Processing.

Так и вышло в 4.18.0: `xy[:, 0].ptp()` в теле 2.17 уронил инструмент на
первом же прогоне, притом что все 17 тестов ядра были зелёными. Проверять
исходники текстом - единственный способ поймать такое до пользователя.

    python grid_isolines/tests/test_numpy2_rules.py
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(MODULE))

# Убраны в NumPy 2.0 как методы массива. Замена в комментарии.
BANNED_METHODS = {
    "ptp": "np.ptp(a)",
    "itemset": "a[idx] = value",
    "newbyteorder": "a.view(a.dtype.newbyteorder())",
}

# Убраны в NumPy 2.0 из пространства имён.
BANNED_NAMES = {
    "np.float_": "np.float64",
    "np.complex_": "np.complex128",
    "np.unicode_": "np.str_",
    "np.string_": "np.bytes_",
    "np.NaN": "np.nan",
    "np.NAN": "np.nan",
    "np.Inf": "np.inf",
    "np.infty": "np.inf",
    "np.NINF": "-np.inf",
    "np.PINF": "np.inf",
    "np.round_": "np.round",
    "np.product": "np.prod",
    "np.cumproduct": "np.cumprod",
    "np.alltrue": "np.all",
    "np.sometrue": "np.any",
    "np.in1d": "np.isin",
    "np.msort": "np.sort",
    "np.bool8": "np.bool_",
}


def _sources():
    for path in sorted(glob.glob(os.path.join(MODULE, "*.py"))):
        with open(path, encoding="utf-8") as fh:
            yield os.path.basename(path), fh.read()


def test_no_removed_array_methods():
    """Методы массива, убранные в NumPy 2.0."""
    bad = []
    for name, src in _sources():
        for meth, fix in BANNED_METHODS.items():
            # вызов методом: точка, имя, скобка. np.ptp(...) под запрет не
            # подпадает - перед именем стоит np, а не закрывающая скобка
            for m in re.finditer(r"(?<!np)\.%s\s*\(" % meth, src):
                line = src[:m.start()].count("\n") + 1
                bad.append("%s:%d  .%s() -> %s" % (name, line, meth, fix))
    assert not bad, "убранные в NumPy 2 методы:\n  " + "\n  ".join(bad)


def test_no_removed_namespace_names():
    """Имена, убранные из пространства имён NumPy 2.0."""
    bad = []
    for name, src in _sources():
        for old, fix in BANNED_NAMES.items():
            for m in re.finditer(r"\b%s\b" % re.escape(old), src):
                line = src[:m.start()].count("\n") + 1
                bad.append("%s:%d  %s -> %s" % (name, line, old, fix))
    assert not bad, "убранные в NumPy 2 имена:\n  " + "\n  ".join(bad)


def test_rule_catches_a_real_case():
    """Сторож обязан ловить именно ту строку, что уронила 2.17."""
    sample = "diag = float(np.hypot(xy[:, 0].ptp(), xy[:, 1].ptp()))"
    assert re.search(r"(?<!np)\.ptp\s*\(", sample)
    # правильная запись под запрет не подпадает
    good = "diag = float(np.hypot(np.ptp(xy[:, 0]), np.ptp(xy[:, 1])))"
    assert not re.search(r"(?<!np)\.ptp\s*\(", good)


def _run():
    for fn in (test_no_removed_array_methods,
               test_no_removed_namespace_names,
               test_rule_catches_a_real_case):
        fn()
        print("[ok]", fn.__name__)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    _run()
