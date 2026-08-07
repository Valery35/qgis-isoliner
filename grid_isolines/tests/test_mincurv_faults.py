# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Разломы в минимальной кривизне: барьер на рёбрах сетки.

Барьер живёт на рёбрах между соседними узлами, а не в ячейках. Растровая
маска барьерных ячеек, стоявшая когда-то в кригинге, давала две беды:
косая линия ложилась в сетку ступеньками, и ячейка на самой линии
оказывалась отрезана от обоих крыльев сразу. Здесь ни того, ни другого
быть не должно.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kb2d  # noqa: E402
import mincurv as mc  # noqa: E402

NX = NY = 60
CELL = 1.0
XMIN = YMIN = 0.0


def _step_data(seed=0):
    """Замеры со ступенью 30 единиц на линии x = 30."""
    rng = np.random.default_rng(seed)
    xd = rng.uniform(0.0, 60.0, 120)
    yd = rng.uniform(0.0, 60.0, 120)
    vd = np.where(xd < 30.0, 100.0, 130.0)
    return xd, yd, vd


def _vertical_fault():
    return kb2d.fault_segments([[(30.0, 0.0), (30.0, 60.0)]])


def test_edges_are_blocked_across_the_line_only():
    """Перекрыты рёбра поперёк линии и только они.

    Вертикальный разлом рассекает рёбра, идущие на восток, по одному на
    строку, и не трогает рёбра, идущие на юг: те лежат вдоль линии.
    """
    be, bs = mc.fault_edges(_vertical_fault(), XMIN, YMIN, CELL, NX, NY)
    assert int(be.sum()) == NY, "перекрыто не по одному ребру на строку"
    assert int(bs.sum()) == 0, "перекрыты рёбра вдоль линии"


def test_diagonal_fault_blocks_both_directions():
    """Косой разлом перекрывает рёбра обоих направлений.

    Это и отличает рёберный барьер от растровой маски: диагональ не
    приходится укладывать ступеньками, она рассекает рёбра как есть.
    """
    segs = kb2d.fault_segments([[(5.0, 5.0), (55.0, 55.0)]])
    be, bs = mc.fault_edges(segs, XMIN, YMIN, CELL, NX, NY)
    assert be.sum() > 0 and bs.sum() > 0
    # след идёт по диагонали, а не полосой: перекрытых рёбер порядка
    # длины линии в ячейках, а не её квадрата
    assert be.sum() + bs.sum() < 3 * NX


def test_barrier_reproduces_the_true_jump():
    """Через разлом ступень воспроизводится, без него размазывается.

    Число взято не на глаз: замеры ступенчатые ровно на 30 единиц, и
    поверхность с барьером обязана дать тот же перепад между соседними
    узлами по разные стороны линии.
    """
    xd, yd, vd = _step_data()
    z0, fixed = mc.grid_points(xd, yd, vd, XMIN, YMIN, CELL, NX, NY)
    plain, _, _ = mc.solve(z0.copy(), fixed, tension=0.0,
                           max_iter=400, tol=1e-5)
    cut, _, _ = mc.solve(z0.copy(), fixed, tension=0.0, max_iter=400,
                         tol=1e-5, fault_segs=_vertical_fault(),
                         xmin=XMIN, ymin=YMIN, cell=CELL)
    row = NY // 2
    jump_plain = float(plain[row, 30] - plain[row, 29])
    jump_cut = float(cut[row, 30] - cut[row, 29])
    assert jump_cut > 25.0, "разлом не держит ступень: %.2f" % jump_cut
    assert jump_cut > jump_plain + 15.0, (
        "с разломом ступень не резче: %.2f против %.2f"
        % (jump_cut, jump_plain))


def test_surface_stays_finite_and_within_data_range():
    """Решение не разваливается: конечные значения в пределах данных.

    Сторож против расхождения. Мембрана у линии и бигармония вдали
    сшиваются, и на стыке легко получить разгон.
    """
    xd, yd, vd = _step_data(seed=3)
    z0, fixed = mc.grid_points(xd, yd, vd, XMIN, YMIN, CELL, NX, NY)
    cut, _, _ = mc.solve(z0, fixed, tension=0.0, max_iter=400, tol=1e-5,
                         fault_segs=_vertical_fault(),
                         xmin=XMIN, ymin=YMIN, cell=CELL)
    assert np.isfinite(cut).all(), "в решении появились NaN"
    lo, hi = float(vd.min()), float(vd.max())
    span = hi - lo
    assert cut.min() > lo - span, "решение ушло далеко вниз за данные"
    assert cut.max() < hi + span, "решение ушло далеко вверх за данные"


