# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
"""Сшивка детальной ЦМР с открытой. Без QGIS, только NumPy.

Задача повторяется у топографов и гидрологов: есть подробная съёмка на
участок и открытая ЦМР на всю округу, нужен один рельеф. Наивное
объединение даёт ступень по краю участка, и поток на ней встаёт.

Ступеней две, и они разной природы.

Первая - систематический сдвиг по высоте. Детальная съёмка идёт в
местной системе, открытые наборы в геоидной: COP30 на EGM2008, SRTM на
EGM96. Расхождение измеряется метрами и одинаково по всему участку.
Сглаживание перехода его не убирает: сгладится край, а вся открытая
часть останется поднятой или опущенной, и водосбор поедет. Поэтому
невязка снимается заранее, по кольцу вокруг участка, где обе ЦМР есть.
Медиана, а не среднее: в кольцо попадают крыши и кроны, а у радарных
наборов это выбросы в десятки метров, и среднее они утащат за собой.

Вторая - несовпадение форм на самом стыке. Здесь работает не дырка с
последующей интерполяцией, а вес: внутри участка единица, за буфером
ноль, в кольце плавный переход с нулевой производной на концах. Переход
выходит гладким по построению, а не по факту сглаживания, и в шов не
попадает форма, придуманная интерполяцией.

Ширина буфера задаётся в ячейках ОТКРЫТОЙ ЦМР, а не в метрах: смысл её
в том, чтобы переход был длиннее, чем размер ячейки грубого набора,
иначе смешивать нечего. Три-пять ячеек COP30 это 90-150 м.
"""

import numpy as np

_BIG = 1e18


def grid_from_extent(xmin, ymin, xmax, ymax, cell):
    """Сетка по охвату и размеру ячейки: (geotransform, nx, ny).

    Охват расширяется до целого числа ячеек, начало кладётся в левый
    верхний угол, шаг по игреку отрицательный - как принято в GeoTIFF.
    """
    cell = float(cell)
    if cell <= 0:
        raise ValueError("Размер ячейки должен быть больше нуля.")
    if xmax <= xmin or ymax <= ymin:
        raise ValueError("Охват пуст.")
    nx = int(np.ceil((float(xmax) - float(xmin)) / cell))
    ny = int(np.ceil((float(ymax) - float(ymin)) / cell))
    nx = max(1, nx)
    ny = max(1, ny)
    gt = (float(xmin), cell, 0.0, float(ymin) + ny * cell, 0.0, -cell)
    return gt, nx, ny


def cells_for(xmin, ymin, xmax, ymax, cell):
    """Сколько ячеек выйдет при таком охвате и шаге.

    Считается ДО выделения памяти: на сантиметровой съёмке и региональном
    охвате счёт идёт на триллионы, и попытка создать такой растр
    роняет QGIS без внятного сообщения.
    """
    _gt, nx, ny = grid_from_extent(xmin, ymin, xmax, ymax, cell)
    return nx * ny


def resample_rule(src_cell, dst_cell):
    """Как пересчитывать поверхность на другую сетку.

    При заметном укрупнении ячейки правильнее осреднять: билинейная выборка
    берёт значение в точке и теряет всё, что было между узлами, а на
    подробной съёмке это и есть сама подробность. При измельчении и
    близких размерах берётся билинейная.
    """
    src_cell = float(src_cell)
    dst_cell = float(dst_cell)
    if src_cell <= 0 or dst_cell <= 0:
        return "bilinear"
    return "average" if dst_cell > src_cell * 1.5 else "bilinear"


def _edt_1d_rows(f):
    """Квадраты расстояний вдоль строк, сразу по всем строкам.

    Параболическая огибающая Фельзенсвальба-Хуттенлохера, развёрнутая по
    второй оси. Стеки парабол ведутся массивами: у каждой строки свой
    указатель, а внутренние повторы идут по маске ещё не улёгшихся строк.
    Так на весь растр приходится столько шагов интерпретатора, сколько в
    строке ячеек, а не столько, сколько ячеек всего. Построчный вариант
    из topo_form на подробной ЦМР считался бы десятки секунд.
    """
    m, n = f.shape
    rows = np.arange(m)
    v = np.zeros((m, n), dtype=np.int64)
    z = np.empty((m, n + 1))
    z[:, 0] = -np.inf
    z[:, 1] = np.inf
    k = np.zeros(m, dtype=np.int64)

    for q in range(1, n):
        fq = f[:, q]
        fv = f[rows, v[rows, k]]
        vk = v[rows, k]
        with np.errstate(invalid="ignore"):
            s = ((fq + q * q) - (fv + vk * vk)) / (2.0 * (q - vk))
        active = s <= z[rows, k]
        while active.any():
            k[active] -= 1
            idx = np.flatnonzero(active)
            vk = v[idx, k[idx]]
            fv = f[idx, vk]
            with np.errstate(invalid="ignore"):
                s[idx] = (((f[idx, q] + q * q) - (fv + vk * vk))
                          / (2.0 * (q - vk)))
            active = np.zeros(m, dtype=bool)
            active[idx] = s[idx] <= z[idx, k[idx]]
        k += 1
        v[rows, k] = q
        z[rows, k] = s
        z[rows, k + 1] = np.inf

    k[:] = 0
    d = np.empty((m, n))
    for q in range(n):
        move = z[rows, k + 1] < q
        while move.any():
            k[move] += 1
            move = z[rows, k + 1] < q
        p = v[rows, k]
        d[:, q] = (q - p) * (q - p) + f[rows, p]
    return d


