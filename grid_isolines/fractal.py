# -*- coding: utf-8 -*-
"""Фрактальная размерность поверхности вариограммным методом.

Для самоаффинной поверхности полувариограмма растёт степенным законом
gamma(h) ~ h^(2H), где H - показатель Хёрста. Наклон лог-лог вариограммы
на малых лагах даёт H, а фрактальная размерность D = 3 - H лежит в
диапазоне [2, 3]: гладкая дифференцируемая поверхность - около 2, белый
шум - около 3. Расчёт ведётся в скользящем окне через кумулятивные
суммы, чистый NumPy."""

import numpy as np


def _boxsum(a, r):
    """Сумма в квадратном окне (2r+1)x(2r+1) через интегральное изображение."""
    ny, nx = a.shape
    p = np.zeros((ny + 1, nx + 1), dtype=np.float64)
    p[1:, 1:] = np.cumsum(np.cumsum(a, axis=0), axis=1)
    i0 = np.clip(np.arange(ny) - r, 0, ny)
    i1 = np.clip(np.arange(ny) + r + 1, 0, ny)
    j0 = np.clip(np.arange(nx) - r, 0, nx)
    j1 = np.clip(np.arange(nx) + r + 1, 0, nx)
    return (p[i1][:, j1] - p[i0][:, j1] - p[i1][:, j0] + p[i0][:, j0])


def _lag_stats(z, valid, h):
    """Суммы квадратов разностей и счётчики пар для лага h по X и Y,
    развёрнутые обратно к форме грида (пара пишется в обе ячейки)."""
    ny, nx = z.shape
    s = np.zeros((ny, nx), dtype=np.float64)
    n = np.zeros((ny, nx), dtype=np.float64)
    zz = np.where(valid, z, 0.0)
    # X-направление
    m = valid[:, h:] & valid[:, :-h]
    d2 = np.where(m, (zz[:, h:] - zz[:, :-h]) ** 2, 0.0)
    s[:, h:] += d2; s[:, :-h] += d2
    n[:, h:] += m;  n[:, :-h] += m
    # Y-направление
    m = valid[h:, :] & valid[:-h, :]
    d2 = np.where(m, (zz[h:, :] - zz[:-h, :]) ** 2, 0.0)
    s[h:, :] += d2; s[:-h, :] += d2
    n[h:, :] += m;  n[:-h, :] += m
    return s, n


def fractal_dimension_map(z, window=8, max_lag=4, min_pairs=32):
    """Карта фрактальной размерности D в скользящем окне.

    z - 2D массив (NaN = нет данных); window - полурадиус окна в ячейках;
    max_lag - число лагов 1..max_lag; min_pairs - минимум пар на лаг.
    Возвращает (D, H): массивы с NaN там, где расчёт невозможен."""
    z = np.asarray(z, dtype=np.float64)
    valid = np.isfinite(z)
    lags = np.arange(1, int(max_lag) + 1)
    logs_h = np.log(lags.astype(np.float64))
    lg = []
    ok = np.ones(z.shape, dtype=bool)
    for h in lags:
        s, n = _lag_stats(z, valid, int(h))
        S = _boxsum(s, window)
        N = _boxsum(n, window)
        with np.errstate(divide="ignore", invalid="ignore"):
            g = 0.5 * S / N
        good = (N >= min_pairs) & (g > 0)
        ok &= good
        lg.append(np.where(good, np.log(g), 0.0))
    lg = np.stack(lg)                       # (nlag, ny, nx)
    hx = logs_h - logs_h.mean()
    denom = float((hx ** 2).sum())
    slope = np.tensordot(hx, lg - lg.mean(axis=0), axes=(0, 0)) / denom
    H = 0.5 * slope
    D = 3.0 - H
    D = np.where(ok & valid, np.clip(D, 2.0, 3.0), np.nan)
    H = np.where(np.isfinite(D), np.clip(H, 0.0, 1.0), np.nan)
    return D, H


