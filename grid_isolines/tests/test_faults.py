# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Разломы в кригинге: отбор соседей по видимости."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kb2d  # noqa: E402


def _segs():
    """Вертикальный разлом от y=0 до y=26, затухающий выше."""
    return kb2d.fault_segments([[(20.0, 0.0), (20.0, 26.0)]])


def test_fault_stays_a_line_and_is_not_rasterized():
    """Разлом остаётся линией: звенья, а не ячейки.

    Растровая маска не воспроизводила диагональ и, что хуже, делала
    дырку в гриде. Возврата к сетке быть не должно.
    """
    segs = _segs()
    assert segs.shape == (1, 4)
    assert not hasattr(kb2d, "rasterize_faults"), \
        "растровая маска разломов вернулась"


def test_sight_is_blocked_across_and_open_around():
    """Сквозь разлом не видно, вокруг его конца видно.

    Это и есть причина, по которой затухающий разлом не требует, чтобы
    линия рассекала площадь: влияние огибает конец само.
    """
    s = _segs()
    assert not kb2d.visible(10, 10, 30, 10, s)
    assert kb2d.visible(10, 35, 30, 35, s)
    assert kb2d.visible(5, 10, 15, 10, s)


def test_cell_on_the_line_sees_its_wing():
    """Точка оценки НА линии видит замеры, а не остаётся слепой.

    Сторож против самоблокировки. Пока барьер был растровой маской, луч
    начинался в барьерной ячейке, первая же проверка попадала в маску, и
    такая ячейка не видела ни одного замера из всей площади. Она уходила
    в nodata, и вдоль разлома в гриде оставалась ступенчатая щель, из
    которой потом росла лесенка полос в 1.04.
    """
    s = _segs()
    rng = np.random.default_rng(0)
    xd = rng.uniform(0.0, 40.0, 200)
    yd = rng.uniform(0.0, 40.0, 200)
    seen = kb2d.visible_mask(20.0, 10.0, xd, yd, s)
    assert seen.sum() > 0, "точка на линии не видит ни одного замера"
    # Видит она при этом не всё подряд: замеры за линией отсечены у любой
    # точки, лежащей рядом с ней.
    aside = kb2d.visible_mask(19.5, 10.0, xd, yd, s)
    assert aside.sum() < len(xd), "разлом перестал отсекать"


def test_wing_follows_the_side_of_the_point():
    """Крыло определяется стороной точки, отдельного разбиения не нужно."""
    s = _segs()
    xd = np.array([5.0, 35.0])
    yd = np.array([10.0, 10.0])
    left = kb2d.visible_mask(10.0, 10.0, xd, yd, s)
    right = kb2d.visible_mask(30.0, 10.0, xd, yd, s)
    assert left.tolist() == [True, False]
    assert right.tolist() == [False, True]


def test_measurement_on_the_line_is_seen_from_both_wings():
    """Замер, стоящий точно на разломе, виден с обеих сторон.

    Скважина на линии принадлежит обоим крыльям, отбрасывать её незачем.
    """
    s = _segs()
    xd = np.array([20.0])
    yd = np.array([10.0])
    assert kb2d.visible_mask(10.0, 10.0, xd, yd, s)[0]
    assert kb2d.visible_mask(30.0, 10.0, xd, yd, s)[0]


def test_fault_keeps_the_step_sharp():
    """Через разлом ступень не размазывается, за его концом смыкается.

    Без разлома кригинг сглаживает скачок между блоками, и это верно для
    сплошного поля. Разлом говорит, что поля два.
    """
    rng = np.random.default_rng(1)
    xd = rng.uniform(0, 40, 80)
    yd = rng.uniform(0, 40, 80)
    vrd = np.where(xd < 20, 100.0, 120.0) + rng.normal(0, 0.3, 80)
    vg = kb2d.Variogram(0.1, [dict(it=1, cc=100.0, aa=25.0, ang=0.0,
                                   anis=1.0)])
    kw = dict(vg=vg, ktype=1, skmean=0.0, ndmin=2, ndmax=16,
              rad2=40.0 ** 2, nodata=-9999.0, xmn=0.0, ymn=0.0, cell=1.0,
              nx=40, ny=40)
    plain = kb2d.build_grid(xd, yd, vrd, **kw)
    cut = kb2d.build_grid(xd, yd, vrd, fault_segs=_segs(), **kw)

    def jump(g, y):
        row = g[40 - 1 - y]
        return float(row[21]) - float(row[18])

    assert jump(cut, 10) > jump(plain, 10) + 5.0
    assert jump(cut, 10) > 15.0
    assert jump(cut, 35) < jump(cut, 10) / 2.0


def test_grid_has_no_holes_along_the_fault():
    """Вдоль разлома в гриде не остаётся ни одной пустой ячейки.

    Главный сторож этой правки. Разлом взят косым: на диагонали растровая
    маска давала ступеньки, и именно там появлялась щель.
    """
    rng = np.random.default_rng(2)
    xd = rng.uniform(0, 40, 120)
    yd = rng.uniform(0, 40, 120)
    vrd = np.where(yd > xd, 100.0, 130.0) + rng.normal(0, 0.3, 120)
    vg = kb2d.Variogram(0.1, [dict(it=1, cc=100.0, aa=25.0, ang=0.0,
                                   anis=1.0)])
    segs = kb2d.fault_segments([[(2.0, 2.0), (38.0, 38.0)]])
    grid = kb2d.build_grid(xd, yd, vrd, vg=vg, ktype=1, skmean=0.0,
                           ndmin=1, ndmax=16, rad2=40.0 ** 2,
                           nodata=-9999.0, xmn=0.0, ymn=0.0, cell=1.0,
                           nx=40, ny=40, fault_segs=segs)
    assert not (grid == -9999.0).any(), \
        "вдоль разлома остались пустые ячейки: %d" % int((grid == -9999.0).sum())


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("ok: %s" % name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL: %s - %s" % (name, exc))
    print("\n%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_run())
