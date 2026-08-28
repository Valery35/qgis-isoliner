# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
"""Мультисеточные B-сплайны (MBA): поверхность по разбросанным точкам.

Метод Ли, Волберга и Шина (1997). Идея простая. Берётся грубая решётка
контрольных точек, по ней строится кубический B-сплайн, приближающий
данные. Он приближает грубо, поэтому считается остаток - разность между
замером и текущей поверхностью, - решётка удваивается, и остаток
приближается заново. Так уровень за уровнем: каждый следующий подхватывает
то, что не смог предыдущий.

Чем он берёт. Система уравнений не решается вообще: коэффициент решётки
считается явно, взвешенной суммой по точкам, попавшим в носитель своего
сплайна. Поэтому работа линейна по числу точек, а память - по размеру
решётки. Кригинг на каждой ячейке решает систему по соседям, и на десятке
миллионов замеров это несопоставимые вещи.

Чего он не даёт. Ни ошибки оценки, ни модели ковариации, ни весов, которые
можно предъявить. Это аппроксиматор, а не оценщик: он не знает, насколько
хорош его ответ. Поэтому MBA идёт рядом с минимальной кривизной, а не
вместо кригинга, и особенно хорош как ТРЕНД, который дальше уточняют
кригингом остатков.

За пределами облака точек он экстраполирует произвольно: у краевых
коэффициентов нет данных, и поверхность там уходит куда угодно. Обрезка
области - забота вызывающей стороны.

Размерность не зашита. Решётка задаётся числом ячеек по каждой оси, и
двумерный случай отличается от трёхмерного только длиной этого списка.
Носитель кубического сплайна - окно 4 в каждой оси: 16 ячеек на плоскости,
64 в объёме.

Модуль не зависит от QGIS и проверяется тестами.
"""

import numpy as np

# Носитель кубического B-сплайна: четыре ячейки по каждой оси.
SUPPORT = 4


def _weights_1d(t):
    """Кубические B-сплайны в точке t внутри ячейки, t в [0, 1).

    Возвращает массив (4, n): вклад четырёх соседних контрольных точек.
    Сумма по четырём равна единице при любом t - это и есть разбиение
    единицы, на котором держится всё остальное.
    """
    t = np.asarray(t, dtype=np.float64)
    omt = 1.0 - t
    return np.stack([
        omt * omt * omt / 6.0,
        (3.0 * t * t * t - 6.0 * t * t + 4.0) / 6.0,
        (-3.0 * t * t * t + 3.0 * t * t + 3.0 * t + 1.0) / 6.0,
        t * t * t / 6.0,
    ])


def _offsets(ndim):
    """Все смещения носителя: (SUPPORT**ndim, ndim)."""
    grids = np.meshgrid(*([np.arange(SUPPORT)] * ndim), indexing="ij")
    return np.stack([g.ravel() for g in grids], axis=1)


