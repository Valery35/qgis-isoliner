# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""7.05 Пример для фракталов (демо): расчёт без QGIS.

Поля - ключи параметров класса FractalDemoAlgorithm. Охват EXTENT
приходит объектами (features), как у 1.11. Выходы OUT_RIVERS (линии с
полем order), OUT_BASIN (полигон водосбора, geometry="polygon") и
OUT_COAST (береговая линия). Генератор NumPy default_rng(SEED), при
SEED=0 тоже с зерном 0, как было в классе. Порядок обращений к
генератору тот же: реки, затем водосбор, затем берег.

Имена объектов («Водосбор (демо)», «Берег (демо)») - слова интерфейса,
в QGIS на языке окна, без него русские.
"""

import numpy as np

from ..toolspec import N_, Result, ToolError
from .geophys_profiles import bbox
from .vario import word

SPEC_OVERRIDES = {
    "EXTENT": {"kind": "features",
               "label": "Слой, по охвату которого строить",
               "hint": "Демо-объекты ложатся в охват объектов этого слоя."},
}


def midpoint(p0, p1, rough, depth, rng):
    """Фрактальная ломаная срединных смещений между двумя точками."""
    pts = [np.array(p0, float), np.array(p1, float)]
    for _ in range(depth):
        out = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            m = (a + b) / 2.0
            d = b - a
            n = np.array([-d[1], d[0]])
            L = float(np.hypot(*d))
            m = m + n / (L + 1e-12) * rng.normal(0, rough * L)
            out += [m, b]
        pts = out
    return np.array(pts)


def _xyz(pts):
    a = np.asarray(pts, dtype=float).reshape(-1, 2)
    return np.column_stack([a, np.full(len(a), np.nan)])


def run(values, report):
    ext = bbox(values.get("EXTENT"))
    if ext is None or not (ext[2] > ext[0] and ext[3] > ext[1]):
        raise ToolError(N_("Не задан охват."))
    x0, y0 = ext[0], ext[1]
    W, H = ext[2] - ext[0], ext[3] - ext[1]
    rng = np.random.default_rng(int(values.get("SEED") or 0))

    # речная сеть: рекурсивное ветвление со срединной шершавостью
    rivers = []

    def branch(p, ang, L, order, depth):
        if depth <= 0 or L < 0.02 * min(W, H):
            return
        q = p + L * np.array([np.cos(ang), np.sin(ang)])
        pl = midpoint(p, q, 0.10, 3, rng)
        rivers.append((order, pl))
        k = rng.integers(2, 4)
        for i in range(k):
            tt = 0.3 + 0.6 * rng.random()
            bp = pl[int(tt * (len(pl) - 1))]
            da = np.deg2rad(25 + 25 * rng.random()) * (1 if i % 2 == 0
                                                       else -1)
            branch(bp, ang + da, L * (0.45 + 0.15 * rng.random()),
                   order + 1, depth - 1)

    start = np.array([x0 + 0.5 * W, y0 + 0.06 * H])
    branch(start, np.pi / 2, 0.8 * H, 1, 4)
    out_r = Result([("order", "int")], geometry="line")
    for order, pl in rivers:
        out_r.add({"points": _xyz(pl)}, [int(order)])

    # водосбор: изрезанный овал вокруг сети
    cx, cy = x0 + 0.5 * W, y0 + 0.5 * H
    nang = 24
    base = []
    for i in range(nang):
        a = 2 * np.pi * i / nang
        r = (0.42 + 0.06 * rng.random())
        base.append((cx + r * W * np.cos(a), cy + r * H * np.sin(a)))
    ring = []
    for a, b in zip(base, base[1:] + base[:1]):
        seg = midpoint(a, b, 0.16, 3, rng)
        ring.extend(seg[:-1])
    ring.append(ring[0])
    out_b = Result([("name", "string")], geometry="polygon")
    out_b.add({"rings": [_xyz(ring)]}, [word(report, N_("Водосбор (демо)"))])

    # берег: одна сильно изрезанная линия через охват
    coast = midpoint((x0 + 0.05 * W, y0 + 0.85 * H),
                     (x0 + 0.95 * W, y0 + 0.7 * H), 0.22, 8, rng)
    out_c = Result([("name", "string")], geometry="line")
    out_c.add({"points": _xyz(coast)}, [word(report, N_("Берег (демо)"))])

    report.info(N_("Рек сгенерировано: %d."), len(rivers))
    return {"OUT_RIVERS": out_r, "OUT_BASIN": out_b, "OUT_COAST": out_c}
