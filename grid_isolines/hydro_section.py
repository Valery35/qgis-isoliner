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
каждого участков считается по смоченному периметру его твёрдых границ,
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
    """Кривая расходов створа по участковм и суммарно.

    fragments - список Fragment в порядке слева направо.
    slope - уклон водной поверхности, один на створ.
    levels - отметки воды; если не заданы, строятся от низшей точки
    профиля до top (по умолчанию до высшей точки) с шагом step.

    Возвращает dict массивов одинаковой длины: level, затем по каждому
    фрагменту area/width/perimeter/radius/q/v с суффиксом имени, и
    суммарные area_total, q_total, v_total.

    Скорость это средняя по живому сечению, Q делить на A. У участка она
    своя, и разница между руслом и поймой на графике видна сразу: пойма
    добавляет площадь, но почти не добавляет расхода, и общая скорость
    на переломе падает. Там, где площади нет, скорость ноль, а не
    бесконечность.
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
        out["v_" + f.name] = np.where(a > 1e-12, q / np.where(a > 1e-12,
                                                             a, 1.0), 0.0)
        a_tot += a
        q_tot += q
    out["area_total"] = a_tot
    out["q_total"] = q_tot
    out["v_total"] = np.where(a_tot > 1e-12,
                              q_tot / np.where(a_tot > 1e-12, a_tot, 1.0), 0.0)
    return out


def curve_plot(curve, xkey, ykey="level", width=1.0, height=1.0,
               x_from_zero=True):
    """Кривая в координатах чертежа: точки линии и параметры осей.

    График строится в СВОИХ осях: по горизонтали расход или площадь, по
    вертикали отметка. Смешивать их с координатами профиля нельзя -
    метры расстояния и кубометры в секунду несопоставимы, и общий
    масштаб для них не существует. Поэтому график живёт отдельным
    слоем, а на лист оба кладутся макетом.

    Возвращает словарь: pts - точки линии, xmin/xmax/ymin/ymax -
    диапазоны исходных величин, sx/sy - множители перевода в чертёж.
    Обратный перевод нужен подписям осей, поэтому множители отдаются
    наружу, а не прячутся внутри.
    """
    xs = np.asarray(curve[xkey], dtype=np.float64)
    ys = np.asarray(curve[ykey], dtype=np.float64)
    good = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[good], ys[good]
    if xs.size < 2:
        return None
    xmin = 0.0 if x_from_zero else float(xs.min())
    xmax = float(xs.max())
    ymin, ymax = float(ys.min()), float(ys.max())
    dx = xmax - xmin
    dy = ymax - ymin
    sx = float(width) / dx if dx > 0 else 1.0
    sy = float(height) / dy if dy > 0 else 1.0
    pts = [((float(x) - xmin) * sx, (float(y) - ymin) * sy)
           for x, y in zip(xs, ys)]
    return {"pts": pts, "xmin": xmin, "xmax": xmax, "ymin": ymin,
            "ymax": ymax, "sx": sx, "sy": sy, "width": float(width),
            "height": float(height)}


def plot_ticks(lo, hi, scale, step=0.0, count=5):
    """Засечки оси: список (значение, координата чертежа).

    Шаг задан - идут ровно по нему, от округлённого вниз начала. Это то,
    чего просит нормативный чертёж: шкала высот через метр, шкала
    расходов округлыми значениями. Шаг ноль - выбираются красивые числа
    по их количеству.
    """
    if not (hi > lo):
        return []
    if step and step > 0:
        vals = []
        v = math.ceil(lo / step) * step
        for _ in range(10000):
            if v > hi + 1e-9:
                break
            vals.append(round(v, 6))
            v += step
    else:
        vals = _nice_values(lo, hi, count)
    return [(v, (v - lo) * scale) for v in vals]


def _nice_values(lo, hi, n):
    """Округлённые значения между lo и hi: ряд 1, 2, 2.5, 5, 10."""
    if not (hi > lo) or n < 2:
        return []
    raw = (hi - lo) / (n - 1)
    mag = 10.0 ** math.floor(math.log10(raw)) if raw > 0 else 1.0
    best, bestd = mag, None
    for f in (1, 2, 2.5, 5, 10):
        s = f * mag
        cnt = int(math.floor((hi - math.ceil(lo / s) * s) / s + 1e-9)) + 1
        d = abs(cnt - n)
        if bestd is None or d < bestd:
            best, bestd = s, d
    out = []
    v = math.ceil(lo / best) * best
    while v <= hi + 1e-9:
        out.append(round(v, 6))
        v += best
    return out


def curve_marks(plot, levels, curve, xkey):
    """Засечки уровней обеспеченности: пунктир от кривой к обеим осям.

    Для каждой заданной отметки берётся её место на кривой и строятся
    два отрезка - к оси отметок и к оси расходов. Именно так подписаны
    уровни на нормативных графиках: видно и отметку, и отвечающий ей
    расход.

    Отметка вне диапазона кривой пропускается: рисовать засечку в
    пустоте незачем.
    """
    if not plot:
        return []
    xs = np.asarray(curve[xkey], dtype=np.float64)
    ys = np.asarray(curve["level"], dtype=np.float64)
    good = np.isfinite(xs) & np.isfinite(ys)
    xs, ys = xs[good], ys[good]
    out = []
    for name, lev in levels:
        lev = float(lev)
        if not (ys.min() - 1e-9 <= lev <= ys.max() + 1e-9):
            continue
        xv = float(np.interp(lev, ys, xs))
        px = (xv - plot["xmin"]) * plot["sx"]
        py = (lev - plot["ymin"]) * plot["sy"]
        out.append({"name": name, "level": lev, "value": xv,
                    "to_y": [(0.0, py), (px, py)],
                    "to_x": [(px, 0.0), (px, py)]})
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
    """Одна линия с границами участков -> три фрагмента.

    div_left и div_right - расстояния по профилю. Точки границ
    вставляются в оба соседних фрагмента с интерполированной отметкой,
    чтобы профиль не рвался и площади фрагментов складывались в
    площадь целого без зазора.
    """
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    if not (d[0] <= div_left < div_right <= d[-1]):
        raise ValueError("границы участков вне профиля или переставлены")

    def cut(d0, d1, side):
        """Вырезка [d0, d1] с правилом для кратных узлов на границе.

        Вертикальная стенка на границе участков даёт два узла на одном
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


