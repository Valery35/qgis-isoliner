# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Прореживание и скругление изолиний без пересечений. Без QGIS.

Изолинии одного грида пересекаться не могут: линия уровня L проходит там,
где поле равно L, и соседняя линия L+шаг не может пройти там же. Но
прореживание (Дуглас-Пекер) и скругление (Chaikin) обрабатывают каждую
линию отдельно и о соседях не знают. Прореживание спрямляет линию в
пределах допуска, скругление срезает углы. Там, где линии идут чаще
допуска, спрямлённая линия заходит за соседнюю. На мульде оседания с
шагом 10 мм и ячейкой 150 м линии стоят через 15 м при допуске 37 м, и
пересечений вышло шесть десятков.

Здесь оба шага проверяют себя по самому гриду. Линия уровня L держится в
коридоре значений поля: ниже середины до следующего уровня и выше
середины до предыдущего. Линия соседнего уровня держится в своём
коридоре, коридоры не перекрываются, и линии не встречаются. Спрямление
или срез угла, выводящие линию из коридора, отменяются, и там остаются
исходные вершины. На пологих участках коридор широк, и работает обычный
допуск. На крутых он узок, и линия идёт по гриду.
"""
import math

import numpy as np


class GridSampler(object):
    """Билинейное значение грида по центрам ячеек, как у gdal:contour.

    arr, valid - массив и маска значимых ячеек, gt - геотрансформ GDAL.
    Точка вне сетки центров или рядом с пустой ячейкой даёт NaN, и
    проверка коридора на ней не проходит: через пустоты линия не
    спрямляется."""

    def __init__(self, arr, valid, gt):
        self.a = np.asarray(arr, dtype=float)
        self.v = np.asarray(valid, dtype=bool)
        self.x0, self.dx = float(gt[0]), float(gt[1])
        self.y0, self.dy = float(gt[3]), float(gt[5])
        self.ny, self.nx = self.a.shape
        self.cell = min(abs(self.dx), abs(self.dy)) or 1.0

    def values(self, xs, ys):
        xs = np.asarray(xs, dtype=float)
        ys = np.asarray(ys, dtype=float)
        # индекс в сетке центров: центр ячейки (0, 0) в x0 + dx/2
        c = (xs - self.x0) / self.dx - 0.5
        r = (ys - self.y0) / self.dy - 0.5
        eps = 1e-9
        c = np.where((c < 0) & (c > -eps), 0.0, c)
        r = np.where((r < 0) & (r > -eps), 0.0, r)
        c = np.where((c > self.nx - 1) & (c < self.nx - 1 + eps),
                     self.nx - 1.0, c)
        r = np.where((r > self.ny - 1) & (r < self.ny - 1 + eps),
                     self.ny - 1.0, r)
        out = np.full(xs.shape, np.nan)
        inside = (c >= 0) & (r >= 0) & (c <= self.nx - 1) & (r <= self.ny - 1)
        if not inside.any():
            return out
        ci = c[inside]
        ri = r[inside]
        c0 = np.minimum(np.floor(ci).astype(int), max(self.nx - 2, 0))
        r0 = np.minimum(np.floor(ri).astype(int), max(self.ny - 2, 0))
        c1 = np.minimum(c0 + 1, self.nx - 1)
        r1 = np.minimum(r0 + 1, self.ny - 1)
        fc = ci - c0
        fr = ri - r0
        a, v = self.a, self.v
        ok = v[r0, c0] & v[r0, c1] & v[r1, c0] & v[r1, c1]
        val = (a[r0, c0] * (1 - fc) * (1 - fr) + a[r0, c1] * fc * (1 - fr)
               + a[r1, c0] * (1 - fc) * fr + a[r1, c1] * fc * fr)
        val = np.where(ok, val, np.nan)
        out[inside] = val
        return out

    def segments_ok(self, P, Q, lo, hi):
        """То же для пачки отрезков P[i]-Q[i], массивом булевых."""
        P = np.asarray(P, dtype=float).reshape(-1, 2)
        Q = np.asarray(Q, dtype=float).reshape(-1, 2)
        if not len(P):
            return np.zeros(0, dtype=bool)
        length = np.hypot(Q[:, 0] - P[:, 0], Q[:, 1] - P[:, 1])
        n = int(min(64, max(3, math.ceil(float(length.max())
                                         / (0.25 * self.cell)) + 1)))
        t = np.linspace(0.0, 1.0, n)[None, :]
        xs = P[:, :1] + (Q[:, :1] - P[:, :1]) * t
        ys = P[:, 1:] + (Q[:, 1:] - P[:, 1:]) * t
        vals = self.values(xs, ys)
        good = (vals > lo) & (vals < hi)
        return good.all(axis=1)

    def segment_ok(self, p, q, lo, hi):
        """Весь отрезок p-q лежит в коридоре значений (lo, hi)."""
        length = math.hypot(q[0] - p[0], q[1] - p[1])
        n = max(3, int(math.ceil(length / (0.25 * self.cell))) + 1)
        t = np.linspace(0.0, 1.0, n)
        vals = self.values(p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)
        if np.isnan(vals).any():
            return False
        return bool(((vals > lo) & (vals < hi)).all())


def corridor(level, levels, step=0.0):
    """Коридор значений для линии уровня level.

    levels - все уровни набора. Граница коридора лежит посередине до
    соседнего уровня. Если соседа с какой-то стороны нет, берётся половина
    шага, а без шага сторона открыта."""
    lv = sorted(set(float(x) for x in levels)) if levels else []
    below = [x for x in lv if x < level - 1e-12]
    above = [x for x in lv if x > level + 1e-12]
    half = 0.5 * float(step) if step and step > 0 else None
    if below:
        lo = 0.5 * (level + below[-1])
    elif half:
        lo = level - half
    else:
        lo = -math.inf
    if above:
        hi = 0.5 * (level + above[0])
    elif half:
        hi = level + half
    else:
        hi = math.inf
    return lo, hi


def _dp_open(pts, tol, ok):
    """Дуглас-Пекер с проверкой коридора. Концы сохраняются."""
    n = len(pts)
    if n <= 2:
        return list(pts), 0
    arr = np.asarray(pts, dtype=float)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    kept_for_corridor = 0
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = pts[i], pts[j]
        seg = arr[i + 1:j]
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L > 0.0:
            d = np.abs(dx * (a[1] - seg[:, 1]) - dy * (a[0] - seg[:, 0])) / L
        else:
            d = np.hypot(seg[:, 0] - a[0], seg[:, 1] - a[1])
        m = int(np.argmax(d))
        dmax, k = float(d[m]), i + 1 + m
        if dmax > tol:
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
        elif not ok(a, b):
            kept_for_corridor += 1
            keep[k] = True
            stack.append((i, k))
            stack.append((k, j))
    return [p for p, f in zip(pts, keep) if f], kept_for_corridor


def simplify_in_corridor(pts, tol, ok):
    """Прореживание линии с допуском tol, не выходящее из коридора.

    ok(p, q) - проверка отрезка. Замкнутое кольцо (первая точка равна
    последней) делится на две половины по самой дальней от начала
    вершине, чтобы кольцо не схлопнулось в отрезок. Возвращает
    (точки, сколько раз коридор запретил спрямление)."""
    pts = [tuple(p) for p in pts]
    if len(pts) < 3 or tol <= 0:
        return pts, 0
    closed = pts[0] == pts[-1]
    if not closed:
        return _dp_open(pts, tol, ok)
    if len(pts) < 5:
        return pts, 0
    p0 = pts[0]
    far = max(range(1, len(pts) - 1),
              key=lambda m: (pts[m][0] - p0[0]) ** 2 + (pts[m][1] - p0[1]) ** 2)
    a, na = _dp_open(pts[:far + 1], tol, ok)
    b, nb = _dp_open(pts[far:], tol, ok)
    ring = a + b[1:]
    if len(ring) < 4:
        return pts, 0
    return ring, na + nb


def chaikin_in_corridor(pts, iterations, ok, offset=0.25, ok_many=None):
    """Скругление Chaikin, срез угла только внутри коридора.

    Новые точки Chaikin лежат на исходных отрезках, поэтому проверять
    надо только сам срез угла. Если срез выходит из коридора, угол
    остаётся: вместо двух точек среза ставится исходная вершина между
    ними. Концы открытой линии не двигаются, как у native:smoothgeometry.
    ok_many(Q, R) - пачечная проверка срезов, ускоряет длинные линии; без
    неё срезы проверяются по одному через ok.
    Возвращает (точки, сколько углов оставлено)."""
    pts = [tuple(p) for p in pts]
    kept = 0
    for _ in range(int(iterations)):
        if len(pts) < 3:
            break
        closed = pts[0] == pts[-1]
        ring = np.asarray(pts[:-1] if closed else pts, dtype=float)
        n = len(ring)
        if n < 3:
            break
        if closed:
            cur = ring
            prv = np.roll(ring, 1, axis=0)
            nxt = np.roll(ring, -1, axis=0)
        else:
            cur = ring[1:-1]
            prv = ring[:-2]
            nxt = ring[2:]
        q = cur + (prv - cur) * offset
        r = cur + (nxt - cur) * offset
        if ok_many is not None:
            good = np.asarray(ok_many(q, r), dtype=bool)
        else:
            good = np.array([bool(ok(tuple(a), tuple(b)))
                             for a, b in zip(q, r)], dtype=bool)
        kept += int((~good).sum())
        m = len(cur)
        out = np.empty((2 * m, 2))
        out[0::2] = np.where(good[:, None], q, cur)
        out[1::2] = np.where(good[:, None], r, cur)
        # на оставленном углу обе точки совпали с вершиной: одну снять
        drop = np.zeros(2 * m, dtype=bool)
        drop[1::2] = ~good
        out = out[~drop]
        res = [tuple(p) for p in out.tolist()]
        if closed:
            res.append(res[0])
        else:
            res = [tuple(ring[0])] + res + [tuple(ring[-1])]
        pts = res
    return pts, kept


def outline_segments(lines):
    """Отрезки контура области из списка ломаных [[(x, y), ...], ...]:
    массивы начал и концов для nearest_on_outline."""
    a, b = [], []
    for pts in lines:
        for k in range(len(pts) - 1):
            a.append(pts[k])
            b.append(pts[k + 1])
    return np.asarray(a, dtype=float).reshape(-1, 2), \
        np.asarray(b, dtype=float).reshape(-1, 2)


def nearest_on_outline(x, y, seg_a, seg_b):
    """Ближайшая к (x, y) точка контура и расстояние до неё."""
    if not len(seg_a):
        return None, math.inf
    d = seg_b - seg_a
    L2 = (d * d).sum(axis=1)
    L2 = np.where(L2 > 0, L2, 1.0)
    t = ((x - seg_a[:, 0]) * d[:, 0] + (y - seg_a[:, 1]) * d[:, 1]) / L2
    t = np.clip(t, 0.0, 1.0)
    fx = seg_a[:, 0] + d[:, 0] * t
    fy = seg_a[:, 1] + d[:, 1] * t
    dist = np.hypot(fx - x, fy - y)
    k = int(np.argmin(dist))
    return (float(fx[k]), float(fy[k])), float(dist[k])


def extend_ends_to_outline(parts, seg_a, seg_b, tol):
    """Довести открытые концы изолиний до контура области.

    Изолиния из грида кончается на центрах крайних ячеек, а контур
    области проходит по их внешним краям, на полячейки дальше. Раньше
    конец переносился на контур (native:snapgeometries), и последний
    отрезок менял направление. Там, где у края грида линии стоят часто,
    перенесённые концы заходили друг за друга. Теперь конец остаётся на
    месте, а к нему добавляется короткий отрезок до ближайшей точки
    контура: у прямого края такие отрезки параллельны и не пересекаются.
    Замкнутые кольца не трогаются. Возвращает (части, сколько концов
    доведено)."""
    out = []
    n = 0
    for part in parts:
        pts = [tuple(p) for p in part]
        if len(pts) >= 2 and pts[0] != pts[-1]:
            for at in (0, -1):
                foot, dist = nearest_on_outline(pts[at][0], pts[at][1],
                                                seg_a, seg_b)
                if foot is None or dist <= 1e-9 or dist > tol:
                    continue
                if at == 0:
                    pts.insert(0, foot)
                else:
                    pts.append(foot)
                n += 1
        out.append(pts)
    return out, n
