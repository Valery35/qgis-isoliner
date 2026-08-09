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
получает промежуточную высоту (ограничение первой версии).
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
    """Средневзвешенная высота образцов по ячейкам: (маска, значения).

    Столбец 4 (если он есть) - вес образца. Он нужен потому, что в один
    поток жёстких узлов сходятся разные по достоверности данные:
    измеренная отметка снята прибором, а вершина оцифрованной горизонтали
    получена рисовальщиком по линии сечения и уклоняется в пределах половины
    сечения. Когда они попадают в одну ячейку, простое среднее уравнивает
    их в правах, и измерение тонет в оцифровке. Вес это исправляет, не
    трогая саму схему релаксации: узел остаётся узлом, меняется только
    то, какое число в нём стоит.

    Без четвёртого столбца всё работает как раньше, с весом единица.
    """
    ny, nx = shape
    val = np.zeros(ny * nx, dtype=np.float64)
    cnt = np.zeros(ny * nx, dtype=np.float64)
    if pts is not None and len(pts):
        r, c = world_to_cell(pts[:, 0], pts[:, 1], x0, y_top, cell)
        ok = (r >= 0) & (r < ny) & (c >= 0) & (c < nx)
        flat = r[ok] * nx + c[ok]
        w = pts[ok, 3] if pts.shape[1] > 3 else np.ones(int(ok.sum()))
        w = np.where(np.isfinite(w) & (w > 0.0), w, 1.0)
        np.add.at(val, flat, pts[ok, 2] * w)
        np.add.at(cnt, flat, w)
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


# Сколько ближайших вершин кольца участвует в интерполяции уреза.
# Меньше - урез точнее следует своему берегу, но в середине широкого
# русла возможен шов по оси. Больше - глаже, но возвращается подтягивание
# с чужого берега. Восемь проверено замером на меандрах.
K_RING_NEAREST = 8


