# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Тесты трёхмерных тальвегов: приведение отметок и спор узлов.

Тальвег с измеренными отметками становится набором жёстких узлов, а не
только условием падения. Два места, где это может сломаться, и проверяются
здесь: шум, идущий вверх по течению, и пересечение русла с горизонталью.

    python grid_isolines/tests/test_streams_z.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(MODULE))

from grid_isolines import topo_t2r as T  # noqa: E402


def test_monotone_down_makes_it_fall():
    """Отметки с подъёмом посередине становятся строго падающими."""
    z = np.array([100.0, 99.0, 99.4, 98.0, 98.2, 97.0])
    out = T.monotone_down(z, 0.01)
    assert np.all(np.diff(out) < 0)


def test_monotone_down_keeps_good_data():
    """Уже падающие отметки не трогаются: правка не должна быть данью форме."""
    z = np.array([50.0, 49.0, 48.0, 47.0])
    out = T.monotone_down(z, 0.01)
    assert np.allclose(out, z)


def test_monotone_down_only_lowers():
    """Правка идёт вниз: отметку выше измеренной инструмент не выдумывает."""
    z = np.array([10.0, 12.0, 9.0, 11.0, 8.0])
    out = T.monotone_down(z, 0.01)
    assert np.all(out <= z + 1e-12)


def test_monotone_down_short_chain():
    """Цепочка из одной вершины не роняет расчёт."""
    assert T.monotone_down(np.array([5.0]), 0.01).size == 1
    assert T.monotone_down(np.array([]), 0.01).size == 0


def test_conflicts_counted_once_per_cell():
    """Спор узлов считается по ячейкам, а не по вершинам."""
    a = np.array([[0.5, 0.5, 100.0]])
    b = np.array([[0.6, 0.6, 95.0], [0.7, 0.7, 94.0]])
    assert T.count_conflicts(a, b, cell=1.0) == 1


def test_conflicts_ignore_small_difference():
    """Расхождение в пределах допуска спором не считается."""
    a = np.array([[0.5, 0.5, 100.0]])
    b = np.array([[0.6, 0.6, 100.02]])
    assert T.count_conflicts(a, b, cell=1.0, tol=0.05) == 0


def test_conflicts_none_when_apart():
    """Узлы в разных ячейках не спорят."""
    a = np.array([[0.5, 0.5, 100.0]])
    b = np.array([[5.5, 5.5, 10.0]])
    assert T.count_conflicts(a, b, cell=1.0) == 0


def test_conflicts_empty_input():
    """Пустой набор не роняет счёт."""
    a = np.zeros((0, 3))
    b = np.array([[1.0, 1.0, 1.0]])
    assert T.count_conflicts(a, b, cell=1.0) == 0
    assert T.count_conflicts(b, a, cell=1.0) == 0


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t()
            print("[ok]", t.__name__)
        except AssertionError as e:
            bad += 1
            print("FAIL", t.__name__, e)
    print("%d тестов, ошибок %d" % (len(TESTS), bad))
    sys.exit(1 if bad else 0)
