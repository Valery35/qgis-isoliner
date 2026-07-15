# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Гидрология рельефа на чистом NumPy: направления стока D8
(Jenson-Domingue 1988), аккумуляция векторизованным обходом Кана,
трассировка речной сети с порядком Стралера, бассейны через
прыжки указателей (pointer doubling).

Соглашения. Ось строк направлена на юг (стандартный GeoTIFF с
отрицательным шагом по Y): строка 0 северная. Коды направлений ESRI:
E=1, SE=2, S=4, SW=8, W=16, NW=32, N=64, NE=128, сток (яма или выход
с грида) = 0. Вход - ЦМР после заполнения понижений с epsilon больше
нуля: на плоскостях без уклона D8 не определён.
"""

import numpy as np

# (dr, dc, esri, dist)
_D8 = (
    (0, 1, 1, 1.0),
    (1, 1, 2, 2.0 ** 0.5),
    (1, 0, 4, 1.0),
    (1, -1, 8, 2.0 ** 0.5),
    (0, -1, 16, 1.0),
    (-1, -1, 32, 2.0 ** 0.5),
    (-1, 0, 64, 1.0),
    (-1, 1, 128, 2.0 ** 0.5),
)

ESRI_CODES = tuple(d[2] for d in _D8)
NODIR = -1  # внутренний индекс: сток


def _shift(a, dr, dc, fill):
    """Сдвиг массива: значение соседа (r+dr, c+dc) в ячейке (r, c)."""
    out = np.full_like(a, fill)
    src_r = slice(max(dr, 0), a.shape[0] + min(dr, 0))
    dst_r = slice(max(-dr, 0), a.shape[0] + min(-dr, 0))
    src_c = slice(max(dc, 0), a.shape[1] + min(dc, 0))
    dst_c = slice(max(-dc, 0), a.shape[1] + min(-dc, 0))
    out[dst_r, dst_c] = a[src_r, src_c]
    return out


def d8_directions(z, nodata_mask=None):
    """Направления стока D8.

    Возвращает (dir_idx, downstream):
    dir_idx int8 (ny, nx): индекс направления 0..7 в _D8, NODIR у стока
    (нет соседа ниже, выход с грида или в nodata) и в nodata-ячейках.
    downstream int64 (ny*nx,): плоский индекс приёмника, -1 у стока.

    Сосед nodata (море, вырез) считается бесконечно низким: береговая
    ячейка льёт в него. Заграничье, наоборот, недостижимо: ячейка на
    рамке уходит с грида только если у неё нет более низкого соседа
    внутри, иначе поток вдоль рамки рвался бы. Из двух равных уклонов
    берётся первый по списку _D8 (детерминизм).
    """
    z = np.asarray(z, dtype=np.float64)
    ny, nx = z.shape
    if nodata_mask is None:
        nodata_mask = ~np.isfinite(z)

    best_slope = np.full(z.shape, 0.0)
    dir_idx = np.full(z.shape, NODIR, dtype=np.int8)
    for k, (dr, dc, _esri, dist) in enumerate(_D8):
        nb = _shift(z, dr, dc, np.inf)               # заграничье: стенка
        nb_nd = _shift(nodata_mask, dr, dc, False)   # соседство с nodata
        nb = np.where(nb_nd, -np.inf, nb)            # nodata-сосед: слив
        slope = (z - nb) / dist
        better = slope > best_slope
        dir_idx[better] = k
        best_slope[better] = slope[better]
    dir_idx[nodata_mask] = NODIR

    flat = np.arange(ny * nx, dtype=np.int64)
    rows, cols = np.divmod(flat, nx)
    downstream = np.full(ny * nx, -1, dtype=np.int64)
    for k, (dr, dc, _esri, _dist) in enumerate(_D8):
        sel = (dir_idx.ravel() == k)
        if not sel.any():
            continue
        rr = rows[sel] + dr
        cc = cols[sel] + dc
        ok = (rr >= 0) & (rr < ny) & (cc >= 0) & (cc < nx)
        tgt = np.where(ok, rr * nx + cc, -1)
        # приёмник в nodata равносилен выходу с грида
        if nodata_mask.any():
            nd = nodata_mask.ravel()
            inside = tgt >= 0
            tgt[inside & nd[np.maximum(tgt, 0)]] = -1
        downstream[np.flatnonzero(sel)] = tgt
    return dir_idx, downstream


def dir_to_esri(dir_idx):
    """Индексы 0..7 в коды ESRI, сток и nodata в 0."""
    esri = np.zeros(dir_idx.shape, dtype=np.uint8)
    for k, (_dr, _dc, code, _dist) in enumerate(_D8):
        esri[dir_idx == k] = code
    return esri


def flow_accumulation(downstream, shape, nodata_mask=None):
    """Аккумуляция: число ячеек, стекающих в ячейку, включая её саму.

    Векторизованный обход Кана: считаем входящие степени, двигаем
    фронт готовых ячеек, передаём их суммы приёмникам через np.add.at.
    Работает и на конфигурациях с равными высотами: важен только сам
    граф downstream (без циклов, что гарантирует заполнение с epsilon).
    """
    n = shape[0] * shape[1]
    acc = np.ones(n, dtype=np.float64)
    if nodata_mask is not None:
        acc[nodata_mask.ravel()] = 0.0
    indeg = np.zeros(n, dtype=np.int64)
    valid = downstream >= 0
    np.add.at(indeg, downstream[valid], 1)

    frontier = np.flatnonzero((indeg == 0))
    if nodata_mask is not None:
        frontier = frontier[~nodata_mask.ravel()[frontier]]
    while frontier.size:
        ds = downstream[frontier]
        has_ds = ds >= 0
        ds = ds[has_ds]
        np.add.at(acc, ds, acc[frontier[has_ds]])
        np.add.at(indeg, ds, -1)
        cand = np.unique(ds)
        frontier = cand[indeg[cand] == 0]
    return acc.reshape(shape)


def _in_degree_masked(downstream, mask_flat):
    """Входящая степень внутри маски: сколько ячеек маски стекают сюда."""
    indeg = np.zeros(mask_flat.size, dtype=np.int32)
    src = np.flatnonzero(mask_flat)
    ds = downstream[src]
    ok = (ds >= 0)
    ds = ds[ok]
    ds = ds[mask_flat[ds]]
    np.add.at(indeg, ds, 1)
    return indeg


def river_network(downstream, acc, threshold, shape):
    """Речная сеть по порогу аккумуляции.

    Возвращает список звеньев: dict(cells=[плоские индексы вниз по
    течению], order=Стралер, acc_out=аккумуляция в замыкании звена).
    Звено идёт от истока или узла слияния до следующего слияния или
    до выхода из сети. Направление вершин - вниз по течению.
    """
    n = shape[0] * shape[1]
    mask = (acc.ravel() >= float(threshold))
    indeg = _in_degree_masked(downstream, mask)

    def is_junction(i):
        return indeg[i] >= 2

    heads = [int(i) for i in np.flatnonzero(mask & (indeg == 0))]
    starts = list(heads)
    for j in np.flatnonzero(mask & (indeg >= 2)):
        starts.append(int(j))

    links = []
    link_at_start = {}
    for s in starts:
        cells = [s]
        cur = s
        while True:
            ds = downstream[cur]
            if ds < 0 or not mask[ds]:
                break
            cells.append(int(ds))
            if is_junction(ds):
                break
            cur = int(ds)
        if len(cells) < 2:
            # исток, сразу упирающийся в слияние или край: точка не звено
            if len(cells) == 1 and downstream[s] >= 0 and mask[downstream[s]]:
                cells.append(int(downstream[s]))
            else:
                continue
        links.append({"cells": cells, "order": 0,
                      "acc_out": float(acc.ravel()[cells[-1]])})
        link_at_start[s] = len(links) - 1

    # Стралер: вливающиеся звенья каждого узла
    inflows = {}
    for li, lk in enumerate(links):
        end = lk["cells"][-1]
        inflows.setdefault(end, []).append(li)

    def resolve(li, depth=0):
        lk = links[li]
        if lk["order"]:
            return lk["order"]
        if depth > n:
            lk["order"] = 1
            return 1
        start = lk["cells"][0]
        ups = []
        if is_junction(start):
            for uli in inflows.get(start, []):
                ups.append(resolve(uli, depth + 1))
        if not ups:
            lk["order"] = 1
        else:
            top = max(ups)
            lk["order"] = top + 1 if ups.count(top) >= 2 else top
        return lk["order"]

    import sys
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 10000))
    try:
        for li in range(len(links)):
            resolve(li)
    finally:
        sys.setrecursionlimit(old)
    return links


def basins(downstream, shape, seeds=None, nodata_mask=None,
           acc=None, threshold=None):
    """Бассейны: метка каждой ячейки по её конечному стоку.

    seeds: dict {плоский индекс: метка > 0} - точки замыкания
    (ячейка-семя рвёт путь: всё, что стекает через неё, получает её
    метку). Без seeds метятся устья: ячейки, чей сток уходит с грида,
    а при заданных acc и threshold - только устья с аккумуляцией не
    ниже порога (остальное получает 0).

    Прыжки указателей: log(L) векторных проходов вместо обхода.
    """
    n = shape[0] * shape[1]
    down = downstream.copy()
    label = np.zeros(n, dtype=np.int32)

    if seeds:
        for idx, lab in seeds.items():
            label[idx] = lab
            down[idx] = -1  # семя терминально
    else:
        outlets = np.flatnonzero(down < 0)
        if nodata_mask is not None:
            outlets = outlets[~nodata_mask.ravel()[outlets]]
        if acc is not None and threshold is not None:
            outlets = outlets[acc.ravel()[outlets] >= float(threshold)]
        label[outlets] = np.arange(1, outlets.size + 1, dtype=np.int32)

    # терминалы указывают сами на себя
    term = down < 0
    ptr = np.where(term, np.arange(n, dtype=np.int64), down)
    for _ in range(64):  # 2**64 ячеек хватит всем
        nxt = ptr[ptr]
        if np.array_equal(nxt, ptr):
            break
        ptr = nxt
    out = label[ptr]
    if nodata_mask is not None:
        out[nodata_mask.ravel()] = 0
    return out.reshape(shape)
