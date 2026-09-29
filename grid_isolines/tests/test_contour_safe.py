# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Прореживание и скругление изолиний в коридоре значений (contour_safe).

Найдено на мульде оседания: минимальная кривизна с ячейкой 150 м, шаг
изолиний 10 мм. Линии на борту мульды шли через 15 м, прореживание с
допуском в четверть ячейки (37 м) спрямляло каждую сквозь соседнюю, и
изолинии пересеклись в шести десятках мест. Скругление Chaikin добавляло
своих пересечений.

Модель здесь - концентрические окружности через 2 м, допуск 8 м. Без
коридора спрямлённая окружность срезает хордой соседнюю. С коридором
пересечений нет, а прореживание там, где линии редки, работает как было.

Запуск:  python grid_isolines/tests/test_contour_safe.py
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from contour_safe import (GridSampler, corridor,  # noqa: E402
                          simplify_in_corridor, chaikin_in_corridor,
                          outline_segments, extend_ends_to_outline)

CELL = 10.0
N = 80
CX = CY = N * CELL / 2.0


def _grid():
    x = (np.arange(N) + 0.5) * CELL
    y = N * CELL - (np.arange(N) + 0.5) * CELL
    X, Y = np.meshgrid(x, y)
    z = np.hypot(X - CX, Y - CY)
    gt = (0.0, CELL, 0.0, N * CELL, 0.0, -CELL)
    return z, gt


def _circle(r, n=None):
    # у каждой окружности своё число вершин и свой сдвиг начала, как у
    # контуров из грида: одинаковые окружности упрощались бы подобно
    n = n or 300 + int(r) % 37 * 3
    t = np.linspace(0.0, 2 * math.pi, n, endpoint=False) + (r * 0.37) % 1.0
    pts = [(CX + r * math.cos(a), CY + r * math.sin(a)) for a in t]
    return pts + [pts[0]]


def _crosses(l1, l2):
    a1 = np.asarray(l1[:-1]); b1 = np.asarray(l1[1:])
    a2 = np.asarray(l2[:-1]); b2 = np.asarray(l2[1:])

    def orient(p, q, r):
        return ((q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1])
                - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0]))
    P, Q = a1[:, None, :], b1[:, None, :]
    R, S = a2[None, :, :], b2[None, :, :]
    return bool(((orient(P, Q, R) * orient(P, Q, S) < 0)
                 & (orient(R, S, P) * orient(R, S, Q) < 0)).any())


def _count(lines):
    n = 0
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            if _crosses(lines[i][1], lines[j][1]):
                n += 1
    return n


def _run(ok_factory, tol=8.0, iters=0):
    z, gt = _grid()
    s = GridSampler(z, np.ones_like(z, bool), gt)
    levels = [200.0 + 2.0 * k for k in range(12)]
    out = []
    for L in levels:
        ok, many = ok_factory(s, L, levels)
        pts, _ = simplify_in_corridor(_circle(L), tol, ok)
        pts, _ = chaikin_in_corridor(pts, iters, ok, ok_many=many)
        out.append((L, pts))
    return out


def _naive(s, L, levels):
    return (lambda p, q: True), None


def _safe(s, L, levels):
    lo, hi = corridor(L, levels, 2.0)
    return ((lambda p, q: s.segment_ok(p, q, lo, hi)),
            (lambda P, Q: s.segments_ok(P, Q, lo, hi)))


def test_naive_thinning_crosses():
    """Без коридора модель воспроизводит дефект: иначе тест ничего не
    доказывает."""
    assert _count(_run(_naive)) > 0


def test_corridor_prevents_crossings():
    assert _count(_run(_safe)) == 0
    assert _count(_run(_safe, iters=2)) == 0


def test_chaikin_cut_stays_off_the_neighbour():
    """Срез угла Chaikin заходит за соседнюю линию, если та идёт ближе
    четверти отрезка. Поле min(10 - x, y): уровень 0 - угол в (10, 0),
    уровень 1 - угол в (9, 1). Срез угла нулевой линии проходит выше
    угла соседней."""
    c = 0.25
    n = 64
    x = (np.arange(n) + 0.5) * c - 2.0
    y = n * c - (np.arange(n) + 0.5) * c - 2.0
    X, Y = np.meshgrid(x, y)
    z = np.minimum(10.0 - X, Y)
    s = GridSampler(z, np.ones_like(z, bool), (-2.0, c, 0.0, n * c - 2.0,
                                               0.0, -c))
    a = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    b = [(0.0, 1.0), (9.0, 1.0), (9.0, 10.0)]
    a1, _ = chaikin_in_corridor(a, 1, lambda p, q: True)
    assert _crosses(a1, b)
    lo, hi = corridor(0.0, [0.0, 1.0])
    a2, kept = chaikin_in_corridor(a, 1, lambda p, q: s.segment_ok(p, q, lo, hi))
    assert kept == 1 and not _crosses(a2, b)


