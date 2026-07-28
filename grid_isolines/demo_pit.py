# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Демо-карьер: синтетический рельеф с известными бровками и подошвами.

Генератор отдаёт две вещи разом: растр рельефа и истинные структурные
линии с трёхмерными вершинами. Пара служит эталоном для детектора
кандидатов (полнота и боковое отклонение считаются числом, а не глазами),
готовым входом для поверхности между линиями и учебным примером.

Состав рельефа: волнистое основание, эллиптический карьер с уступами и
бермами, съезд, прорезающий уступы (на его дуге истинные линии рвутся),
отвал с плоским верхом (замкнутая пара бровка-подошва) и сходящаяся
нагорная канава (две бровки и тальвег, сходящиеся в точке). Всё
детерминировано зерном.
"""

import numpy as np

DEFAULT_NX = 400
DEFAULT_NY = 300
DEFAULT_CELL = 1.0
DEFAULT_SEED = 7


def _base(nx, ny, cell, rng):
    """Волнистое основание: наклон плюс две гармоники, метров пять размаха."""
    xx = np.tile(np.arange(nx, dtype=float), (ny, 1)) * cell
    yy = np.tile(np.arange(ny, dtype=float)[:, None], (1, nx)) * cell
    z = 120.0 + 0.004 * xx - 0.003 * yy
    z += 1.5 * np.sin(xx / 90.0 + rng.uniform(0, 6.28))
    z += 1.2 * np.cos(yy / 70.0 + rng.uniform(0, 6.28))
    return z, xx, yy


def _ellipse_pts(cx, cy, rx, ry, n=180, a0=0.0, a1=2.0 * np.pi):
    a = np.linspace(a0, a1, n)
    return np.stack([cx + rx * np.cos(a), cy + ry * np.sin(a)], axis=1)


def generate(nx=DEFAULT_NX, ny=DEFAULT_NY, cell=DEFAULT_CELL,
             seed=DEFAULT_SEED, benches=3, bench_h=10.0, slope_deg=55.0,
             berm_w=8.0, noise=0.03, dump=True, ditch=True):
    """Рельеф демо-карьера и истинные линии.

    Возвращает (z, truth), где truth - список
    dict(kind, link, pts=[(x, y, z)...]). kind: brow, toe, thalweg.
    link - значение поля связи, одно на пару. Координаты pts в метрах от
    верхнего левого угла, x на восток, y на юг (как строки растра).
    Дуга съезда исключена из линий уступов: там честный разрыв.
    """
    rng = np.random.default_rng(int(seed))
    z, xx, yy = _base(nx, ny, cell, rng)
    truth = []

    w, h = nx * cell, ny * cell
    cx, cy = 0.42 * w, 0.52 * h
    rx0, ry0 = 0.30 * w, 0.36 * h
    ws = bench_h / np.tan(np.radians(slope_deg))     # заложение откоса

    # эллиптическая метрика: расстояние наружу от верхней бровки, в метрах
    ex = (xx - cx) / rx0
    ey = (yy - cy) / ry0
    rho = np.sqrt(ex * ex + ey * ey)                  # 1.0 на верхней бровке
    r_m = (rho - 1.0) * min(rx0, ry0)                 # ~метры от бровки

    # профиль карьера от расстояния внутрь: чередование откос-берма, дно
    depth = np.zeros_like(z)
    z_top = None
    inward = -r_m
    for i in range(int(benches)):
        s0 = i * (ws + berm_w)                        # начало откоса i
        t = np.clip((inward - s0) / ws, 0.0, 1.0)     # доля откоса пройдена
        depth += t * bench_h
    z_pit = z - depth
    pit_mask = rho < 1.0 + 2.0 / min(rx0, ry0)

    # съезд: сектор, где вместо уступов пандус от базы до дна
    a_ramp = rng.uniform(0.0, 2.0 * np.pi)
    da = np.radians(16.0)
    ang = np.arctan2(ey, ex)
    d_ang = np.abs((ang - a_ramp + np.pi) % (2.0 * np.pi) - np.pi)
    in_ramp = d_ang < da
    total_in = benches * (ws + berm_w) - berm_w
    t_ramp = np.clip(inward / max(total_in, 1e-9), 0.0, 1.0)
    z_ramp = z - t_ramp * benches * bench_h
    zf = np.where(pit_mask & in_ramp, z_ramp,
                  np.where(pit_mask, z_pit, z))

    # истинные линии уступов: эллипсы, дуга съезда выброшена
    gap = da * 1.15
    for i in range(int(benches)):
        s_brow = i * (ws + berm_w)
        s_toe = s_brow + ws
        for kind, s in (("brow", s_brow), ("toe", s_toe)):
            shrink = 1.0 - s / min(rx0, ry0)
            if shrink <= 0.05:
                continue
            a0 = a_ramp + gap
            a1 = a_ramp + 2.0 * np.pi - gap
            pts = _ellipse_pts(cx, cy, rx0 * shrink, ry0 * shrink,
                               n=160, a0=a0, a1=a1)
            zs = []
            for x, y in pts:
                r = int(min(max(y / cell, 0), ny - 1))
                c = int(min(max(x / cell, 0), nx - 1))
                zs.append(float(zf[r, c]))
            truth.append({"kind": kind, "link": "bench-%d" % (i + 1),
                          "pts": [(float(x), float(y), zv)
                                  for (x, y), zv in zip(pts, zs)]})

    # отвал: усечённый конус с плоским верхом, замкнутая пара
    dcx, dcy = 0.82 * w, 0.28 * h
    r_top, h_dump = 0.06 * w, 12.0
    if not dump:
        r_top = -1.0                                  # пятно пустое
    ws_d = h_dump / np.tan(np.radians(35.0))
    rd = np.hypot(xx - dcx, yy - dcy)
    t = np.clip(1.0 - (rd - r_top) / ws_d, 0.0, 1.0)
    # поднимать только в пятне отвала: вне его t = 0 и z_dump равен базе,
    # а максимум с базой затёр бы карьер обратно
    zf = np.where(t > 0.0, np.maximum(zf, z + t * h_dump), zf)
    z_top_dump = None
    for kind, rr in ((("brow", r_top), ("toe", r_top + ws_d))
                     if dump else ()):
        pts = _ellipse_pts(dcx, dcy, rr, rr, n=120)
        zs = []
        for x, y in pts:
            r = int(min(max(y / cell, 0), ny - 1))
            c = int(min(max(x / cell, 0), nx - 1))
            zs.append(float(zf[r, c]))
        truth.append({"kind": kind, "link": "dump",
                      "pts": [(float(x), float(y), zv)
                              for (x, y), zv in zip(pts, zs)]})

    # сходящаяся канава: от точки схождения вглубь, глубина растёт от нуля
    if not ditch:
        zf = zf + rng.normal(0.0, float(noise), zf.shape)
        return zf.astype(np.float64), truth
    gx0, gy0 = 0.72 * w, 0.86 * h                     # точка схождения
    gx1, gy1 = 0.95 * w, 0.95 * h
    length = np.hypot(gx1 - gx0, gy1 - gy0)
    ux, uy = (gx1 - gx0) / length, (gy1 - gy0) / length
    nxv, nyv = -uy, ux                                 # нормаль к оси
    px = xx - gx0
    py = yy - gy0
    s_along = px * ux + py * uy                        # вдоль оси, метры
    s_across = np.abs(px * nxv + py * nyv)             # поперёк
    g_depth = np.clip(s_along / length, 0.0, 1.0) * 2.0
    half_w = 1.0 + np.clip(s_along / length, 0.0, 1.0) * 4.0
    cut = np.clip(1.0 - s_across / np.maximum(half_w, 1e-9), 0.0, 1.0)
    in_ditch = (s_along >= 0.0) & (s_along <= length)
    zf = np.where(in_ditch, zf - cut * g_depth, zf)

    npts = 40
    ss = np.linspace(0.0, length, npts)
    for kind, side in (("thalweg", 0.0), ("brow", 1.0), ("brow", -1.0)):
        pts = []
        for s in ss:
            hw = 1.0 + (s / length) * 4.0
            x = gx0 + ux * s + nxv * side * hw
            y = gy0 + uy * s + nyv * side * hw
            r = int(min(max(y / cell, 0), ny - 1))
            c = int(min(max(x / cell, 0), nx - 1))
            pts.append((float(x), float(y), float(zf[r, c])))
        truth.append({"kind": kind, "link": "ditch", "pts": pts})

    zf = zf + rng.normal(0.0, float(noise), zf.shape)
    return zf.astype(np.float64), truth