def profile_ring_from_points(rings, points, tol, min_pts=2):
    """Отметки вершин уреза по точкам высот у его контура.

    Алгоритм В. Швалева, который он до сих пор выполнял руками: контур
    уреза отрисован, а отметки уреза стоят обычными точками высот рядом с
    ним. Берутся точки не дальше tol от контура, и вершина кольца
    получает отметку по ним.

    Чем это лучше профилирования по оси реки: оси не требуется вовсе,
    поэтому работает и на пруду, и на старице, и на реке с островом.
    Отметка при этом остаётся замером, а не значением, которое поставил
    оцифровщик по своему разумению.

    Точка, попавшая в допуск сразу от двух водоёмов, отдаётся ближайшему
    и только ему: отдать обоим значило бы поднять один урез отметкой
    другого.

    rings - список колец (K, 2) по одному водоёму;
    points - (N, 3) точки высот x, y, z;
    tol - допуск отбора в единицах карты;
    min_pts - сколько точек нужно, чтобы профилировать вообще.

    Возвращает (список Z по кольцам, сколько точек подобрано) либо
    (None, 0), если точек не хватило: тогда урез остаётся как был.
    """
    if points is None or len(points) == 0:
        return None, 0
    pts = np.asarray(points, dtype=np.float64)
    px, py, pz = pts[:, 0], pts[:, 1], pts[:, 2]
    # отбор: расстояние до ближайшего звена любого кольца не больше tol
    best = np.full(px.shape, np.inf)
    for ring in rings:
        r = np.asarray(ring, dtype=np.float64)
        for k in range(len(r) - 1):
            ax, ay = r[k]
            bx, by = r[k + 1]
            dx, dy = bx - ax, by - ay
            den = dx * dx + dy * dy
            if den <= 0.0:
                continue
            s = ((px - ax) * dx + (py - ay) * dy) / den
            s = np.clip(s, 0.0, 1.0)
            d = np.hypot(px - (ax + s * dx), py - (ay + s * dy))
            best = np.minimum(best, d)
    take = best <= float(tol)
    if int(take.sum()) < int(min_pts):
        return None, int(take.sum())
    sx, sy, sz = px[take], py[take], pz[take]

    out = []
    for ring in rings:
        r = np.asarray(ring, dtype=np.float64)
        # отметка вершины по ближайшим отобранным точкам, обратные
        # квадраты расстояний. Локально, как и у переменного уреза:
        # глобальное взвешивание тянуло бы уровень с другого конца
        # водоёма.
        k = int(min(K_RING_NEAREST, sx.size))
        d2 = ((r[:, 0][:, None] - sx[None, :]) ** 2
              + (r[:, 1][:, None] - sy[None, :]) ** 2)
        d2 = np.where(d2 < 1e-6, 1e-6, d2)
        if k < sx.size:
            idx = np.argpartition(d2, k - 1, axis=1)[:, :k]
            d2 = np.take_along_axis(d2, idx, axis=1)
            vv = sz[idx]
        else:
            vv = np.broadcast_to(sz[None, :], d2.shape)
        w = 1.0 / d2
        out.append((w * vv).sum(axis=1) / w.sum(axis=1))
    return out, int(take.sum())


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
    # IDW степени 2 по БЛИЖАЙШИМ вершинам, блоками чтобы не раздувать
    # память.
    #
    # Раньше веса брались по всем вершинам кольца сразу, и на меандре это
    # давало ошибку: противоположный берег близко в пространстве, но далеко по
    # реке и с другой отметкой, поэтому урез подтягивался к чужому
    # уровню. Ошибка растёт с падением реки: при падении 15 м на лист она
    # доходила до 0.9 м с размахом 1.8 м, и вдоль уреза шли бугры.
    #
    # Восемь ближайших вершин оставляют интерполяцию локальной. Для
    # ячейки на самом урезе вес почти целиком у соседних вершин своего
    # берега, а для ячейки в середине русла уровень плавно делится между
    # двумя берегами, без шва по оси.
    k = int(min(K_RING_NEAREST, pts.shape[0]))
    block = 4096
    for start in range(0, rr.size, block):
        sl = slice(start, start + block)
        dx = cx[sl][:, None] - pts[None, :, 0]
        dy = cy[sl][:, None] - pts[None, :, 1]
        d2 = dx * dx + dy * dy
        d2 = np.where(d2 < 1e-6, 1e-6, d2)
        if k < pts.shape[0]:
            idx = np.argpartition(d2, k - 1, axis=1)[:, :k]
            d2 = np.take_along_axis(d2, idx, axis=1)
            vv = vals[idx]
        else:
            vv = np.broadcast_to(vals[None, :], d2.shape)
        w = 1.0 / d2
        surf[rr[sl], cc[sl]] = (w * vv).sum(axis=1) / w.sum(axis=1)
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
           feedback=None, stream_bnds=None):
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
            _enforce_streams(z, streams_flat, min_drop, stream_bnds)
            z[pin_mask] = pin_val[pin_mask]
        if feedback is not None and it % 10 == 0:
            if feedback.isCanceled():
                break
    return z


def project_depth_points(axis_xy, points, tol):
    """Промеры дна на ось тальвега: якоря по дуговой координате.

    Промеры снимают створами поперёк русла, и в ячейку самой оси попадают
    далеко не все. Такой промер держал только свою ячейку, а продольный
    профиль не задавал, и между створами тальвег снова срезал дно по
    минимальному падению.

    Отметкой оси в створе берётся МИНИМУМ из спроецированных промеров:
    тальвег это линия наибольших глубин, значит его касается самый низкий
    промер створа. Береговые промеры при этом никуда не деваются, они
    по-прежнему работают жёсткими узлами в своих ячейках и формируют
    поперечник.

    axis_xy - вершины оси (K, 2) в мировых координатах;
    points - (N, 3) промеры x, y, z;
    tol - допуск отбора от оси в единицах карты.

    Возвращает (список (дуговая координата, отметка), число учтённых
    промеров). Промеры дальше tol от оси не участвуют.
    """
    axis = np.asarray(axis_xy, dtype=np.float64)
    if points is None or len(points) == 0 or len(axis) < 2 or tol <= 0:
        return [], 0
    pts = np.asarray(points, dtype=np.float64)
    px, py, pz = pts[:, 0], pts[:, 1], pts[:, 2]
    seg = np.hypot(np.diff(axis[:, 0]), np.diff(axis[:, 1]))
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    best_d = np.full(px.shape, np.inf)
    best_s = np.zeros(px.shape)
    for k in range(len(axis) - 1):
        ax, ay = axis[k]
        bx, by = axis[k + 1]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        if den <= 0.0:
            continue
        s = np.clip(((px - ax) * dx + (py - ay) * dy) / den, 0.0, 1.0)
        d = np.hypot(px - (ax + s * dx), py - (ay + s * dy))
        closer = d < best_d
        best_d = np.where(closer, d, best_d)
        best_s = np.where(closer, cum[k] + s * seg[k], best_s)
    take = best_d <= float(tol)
    if not take.any():
        return [], 0
    # минимум по створу: промеры с близкой дуговой координатой это один
    # створ, и оси касается самый низкий из них
    out = {}
    for s, z in zip(best_s[take], pz[take]):
        key = round(float(s), 6)
        out[key] = min(out.get(key, float(z)), float(z))
    return sorted(out.items()), int(take.sum())


