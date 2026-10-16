# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Снятие мостов с ЦМР (2.24). Чистый NumPy, без QGIS.

Спутниковая модель поверхности снимает высоту по верху настила, и мост
ложится поперёк реки плотиной. Сток по такой модели упирается в насыпь,
выше моста разливается ложное озеро, а речная сеть рвётся.

Ячейки под мостом пересчитываются по ячейкам вокруг него, по пролётам
(directional_fill). Из каждой ячейки берётся линейная интерполяция между
ближайшими известными ячейками по строке, столбцу и двум диагоналям. Из
направлений с самыми короткими пролётами выбирается самое низкое
значение. У ячейки под настилом пролёт с воды выше по течению на воду ниже
даёт уровень воды, а пролёт, задевший берег, даёт выше. У опор все
короткие пролёты идут по берегу, и поверхность выходит на берег.

Гармоническая интерполяция (среднее соседей) тут не годится, хоть и
гладкая: в узкой полосе она тянет значения и вдоль полосы, от высоких
берегов, и вода под настилом выходила на полметра выше воды по обе
стороны. Такой горб снова запирает сток. Не годится и вес по длине
пролёта: на насыпи через лог у Перми (GLO-30) кратчайшим оказался
диагональный пролёт в борт долины, и над руслом остался порог в 4.5 м.

