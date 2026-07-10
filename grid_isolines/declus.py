# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Ячеистая декластеризация данных (порт GSLIB declus на NumPy).

Когда пробы сгущены неравномерно, наивная глобальная статистика (среднее,
гистограмма) смещается в сторону переразведанных участков. Декластеризация
даёт каждой пробе вес, обратный локальной плотности: сетка ячеек, вес пробы
пропорционален 1/(число проб в её ячейке), затем нормируется. По взвешенным
данным считаются представительное среднее и гистограмма. Размер ячейки
подбирается свипом (обычно по минимуму декластеризованного среднего, если
скопления пришлись на богатые зоны).

Чистый NumPy, без импорта QGIS - модуль под headless-тестами
(tests/test_declus.py).
"""
import numpy as np


def cell_declus(xs, ys, vs, cell_x, cell_y, noff=4):
    """Веса ячеистой декластеризации для одного размера ячейки.

    Усреднение по noff смещениям начала сетки убирает зависимость от того,
    куда легли границы ячеек. Возвращает (weights, decl_mean): веса
    нормированы так, что их сумма равна числу проб (среднее = 1).
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = xs.size
    if n == 0:
        return np.zeros(0), 0.0
    cell_x = float(cell_x) if cell_x > 0 else 1.0
    cell_y = float(cell_y) if cell_y > 0 else 1.0
    noff = max(1, int(noff))
    xmin, ymin = float(xs.min()), float(ys.min())
    acc = np.zeros(n)
    for io in range(noff):
        ox = xmin - cell_x * (io + 0.5) / noff
        oy = ymin - cell_y * (io + 0.5) / noff
        ix = np.floor((xs - ox) / cell_x).astype(np.int64)
        iy = np.floor((ys - oy) / cell_y).astype(np.int64)
        key = ix * 100000007 + iy
        _u, inv, counts = np.unique(key, return_inverse=True,
                                    return_counts=True)
        acc += 1.0 / counts[inv]
    w = acc / noff
    w = w * (n / w.sum())
    decl_mean = float(np.sum(w * vs) / np.sum(w))
    return w, decl_mean


def declus_sweep(xs, ys, vs, cell_min, cell_max, ncell=24, noff=4,
                 aspect=1.0, objective="min"):
    """Свип по размерам ячейки: для каждого - декластеризованное среднее,
    выбирается размер по минимуму (или максимуму) среднего.

    Возвращает словарь: best_cell, weights, decl_mean, naive_mean,
    sizes (массив размеров), means (декластеризованные средние).
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    ncell = max(1, int(ncell))
    lo = float(min(cell_min, cell_max))
    hi = float(max(cell_min, cell_max))
    if hi <= lo:
        hi = lo * 2.0 + 1e-9
    sizes = np.linspace(lo, hi, ncell)
    means = np.empty(ncell)
    wlist = []
    for k, c in enumerate(sizes):
        w, m = cell_declus(xs, ys, vs, c, c * float(aspect), noff)
        means[k] = m
        wlist.append(w)
    kbest = int(np.argmax(means) if objective == "max" else np.argmin(means))
    naive = float(np.mean(vs)) if vs.size else 0.0
    return dict(best_cell=float(sizes[kbest]), weights=wlist[kbest],
                decl_mean=float(means[kbest]), naive_mean=naive,
                sizes=sizes, means=means)


def suggest_range(xs, ys, ncell=24):
    """Разумный диапазон размеров ячейки для свипа: от среднего расстояния
    между ближайшими соседями до примерно половины стороны области."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    n = xs.size
    w = float(xs.max() - xs.min()) if n else 1.0
    h = float(ys.max() - ys.min()) if n else 1.0
    area = max(w * h, 1e-9)
    nn = np.sqrt(area / max(n, 1))          # ~ шаг сети
    cell_min = max(nn * 0.5, 1e-9)
    cell_max = max(w, h) * 0.5
    if cell_max <= cell_min:
        cell_max = cell_min * 10.0
    return cell_min, cell_max