def against_the_fall(anchors):
    """Сколько якорей идёт против общего падения и наибольший подъём.

    Русло меняется год от года, плёсы и перекаты нормальны, поэтому
    подъём между промерами это не ошибка и помечать каждый незачем.
    Одна строка с итогом покажет опечатку в отметке и не создаст шума.
    """
    ups, worst = 0, 0.0
    for (_s0, z0), (_s1, z1) in zip(anchors[:-1], anchors[1:]):
        if z1 > z0:
            ups += 1
            worst = max(worst, float(z1 - z0))
    return ups, worst


def stream_bounds(chain, pin_mask, pin_val, min_drop, extra=None):
    """Потолок и пол вдоль цепочки по закреплённым отметкам дна.

    Прежнее принуждение умело только опускать. Отметку дна выше текущего
    профиля оно просто игнорировало, поэтому пикет оставался одиноким
    пиком: соседний узел оказывался ниже него на всю разницу. А ниже по
    течению профиль срезался от верхнего значения и проходил мимо
    следующего пикета, подпирая его ступенькой. На продольном профиле это
    выглядело как ровное дно с двумя иглами вместо плавного ската от
    отметки к отметке.

    Теперь закреплённые отметки работают якорями с двух сторон.
    Потолок идёт вниз по течению от каждого якоря: ниже якоря профиль не
    поднимется. Пол идёт вверх по течению от каждого якоря: выше по руслу
    профиль не срежется ниже того, что позволяет нижний якорь. Между
    двумя якорями это даёт скат от одного к другому, а падение остаётся
    монотонным.

    Возвращает (потолок, пол) массивами по длине цепочки. Если якорей на
    цепочке нет, потолок и пол бесконечны, и поведение прежнее.
    """
    n = int(chain.size)
    step = np.arange(n, dtype=np.float64) * float(min_drop)
    pinned = pin_mask.ravel()[chain].copy()
    vals = pin_val.ravel()[chain].astype(np.float64).copy()
    if extra:
        # промеры, спроецированные на ось: якорь ставится в ближайший узел
        # цепочки по дуговой координате, минимум если их несколько
        last = int(chain.size) - 1
        smax = max(s for s, _z in extra)
        scale = (last / smax) if smax > 0 else 0.0
        for s, z in extra:
            i = int(round(float(s) * scale))
            i = 0 if i < 0 else (int(chain.size) - 1 if i >= chain.size else i)
            if pinned[i]:
                vals[i] = min(float(vals[i]), float(z))
            else:
                pinned[i] = True
                vals[i] = float(z)
    cap = np.full(n, np.inf)
    if pinned.any():
        run = np.inf
        for i in range(n):
            if pinned[i]:
                run = float(vals[i]) + step[i]
            cap[i] = run
        cap = cap - step
        # Пол между двумя якорями идёт ПО ЛИНИИ от одного к другому, а не
        # по минимальному падению. Иначе профиль срезается мимо нижнего
        # пикета и подпирает его ступенькой: отметка дна влияет только
        # рядом с собой, а между отметками русло определяет тальвег.
        idx = np.nonzero(pinned)[0]
        floor = np.full(n, -np.inf)
        for a, b in zip(idx[:-1], idx[1:]):
            if b > a:
                line = np.linspace(float(vals[a]), float(vals[b]), b - a + 1)
                # Между двумя отметками дна профиль И ЕСТЬ эта линия:
                # других данных о русле там нет, а тальвег задаёт только
                # направление падения, не глубину. Поэтому линия ставится
                # и полом, и потолком.
                floor[a:b + 1] = line
                cap[a:b + 1] = line
        # выше первого якоря держим минимальное падение к нему
        first = int(idx[0])
        floor[:first] = float(vals[first]) + (step[first] - step[:first])
        floor[idx] = vals[idx]
    else:
        floor = np.full(n, -np.inf)
    return cap, floor