class Lattice(object):
    """Решётка коэффициентов одного уровня.

    `lo` и `hi` - углы области, `n` - число ЯЧЕЕК по каждой оси. Контрольных
    точек на три больше по каждой оси: кубическому сплайну нужен запас по
    краям, иначе крайняя ячейка останется без носителя.
    """

    def __init__(self, lo, hi, n):
        self.lo = np.asarray(lo, dtype=np.float64)
        self.hi = np.asarray(hi, dtype=np.float64)
        self.n = np.asarray(n, dtype=np.int64)
        if np.any(self.n < 1):
            raise ValueError("Решётка должна иметь хотя бы одну ячейку.")
        if self.lo.shape != self.hi.shape or self.lo.shape != self.n.shape:
            raise ValueError("Размерности lo, hi и n должны совпадать.")
        self.ndim = int(self.lo.size)
        span = self.hi - self.lo
        span[span <= 0] = 1.0
        self.step = span / self.n
        self.shape = tuple(int(v) + 3 for v in self.n)
        self.coef = np.zeros(self.shape, dtype=np.float64)

    # --- служебное ---

    def _cells(self, pts):
        """Ячейка и доля внутри неё для каждой точки."""
        rel = (np.asarray(pts, dtype=np.float64) - self.lo) / self.step
        base = np.floor(rel).astype(np.int64)
        # точка на самом верхнем краю обязана попасть в последнюю ячейку,
        # иначе она вылетит за решётку и её вклад потеряется
        np.clip(base, 0, self.n - 1, out=base)
        frac = rel - base
        np.clip(frac, 0.0, 1.0, out=frac)
        return base, frac

    def _support(self, base, frac):
        """Индексы носителя и веса для каждой точки.

        Возвращает (idx, w): idx - плоские индексы в массиве коэффициентов,
        (m, SUPPORT**ndim); w - веса той же формы.
        """
        npts = base.shape[0]
        offs = _offsets(self.ndim)                       # (k, ndim)
        w1 = [_weights_1d(frac[:, d]) for d in range(self.ndim)]

        idx = np.zeros((npts, offs.shape[0]), dtype=np.int64)
        w = np.ones((npts, offs.shape[0]), dtype=np.float64)
        for d in range(self.ndim):
            take = base[:, d][:, None] + offs[None, :, d]
            idx = idx * self.shape[d] + take
            w = w * w1[d][offs[:, d], :].T
        return idx, w

    # --- построение и оценка ---

    def fit(self, pts, vals):
        """Считает коэффициенты по точкам и значениям.

        Правило из статьи: каждая точка раскладывает своё значение по
        носителю пропорционально квадрату веса, а коэффициент собирает
        приход со всех своих точек. Знаменатель - сумма квадратов весов:
        там, где точек нет, он ноль, и коэффициент остаётся нулевым.
        """
        pts = np.asarray(pts, dtype=np.float64)
        vals = np.asarray(vals, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != self.ndim:
            raise ValueError("Точки должны быть массивом (m, ndim).")
        if vals.shape[0] != pts.shape[0]:
            raise ValueError("Число значений и точек должно совпадать.")
        base, frac = self._cells(pts)
        idx, w = self._support(base, frac)

        w2 = w * w
        denom_pt = w2.sum(axis=1)
        denom_pt[denom_pt == 0.0] = 1.0
        # вклад точки в коэффициент: phi = w * v / sum(w^2)
        phi = w * (vals / denom_pt)[:, None]

        size = self.coef.size
        num = np.bincount(idx.ravel(), weights=(w2 * phi).ravel(),
                          minlength=size)
        den = np.bincount(idx.ravel(), weights=w2.ravel(), minlength=size)
        with np.errstate(invalid="ignore", divide="ignore"):
            c = np.where(den > 0, num / np.where(den > 0, den, 1.0), 0.0)
        self.coef = c.reshape(self.shape)
        return self

    def evaluate(self, pts):
        """Значение поверхности в точках."""
        pts = np.asarray(pts, dtype=np.float64)
        if pts.ndim != 2 or pts.shape[1] != self.ndim:
            raise ValueError("Точки должны быть массивом (m, ndim).")
        base, frac = self._cells(pts)
        idx, w = self._support(base, frac)
        flat = self.coef.ravel()
        return (flat[idx] * w).sum(axis=1)


def fit(pts, vals, lo=None, hi=None, grid=(2, 2), levels=8, tol=None,
        progress=None):
    """Строит поверхность MBA и возвращает список решёток по уровням.

    `grid` - число ячеек начальной решётки по каждой оси. Оно и задаёт
    радиус влияния: чем крупнее ячейка, тем дальше расходится замер.
    Разные числа по осям дают анизотропию - на разведочной сети, вытянутой
    по простиранию, это то, что нужно.

    `levels` - сколько раз решётка удваивается. Каждый уровень вдвое
    подробнее и вдвое ближе к данным; мало уровней - гладкий тренд, много -
    поверхность проходит через замеры. Это и есть управление сглаживанием.

    `tol` - остановка по остатку: если наибольшая невязка стала меньше,
    дальше дробить нечего.
    """
    pts = np.asarray(pts, dtype=np.float64)
    vals = np.asarray(vals, dtype=np.float64).ravel()
    if pts.ndim != 2:
        raise ValueError("Точки должны быть массивом (m, ndim).")
    if pts.shape[0] == 0:
        raise ValueError("Нет ни одной точки.")
    ndim = pts.shape[1]

    lo = pts.min(axis=0) if lo is None else np.asarray(lo, dtype=np.float64)
    hi = pts.max(axis=0) if hi is None else np.asarray(hi, dtype=np.float64)
    lo = np.asarray(lo, dtype=np.float64).copy()
    hi = np.asarray(hi, dtype=np.float64).copy()
    flat = hi <= lo
    if np.any(flat):                       # вырожденная ось: даём ей толщину
        hi[flat] = lo[flat] + 1.0

    grid = np.asarray(grid, dtype=np.int64)
    if grid.size == 1:
        grid = np.repeat(grid, ndim)
    if grid.size != ndim:
        raise ValueError("Размер начальной решётки должен быть по числу осей.")

    lattices = []
    resid = vals.copy()
    n = grid.copy()
    for lvl in range(max(1, int(levels))):
        lat = Lattice(lo, hi, n).fit(pts, resid)
        lattices.append(lat)
        resid = resid - lat.evaluate(pts)
        worst = float(np.max(np.abs(resid))) if resid.size else 0.0
        if progress is not None:
            progress(lvl, worst, tuple(int(v) for v in n))
        if tol is not None and worst <= float(tol):
            break
        n = n * 2
    return lattices


def evaluate(lattices, pts):
    """Сумма всех уровней в точках."""
    pts = np.asarray(pts, dtype=np.float64)
    out = np.zeros(pts.shape[0], dtype=np.float64)
    for lat in lattices:
        out += lat.evaluate(pts)
    return out


def evaluate_grid(lattices, axes):
    """Значения на прямоугольной сетке.

    `axes` - координаты узлов по каждой оси. Возвращает массив формы
    (len(axes[0]), len(axes[1]), ...). Точки строятся построчно, чтобы на
    большой сетке не держать в памяти весь список сразу.
    """
    axes = [np.asarray(a, dtype=np.float64) for a in axes]
    shape = tuple(a.size for a in axes)
    out = np.empty(shape, dtype=np.float64)
    if len(axes) == 1:
        return evaluate(lattices, axes[0][:, None])

    rest = [a for a in axes[1:]]
    mesh = np.meshgrid(*rest, indexing="ij")
    tail = np.stack([m.ravel() for m in mesh], axis=1)
    for i, v in enumerate(axes[0]):
        block = np.column_stack([np.full(tail.shape[0], v), tail])
        out[i] = evaluate(lattices, block).reshape(tuple(a.size for a in rest))
    return out


def levels_report(lattices, pts, vals):
    """Невязка по уровням: чем каждый уровень помог.

    Нужна не для красоты. По ней видно, когда дробить пора прекратить:
    если очередной уровень почти не уменьшил невязку, дальше он ловит уже
    не структуру, а шум замеров.
    """
    pts = np.asarray(pts, dtype=np.float64)
    vals = np.asarray(vals, dtype=np.float64).ravel()
    out = []
    cur = np.zeros_like(vals)
    for lat in lattices:
        cur = cur + lat.evaluate(pts)
        d = vals - cur
        out.append({"cells": tuple(int(v) for v in lat.n),
                    "max": float(np.max(np.abs(d))) if d.size else 0.0,
                    "rms": float(np.sqrt(np.mean(d * d))) if d.size else 0.0})
    return out


def surface_on_grid(lattices, gt, nx, ny):
    """Поверхность на растровой сетке: массив (ny, nx), строка 0 - север.

    Отдельная функция, а не пара строк в инструменте, по одной причине:
    здесь легко перепутать порядок осей. Точки данных идут как (x, y), а
    растр адресуется как (строка, столбец), то есть (y, x). Подставив
    координаты в порядке строк, получаешь картинку, которая выглядит
    правдоподобно и при этом неверна везде - на неквадратной сетке она
    просто съезжает, а на квадратной оказывается зеркальной.

    `gt` - геопривязка GDAL: (x0, шаг_x, 0, y_верх, 0, -шаг_y). Значения
    берутся в ЦЕНТРАХ ячеек.
    """
    x0, dx, _rx, ytop, _ry, dy = [float(v) for v in gt]
    nx, ny = int(nx), int(ny)
    ax = x0 + dx * (np.arange(nx) + 0.5)
    ay = ytop + dy * (np.arange(ny) + 0.5)
    # оси передаются в порядке данных (x, y), результат разворачивается в
    # порядок растра (строка = y)
    return evaluate_grid(lattices, [ax, ay]).T


def clamp(values, lo=None, hi=None):
    """Срезает значения к физическим границам. Возвращает (массив, отчёт).

    Аппроксиматор не знает, что содержание не бывает отрицательным, а
    мощность не бывает больше вскрытой. Между замерами гладкая поверхность
    проскакивает за диапазон - особенно там, где рядом стоят сильно разные
    значения, и особенно у края облака точек.

    Срез полезнее, чем вид «как получилось», но он же и признак: если к
    границе прижата заметная доля площади, поверхность там не оценена, а
    выдумана, и надо менять решётку или число уровней. Поэтому отчёт
    считает не только сколько ячеек срезано, но и насколько далеко
    уходило: одно дело промах на сотую, другое - вдвое за диапазон.

    NaN не трогаются: это ячейки вне области, а не вылет за границу.
    """
    arr = np.array(values, dtype=np.float64, copy=True)
    rep = {"low": 0, "high": 0, "worst_low": 0.0, "worst_high": 0.0,
           "total": int(np.count_nonzero(np.isfinite(arr)))}
    if lo is not None:
        m = np.isfinite(arr) & (arr < lo)
        rep["low"] = int(np.count_nonzero(m))
        if rep["low"]:
            rep["worst_low"] = float(lo - np.min(arr[m]))
        arr[m] = lo
    if hi is not None:
        m = np.isfinite(arr) & (arr > hi)
        rep["high"] = int(np.count_nonzero(m))
        if rep["high"]:
            rep["worst_high"] = float(np.max(arr[m]) - hi)
        arr[m] = hi
    return arr, rep