def wet_spans(d, z, level, min_width=1e-9):
    """Смоченные участки профиля на отметке: список пар (начало, конец).

    Уровень воды на чертеже не тянется через всю ширину створа: он
    существует только там, где вода есть, то есть между точками уреза. На
    борта и на сухие острова линия заходить не должна.

    Участков может быть несколько: отмель посреди русла или замкнутое
    понижение на пойме разрывают зеркало на части, и каждая часть живёт
    своим отрезком.

    Вертикальные звенья профиля обрабатываются наравне с наклонными:
    у стенки русла переход через урез происходит в той же координате, и
    пропустить такое звено значит потерять весь участок.
    """
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    if d.size < 2:
        return []
    h = float(level) - z
    spans = []
    start = None if h[0] <= 0.0 else float(d[0])
    for i in range(d.size - 1):
        x0, x1 = float(d[i]), float(d[i + 1])
        h0, h1 = float(h[i]), float(h[i + 1])
        if h0 <= 0.0 < h1:                      # вход под воду
            t = 0.0 if x1 == x0 else h0 / (h0 - h1)
            start = x0 + t * (x1 - x0)
        elif h0 > 0.0 >= h1:                    # выход из-под воды
            t = 0.0 if x1 == x0 else h0 / (h0 - h1)
            end = x0 + t * (x1 - x0)
            spans.append((start if start is not None else x0, end))
            start = None
    if start is not None:
        spans.append((start, float(d[-1])))
    return [(a, b) for a, b in spans if b - a > min_width]


