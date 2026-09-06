# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Перестановка строк входного слоя не должна менять карту.

На правильной сети проб узел оценки часто равноудалён сразу от
нескольких замеров, а место в выборке одно. Раньше при равных
расстояниях брался замер с меньшим номером, а номер это строка входного
файла. Тот же слой, записанный в другом порядке, давал другую выборку и
другую карту: на сети 11 x 11 с узлами посреди ячеек расхождение
доходило до 85 единиц при размахе значений от нуля до ста.

Теперь ничья решается по самим данным: координата X, затем Y, затем
значение. Эти ключи от порядка строк не зависят.

Проверка идёт на ядре kb2d, без QGIS.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import kb2d  # noqa: E402

BIG_RADIUS = 1e18       # без ограничения по радиусу


def _grid(xs, ys, vs, ndmax):
    """Оценка на сетке, сдвинутой на полшага от сети проб.

    Сдвиг важен: узел посреди ячейки равноудалён от четырёх замеров
    сразу, то есть ничья возникает в каждом узле, а не изредка.
    """
    vg = kb2d.Variogram(10.0, [dict(it=1, cc=90.0, aa=400.0,
                                    ang=0.0, anis=1.0)])
    return kb2d.build_grid(xs, ys, vs, vg, ktype=1, skmean=0.0,
                           ndmin=1, ndmax=ndmax, rad2=BIG_RADIUS,
                           nodata=-9999.0, xmn=50.0, ymn=50.0, cell=100.0,
                           nx=10, ny=10).astype("float64")


def _samples():
    rng = np.random.default_rng(5)
    gx, gy = np.meshgrid(np.arange(0, 1001, 100.0),
                         np.arange(0, 1001, 100.0))
    xs = gx.ravel().astype(float)
    ys = gy.ravel().astype(float)
    vs = np.round(rng.uniform(0.0, 100.0, xs.size), 3)
    return xs, ys, vs


def test_row_order_does_not_change_the_map():
    xs, ys, vs = _samples()
    order = np.random.default_rng(5).permutation(xs.size)
    for ndmax in (1, 2, 3, 5, 7):
        a = _grid(xs, ys, vs, ndmax)
        b = _grid(xs[order], ys[order], vs[order], ndmax)
        assert np.array_equal(a, b), (
            "при ndmax=%d перестановка строк входного слоя изменила карту: "
            "различий %d из %d, наибольшее %.6g"
            % (ndmax, int(np.count_nonzero(a != b)), a.size,
               float(np.abs(a - b).max())))


def test_reversed_order_too():
    """Обратный порядок строк - отдельный случай, а не та же перестановка."""
    xs, ys, vs = _samples()
    a = _grid(xs, ys, vs, 3)
    b = _grid(xs[::-1], ys[::-1], vs[::-1], 3)
    assert np.array_equal(a, b), "обратный порядок строк изменил карту"


def test_same_order_twice_is_identical():
    """Контроль: без перестановки расчёт и так повторяется побитово.

    Без этой проверки предыдущие две ничего не значили бы: совпадение
    могло бы означать, что расчёт вообще не зависит от входа.
    """
    xs, ys, vs = _samples()
    assert np.array_equal(_grid(xs, ys, vs, 3), _grid(xs, ys, vs, 3))


def test_shifted_samples_have_no_ties():
    """Контроль наоборот: со сбитой сети ничьих нет, и карта та же.

    Сдвиг в пять сантиметров разводит расстояния, ничья исчезает, и
    порядок строк не может влиять даже при старом правиле. Если этот
    тест когда-нибудь упадёт, дело не в ничьих, а в чём-то другом.
    """
    xs, ys, vs = _samples()
    rng = np.random.default_rng(11)
    xs = xs + rng.uniform(-0.05, 0.05, xs.size)
    ys = ys + rng.uniform(-0.05, 0.05, ys.size)
    order = rng.permutation(xs.size)
    a = _grid(xs, ys, vs, 3)
    b = _grid(xs[order], ys[order], vs[order], 3)
    assert np.array_equal(a, b)


def test_tie_is_resolved_by_data_not_by_row_number():
    """Выбранный сосед определяется координатами, а не номером строки.

    Четыре замера стоят вокруг узла на одинаковом расстоянии, берётся
    один. При решении по данным это всегда замер с наименьшим X (при
    равных X - с наименьшим Y), какой бы строкой он ни был записан.
    """
    xs = np.array([0.0, 100.0, 0.0, 100.0])
    ys = np.array([0.0, 0.0, 100.0, 100.0])
    vs = np.array([10.0, 20.0, 30.0, 40.0])
    vg = kb2d.Variogram(0.0, [dict(it=1, cc=100.0, aa=1000.0,
                                   ang=0.0, anis=1.0)])

    def one(idx):
        g = kb2d.build_grid(xs[idx], ys[idx], vs[idx], vg, ktype=1,
                            skmean=0.0, ndmin=1, ndmax=1, rad2=BIG_RADIUS,
                            nodata=-9999.0, xmn=50.0, ymn=50.0, cell=100.0,
                            nx=1, ny=1)
        return float(g[0, 0])

    straight = one(np.arange(4))
    assert abs(straight - 10.0) < 1e-6, (
        "взят не замер в начале координат, а значение %.6g" % straight)
    assert abs(one(np.array([3, 2, 1, 0])) - straight) < 1e-12
    assert abs(one(np.array([2, 0, 3, 1])) - straight) < 1e-12