Результат берётся не выше исходного: снятие моста только опускает
поверхность. Ячейка, которая и так ниже интерполяции (вода, видная между
пролётами), остаётся как была.
"""
import math

import numpy as np


def dilate(mask, cells):
    """Расширить маску на cells ячеек по восьми направлениям."""
    m = np.asarray(mask, dtype=bool).copy()
    for _ in range(int(max(0, cells))):
        g = m.copy()
        g[1:, :] |= m[:-1, :]
        g[:-1, :] |= m[1:, :]
        g[:, 1:] |= m[:, :-1]
        g[:, :-1] |= m[:, 1:]
        g[1:, 1:] |= m[:-1, :-1]
        g[1:, :-1] |= m[:-1, 1:]
        g[:-1, 1:] |= m[1:, :-1]
        g[:-1, :-1] |= m[1:, 1:]
        m = g
    return m


def components(mask):
    """Связные пятна маски (восьмисвязность): список массивов (rows, cols).

    Обход в ширину на питоне: пятна мостов малы, а зависимость от scipy
    в поставке QGIS не гарантирована."""
    m = np.asarray(mask, dtype=bool)
    ny, nx = m.shape
    seen = np.zeros_like(m)
    out = []
    starts = np.argwhere(m)
    for r0, c0 in starts:
        if seen[r0, c0]:
            continue
        seen[r0, c0] = True
        stack = [(int(r0), int(c0))]
        rows, cols = [], []
        while stack:
            r, c = stack.pop()
            rows.append(r)
            cols.append(c)
            for dr in (-1, 0, 1):
                rr = r + dr
                if rr < 0 or rr >= ny:
                    continue
                for dc in (-1, 0, 1):
                    cc = c + dc
                    if (dr or dc) and 0 <= cc < nx and m[rr, cc] \
                            and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
        out.append((np.array(rows), np.array(cols)))
    return out


DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))
SPAN_TOL = 2.0
LOW_QUANTILE = 25.0


def directional_fill(z, unknown, valid, span_tol=None, low=None):
    """Пересчитать неизвестные ячейки по пролётам в четырёх направлениях.

    Из каждой неизвестной ячейки делается шаг в обе стороны по строке,
    столбцу и двум диагоналям до первой известной ячейки. По каждому
    направлению, где известны оба конца, значение берётся линейно между
    концами. Берётся самое низкое значение из кратчайших пролётов и из
    пролётов не длиннее span_tol кратчайшего, у которых оба конца не выше
    low (низ окружения, то есть вода).

    Самое низкое, а не среднее, потому что задача - снять преграду. Ячейка
    под настилом видит воду выше и ниже по течению и берега по бокам.
    Пролёт с воды на воду даёт уровень воды, пролёт, задевший берег, даёт
    выше. Река редко идёт точно по строке или столбцу, и если брать
    кратчайший пролёт, он нередко упирается в борт долины, и над руслом
    остаётся порог. Удлинённый пролёт принимается, только если оба его
    конца стоят на низу окружения: так он находит русло, но не срезает
    борт долины, где один конец пролёта всегда высоко.

    Плоскость воспроизводится любым направлением точно, поэтому на ровном
    склоне берега выбор направления ничего не меняет. Каждое значение лежит
    между значениями окружения.

    Возвращает (z, сколько ячеек осталось без пролёта)."""
    if span_tol is None:
        span_tol = SPAN_TOL
    z = np.array(z, dtype=float)
    unknown = np.asarray(unknown, dtype=bool) & np.asarray(valid, dtype=bool)
    valid = np.asarray(valid, dtype=bool)
    rows, cols = np.nonzero(unknown)
    if not len(rows):
        return z, 0
    ny, nx = z.shape
    ests = []
    spans = []
    lows = []
    for dr, dc in DIRECTIONS:
        step = math.hypot(dr, dc)
        ends = []
        for sgn in (1, -1):
            r = rows.copy()
            c = cols.copy()
            dist = np.zeros(len(rows), dtype=int)
            alive = np.ones(len(rows), dtype=bool)
            val = np.full(len(rows), np.nan)
            while alive.any():
                r[alive] += sgn * dr
                c[alive] += sgn * dc
                dist[alive] += 1
                out = alive & ((r < 0) | (r >= ny) | (c < 0) | (c >= nx))
                alive &= ~out
                rr = np.clip(r, 0, ny - 1)
                cc = np.clip(c, 0, nx - 1)
                stop = alive & ~unknown[rr, cc]
                hit = stop & valid[rr, cc]
                val[hit] = z[rr[hit], cc[hit]]
                alive &= ~stop
            ends.append((val, dist))
        (v1, d1), (v2, d2) = ends
        ok = np.isfinite(v1) & np.isfinite(v2)
        span = np.where(ok, (d1 + d2) * step, np.inf)
        est = np.where(ok, v1 + (v2 - v1) * d1 / np.maximum(d1 + d2, 1),
                       np.inf)
        ests.append(est)
        spans.append(span)
        if low is None:
            lows.append(ok)
        else:
            lows.append(ok & (v1 <= low) & (v2 <= low))
    ests = np.array(ests)
    spans = np.array(spans)
    shortest = spans.min(axis=0)
    have = np.isfinite(shortest)
    tight = spans <= shortest[None, :] * (1.0 + 1e-9)
    near = spans <= shortest[None, :] * float(span_tol) + 1e-9
    take = tight | (near & np.array(lows))
    best = np.where(take, ests, np.inf).min(axis=0)
    z[rows[have], cols[have]] = best[have]
    return z, int((~have).sum())


def remove_bridges(z, nodata_mask, bridge_mask):
    """Снять мосты: пересчитать ячейки маски по их окружению.

    z - грид; nodata_mask - True там, где данных нет; bridge_mask - True
    под мостами (уже с запасом). Каждое связное пятно считается в своём
    окне. Возвращает (новый грид, отчёты по пятнам). Отчёт - словарь:
    row, col (центр пятна), cells, status ('ok', 'no_edge'), edge_min,
    edge_max (отметки окружения), lowered_max, lowered_mean, z_min, z_max
    (после снятия), orphans (ячейки без пролёта, оставлены как были)."""
    z = np.asarray(z, dtype=float)
    nod = (np.asarray(nodata_mask, dtype=bool) if nodata_mask is not None
           else ~np.isfinite(z)) | ~np.isfinite(z)
    valid = ~nod
    bm = np.asarray(bridge_mask, dtype=bool) & valid
    out = z.copy()
    ny, nx = z.shape
    reports = []
    for rows, cols in components(bm):
        r0 = max(int(rows.min()) - 1, 0)
        r1 = min(int(rows.max()) + 2, ny)
        c0 = max(int(cols.min()) - 1, 0)
        c1 = min(int(cols.max()) + 2, nx)
        win = z[r0:r1, c0:c1]
        vwin = valid[r0:r1, c0:c1]
        unk = np.zeros(win.shape, dtype=bool)
        unk[rows - r0, cols - c0] = True
        ring = dilate(unk, 1) & ~unk & vwin
        rep = {"row": float(rows.mean()), "col": float(cols.mean()),
               "cells": int(len(rows)), "orphans": 0}
        if not ring.any():
            rep.update(status="no_edge", edge_min=math.nan,
                       edge_max=math.nan, lowered_max=0.0,
                       lowered_mean=0.0, z_min=math.nan, z_max=math.nan)
            reports.append(rep)
            continue
        # низ окружения: вода выше и ниже по течению лежит в нижней
        # четверти отметок кольца, берега и дорога выше
        low = float(np.percentile(win[ring], LOW_QUANTILE))
        filled, orphans = directional_fill(win, unk, vwin, low=low)
        old = win[unk]
        new = np.minimum(old, filled[unk])
        block = out[r0:r1, c0:c1]
        block[unk] = new
        drop = old - new
        rep.update(status="ok", orphans=orphans,
                   edge_min=float(win[ring].min()),
                   edge_max=float(win[ring].max()),
                   lowered_max=float(drop.max()),
                   lowered_mean=float(drop.mean()),
                   z_min=float(new.min()), z_max=float(new.max()))
        reports.append(rep)
    return out, reports
