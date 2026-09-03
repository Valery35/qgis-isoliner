# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), - либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии - в файле LICENSE (на английском, юридически значим).
"""
KB2D engine - pure Python/NumPy, no QGIS dependency (headless-testable).

Faithful port of the ArcGIS Isoliner add-in 2D kriging core
(GlobalOperation.vb: cova2_2D / Kriging2DInPoint), itself a port of the
GSLIB kb2d routine.

GSLIB conventions are preserved:
  it (model)  : 1 spherical, 2 exponential, 3 gaussian, 4 power
  ktype       : 0 = simple kriging (uses skmean), 1 = ordinary kriging
  ang         : azimuth, clockwise degrees from Y (north)
  anis        : minor-axis range / major-axis range  (<=1)
"""
import math
import numpy as np

DTOR = math.pi / 180.0
EPS = 1e-11

# UI model codes (0-based) -> GSLIB it (1-based)
MODEL_SPHERICAL = 0
MODEL_EXPONENTIAL = 1
MODEL_GAUSSIAN = 2
MODEL_POWER = 3

# Гауссова модель без наггета даёт плохо обусловленную систему (осцилляции,
# отрицательные веса). Держим минимальный наггет как долю структурного силла.
GAUSS_MIN_NUGGET_FRAC = 0.01


class Variogram:
    """Nugget + one or more nested structures (cova2_2D port)."""

    def __init__(self, nugget, structures):
        # structures: list of dict(it=1..4, cc, aa, ang, anis)
        self.c0 = float(nugget)
        self.it = [int(s["it"]) for s in structures]
        self.cc = [float(s["cc"]) for s in structures]
        self.aa = [max(float(s["aa"]), EPS) for s in structures]
        # У степенной модели «радиус» это показатель степени ω, а не
        # расстояние: cov = pmx - cc·h^ω, и осмысленна она лишь при
        # 0 < ω < 2. Радиус в метрах, попавший сюда по недосмотру, даёт
        # h^3000 - переполнение и мусор вместо оценки.
        #
        # Проверка стоит в ядре, а не только в обвязке инструмента:
        # вариограмма приходит ещё из таблицы моделей и из чужого кода,
        # и защита обязана быть там, где живёт формула.
        self.power_clamped = []
        raw_aa = [float(s["aa"]) for s in structures]
        for i, code in enumerate(self.it):
            # Сравнивается ИСХОДНОЕ значение: self.aa уже поджато к EPS
            # выше, и ноль превратился бы в крошечное положительное
            # число, проскочив проверку.
            if code == 4 and not (0.05 <= raw_aa[i] < 2.0):
                self.power_clamped.append((i + 1, raw_aa[i]))
                self.aa[i] = min(max(raw_aa[i], 0.05), 1.999)
        self.ang = [float(s["ang"]) for s in structures]
        self.anis = [max(float(s["anis"]), EPS) for s in structures]
        # Гауссова модель численно неустойчива при нулевом наггете -
        # принудительно держим минимальный наггет (доля структурного силла).
        self.nugget_raised_from = None
        gauss_sill = sum(c for t, c in zip(self.it, self.cc) if t == 3)
        if gauss_sill > 0.0:
            min_c0 = GAUSS_MIN_NUGGET_FRAC * gauss_sill
            if self.c0 < min_c0:
                self.nugget_raised_from = self.c0
                self.c0 = min_c0
        self.nst = len(structures)
        self.pmx = 9999.0
        self.rotmat = []
        # Кэш поворотов: пересобирать матрицу на каждую ячейку сетки
        # дорого, а различных азимутов с шагом в десятую градуса 1800.
        self._rot_cache = {}
        self.maxcov = self.c0
        for i in range(self.nst):
            az = (90.0 - self.ang[i]) * DTOR
            ca, sa = math.cos(az), math.sin(az)
            self.rotmat.append((ca, sa, -sa, ca))
            self.maxcov += self.pmx if self.it[i] == 4 else self.cc[i]

    def rotated(self, azimuth):
        """Копия вариограммы с другим азимутом главной оси.

        Пересобирается только матрица поворота: модель, наггет, вклад и
        коэффициент сжатия остаются как были. Коэффициент один на участок
        решено сознательно - второй меняющийся параметр дал бы возможность
        подогнать карту под ожидание, а кросс-валидация этого не покажет.
        """
        key = round(float(azimuth), 1)
        cached = self._rot_cache.get(key)
        if cached is not None:
            return cached
        other = Variogram.__new__(Variogram)
        other.__dict__.update(self.__dict__)
        other.ang = [float(azimuth)] * self.nst
        az = (90.0 - float(azimuth)) * DTOR
        ca, sa = math.cos(az), math.sin(az)
        other.rotmat = [(ca, sa, -sa, ca)] * self.nst
        other._rot_cache = self._rot_cache
        self._rot_cache[key] = other
        return other

    def cova2(self, dx, dy):
        """Covariance for a separation vector (dx, dy)."""
        if (dx * dx + dy * dy) < EPS:
            return self.maxcov
        cov = 0.0
        for i in range(self.nst):
            r0, r1, r2, r3 = self.rotmat[i]
            dx1 = dx * r0 + dy * r1
            dy1 = (dx * r2 + dy * r3) / self.anis[i]
            h = math.sqrt(max(dx1 * dx1 + dy1 * dy1, 0.0))
            t = self.it[i]
            if t == 1:                       # spherical
                hr = h / self.aa[i]
                if hr < 1.0:
                    cov += self.cc[i] * (1.0 - hr * (1.5 - 0.5 * hr * hr))
            elif t == 2:                     # exponential
                cov += self.cc[i] * math.exp(-h / self.aa[i])
            elif t == 3:                     # gaussian
                cov += self.cc[i] * math.exp(-(h * h) / (self.aa[i] * self.aa[i]))
            else:                            # power
                cov += self.pmx - self.cc[i] * (h ** self.aa[i])
        return cov

    def cova2_array(self, dx, dy):
        """Ковариация для массивов разностей (dx, dy) - векторный двойник cova2.

        Нужна для блочного кригинга, где на каждую ячейку приходится суммировать
        ковариацию по точкам дискретизации. Результат совпадает с cova2 поэлементно,
        включая значение maxcov при нулевой разности (точечная C(0))."""
        dx = np.asarray(dx, float)
        dy = np.asarray(dy, float)
        cov = np.zeros(np.broadcast(dx, dy).shape, float)
        for i in range(self.nst):
            r0, r1, r2, r3 = self.rotmat[i]
            dx1 = dx * r0 + dy * r1
            dy1 = (dx * r2 + dy * r3) / self.anis[i]
            h = np.sqrt(np.maximum(dx1 * dx1 + dy1 * dy1, 0.0))
            t = self.it[i]
            if t == 1:                       # spherical
                hr = h / self.aa[i]
                cov += np.where(hr < 1.0,
                                self.cc[i] * (1.0 - hr * (1.5 - 0.5 * hr * hr)),
                                0.0)
            elif t == 2:                     # exponential
                cov += self.cc[i] * np.exp(-h / self.aa[i])
            elif t == 3:                     # gaussian
                cov += self.cc[i] * np.exp(-(h * h) / (self.aa[i] * self.aa[i]))
            else:                            # power
                cov += self.pmx - self.cc[i] * (h ** self.aa[i])
        zero = (dx * dx + dy * dy) < EPS
        if np.any(zero):
            cov = np.where(zero, self.maxcov, cov)
        return cov


def block_offsets(cell, nxdis, nydis):
    """Смещения точек дискретизации блока относительно центра ячейки (GSLIB kb2d).

    Блок - квадратная ячейка грида размером cell. Делится на nxdis×nydis
    подъячеек, точки берутся в их центрах. Возвращает (bdx, bdy) - два массива
    длины nxdis*nydis. При 1×1 - единственная точка (0, 0): точечный кригинг."""
    nxdis = max(int(nxdis), 1)
    nydis = max(int(nydis), 1)
    xdis = cell / nxdis
    ydis = cell / nydis
    xs = (np.arange(nxdis) + 0.5) * xdis - 0.5 * cell
    ys = (np.arange(nydis) + 0.5) * ydis - 0.5 * cell
    gx, gy = np.meshgrid(xs, ys)
    return gx.ravel(), gy.ravel()


def block_block_cov(vg, bdx, bdy):
    """Средняя ковариация «блок-блок» Cbb - по всем парам точек дискретизации.

    Это дисперсионный член блочного кригинга: средняя точечная ковариация внутри
    блока. На диагонали (точка сама с собой) по правилу GSLIB вычитается наггет:
    короткомасштабный наггет внутри блока усредняется. Для блока 1×1 возвращает
    точечную C(0) = maxcov, и блочный кригинг вырождается в точечный."""
    ndb = len(bdx)
    if ndb <= 1:
        return vg.maxcov
    dx = bdx[:, None] - bdx[None, :]
    dy = bdy[:, None] - bdy[None, :]
    cov = vg.cova2_array(dx, dy)
    cov = cov - np.eye(ndb) * vg.c0          # снять наггет с диагонали (GSLIB)
    return float(cov.sum() / (ndb * ndb))


def clip_outliers(values, vmin=None, vmax=None, pct=0.0, cap=False):
    """Отсев/срезка ураганных проб. Возвращает (values_out, keep_mask, lo, hi).

    Границы: явные vmin/vmax имеют приоритет; иначе - перцентильные [pct;
    100-pct] (если pct>0); иначе границ нет. Режимы:
      cap=False - отсеять: keep_mask=False у проб вне [lo; hi];
      cap=True  - срезать: значения вне [lo; hi] прижимаются к границе,
                  keep_mask остаётся True для всех.
    Универсальное применение: xs, ys, v = xs[keep], ys[keep], out[keep].
    """
    v = np.asarray(values, float)
    if vmin is None and vmax is None and (pct is None or pct <= 0):
        return v, np.ones(len(v), bool), float("-inf"), float("inf")
    lo, hi = float("-inf"), float("inf")
    if pct and pct > 0 and len(v):
        p = min(max(float(pct), 0.0), 49.0)
        lo = float(np.percentile(v, p))
        hi = float(np.percentile(v, 100.0 - p))
    if vmin is not None:
        lo = float(vmin)
    if vmax is not None:
        hi = float(vmax)
    if lo > hi:
        lo, hi = hi, lo
    if cap:
        return np.clip(v, lo, hi), np.ones(len(v), bool), lo, hi
    return v, (v >= lo) & (v <= hi), lo, hi


