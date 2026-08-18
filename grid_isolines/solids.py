# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Тела из поясов: замкнутая оболочка на каждый пояс.

Пояс между изолиниями сам по себе поверхность: у него есть контур, но
нет ни объёма, ни низа. Для объёма, для обрезки сцены с закрытым срезом
и для программ, понимающих только замкнутые оболочки, нужен другой вид
вывода: крышка сверху, крышка снизу и боковые стенки по всем кольцам,
включая дыры.

Правило замкнутости одно и от способа сборки не зависит: каждое ребро
оболочки принадлежит ровно двум граням. Стенка кольца даёт по ребру
вверх и вниз, крышки замыкают их сверху и снизу, а внутреннее кольцо
получает свою стенку наравне с внешним - иначе оболочка не замкнётся
вокруг отверстия.

Модуль чистый: numpy и только. QGIS нужен лишь тому, кто складывает
результат в слой.
"""

import numpy as np


def _closed(ring):
    """Кольцо без повтора последней вершины."""
    pts = [tuple(map(float, p[:2])) for p in ring]
    if len(pts) >= 2 and abs(pts[0][0] - pts[-1][0]) < 1e-12 \
            and abs(pts[0][1] - pts[-1][1]) < 1e-12:
        pts = pts[:-1]
    return pts


def ring_area(ring):
    """Площадь кольца со знаком: против часовой положительная."""
    pts = _closed(ring)
    if len(pts) < 3:
        return 0.0
    a = np.asarray(pts, dtype=float)
    x, y = a[:, 0], a[:, 1]
    return float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y) / 2.0)


def wall_faces(ring, z_lo, z_hi):
    """Стенка кольца: по четырёхугольнику на каждое ребро.

    Четырёхугольник берётся в порядке низ-низ-верх-верх, поэтому обход
    у соседних граней встречный и каждое вертикальное ребро попадает
    ровно в две грани.
    """
    pts = _closed(ring)
    if len(pts) < 3 or z_hi == z_lo:
        return []
    out = []
    n = len(pts)
    for i in range(n):
        (ax, ay), (bx, by) = pts[i], pts[(i + 1) % n]
        if abs(ax - bx) < 1e-12 and abs(ay - by) < 1e-12:
            continue
        out.append([[(ax, ay, z_lo), (bx, by, z_lo),
                     (bx, by, z_hi), (ax, ay, z_hi), (ax, ay, z_lo)]])
    return out


def cap_faces(rings, z, flip=False, triangulate=None):
    """Крышка на отметке z: полигон с дырами или его треугольники.

    Без разбивки крышка отдаётся одним полигоном с внутренними кольцами.
    Замкнутости это не мешает: рёбра колец всё равно попадают в стенки,
    и каждое ребро оболочки остаётся в двух гранях. Разбивка нужна тем
    потребителям, кто не умеет вогнутый контур с дырами, и включается
    передачей `triangulate`.
    """
    if triangulate is not None:
        tris = triangulate(rings) or []
        out = []
        for t in tris:
            pts = [(float(x), float(y), float(z)) for x, y in t[:3]]
            if flip:
                pts = pts[::-1]
            out.append([pts + [pts[0]]])
        return out
    part = []
    for k, ring in enumerate(rings):
        pts = _closed(ring)
        if len(pts) < 3:
            continue
        # внешнее кольцо крышки обходится в одну сторону, дыры в другую;
        # у нижней крышки обход встречный к верхней
        area = ring_area(pts)
        want_ccw = (k == 0) != bool(flip)
        if (area > 0) != want_ccw:
            pts = pts[::-1]
        part.append([(x, y, float(z)) for x, y in pts] + [(pts[0][0],
                                                           pts[0][1],
                                                           float(z))])
    return [part] if part else []


def shell_faces(rings, z_lo, z_hi, triangulate=None):
    """Оболочка пояса: нижняя крышка, верхняя и стенки по всем кольцам.

    `rings` - кольца пояса в плане, первое внешнее, остальные дыры.
    Возвращает список частей, каждая часть это список колец из точек
    (x, y, z). В таком виде она ложится в MULTIPOLYGON Z без потерь.
    """
    z_lo, z_hi = float(z_lo), float(z_hi)
    if z_hi < z_lo:
        z_lo, z_hi = z_hi, z_lo
    rings = [r for r in rings if len(_closed(r)) >= 3]
    if not rings or z_hi - z_lo <= 0:
        return []
    out = []
    out.extend(cap_faces(rings, z_lo, flip=True, triangulate=triangulate))
    out.extend(cap_faces(rings, z_hi, flip=False, triangulate=triangulate))
    for ring in rings:
        out.extend(wall_faces(ring, z_lo, z_hi))
    return out


def edge_report(parts, snap=1e-4):
    """Счёт рёбер оболочки: (граней, рёбер, висячих, кратных).

    Тот же критерий, что в `tools/check_solids.py`: в замкнутой оболочке
    каждое ребро принадлежит ровно двум граням. Здесь он нужен тестам и
    диагностике, чтобы не собирать оболочку вслепую.
    """
    def key(p):
        return (round(p[0] / snap), round(p[1] / snap), round(p[2] / snap))

    edges = {}
    n_faces = 0
    for part in parts:
        for ring in part:
            pts = list(ring)
            if len(pts) >= 2 and key(pts[0]) == key(pts[-1]):
                pts = pts[:-1]
            if len(pts) < 3:
                continue
            n_faces += 1
            keys = [key(p) for p in pts]
            for i in range(len(keys)):
                a, b = keys[i], keys[(i + 1) % len(keys)]
                if a == b:
                    continue
                e = (a, b) if a < b else (b, a)
                edges[e] = edges.get(e, 0) + 1
    loose = sum(1 for n in edges.values() if n == 1)
    many = sum(1 for n in edges.values() if n > 2)
    return n_faces, len(edges), loose, many


def volume(parts):
    """Объём замкнутой оболочки по теореме о дивергенции.

    Годится для проверки: у пояса с крышками объём равен площади в плане
    на мощность, и всякое расхождение означает потерянную или лишнюю
    грань.
    """
    total = 0.0
    for part in parts:
        for ring in part:
            pts = [p for p in ring]
            if len(pts) >= 2 and pts[0] == pts[-1]:
                pts = pts[:-1]
            if len(pts) < 3:
                continue
            o = pts[0]
            for i in range(1, len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                ux, uy, uz = a[0] - o[0], a[1] - o[1], a[2] - o[2]
                vx, vy, vz = b[0] - o[0], b[1] - o[1], b[2] - o[2]
                nx = uy * vz - uz * vy
                ny = uz * vx - ux * vz
                nz = ux * vy - uy * vx
                total += (o[0] * nx + o[1] * ny + o[2] * nz) / 6.0
    return abs(total)
