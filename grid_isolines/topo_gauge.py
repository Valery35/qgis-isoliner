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


# --- линейный водосбор: канава, лоток, дорога ----------------------------
#
# Задача из QGIS Курилки: площадь водосбора нагорной канавы, которой нет на
# ЦМР. Врезать трассу в рельеф для этого не нужно. Трасса растеризуется в
# ячейки, все они берутся семенами, и водосбор это множество ячеек, чей путь
# стока приходит в любую ячейку трассы. Врезка нужна для другого вопроса -
# удержит ли канава поток, - и потому остаётся отдельной необязательной
# операцией.

def cells_along_polyline(vertices, origin_x, origin_y, cell, shape,
                         step=0.5):
    """Ячейки, через которые проходит ломаная: список плоских индексов.

    vertices - точки (x, y) в координатах карты, origin_x и origin_y -
    левый верхний угол грида, cell - размер ячейки, shape - (ny, nx).
    Сегменты идут с шагом step ячейки, поэтому пропусков на диагоналях нет.
    Порядок обхода сохраняется, повторы убираются, точки вне грида
    отбрасываются (трасса может выходить за рамку ЦМР).
    """
    ny, nx = shape
    out, seen = [], set()
    if not vertices:
        return out

    def _put(x, y):
        c = int(math.floor((x - origin_x) / cell))
        r = int(math.floor((origin_y - y) / cell))
        if 0 <= r < ny and 0 <= c < nx:
            idx = r * nx + c
            if idx not in seen:
                seen.add(idx)
                out.append(idx)

    _put(*vertices[0])
    for i in range(1, len(vertices)):
        x1, y1 = vertices[i - 1]
        x2, y2 = vertices[i]
        d = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(math.ceil(d / (cell * float(step)))))
        for k in range(1, n + 1):
            t = k / float(n)
            _put(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
    return out


def polyline_length(vertices):
    """Длина ломаной в единицах карты."""
    return sum(math.hypot(vertices[i][0] - vertices[i - 1][0],
                          vertices[i][1] - vertices[i - 1][1])
               for i in range(1, len(vertices)))


def cells_in_polygon(rings, origin_x, origin_y, cell, shape):
    """Ячейки внутренности полигона плюс ячейки его контура: плоские индексы.

    rings - внешние кольца полигона, каждое кольцо это список точек (x, y)
    в координатах карты. Дырки сюда не передаются: затравка контура
    (например карьера) берётся сплошной внутренностью, внутри залитой
    депрессии направления стока условны и полагаться на них нельзя.

    Внутренность собирается строчной развёрткой по центрам ячеек
    (полуоткрытое правило пересечения ребра со строкой), контур
    добавляется проходом cells_along_polyline, чтобы узкие части уже
    одной ячейки не выпадали. Точки вне грида отбрасываются, повторы
    убираются, кольцо замыкается само, если последняя точка не равна
    первой.
    """
    ny, nx = shape
    out, seen = [], set()

    def _put(idx):
        if idx not in seen:
            seen.add(idx)
            out.append(idx)

    for ring in rings or []:
        pts = [(float(p[0]), float(p[1])) for p in ring]
        if len(pts) < 3:
            continue
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        ys = [p[1] for p in pts]
        r0 = max(0, int(math.floor((origin_y - max(ys)) / cell)))
        r1 = min(ny - 1, int(math.floor((origin_y - min(ys)) / cell)))
        for r in range(r0, r1 + 1):
            yc = origin_y - (r + 0.5) * cell
            xs = []
            for i in range(1, len(pts)):
                x1, y1 = pts[i - 1]
                x2, y2 = pts[i]
                if (y1 <= yc < y2) or (y2 <= yc < y1):
                    xs.append(x1 + (x2 - x1) * (yc - y1) / (y2 - y1))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                c0 = max(0, int(math.ceil((xs[k] - origin_x) / cell - 0.5)))
                c1 = min(nx - 1, int(math.floor(
                    (xs[k + 1] - origin_x) / cell - 0.5)))
                for c in range(c0, c1 + 1):
                    _put(r * nx + c)
        for idx in cells_along_polyline(pts, origin_x, origin_y, cell,
                                        shape):
            _put(idx)
    return out


def catchment_mask(downstream, shape, seed_idxs):
    """Водосбор набора ячеек: True у всех, чей путь стока приходит в любое
    из семян. Сами семена входят в водосбор."""
    seeds = {int(i): 1 for i in seed_idxs}
    if not seeds:
        return np.zeros(shape, dtype=bool)
    return topo_flow.basins(downstream, shape, seeds=seeds) == 1


def burn_trace(z, seed_idxs, depth, nodata_mask=None):
    """Врезка трассы: копия рельефа, опущенная на depth в ячейках трассы.

    Меняет гидрологию нарочно и применяется только по просьбе пользователя:
    отвечает на вопрос «удержит ли канава поток», а не «какую площадь она
    перехватывает». Ячейки nodata не трогаются.
    """
    out = np.array(z, dtype=np.float64, copy=True)
    if depth <= 0 or not len(seed_idxs):
        return out
    flat = out.ravel()
    idx = np.asarray(list(seed_idxs), dtype=np.int64)
    if nodata_mask is not None:
        keep = ~np.asarray(nodata_mask, dtype=bool).ravel()[idx]
        idx = idx[keep]
    flat[idx] -= float(depth)
    return out


def burn_trace_sloped(z, ordered_runs, depth, slope, cell, nodata_mask=None):
    """Врезка жёлобом: дно опускается монотонно в сторону стока.

    Приём Ивана Иванова из обсуждения водосборов: постоянная глубина не
    гарантирует, что поток пойдёт по канаве - при локальных вариациях
    рельефа дно канавы может подниматься по ходу трассы, и вода
    переваливает через борт. Жёлоб решает это монотонностью: дно каждой
    трассы не имеет права расти в направлении стока.

    ordered_runs - список трасс, каждая это плоские индексы ячеек В ПОРЯДКЕ
    ВДОЛЬ ЛИНИИ (как их отдаёт cells_along_polyline). Направление стока
    выбирается по концам врезанного профиля: к более низкому концу.
    Дно: сначала z минус depth, затем бегущий минимум в направлении стока
    с обязательным падением slope*cell на ячейку. Итоговая отметка ячейки
    это минимум исходной врезки и дна, то есть выше «рельеф минус depth»
    дно не поднимается никогда.

    Точных расстояний между ячейками не считается: диагональный шаг принят
    равным cell. Уклон здесь подбираемый параметр, а не измеренная
    величина, и такое упрощение.

    Ячейки nodata пропускаются и не участвуют ни в профиле, ни в дне.
    """
    out = np.array(z, dtype=np.float64, copy=True)
    if depth <= 0 or not ordered_runs:
        return out
    flat = out.ravel()
    nd = (np.asarray(nodata_mask, dtype=bool).ravel()
          if nodata_mask is not None else None)
    step = float(slope) * float(cell)
    for run in ordered_runs:
        idx = [int(i) for i in run if nd is None or not nd[i]]
        if not idx:
            continue
        ai = np.asarray(idx, dtype=np.int64)
        prof = flat[ai] - float(depth)
        if len(idx) > 1 and prof[0] < prof[-1]:
            order = slice(None, None, -1)      # сток к началу трассы
        else:
            order = slice(None)                # сток к концу
        p = prof[order].copy()
        for k in range(1, len(p)):
            p[k] = min(p[k], p[k - 1] - step)
        prof[order] = p
        flat[ai] = prof
    return out


# ключи отчёта по линейному водосбору, в порядке вывода
DITCH_KEYS = (
    "area_km2",       # площадь водосбора, км²
    "z_mean",         # средняя высота, м
    "z_min",          # минимальная высота, м
    "z_max",          # максимальная высота, м
    "slope_mean",     # средний уклон водосбора (в единицах растра)
    "trace_km",       # длина трассы или контура, км
    "seed_km2",       # площадь затравки, км² (у линии близка к нулю)
    "trace_cells",    # ячеек затравки на гриде
    "cells",          # ячеек в водосборе
)


def ditch_report(z, downstream, shape, seed_idxs, cellsize, trace_len=None,
                 slope=None, nodata_mask=None):
    """Морфометрия водосбора линии: словарь по DITCH_KEYS.

    Трассировки главного водотока здесь нет: у линейного приёмника она
    бессмысленна, вода приходит в трассу со всей площади. Недоступные
    величины отдаются как None, а не как ноль.
    """
    mask = catchment_mask(downstream, shape, seed_idxs)
    if nodata_mask is not None:
        mask = mask & ~np.asarray(nodata_mask, dtype=bool)
    rep = dict.fromkeys(DITCH_KEYS)
    n_cells = int(mask.sum())
    rep["cells"] = n_cells
    rep["trace_cells"] = len(seed_idxs)
    rep["area_km2"] = n_cells * float(cellsize) ** 2 / 1e6
    rep["seed_km2"] = len(seed_idxs) * float(cellsize) ** 2 / 1e6
    rep["z_mean"], rep["z_min"], rep["z_max"] = zonal_stats(
        z, mask, nodata_mask)
    if slope is not None:
        rep["slope_mean"], _, _ = zonal_stats(slope, mask, nodata_mask)
    if trace_len is not None:
        rep["trace_km"] = float(trace_len) / 1000.0
    return rep


def ditch_report_lines(rep, tr=None):
    """Сводка по линейному водосбору для экрана: две строки."""
    t = tr if tr is not None else (lambda s: s)

    def f(v, fmt="%.2f"):
        return "-" if v is None else fmt % v

    return [
        t("Водосбор: %s км², высоты %s...%s м, средняя %s м.") % (
            f(rep.get("area_km2"), "%.3f"), f(rep.get("z_min")),
            f(rep.get("z_max")), f(rep.get("z_mean"))),
        t("Трасса: %s км, ячеек трассы %s, ячеек водосбора %s.") % (
            f(rep.get("trace_km"), "%.3f"), rep.get("trace_cells"),
            rep.get("cells")),
    ]