def _enforce_streams(z, streams_flat, min_drop, bounds=None):
    """Монотонное падение вдоль каждой цепочки вниз по течению.

    bounds - список (потолок, пол) по цепочкам из stream_bounds. Если он
    задан, закреплённые отметки дна работают якорями и профиль идёт от
    одной к другой, а не срезается мимо них.
    """
    flat = z.ravel()
    for k, chain in enumerate(streams_flat):
        if chain.size < 2:
            continue
        v = flat[chain]
        v = np.minimum.accumulate(v + np.arange(v.size) * min_drop)
        v -= np.arange(v.size) * min_drop
        v = np.minimum(flat[chain], v)
        if bounds is not None and k < len(bounds):
            cap, floor = bounds[k]
            v = np.minimum(v, cap)
            v = np.maximum(v, floor)
        flat[chain] = v


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
            omega=0.5, feedback=None, stream_bnds=None):
    """Полировка минимальной кривизной (Briggs): бигармонический
    стенсил 13 точек с демпфированием. Мембрана из _relax
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
            _enforce_streams(z, streams_flat, min_drop, stream_bnds)
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

def mask_edge(m):
    """Контур маски: ячейки внутри, у которых есть сосед снаружи.

    Нужен урезу в режиме изолинии: закрепляется только линия уреза, а
    поверхность внутри остаётся свободной и определяется точками дна и
    тальвегом. Плоскость прибивает всю площадь, изолиния - только берег.
    """
    e = np.zeros_like(m, dtype=bool)
    if not m.any():
        return e
    e[:-1, :] |= m[:-1, :] & ~m[1:, :]
    e[1:, :] |= m[1:, :] & ~m[:-1, :]
    e[:, :-1] |= m[:, :-1] & ~m[:, 1:]
    e[:, 1:] |= m[:, 1:] & ~m[:, :-1]
    e[0, :] |= m[0, :]
    e[-1, :] |= m[-1, :]
    e[:, 0] |= m[:, 0]
    e[:, -1] |= m[:, -1]
    return e


def topo2raster(points, streams, breaklines, lakes, extent, cell,
                iterations=DEFAULT_ITERATIONS, min_drop=DEFAULT_MIN_DROP,
                feedback=None, depth_tol=0.0):
    """Построить рельеф из векторных ограничений.

    points: (N, 3) x, y, z либо (N, 4) x, y, z, w - все жёсткие узлы
        (точки высот и уплотнённые изолинии вместе). Четвёртый столбец,
        если он есть, задаёт вес образца при усреднении внутри ячейки:
        измеренная отметка достовернее вершины оцифрованной горизонтали и
        не должна тонуть в ней. Без него все веса единичные.
    streams: список ломаных (K, 2), вершины вниз по течению
    breaklines: список ломаных (K, 2)
    lakes: список озёр. Каждое - (кольца, z, ring_z[, as_plane]), где кольца это
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
        pairs = [(xy, polyline_cells(xy, x0, y_top, lcell, lshape))
                 for xy in (streams or ())]
        pairs = [(xy, s) for xy, s in pairs if s.size > 1]
        streams_flat = [s for _xy, s in pairs]
        # промеры дна, спроецированные на ось: якоря продольного профиля
        # по всем замерам, а не только по попавшим в ячейку тальвега
        stream_extra = [project_depth_points(xy, points, depth_tol)[0]
                        if depth_tol > 0 else []
                        for xy, _s in pairs]

        lake_masks = []
        for lake in (lakes or ()):
            as_plane = True
            if len(lake) == 4:
                rings, lz, ring_z, as_plane = lake
            elif len(lake) == 3:
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
                # в режиме изолинии закрепляется только контур: внутри
                # поверхность свободна и подчиняется точкам дна
                lake_masks.append((m if as_plane else mask_edge(m),
                                   lz, surf))

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
        stream_bnds = [stream_bounds(ch, pin, pval, min_drop,
                                     stream_extra[i])
                       for i, ch in enumerate(streams_flat)] \
            if streams_flat else None
        z = _relax(z, pin, pval, barrier, max(5, iters // 2),
                   streams_flat, min_drop, feedback, stream_bnds)

        # приоритет 3: уровень по минимуму прилегающего берега
        for m, lz, surf in lake_masks:
            if surf is None and lz is None:
                ring = _shore_ring(m)
                level = float(z[ring].min()) if ring.any() else \
                    float(z[m].min())
                pin |= m
                pval[m] = level
                z[m] = level
        stream_bnds = [stream_bounds(ch, pin, pval, min_drop,
                                     stream_extra[i])
                       for i, ch in enumerate(streams_flat)] \
            if streams_flat else None
        z = _relax(z, pin, pval, barrier, iters - max(5, iters // 2) + 1,
                   streams_flat, min_drop, feedback, stream_bnds)
        z = _polish(z, pin, pval, barrier, 2 * iters, streams_flat,
                    min_drop, feedback=feedback, stream_bnds=stream_bnds)
        if feedback is not None:
            if feedback.isCanceled():
                break
            feedback.pushInfo("  %d/%d: %dx%d" %
                              (li + 1, n_levels, lshape[1], lshape[0]))

    # финальная сетка ровно ny x nx
    if z.shape != (ny, nx):
        z = _upsample(z, (ny, nx))
    return z, x0, y_top


def monotone_down(z, min_drop=DEFAULT_MIN_DROP):
    """Отметки вдоль цепочки приводятся к строго падающим вниз по течению.

    Нужна для трёхмерных тальвегов. Измеренные отметки по руслу шумят, и
    отдельные вершины уходят вверх по течению. Если подать их жёсткими
    узлами как есть, они начнут спорить с принуждением падения: релаксация
    пришпиливает ячейку к отметке, принуждение продавливает её ниже, и так
    по кругу. Поэтому шум срезается один раз на входе, а не каждую итерацию.

    Приём тот же, что в _enforce_streams: накопленный минимум по цепочке с
    поправкой на обязательный уклон, поэтому результат согласован с ним и
    принуждение после этого становится пустой операцией.
    """
    z = np.asarray(z, dtype=float)
    if z.size < 2:
        return z.copy()
    step = np.arange(z.size, dtype=float) * float(min_drop)
    return np.minimum.accumulate(z + step) - step


def count_conflicts(pts_a, pts_b, cell, tol=0.05):
    """Сколько ячеек получают два жёстких узла с разной отметкой.

    Оба набора пришпиливаются к сетке, и если в одну ячейку попали узлы
    из разных источников с расхождением больше допуска, побеждает тот,
    что записан последним. Молча так делать нельзя: пересечение русла с
    горизонталью это обычное дело, и человек должен знать, где данные
    спорят между собой.
    """
    a = np.asarray(pts_a, dtype=float)
    b = np.asarray(pts_b, dtype=float)
    if a.size == 0 or b.size == 0:
        return 0
    cell = float(cell) or 1.0
    key_a = {}
    for x, y, z in a:
        key_a[(int(np.floor(x / cell)), int(np.floor(y / cell)))] = z
    n = 0
    seen = set()
    for x, y, z in b:
        k = (int(np.floor(x / cell)), int(np.floor(y / cell)))
        za = key_a.get(k)
        if za is not None and abs(za - z) > tol and k not in seen:
            seen.add(k)
            n += 1
    return n
