# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Ядро объёмов земляных работ. Чистая математика, без QGIS и Qt.

Считается разность двух поверхностей на одной сетке и объём по ячейкам.
Формула простая до неприличия: объём равен сумме разностей, умноженной на
площадь ячейки. Вся сложность не в ней, а вокруг неё.

Первое. Две матрицы почти никогда не лежат на одной сетке, и приводить их
надо явно. Билинейная передискретизация здесь, а не в GDAL, по двум
причинам: она детерминированна и её видно в тестах, а ещё ближайший сосед
для поверхностей не годится совсем - он возвращает ступени, которые мы в
2.13 как раз и ловим.

Второе, и это главный источник расхождений с чужими программами. Сетка
может быть привязана по узлам или по центрам ячеек, а рамка обрезана по
разным границам. Сама передискретизация тут ни при чём: веса билинейки в
сумме дают единицу, и сдвиг сетки объём сохраняет, это закреплено тестом.
Расходятся цифры из-за другого - из-за того, какие ячейки вообще попали в
счёт. Полшага рамки на краю участка, чуть иначе обрезанная маска, и объём
уже другой, а виноватой назначают формулу. Поэтому описание сетки здесь
держится честным геотрансформом GDAL (начало в углу крайней ячейки), а
вызывающая сторона обязана напечатать начало, шаг и число ячеек.