# --- чертёж гидроствора: перегибы, пикеты, подвал ---------------------------

def break_indices(d, z, tol=0.02, keep=()):
    """Индексы точек, от которых на чертёж опускаются вертикали.

    Вертикаль идёт от профиля вниз через весь подвал и делит его строки
    на ячейки, поэтому ставить её на каждую промерную точку нельзя: на
    двух сотнях точек подвал превращается в частокол. Берутся перегибы -
    вершины, где уклон профиля меняется больше допуска, - плюс концы
    створа и границы участков, которые нужны всегда.

    Допуск ноль означает все вершины: так строят чертёж по редкому
    промеру, где каждая точка значима.
    """
    d = np.asarray(d, float)
    z = np.asarray(z, float)
    n = d.size
    if n < 2:
        return list(range(n))
    must = {0, n - 1}
    for x in keep or ():
        must.add(int(np.argmin(np.abs(d - float(x)))))
    if tol <= 0.0:
        return sorted(set(range(n)) | must)
    out = set(must)
    for i in range(1, n - 1):
        dx0 = d[i] - d[i - 1]
        dx1 = d[i + 1] - d[i]
        s0 = (z[i] - z[i - 1]) / dx0 if abs(dx0) > 1e-12 else np.inf
        s1 = (z[i + 1] - z[i]) / dx1 if abs(dx1) > 1e-12 else np.inf
        if not np.isfinite(s0) or not np.isfinite(s1):
            out.add(i)
        elif abs(s1 - s0) > tol:
            out.add(i)
    return sorted(out)


def picket_parts(dist, start=0.0, step=100.0):
    """Пикет как пара (номер, остаток) для подписи вида 45+70.

    Пикетаж почти всегда идёт от начала трассы, а не от начала створа,
    поэтому начальное значение задаётся отдельно. Шаг пикета сто метров,
    но он вынесен параметром: на изысканиях встречаются и другие.
    """
    step = float(step)
    if step <= 0.0:
        return 0, float(start) + float(dist)
    v = float(start) + float(dist)
    n = int(math.floor(v / step))
    rem = v - n * step
    if step - rem < 5e-3:            # 99.999 это уже следующий пикет
        n, rem = n + 1, 0.0
    return n, rem