class InverseCache:
    """Кэш обращённых матриц кригинга по набору соседей.

    Матрица системы зависит только от того, КАКИЕ замеры попали в
    окрестность, а правая часть - от положения ячейки. Наборы у соседних
    ячеек часто совпадают, поэтому обращение считается один раз на
    набор, а дальше оценка это одно матрично-векторное умножение.

    Ключ - набор, отсортированный по индексу пробы, а не по расстоянию.
    Порядок по расстоянию у каждой ячейки свой, и как ключ он бесполезен:
    замер показал 9973 различных набора на 10000 ячеек против 4032 при
    сортировке по индексу.

    Локальный азимут в ключ не входит: он усредняется по тем же соседям,
    поэтому одинаковый набор даёт одинаковую вариограмму. Проверено
    замером - число записей с анизотропией и без совпадает.

    Потолок нужен на густой сети, где наборы почти не повторяются: там
    кэш растёт, а пользы не приносит. По достижении предела пополнение
    прекращается, а расчёт продолжается как раньше.
    """

    __slots__ = ("data", "budget", "spent", "hits", "miss", "full")

    def __init__(self, budget_mb=64.0):
        self.data = {}
        # Предел по ПАМЯТИ, а не по числу записей: при сорока восьми
        # соседях одна матрица весит вчетверо больше, чем при двадцати
        # четырёх, и потолок в тысячах записей означал бы то сорок
        # мегабайт, то сто шестьдесят пять.
        self.budget = float(budget_mb) * 1e6
        self.spent = 0.0
        self.hits = 0
        self.miss = 0
        self.full = False

    def get(self, key):
        got = self.data.get(key)
        if got is None:
            self.miss += 1
        else:
            self.hits += 1
        return got

    def put(self, key, value):
        if self.full:
            return
        size = float(value.nbytes)
        if self.spent + size > self.budget:
            # Место кончилось: пополнение прекращается, расчёт идёт как
            # прежде. Выбрасывать накопленное незачем - оно уже окупилось.
            self.full = True
            return
        self.data[key] = value
        self.spent += size

    def rate(self):
        total = self.hits + self.miss
        return (self.hits / total) if total else 0.0


def _solve_point(xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                 ndmin, ndmax, rad2, nodata, bdx=None, bdy=None, cbb=None,
                 cache=None, gid=None, vg_key=b""):
    """Кригинг в одной точке (или в блоке). Возвращает (оценка, дисперсия).

    Точечный кригинг (bdx=None или один узел дискретизации) воспроизводит пробу
    в совпадающем узле и даёт нулевую дисперсию. Блочный кригинг (bdx/bdy -
    смещения точек дискретизации от центра ячейки, cbb - блок-блок ковариация)
    оценивает СРЕДНЕЕ по блоку: правые ковариации усредняются по точкам блока,
    дисперсионный член - блочный (cbb < C(0)). Блок не воспроизводит пробы точно,
    зато дисперсия ниже точечной - это и есть смысл оценки запасов по блоку.

    Дисперсия - это дисперсия ошибки кригинга:
        SK:  σ² = Cbb − Σ λ_i C(x_i, блок)
        OK:  σ² = Cbb − Σ λ_i C(x_i, блок) − μ   (μ - множитель Лагранжа)
    Cbb = C(0) для точки, средняя внутриблочная ковариация для блока. Если оценка
    не получена (вырожденная система) - дисперсия принимается равной Cbb. Когда
    соседей меньше ndmin, и оценка, и дисперсия = nodata."""
    dx = xd - xloc
    dy = yd - yloc
    h2 = dx * dx + dy * dy
    # Полная сортировка не нужна: берётся ndmax ближайших. argpartition
    # раскладывает за линейное время, сортируется потом только отобранная
    # горстка. На густой сети это заметно: при десяти тысячах замеров
    # сортировка всей выборки для каждой ячейки съедала больше половины
    # времени.
    # Ничья на границе отбора: на регулярной сети профилей два пикета
    # часто стоят ровно на одинаковом расстоянии от узла, а место в
    # выборке одно. Кого взять, решал порядок перебора, и он разный при
    # разных путях отбора - отсюда расхождение оценки на сотую долю
    # процента ячеек.
    #
    # Правило одно для обоих путей: при равных расстояниях берётся замер
    # с меньшим ГЛОБАЛЬНЫМ номером. Решать это надо ДО усечения до
    # ndmax, а не после: ничья и происходит на самой границе.
    if h2.size > ndmax:
        # Полная сортировка не нужна: argpartition раскладывает за
        # линейное время. Но граница отбора может рассекать группу
        # равных расстояний, поэтому ничья решается отдельно и только
        # среди тех, кто стоит ровно на пороговом расстоянии.
        cut = np.argpartition(h2, ndmax)[:ndmax]
        thr = float(h2[cut].max())
        tie = np.nonzero(h2 == thr)[0]
        if tie.size > 1:
            keep = cut[h2[cut] < thr]
            need = ndmax - keep.size
            num = tie if gid is None else gid[tie]
            tie = tie[np.argsort(num, kind="stable")][:need]
            cut = np.concatenate([keep, tie])
        order = cut[np.argsort(h2[cut], kind="stable")]
    else:
        order = np.argsort(h2, kind="stable")
    sel = order[h2[order] <= rad2][:ndmax]
    # Для кэша набор упорядочивается по индексу пробы: порядок по
    # расстоянию у каждой ячейки свой и ключом быть не может. Значения
    # берутся в том же порядке, поэтому оценка не меняется - меняется
    # только нумерация уравнений внутри системы.
    if cache is not None and len(sel):
        sel = np.sort(sel)
    na = len(sel)
    if na < ndmin:
        return nodata, nodata

    xa = xd[sel]
    ya = yd[sel]
    vra = vrd[sel]
    h2sel = h2[sel]
    c0pt = vg.maxcov                         # точечная C(0): диагональ данных
    block = bdx is not None and len(bdx) > 1
    if cbb is None:                          # дисперсионный член
        cbb = c0pt                           # точка по умолчанию

    # точечный кригинг: совпадение узла с пробой -> значение пробы, дисперсия 0
    # Узел совпал с пробой - возвращаем её значение. Ищется МИНИМУМ, а не
    # первый элемент: при включённом кэше набор отсортирован по индексу
    # пробы, и ближайшая уже не стоит первой. Раньше проверка смотрела
    # h2sel[0] и после сортировки пропускала совпадение, отчего у самых
    # проб оценка уезжала на десятки единиц.
    if not block:
        imin = int(np.argmin(h2sel))
        if h2sel[imin] < EPS:
            return float(vra[imin]), 0.0

    # правые ковариации: точка-точка либо среднее точка-блок
    if block:
        bx = xloc + bdx
        by = yloc + bdy
        ddx = xa[:, None] - bx[None, :]
        ddy = ya[:, None] - by[None, :]
        cb_arr = vg.cova2_array(ddx, ddy)
        coin = (ddx * ddx + ddy * ddy) < EPS
        if np.any(coin):                     # проба на узле блока: снять наггет
            cb_arr = np.where(coin, cb_arr - vg.c0, cb_arr)
        rhs = cb_arr.mean(axis=1)
    else:
        rhs = vg.cova2_array(xa - xloc, ya - yloc)

    if na == 1:
        cb = float(rhs[0])
        if ktype == 0:                       # simple
            s = cb / c0pt
            return s * vra[0] + (1.0 - s) * skmean, max(cbb - s * cb, 0.0)
        # ordinary, единственная проба: λ=1, μ=cb−C(0) -> σ²=Cbb−2cb+C(0)
        return float(vra[0]), max(cbb - 2.0 * cb + c0pt, 0.0)

    # допустимый разброс оценки (защита от «разлёта» весов)
    vmin = float(vra.min()); vmax = float(vra.max())
    span = (vmax - vmin) or (abs(vmax) + 1.0)
    lo, hi = vmin - 3.0 * span, vmax + 3.0 * span
    jitter = c0pt * 1e-9                       # микро-регуляризация диагонали

    neq = na + ktype                         # +1 row for OK unbiasedness
    # При попадании в кэш матрица не нужна вовсе: она уже обращена.
    # Строить её и потом выбрасывать значило бы отдать назад весь
    # выигрыш - сборка ковариаций дороже самого решения.
    cached_inv = None
    key = None
    if cache is not None:
        # Ключ строится из ГЛОБАЛЬНЫХ номеров проб. Локальные индексы
        # годятся, только пока в решатель идёт весь набор: с разломами
        # туда попадают лишь видимые замеры, и пятый видимый у разных
        # ячеек - разная проба. Один ключ отвечал бы разным наборам, и
        # матрица бралась бы чужая.
        key = (sel if gid is None else gid[sel]).tobytes() + vg_key
        cached_inv = cache.get(key)
    A = None if cached_inv is not None else np.empty((neq, neq))
    r = np.empty(neq)
    # Матрица ковариаций строится разом, а не двойным циклом с вызовом
    # cova2 на каждую пару. При двадцати четырёх соседях это было 276
    # питоновских вызовов на КАЖДУЮ ячейку сетки, и именно они, а не
    # решение системы, съедали время: замер показывал полмиллисекунды на
    # ячейку и почти полную независимость от числа замеров.
    if A is not None:
        A[:na, :na] = vg.cova2_array(xa[:, None] - xa[None, :],
                                     ya[:, None] - ya[None, :])
        np.fill_diagonal(A[:na, :na], c0pt + jitter)
    r[:na] = rhs
    if ktype == 1:                           # ordinary kriging
        if A is not None:
            A[na, :na] = vg.maxcov
            A[:na, na] = vg.maxcov
            A[na, na] = 0.0
        r[na] = vg.maxcov

    est = None
    var = None
    try:
        if cache is None:
            s = np.linalg.solve(A, r)
        else:
            # Обращение вместо решения: считается один раз на набор, а
            # дальше каждая ячейка стоит одно умножение.
            if cached_inv is None:
                cached_inv = np.linalg.inv(A)
                cache.put(key, cached_inv)
            s = cached_inv @ r
        if np.all(np.isfinite(s)):
            w = s[:na]
            e = float(np.dot(w, vra))
            if ktype == 0:                   # simple kriging mean term
                e += (1.0 - w.sum()) * skmean
            v = cbb - float(np.dot(w, r[:na]))
            if ktype == 1:                   # минус множитель Лагранжа
                v -= vg.maxcov * float(s[na])   # (масштабирован на C(0))
            if math.isfinite(e) and lo <= e <= hi:
                est = e                      # принимаем только разумную оценку
                var = max(v, 0.0)
    except np.linalg.LinAlgError:
        pass

    if est is None:                          # запас: обратные расстояния соседей
        wts = 1.0 / np.maximum(h2sel, EPS)
        est = float(np.dot(wts, vra) / wts.sum())
        var = cbb                            # априорная (максимальная) дисперсия
    return est, var


def krige_point(xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                ndmin, ndmax, rad2, nodata, return_var=False):
    """Точечный кригинг (обёртка). По умолчанию возвращает только оценку;
    при return_var=True - кортеж (оценка, дисперсия)."""
    est, var = _solve_point(xloc, yloc, xd, yd, vrd, vg, ktype, skmean,
                            ndmin, ndmax, rad2, nodata)
    return (est, var) if return_var else est


def cross_validate(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
                   rad2, nodata, progress=None):
    """Скользящий контроль (leave-one-out). Для каждой точки оценка строится
    по всем ОСТАЛЬНЫМ точкам, затем сравнивается с фактическим значением.
    Возвращает (est, var) той же длины, что и входные данные (nodata там, где
    оценка не получена). Это основа подбора вариограммы по ошибке."""
    n = len(xd)
    est = np.full(n, nodata, float)
    var = np.full(n, nodata, float)
    keep = np.ones(n, bool)
    for i in range(n):
        keep[i] = False                      # исключаем саму точку
        e, v = _solve_point(xd[i], yd[i], xd[keep], yd[keep], vrd[keep],
                            vg, ktype, skmean, ndmin, ndmax, rad2, nodata)
        keep[i] = True
        est[i] = e
        var[i] = v
        if progress is not None and (i & 255) == 0:
            progress(i, n)
    return est, var


