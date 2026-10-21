# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Демо-набор к 3.01 и 3.02 (demo_ikedk, 3.09), без QGIS.

Набор нужен, чтобы на совещании было видно разницу методов на поле, где
истина известна. Тесты держат то, на чём держится показ: доли классов,
связь кровли с маркирующим горизонтом, разрыв сети над складкой и
совпадение сетки с гридами группы 1 и 3.

Здесь же тест на индикаторный кригинг. До 5.13.40 без весов индикатор
кригировался к нулю, и на этом наборе зоны замещения занимали 11% площади
при истинных 32%.

Запуск:  python grid_isolines/tests/test_demo_ikedk.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import demo_ikedk as D  # noqa: E402
import kb2d  # noqa: E402

BOX = (450000.0, 6430000.0, 456000.0, 6434000.0)


def _ds(**kw):
    return D.make_dataset(*BOX, **kw)


def test_deterministic_and_shares():
    a, b = _ds(seed=1), _ds(seed=1)
    assert a["wells"] == b["wells"]
    assert np.array_equal(a["classes"], b["classes"])
    sh = a["stats"]["shares"]
    assert abs(sh[1] - D.SHARE_PARTIAL) < 0.01
    assert abs(sh[2] - D.SHARE_FULL) < 0.01
    assert _ds(seed=2)["wells"] != a["wells"]


def test_full_replacement_inside_partial():
    """Полное замещение лежит внутри частичного: у ячейки полного
    замещения нет соседа-сильвинита."""
    c = _ds()["classes"]
    full = c == 2
    sylv = c == 0
    near = np.zeros_like(full)
    near[1:, :] |= sylv[:-1, :]
    near[:-1, :] |= sylv[1:, :]
    near[:, 1:] |= sylv[:, :-1]
    near[:, :-1] |= sylv[:, 1:]
    assert not (full & near).any()


def test_roof_follows_marker_and_gap_is_empty():
    d = _ds()
    assert d["stats"]["corr"] > 0.95
    off = d["roof"] - d["marker"]
    assert abs(off.mean() - D.ROOF_OFFSET) < 0.5
    gx, gy, a, b = d["gap"]
    for x, y, _n, _z, _c in d["wells"]:
        assert ((x - gx) / a) ** 2 + ((y - gy) / b) ** 2 > 1.0
    assert _ds(gap=False)["gap"] is None


def test_grid_matches_kriging_grid():
    """Число ячеек вверх от левого нижнего угла, как у кригинга: грид с
    той же ячейкой ложится ячейка в ячейку, растр дрейфа покрывает его."""
    cell, nx, ny = D.grid_spec(*BOX)
    assert (cell, nx, ny) == (30.0, 200, 134)
    gt = _ds()["gt"]
    assert gt[3] == BOX[1] + ny * cell


def test_indicator_keeps_replacement_area():
    """Индикатор кригуется к доле класса: площадь замещения близка к
    истинной, а не в три раза меньше."""
    d = _ds()
    w = d["wells"]
    xs = np.array([t[0] for t in w])
    ys = np.array([t[1] for t in w])
    lab = np.array([D.CLASSES[t[4]] for t in w], dtype=object)
    classes = sorted(set(lab))
    cell = 60.0
    nx, ny = 100, 67
    probs, zone, _ = kb2d.categorical_indicator_grids(
        xs, ys, lab, classes, BOX[0] + cell / 2, BOX[1] + cell / 2, cell,
        nx, ny, ndmin=4, ndmax=24, radius=8000.0)
    k1 = classes.index(D.CLASSES[1])
    k2 = classes.index(D.CLASSES[2])
    repl = (probs[:, :, k1] + probs[:, :, k2]) >= 0.5
    truth = (d["classes"] > 0).mean()
    assert abs(repl.mean() - truth) < 0.08, (repl.mean(), truth)
    # между скважинами вероятность класса не проваливается к нулю: средняя
    # по площади не меньше половины доли класса в скважинах (с нулевым
    # средним было 5% при доле 22%)
    for k, c in enumerate(classes):
        share = float(np.mean(lab == c))
        assert float(probs[:, :, k].mean()) > 0.5 * share, c


def test_rmse_and_ring():
    t = np.zeros((3, 3))
    e = np.ones((3, 3))
    e[0, 0] = np.nan
    assert D.rmse(e, t) == 1.0
    m = np.zeros((3, 3), dtype=bool)
    assert np.isnan(D.rmse(e, t, m))
    ring = D.ellipse_ring(0, 0, 2, 1, n=8)
    assert ring[0] == ring[-1] and len(ring) == 9


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
