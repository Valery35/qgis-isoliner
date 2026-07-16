# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Topo2Raster на чистом NumPy: гидрологически осмысленный рельеф из
векторных данных. Не буквальный ANUDEM, а его суть: мультисеточная
итеративная интерполяция от грубой сетки к тонкой, где каждый тип
входа работает своим ограничением.

Точки и отцифрованные изолинии - жёсткие узлы (высота приколота).
Тальвеги - монотонное падение вниз по течению вдоль линии.
Обрывы - барьер сглаживания: соседства через линию разрываются,
поверхности по сторонам независимы, сама линия шириной в ячейку
получает промежуточную высоту (честное ограничение первой версии).
Озёра - плоскости: с заданной высотой приколоты к ней, без высоты
уровень берётся как минимум прилегающего берега на каждом уровне
сетки.

Сглаживание мембранное (Лаплас, Якоби): при плотных изолиниях даёт
устойчивую поверхность без выбросов. Дренаж вдоль тальвегов
принуждается в цикле, финальное заполнение понижений остаётся за
вызывающим кодом.
"""

import numpy as np

DEFAULT_CELL = 30.0
DEFAULT_ITERATIONS = 60
DEFAULT_MIN_DROP = 0.01
COARSEST = 64


class Topo2RasterError(ValueError):
    pass


# --- геометрия на сетке -------------------------------------------------

def world_to_cell(x, y, x0, y_top, cell):
    """Мировые координаты в (row, col). y_top - верх грида (север)."""
    col = np.floor((np.asarray(x, float) - x0) / cell).astype(np.int64)
    row = np.floor((y_top - np.asarray(y, float)) / cell).astype(np.int64)
    return row, col


def densify(xy, step):
    """Добавить точки вдоль ломаной с шагом не крупнее step."""
    xy = np.asarray(xy, dtype=np.float64)
    if len(xy) < 2:
        return xy
    out = [xy[0]]
    for a, b in zip(xy[:-1], xy[1:]):
        seg = b - a
        dist = float(np.hypot(*seg))
        n = max(1, int(np.ceil(dist / step)))
        for k in range(1, n + 1):
            out.append(a + seg * (k / n))
    return np.array(out)


def polyline_cells(xy, x0, y_top, cell, shape):
    """Цепочка ячеек вдоль ломаной, подряд идущие дубли убраны."""
    ny, nx = shape
    dense = densify(xy, cell / 3.0)
    r, c = world_to_cell(dense[:, 0], dense[:, 1], x0, y_top, cell)
    ok = (r >= 0) & (r < ny) & (c >= 0) & (c < nx)
    r, c = r[ok], c[ok]
    if r.size == 0:
        return np.empty(0, dtype=np.int64)
    flat = r * nx + c
    keep = np.ones(flat.size, dtype=bool)
    keep[1:] = flat[1:] != flat[:-1]
    return flat[keep]


def polygon_mask(rings, x0, y_top, cell, shape):
    """Маска полигона чётно-нечётным правилом по центрам ячеек.

    rings: список колец (внешнее и дыры), каждое (K, 2). Скан-линия
    по строкам, пересечения рёбер с горизонталью центра строки.
    """
    ny, nx = shape
    mask = np.zeros(shape, dtype=bool)
    edges = []
    for ring in rings:
        ring = np.asarray(ring, dtype=np.float64)
        if len(ring) < 3:
            continue
        if not np.allclose(ring[0], ring[-1]):
            ring = np.vstack([ring, ring[:1]])
        for a, b in zip(ring[:-1], ring[1:]):
            if a[1] != b[1]:
                edges.append((a[0], a[1], b[0], b[1]))
    if not edges:
        return mask
    ex0, ey0, ex1, ey1 = (np.array([e[i] for e in edges]) for i in range(4))
    for row in range(ny):
        yc = y_top - (row + 0.5) * cell
        lo = np.minimum(ey0, ey1)
        hi = np.maximum(ey0, ey1)
        hit = (yc >= lo) & (yc < hi)
        if not hit.any():
            continue
        t = (yc - ey0[hit]) / (ey1[hit] - ey0[hit])
        xs = np.sort(ex0[hit] + t * (ex1[hit] - ex0[hit]))
        for i in range(0, xs.size - 1, 2):
            c0 = int(np.ceil((xs[i] - x0) / cell - 0.5))
            c1 = int(np.floor((xs[i + 1] - x0) / cell - 0.5))
            c0 = max(c0, 0)
            c1 = min(c1, nx - 1)
            if c1 >= c0:
                mask[row, c0:c1 + 1] = True
    return mask


# --- ограничения на уровне сетки ---------------------------------------

def _pin_from_points(pts, x0, y_top, cell, shape):
    """Средняя высота образцов по ячейкам: (маска, значения)."""
    ny, nx = shape
    val = np.zeros(ny * nx, dtype=np.float64)
    cnt = np.zeros(ny * nx, dtype=np.float64)
    if pts is not None and len(pts):
        r, c = world_to_cell(pts[:, 0], pts[:, 1], x0, y_top, cell)
        ok = (r >= 0) & (r < ny) & (c >= 0) & (c < nx)
        flat = r[ok] * nx + c[ok]
        np.add.at(val, flat, pts[ok, 2])
        np.add.at(cnt, flat, 1.0)
    pin = cnt > 0
    val[pin] /= cnt[pin]
    return pin.reshape(shape), val.reshape(shape)


def _barrier_mask(breaklines, x0, y_top, cell, shape):
    mask = np.zeros(shape, dtype=bool)
    for xy in breaklines or ():
        flat = polyline_cells(xy, x0, y_top, cell, shape)
        mask.ravel()[flat] = True
    return mask


def _erode4(mask):
    m = mask.copy()
    m[1:, :] &= mask[:-1, :]
    m[:-1, :] &= mask[1:, :]
    m[:, 1:] &= mask[:, :-1]
    m[:, :-1] &= mask[:, 1:]
    return m


def _interp_ring_surface(rings, ring_z, mask, x0, y_top, cell, shape):
    """Поверхность переменного уреза по Z-высотам вершин кольца.

    Для наклонного уреза реки: высота вершин полигона задаёт урез
    вдоль берега, внутри маски высота интерполируется методом обратных
    взвешенных расстояний (IDW) по этим вершинам. Плоское озеро с
    одинаковыми Z даёт плоскость, река с падающими Z - наклон.

    rings: список колец (K, 2). ring_z: список массивов высот вершин,
    по одному на кольцо. Возвращает массив shape с высотами в ячейках
    маски (вне маски нули, они не используются).
    """
    xs = []
    zs = []
    for ring, zr in zip(rings, ring_z):
        if zr is None:
            continue
        ring = np.asarray(ring, dtype=np.float64)
        zr = np.asarray(zr, dtype=np.float64)
        n = min(len(ring), len(zr))
        for i in range(n):
            if np.isfinite(zr[i]):
                xs.append(ring[i])
                zs.append(zr[i])
    if not xs:
        return None
    pts = np.array(xs)
    vals = np.array(zs)

    ny, nx = shape
    rr, cc = np.nonzero(mask)
    if rr.size == 0:
        return None
    cx = x0 + (cc + 0.5) * cell
    cy = y_top - (rr + 0.5) * cell

    surf = np.zeros(shape, dtype=np.float64)
    # IDW степени 2, блоками чтобы не раздувать память
    block = 4096
    for start in range(0, rr.size, block):
        sl = slice(start, start + block)
        dx = cx[sl][:, None] - pts[None, :, 0]
        dy = cy[sl][:, None] - pts[None, :, 1]
        d2 = dx * dx + dy * dy
        d2 = np.where(d2 < 1e-6, 1e-6, d2)
        w = 1.0 / d2
        surf[rr[sl], cc[sl]] = (w * vals[None, :]).sum(axis=1) / w.sum(axis=1)
    return surf


def _shore_ring(mask):
    """Ячейки снаружи маски, примыкающие к ней по 4 соседям."""
    grow = mask.copy()
    grow[1:, :] |= mask[:-1, :]
    grow[:-1, :] |= mask[1:, :]
    grow[:, 1:] |= mask[:, :-1]
    grow[:, :-1] |= mask[:, 1:]
    return grow & ~mask


# --- сглаживание --------------------------------------------------------

def _relax(z, pin_mask, pin_val, barrier, iters, streams_flat, min_drop,
           feedback=None):
    """Якоби по 4 соседям с барьерами, приколкой и дренажем."""
    ny, nx = z.shape
    for it in range(iters):
        acc = np.zeros_like(z)
        cnt = np.zeros_like(z)
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src_r = slice(max(dr, 0), ny + min(dr, 0))
            dst_r = slice(max(-dr, 0), ny + min(-dr, 0))
            src_c = slice(max(dc, 0), nx + min(dc, 0))
            dst_c = slice(max(-dc, 0), nx + min(-dc, 0))
            nb = z[src_r, src_c]
            ok = ~barrier[src_r, src_c]  # через обрыв не смотрим
            acc[dst_r, dst_c] += np.where(ok, nb, 0.0)
            cnt[dst_r, dst_c] += ok
        have = cnt > 0
        z = np.where(have, acc / np.maximum(cnt, 1.0), z)
        z[pin_mask] = pin_val[pin_mask]
        if streams_flat and (it % 5 == 4 or it == iters - 1):
            _enforce_streams(z, streams_flat, min_drop)
            z[pin_mask] = pin_val[pin_mask]
        if feedback is not None and it % 10 == 0:
            if feedback.isCanceled():
                break
    return z


def _enforce_streams(z, streams_flat, min_drop):
    """Монотонное падение вдоль каждой цепочки вниз по течению."""
    flat = z.ravel()
    for chain in streams_flat:
        if chain.size < 2:
            continue
        v = flat[chain]
        v = np.minimum.accumulate(v + np.arange(v.size) * min_drop)
        v -= np.arange(v.size) * min_drop
        flat[chain] = np.minimum(flat[chain], v)


def _dilate(mask, steps):
    m = mask.copy()
    for _ in range(steps):
        g = m.copy()
        g[1:, :] |= m[:-1, :]
        g[:-1, :] |= m[1:, :]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        m = g
    return m


def _polish(z, pin_mask, pin_val, barrier, iters, streams_flat, min_drop,
            omega=0.5, feedback=None):
    """Полировка минимальной кривизной (Briggs): бигармонический
    стенсил 13 точек с демпфированием. Мембрана из _relax честно
    держит узлы, но между кривыми изолиниями даёт смещение, кривизна
    его убирает. Возле обрывов (2 ячейки) стенсил через разрыв не
    считаем, там остаётся мембранное решение."""
    if iters <= 0:
        return z
    frozen = _dilate(barrier, 2) if barrier.any() else None
    for it in range(iters):
        p = np.pad(z, 2, mode="edge")
        znew = (8.0 * (p[1:-3, 2:-2] + p[3:-1, 2:-2]
                       + p[2:-2, 1:-3] + p[2:-2, 3:-1])
                - 2.0 * (p[1:-3, 1:-3] + p[1:-3, 3:-1]
                         + p[3:-1, 1:-3] + p[3:-1, 3:-1])
                - (p[0:-4, 2:-2] + p[4:, 2:-2]
                   + p[2:-2, 0:-4] + p[2:-2, 4:])) / 20.0
        if frozen is None:
            z = (1.0 - omega) * z + omega * znew
        else:
            upd = (1.0 - omega) * z + omega * znew
            z = np.where(frozen, z, upd)
        z[pin_mask] = pin_val[pin_mask]
        if streams_flat and (it % 10 == 9 or it == iters - 1):
            _enforce_streams(z, streams_flat, min_drop)
            z[pin_mask] = pin_val[pin_mask]
        if feedback is not None and it % 20 == 0:
            if feedback.isCanceled():
                break
    return z


def _initial_fill(z, known):
    """Заполнить неизвестные ячейки волной средних по известным."""
    z = z.copy()
    filled = known.copy()
    if not filled.any():
        raise Topo2RasterError("нет ни одного узла с высотой")
    z[~filled] = 0.0
    while not filled.all():
        acc = np.zeros_like(z)
        cnt = np.zeros_like(z)
        ny, nx = z.shape
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            src_r = slice(max(dr, 0), ny + min(dr, 0))
            dst_r = slice(max(-dr, 0), ny + min(-dr, 0))
            src_c = slice(max(dc, 0), nx + min(dc, 0))
            dst_c = slice(max(-dc, 0), nx + min(-dc, 0))
            w = filled[src_r, src_c].astype(np.float64)
            acc[dst_r, dst_c] += z[src_r, src_c] * w
            cnt[dst_r, dst_c] += w
        new = (~filled) & (cnt > 0)
        z[new] = acc[new] / cnt[new]
        if not new.any():
            z[~filled] = float(z[filled].mean())
            break
        filled |= new
    return z


def _upsample(z, shape):
    """Билинейное растяжение на удвоенную сетку."""
    ny, nx = shape
    ry = np.linspace(0, z.shape[0] - 1, ny)
    rx = np.linspace(0, z.shape[1] - 1, nx)
    r0 = np.floor(ry).astype(int)
    c0 = np.floor(rx).astype(int)
    r1 = np.minimum(r0 + 1, z.shape[0] - 1)
    c1 = np.minimum(c0 + 1, z.shape[1] - 1)
    fr = (ry - r0)[:, None]
    fc = (rx - c0)[None, :]
    top = z[r0][:, c0] * (1 - fc) + z[r0][:, c1] * fc
    bot = z[r1][:, c0] * (1 - fc) + z[r1][:, c1] * fc
    return top * (1 - fr) + bot * fr


# --- основной вход ------------------------------------------------------

def topo2raster(points, streams, breaklines, lakes, extent, cell,
                iterations=DEFAULT_ITERATIONS, min_drop=DEFAULT_MIN_DROP,
                feedback=None):
    """Построить рельеф из векторных ограничений.

    points: (N, 3) x, y, z - все жёсткие узлы (точки высот и
        уплотнённые изолинии вместе)
    streams: список ломаных (K, 2), вершины вниз по течению
    breaklines: список ломаных (K, 2)
    lakes: список озёр. Каждое - (кольца, z, ring_z), где кольца это
        список (K, 2). Приоритет высоты: (1) ring_z задан (список Z по
        вершинам каждого кольца) - переменный урез интерполируется
        вдоль границы, река получает наклон; (2) z задан числом -
        горизонтальная плоскость; (3) оба None - уровень по минимуму
        берега. Разнотипные объекты в одном слое обрабатываются каждый
        своей веткой. Для совместимости принимается и старый кортеж
        (кольца, z) - тогда ring_z считается None.
    extent: (xmin, ymin, xmax, ymax) в метрической СК
    cell: размер ячейки итогового грида, м

    Возвращает (z, x0, y_top): грид float64 и привязку верхнего
    левого угла.
    """
    xmin, ymin, xmax, ymax = (float(v) for v in extent)
    if not (xmax > xmin and ymax > ymin):
        raise Topo2RasterError("пустой охват")
    nx = max(4, int(np.ceil((xmax - xmin) / cell)))
    ny = max(4, int(np.ceil((ymax - ymin) / cell)))
    x0, y_top = xmin, ymax

    points = None if points is None or len(points) == 0 else \
        np.asarray(points, dtype=np.float64)
    if points is None:
        raise Topo2RasterError("нужен хотя бы один узел с высотой: "
                               "точки или изолинии")

    # уровни: от грубого к тонкому
    factors = [1]
    while max(ny, nx) // factors[-1] > COARSEST:
        factors.append(factors[-1] * 2)
    factors.reverse()

    z = None
    n_levels = len(factors)
    for li, f in enumerate(factors):
        lcell = cell * f
        lshape = (max(4, int(np.ceil(ny / f))), max(4, int(np.ceil(nx / f))))
        pin, pval = _pin_from_points(points, x0, y_top, lcell, lshape)
        barrier = _barrier_mask(breaklines, x0, y_top, lcell, lshape)
        streams_flat = [polyline_cells(xy, x0, y_top, lcell, lshape)
                        for xy in (streams or ())]
        streams_flat = [s for s in streams_flat if s.size > 1]

        lake_masks = []
        for lake in (lakes or ()):
            if len(lake) == 3:
                rings, lz, ring_z = lake
            else:
                rings, lz = lake
                ring_z = None
            m = polygon_mask(rings, x0, y_top, lcell, lshape)
            if m.any():
                surf = None
                if ring_z is not None:
                    surf = _interp_ring_surface(
                        rings, ring_z, m, x0, y_top, lcell, lshape)
                lake_masks.append((m, lz, surf))

        if z is None:
            z = _initial_fill(np.where(pin, pval, 0.0), pin)
        else:
            z = _upsample(z, lshape)
            z[pin] = pval[pin]

        # приоритет 1 (переменный урез по узлам) и 2 (плоскость) - колом
        for m, lz, surf in lake_masks:
            if surf is not None:
                pin |= m
                pval[m] = surf[m]
                z[m] = surf[m]
            elif lz is not None:
                pin |= m
                pval[m] = float(lz)
                z[m] = float(lz)

        iters = max(10, iterations // (li + 1)) if li < n_levels - 1 \
            else iterations
        z = _relax(z, pin, pval, barrier, max(5, iters // 2),
                   streams_flat, min_drop, feedback)

        # приоритет 3: уровень по минимуму прилегающего берега
        for m, lz, surf in lake_masks:
            if surf is None and lz is None:
                ring = _shore_ring(m)
                level = float(z[ring].min()) if ring.any() else \
                    float(z[m].min())
                pin |= m
                pval[m] = level
                z[m] = level
        z = _relax(z, pin, pval, barrier, iters - max(5, iters // 2) + 1,
                   streams_flat, min_drop, feedback)
        z = _polish(z, pin, pval, barrier, 2 * iters, streams_flat,
                    min_drop, feedback=feedback)
        if feedback is not None:
            if feedback.isCanceled():
                break
            feedback.pushInfo("  %d/%d: %dx%d" %
                              (li + 1, n_levels, lshape[1], lshape[0]))

    # финальная сетка ровно ny x nx
    if z.shape != (ny, nx):
        z = _upsample(z, (ny, nx))
    return z, x0, y_top