def cross_validate_detrend(xd, yd, vrd, degree, vg, ktype, skmean,
                           ndmin, ndmax, rad2, nodata, progress=None):
    """Скользящий контроль для регрессии-кригинга. На каждом шаге исключённая
    точка не участвует в подборе тренда: тренд переподбирается по n-1 точкам,
    кригуются их остатки, тренд добавляется к оценке обратно. Так LOO остаётся
    добросовестным, без утечки исключённой точки ни в тренд, ни в кригинг.

    Вариограмма vg задаётся по остаткам (фиксирована). Возвращает (est, var) в
    исходных единицах значения: var - дисперсия ошибки кригинга остатков, тренд
    детерминирован и своей погрешности не добавляет. PolyTrend берётся при
    вызове (определён ниже в модуле)."""
    n = len(xd)
    est = np.full(n, nodata, float)
    var = np.full(n, nodata, float)
    keep = np.ones(n, bool)
    for i in range(n):
        keep[i] = False
        xi, yi, zi = xd[keep], yd[keep], vrd[keep]
        tr = PolyTrend.fit(xi, yi, zi, degree)
        ri = zi - tr(xi, yi)
        e, v = _solve_point(xd[i], yd[i], xi, yi, ri, vg, ktype, skmean,
                            ndmin, ndmax, rad2, nodata)
        keep[i] = True
        if e != nodata:
            est[i] = float(tr(xd[i:i + 1], yd[i:i + 1])[0]) + e
            var[i] = v
        if progress is not None and (i & 255) == 0:
            progress(i, n)
    return est, var


def axial_mean(angles, weights=None):
    """Среднее простирание по осевым углам, в градусах от севера.

    Простирание это ОСЬ, а не направление: период 180 градусов, и 170
    отличается от 10 на двадцать градусов, а не на сто шестьдесят.
    Обычное среднее здесь даёт перпендикуляр к истине, и ошибка тихая:
    карта вытягивается поперёк структуры и выглядит закономерной.

    Считается через удвоенный угол: складываются единичные векторы 2a, а
    результат делится пополам. Веса, если заданы, дают более уверенным
    точкам больший вклад.

    Возвращает (азимут в [0, 180), сила вытянутости в [0, 1]). Сила это
    длина среднего вектора: близко к единице - направления согласны,
    близко к нулю - разнобой, и назначать направление незачем.
    """
    a = np.asarray(angles, dtype=np.float64)
    if a.size == 0:
        return 0.0, 0.0
    w = np.ones_like(a) if weights is None else np.asarray(weights, float)
    w = np.where(np.isfinite(w) & (w > 0), w, 0.0)
    if not w.any():
        return 0.0, 0.0
    r = np.radians(a) * 2.0
    cs = float(np.sum(w * np.cos(r)))
    sn = float(np.sum(w * np.sin(r)))
    tot = float(np.sum(w))
    strength = math.hypot(cs, sn) / tot if tot > 0 else 0.0
    mean = math.degrees(math.atan2(sn, cs)) / 2.0
    return mean % 180.0, strength


