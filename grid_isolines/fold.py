# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Складчатость поверхности: локальная дисперсия со снятым трендом.

Складчатость есть извилистость поверхности, и мерить её надо по
поверхности, а не по мощности: цилиндрическая складка с постоянной
истинной мощностью существует и по мощности невидима вовсе.

Прямая дисперсия отметок для этого не годится: на общем наклоне она
велика всюду, и спокойный склон получает ту же оценку, что смятая зона.
Поэтому в каждом окне подгоняется плоскость и считается разброс остатков
вокруг неё. Наклон уходит, извилистость остаётся.

Тренд именно линейный. Квадратичный вберёт в себя часть самой
складчатости и погасит то, что мы ищем.

Одно число здесь ничего не значит: дисперсия зависит от размера окна.
Правильная мера это зависимость дисперсии от масштаба, а её наклон и есть
показатель Гёльдера, через который выражается фрактальная размерность.
Аппарат для неё в плагине уже стоит, и новое тут не расчёт, а вход:
подать детрендированную поверхность вместо готового рельефа.
"""

import numpy as np


def boxsum(a, win):
    """Сумма значений в квадратном окне win на ячейку, края отражением.

    Считается через кумулятивные суммы: время не зависит от размера окна,
    и это существенно, потому что мера складчатости требует не одного
    окна, а нескольких.
    """
    a = np.asarray(a, dtype=float)
    r = int(win) // 2
    pad = np.pad(a, r, mode="reflect")
    c = np.cumsum(np.cumsum(pad, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)), mode="constant")
    w = 2 * r + 1
    ny, nx = a.shape
    i0 = np.arange(ny)[:, None]
    j0 = np.arange(nx)[None, :]
    return (c[i0 + w, j0 + w] - c[i0, j0 + w]
            - c[i0 + w, j0] + c[i0, j0])


def detrended_variance(z, win):
    """Дисперсия отметок в окне вокруг подогнанной плоскости.

    В окне подгоняется z = a·x + b·y + c, где x и y отсчитываются от его
    центра. Тогда суммы нечётных степеней обращаются в ноль, коэффициенты
    выражаются в лоб, а остаточная дисперсия считается без единого цикла.
    """
    z = np.asarray(z, dtype=float)
    w = int(win) | 1                      # окно нечётное, есть центр
    if w < 3:
        raise ValueError("окно меньше трёх ячеек")
    ny, nx = z.shape
    good = np.isfinite(z)
    zz = np.where(good, z, 0.0)

    n = boxsum(good.astype(float), w)
    sz = boxsum(zz, w)
    szz = boxsum(zz * zz, w)

    jj = np.broadcast_to(np.arange(nx, dtype=float), (ny, nx))
    ii = np.broadcast_to(np.arange(ny, dtype=float)[:, None], (ny, nx))
    g = good.astype(float)
    sjz = boxsum(jj * zz, w)
    siz = boxsum(ii * zz, w)
    sj = boxsum(jj * g, w)
    si = boxsum(ii * g, w)
    sjj = boxsum(jj * jj * g, w)
    sii = boxsum(ii * ii * g, w)
    sji = boxsum(jj * ii * g, w)

    with np.errstate(invalid="ignore", divide="ignore"):
        mean = sz / n
        jbar = sj / n
        ibar = si / n
        # Суммы произведений отклонений считаются по фактическому составу
        # окна, а не по идеальному: у края часть ячеек приходит отражением,
        # и готовая формула для полного окна там неверна.
        sxz = sjz - jbar * sz
        syz = siz - ibar * sz
        sxx = sjj - n * jbar * jbar
        syy = sii - n * ibar * ibar
        sxy = sji - n * jbar * ibar
        det = sxx * syy - sxy * sxy
        a = np.where(np.abs(det) > 1e-9,
                     (sxz * syy - syz * sxy) / det, 0.0)
        b = np.where(np.abs(det) > 1e-9,
                     (syz * sxx - sxz * sxy) / det, 0.0)
        rss = szz - n * mean * mean - a * sxz - b * syz
        var = np.maximum(rss / n, 0.0)
    var[~good] = np.nan
    var[n < 4] = np.nan
    return var


def detrend(z, win):
    """Остаток поверхности после снятия плоскости в скользящем окне.

    Это и есть вход для вариограммного анализа: на детрендированной
    поверхности наклон вариограммы описывает извилистость, а не общее
    падение слоя.
    """
    z = np.asarray(z, dtype=float)
    w = int(win) | 1
    good = np.isfinite(z)
    zz = np.where(good, z, 0.0)
    n = boxsum(good.astype(float), w)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = boxsum(zz, w) / n
    out = np.where(good, z - mean, np.nan)
    out[n < 1] = np.nan
    return out


def scale_slope(z, wins):
    """Наклон зависимости дисперсии от размера окна, по ячейкам.

    Одно окно не отвечает на вопрос о складчатости: спокойная поверхность
    и смятая различаются не величиной разброса, а тем, как быстро он
    растёт с масштабом. Наклон в двойном логарифме и есть эта скорость.
    """
    wins = [int(w) | 1 for w in wins]
    if len(wins) < 2:
        raise ValueError("нужно хотя бы два окна")
    xs = np.log(np.asarray(wins, dtype=float))
    vars_ = [detrended_variance(z, w) for w in wins]
    ys = np.log(np.maximum(np.stack(vars_, axis=0), 1e-12))
    xm = xs.mean()
    ym = ys.mean(axis=0)
    num = np.tensordot(xs - xm, ys - ym, axes=(0, 0))
    den = float(np.sum((xs - xm) ** 2))
    slope = num / den
    slope[~np.isfinite(vars_[0])] = np.nan
    return slope
