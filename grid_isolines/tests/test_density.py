# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Headless-тесты ядра плотности с переменной опорой (без QGIS)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from grid_isolines import density as D          # noqa: E402


def _grid():
    return D.GridSpec.from_extent(0.0, 0.0, 1000.0, 800.0, 10.0)


def test_point_mass_invariant():
    gs = _grid()
    acc, sn, ws = gs.new_acc()
    masses = [5.0, 3.0, 7.5]
    sig = [30.0, 60.0, 15.0]
    xy = [(500, 400), (250, 200), (700, 600)]
    for (x, y), m, s in zip(xy, masses, sig):
        D.add_point(acc, sn, ws, gs, x, y, m, s)
    _, _, total = D.finalize(acc, sn, ws, gs)
    assert abs(total - sum(masses)) < 1e-9         # инвариант массы точно


def test_density_independent_of_cell_size():
    # одна точка, масса 10, плотность в массе/км² не должна зависеть от ячейки
    tot = []
    for cell in (5.0, 10.0, 20.0):
        gs = D.GridSpec.from_extent(0.0, 0.0, 1000.0, 1000.0, cell)
        acc, sn, ws = gs.new_acc()
        D.add_point(acc, sn, ws, gs, 500, 500, 10.0, 50.0)
        dens, _, _ = D.finalize(acc, sn, ws, gs)
        # интеграл плотности*площадь = масса, при любой ячейке
        integral = dens.sum() * gs.cell_area_km2()
        tot.append(integral)
    assert all(abs(t - 10.0) < 1e-9 for t in tot)


def test_sigma_floor():
    gs = _grid()
    acc, sn, ws = gs.new_acc()
    log = []
    # сигма меньше полуячейки (5) -> поднимется, масса всё равно сойдётся
    D.add_point(acc, sn, ws, gs, 500, 400, 4.0, 1.0, log=log)
    _, _, total = D.finalize(acc, sn, ws, gs)
    assert abs(total - 4.0) < 1e-9
    assert any("поднята до полуячейки" in s for s in log)


def test_zero_and_negative_mass_skipped():
    gs = _grid()
    acc, sn, ws = gs.new_acc()
    log = []
    D.add_point(acc, sn, ws, gs, 500, 400, 0.0, 30.0, log=log)
    D.add_point(acc, sn, ws, gs, 500, 400, -2.0, 30.0, log=log)
    _, _, total = D.finalize(acc, sn, ws, gs)
    assert total == 0.0
    assert sum("пропущен" in s for s in log) == 2


def test_edge_renorm_vs_lose():
    # точка у самого края: renorm держит массу, lose - теряет часть
    gs = D.GridSpec.from_extent(0.0, 0.0, 200.0, 200.0, 10.0)
    a1, s1, w1 = gs.new_acc()
    D.add_point(a1, s1, w1, gs, 5.0, 5.0, 10.0, 40.0, renorm_inside=True)
    _, _, t_in = D.finalize(a1, s1, w1, gs)
    a2, s2, w2 = gs.new_acc()
    D.add_point(a2, s2, w2, gs, 5.0, 5.0, 10.0, 40.0, renorm_inside=False)
    _, _, t_lose = D.finalize(a2, s2, w2, gs)
    assert abs(t_in - 10.0) < 1e-9                  # донормировка внутри
    assert t_lose < 10.0                            # часть массы потеряна краем


def test_line_equals_chain_of_subpoints():
    gs = _grid()
    verts = [(100, 400), (900, 400)]               # горизонтальный отрезок
    a1, s1, w1 = gs.new_acc()
    D.add_line(a1, s1, w1, gs, verts, 12.0, 25.0)
    _, _, t_line = D.finalize(a1, s1, w1, gs)
    assert abs(t_line - 12.0) < 1e-9               # масса линии сохранена


def test_polygon_uniform_invariant():
    gs = _grid()
    mask = np.zeros((gs.ny, gs.nx), bool)
    mask[20:40, 30:70] = True                       # прямоугольник
    acc, sn, ws = gs.new_acc()
    D.add_polygon(acc, sn, ws, gs, mask, 20.0)
    _, _, total = D.finalize(acc, sn, ws, gs)
    assert abs(total - 20.0) < 1e-9