class BinGrid:
    """Сетка бинов для отбора соседей на густых данных.

    Без неё расстояние считается до КАЖДОГО замера на каждую ячейку, а
    потом argpartition перебирает весь массив. При радиусе поиска в 3 км
    и ста шестидесяти тысячах пикетов в радиус попадает пять процентов
    точек, остальные девяносто пять перебираются впустую: замер показал
    1200 микросекунд на ячейку против 90 на редкой сети.

    Бин равен радиусу поиска, поэтому кандидаты всегда лежат в девяти
    соседних бинах: дальше радиуса точка всё равно отсеется. Сетка
    строится один раз за прогон - сотая доля секунды против сотни секунд
    расчёта.

    Отбор ТОЧНЫЙ: бины лишь сокращают перебор, а окончательное решение
    принимает то же сравнение с радиусом, что и раньше. Оценка не
    меняется ни в одной ячейке.
    """

    __slots__ = ("x0", "y0", "size", "nbx", "nby", "order", "starts", "ends")

    def __init__(self, xd, yd, size):
        self.size = float(size)
        self.x0 = float(np.min(xd))
        self.y0 = float(np.min(yd))
        ix = ((xd - self.x0) // self.size).astype(np.int64)
        iy = ((yd - self.y0) // self.size).astype(np.int64)
        self.nbx = int(ix.max()) + 1
        self.nby = int(iy.max()) + 1
        key = iy * self.nbx + ix
        self.order = np.argsort(key, kind="stable")
        skey = key[self.order]
        cells = np.arange(self.nbx * self.nby)
        self.starts = np.searchsorted(skey, cells)
        self.ends = np.searchsorted(skey, cells, side="right")

    def candidates(self, xloc, yloc):
        """Индексы замеров в девяти бинах вокруг точки."""
        bx = int((xloc - self.x0) // self.size)
        by = int((yloc - self.y0) // self.size)
        parts = []
        for jy in range(max(by - 1, 0), min(by + 2, self.nby)):
            base = jy * self.nbx
            lo = max(bx - 1, 0)
            hi = min(bx + 2, self.nbx)
            if lo >= hi:
                continue
            # Бины одной строки лежат в отсортированном ключе подряд,
            # поэтому берётся один срез на строку, а не три.
            s = self.starts[base + lo]
            e = self.ends[base + hi - 1]
            if e > s:
                parts.append(self.order[s:e])
        if not parts:
            return np.empty(0, dtype=np.int64)
        return parts[0] if len(parts) == 1 else np.concatenate(parts)


def build_grid(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
               rad2, nodata, xmn, ymn, cell, nx, ny, progress=None,
               with_variance=False, ndisc=1, fault_segs=None,
               cell_mask=None, azi=None, azi_min_strength=0.3,
               use_cache=True, stats=None):
    """Sweep the grid, GSLIB order (north row first). Returns float32 (ny,nx).

    При with_variance=True возвращает кортеж (оценка, стд.ошибка), где второй
    грид - стандартная ошибка кригинга = sqrt(дисперсия) (nodata там же, где
    nodata у оценки).

    ndisc - дискретизация блока N×N на ячейку: 1 (по умолчанию) - точечный
    кригинг, >1 - блочный (оценка среднего по ячейке, дисперсия блочная). Блок
    равен ячейке грида; смещения дискретизации и блок-блок ковариация считаются
    один раз до прохода (вариограмма стационарна).

    fault_segs - звенья разломов массивом (m, 4) из fault_segments. Замер,
    отрезок до которого пересекает разлом, в выборку ячейки не попадает, и
    поверхность вдоль линии рвётся. У затухающего разлома влияние огибает
    его конец само: точки за концом остаются видимыми.

    Разлом проверяется точной геометрией, а не растровой маской. Маска
    делала дырку в гриде: луч начинается в центре оцениваемой ячейки, и
    ячейка на самой линии не видела ни одного замера.

    Приближение названо прямо: по видимости отбираются только соседи, а
    ковариации между самими замерами остаются евклидовыми. Занулять их
    нельзя, система потеряет положительную определённость."""
    # Кэш обращённых матриц: один на прогон. Отключается при блочном
    # кригинге - там правая часть усредняется по точкам блока, но матрица
    # та же, так что кэш работает и там; отключать нечего.
    cache = InverseCache() if use_cache else None
    # Сетка бинов оправдана только на густых данных с ограниченным
    # радиусом: на редкой сети перебор и так дёшев, а лишний слой съел бы
    # выигрыш. Порог выбран по замеру - ниже тысячи точек разницы нет.
    bins = None
    if rad2 > 0 and len(xd) >= 1000:
        try:
            bins = BinGrid(xd, yd, math.sqrt(rad2))
        except Exception:  # nosec - вырожденные данные: работаем как раньше
            bins = None
    grid = np.full((ny, nx), nodata, dtype=np.float32)
    sgrid = np.full((ny, nx), nodata, dtype=np.float32) if with_variance else None
    bdx = bdy = None
    cbb = None
    if ndisc and int(ndisc) > 1:
        bdx, bdy = block_offsets(cell, int(ndisc), int(ndisc))
        cbb = block_block_cov(vg, bdx, bdy)
    total = nx * ny
    done = 0
    for row in range(ny):                    # row 0 = north
        iy = ny - row                        # 1..ny counted from south
        yloc = ymn + (iy - 1) * cell
        for ix in range(nx):
            # Ячейка вне маски обрезки всё равно уйдёт в nodata, считать
            # её незачем. На вытянутой по диагонали площади выпуклая
            # оболочка занимает восьмую часть своей рамки, и без этой
            # проверки девять десятых работы делались впустую.
            if cell_mask is not None and not cell_mask[row, ix]:
                continue          # массивы уже заполнены nodata
            xloc = xmn + ix * cell
            xs, ys, vs = xd, yd, vrd
            gid = None                # индексы уже глобальные
            azs = azi if azi is not None else ()
            if bins is not None:
                # Сетка бинов: кандидаты вместо всей выборки. Отбор
                # остаётся точным - радиус проверяется дальше, как и
                # прежде, бины лишь сокращают перебор.
                cand = bins.candidates(xloc, yloc)
                if cand.size < ndmin:
                    done += 1
                    continue
                xs, ys, vs = xd[cand], yd[cand], vrd[cand]
                gid = cand
                if azi is not None:
                    azs = azi[cand]
            if fault_segs is not None and len(fault_segs):
                # Замер за разломом в выборку не идёт. Сперва отсев по
                # радиусу, и только потом видимость: проверять все точки
                # площади для каждой ячейки незачем.
                near = np.nonzero((xs - xloc) ** 2 + (ys - yloc) ** 2
                                  <= rad2)[0] if rad2 > 0 else \
                    np.arange(len(xs))
                idx = near[visible_mask(xloc, yloc, xs[near], ys[near],
                                        fault_segs)]
                if idx.size < ndmin:
                    grid[row, ix] = nodata
                    done += 1
                    continue
                # Индексы локальны внутри xs: если до этого работала
                # сетка бинов, глобальный номер берётся через gid.
                gid = idx if gid is None else gid[idx]
                xs, ys, vs = xs[idx], ys[idx], vs[idx]
                if azi is not None:
                    azs = azs[idx] if len(azs) else azs
            # Локальная анизотропия: направление берётся по замерам
            # окрестности, и вся система решается по ОДНОЙ модели. Разные
            # модели для разных пар сделали бы матрицу не положительно
            # определённой, и решение развалилось бы.
            vg_here = vg
            vg_key = b""
            if azi is not None and len(azs):
                if rad2 > 0:
                    sel = np.nonzero((xs - xloc) ** 2 + (ys - yloc) ** 2
                                     <= rad2)[0]
                else:
                    sel = np.arange(len(xs))
                if sel.size:
                    # Вес по значению: точка с высоким содержанием весит
                    # больше, и порог класса не нужен вовсе. Порог сдвинут -
                    # сдвинулось бы и направление, а это произвол.
                    a_loc, strength = axial_mean(azs[sel])
                    if strength >= azi_min_strength:
                        vg_here = vg.rotated(a_loc)
                        # Азимут в ключ. Без разломов он следует из
                        # набора соседей, а с разломами - нет: у двух
                        # ячеек набор после отбора по расстоянию может
                        # совпасть при разной видимости, и азимут тогда
                        # разный. Восемь байт на запись дешевле, чем
                        # полагаться на такое совпадение.
                        vg_key = np.float64(round(a_loc, 3)).tobytes()
            e, v = _solve_point(
                xloc, yloc, xs, ys, vs, vg_here, ktype, skmean,
                ndmin, ndmax, rad2, nodata, bdx=bdx, bdy=bdy, cbb=cbb,
                cache=cache, gid=gid, vg_key=vg_key)
            grid[row, ix] = e
            if with_variance and e != nodata:
                sgrid[row, ix] = math.sqrt(max(v, 0.0))
            done += 1
        if progress is not None:
            progress(done, total)
    if cache is not None and cache.hits and stats is not None:
        stats["cache_rate"] = cache.rate()
        stats["cache_mb"] = cache.spent / 1e6
        stats["cache_full"] = cache.full
    return (grid, sgrid) if with_variance else grid


# ===========================================================================
#  Экспериментальная вариограмма и подбор модели (чистый NumPy, без QGIS).
#  Параметризация моделей совпадает с cova2 выше: радиус a здесь - это тот же
#  параметр aa структуры, поэтому подобранный a подставляется в кригинг 1:1.
# ===========================================================================
def variogram_shape(model, h, a):
    """Форма вариограммы γ/C (от 0 до 1) для одной структуры, без наггета.

    model: 0 сферическая, 1 экспоненциальная, 2 гауссова (как MODEL_*).
    Совпадает с cova2: для сферической при h>=a даёт 1; для экспоненциальной
    1-exp(-h/a); для гауссовой 1-exp(-(h/a)^2). Параметр a = aa структуры.
    """
    h = np.asarray(h, float)
    a = max(float(a), EPS)
    hr = h / a
    if model == MODEL_SPHERICAL:
        s = 1.5 * hr - 0.5 * hr ** 3
        return np.where(hr < 1.0, s, 1.0)
    if model == MODEL_EXPONENTIAL:
        return 1.0 - np.exp(-hr)
    if model == MODEL_GAUSSIAN:
        return 1.0 - np.exp(-(hr * hr))
    raise ValueError("fit поддерживает только модели 0/1/2 (sph/exp/gauss)")


def experimental_variogram(xs, ys, vs, n_lags=15, maxlag=None, robust=False,
                           max_pairs=6_000_000, cloud_max=20000, seed=0,
                           wts=None):
    """Омнинаправленная экспериментальная полувариограмма (оценка Матерона).

    Делит расстояния между всеми парами на n_lags интервалов до maxlag и
    усредняет полудисперсию 0.5*(z_i - z_j)^2 по каждому интервалу.
      maxlag=None/<=0 -> половина диагонали охвата (классический ориентир);
      robust=True     -> устойчивая оценка Кресси-Хокинса (гасит выбросы);
      max_pairs       -> при превышении точки прореживаются (пар ~ n^2/2),
                         чтобы расчёт оставался быстрым;
      cloud_max       -> сколько пар вернуть для облака рассеяния (предпросмотр).
    Возвращает dict: lag, gamma, npairs (по непустым шагам), maxlag, width,
    n_used, subsampled, cloud_h, cloud_g (полудисперсия пары).
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = len(xs)
    w = None if wts is None else np.asarray(wts, float)
    rng = np.random.default_rng(seed)
    subsampled = False
    if n > 2 and n * (n - 1) // 2 > max_pairs:
        m = int((1.0 + math.sqrt(1.0 + 8.0 * max_pairs)) / 2.0)
        m = max(2, min(n, m))
        idx = rng.choice(n, m, replace=False)
        xs, ys, vs = xs[idx], ys[idx], vs[idx]
        if w is not None:
            w = w[idx]
        n = m
        subsampled = True

    if maxlag is None or maxlag <= 0:
        dx = float(xs.max() - xs.min())
        dy = float(ys.max() - ys.min())
        maxlag = 0.5 * math.hypot(dx, dy)
    if maxlag <= 0:
        maxlag = 1.0
    n_lags = max(int(n_lags), 1)
    width = maxlag / n_lags

    cnt = np.zeros(n_lags)
    sw = np.zeros(n_lags)
    s_h = np.zeros(n_lags)
    s_d2 = np.zeros(n_lags)
    s_sqrt = np.zeros(n_lags)
    cloud_h, cloud_g = [], []
    budget = int(cloud_max)

    for i in range(n - 1):
        hx = xs[i + 1:] - xs[i]
        hy = ys[i + 1:] - ys[i]
        h = np.sqrt(hx * hx + hy * hy)
        d = vs[i + 1:] - vs[i]
        sel = (h > 0.0) & (h <= maxlag)
        if not sel.any():
            continue
        h = h[sel]
        d = d[sel]
        b = np.minimum((h / width).astype(np.intp), n_lags - 1)
        wp = (w[i] * w[i + 1:][sel]) if w is not None else np.ones(len(h))
        np.add.at(cnt, b, 1.0)
        np.add.at(sw, b, wp)
        np.add.at(s_h, b, wp * h)
        np.add.at(s_d2, b, wp * d * d)
        if robust:
            np.add.at(s_sqrt, b, wp * np.sqrt(np.abs(d)))
        if budget > 0:
            k = len(h)
            take = min(k, budget)
            if take < k:
                pick = rng.choice(k, take, replace=False)
                cloud_h.append(h[pick]); cloud_g.append(0.5 * d[pick] * d[pick])
            else:
                cloud_h.append(h); cloud_g.append(0.5 * d * d)
            budget -= take

    safe = np.maximum(sw, 1e-12)
    cntsafe = np.maximum(cnt, 1.0)
    lag = np.where(cnt > 0, s_h / safe, (np.arange(n_lags) + 0.5) * width)
    if robust:
        mean_sqrt = np.where(cnt > 0, s_sqrt / safe, 0.0)
        denom = 0.457 + 0.494 / cntsafe + 0.045 / (cntsafe * cntsafe)
        gamma = 0.5 * (mean_sqrt ** 4) / denom
    else:
        gamma = 0.5 * np.where(cnt > 0, s_d2 / safe, np.nan)

    valid = cnt > 0
    return {
        "lag": lag[valid],
        "gamma": gamma[valid],
        "npairs": cnt[valid].astype(int),
        "maxlag": float(maxlag),
        "width": float(width),
        "n_used": int(n),
        "subsampled": bool(subsampled),
        "cloud_h": (np.concatenate(cloud_h) if cloud_h else np.array([])),
        "cloud_g": (np.concatenate(cloud_g) if cloud_g else np.array([])),
    }


def _wls_c0_c(s, y, w):
    """Взвешенный МНК для γ = c0 + C*s при фикс. форме s, c0>=0, C>=0.
    Возвращает (c0, C, sse)."""
    sw = float(np.sum(w))
    ss = float(np.sum(w * s))
    sss = float(np.sum(w * s * s))
    sy = float(np.sum(w * y))
    ssy = float(np.sum(w * s * y))
    det = sw * sss - ss * ss
    c0 = c = 0.0
    if abs(det) > 1e-30:
        c0 = (sy * sss - ss * ssy) / det
        c = (sw * ssy - ss * sy) / det
    if c0 < 0.0 or c < 0.0 or abs(det) <= 1e-30:
        # граница допустимой области: перебираем варианты с занулением
        cands = []
        # c0=0 -> C по МНК
        cc = ssy / sss if sss > 1e-30 else 0.0
        cands.append((0.0, max(cc, 0.0)))
        # C=0 -> c0 = взвешенное среднее
        cands.append((sy / sw if sw > 0 else 0.0, 0.0))
        # оба >=0 (если вышло)
        if c0 >= 0.0 and c >= 0.0:
            cands.append((c0, c))
        best = None
        for a0, a1 in cands:
            r = y - a0 - a1 * s
            e = float(np.sum(w * r * r))
            if best is None or e < best[2]:
                best = (a0, a1, e)
        return best
    r = y - c0 - c * s
    return c0, c, float(np.sum(w * r * r))


def fit_variogram(lag, gamma, npairs, model="auto", n_a=80, sill_cap=None):
    """Подбор модели вариограммы по экспериментальным точкам (рекомендация).

    γ(h) = c0 + C*shape(h; a). При фиксированном a задача линейна по (c0, C),
    поэтому a сканируется по сетке (лог-шаг) с локальным уточнением, а (c0, C)
    на каждом шаге решаются взвешенным МНК (веса = число пар в шаге, так точнее
    оценённые шаги весомее). Без scipy/skgstat.

    Плато модели c0+C ограничивается сверху (sill_cap), чтобы модель не
    «убегала» в несуществующий силл за счёт длинного радиуса и экстраполяции
    выше наблюдаемой вариограммы. По умолчанию cap = 1.15*max(γ). Благодаря
    этому auto не выбирает гауссову там, где она выигрывает лишь экстраполяцией.

    model: 'auto' (выбрать sph/exp/gauss по лучшему R^2) либо 0/1/2.
    Возвращает dict: model, nugget, sill, range, r2, npts. None при нехватке точек.
    """
    lag = np.asarray(lag, float)
    gamma = np.asarray(gamma, float)
    w = np.asarray(npairs, float)
    ok = np.isfinite(lag) & np.isfinite(gamma) & (lag > 0)
    lag, gamma, w = lag[ok], gamma[ok], np.maximum(w[ok], 1.0)
    if len(lag) < 3:
        return None
    if sill_cap is None:
        sill_cap = 1.15 * float(np.max(gamma))

    L = float(lag.max())
    a_lo = max(0.05 * L, float(lag.min()) * 0.5, EPS)
    a_hi = 1.5 * L
    grid = np.geomspace(a_lo, a_hi, max(int(n_a), 8))
    models = [MODEL_SPHERICAL, MODEL_EXPONENTIAL, MODEL_GAUSSIAN] \
        if model == "auto" else [int(model)]

    sw = float(np.sum(w))
    ybar = float(np.sum(w * gamma) / sw)
    sst = float(np.sum(w * (gamma - ybar) ** 2)) or 1.0

    def _eval(m, a):
        s = variogram_shape(m, lag, a)
        c0, c, sse = _wls_c0_c(s, gamma, w)
        total = c0 + c
        if sill_cap and total > sill_cap and total > 0:
            f = sill_cap / total                 # прижать плато к cap
            c0, c = c0 * f, c * f
            r = gamma - c0 - c * s
            sse = float(np.sum(w * r * r))
        return c0, c, sse

    best = None
    for m in models:
        coarse = [(a, _eval(m, a)) for a in grid]
        a_star, (c0, c, sse) = min(coarse, key=lambda t: t[1][2])
        j = [i for i, (a, _) in enumerate(coarse) if a == a_star][0]
        alo = coarse[max(j - 1, 0)][0]
        ahi = coarse[min(j + 1, len(coarse) - 1)][0]
        for _ in range(40):
            a1 = alo * (ahi / alo) ** (1.0 / 3.0)
            a2 = alo * (ahi / alo) ** (2.0 / 3.0)
            if _eval(m, a1)[2] < _eval(m, a2)[2]:
                ahi = a2
            else:
                alo = a1
            if ahi / alo < 1.0001:
                break
        a_ref = math.sqrt(alo * ahi)
        c0, c, sse = _eval(m, a_ref)
        r2 = 1.0 - sse / sst
        cand = {"model": m, "nugget": float(max(c0, 0.0)),
                "sill": float(max(c, 0.0)), "range": float(a_ref),
                "r2": float(r2), "npts": int(len(lag))}
        if best is None or cand["r2"] > best["r2"]:
            best = cand
    return best


def model_curve(vg, hmax, ndir=None, npts=120):
    """Кривая γ(h) заданной модели для наложения на экспериментальную.

    Возвращает (h, gamma_major[, gamma_minor]). По умолчанию вдоль главной оси
    первой структуры (азимут структуры 1); если анизотропия != 1, второй
    кривой добавляется малая ось - так видно обе ветви. Изотропная модель даёт
    одну кривую (ветви совпадают, малая не возвращается).
    """
    h = np.linspace(0.0, float(hmax), int(npts))
    az = vg.ang[0] if vg.nst else 0.0
    ar = az * DTOR
    ux, uy = math.sin(ar), math.cos(ar)          # главная ось (азимут от севера)
    g_major = np.array([vg.maxcov - vg.cova2(hi * ux, hi * uy) for hi in h])
    anis_min = min(vg.anis) if vg.nst else 1.0
    if anis_min < 0.999:
        vx, vy = math.cos(ar), -math.sin(ar)     # перпендикуляр (малая ось)
        g_minor = np.array([vg.maxcov - vg.cova2(hi * vx, hi * vy) for hi in h])
        return h, g_major, g_minor
    return h, g_major


# ===========================================================================
#  Вариограммная карта (поверхность) и оценка анизотропии
# ===========================================================================
def _vmap_bilinear(grid, cell, n_bins, hx, hy):
    """Билинейная выборка карты в точке расстояния (hx, hy). NaN вне диапазона.
    Если часть углов пуста - среднее по валидным углам."""
    size = grid.shape[0]
    fx = hx / cell + n_bins
    fy = hy / cell + n_bins
    x0 = int(math.floor(fx))
    y0 = int(math.floor(fy))
    if x0 < 0 or x0 + 1 >= size or y0 < 0 or y0 + 1 >= size:
        return float("nan")
    tx, ty = fx - x0, fy - y0
    v00, v10 = grid[y0, x0], grid[y0, x0 + 1]
    v01, v11 = grid[y0 + 1, x0], grid[y0 + 1, x0 + 1]
    corners = [v00, v10, v01, v11]
    if not all(np.isfinite(c) for c in corners):
        valid = [c for c in corners if np.isfinite(c)]
        return float(np.mean(valid)) if valid else float("nan")
    a = v00 * (1 - tx) + v10 * tx
    b = v01 * (1 - tx) + v11 * tx
    return float(a * (1 - ty) + b * ty)


def _estimate_anisotropy(grid, cell, n_bins, sill, maxlag, n_az=36,
                         reach=0.95):
    """Главная ось и коэффициент анизотропии по направленным радиусам.
    Азимут геогр. (0=С, по часовой); направление (E=x, N=y) = (sin A, cos A).
    Радиус - расстояние, где γ впервые достигает reach*sill (иначе maxlag). Радиусы
    сглаживаются по азимуту (период 180°), главная ось = макс. сглаженного
    радиуса, малая ось берётся ПЕРПЕНДИКУЛЯРНО главной (устойчивее минимума).
    Возвращает ..., capped: главная ось упёрлась в окно (γ не вышла на полку)."""
    if not (sill > 0):
        return 0.0, 1.0, maxlag, maxlag, [], False, False
    target = reach * sill
    step = max(cell * 0.5, maxlag / 200.0)
    n_az = int(n_az)
    raw = []
    reached = []                                 # дошла ли γ до полки в окне
    for k in range(n_az):
        a_deg = 180.0 * k / n_az
        a = math.radians(a_deg)
        ux, uy = math.sin(a), math.cos(a)
        rng_found = maxlag
        hit = False
        r = step
        while r <= maxlag:
            g = _vmap_bilinear(grid, cell, n_bins, r * ux, r * uy)
            if np.isfinite(g) and g >= target:
                rng_found = r
                hit = True
                break
            r += step
        raw.append(rng_found)
        reached.append(hit)
    raw = np.asarray(raw, float)
    # циклическое сглаживание (период n_az), окно ~ n_az/12
    w = max(1, n_az // 12)
    sm = np.array([raw[(np.arange(k - w, k + w + 1)) % n_az].mean()
                   for k in range(n_az)])
    k_major = int(np.argmax(sm))
    r_major = float(sm[k_major])
    k_minor = (k_major + n_az // 2) % n_az      # перпендикуляр
    r_minor = float(sm[k_minor])
    az_major = 180.0 * k_major / n_az
    ranges = [(180.0 * k / n_az, float(raw[k])) for k in range(n_az)]
    # структура разрешима, только если главный радиус заметно больше ячейки;
    # иначе радиусы «на уровне сетки» и анизотропия недостоверна (шум/нет
    # структуры) - корректнее сообщить «изотропно / не определено».
    resolved = r_major >= 3.0 * cell
    if not resolved:
        return 0.0, 1.0, r_major, r_major, ranges, False, False
    # радиус упёрся в край окна, если вдоль главной оси γ до полки не дошла:
    # порог получен экстраполяцией, радиус - нижняя оценка, анизотропия по
    # выраженности занижена (истинный r_major может быть больше окна).
    capped = not bool(reached[k_major])
    anis = (r_minor / r_major) if r_major > 0 else 1.0
    return (float(az_major), float(min(max(anis, 1e-3), 1.0)),
            r_major, r_minor, ranges, True, capped)


def variogram_map(xs, ys, vs, n_bins=15, maxlag=None, min_pairs=5,
                  max_pairs=4_000_000, seed=0, n_az=36, wts=None):
    """Вариограммная карта γ(h_x, h_y) и оценка анизотропии.

    Для всех пар берётся вектор разноса (dx, dy) и полудисперсия
    0.5*(z_i - z_j)^2; усредняется по двумерной сетке расстояний в [-maxlag; maxlag].
    Карта симметрична (учитываем пару и зеркало). wts - веса декластеризации:
    пара берётся с весом w_i*w_j. Возвращает dict: grid
    (квадрат 2*n_bins+1, NaN в пустых), counts, cell, extent (=maxlag), sill,
    azimuth (геогр.), anis (0..1), range_major/minor, range_capped (радиус
    упёрся в окно), n_used, subsampled."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = len(xs)
    w = None if wts is None else np.asarray(wts, float)
    rng = np.random.default_rng(seed)
    subsampled = False
    if n > 2 and n * (n - 1) // 2 > max_pairs:
        m = int((1.0 + math.sqrt(1.0 + 8.0 * max_pairs)) / 2.0)
        m = max(2, min(n, m))
        idx = rng.choice(n, m, replace=False)
        xs, ys, vs = xs[idx], ys[idx], vs[idx]
        if w is not None:
            w = w[idx]
        n = m
        subsampled = True

    if maxlag is None or maxlag <= 0:
        dx = float(xs.max() - xs.min())
        dy = float(ys.max() - ys.min())
        maxlag = 0.5 * math.hypot(dx, dy)
    if maxlag <= 0:
        maxlag = 1.0
    n_bins = max(int(n_bins), 1)
    cell = maxlag / n_bins
    size = 2 * n_bins + 1
    s_g = np.zeros((size, size))
    s_c = np.zeros((size, size))
    s_w = np.zeros((size, size))

    for i in range(n - 1):
        dx = xs[i + 1:] - xs[i]
        dy = ys[i + 1:] - ys[i]
        g = 0.5 * (vs[i + 1:] - vs[i]) ** 2
        wp = (w[i] * w[i + 1:]) if w is not None else np.ones(len(dx))
        for sx, sy in ((dx, dy), (-dx, -dy)):
            ix = np.round(sx / cell).astype(int) + n_bins
            iy = np.round(sy / cell).astype(int) + n_bins
            ok = (ix >= 0) & (ix < size) & (iy >= 0) & (iy < size)
            np.add.at(s_g, (iy[ok], ix[ok]), wp[ok] * g[ok])
            np.add.at(s_w, (iy[ok], ix[ok]), wp[ok])
            np.add.at(s_c, (iy[ok], ix[ok]), 1.0)

    grid = np.full((size, size), np.nan)
    mask = s_c >= float(min_pairs)
    grid[mask] = s_g[mask] / np.maximum(s_w[mask], 1e-12)
    grid[n_bins, n_bins] = 0.0

    sill = float(np.var(vs)) if n > 1 else 0.0
    az, anis, rmaj, rmin, dranges, resolved, capped = _estimate_anisotropy(
        grid, cell, n_bins, sill, maxlag, n_az)

    return {"grid": grid, "counts": s_c, "cell": cell, "extent": maxlag,
            "sill": sill, "azimuth": az, "anis": anis, "resolved": resolved,
            "range_major": rmaj, "range_minor": rmin, "dir_ranges": dranges,
            "range_capped": capped,
            "n_used": n, "subsampled": subsampled, "maxlag": maxlag,
            "n_bins": n_bins}


# ===========================================================================
#  Полиномиальный тренд (регрессия-кригинг)
# ===========================================================================
def data_warnings(xs, ys, vs, min_points=8):
    """Дешёвые проверки кондиционности входных точек перед кригингом. Возвращает
    список кодов с деталью: каждый элемент это (код, значение). Сообщения и их
    перевод формируются на стороне алгоритмов, здесь только обнаружение.

    Коды:
      "few_points" - точек меньше порога, оценка неустойчива (деталь n);
      "duplicates" - есть точки с совпадающими координатами, частая причина
                     вырожденной матрицы (деталь - число лишних совпадений);
      "constant"   - все значения одинаковы, кригинг вырождается (деталь None).
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = len(vs)
    out = []
    if n < int(min_points):
        out.append(("few_points", n))
    if n > 1:
        uniq = len(set(zip(xs.tolist(), ys.tolist())))
        dup = n - uniq
        if dup > 0:
            out.append(("duplicates", dup))
    if n > 0 and float(np.ptp(vs)) == 0.0:
        out.append(("constant", None))
    return out


def nugget_pairs(xs, ys, vs, maxdist, top=5, chunk=1024):
    """Пары точек, формирующие наггет: близкие по плану, далёкие по значению.

    Наггет это разброс на нулевом расстоянии, и в разреженной сети его почти
    всегда задают единицы пар. Две скважины в десятках метров с несопоставимыми
    значениями поднимают первый шаг выше силла, подбору остаётся описать это
    почти чистым наггетом, а кригинг по такой модели возвращает среднее.
    Функция называет виновников, чтобы можно было посмотреть в данные, а не
    гадать по форме кривой.

    Возвращает список кортежей (i, j, dist, vi, vj, gamma), где
    gamma = 0.5*(vi - vj)**2 - вклад пары в вариограмму. Список отсортирован по
    gamma по убыванию и не длиннее top, учитываются только пары ближе maxdist.
    Обход блоками: полная матрица расстояний не создаётся, размер блока
    подбирается так, чтобы промежуточные массивы оставались небольшими.
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    n = len(vs)
    if n < 2 or not (float(maxdist) > 0.0):
        return []
    md2 = float(maxdist) ** 2
    step = max(1, min(int(chunk), int(2000000 // max(n, 1)) or 1))
    keep = max(int(top) * 4, 20)
    best = []
    cols = np.arange(n)
    for i0 in range(0, n - 1, step):
        i1 = min(i0 + step, n - 1)
        rows = np.arange(i0, i1)
        dx = xs[i0:i1, None] - xs[None, :]
        dy = ys[i0:i1, None] - ys[None, :]
        d2 = dx * dx + dy * dy
        ok = (cols[None, :] > rows[:, None]) & (d2 <= md2)
        r, c = np.nonzero(ok)
        if not len(r):
            continue
        gi = rows[r]
        g = 0.5 * (vs[gi] - vs[c]) ** 2
        d = np.sqrt(d2[r, c])
        best.extend(zip(gi.tolist(), c.tolist(), d.tolist(),
                        vs[gi].tolist(), vs[c].tolist(), g.tolist()))
        best.sort(key=lambda t: -t[5])
        del best[keep:]
    return [tuple(t) for t in best[:int(top)]]


def _auto_indicator_variogram(xd, yd, ind, n_lags=15, maxlag=None):
    """Авто-вариограмма индикатора 0/1: сферическая модель по экспериментальной.
    Сферическая, потому что индикаторные вариограммы устойчивее и не уводят в
    численно капризную гауссову."""
    ev = experimental_variogram(xd, yd, ind, n_lags=n_lags, maxlag=maxlag,
                                robust=False)
    fit = fit_variogram(ev["lag"], ev["gamma"], ev["npairs"], model=0)
    if fit is None:
        p = float(np.mean(ind))
        sill = max(p * (1.0 - p), 1e-6)
        aa = max((ev.get("maxlag") or 1.0) * 0.25, EPS)
        return Variogram(0.0, [{"it": 1, "cc": sill, "aa": aa,
                                "ang": 0.0, "anis": 1.0}])
    return Variogram(fit["nugget"], [{"it": fit["model"] + 1,
            "cc": fit["sill"], "aa": fit["range"], "ang": 0.0, "anis": 1.0}])


def rescale_nugget(vg, frac):
    """Переставляет долю самородка, не трогая общую дисперсию и радиусы.

    frac - доля самородка в сумме c0 и структурных силлов, от 0 до 1. Ноль
    делает кригинг точным интерполятором: оценка в узле со скважиной равна
    самому замеру. Общая дисперсия сохраняется, поэтому меняется только
    гладкость поверхности, а не её масштаб.

    Возвращает новую вариограмму, исходная не трогается. При frac = None
    возвращается она же.
    """
    if frac is None:
        return vg
    frac = min(max(float(frac), 0.0), 1.0)
    total = vg.c0 + sum(vg.cc)
    if total <= 0.0:
        return vg
    c0 = total * frac
    rest = total - c0
    old = sum(vg.cc)
    scale = (rest / old) if old > 0.0 else 0.0
    structs = [{"it": it, "cc": cc * scale, "aa": aa, "ang": ang, "anis": an}
               for it, cc, aa, ang, an in
               zip(vg.it, vg.cc, vg.aa, vg.ang, vg.anis)]
    if not structs:
        structs = [{"it": 1, "cc": rest, "aa": EPS, "ang": 0.0, "anis": 1.0}]
    return Variogram(c0, structs)


def categorical_indicator_grids(xd, yd, labels, classes, xmn, ymn, cell, nx, ny,
                                ndmin=4, ndmax=24, radius=None, nodata=-9999.0,
                                models=None, progress=None, wts=None,
                                nugget_frac=None, on_model=None,
                                fault_segs=None):
    """Категориальный индикаторный кригинг.

    На каждый класс из classes строит индикатор 0/1, кригует простым
    кригингом (ktype=0) и обрезает оценку в [0, 1]. Затем вероятности по классам
    нормируются к сумме 1 в каждой ячейке. Кодом класса НЕ кригуем: у категорий
    нет порядка, поэтому только раздельные индикаторы.

    wts - веса декластеризации. Если заданы, средним простого кригинга для
    индикатора берётся декластеризованная доля класса (взвешенная), поэтому
    вдали от данных вероятность стремится к представительной доле, а не к нулю.
    Без весов поведение прежнее (среднее 0).

    Возвращает (probs, zone, conf):
      probs - float32 ny×nx×K, нормированные вероятности (nodata вне области),
      zone  - int32 ny×nx, индекс самого вероятного класса (-1 = нет оценки),
      conf  - float32 ny×nx, максимум нормированной вероятности (nodata вне).

    nugget_frac - доля самородка в подобранной вариограмме, от 0 до 1. None
    оставляет подбор как есть. Ноль делает кригинг точным в точках замеров:
    вероятность в узле со скважиной равна её собственному индикатору, а не
    сглаженному среднему по соседям. Общая дисперсия при переносе доли
    сохраняется, меняется только гладкость.

    on_model - необязательный обработчик (класс, доля самородка из подбора,
    доля после подстановки, радиус) для журнала.

    radius - радиус поиска (по умолчанию по размеру грида). models - список
    готовых Variogram по классам или None (тогда авто-подбор по индикатору)."""
    xd = np.asarray(xd, float)
    yd = np.asarray(yd, float)
    labels = np.asarray(labels, dtype=object)
    w = None if wts is None else np.asarray(wts, float)
    K = len(classes)
    if not radius or radius <= 0:
        radius = max(nx * cell, ny * cell)
    rad2 = float(radius) * float(radius)
    raw = np.empty((ny, nx, K), dtype=np.float32)
    pockets = 0
    for k, c in enumerate(classes):
        ind = (labels == c).astype(float)
        if w is not None:
            skmean_k = float(np.sum(w * ind) / np.sum(w))   # декласт. доля
        else:
            skmean_k = 0.0
        vg = (models[k] if (models and k < len(models) and models[k] is not None)
              else _auto_indicator_variogram(xd, yd, ind))
        tot = vg.c0 + sum(vg.cc)
        fitted = (vg.c0 / tot) if tot > 0 else 0.0
        vg = rescale_nugget(vg, nugget_frac)
        tot2 = vg.c0 + sum(vg.cc)
        if on_model is not None:
            on_model(c, fitted, (vg.c0 / tot2) if tot2 > 0 else 0.0,
                     max(vg.aa) if vg.aa else 0.0)
        prog = (lambda d, t, _k=k: progress(_k, K, d, t)) if progress else None
        g = build_grid(xd, yd, ind, vg, 0, skmean_k, ndmin, ndmax,
                       rad2, nodata, xmn, ymn, cell, nx, ny,
                       progress=prog, fault_segs=fault_segs)
        if fault_segs is not None and len(fault_segs):
            # Ячейка, запертая в складке разлома, не видит ни одного
            # замера. У индикатора это опаснее, чем у поверхности: пустая
            # полоса хотя бы одного класса выбивает ячейку из зоны
            # целиком, потому что зона требует оценки по всем классам.
            g, n_fill, _ = fill_pockets(g, nodata)
            pockets = max(pockets, n_fill)
        raw[:, :, k] = g
    valid = np.all(raw != nodata, axis=2)
    clipped = np.clip(raw, 0.0, 1.0)
    s = clipped.sum(axis=2, keepdims=True)
    s[s == 0] = 1.0
    probs = (clipped / s).astype(np.float32)
    am = np.argmax(probs, axis=2)
    zone = np.full((ny, nx), -1, dtype=np.int32)
    zone[valid] = am[valid].astype(np.int32)
    conf = np.where(valid, probs.max(axis=2), nodata).astype(np.float32)
    probs[~valid] = nodata
    return probs, zone, conf


class PolyTrend:
    """Полиномиальный тренд m(x, y) степени 1 или 2, подобранный МНК.

    Назначение - снять региональную составляющую перед кригингом. При наличии
    тренда (падение пласта, общий уклон поля) экспериментальная вариограмма
    сырого значения раздувается: радиус завышен, порога нет, форма как у
    степенной модели. Снятие тренда возвращает вариограмму остатков к виду с
    наггетом и порогом, кригинг идёт по остаткам, а тренд добавляется к оценке
    обратно:  оценка = m(x, y) + кригинг_остатков(x, y).

    Координаты центрируются и масштабируются на собственное стандартное
    отклонение, иначе матрица плана для степени 2 плохо обусловлена в метровых
    координатах. Чистый NumPy, как и всё ядро.

    Степень 1: m = b0 + b1*x + b2*y.
    Степень 2: m = b0 + b1*x + b2*y + b3*x^2 + b4*x*y + b5*y^2.
    """

    def __init__(self, beta, x0, y0, sx, sy, degree):
        self.beta = np.asarray(beta, float)
        self.x0 = float(x0)
        self.y0 = float(y0)
        self.sx = float(sx) or 1.0
        self.sy = float(sy) or 1.0
        self.degree = int(degree)

    @staticmethod
    def n_terms(degree):
        return 3 if int(degree) == 1 else 6

    def _design(self, x, y):
        X = (np.asarray(x, float) - self.x0) / self.sx
        Y = (np.asarray(y, float) - self.y0) / self.sy
        cols = [np.ones_like(X), X, Y]
        if self.degree >= 2:
            cols += [X * X, X * Y, Y * Y]
        return np.column_stack(cols)

    @classmethod
    def fit(cls, x, y, z, degree):
        """Подбор тренда по точкам. degree приводится к 1 или 2."""
        degree = 2 if int(degree) >= 2 else 1
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        z = np.asarray(z, float)
        x0, y0 = float(np.mean(x)), float(np.mean(y))
        sx = float(np.std(x)) or 1.0
        sy = float(np.std(y)) or 1.0
        self = cls(np.zeros(cls.n_terms(degree)), x0, y0, sx, sy, degree)
        B = self._design(x, y)
        beta, *_ = np.linalg.lstsq(B, z, rcond=None)
        self.beta = beta
        return self

    def __call__(self, x, y):
        """Значение тренда в точке/массиве точек."""
        return self._design(x, y) @ self.beta

    def residuals(self, x, y, z):
        return np.asarray(z, float) - self(x, y)

class ExternalDrift:
    """Линейный дрейф по внешней переменной s, известной всюду (растр).

    Брат PolyTrend для кригинга с внешним дрейфом (External Drift). Если поле
    закономерно связано с уже известной всюду величиной s (соседний пласт,
    структурная поверхность, сейсмический атрибут, грубая модель), эту связь
    снимают регрессией перед кригингом. Дальше кригуются остатки, а дрейф
    добавляется к оценке обратно из растра s:
        оценка = m(s) + кригинг_остатков.

    Дрейф здесь не функция координат, как у PolyTrend, а функция стороннего
    значения s в той же точке. Математика кригинга при этом не меняется: ровно
    та же схема регрессия-кригинг, что и у снятия полиномиального тренда.

    Степень 1: m = a0 + a1*s (линейный дрейф, обычный выбор для External Drift).
    Степень 2: m = a0 + a1*s + a2*s^2 (если связь явно изогнута).

    s центрируется и масштабируется на своё стандартное отклонение, иначе план
    для степени 2 плохо обусловлен. Чистый NumPy, как и всё ядро.
    """

    def __init__(self, beta, s0, ss, degree):
        self.beta = np.asarray(beta, float)
        self.s0 = float(s0)
        self.ss = float(ss) or 1.0
        self.degree = int(degree)

    @staticmethod
    def n_terms(degree):
        return 2 if int(degree) == 1 else 3

    def _design(self, s):
        S = (np.asarray(s, float) - self.s0) / self.ss
        cols = [np.ones_like(S), S]
        if self.degree >= 2:
            cols.append(S * S)
        return np.column_stack(cols)

    @classmethod
    def fit(cls, s, z, degree):
        """Подбор дрейфа по точкам (s, z). degree приводится к 1 или 2."""
        degree = 2 if int(degree) >= 2 else 1
        s = np.asarray(s, float)
        z = np.asarray(z, float)
        s0 = float(np.mean(s))
        ss = float(np.std(s)) or 1.0
        self = cls(np.zeros(cls.n_terms(degree)), s0, ss, degree)
        B = self._design(s)
        beta, *_ = np.linalg.lstsq(B, z, rcond=None)
        self.beta = beta
        return self

    def __call__(self, s):
        """Значение дрейфа по внешнему значению s (точка или массив)."""
        return self._design(s) @ self.beta

    def residuals(self, s, z):
        return np.asarray(z, float) - self(s)


def _erf(x):
    """Векторная аппроксимация erf (Abramowitz & Stegun 7.1.26).

    Максимальная погрешность около 1.5e-7 - с запасом для карты вероятности.
    Чистый NumPy, без scipy.
    """
    x = np.asarray(x, float)
    sign = np.sign(x)
    ax = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * ax)
    poly = (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t
             - 0.284496736) * t + 0.254829592) * t
    y = 1.0 - poly * np.exp(-ax * ax)
    return sign * y


def norm_cdf(z):
    """Функция распределения стандартного нормального закона Φ(z), векторно."""
    return 0.5 * (1.0 + _erf(np.asarray(z, float) / math.sqrt(2.0)))


def exceedance_prob(estimate, stderr, threshold, above=True):
    """Карта вероятности превышения порога из оценки и стандартной ошибки.

    Локальное распределение считается нормальным: Z ~ N(оценка, ошибка²). Тогда
    P(Z > порог) = Φ((оценка − порог) / ошибка). Это дешёвая оценка из готовых
    растров кригинга, отдельный кригинг не нужен. Для сильно скошенных полей
    нормальное допущение грубовато - там точнее индикаторный кригинг.

    Где ошибка ≤ 0 (узлы у данных, вырожденные ячейки) распределение
    вырождается в ступеньку: вероятность 1, если оценка выше порога, иначе 0.
    above=False даёт P(Z < порог) = 1 − P(Z > порог). Возвращает массив [0, 1].
    """
    est = np.asarray(estimate, float)
    se = np.asarray(stderr, float)
    safe = se > 0
    z = (est - threshold) / np.where(safe, se, 1.0)
    p = norm_cdf(z)
    step = (est > threshold).astype(float)
    p = np.where(safe, p, step)
    if not above:
        p = 1.0 - p
    return p


# ===========================================================================
#  Последовательная гауссова симуляция (SGS). Ансамбль равновероятных
#  реализаций вместо одной сглаженной оценки кригинга: даёт неопределённость
#  (P10/P50/P90, разброс) и непараметрическую вероятность превышения. Чистый
#  NumPy поверх простого кригинга _solve_point. Без scipy.
# ===========================================================================
def _norm_ppf(p):
    """Обратная функция стандартного нормального распределения Φ⁻¹(p).

    Векторная рациональная аппроксимация Acklam, погрешность < 1e-9. Без scipy.
    """
    a = (-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00)
    b = (-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00)
    pp = np.clip(np.asarray(p, float), 1e-12, 1.0 - 1e-12)
    out = np.empty_like(pp)
    plow, phigh = 0.02425, 1.0 - 0.02425
    lo = pp < plow
    hi = pp > phigh
    mid = ~(lo | hi)
    if lo.any():
        q = np.sqrt(-2.0 * np.log(pp[lo]))
        out[lo] = (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                   + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if hi.any():
        q = np.sqrt(-2.0 * np.log(1.0 - pp[hi]))
        out[hi] = -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q
                    + c[5]) / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    if mid.any():
        q = pp[mid] - 0.5
        r = q * q
        out[mid] = (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r
                    + a[5]) * q / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r
                                    + b[4]) * r + 1.0)
    return out


def nscore_transform(v, wts=None):
    """Нормально-оценочное преобразование (normal score).

    Значения переводятся в стандартные нормальные баллы через эмпирическую
    функцию распределения: ранг -> вероятность (ранг + 0.5)/n -> Φ⁻¹. Связки
    разводятся стабильной сортировкой. Если заданы веса wts (декластеризация),
    функция распределения строится по накопленному весу, а не по рангам.
    Возвращает (баллы, таблица_значений, таблица_баллов); две последних - для
    обратного преобразования."""
    v = np.asarray(v, float)
    n = v.size
    order = np.argsort(v, kind="mergesort")
    if wts is None:
        ranks = np.empty(n, float)
        ranks[order] = np.arange(n, dtype=float)
        ns = _norm_ppf((ranks + 0.5) / n)
        return ns, v[order], ns[order]
    w = np.asarray(wts, float)
    w = w / w.sum()
    wo = w[order]
    cum = np.cumsum(wo)
    p = np.clip(cum - 0.5 * wo, 1e-6, 1.0 - 1e-6)   # серединная CDF по весу
    ns_sorted = _norm_ppf(p)
    ns = np.empty(n, float)
    ns[order] = ns_sorted
    return ns, v[order], ns_sorted


def nscore_back(y, sv, sns):
    """Обратное normal-score преобразование: балл -> значение интерполяцией в
    таблице (sns -> sv). За пределами таблицы зажимается к min/max данных."""
    return np.interp(np.asarray(y, float), sns, sv)


def sgsim(xd, yd, vrd, vg, xmn, ymn, cell, nx, ny, nreal,
          ndmin, ndmax, rad2, seed=None, progress=None, wts=None):
    """Последовательная гауссова симуляция на регулярном гриде.

    Возвращает float32 (nreal, ny, nx) реализаций в ИСХОДНЫХ единицах (row 0 =
    север, как build_grid). Данные переводятся в нормальные баллы, симуляция
    идёт в гауссовом пространстве простым кригингом (среднее 0) по случайному
    пути; каждый узел розыгрывается из N(оценка, дисперсия) и сразу становится
    обуславливающим. Жёсткие данные привязываются к ближайшим узлам и фиксируются
    во всех реализациях. Соседи берутся окном по гриду и решаются _solve_point.
    vg - модель вариограммы НОРМАЛЬНЫХ БАЛЛОВ (порог около 1)."""
    rng = np.random.default_rng(seed)
    xd = np.asarray(xd, float)
    yd = np.asarray(yd, float)
    ns, sv, sns = nscore_transform(np.asarray(vrd, float), wts=wts)
    fix_ix = np.clip(np.round((xd - xmn) / cell).astype(int), 0, nx - 1)
    fix_iy = np.clip(np.round((yd - ymn) / cell).astype(int), 0, ny - 1)
    frozen = np.zeros((ny, nx), bool)
    fval = np.zeros((ny, nx), float)
    for k in range(ns.size):
        frozen[fix_iy[k], fix_ix[k]] = True
        fval[fix_iy[k], fix_ix[k]] = ns[k]
    free_idx = np.argwhere(~frozen)
    radius = math.sqrt(rad2)
    half = min(max(1, int(math.ceil(radius / cell))), 25)
    xs_node = xmn + np.arange(nx) * cell
    ys_node = ymn + np.arange(ny) * cell
    maxcand = max(4 * int(ndmax), 8)
    out = np.empty((nreal, ny, nx), np.float32)
    for r in range(nreal):
        sim = np.full((ny, nx), np.nan)
        sim[frozen] = fval[frozen]
        path = free_idx.copy()
        rng.shuffle(path)
        for iy, ix in path:
            x0 = max(0, ix - half); x1 = min(nx, ix + half + 1)
            y0 = max(0, iy - half); y1 = min(ny, iy + half + 1)
            sub = sim[y0:y1, x0:x1]
            m = ~np.isnan(sub)
            e = var = float("nan")
            if m.any():
                jy, jx = np.nonzero(m)
                ddx = (x0 + jx) - ix
                ddy = (y0 + jy) - iy
                d2 = ddx * ddx + ddy * ddy
                if d2.size > maxcand:
                    keep = np.argpartition(d2, maxcand)[:maxcand]
                    jy = jy[keep]; jx = jx[keep]
                e, var = _solve_point(
                    xs_node[ix], ys_node[iy], xs_node[x0 + jx], ys_node[y0 + jy],
                    sub[jy, jx], vg, 0, 0.0, 1, ndmax, rad2, float("nan"))
            if e != e:                           # nan: соседей нет -> априор
                val = rng.standard_normal()
            else:
                val = e + math.sqrt(max(var, 0.0)) * rng.standard_normal()
            sim[iy, ix] = val
        out[r] = np.flipud(nscore_back(sim, sv, sns).astype(np.float32))
        if progress is not None:
            progress(r + 1, nreal)
    return out


# ===========================================================================
#  Пересечение TIN (3D-треугольников) с вертикальной шторой разреза. В отличие
#  от растрового грида (z = f(x,y), одно значение на точку) TIN из настоящих
#  3D-граней может нависать и опрокидываться: над одной станцией оказывается
#  несколько отметок, и трасса на разрезе заворачивается. Чистая геометрия в
#  3D (треугольник × вертикальная плоскость сегмента), без QGIS.
# ===========================================================================
def tin_section_trace(poly_xy, triangles, eps=1e-9):
    """Трасса TIN на разрезе вдоль ломаной poly_xy.

    poly_xy:   [(x, y), ...] вершины линии разреза.
    triangles: итерируемое из ((x0,y0,z0),(x1,y1,z1),(x2,y2,z2)).
    Возвращает список сегментов (d0, z0, d1, z1) в осях расстояние-высота
    (без множителя vex - его накладывает вызывающий). Корректно для нависающих
    поверхностей: один треугольник даёт один сегмент, перекрытие по расстоянию
    с разной высотой образует заворот трассы."""
    P = [(float(x), float(y)) for x, y in poly_xy]
    if len(P) < 2:
        return []
    dv = [0.0]
    for i in range(1, len(P)):
        dv.append(dv[-1] + math.hypot(P[i][0] - P[i - 1][0],
                                      P[i][1] - P[i - 1][1]))
    tris = [np.asarray(t, float) for t in triangles]
    out = []
    for k in range(len(P) - 1):
        ax, ay = P[k]; bx, by = P[k + 1]
        dA, dB = dv[k], dv[k + 1]
        abx, aby = bx - ax, by - ay
        L2 = abx * abx + aby * aby
        if L2 <= eps:
            continue
        nx, ny = -aby, abx                  # горизонтальная нормаль к сегменту
        for T in tris:
            fx = nx * (T[:, 0] - ax) + ny * (T[:, 1] - ay)
            cross = []
            for i, j in ((0, 1), (1, 2), (2, 0)):
                fi, fj = fx[i], fx[j]
                if abs(fi) <= eps and abs(fj) <= eps:
                    cross.append(T[i]); cross.append(T[j])
                elif (fi <= 0.0 <= fj) or (fj <= 0.0 <= fi):
                    if abs(fi - fj) <= eps:
                        continue
                    t = fi / (fi - fj)
                    cross.append(T[i] + t * (T[j] - T[i]))
            if len(cross) < 2:
                continue
            ss = [((q[0] - ax) * abx + (q[1] - ay) * aby) / L2 for q in cross]
            i0 = int(np.argmin(ss)); i1 = int(np.argmax(ss))
            s0, s1 = ss[i0], ss[i1]
            z0, z1 = cross[i0][2], cross[i1][2]
            if s1 - s0 <= eps:
                continue
            lo, hi = max(s0, 0.0), min(s1, 1.0)
            if hi - lo <= eps:
                continue
            ds = s1 - s0
            zl = z0 + (lo - s0) / ds * (z1 - z0)
            zh = z0 + (hi - s0) / ds * (z1 - z0)
            out.append((dA + lo * (dB - dA), zl, dA + hi * (dB - dA), zh))
    return out


def fan_triangulate(ring):
    """Веерная триангуляция плоского кольца [(x,y,z), ...] (без замыкающей
    повторной вершины) в список треугольников от вершины 0. Для граней TIN с
    числом вершин больше трёх."""
    pts = list(ring)
    if len(pts) >= 2 and pts[0] == pts[-1]:
        pts = pts[:-1]
    return [(pts[0], pts[i], pts[i + 1]) for i in range(1, len(pts) - 1)]


# ---------------------------------------------------------------------------
#  Разломы: отбор соседей по видимости
# ---------------------------------------------------------------------------

def _dist_point_to_polyline(x, y, pts):
    """Расстояние от точки до ломаной."""
    best = None
    for k in range(len(pts) - 1):
        ax, ay = pts[k]
        bx, by = pts[k + 1]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        if den <= 0.0:
            continue
        s = ((x - ax) * dx + (y - ay) * dy) / den
        s = 0.0 if s < 0.0 else (1.0 if s > 1.0 else s)
        d = math.hypot(x - (ax + s * dx), y - (ay + s * dy))
        if best is None or d < best:
            best = d
    return best


def junction_report(lines, tol, eps=None):
    """Стыки разломов: сомкнутые концы и недоведённые.

    Барьер локален и пересечения обрабатывает сам: на X-образном
    пересечении и на T-стыке из своего сектора чужих замеров не видно.
    Матрица отношений между разломами, как в других реализациях, здесь не
    нужна - она лечит болезнь подхода через дрейф, где функция разлома
    задана на всей площади и её надо где-то обрывать.

    Чего барьер не умеет, так это догадаться о намерении. Разлом,
    недоведённый до соседнего при оцифровке, оставляет щель, и через неё
    видимость протекает. На карте это невидимо: линии выглядят
    сомкнутыми. Замеры показывают, что щель в один метр пропускает
    единицы замеров, в три метра десятки, а в пять метров больше
    половины.

    Возвращает (сомкнутых концов, [(номер линии, конец, расстояние)]),
    где конец это 0 для начала и 1 для конца ломаной. В список попадают
    только недоведённые концы: расстояние больше eps и не больше tol.
    """
    eps = float(tol) * 1e-3 if eps is None else float(eps)
    joined = 0
    gaps = []
    for i, pts in enumerate(lines):
        if len(pts) < 2:
            continue
        for at, (x, y) in ((0, pts[0]), (1, pts[-1])):
            near = None
            for j, other in enumerate(lines):
                if j == i or len(other) < 2:
                    continue
                d = _dist_point_to_polyline(x, y, other)
                if d is not None and (near is None or d < near):
                    near = d
            if near is None:
                continue
            if near <= eps:
                joined += 1
            elif near <= tol:
                gaps.append((i, at, near))
    return joined, gaps


def fill_pockets(grid, nodata):
    """Заполнить запертые ячейки значением ближайшей рассчитанной.

    Складка разлома мельче ячейки запирает ячейку в кармане, откуда не
    видно ни одного замера. Оценивать там не из чего, и ячейка остаётся
    пустой. Дырка в гриде дороже, чем небольшой сдвиг: по ней рвутся
    изолинии, разваливаются пояса, искажаются объёмы и мощности. Значение
    соседней ячейки даёт ошибку геометрии не больше размера самого
    кармана, а карман по определению мельче складки.

    Заполнение идёт волной от края кармана внутрь: за один проход пустая
    ячейка берёт среднее по уже рассчитанным соседям из восьми, и так до
    исчезновения пустот. Порядок обхода на результат не влияет.

    Возвращает (грид, сколько заполнено, ширина наибольшего кармана в
    ячейках). Ширина это число проходов: по ней видно, был ли карман в
    одну ячейку или это уже область, о которой стоит сказать громче.
    """
    out = np.array(grid, dtype=float, copy=True)
    empty = (out == nodata)
    total = int(empty.sum())
    if not total:
        return out, 0, 0
    passes = 0
    while empty.any() and passes < 1000:
        acc = np.zeros_like(out)
        cnt = np.zeros_like(out)
        good = ~empty
        vals = np.where(good, out, 0.0)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0:
                    continue
                sv = np.roll(np.roll(vals, dy, axis=0), dx, axis=1)
                sg = np.roll(np.roll(good, dy, axis=0), dx, axis=1)
                # края грида не заворачиваем: соседей за краем нет
                if dy == 1:
                    sv[0, :] = 0.0; sg[0, :] = False
                elif dy == -1:
                    sv[-1, :] = 0.0; sg[-1, :] = False
                if dx == 1:
                    sv[:, 0] = 0.0; sg[:, 0] = False
                elif dx == -1:
                    sv[:, -1] = 0.0; sg[:, -1] = False
                acc += sv
                cnt += sg
        can = empty & (cnt > 0)
        if not can.any():
            break                      # карман отрезан от всего грида
        out[can] = acc[can] / cnt[can]
        empty &= ~can
        passes += 1
    return out, total - int(empty.sum()), passes


def fault_segments(lines):
    """Звенья разломов массивом (m, 4): x0, y0, x1, y1.

    Разлом здесь остаётся линией и в сетку не переводится. Растровая маска
    барьерных ячеек, стоявшая тут раньше, порождала две беды сразу.
    Первая: ячейка не передаёт диагональ, и косой разлом ложился в сетку
    ступеньками. Вторая, и она была причиной пустот в гриде: луч видимости
    начинается в центре оцениваемой ячейки, и если сама ячейка барьерная,
    первая же проверка попадала в маску. Такая ячейка не видела ни одного
    замера из всей площади, получала nodata, и вдоль разлома в гриде
    оставалась ступенчатая щель.
    """
    segs = []
    for ln in lines or ():
        pts = np.asarray(ln, dtype=float)
        if pts.ndim != 2 or pts.shape[0] < 2:
            continue
        for k in range(pts.shape[0] - 1):
            x0, y0 = float(pts[k, 0]), float(pts[k, 1])
            x1, y1 = float(pts[k + 1, 0]), float(pts[k + 1, 1])
            if x0 != x1 or y0 != y1:
                segs.append((x0, y0, x1, y1))
    if not segs:
        return np.zeros((0, 4), dtype=float)
    return np.asarray(segs, dtype=float)


def _segments_near_bundle(segs, xloc, yloc, xs, ys):
    """Звенья, чей габарит пересекается с габаритом пучка лучей.

    Звено, лежащее в стороне от всего пучка, не может перекрыть ни один
    луч. Проверка стоит O(звеньев), а снимает работу O(звеньев на
    замеры). Возвращает None, если не осталось ни одного.
    """
    x0 = min(float(xloc), float(xs.min()))
    x1 = max(float(xloc), float(xs.max()))
    y0 = min(float(yloc), float(ys.min()))
    y1 = max(float(yloc), float(ys.max()))
    sx0 = np.minimum(segs[:, 0], segs[:, 2])
    sx1 = np.maximum(segs[:, 0], segs[:, 2])
    sy0 = np.minimum(segs[:, 1], segs[:, 3])
    sy1 = np.maximum(segs[:, 1], segs[:, 3])
    near = (sx1 >= x0) & (sx0 <= x1) & (sy1 >= y0) & (sy0 <= y1)
    if not near.any():
        return None
    return segs[near] if not near.all() else segs


def visible_mask(xloc, yloc, xs, ys, segs):
    """Какие замеры видны из точки оценки: отрезок не пересекает разлом.

    Пересечение строгое: знаки поворотов на обоих концах обязаны быть
    противоположны. Касание концом и совпадение по прямой барьером не
    считаются, и это осознанно. Центр ячейки, попавший точно на линию,
    иначе не увидел бы ни одного замера - ровно та беда, ради которой
    маска и убрана. Замер, стоящий точно на разломе, виден с обоих крыльев.

    Крыло у ячейки получается само собой: центр лежит строго с одной
    стороны линии, и всё, что за ней, отсекается. Отдельного разбиения на
    блоки не нужно.

    Приближение здесь одно и оно названо в справке: по видимости
    отбираются только соседи, а ковариации между самими замерами остаются
    евклидовыми. Занулять их нельзя - система потеряет положительную
    определённость, и кригинг развалится.
    """
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    if segs is None or len(segs) == 0 or xs.size == 0:
        return np.ones(xs.shape, dtype=bool)
    # Отсев по габаритам. Звено, прямоугольник которого не пересекается с
    # прямоугольником всего пучка лучей, не может перекрыть ни один из
    # них. Проверка стоит O(звеньев), а снимает работу O(звеньев на
    # замеры). На сети разломов, растянутой по площади, при ограниченном
    # радиусе поиска до расчёта доходят единицы звеньев из сотен.
    # Порог включения выбран замером, не на глаз. На малой задаче отсев
    # не окупается: он сам заводит несколько массивов, а работы снимает
    # меньше, чем стоит. При пороге вдвое ниже средние сети выигрывают
    # вдвое, но задачи, где отсев ничего не снимает, теряют пятую часть
    # скорости. Регресс дороже упущенного выигрыша, поэтому порог здесь.
    spread = False
    if segs.shape[0] > 8 and segs.shape[0] * xs.size > 20000:
        n_all = segs.shape[0]
        segs = _segments_near_bundle(segs, xloc, yloc, xs, ys)
        if segs is None:
            return np.ones(xs.shape, dtype=bool)
        # Отсев снял большую часть звеньев - значит сеть разломов широко
        # растянута относительно пучка лучей. Только в этом случае имеет
        # смысл разбирать уцелевшие пары по индексам.
        spread = segs.shape[0] * 4 <= n_all

    ax = segs[:, 0][:, None]
    ay = segs[:, 1][:, None]
    bx = segs[:, 2][:, None]
    by = segs[:, 3][:, None]
    # Сперва d3, d4 - с какой стороны ЛУЧА лежат концы звена. Этот тест
    # отсеивает куда сильнее, чем сторона звена: у дальнего звена оба
    # конца почти всегда по одну сторону луча, тогда как по сторону
    # звена замеры делятся примерно пополам.
    rx = xs - xloc
    ry = ys - yloc
    d3 = rx * (ay - yloc) - ry * (ax - xloc)
    d4 = rx * (by - yloc) - ry * (bx - xloc)
    cand = (d3 * d4) < 0.0

    # d1, d2 - с какой стороны звена лежат точка оценки и замер. Ветка
    # выбирается по итогу отсева, и это не украшение: разбор по индексам
    # собирает значения вразнобой по памяти и на плотной матрице
    # проигрывает сплошному счёту в полтора раза. Замерено.
    if not spread or np.count_nonzero(cand) > 0.25 * cand.size:
        d1 = (bx - ax) * (yloc - ay) - (by - ay) * (xloc - ax)
        d2 = (bx - ax) * (ys - ay) - (by - ay) * (xs - ax)
        return ~(cand & ((d1 * d2) < 0.0)).any(axis=0)
    if not cand.any():
        return np.ones(xs.shape, dtype=bool)

    si, pj = np.nonzero(cand)
    dx = segs[si, 2] - segs[si, 0]
    dy = segs[si, 3] - segs[si, 1]
    d1 = dx * (yloc - segs[si, 1]) - dy * (xloc - segs[si, 0])
    d2 = dx * (ys[pj] - segs[si, 1]) - dy * (xs[pj] - segs[si, 0])
    blocked = np.zeros(xs.shape, dtype=bool)
    hit = (d1 * d2) < 0.0
    if hit.any():
        blocked[pj[hit]] = True
    return ~blocked


def visible(x0, y0, x1, y1, segs):
    """Виден ли один замер из точки оценки. Обёртка над visible_mask."""
    return bool(visible_mask(x0, y0, np.array([x1], dtype=float),
                             np.array([y1], dtype=float), segs)[0])


def visible_points(xloc, yloc, xd, yd, segs):
    """Индексы замеров, видимых из точки оценки."""
    return np.nonzero(visible_mask(xloc, yloc, xd, yd, segs))[0]
