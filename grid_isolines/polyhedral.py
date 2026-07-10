# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Полиэдральные поверхности: примитивы, тело пласта и сборка WKT.

Чистый NumPy, без импорта QGIS - модуль проверяется headless-тестами
(tests/test_polyhedral.py). Строит грани (patch) как замкнутые кольца
вершин (x, y, z) и собирает из них WKT одного из типов:

    POLYHEDRALSURFACE Z (((...)), ((...)), ...)
    TIN Z              (((...)), ((...)), ...)   - только треугольники
    MULTIPOLYGON Z     (((...)), ((...)), ...)   - запасной вывод

Тело кольцевого типа собирается из пары поверхностей той же функцией
mesh3d.bed_to_mesh_arrays, что и боевой экспорт: кровля, перевёрнутая
подошва и боковая юбка образуют водонепроницаемую оболочку.
"""
import numpy as np

from .mesh3d import bed_to_mesh_arrays


# --- формат координат -----------------------------------------------------

def _fmt(v, nd=3):
    """Компактная запись координаты: округление и отсечение хвостов."""
    x = round(float(v), nd)
    if x == 0.0:
        x = 0.0  # убрать -0.0
    return f"{x:g}"


def ring_wkt(ring):
    """Кольцо вершин [(x, y, z), ...] -> «(x y z, x y z, ...)», замыкается."""
    pts = [tuple(p) for p in ring]
    if pts and pts[0] != pts[-1]:
        pts = pts + [pts[0]]
    inner = ", ".join(f"{_fmt(x)} {_fmt(y)} {_fmt(z)}" for x, y, z in pts)
    return "(" + inner + ")"


def patches_to_wkt(patches, kind="POLYHEDRALSURFACE"):
    """Список граней (каждая - одно внешнее кольцо) -> WKT.

    kind: POLYHEDRALSURFACE | TIN | MULTIPOLYGON. Тело WKT одинаково,
    различается только ключевое слово.
    """
    k = kind.upper()
    if k not in ("POLYHEDRALSURFACE", "TIN", "MULTIPOLYGON"):
        raise ValueError("kind must be POLYHEDRALSURFACE, TIN or MULTIPOLYGON")
    if not patches:
        return f"{k} Z EMPTY"
    body = ", ".join("(" + ring_wkt(r) + ")" for r in patches)
    return f"{k} Z ({body})"


# --- перевод треугольного меша в грани ------------------------------------

def tris_to_patches(verts, faces):
    """(verts (N,3), faces (M,3) с нулевой базой) -> список треугольных
    колец [[(x,y,z) x3], ...]."""
    v = np.asarray(verts, dtype=float)
    f = np.asarray(faces, dtype=np.int64)
    out = []
    for tri in f:
        out.append([tuple(v[tri[0]]), tuple(v[tri[1]]), tuple(v[tri[2]])])
    return out


def mesh_to_wkt(verts, faces, kind="POLYHEDRALSURFACE"):
    """Треугольный меш -> WKT выбранного типа."""
    return patches_to_wkt(tris_to_patches(verts, faces), kind)


# --- разбор WKT обратно в треугольники (для 3D-просмотра) ------------------

def _rings_from_wkt(wkt):
    """Внутренние группы координат (кольца без вложенных скобок).

    В нашем WKT каждая грань записана как ((кольцо)), поэтому самая
    внутренняя пара скобок - это одно внешнее кольцо грани. Предполагаем
    грани без дыр (так строит этот инструмент).
    """
    import re
    return re.findall(r"\(([^()]+)\)", wkt or "")


def _ring_points(ring_str):
    pts = []
    for tok in ring_str.split(","):
        parts = tok.split()
        if len(parts) < 2:
            continue
        x = float(parts[0])
        y = float(parts[1])
        z = float(parts[2]) if len(parts) >= 3 else 0.0
        pts.append((x, y, z))
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return pts


def wkt_to_tris(wkt):
    """WKT (POLYHEDRALSURFACE / TIN / MULTIPOLYGON / POLYGON, с Z) ->
    (verts (N,3), faces (M,3)). Каждое кольцо веерно триангулируется.

    Чистый разбор строки, без QGIS: годится для 3D-просмотра тел,
    собранных этим инструментом. Возвращает пустые массивы для EMPTY.
    """
    verts, faces = [], []
    if not wkt or "EMPTY" in wkt.upper():
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64)
    for ring_str in _rings_from_wkt(wkt):
        pts = _ring_points(ring_str)
        if len(pts) < 3:
            continue
        base = len(verts)
        verts.extend(pts)
        for i in range(1, len(pts) - 1):
            faces.append((base, base + i, base + i + 1))
    v = np.asarray(verts, dtype=float) if verts else np.zeros((0, 3))
    f = (np.asarray(faces, dtype=np.int64) if faces
         else np.zeros((0, 3), dtype=np.int64))
    return v, f


def z_range(patches):
    """(zmin, zmax) по всем вершинам граней. Пустой список -> (0, 0)."""
    zs = [float(p[2]) for ring in patches for p in ring if len(p) >= 3]
    return (min(zs), max(zs)) if zs else (0.0, 0.0)


def slice_triangles(verts, faces, p0, normal):
    """Пересечение треугольного меша с плоскостью (точка p0, нормаль).

    Для каждого треугольника, чьи вершины лежат по разные стороны
    плоскости, находит две точки на рёбрах и даёт отрезок следа. Возвращает
    массив сегментов (M, 2, 3). Чистый NumPy, headless-тест.
    """
    v = np.asarray(verts, dtype=float)
    f = np.asarray(faces, dtype=np.int64)
    p0 = np.asarray(p0, dtype=float)
    n = np.asarray(normal, dtype=float)
    nn = float(np.linalg.norm(n))
    if nn == 0.0 or len(f) == 0:
        return np.zeros((0, 2, 3))
    n = n / nn
    d = (v - p0) @ n
    segs = []
    for tri in f:
        dd = d[tri]
        pts = []
        for a, b in ((0, 1), (1, 2), (2, 0)):
            da, db = dd[a], dd[b]
            if (da > 0.0) != (db > 0.0):  # ребро пересекает плоскость
                t = da / (da - db)
                pa, pb = v[tri[a]], v[tri[b]]
                pts.append(pa + t * (pb - pa))
        if len(pts) == 2:
            segs.append([pts[0], pts[1]])
    return np.asarray(segs, dtype=float) if segs else np.zeros((0, 2, 3))


# --- аудит замкнутости -----------------------------------------------------

def edge_audit(patches):
    """Считает неориентированные рёбра по внешним кольцам граней.

    Возвращает (n_edges, n_open): n_open - количество рёбер, встречающихся
    не ровно дважды. Водонепроницаемая оболочка даёт n_open == 0.
    """
    from collections import Counter
    c = Counter()
    for ring in patches:
        pts = [tuple(round(float(x), 6) for x in p) for p in ring]
        if pts and pts[0] == pts[-1]:
            pts = pts[:-1]
        m = len(pts)
        for i in range(m):
            a, b = pts[i], pts[(i + 1) % m]
            c[frozenset((a, b))] += 1
    n_open = sum(1 for v in c.values() if v != 2)
    return len(c), n_open


def is_watertight(patches):
    return edge_audit(patches)[1] == 0


# --- примитивы -------------------------------------------------------------

def cube(cx=0.0, cy=0.0, cz=0.0, sx=100.0, sy=100.0, sz=40.0):
    """Куб (параллелепипед) как 6 четырёхугольных граней с внешними
    нормалями. Возвращает список граней (колец)."""
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    # 8 вершин
    x0, x1 = cx - hx, cx + hx
    y0, y1 = cy - hy, cy + hy
    z0, z1 = cz - hz, cz + hz
    v = {
        "a": (x0, y0, z0), "b": (x1, y0, z0), "c": (x1, y1, z0), "d": (x0, y1, z0),
        "e": (x0, y0, z1), "f": (x1, y0, z1), "g": (x1, y1, z1), "h": (x0, y1, z1),
    }
    # грани CCW при взгляде снаружи
    faces = [
        ["a", "d", "c", "b"],  # низ (нормаль -Z)
        ["e", "f", "g", "h"],  # верх (нормаль +Z)
        ["a", "b", "f", "e"],  # -Y
        ["b", "c", "g", "f"],  # +X
        ["c", "d", "h", "g"],  # +Y
        ["d", "a", "e", "h"],  # -X
    ]
    return [[v[k] for k in face] for face in faces]


def tetrahedron(cx=0.0, cy=0.0, cz=0.0, size=100.0):
    """Тетраэдр как 4 треугольные грани (это же валидный TIN)."""
    s = size
    p0 = (cx - s / 2, cy - s / 3, cz - s / 4)
    p1 = (cx + s / 2, cy - s / 3, cz - s / 4)
    p2 = (cx, cy + 2 * s / 3, cz - s / 4)
    p3 = (cx, cy, cz + 3 * s / 4)
    return [
        [p0, p2, p1],
        [p0, p1, p3],
        [p1, p2, p3],
        [p2, p0, p3],
    ]


def bed_body(nx=8, ny=8, size=200.0, x0=0.0, y0=0.0,
             base=100.0, thickness=25.0, amp=18.0, tilt=0.15):
    """Пример тела пласта: гладкая складчатая кровля, подошва = кровля минус
    мощность, боковая юбка. Собирается тем же mesh3d.bed_to_mesh_arrays,
    что и боевой экспорт, поэтому оболочка водонепроницаема.

    Возвращает (patches, verts, faces): patches - треугольные грани для WKT,
    verts/faces - исходный меш (для контроля).
    """
    nx = max(2, int(nx))
    ny = max(2, int(ny))
    cell = float(size) / max(nx - 1, 1)
    # geotransform: origin в левом-верхнем углу, шаг ячейки, y вниз
    gt = (float(x0), cell, 0.0, float(y0) + (ny - 1) * cell, 0.0, -cell)
    j = np.arange(nx)
    i = np.arange(ny)
    X = x0 + j * cell
    Y = y0 + i * cell
    XX, YY = np.meshgrid(X, Y)
    kx = 2.0 * np.pi / max(size, 1e-9)
    top = (base
           + amp * np.sin(kx * (XX - x0)) * np.cos(kx * (YY - y0))
           + tilt * (XX - x0))
    bot = top - float(thickness)
    verts, faces = bed_to_mesh_arrays(top, bot, gt)
    return tris_to_patches(verts, faces), verts, faces


def folded_bed(nx=20, ny=20, size=200.0, x0=0.0, y0=0.0,
               base=100.0, thickness=25.0, amp=None, folds=2, tilt=0.08):
    """Складчатый пласт: пологая кровля-фолд-трейн из антиклиналей и
    синклиналей вдоль X с лёгким погружением. Подошва = кровля минус
    мощность, оболочка водонепроницаема (bed_to_mesh_arrays)."""
    nx = max(2, int(nx))
    ny = max(2, int(ny))
    if amp is None:
        amp = max(float(thickness) * 0.8, float(size) * 0.045)
    cell = float(size) / max(nx - 1, 1)
    gt = (float(x0), cell, 0.0, float(y0) + (ny - 1) * cell, 0.0, -cell)
    X = x0 + np.arange(nx) * cell
    Y = y0 + np.arange(ny) * cell
    XX, YY = np.meshgrid(X, Y)
    kx = 2.0 * np.pi * float(folds) / max(size, 1e-9)
    ky = 2.0 * np.pi / max(size, 1e-9)
    top = (base
           + amp * np.sin(kx * (XX - x0))
           + 0.15 * amp * np.sin(2.0 * kx * (XX - x0))
           + 0.12 * amp * np.cos(ky * (YY - y0))
           + tilt * (XX - x0))
    bot = top - float(thickness)
    verts, faces = bed_to_mesh_arrays(top, bot, gt)
    return tris_to_patches(verts, faces), verts, faces


def suite_beds(n=3, nx=8, ny=8, size=200.0, x0=0.0, y0=0.0,
               base=100.0, thickness=25.0, gap=None, folds=3, tilt=0.1):
    """Свита складчатых пластов по отдельности: список из n наборов граней,
    снизу вверх с зазором. base - отметка залегания нижнего пласта. Каждый
    пласт - складчатый (folded_bed), водонепроницаем."""
    n = max(1, int(n))
    if gap is None:
        gap = float(thickness) * 0.6
    step = float(thickness) + float(gap)
    beds = []
    for k in range(n):
        floor_k = float(base) + k * step
        patches, _, _ = folded_bed(
            nx=max(int(nx), 20), ny=max(int(nx), 20), size=size,
            x0=x0, y0=y0, base=floor_k + float(thickness),
            thickness=thickness, folds=folds, tilt=tilt)
        beds.append(patches)
    return beds


def suite(n=3, **kw):
    """Свита одним объединённым набором граней (для round-trip и совместимости).
    Каждая оболочка замкнута отдельно, аудит рёбер даёт n_open == 0."""
    out = []
    for bed in suite_beds(n=n, **kw):
        out.extend(bed)
    return out


# --- фасад для инструмента -------------------------------------------------

EXAMPLES = ("bed", "fold", "suite", "cube", "tetra")


def build_example(kind="bed", as_tin=False, **kw):
    """Единая точка входа: возвращает (patches, wkt_kind, meta).

    kind: bed | cube | tetra. as_tin принудительно триангулирует и просит
    тип TIN (для куба режет четырёхугольники на треугольники). meta - словарь
    с полями name, patches, watertight.
    """
    if kind == "cube":
        patches = cube(**{k: v for k, v in kw.items()
                          if k in ("cx", "cy", "cz", "sx", "sy", "sz")})
        name = "cube"
    elif kind == "tetra":
        patches = tetrahedron(**{k: v for k, v in kw.items()
                                 if k in ("cx", "cy", "cz", "size")})
        name = "tetrahedron"
    elif kind == "bed":
        patches, _, _ = bed_body(**{k: v for k, v in kw.items()
                                    if k in ("nx", "ny", "size", "x0", "y0",
                                             "base", "thickness", "amp",
                                             "tilt")})
        name = "bed_body"
    elif kind == "fold":
        patches, _, _ = folded_bed(**{k: v for k, v in kw.items()
                                      if k in ("nx", "ny", "size", "x0", "y0",
                                               "base", "thickness", "amp",
                                               "folds", "tilt")})
        name = "folded_bed"
    elif kind == "suite":
        patches = suite(**{k: v for k, v in kw.items()
                           if k in ("n", "nx", "ny", "size", "x0", "y0",
                                    "base", "thickness", "gap", "folds",
                                    "tilt")})
        name = "suite"
    else:
        raise ValueError("kind must be one of %s" % (EXAMPLES,))

    if as_tin:
        patches = _triangulate(patches)
        wkt_kind = "TIN"
    else:
        wkt_kind = "POLYHEDRALSURFACE"
    n_edges, n_open = edge_audit(patches)
    meta = {"name": name, "patches": len(patches),
            "watertight": bool(n_open == 0), "open_edges": int(n_open)}
    return patches, wkt_kind, meta


def _triangulate(patches):
    """Веерная триангуляция граней (для TIN и для запасного вывода)."""
    out = []
    for ring in patches:
        pts = [tuple(p) for p in ring]
        if pts and pts[0] == pts[-1]:
            pts = pts[:-1]
        for k in range(1, len(pts) - 1):
            out.append([pts[0], pts[k], pts[k + 1]])
    return out
