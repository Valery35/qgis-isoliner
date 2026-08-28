# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Гридирование методом минимальной кривизны (бигармония с натяжением).

Классическая минимальная кривизна (Briggs, 1974) - поверхность как тонкая
упругая пластина, проходящая через данные с минимумом изгиба, то есть
решение бигармонического уравнения ∇⁴z = 0 в свободных узлах. Натяжение
(Smith & Wessel, 1990) подмешивает мембранный (лапласов) член: при tension=0
чистая минимальная кривизна, при tension=1 - мембрана (∇²z = 0).

Реализация: SOR-релаксация на регулярной сетке. Данные снапятся к узлам
(совпадающие усредняются) и держатся как условие Дирихле. Граница -
натуральная (линейная экстраполяция ghost-узлов, то есть нулевая вторая
производная поперёк края), поэтому плоскость воспроизводится точно.

Чистый NumPy, без импорта QGIS - модуль проверяется headless-тестами
(tests/test_mincurv.py).
"""
import numpy as np


def _side(d):
    """Знак с нулём на плюсовой стороне: -1 или +1, но не 0."""
    return np.where(d >= 0.0, 1, -1)


def fault_edges(segs, xmin, ymin, cell, nx, ny):
    """Рёбра сетки, пересечённые разломом: (на восток, на юг).

    Барьер живёт на РЁБРАХ между соседними узлами, а не в ячейках. Так он
    точен: разлом остаётся линией, в сетку не переводится, ступенек по
    ячейкам не возникает и крыло у каждого узла определяется само -
    стороной, с которой лежит сам узел.

    Хранятся два направления из четырёх: на восток от узла и на юг.
    Западное и северное рёбра это те же самые рёбра соседних узлов.

    Узел, попавший ровно на линию, приписывается к одной стороне по
    правилу знака (ноль считается плюсом). Строгое сравнение здесь не
    годится: разлом, проведённый точно через центры узлов, не перекрывал
    бы ни одного ребра, а на шахтной сетке линия по оси - обычное дело.
    Приписывание же к обеим сторонам сразу отрезало бы такой узел от всех
    соседей, и он застыл бы на стартовом значении.

    segs: массив (m, 4) из kb2d.fault_segments.
    Узлы в центрах ячеек, row 0 = север, как в grid_points.
    """
    blk_e = np.zeros((ny, nx), dtype=bool)
    blk_s = np.zeros((ny, nx), dtype=bool)
    if segs is None or len(segs) == 0 or nx < 2 or ny < 2:
        return blk_e, blk_s
    top = ymin + ny * cell
    gx = xmin + (np.arange(nx) + 0.5) * cell
    gy = top - (np.arange(ny) + 0.5) * cell
    X, Y = np.meshgrid(gx, gy)
    segs = np.asarray(segs, dtype=float)
    for ax, ay, bx, by in segs:
        sx, sy = bx - ax, by - ay

        def crossed(px, py, qx, qy):
            d1 = sx * (py - ay) - sy * (px - ax)
            d2 = sx * (qy - ay) - sy * (qx - ax)
            rx, ry = qx - px, qy - py
            d3 = rx * (ay - py) - ry * (ax - px)
            d4 = rx * (by - py) - ry * (bx - px)
            return (_side(d1) != _side(d2)) & (_side(d3) != _side(d4))

        blk_e[:, :-1] |= crossed(X[:, :-1], Y[:, :-1], X[:, 1:], Y[:, 1:])
        blk_s[:-1, :] |= crossed(X[:-1, :], Y[:-1, :], X[1:, :], Y[1:, :])
    return blk_e, blk_s


def _dilate2(mask):
    """Расширить маску на два узла по четырём соседям."""
    m = mask.copy()
    for _ in range(2):
        g = m.copy()
        g[1:, :] |= m[:-1, :]
        g[:-1, :] |= m[1:, :]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        m = g
    return m


def _membrane(z, blk_e, blk_s):
    """Среднее по НЕперекрытым четырём соседям: (сумма, количество)."""
    acc = np.zeros_like(z)
    cnt = np.zeros_like(z)
    oke = ~blk_e[:, :-1]
    acc[:, :-1] += np.where(oke, z[:, 1:], 0.0)
    cnt[:, :-1] += oke
    acc[:, 1:] += np.where(oke, z[:, :-1], 0.0)
    cnt[:, 1:] += oke
    oks = ~blk_s[:-1, :]
    acc[:-1, :] += np.where(oks, z[1:, :], 0.0)
    cnt[:-1, :] += oks
    acc[1:, :] += np.where(oks, z[:-1, :], 0.0)
    cnt[1:, :] += oks
    return acc, cnt


def _pad_natural(z, pad=2):
    """Дополняет массив ghost-узлами линейной экстраполяцией (натуральная
    граница: вторая производная поперёк края = 0). Плоскость при этом
    продолжается точно."""
    zp = z
    top = [(k + 1) * zp[0] - k * zp[1] for k in range(pad, 0, -1)]
    bot = [(k + 1) * zp[-1] - k * zp[-2] for k in range(1, pad + 1)]
    zp = np.vstack([np.stack(top), zp, np.stack(bot)])
    left = [(k + 1) * zp[:, 0] - k * zp[:, 1] for k in range(pad, 0, -1)]
    right = [(k + 1) * zp[:, -1] - k * zp[:, -2] for k in range(1, pad + 1)]
    zp = np.hstack([np.stack(left, axis=1), zp, np.stack(right, axis=1)])
    return zp


def _targets(z, tfield, aniso, blocks=None):
    """Целевое значение узла: смесь бигармонического (мин. кривизна) и
    лапласова (натяжение) шаблонов. tfield - поле натяжения [0..1].

    blocks = (blk_e, blk_s, near). У разлома тринадцатиточечный шаблон
    непригоден: он дотягивается на две ячейки и всё равно перешагнул бы
    линию, а вырезать из него отдельные точки нельзя - шаблон перестанет
    приближать бигармонию. Поэтому в полосе шириной два узла вдоль линии
    (near) решение переходит на мембрану по неперекрытым соседям, и та
    разрыв держит точно. Дальше от линии работает обычная кривизна.

    Тот же приём стоит в Topo2Raster у обрывов, и по той же причине.
    """
    zp = _pad_natural(z, 2)
    c = zp[2:-2, 2:-2]
    N = zp[1:-3, 2:-2]; S = zp[3:-1, 2:-2]
    E = zp[2:-2, 3:-1]; W = zp[2:-2, 1:-3]
    NE = zp[1:-3, 3:-1]; NW = zp[1:-3, 1:-3]
    SE = zp[3:-1, 3:-1]; SW = zp[3:-1, 1:-3]
    NN = zp[0:-4, 2:-2]; SS = zp[4:, 2:-2]
    EE = zp[2:-2, 4:]; WW = zp[2:-2, 0:-4]
    del c
    # бигармония ∇⁴z = 0 -> центр через 13-точечный шаблон
    bih = (8.0 * (N + S + E + W)
           - 2.0 * (NE + NW + SE + SW)
           - (NN + SS + EE + WW)) / 20.0
    # анизотропный лаплас ∇²z = 0 (натяжение вдоль осей)
    wx, wy = 1.0, float(aniso)
    lap = (wx * (E + W) + wy * (N + S)) / (2.0 * wx + 2.0 * wy)
    out = (1.0 - tfield) * bih + tfield * lap
    if blocks is None:
        return out
    blk_e, blk_s, near = blocks
    acc, cnt = _membrane(z, blk_e, blk_s)
    # узел, отрезанный со всех сторон, остаётся при своём значении
    mem = np.where(cnt > 0, acc / np.maximum(cnt, 1.0), z)
    return np.where(near, mem, out)


def solve(z, fixed, tension=0.0, boundary_tension=0.0,
          max_iter=100000, tol=1e-4, relax=1.5, aniso=1.0, progress=None,
          fault_segs=None, xmin=0.0, ymin=0.0, cell=1.0):
    """SOR к поверхности минимальной кривизны с натяжением.

    z: 2D float, стартовое поле (в fixed-узлах - данные).
    fixed: 2D bool, узлы-данные (держатся, условие Дирихле).
    tension: 0 = мин. кривизна, 1 = мембрана. boundary_tension - на краю.
    relax: коэффициент релаксации SOR (1 = Гаусс-Зейдель, >1 - ускорение).

    fault_segs - звенья разломов (m, 4) из kb2d.fault_segments вместе с
    привязкой сетки (xmin, ymin, cell). Ребро между соседними узлами,
    пересечённое разломом, из шаблона выпадает, и поверхность вдоль линии
    рвётся. Барьер точный, в сетку разлом не переводится.

    Обход девятью цветами (i%3, j%3): узлы одного цвета отстоят на 3+ и не
    попадают в 13-точечный шаблон друг друга, поэтому их можно обновлять
    разом - это точный Гаусс-Зейдель, устойчивый там, где Якоби расходится.
    Возвращает (z, n_iter, last_change).
    """
    z = np.array(z, dtype=float)
    fixed = np.asarray(fixed, dtype=bool)
    if z.shape[0] < 3 or z.shape[1] < 3:
        return z, 0, 0.0
    tfield = np.full(z.shape, float(tension))
    tb = max(float(tension), float(boundary_tension))
    tfield[0, :] = tb; tfield[-1, :] = tb
    tfield[:, 0] = tb; tfield[:, -1] = tb
    relax = float(relax)
    max_iter = max(1, int(max_iter))
    blocks = None
    if fault_segs is not None and len(fault_segs):
        ny, nx = z.shape
        blk_e, blk_s = fault_edges(fault_segs, float(xmin), float(ymin),
                                   float(cell), nx, ny)
        if blk_e.any() or blk_s.any():
            touched = np.zeros(z.shape, dtype=bool)
            touched[:, :-1] |= blk_e[:, :-1]
            touched[:, 1:] |= blk_e[:, :-1]
            touched[:-1, :] |= blk_s[:-1, :]
            touched[1:, :] |= blk_s[:-1, :]
            blocks = (blk_e, blk_s, _dilate2(touched))
    ii, jj = np.indices(z.shape)
    colors = [((ii % 3 == a) & (jj % 3 == b) & ~fixed)
              for a in range(3) for b in range(3)]
    last = 0.0
    it = 0
    for it in range(1, max_iter + 1):
        prev = z.copy()
        for m in colors:
            if not m.any():
                continue
            target = _targets(z, tfield, aniso, blocks)
            z[m] = z[m] + relax * (target[m] - z[m])
        change = float(np.max(np.abs(z - prev))) if z.size else 0.0
        last = change
        if progress is not None and (it % 20 == 0):
            progress(it, max_iter, last)
        if change < tol:
            break
    return z, it, last


def grid_points(xs, ys, vs, xmin, ymin, cell, nx, ny):
    """Снапит точки к узлам сетки (row 0 = север, как в geotransform).

    Возвращает (z0, fixed): z0 - стартовое поле (в узлах-данных среднее по
    точкам, в свободных - среднее по всем данным), fixed - маска узлов-данных.
    """
    xs = np.asarray(xs, float); ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    top = ymin + ny * cell
    col = np.clip(((xs - xmin) / cell).astype(int), 0, nx - 1)
    row = np.clip(((top - ys) / cell).astype(int), 0, ny - 1)
    flat = row * nx + col
    sums = np.zeros(nx * ny); cnts = np.zeros(nx * ny)
    np.add.at(sums, flat, vs)
    np.add.at(cnts, flat, 1.0)
    fixed = (cnts > 0).reshape(ny, nx)
    mean = float(vs.mean()) if len(vs) else 0.0
    z0 = np.full(ny * nx, mean)
    nz = cnts > 0
    z0[nz] = sums[nz] / cnts[nz]
    z0 = z0.reshape(ny, nx)
    # стартовое приближение свободных узлов - ближайшее значение данных:
    # Гаусс-Зейделю остаётся только сгладить, а не «доносить» уровень через
    # всю сетку (иначе бигармония сходится крайне медленно)
    data_rc = np.argwhere(fixed)
    free_rc = np.argwhere(~fixed)
    if len(data_rc) and len(free_rc) and \
            len(data_rc) * len(free_rc) <= 60_000_000:
        dv = z0[fixed]
        d2 = ((free_rc[:, 0][:, None] - data_rc[:, 0][None, :]) ** 2
              + (free_rc[:, 1][:, None] - data_rc[:, 1][None, :]) ** 2)
        nearest = np.argmin(d2, axis=1)
        z0[~fixed] = dv[nearest]
    return z0, fixed


def sample_bilinear(grid, xmin, ymin, cell, nx, ny, x, y):
    """Билинейное значение грида в точке (x, y). Узлы в центрах ячеек,
    row 0 = север (top = ymin + ny*cell)."""
    top = ymin + ny * cell
    fx = (x - xmin) / cell - 0.5
    fy = (top - y) / cell - 0.5
    j0 = int(np.floor(fx)); i0 = int(np.floor(fy))
    tx = fx - j0; ty = fy - i0
    j0 = min(max(j0, 0), nx - 1); j1 = min(j0 + 1, nx - 1)
    i0 = min(max(i0, 0), ny - 1); i1 = min(i0 + 1, ny - 1)
    a = grid[i0, j0]; b = grid[i0, j1]
    c = grid[i1, j0]; d = grid[i1, j1]
    return float((a * (1 - tx) + b * tx) * (1 - ty)
                 + (c * (1 - tx) + d * tx) * ty)


def loo_estimates(xs, ys, vs, xmin, ymin, cell, nx, ny, val_idx,
                  tension=0.0, boundary_tension=0.0, relax=1.85,
                  aniso=1.0, tol=1e-4, base_iter=200000, loo_iter=20000,
                  excl_x=0.0, excl_y=0.0, progress=None):
    """Скользящий контроль (leave-one-out) для минимальной кривизны.

    Полное решение считается один раз и служит тёплым стартом: при
    исключении одной точки поверхность меняется локально, поэтому каждая
    LOO-переоценка сходится за считанные проходы. Для точки из val_idx
    исключается она сама и точки в буфере excl_x/excl_y, грид пересчитывается
    и билинейно берётся значение в точке.

    Возвращает (ests, z_full): ests - оценки в точках val_idx (NaN, если
    точек не осталось), z_full - грид по всем данным.
    """
    xs = np.asarray(xs, float); ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    z0, fixed = grid_points(xs, ys, vs, xmin, ymin, cell, nx, ny)
    z_full, _, _ = solve(z0, fixed, tension=tension,
                         boundary_tension=boundary_tension,
                         max_iter=base_iter, tol=tol, relax=relax, aniso=aniso)
    n = len(xs)
    idxall = np.arange(n)
    ests = np.full(len(val_idx), np.nan)
    for k, i in enumerate(val_idx):
        drop = (idxall == i)
        if excl_x > 0 or excl_y > 0:
            drop = drop | ((np.abs(xs - xs[i]) <= excl_x)
                           & (np.abs(ys - ys[i]) <= excl_y))
        keep = ~drop
        if int(keep.sum()) < 3:
            continue
        z0i, fxi = grid_points(xs[keep], ys[keep], vs[keep],
                               xmin, ymin, cell, nx, ny)
        start = z_full.copy()
        start[fxi] = z0i[fxi]                 # оставшиеся данные держим точно
        zi, _, _ = solve(start, fxi, tension=tension,
                         boundary_tension=boundary_tension,
                         max_iter=loo_iter, tol=tol, relax=relax, aniso=aniso)
        ests[k] = sample_bilinear(zi, xmin, ymin, cell, nx, ny,
                                  xs[i], ys[i])
        if progress is not None:
            progress(k + 1, len(val_idx))
    return ests, z_full