def distance_to_mask(mask):
    """Евклидово расстояние в ячейках до ближайшей ячейки маски.

    Внутри маски ноль. Если маска пуста, всюду бесконечность.
    """
    mask = np.asarray(mask, dtype=bool)
    if not mask.any():
        return np.full(mask.shape, np.inf)
    f = np.where(mask, 0.0, _BIG)
    d2 = _edt_1d_rows(f.T).T          # проход по столбцам
    d2 = _edt_1d_rows(d2)             # проход по строкам
    return np.sqrt(np.maximum(d2, 0.0))


def ring_mask(mask, width_px):
    """Кольцо шириной width_px ячеек СНАРУЖИ маски.

    Кольцо это и есть место, где обе ЦМР перекрываются: по нему меряется
    невязка и в нём же идёт переход.
    """
    width_px = float(width_px)
    if width_px <= 0:
        return np.zeros(np.shape(mask), dtype=bool)
    dist = distance_to_mask(mask)
    return (dist > 0) & (dist <= width_px)


def blend_weights(mask, width_px):
    """Вес детальной ЦМР: 1 внутри участка, 0 за буфером, плавно в кольце.

    Переход идёт по функции 1 - t*t*(3 - 2t), у неё нулевая производная на
    обоих концах, поэтому на границах кольца излома не возникает. Линейный
    переход дал бы перелом уклона ровно там, где его высматривают
    гидрологи.
    """
    mask = np.asarray(mask, dtype=bool)
    width_px = float(width_px)
    if width_px <= 0:
        return mask.astype(np.float64)
    dist = distance_to_mask(mask)
    t = np.clip(dist / width_px, 0.0, 1.0)
    return 1.0 - t * t * (3.0 - 2.0 * t)


def residual_stats(fine, coarse, where):
    """Невязка детальной и открытой ЦМР там, где обе есть.

    Возвращает словарь: число ячеек, медиана, средняя, размах по
    процентилям. Медиана и есть та поправка, которую надо снять.
    """
    fine = np.asarray(fine, dtype=np.float64)
    coarse = np.asarray(coarse, dtype=np.float64)
    ok = np.asarray(where, dtype=bool) & np.isfinite(fine) & np.isfinite(coarse)
    n = int(np.count_nonzero(ok))
    if n == 0:
        return {"n": 0, "median": 0.0, "mean": 0.0, "p05": 0.0, "p95": 0.0}
    d = fine[ok] - coarse[ok]
    return {"n": n,
            "median": float(np.median(d)),
            "mean": float(np.mean(d)),
            "p05": float(np.percentile(d, 5)),
            "p95": float(np.percentile(d, 95))}


def fit_shift(fine, coarse, where, mode="median"):
    """Поправка к открытой ЦМР: сдвиг или наклонная плоскость.

    mode='median' - одно число на весь растр. Годится почти всегда:
    расхождение геоида с местной системой на участке в километры меняется
    мало.

    mode='plane' - плоскость a*x + b*y + c по методу наименьших квадратов.
    Нужна там, где участок вытянут на десятки километров и поправка
    успевает уползти. Считается по тем же ячейкам кольца.

    mode='none' - не снимать ничего. Нужен, когда обе поверхности заведомо
    в одной системе и правка только испортит: например, подробная съёмка
    и региональная модель посчитаны из одних и тех же скважин.

    Постоянная невязка даёт у median и plane одинаковый ответ, и это не
    ошибка: плоскость по кольцу с постоянным расхождением вырождается в
    константу. Разница между режимами видна только там, где невязка
    наклонная.

    Возвращает (поправка_как_растр, отчёт). В отчёте для плоскости идут её
    крайние значения по растру: плоскость считается по кольцу, а
    применяется ко всему полю, и на большом охвате она может уехать
    далеко за пределы того, что видела.
    """
    fine = np.asarray(fine, dtype=np.float64)
    coarse = np.asarray(coarse, dtype=np.float64)
    ok = np.asarray(where, dtype=bool) & np.isfinite(fine) & np.isfinite(coarse)
    stats = residual_stats(fine, coarse, ok)
    if stats["n"] == 0:
        return np.zeros(fine.shape), dict(stats, mode="none")

    if mode == "none":
        # «Не снимать» обязано не снимать: раньше этот режим уходил в
        # ветку медианы и молча правил высоту, хотя пользователь просил
        # обратного.
        return np.zeros(fine.shape), dict(stats, mode="none",
                                          corr_min=0.0, corr_max=0.0)

    if mode != "plane":
        med = stats["median"]
        return np.full(fine.shape, med), dict(stats, mode="median",
                                              corr_min=med, corr_max=med)

    ny, nx = fine.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    d = fine[ok] - coarse[ok]
    # выбросы кольца сначала отсекаются по процентилям, иначе крыши и
    # кроны наклонят плоскость сильнее, чем сам датум
    lo, hi = np.percentile(d, [5, 95])
    keep = (d >= lo) & (d <= hi)
    a = np.column_stack([xx[ok][keep], yy[ok][keep],
                         np.ones(int(keep.sum()))])
    coef, _res, _rank, _sv = np.linalg.lstsq(a, d[keep], rcond=None)
    plane = coef[0] * xx + coef[1] * yy + coef[2]
    return plane, dict(stats, mode="plane",
                       plane=(float(coef[0]), float(coef[1]), float(coef[2])),
                       corr_min=float(plane.min()),
                       corr_max=float(plane.max()))


