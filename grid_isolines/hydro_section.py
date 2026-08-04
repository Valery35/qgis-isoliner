# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Створы и кривые расходов: ядро.

Гидравлическая характеристика створа строится по профилю долины: для
ряда отметок воды считается живое сечение и расход по Маннингу, выходом
служит кривая Q(H). Связь работает в обе стороны: по расходу отметка
затопления, по отметке расход.

Профиль членится на левый берег, русло и правый берег, и раздельный
счёт обязателен: шероховатость поймы и русла различается в разы, и
единый счёт занижает русловую составляющую. Гидравлический радиус
каждого членения считается по смоченному периметру его твёрдых границ,
межевые вертикали в периметр не входят - простейшее из принятых
соглашений, см. постановку.

Все функции чистые, NumPy без QGIS: ядро проверяется headless-тестами
по демо-реке с известной эталонной кривой.
"""

import math

import numpy as np


class Fragment(object):
    """Фрагмент профиля: имя, расстояния и отметки по вершинам.

    Расстояния идут вдоль створа и внутри фрагмента строго возрастают.
    Шероховатость n своя у каждого фрагмента, уклон один на створ и
    хранится у створа.
    """

    __slots__ = ("name", "d", "z", "n")

    def __init__(self, name, d, z, n):
        self.name = name
        self.d = np.asarray(d, float)
        self.z = np.asarray(z, float)
        self.n = float(n)
        if self.d.size != self.z.size:
            raise ValueError("расстояний и отметок разное число")
        if self.d.size >= 2 and not np.all(np.diff(self.d) >= 0):
            raise ValueError("расстояния вдоль створа обязаны не убывать")


def wetted_geometry(d, z, level):
    """Площадь, ширина по зеркалу и смоченный периметр на отметке воды.

    Профиль - ломаная (d, z). Вода стоит на отметке level, сечение
    считается точным интегрированием по отрезкам ломаной: на каждом
    отрезке берётся его подводная часть, площадь трапецией, периметр
    длиной подводного куска. Урез внутри отрезка находится линейной
    интерполяцией, поэтому кривая по отметкам гладкая, а не ступенчатая
    - никакой дискретизации по вертикали здесь нет.

    Возвращает (area, width, perimeter). Сухой профиль даёт нули.
    """
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    area = width = per = 0.0
    for i in range(d.size - 1):
        d0, d1 = d[i], d[i + 1]
        z0, z1 = z[i], z[i + 1]
        h0 = level - z0
        h1 = level - z1
        if h0 <= 0.0 and h1 <= 0.0:
            continue
        if h0 > 0.0 and h1 > 0.0:
            dd = d1 - d0
            area += 0.5 * (h0 + h1) * dd
            width += dd
            per += math.hypot(dd, z1 - z0)
            continue
        # урез внутри отрезка
        t = h0 / (h0 - h1)            # доля отрезка до уреза от точки 0
        dc = d0 + t * (d1 - d0)
        if h0 > 0.0:
            dd = dc - d0
            area += 0.5 * h0 * dd
            width += dd
            per += math.hypot(dd, t * (z1 - z0))
        else:
            dd = d1 - dc
            area += 0.5 * h1 * dd
            width += dd
            per += math.hypot(dd, (1.0 - t) * (z1 - z0))
    return area, width, per


def manning_q(area, perimeter, n, slope):
    """Расход по Маннингу: Q = A/n · R^(2/3) · i^(1/2).

    Формула описывает установившееся равномерное движение, и кривая по
    ней - гидравлическая характеристика створа, а не расчёт
    неустановившегося потока.
    """
    if area <= 0.0 or perimeter <= 0.0 or n <= 0.0 or slope <= 0.0:
        return 0.0
    r = area / perimeter
    return area / n * r ** (2.0 / 3.0) * math.sqrt(slope)


def rating_curve(fragments, slope, levels=None, step=0.1, top=None):
    """Кривая расходов створа по членениям и суммарно.

    fragments - список Fragment в порядке слева направо.
    slope - уклон водной поверхности, один на створ.
    levels - отметки воды; если не заданы, строятся от низшей точки
    профиля до top (по умолчанию до высшей точки) с шагом step.

    Возвращает dict массивов одинаковой длины: level, затем по каждому
    фрагменту area/width/perimeter/radius/q с суффиксом имени, и
    суммарные area_total, q_total. Скорость не хранится: она выходит из
    q и area делением и считается потребителем.
    """
    zmin = min(float(np.min(f.z)) for f in fragments)
    zmax = max(float(np.max(f.z)) for f in fragments)
    if levels is None:
        hi = zmax if top is None else float(top)
        nlev = max(2, int(math.ceil((hi - zmin) / float(step))) + 1)
        levels = zmin + np.arange(nlev) * float(step)
        levels = levels[levels <= hi + 1e-9]
    else:
        levels = np.asarray(levels, float)

    out = {"level": levels.copy()}
    a_tot = np.zeros_like(levels)
    q_tot = np.zeros_like(levels)
    for f in fragments:
        a = np.empty_like(levels)
        b = np.empty_like(levels)
        p = np.empty_like(levels)
        for k, lv in enumerate(levels):
            a[k], b[k], p[k] = wetted_geometry(f.d, f.z, float(lv))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(p > 0.0, a / p, 0.0)
        q = np.array([manning_q(a[k], p[k], f.n, slope)
                      for k in range(levels.size)])
        out["area_" + f.name] = a
        out["width_" + f.name] = b
        out["perimeter_" + f.name] = p
        out["radius_" + f.name] = r
        out["q_" + f.name] = q
        a_tot += a
        q_tot += q
    out["area_total"] = a_tot
    out["q_total"] = q_tot
    return out


def level_for_q(curve, q):
    """Отметка воды для заданного расхода: обратный ход по кривой.

    Кривая Q(H) монотонна по построению, отметка находится линейной
    интерполяцией между соседними точками. Расход ниже первой точки
    даёт первую отметку, выше последней - None с точки зрения кривой:
    экстраполировать расход за верх профиля нельзя, вода уже вышла за
    посчитанный диапазон.
    """
    lv = curve["level"]
    qq = curve["q_total"]
    if q <= qq[0]:
        return float(lv[0])
    if q > qq[-1]:
        return None
    k = int(np.searchsorted(qq, q))
    q0, q1 = qq[k - 1], qq[k]
    t = 0.0 if q1 <= q0 else (q - q0) / (q1 - q0)
    return float(lv[k - 1] + t * (lv[k] - lv[k - 1]))


def q_for_level(curve, level):
    """Суммарный расход для заданной отметки: прямой ход по кривой."""
    lv = curve["level"]
    qq = curve["q_total"]
    if level <= lv[0]:
        return 0.0
    if level >= lv[-1]:
        return float(qq[-1])
    k = int(np.searchsorted(lv, level))
    t = (level - lv[k - 1]) / (lv[k] - lv[k - 1])
    return float(qq[k - 1] + t * (qq[k] - qq[k - 1]))


def split_by_divides(d, z, div_left, div_right, n_left, n_channel,
                     n_right):
    """Одна линия с границами членения -> три фрагмента.

    div_left и div_right - расстояния по профилю. Точки границ
    вставляются в оба соседних фрагмента с интерполированной отметкой,
    чтобы профиль не рвался и площади фрагментов складывались в
    площадь целого без зазора.
    """
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    if not (d[0] <= div_left < div_right <= d[-1]):
        raise ValueError("границы членения вне профиля или переставлены")

    def cut(d0, d1, side):
        """Вырезка [d0, d1] с правилом для кратных узлов на границе.

        Вертикальная стенка на границе членения даёт два узла на одном
        расстоянии, и принадлежит она руслу: его смоченный периметр
        включает стенки, пойменный нет. Поэтому средний фрагмент берёт
        кратные узлы целиком, левый на своей правой границе оставляет
        только первый узел, правый на левой - только последний.
        Индексы, а не сортировка: порядок узлов профиля сохраняется,
        argsort на равных расстояниях мог бы переставить стенку.
        """
        idx = [i for i in range(d.size)
               if d0 - 1e-9 <= d[i] <= d1 + 1e-9]
        if side == "left":
            while len(idx) >= 2 and abs(d[idx[-1]] - d1) < 1e-9 \
                    and abs(d[idx[-2]] - d1) < 1e-9:
                idx.pop()
        elif side == "right":
            while len(idx) >= 2 and abs(d[idx[0]] - d0) < 1e-9 \
                    and abs(d[idx[1]] - d0) < 1e-9:
                idx.pop(0)
        dd = [float(d[i]) for i in idx]
        zz = [float(z[i]) for i in idx]
        if not dd or abs(dd[0] - d0) > 1e-9:
            dd.insert(0, d0)
            zz.insert(0, float(np.interp(d0, d, z)))
        if abs(dd[-1] - d1) > 1e-9:
            dd.append(d1)
            zz.append(float(np.interp(d1, d, z)))
        return np.asarray(dd), np.asarray(zz)

    dl, zl = cut(d[0], div_left, "left")
    dc, zc = cut(div_left, div_right, "mid")
    dr, zr = cut(div_right, d[-1], "right")
    return [Fragment("left", dl, zl, n_left),
            Fragment("channel", dc, zc, n_channel),
            Fragment("right", dr, zr, n_right)]


def chain_slopes(km, z_ref):
    """Уклоны по цепочке створов: к соседнему по километражу.

    km - километраж створов, z_ref - отметка уреза или дна каждого.
    Уклон i-го створа берётся к следующему вниз по течению, последний -
    к предыдущему. Отрицательный уклон (отметка растёт вниз по течению)
    оставляется как есть: это признак ошибки данных, и прятать его
    нельзя - потребитель обязан увидеть и разобраться.
    """
    km = np.asarray(km, float)
    z = np.asarray(z_ref, float)
    if km.size < 2:
        raise ValueError("для уклона по цепочке нужно не меньше двух створов")
    order = np.argsort(km)
    s = np.empty_like(z)
    ks, zs = km[order], z[order]
    for j in range(ks.size):
        j2 = j + 1 if j + 1 < ks.size else j - 1
        dz = zs[j] - zs[j2]
        dx = abs(ks[j2] - ks[j]) * 1000.0     # километраж в метры
        s[order[j]] = dz / dx if j2 > j else -dz / dx
    return s


def gauge_flat_at_level(d, z, level, tol=0.02, min_width=3):
    """Признак водной глади: плоский участок профиля на отметке.

    Касается профилей, срезанных с ЦМР: зашитая гладь даёт подряд идущие
    вершины с почти одинаковой отметкой. Возвращает суммарную ширину
    таких участков на отметке level; ноль означает, что признака нет.
    """
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    near = np.abs(z - level) <= tol
    width = 0.0
    run = 0
    start = 0.0
    for i in range(d.size):
        if near[i]:
            if run == 0:
                start = d[i]
            run += 1
        else:
            if run >= min_width:
                width += d[i - 1] - start
            run = 0
    if run >= min_width:
        width += d[-1] - start
    return width
