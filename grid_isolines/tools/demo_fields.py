# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Поля с пространственной структурой для демо-генераторов, без QGIS.

Перенесены из algorithms.py (_demo_field, _demo_sample, _demo_values)
без изменений, там они остались под прежними именами импортом отсюда.
Порядок обращений к генератору rng тот же, поэтому демо с зерном дают
те же числа.
"""

import numpy as np


def demo_field(rng, G, w):
    """Коррелированное поле GxG: белый шум, сглаженный скользящим средним
    (окно 2w+1, три прохода ≈ гауссово ядро). Края корректируются делением
    на количество валидных отсчётов. Возвращает поле со средним 0 и ст.откл. 1."""
    f = rng.standard_normal((G, G))
    k = np.ones(2 * w + 1)
    d = np.convolve(np.ones(G), k, "same")
    for _ in range(3):
        f = np.apply_along_axis(lambda m: np.convolve(m, k, "same"), 0, f) / d[:, None]
        f = np.apply_along_axis(lambda m: np.convolve(m, k, "same"), 1, f) / d[None, :]
    s = f.std() or 1.0
    return (f - f.mean()) / s


def demo_sample(f, xs, ys, xmin, xmax, ymin, ymax):
    """Билинейная выборка значений поля f в точках (xs, ys)."""
    G = f.shape[0]
    fx = (xs - xmin) / ((xmax - xmin) or 1.0) * (G - 1)
    fy = (ys - ymin) / ((ymax - ymin) or 1.0) * (G - 1)
    x0 = np.clip(np.floor(fx).astype(int), 0, G - 1)
    x1 = np.clip(x0 + 1, 0, G - 1)
    y0 = np.clip(np.floor(fy).astype(int), 0, G - 1)
    y1 = np.clip(y0 + 1, 0, G - 1)
    tx = fx - x0
    ty = fy - y0
    v0 = f[y0, x0] * (1 - tx) + f[y0, x1] * tx
    v1 = f[y1, x0] * (1 - tx) + f[y1, x1] * tx
    return v0 * (1 - ty) + v1 * ty


def demo_values(rng, G, w, xs, ys, ext, vmin, vmax, nf):
    """Сгенерировать значения в [vmin, vmax] с пространственной структурой:
    сглаженное поле (структура) + независимый шум (наггет долей nf дисперсии)."""
    field = demo_field(rng, G, w)
    s = demo_sample(field, xs, ys, ext[0], ext[1], ext[2], ext[3])
    mean = 0.5 * (vmin + vmax)
    tstd = 0.25 * (vmax - vmin)
    struct = s * (np.sqrt(max(1.0 - nf, 0.0)) * tstd)
    noise = rng.standard_normal(len(xs)) * (np.sqrt(max(nf, 0.0)) * tstd)
    return np.clip(mean + struct + noise, vmin, vmax)