def footer_layout(d, rows, breaks, y_top, row_h, title_gap=0.0):
    """Разметка подвала чертежа: линейки, вертикали и ячейки с текстом.

    Строки бывают двух видов. Точечная несёт значение под каждой
    вертикалью - расстояние, отметка, пикет. Полосовая несёт значение на
    отрезке профиля - участок русла со своей шероховатостью или полоса
    растительности, которая идёт через несколько участков сразу.
    Механика у них общая, различается только источник.

    Ячейка это отрезок во всю свою ширину, а не точка. У полосовой
    строки он идёт от края до края отрезка, у точечной - между
    серединами промежутков до соседних вертикалей. Подпись тогда
    вешается стилем по линии и встаёт по центру ячейки сама.

    У ячейки, кроме подписи, бывает своё число: подпись идёт на чертёж,
    а число нужно оформлению и выборкам. Точечная строка берёт его из
    `nums` рядом со списком подписей, полосовая - четвёртым элементом
    отрезка. Где числа нет, там None: у названия участка и у полосы
    растительности числа не бывает.

    Возвращает словарь: rules линейки строк, verticals вертикали от
    профиля до низа подвала, cells ячейки с текстом и его точкой,
    titles заголовки строк слева, bottom отметка низа.
    """
    d = np.asarray(d, float)
    if d.size < 2 or not rows:
        return {"rules": [], "verticals": [], "cells": [], "titles": [],
                "bottom": float(y_top)}
    x0, x1 = float(d[0]), float(d[-1])
    row_h = float(row_h)
    y_top = float(y_top)
    bottom = y_top - row_h * len(rows)
    rules = [[(x0, y_top - k * row_h), (x1, y_top - k * row_h)]
             for k in range(len(rows) + 1)]
    xs = [float(d[i]) for i in breaks] if breaks is not None else []
    verticals = [[(x, y_top), (x, bottom)] for x in xs]
    cells, titles = [], []
    for k, row in enumerate(rows):
        ytop = y_top - k * row_h
        ymid = ytop - row_h / 2.0
        titles.append({"key": row.get("key"), "text": row.get("title") or "",
                       "x": x0 - float(title_gap), "y": ymid})
        if row.get("kind") == "point":
            vals = row.get("values") or []
            nums = row.get("nums") or []
            for j, (i, x) in enumerate(zip(breaks or [], xs)):
                if i >= len(vals) or vals[i] is None:
                    continue
                a = (xs[j - 1] + x) / 2.0 if j else x
                b = (xs[j + 1] + x) / 2.0 if j + 1 < len(xs) else x
                cells.append({"key": row.get("key"), "row": k,
                              "text": str(vals[i]), "x": x, "y": ymid,
                              "align": "center", "span": (a, b),
                              "num": nums[i] if i < len(nums) else None})
        else:
            for item in row.get("values") or []:
                a, b, text = item[0], item[1], item[2]
                num = item[3] if len(item) > 3 else None
                a, b = max(float(a), x0), min(float(b), x1)
                if b - a <= 1e-9 or text in (None, ""):
                    continue
                cells.append({"key": row.get("key"), "row": k,
                              "text": str(text), "x": (a + b) / 2.0,
                              "y": ymid, "align": "center",
                              "span": (a, b), "num": num})
    return {"rules": rules, "verticals": verticals, "cells": cells,
            "titles": titles, "bottom": bottom}


def elevation_scale(zlo, zhi, x0, vex=1.0, count=8, tick=1.0, step=0.0):
    """Вертикальная шкала отметок сбоку от профиля.

    Стилем шкалу тоже нарисовать можно, но правильно расставить деления
    трудно, поэтому она приходит готовой геометрией. Ось идёт от нижней
    отметки к верхней, деления встают на округлых значениях ряда
    1, 2, 2.5, 5, 10, подпись несёт саму отметку.

    Отсчёт растяжения тот же, что у профиля: от низа створа. Ось и
    деления возвращаются в координатах чертежа, отметка деления - в
    метрах, как её и подписывают.

    Возвращает (ось, деления), где ось это пара точек, а деление -
    словарь с отрезком, отметкой и подписью.
    """
    zlo, zhi = float(zlo), float(zhi)
    if not (zhi > zlo):
        return [], []

    def vy(v):
        return zlo + (float(v) - zlo) * float(vex)

    vals = ([round(v, 6) for v in _nice_values(zlo, zhi, int(count))]
            if step <= 0 else [])
    if step > 0:
        v = math.ceil(zlo / step) * step
        while v <= zhi + 1e-9:
            vals.append(round(v, 6))
            v += step
    # По делению сверху и снизу за пределами створа: от нижнего строят
    # подвал, верхнее закрывает шкалу над бровкой. Без них шкала
    # обрывается на первой круглой отметке внутри профиля
    if len(vals) >= 2:
        d = round(vals[1] - vals[0], 9)
        vals = [round(vals[0] - d, 6)] + vals + [round(vals[-1] + d, 6)]
    lo_v = min(vals) if vals else zlo
    hi_v = max(vals) if vals else zhi
    axis = [(float(x0), vy(min(zlo, lo_v))), (float(x0), vy(max(zhi, hi_v)))]
    ticks = []
    for v in vals:
        ticks.append({"z": v, "text": ("%g" % v),
                      "pts": [(float(x0) - float(tick), vy(v)),
                              (float(x0), vy(v))]})
    return axis, ticks