Знак принят такой: разность считается «стало минус было». Положительная
разность это насыпь (материал добавлен), отрицательная это выемка
(материал снят). Так же считает и ArcGIS в инструменте Cut/Fill, только с
обратным знаком, поэтому знак оговаривается в справке отдельно.
"""
import numpy as np


# --- описание сетки ------------------------------------------------------

def grid_origin(gt):
    """Начало сетки: координаты угла крайней ячейки (x, y)."""
    return float(gt[0]), float(gt[3])


def cell_size(gt):
    """Размер ячейки по осям, всегда положительный (dx, dy)."""
    return abs(float(gt[1])), abs(float(gt[5]))


def cell_area(gt):
    """Площадь ячейки в единицах СК."""
    dx, dy = cell_size(gt)
    return dx * dy


def same_grid(gt_a, shape_a, gt_b, shape_b, tol=1e-6):
    """Совпадают ли сетки: размер ячейки, начало и число ячеек.

    Допуск нужен: геотрансформы приходят из разных файлов и различаются в
    последних разрядах, но это одна и та же сетка.
    """
    if tuple(shape_a) != tuple(shape_b):
        return False
    for i in (0, 1, 2, 3, 4, 5):
        if abs(float(gt_a[i]) - float(gt_b[i])) > tol:
            return False
    return True


# --- приведение к одной сетке -------------------------------------------

def resample_bilinear(arr, gt_src, gt_dst, shape_dst):
    """Билинейная передискретизация arr на сетку (gt_dst, shape_dst).

    Оси предполагаются несклонёнными (gt[2] и gt[4] равны нулю), что верно
    для всех матриц, которые мы строим и читаем. За краем исходной матрицы
    возвращается NaN: экстраполировать высоты нельзя, объём за пределами
    данных не считается.

    NaN в исходной матрице расползается на все четыре соседние ячейки, и
    это намеренно. Лучше отказаться от объёма в сомнительной ячейке, чем
    досчитать его по трём углам из четырёх.
    """
    arr = np.asarray(arr, dtype=float)
    ny_s, nx_s = arr.shape
    ny_d, nx_d = int(shape_dst[0]), int(shape_dst[1])

    # центры ячеек приёмника в координатах СК
    cols = np.arange(nx_d, dtype=float) + 0.5
    rows = np.arange(ny_d, dtype=float) + 0.5
    xs = gt_dst[0] + cols * gt_dst[1]
    ys = gt_dst[3] + rows * gt_dst[5]

    # обратный переход в непрерывные индексы источника (центр ячейки = i+0.5)
    fx = (xs - gt_src[0]) / gt_src[1] - 0.5
    fy = (ys - gt_src[3]) / gt_src[5] - 0.5
    FX, FY = np.meshgrid(fx, fy)

    x0 = np.floor(FX).astype(int)
    y0 = np.floor(FY).astype(int)
    tx = FX - x0
    ty = FY - y0

    out = np.full((ny_d, nx_d), np.nan, dtype=float)
    inside = (x0 >= 0) & (y0 >= 0) & (x0 + 1 < nx_s) & (y0 + 1 < ny_s)
    if not np.any(inside):
        return out

    xi = np.clip(x0, 0, nx_s - 2)
    yi = np.clip(y0, 0, ny_s - 2)
    v00 = arr[yi, xi]
    v10 = arr[yi, xi + 1]
    v01 = arr[yi + 1, xi]
    v11 = arr[yi + 1, xi + 1]

    top = v00 * (1.0 - tx) + v10 * tx
    bot = v01 * (1.0 - tx) + v11 * tx
    val = top * (1.0 - ty) + bot * ty
    out[inside] = val[inside]
    return out


# --- объёмы --------------------------------------------------------------

def difference(after, before):
    """Разность поверхностей «стало минус было», NaN там, где нет данных."""
    a = np.asarray(after, dtype=float)
    b = np.asarray(before, dtype=float)
    if a.shape != b.shape:
        raise ValueError("Поверхности разного размера, приведите к одной сетке.")
    return a - b


def cutfill_stats(diff, area, dead=0.0):
    """Объёмы и площади насыпи и выемки по матрице разностей.

    area  - площадь ячейки в единицах СК.
    dead  - мёртвая зона по высоте: ячейки, где модуль разности меньше
            этого числа, считаются неизменными. Нужна не для красоты:
            две поверхности, построенные разными способами, всегда шумят
            на сантиметры, и без мёртвой зоны весь фон попадает то в
            насыпь, то в выемку и раздувает обе цифры.

    Возвращает словарь. Объёмы положительны обе величины, знак несёт net.
    """
    d = np.asarray(diff, dtype=float)
    ok = np.isfinite(d)
    n_ok = int(np.count_nonzero(ok))
    st = {
        "cells": int(d.size),
        "cells_valid": n_ok,
        "cells_nodata": int(d.size - n_ok),
        "fill_volume": 0.0, "cut_volume": 0.0, "net_volume": 0.0,
        "fill_area": 0.0, "cut_area": 0.0, "flat_area": 0.0,
        "max_fill": 0.0, "max_cut": 0.0, "mean_diff": float("nan"),
        "dead": float(dead),
    }
    if n_ok == 0:
        return st

    v = d[ok]
    dead = abs(float(dead))
    is_fill = v > dead
    is_cut = v < -dead
    is_flat = ~(is_fill | is_cut)

    st["fill_volume"] = float(np.sum(v[is_fill]) * area)
    st["cut_volume"] = float(-np.sum(v[is_cut]) * area)
    st["net_volume"] = st["fill_volume"] - st["cut_volume"]
    st["fill_area"] = float(np.count_nonzero(is_fill) * area)
    st["cut_area"] = float(np.count_nonzero(is_cut) * area)
    st["flat_area"] = float(np.count_nonzero(is_flat) * area)
    st["max_fill"] = float(v.max()) if v.size else 0.0
    st["max_cut"] = float(-v.min()) if v.size else 0.0
    st["mean_diff"] = float(v.mean())
    return st


def zone_stats(diff, labels, area, dead=0.0):
    """Те же объёмы по участкам. labels - целые метки, 0 это вне участков.

    Возвращает словарь {метка: статистика}. Метка 0 не возвращается.
    """
    d = np.asarray(diff, dtype=float)
    lb = np.asarray(labels)
    if d.shape != lb.shape:
        raise ValueError("Матрица меток не совпадает с матрицей разностей.")
    out = {}
    for code in np.unique(lb):
        code = int(code)
        if code == 0:
            continue
        sub = np.where(lb == code, d, np.nan)
        out[code] = cutfill_stats(sub, area, dead)
    return out


def balance_verdict(st, tol_frac=0.05):
    """Ключ вердикта по балансу земляных масс.

    Сравнивается нетто с оборотом (насыпь плюс выемка). Проектировщику
    важно не само нетто, а его доля: сто кубов невязки при обороте в сто
    тысяч это баланс, а при обороте в двести кубов это вывоз половины.
    """
    turn = st["fill_volume"] + st["cut_volume"]
    if turn <= 0:
        return "empty"
    frac = abs(st["net_volume"]) / turn
    if frac <= tol_frac:
        return "balanced"
    return "import" if st["net_volume"] > 0 else "export"


# --- представление чисел -------------------------------------------------

NBSP = "\u00a0"


def format_number(value, digits=0, group=NBSP):
    """Число с разделителями разрядов: 701 224 863, а не 7.01225e+08.

    Экспоненциальная запись годится для отладки и никуда не годится в
    ведомости, которую кто-то понесёт согласовывать. Разряды разделяются
    неразрывным пробелом, чтобы число не рвалось по переносу строки.

    Разделителем целой и дробной части оставлена точка. Русской запятой
    здесь быть не может: тот же отчёт собирается и по-английски, а числа
    словарь не переводит.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    if v != v or v in (float("inf"), float("-inf")):
        return "-"
    digits = max(0, int(digits))
    s = "%.*f" % (digits, abs(v))
    if "." in s:
        head, tail = s.split(".", 1)
        tail = "." + tail
    else:
        head, tail = s, ""
    parts = []
    while len(head) > 3:
        parts.insert(0, head[-3:])
        head = head[:-3]
    parts.insert(0, head)
    out = group.join(parts) + tail
    return ("-" + out) if v < 0 else out


def format_volume(value):
    """Объём в кубометрах: целые, с разделителями разрядов."""
    return format_number(value, 0)


def format_area_ha(value_m2):
    """Площадь в гектарах с двумя знаками. В ведомости гектары читаемее."""
    return format_number(float(value_m2) / 10000.0, 2)
