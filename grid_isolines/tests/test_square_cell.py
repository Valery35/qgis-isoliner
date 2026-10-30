# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Квадратность ячейки ЦМР для группы «Топография» (topo_flow.square_cell).

Пришло от пользователя: растр геодезистов с ячейкой 0.1999974471814439 по
X и 0.2000043491567239 по Y группа 2 отвергала как неквадратный. Допуск
был в миллионную долю метра, а расхождение в 0.003 процента ни на уклоны,
ни на сток не влияет. Теперь допуск относительный, 0.1 процента.

Запуск:  python grid_isolines/tests/test_square_cell.py
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import topo_flow  # noqa: E402


def test_user_raster_is_square():
    gt = (0.0, 0.1999974471814439381, 0.0, 0.0, 0.0, -0.2000043491567239096)
    cell, rel = topo_flow.square_cell(gt)
    assert abs(cell - 0.2) < 1e-5
    assert rel < 1e-4


def test_scale_does_not_matter():
    """Допуск относительный: тот же хвост в знаках на ячейке 30 м и на
    ячейке 1 см даёт одно и то же решение."""
    for c in (0.01, 0.2, 30.0, 1000.0):
        topo_flow.square_cell((0, c * (1 - 3e-5), 0, 0, 0, -c * (1 + 3e-5)))


def test_real_rectangular_cell_is_rejected():
    with pytest.raises(ValueError):
        topo_flow.square_cell((0, 0.2, 0, 0, 0, -0.25))
    with pytest.raises(ValueError):
        topo_flow.square_cell((0, 30.0, 0, 0, 0, -30.1))


def test_zero_cell_is_rejected():
    with pytest.raises(ValueError):
        topo_flow.square_cell((0, 0.0, 0, 0, 0, -1.0))


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