def fractal_dimension_global(z, max_lag=4):
    """Глобальные D и H по всему гриду тем же методом."""
    z = np.asarray(z, dtype=np.float64)
    valid = np.isfinite(z)
    lags = np.arange(1, int(max_lag) + 1)
    gs = []
    for h in lags:
        s, n = _lag_stats(z, valid, int(h))
        tot_n = float(n.sum())
        if tot_n <= 0:
            return float("nan"), float("nan")
        g = 0.5 * float(s.sum()) / tot_n
        if g <= 0:
            return float("nan"), float("nan")
        gs.append(np.log(g))
    x = np.log(lags.astype(np.float64))
    x = x - x.mean()
    slope = float((x * (np.array(gs) - np.mean(gs))).sum() / (x ** 2).sum())
    H = min(max(0.5 * slope, 0.0), 1.0)
    return 3.0 - H, H


def box_count_dimension(mask, sizes=None):
    """Размерность бинарной маски методом box-counting.

    mask - 2D булев массив; sizes - размеры ячеек покрытия (по умолчанию
    степени двойки от min(shape)//2 вниз до 2). Возвращает (D, sizes,
    counts). Плоское пятно даёт D около 2, линия - около 1."""
    m = np.asarray(mask, dtype=bool)
    if sizes is None:
        # верх диапазона ограничен: крупные ячейки насыщаются на компактных
        # пятнах и заваливают наклон
        n = int(np.log2(max(min(m.shape) // 8, 2)))
        sizes = [2 ** k for k in range(n, 0, -1)]
    sizes = [int(s) for s in sizes if 1 < s <= min(m.shape)]
    counts = []
    for s in sizes:
        ny = (m.shape[0] // s) * s
        nx = (m.shape[1] // s) * s
        c = m[:ny, :nx].reshape(ny // s, s, nx // s, s)
        counts.append(int(np.any(c, axis=(1, 3)).sum()))
    ok = [(s, c) for s, c in zip(sizes, counts) if c > 4]
    if len(ok) < 2:
        return float("nan"), sizes, counts
    x = np.log(1.0 / np.array([s for s, _c in ok], dtype=float))
    yv = np.log(np.array([c for _s, c in ok], dtype=float))
    x = x - x.mean()
    D = float((x * (yv - yv.mean())).sum() / (x ** 2).sum())
    return D, sizes, counts


def _polyline_length(pts):
    d = np.diff(np.asarray(pts, dtype=float), axis=0)
    return float(np.sqrt((d ** 2).sum(axis=1)).sum())


def _divider_steps(pts, r):
    """Число шагов циркуля раствора r вдоль ломаной (метод Ричардсона)."""
    P = np.asarray(pts, dtype=float)
    if len(P) < 2 or r <= 0:
        return 0
    steps = 0
    cur = P[0].copy()
    i = 0
    while True:
        # ищем первую точку ломаной на евклидовом расстоянии r от cur
        j = i
        found = False
        while j < len(P) - 1:
            a, b = (P[j] if j > i else cur), P[j + 1]
            da = a - cur
            db = b - a
            # |cur + t*db + da - cur| = r на отрезке a-b
            A = float((db ** 2).sum())
            B = 2.0 * float((da * db).sum())
            C = float((da ** 2).sum()) - r * r
            if A > 0:
                disc = B * B - 4 * A * C
                if disc >= 0:
                    tt = (-B + np.sqrt(disc)) / (2 * A)
                    if 0.0 <= tt <= 1.0:
                        cur = a + tt * db
                        steps += 1
                        i = j
                        found = True
                        break
            j += 1
        if not found:
            break
    return steps


def divider_dimension(pts, n_rulers=6):
    """Размерность линии методом циркуля: log N(r) ~ -D log r.

    Возвращает (D, rulers, steps); для прямой D = 1."""
    L = _polyline_length(pts)
    if L <= 0:
        return float("nan"), [], []
    rulers = np.exp(np.linspace(np.log(L / 200.0), np.log(L / 8.0),
                                int(n_rulers)))
    steps = [_divider_steps(pts, float(r)) for r in rulers]
    ok = [(r, s) for r, s in zip(rulers, steps) if s >= 2]
    if len(ok) < 3:
        return float("nan"), list(rulers), steps
    x = np.log(np.array([r for r, _s in ok]))
    yv = np.log(np.array([s for _r, s in ok], dtype=float))
    x = x - x.mean()
    D = float(-(x * (yv - yv.mean())).sum() / (x ** 2).sum())
    return D, list(rulers), steps
