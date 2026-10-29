# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Снятие мостов с ЦМР (topo_bridge, 2.17), без QGIS.

Модель - река вдоль оси x с падением воды по течению, берега поднимаются
от уреза. Мост лежит поперёк реки настилом на десять метров выше берегов.
Проверяется главное: под настилом встаёт вода, русло не запирается, а
поверхность только опускается и не трогается за контуром.

Пробовалась и гармоническая интерполяция (среднее соседей). В узкой полосе
она тянет значения от высоких берегов вдоль полосы, и вода под настилом
выходила на 0.7 м выше воды по обе стороны. Тест на прямой мост держит
это место.

Запуск:  python grid_isolines/tests/test_topo_bridge.py
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import topo_bridge as tb  # noqa: E402

NY, NX = 60, 80


def _valley():
    y, x = np.mgrid[0:NY, 0:NX].astype(float)
    water = 100.0 - 0.02 * x
    z = water + np.maximum(0.0, np.abs(y - 30) - 3) * 0.8
    return z, y


def _deck(kind):
    d = np.zeros((NY, NX), dtype=bool)
    for k in range(-9, 10):
        if kind == "perp":
            d[30 + k, 40:42] = True
        elif kind == "skew":
            d[30 + k, 40 + k // 2:42 + k // 2] = True
        else:
            d[30 + k, 40 + k:42 + k] = True
    return d


def _run(kind, margin=1):
    z0, y = _valley()
    deck = _deck(kind)
    z = z0.copy()
    z[deck] = 110.0
    out, reps = tb.remove_bridges(z, None, tb.dilate(deck, margin))
    return z0, z, out, reps, y


def _channel_rises(out):
    """Наибольший подъём дна русла по течению: минимум поперёк русла по
    каждому столбцу должен не расти вниз по течению."""
    chan = out[26:35].min(axis=0)
    run_min = np.minimum.accumulate(chan)
    return float(max(0.0, np.max(chan[1:] - run_min[:-1])))


def test_square_bridge_restores_water():
    z0, z, out, reps, _ = _run("perp")
    assert len(reps) == 1 and reps[0]["status"] == "ok"
    assert reps[0]["lowered_max"] > 9.0
    assert np.abs(out - z0).max() < 0.15, np.abs(out - z0).max()


def test_skewed_bridges_do_not_dam_the_river():
    for kind in ("perp", "skew", "diag"):
        z0, z, out, reps, y = _run(kind)
        assert _channel_rises(out) == 0.0, kind
        assert out[28:33].max() - z0[28:33].max() < 0.5, kind


def test_diagonal_valley_under_square_embankment_opens():
    """Лог идёт наискось к сетке, насыпь обведена прямоугольником.

    Так было на насыпи через лог у Перми (GLO-30). Кратчайший пролёт у
    ячейки над руслом шёл по диагонали в борт долины, и при весе по длине
    пролёта над руслом оставался порог в 4.5 м. Самый низкий из коротких
    пролётов идёт вдоль русла."""
    n = 60
    y, x = np.mgrid[0:n, 0:n].astype(float)
    along = (x + y) / math.sqrt(2.0)          # по течению
    across = (x - y) / math.sqrt(2.0)         # поперёк
    z0 = 100.0 - 0.1 * along + 1.5 * np.abs(across)
    z = z0.copy()
    emb = np.zeros_like(z, dtype=bool)
    emb[26:32, 24:36] = True                  # насыпь поперёк лога
    z[emb] = z0[emb].max() + 12.0
    out, reps = tb.remove_bridges(z, None, emb)
    # по оси лога поверхность падает монотонно
    axis = [out[k, k] for k in range(n)]
    assert all(b2 <= a2 + 1e-9 for a2, b2 in zip(axis, axis[1:]))
    assert abs(out[28, 28] - z0[28, 28]) < 0.2


def test_surface_is_only_lowered_and_only_inside():
    z0, z, out, reps, _ = _run("skew")
    assert (out <= z + 1e-12).all()
    m = tb.dilate(_deck("skew"), 1)
    assert np.array_equal(out[~m], z[~m])


def test_margin_matters_for_blurred_deck():
    """Край настила в модели поверхности размыт: ячейка за контуром стоит
    на полпути к настилу. Без запаса она становится окружением, и вода
    под мостом встаёт выше настоящей."""
    z0, y = _valley()
    deck = _deck("perp")
    z = z0.copy()
    z[deck] = 110.0
    blur = tb.dilate(deck, 1) & ~deck
    z[blur] = 0.5 * (z0[blur] + 110.0)
    no_margin, _ = tb.remove_bridges(z, None, deck)
    with_margin, _ = tb.remove_bridges(z, None, tb.dilate(deck, 1))
    river = np.abs(y - 30) <= 3
    assert (no_margin - z0)[river].max() > 2.0
    assert (with_margin - z0)[river].max() < 0.15


def test_nodata_is_kept_and_isolated_patch_reported():
    z0, y = _valley()
    z = z0.copy()
    nod = np.zeros_like(z, dtype=bool)
    nod[10:20, 10:20] = True
    mask = np.zeros_like(nod)
    mask[13:17, 13:17] = True              # мост внутри пустоты
    out, reps = tb.remove_bridges(z, nod, mask)
    assert reps == []                      # ячеек с отметкой под ним нет
    mask[5:8, 50:53] = True
    nod2 = nod.copy()
    nod2[4:9, 49:54] = True
    nod2[5:8, 50:53] = False               # пятно в кольце пустоты
    out, reps = tb.remove_bridges(z, nod2, mask)
    assert len(reps) == 1 and reps[0]["status"] == "no_edge"
    assert np.array_equal(out, z)


def test_two_bridges_are_separate_reports():
    z0, y = _valley()
    z = z0.copy()
    deck = _deck("perp")
    deck2 = np.roll(deck, -25, axis=1)
    z[deck | deck2] = 110.0
    out, reps = tb.remove_bridges(z, None, tb.dilate(deck | deck2, 1))
    assert len(reps) == 2
    assert all(r["status"] == "ok" for r in reps)
    assert _channel_rises(out) == 0.0


def test_dilate_and_components():
    m = np.zeros((7, 7), dtype=bool)
    m[3, 3] = True
    assert tb.dilate(m, 1).sum() == 9
    assert tb.dilate(m, 2).sum() == 25
    m2 = np.zeros((6, 6), dtype=bool)
    m2[0, 0] = m2[1, 1] = True            # диагональ - одно пятно
    m2[4, 4] = True
    comps = tb.components(m2)
    assert sorted(len(r) for r, _c in comps) == [1, 2]


def test_edge_of_grid_uses_one_side():
    """Мост у края листа: пролёт наружу не замыкается, работают те
    направления, где оба конца есть."""
    z0, y = _valley()
    z = z0.copy()
    deck = np.zeros_like(z, dtype=bool)
    deck[22:39, 0:2] = True
    z[deck] = 110.0
    out, reps = tb.remove_bridges(z, None, deck)
    assert reps[0]["status"] == "ok"
    assert np.isfinite(out[deck]).all()
    assert (out <= z + 1e-12).all()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
