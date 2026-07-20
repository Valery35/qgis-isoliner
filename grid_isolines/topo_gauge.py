# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Ядро отчёта по створу: морфометрия водосбора от точки замыкания.

Чистая математика поверх решётки topo_flow (D8, аккумуляция, бассейны),
без QGIS и Qt. Инструмент группы «Топография» зовёт эти функции, тесты
работают с ними напрямую на синтетическом рельефе с ручными эталонами.

Соглашения те же, что в topo_flow: грид (ny, nx), плоский индекс
idx = r * nx + c, downstream - плоский индекс приёмника или -1 у стока,
cellsize в единицах карты. Площади и длины предполагают метрические
единицы (метры), перевод в километры и квадратные километры делает
gauge_report.

Сознательно не входит (из задания): расходы, модули стока, гидравлика,
снеготаяние. Только морфометрия бассейна.
"""
import math

import numpy as np

from . import topo_flow


def snap_to_max_acc(acc, r, c, radius_cells):
    """Притяжка створа: ячейка наибольшей аккумуляции в квадратном окне.

    Та же механика, что в инструменте «Бассейны» (2.07), но в чистом виде:
    (r, c) - ячейка, куда попала точка пользователя, radius_cells - радиус
    окна в ячейках (0 - без притяжки). Возвращает (r, c) ячейки максимума.
    При равных значениях берётся первая по порядку обхода окна (детерминизм
    np.argmax).
    """
    ny, nx = acc.shape
    r = min(max(int(r), 0), ny - 1)
    c = min(max(int(c), 0), nx - 1)
    k = max(int(radius_cells), 0)
    if k == 0:
        return r, c
    r0, r1 = max(0, r - k), min(ny, r + k + 1)
    c0, c1 = max(0, c - k), min(nx, c + k + 1)
    win = acc[r0:r1, c0:c1]
    dr, dc = np.unravel_index(int(np.argmax(win)), win.shape)
    return r0 + int(dr), c0 + int(dc)


def basin_mask(downstream, shape, seed_idx):
    """Маска водосбора от створа: True у ячеек, стекающих через семя
    (включая само семя). Обход - прыжками указателей из topo_flow.basins."""
    label = topo_flow.basins(downstream, shape, seeds={int(seed_idx): 1})
    return label == 1


def zonal_stats(values, mask, nodata_mask=None):
    """Среднее, минимум и максимум по маске: (mean, vmin, vmax).

    nodata и нечисловые значения исключаются. Пустая выборка даёт тройку
    None - вызывающий решает, что писать в атрибуты.
    """
    m = np.asarray(mask, dtype=bool)
    if nodata_mask is not None:
        m = m & ~np.asarray(nodata_mask, dtype=bool)
    v = np.asarray(values, dtype=np.float64)[m]
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None, None, None
    return float(v.mean()), float(v.min()), float(v.max())


def trace_main_stream(downstream, acc, shape, start_idx, cellsize):
    """Главный водоток вверх от створа: (длина, путь плоскими индексами).

    Из текущей ячейки шаг в того соседа, который стекает в неё и несёт
    наибольшую аккумуляцию, до ячейки без притоков (исток). Длина
    складывается из шагов решётки (диагональ с корнем из двух) на размер
    ячейки. Детерминизм: при равной аккумуляции берётся первый сосед по
    порядку _D8. Путь идёт от створа к истоку, начинается со створа.
    """
    ny, nx = shape
    accf = np.asarray(acc, dtype=np.float64).ravel()
    down = np.asarray(downstream)
    cur = int(start_idx)
    path = [cur]
    total = 0.0
    for _ in range(ny * nx + 1):  # страховка от зацикливания
        r, c = divmod(cur, nx)
        best = None
        best_acc = -math.inf
        best_dist = 0.0
        for (dr, dc, _esri, dist) in topo_flow._D8:
            rr, cc = r + dr, c + dc
            if not (0 <= rr < ny and 0 <= cc < nx):
                continue
            n = rr * nx + cc
            if down[n] == cur and accf[n] > best_acc:
                best_acc = accf[n]
                best = n
                best_dist = dist
        if best is None:
            break
        total += best_dist * float(cellsize)
        path.append(best)
        cur = best
    return total, path


# ключи отчёта, в порядке вывода в атрибуты и таблицу
REPORT_KEYS = (
    "area_km2",       # площадь бассейна, км²
    "z_mean",         # средняя высота, м
    "z_min",          # минимальная высота, м
    "z_max",          # максимальная высота, м
    "slope_mean",     # средний уклон бассейна (в единицах поданного растра)
    "z_gauge",        # отметка створа, м
    "stream_km",      # длина главного водотока от створа до истока, км
    "stream_fall_m",  # падение водотока, м
    "stream_ppm",     # средний уклон водотока, промилле
    "cells",          # ячеек в бассейне (служебно, для контроля)
)


def gauge_report(z, downstream, acc, shape, seed_idx, cellsize,
                 slope=None, nodata_mask=None):
    """Морфометрия бассейна от створа: словарь по REPORT_KEYS.

    z - отметки, downstream и acc - решётка стока, seed_idx - плоский
    индекс створа (уже притянутого), cellsize - размер ячейки в метрах,
    slope - необязательный растр уклона (средний уклон бассейна считается
    в его же единицах, инструмент решает, градусы это или промилле).
    Недоступные величины отдаются как None, а не как ноль: ноль это
    измерение, None это отсутствие измерения.
    """
    mask = basin_mask(downstream, shape, seed_idx)
    if nodata_mask is not None:
        mask = mask & ~np.asarray(nodata_mask, dtype=bool)
    n_cells = int(mask.sum())
    rep = dict.fromkeys(REPORT_KEYS)
    rep["cells"] = n_cells
    rep["area_km2"] = n_cells * float(cellsize) ** 2 / 1e6
    rep["z_mean"], rep["z_min"], rep["z_max"] = zonal_stats(
        z, mask, nodata_mask)
    if slope is not None:
        rep["slope_mean"], _, _ = zonal_stats(slope, mask, nodata_mask)
    zf = np.asarray(z, dtype=np.float64).ravel()
    zg = zf[int(seed_idx)]
    rep["z_gauge"] = float(zg) if math.isfinite(zg) else None

    length, path = trace_main_stream(downstream, acc, shape, seed_idx,
                                     cellsize)
    if length > 0 and len(path) > 1:
        z0, z1 = zf[path[0]], zf[path[-1]]
        rep["stream_km"] = length / 1000.0
        if math.isfinite(z0) and math.isfinite(z1):
            fall = float(z1 - z0)
            rep["stream_fall_m"] = fall
            rep["stream_ppm"] = fall / length * 1000.0
    return rep


def report_lines(rep, tr=None):
    """Сводка по створу для экрана, по принципу машины УК: ключевые цифры
    одной или двумя строками. tr - необязательный переводчик шаблонов."""
    t = tr if tr is not None else (lambda s: s)

    def f(v, fmt="%.2f"):
        return "-" if v is None else fmt % v

    out = [t("Бассейн: %s км², высоты %s...%s м, средняя %s м.") % (
        f(rep.get("area_km2"), "%.3f"), f(rep.get("z_min")),
        f(rep.get("z_max")), f(rep.get("z_mean")))]
    out.append(t("Водоток: %s км, падение %s м, уклон %s промилле, "
                 "отметка створа %s м.") % (
        f(rep.get("stream_km"), "%.3f"), f(rep.get("stream_fall_m")),
        f(rep.get("stream_ppm"), "%.1f"), f(rep.get("z_gauge"))))
    return out
