# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Длина речной сети внутри водосбора.

Вопрос пришёл от пользователя 2.17: он искал в отчёте длину притоков и
взял `sp_iso_km`, а там суммарная длина ГОРИЗОНТАЛЕЙ, число на два
порядка больше. Своего поля для сети в отчёте не было вовсе, и получить
её можно было только отдельным прогоном 2.06 с обрезкой по полигону.

Здесь проверяется ядро: длина считается по звеньям решётки стока, диагональ
весит корень из двух, за границей водосбора счёт прекращается, а густота
это длина на площадь.

Без QGIS.
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from grid_isolines.topo_gauge import network_length_in_mask  # noqa: E402


def _line_down(nx, ny):
    """Решётка стока: каждая ячейка столбца течёт в ячейку ниже.

    Возвращает (downstream, shape). Последняя строка уходит наружу (-1).
    """
    down = np.full(nx * ny, -1, dtype=np.int64)
    for r in range(ny - 1):
        for c in range(nx):
            down[r * nx + c] = (r + 1) * nx + c
    return down, (ny, nx)


def test_straight_chain_is_counted_by_links_not_by_cells():
    """Пять ячеек в цепочке это четыре звена, а не пять.

    Ошибка на единицу здесь самая вероятная, и на короткой сети она даёт
    четверть длины.
    """
    down, shape = _line_down(1, 5)
    acc = np.arange(1, 6, dtype=np.float64).reshape(shape)
    mask = np.ones(shape, dtype=bool)
    got = network_length_in_mask(down, acc, mask, 10.0, threshold=1.0)
    assert abs(got - 40.0) < 1e-9, got


def test_diagonal_link_weighs_sqrt_two():
    down = np.full(4, -1, dtype=np.int64)
    down[0] = 3                      # из левого верхнего в правый нижний
    acc = np.full((2, 2), 5.0)
    mask = np.ones((2, 2), dtype=bool)
    got = network_length_in_mask(down, acc, mask, 10.0, threshold=1.0)
    assert abs(got - 10.0 * math.sqrt(2.0)) < 1e-9, got


def test_threshold_cuts_the_headwaters():
    """Порог решает, что считать водотоком, и длина меняется вместе с ним."""
    down, shape = _line_down(1, 5)
    acc = np.arange(1, 6, dtype=np.float64).reshape(shape)
    mask = np.ones(shape, dtype=bool)
    full = network_length_in_mask(down, acc, mask, 10.0, threshold=1.0)
    cut = network_length_in_mask(down, acc, mask, 10.0, threshold=3.0)
    assert abs(full - 40.0) < 1e-9
    # остаются ячейки с acc 3, 4, 5 - это два звена
    assert abs(cut - 20.0) < 1e-9, cut


def test_link_leaving_the_catchment_is_not_counted():
    """Считаем длину ВНУТРИ заданного водосбора.

    Звено, уходящее за границу, принадлежит уже соседнему водосбору, и
    приписывать его сюда значит считать одну и ту же реку дважды.
    """
    down, shape = _line_down(1, 5)
    acc = np.full(shape, 9.0)
    mask = np.zeros(shape, dtype=bool)
    mask[:3, 0] = True               # водосбор это три верхние ячейки
    got = network_length_in_mask(down, acc, mask, 10.0, threshold=1.0)
    assert abs(got - 20.0) < 1e-9, got


def test_no_network_above_threshold_gives_zero_not_none():
    down, shape = _line_down(1, 5)
    acc = np.ones(shape)
    mask = np.ones(shape, dtype=bool)
    got = network_length_in_mask(down, acc, mask, 10.0, threshold=100.0)
    assert got == 0.0


def test_threshold_zero_means_switched_off():
    """Ноль отключает расчёт, как и в остальных параметрах группы."""
    down, shape = _line_down(1, 5)
    acc = np.arange(1, 6, dtype=np.float64).reshape(shape)
    mask = np.ones(shape, dtype=bool)
    assert network_length_in_mask(down, acc, mask, 10.0, threshold=0.0) is None


def test_nodata_cells_drop_out_of_the_mask():
    down, shape = _line_down(1, 5)
    acc = np.full(shape, 9.0)
    mask = np.ones(shape, dtype=bool)
    nod = np.zeros(shape, dtype=bool)
    nod[3:, 0] = True
    got = network_length_in_mask(down, acc, mask, 10.0, threshold=1.0,
                                 nodata_mask=nod)
    assert abs(got - 20.0) < 1e-9, got