def test_dying_fault_lets_the_surface_close_beyond_its_end():
    """За концом затухающего разлома поверхность смыкается.

    Линия не обязана рассекать площадь насквозь. Выше её конца рёбра не
    перекрыты, и перепад там обязан быть заметно меньше, чем на линии.
    """
    segs = kb2d.fault_segments([[(30.0, 0.0), (30.0, 30.0)]])
    xd, yd, vd = _step_data(seed=5)
    # ступень только там, где есть разлом
    vd = np.where((xd >= 30.0) & (yd <= 30.0), 130.0, 100.0)
    z0, fixed = mc.grid_points(xd, yd, vd, XMIN, YMIN, CELL, NX, NY)
    cut, _, _ = mc.solve(z0, fixed, tension=0.0, max_iter=400, tol=1e-5,
                         fault_segs=segs, xmin=XMIN, ymin=YMIN, cell=CELL)
    at_fault = abs(float(cut[NY - 10, 30] - cut[NY - 10, 29]))
    beyond = abs(float(cut[10, 30] - cut[10, 29]))
    assert at_fault > beyond, (
        "за концом разлома перепад не меньше, чем на нём: %.2f против %.2f"
        % (beyond, at_fault))


def test_no_faults_changes_nothing():
    """Без разломов решение прежнее до последнего знака.

    Правка не должна трогать обычный путь: 1.03 и кросс-валидация метода
    ходят через тот же solve.
    """
    xd, yd, vd = _step_data(seed=7)
    z0, fixed = mc.grid_points(xd, yd, vd, XMIN, YMIN, CELL, NX, NY)
    a, _, _ = mc.solve(z0.copy(), fixed, tension=0.2, max_iter=120, tol=1e-6)
    b, _, _ = mc.solve(z0.copy(), fixed, tension=0.2, max_iter=120, tol=1e-6,
                       fault_segs=None, xmin=XMIN, ymin=YMIN, cell=CELL)
    assert np.array_equal(a, b), "пустой список разломов изменил решение"


def test_barrier_is_watertight():
    """Через барьер влияние не протекает ни по одному пути.

    Проверка прямая: единица ставится в дальний угол одного крыла и
    разгоняется мембраной по неперекрытым рёбрам. На другом крыле обязан
    остаться ноль. Дырявый барьер это как раз то, чего не видно глазом на
    карте, но что портит счёт.
    """
    segs = kb2d.fault_segments([[(5.0, 5.0), (55.0, 55.0)]])
    be, bs = mc.fault_edges(segs, XMIN, YMIN, CELL, NX, NY)
    z = np.zeros((NY, NX))
    z[0, 0] = 1.0
    for _ in range(400):
        acc, cnt = mc._membrane(z, be, bs)
        z = np.where(cnt > 0, acc / np.maximum(cnt, 1.0), z)
        z[0, 0] = 1.0
    assert z[NY - 1, NX - 1] < 1e-9, (
        "барьер дырявый: на дальнее крыло протекло %.3g" % z[NY - 1, NX - 1])


def test_fault_through_node_centres_still_blocks():
    """Разлом точно по центрам узлов перекрывает рёбра.

    На шахтной сетке линия по оси - обычное дело. При строгом сравнении
    знаков такой разлом не перекрывал бы ничего вовсе: узел лежит на
    линии, и пересечения формально нет.
    """
    line = [(0.5, 0.0), (0.5, 60.0)]        # ровно по центрам первого столбца
    be, bs = mc.fault_edges(kb2d.fault_segments([line]),
                            XMIN, YMIN, CELL, NX, NY)
    assert int(be.sum() + bs.sum()) > 0, "разлом по центрам узлов не работает"


def test_node_on_the_line_keeps_at_least_one_neighbour():
    """Узел на самой линии не остаётся отрезанным со всех сторон.

    Иначе он застыл бы на стартовом значении, и вдоль разлома тянулся бы
    шов из несчитанных узлов.
    """
    line = [(0.5, 0.0), (0.5, 60.0)]
    be, bs = mc.fault_edges(kb2d.fault_segments([line]),
                            XMIN, YMIN, CELL, NX, NY)
    _, cnt = mc._membrane(np.zeros((NY, NX)), be, bs)
    assert (cnt > 0).all(), "часть узлов осталась без соседей"