def test_corridor_still_thins_where_lines_are_sparse():
    """Одна окружность без соседей: коридор широк, работает допуск."""
    z, gt = _grid()
    s = GridSampler(z, np.ones_like(z, bool), gt)
    lo, hi = corridor(250.0, [150.0, 250.0, 350.0])
    pts, held = simplify_in_corridor(
        _circle(250.0), 8.0, lambda p, q: s.segment_ok(p, q, lo, hi))
    assert len(pts) < 60, len(pts)
    assert pts[0] == pts[-1] and len(pts) >= 4


def test_nodata_blocks_straightening():
    """Через пустые ячейки линия не спрямляется."""
    z, gt = _grid()
    valid = np.ones_like(z, bool)
    valid[:, N // 2 - 2:N // 2 + 2] = False
    s = GridSampler(z, valid, gt)
    assert not s.segment_ok((CX - 100, CY + 150), (CX + 100, CY + 150),
                            -1e9, 1e9)
    assert s.segment_ok((CX - 300, CY + 150), (CX - 200, CY + 150), -1e9, 1e9)
    assert math.isnan(float(s.values([-50.0], [10.0])[0]))


def test_corridor_bounds():
    assert corridor(10.0, [0.0, 10.0, 20.0]) == (5.0, 15.0)
    assert corridor(10.0, [0.0, 10.0, 30.0]) == (5.0, 20.0)
    assert corridor(0.0, [0.0, 10.0], 10.0) == (-5.0, 5.0)
    lo, hi = corridor(0.0, [0.0])
    assert lo == -math.inf and hi == math.inf


def test_chaikin_keeps_ends_and_rings():
    line = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (20.0, 10.0)]
    pts, kept = chaikin_in_corridor(line, 2, lambda p, q: True)
    assert pts[0] == line[0] and pts[-1] == line[-1] and kept == 0
    ring = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    pts, _ = chaikin_in_corridor(ring, 1, lambda p, q: True)
    assert pts[0] == pts[-1] and len(pts) == 9
    # запрет среза оставляет угол на месте
    pts, kept = chaikin_in_corridor(line, 1, lambda p, q: False)
    assert pts == line and kept == 2


def test_batch_check_matches_single():
    z, gt = _grid()
    s = GridSampler(z, np.ones_like(z, bool), gt)
    rng = np.random.default_rng(1)
    P = rng.uniform(50, 750, (40, 2))
    Q = P + rng.uniform(-40, 40, (40, 2))
    lo, hi = 200.0, 300.0
    many = s.segments_ok(P, Q, lo, hi)
    one = [s.segment_ok(tuple(p), tuple(q), lo, hi) for p, q in zip(P, Q)]
    assert list(many) == one


def test_ends_reach_outline_without_crossing():
    """Концы у края грида доводятся до контура отрезком, конец на месте.

    Прежний перенос конца на контур менял направление последнего отрезка,
    и частые линии у края заходили друг за друга. Здесь две линии
    подходят к краю x = 100 косо и близко: перенос конца скрестил бы их,
    добавленные отрезки параллельны."""
    outline = [[(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0),
                (0.0, 0.0)]]
    a, b = outline_segments(outline)
    l1 = [(60.0, 40.0), (95.0, 50.0)]
    l2 = [(60.0, 52.0), (95.0, 51.0)]
    (p1,), n1 = extend_ends_to_outline([l1], a, b, 10.0)
    (p2,), n2 = extend_ends_to_outline([l2], a, b, 10.0)
    assert n1 == n2 == 1
    assert p1[:2] == l1 and p1[-1] == (100.0, 50.0)
    assert p2[-1] == (100.0, 51.0)
    assert not _crosses(p1, p2)
    # далеко от контура и замкнутое кольцо не трогаются
    far = [(40.0, 40.0), (60.0, 60.0)]
    ring = [(40.0, 40.0), (60.0, 40.0), (50.0, 60.0), (40.0, 40.0)]
    parts, n = extend_ends_to_outline([far, ring], a, b, 10.0)
    assert n == 0 and parts == [far, ring]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