def test_polygon_dasymetric_and_fallback():
    gs = _grid()
    mask = np.zeros((gs.ny, gs.nx), bool)
    mask[20:40, 30:70] = True
    aux = np.zeros((gs.ny, gs.nx), float)
    aux[20:30, 30:70] = 1.0                         # вес только в половине
    acc, sn, ws = gs.new_acc()
    D.add_polygon(acc, sn, ws, gs, mask, 20.0, aux=aux)
    dens, _, total = D.finalize(acc, sn, ws, gs)
    assert abs(total - 20.0) < 1e-9                 # инвариант держится
    # вся масса ушла в верхнюю половину (где aux>0)
    top = dens[20:30, 30:70].sum()
    bot = dens[30:40, 30:70].sum()
    assert top > 0 and abs(bot) < 1e-9
    # откат: пустой aux -> равномерно, с логом
    log = []
    a2, s2, w2 = gs.new_acc()
    D.add_polygon(a2, s2, w2, gs, mask, 20.0, aux=np.zeros_like(aux), log=log)
    _, _, t2 = D.finalize(a2, s2, w2, gs)
    assert abs(t2 - 20.0) < 1e-9
    assert any("откат на равномерное" in s for s in log)


def test_append_series_effective_sigma():
    # дописывание серией: два аккумулятора дают корректную эфф. сигму
    gs = _grid()
    acc, sn, ws = gs.new_acc()
    D.add_point(acc, sn, ws, gs, 500, 400, 10.0, 20.0)   # первый запуск
    D.add_point(acc, sn, ws, gs, 500, 400, 10.0, 60.0)   # дописали второй
    _, eff, total = D.finalize(acc, sn, ws, gs)
    assert abs(total - 20.0) < 1e-9
    # в центре эфф. сигма - средневзвешенная между 20 и 60, внутри диапазона
    c = eff[40, 50]
    assert 20.0 < c < 60.0


def test_rasterize_polygon_and_hole():
    gs = D.GridSpec.from_extent(0, 0, 100, 100, 10.0)
    sq = [(20, 20), (80, 20), (80, 80), (20, 80), (20, 20)]
    m = D.rasterize_polygon(gs, [sq])
    assert int(m.sum()) == 36                      # 6x6 ячеек внутри
    outer = [(10, 10), (90, 10), (90, 90), (10, 90), (10, 10)]
    hole = [(40, 40), (60, 40), (60, 60), (40, 60), (40, 40)]
    full = int(D.rasterize_polygon(gs, [outer]).sum())
    withhole = int(D.rasterize_polygon(gs, [outer, hole]).sum())
    assert withhole < full                         # дыра вычитается


def test_cut_polyline():
    verts = [(0, 0), (100, 0)]
    c = D.cut_polyline(verts, 30, 70)
    assert abs(c[0, 0] - 30) < 1e-9 and abs(c[-1, 0] - 70) < 1e-9
    whole = D.cut_polyline(verts, None, None)
    assert abs(whole[0, 0]) < 1e-9 and abs(whole[-1, 0] - 100) < 1e-9
    assert D.cut_polyline(verts, 70, 30) is None    # пустой интервал



def test_demo_dataset_round_mass():
    ds = D.demo_dataset(0, 0, 1000, 1000, seed=1)
    pm = sum(p[2] for p in ds["points"])
    lm = sum(l["mass"] for l in ds["lines"])
    gm = sum(pg["mass"] for pg in ds["polygons"])
    assert pm == 500.0 and lm == 200.0 and gm == 300.0
    assert ds["total"] == 1000.0
    assert len(ds["points"]) == 10 and len(ds["lines"]) == 2
    assert any(pg["dasy"] for pg in ds["polygons"])       # есть дазиметрический
    # точечная часть воспроизводит свою массу на сетке
    gs = D.GridSpec.from_extent(0, 0, 1000, 1000, 10.0)
    acc, snum, wsum = gs.new_acc()
    for x, y, m, sg in ds["points"]:
        D.add_point(acc, snum, wsum, gs, x, y, m, sg)
    assert abs(acc.sum() - 500.0) < 1e-6



def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok:", fn.__name__)
    print("all %d tests passed" % len(fns))


if __name__ == "__main__":
    _run()