def merge(fine, coarse, mask, width_px=4.0, shift_mode="median",
          fill_holes=True):
    """Сшивает детальную и открытую ЦМР в один растр.

    fine, coarse - массивы одной формы и одной сетки, NaN там, где данных
    нет. mask - зона врезки, True внутри участка. width_px - ширина
    перехода в ячейках ОТКРЫТОЙ ЦМР, пересчитанная в ячейки этой сетки
    вызывающей стороной.

    Возвращает (растр, отчёт). В отчёте невязка до и после поправки:
    по ней и видно, что сшивка удалась.
    """
    fine = np.asarray(fine, dtype=np.float64)
    coarse = np.asarray(coarse, dtype=np.float64)
    if fine.shape != coarse.shape:
        raise ValueError("ЦМР должны быть приведены к одной сетке.")
    mask = np.asarray(mask, dtype=bool)
    if mask.shape != fine.shape:
        raise ValueError("Маска должна быть по форме растра.")

    # Кольцо берётся снаружи маски, но только там, где ЕСТЬ обе ЦМР.
    # Маска врезки обычно уже покрытия подробной съёмки: буфер и нужен
    # затем, чтобы попасть в полосу перекрытия. Если съёмка обрывается
    # ровно по маске, перекрытия нет и снимать датум не по чему - об этом
    # надо сказать вслух, а не молча сшить со ступенью.
    ring = ring_mask(mask, width_px) & np.isfinite(fine) & np.isfinite(coarse)
    correction, report = fit_shift(fine, coarse, ring, mode=shift_mode)
    report["overlap"] = int(np.count_nonzero(ring))
    if report["overlap"] == 0:
        report["warning"] = (
            "Подробная ЦМР не выходит за маску врезки: полосы перекрытия "
            "нет, поправка по высоте не снята. Расширьте маску внутрь "
            "участка или задайте буфер по данным съёмки.")
    coarse_adj = coarse + correction
    report["after"] = residual_stats(fine, coarse_adj, ring)

    w = blend_weights(mask, width_px)
    have_f = np.isfinite(fine)
    have_c = np.isfinite(coarse_adj)
    # там, где одной из ЦМР нет, вес другой становится единицей: дырка в
    # подробной съёмке закрывается открытой, а не остаётся пустой
    w = np.where(have_f, w, 0.0)
    w = np.where(have_c, w, np.where(have_f, 1.0, np.nan))

    out = np.full(fine.shape, np.nan)
    both = have_f & have_c
    out[both] = (w[both] * fine[both] + (1.0 - w[both]) * coarse_adj[both])
    only_f = have_f & ~have_c
    out[only_f] = fine[only_f]
    only_c = have_c & ~have_f
    out[only_c] = coarse_adj[only_c]

    if fill_holes:
        holes = ~np.isfinite(out)
        report["holes"] = int(np.count_nonzero(holes))
    else:
        report["holes"] = int(np.count_nonzero(~np.isfinite(out)))
    report["ring_cells"] = int(np.count_nonzero(ring))
    report["width_px"] = float(width_px)
    return out, report


def seam_step(z, mask, width_px):
    """Наибольший перепад между соседними ячейками в полосе шва.

    Мера приёмки: если сшивка удалась, перепад на шве не выделяется на
    фоне обычного рельефа. Возвращает (макс_перепад_в_кольце,
    макс_перепад_вне_кольца).
    """
    z = np.asarray(z, dtype=np.float64)
    ring = ring_mask(mask, width_px)
    d = np.zeros_like(z)
    for arr in (np.abs(np.diff(z, axis=0)), np.abs(np.diff(z, axis=1))):
        if arr.shape[0] < z.shape[0]:
            d[:-1, :] = np.fmax(d[:-1, :], arr)
            d[1:, :] = np.fmax(d[1:, :], arr)
        else:
            d[:, :-1] = np.fmax(d[:, :-1], arr)
            d[:, 1:] = np.fmax(d[:, 1:], arr)
    d[~np.isfinite(z)] = np.nan
    inside = d[ring & np.isfinite(d)]
    outside = d[(~ring) & np.isfinite(d)]
    return (float(inside.max()) if inside.size else 0.0,
            float(outside.max()) if outside.size else 0.0)
