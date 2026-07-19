# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Ядро валидации рельефа. Чистая математика, без QGIS и Qt.

Задача блока: измерить, насколько построенная ЦМР воспроизводит исходные
горизонтали. Замечание, ради которого блок и делается: цифра «невязка в
вершинах тех же горизонталей, что подавались на вход» измеряет воспроизведение
входа, а не точность предсказания. Поэтому ядро считает обе величины по одной и
той же формуле, а какие горизонтали подавались в построение, а какие отложены,
решает вызывающая сторона. Разделение выборки тоже здесь, чтобы оно было
детерминированным и воспроизводимым.
"""
import math

import numpy as np

from .section_core import sample_grid_points, vertex_distances


# --- разбиение горизонтали на точки опробования -------------------------

def densify_polyline(vertices, step=0.0):
    """Точки вдоль ломаной: все вершины плюс, при step > 0, промежуточные.

    Вершины сохраняются всегда: именно в них лежит исходная геометрия
    горизонтали, и терять их нельзя. Шаг добавляет точки на длинных прямых
    участках, иначе редко оцифрованная горизонталь даст мало проб.
    """
    if len(vertices) < 2:
        return np.array([v[0] for v in vertices], dtype=float), \
               np.array([v[1] for v in vertices], dtype=float)
    vd = vertex_distances(vertices)
    total = vd[-1]
    if step is None or step <= 0 or total <= 0:
        d = np.asarray(vd, dtype=float)
    else:
        n = max(1, int(math.ceil(total / float(step))))
        d = np.unique(np.concatenate(
            [np.linspace(0.0, total, n + 1), np.asarray(vd, dtype=float)]))
        tol = max(total * 1e-9, 1e-12)
        d = d[np.concatenate([[True], np.diff(d) > tol])]
    xs = np.empty(len(d))
    ys = np.empty(len(d))
    idx = np.clip(np.searchsorted(np.asarray(vd), d, side="right") - 1,
                  0, len(vertices) - 2)
    for k in range(len(d)):
        i = int(idx[k])
        seg = vd[i + 1] - vd[i]
        t = 0.0 if seg <= 0 else (d[k] - vd[i]) / seg
        t = min(max(t, 0.0), 1.0)
        xs[k] = vertices[i][0] + t * (vertices[i + 1][0] - vertices[i][0])
        ys[k] = vertices[i][1] + t * (vertices[i + 1][1] - vertices[i][1])
    return xs, ys


# --- сечение рельефа -----------------------------------------------------

def contour_interval(levels, tol=1e-6):
    """Сечение рельефа по набору отметок горизонталей.

    Берётся наименьшая положительная разность между соседними уникальными
    отметками. Для правильно нарезанного набора это и есть сечение. Набор с
    произвольными отметками (например горизонтали, снятые с разных карт) даст
    маленькое число, поэтому результат надо считать подсказкой, а не истиной.
    """
    u = np.unique(np.asarray([lv for lv in levels if np.isfinite(lv)],
                             dtype=float))
    if u.size < 2:
        return None
    diffs = np.diff(u)
    diffs = diffs[diffs > tol]
    if diffs.size == 0:
        return None
    return float(np.min(diffs))


def split_levels(levels, every=4, offset=0):
    """Разделить отметки на построение и проверку: каждая N-я уходит в проверку.

    Делится не по точкам и не по объектам, а по УРОВНЯМ. Убрать из построения
    отдельные звенья одной горизонтали бессмысленно: соседние звенья того же
    уровня подскажут ответ, и проверка получится завышенной. Отложенный уровень
    исчезает целиком, и восстановить его интерполятор может только по соседним
    уровням, а это и есть предсказание.

    Возвращает (build_levels, check_levels), оба отсортированы.
    """
    u = np.unique(np.asarray([lv for lv in levels if np.isfinite(lv)],
                             dtype=float))
    if u.size == 0:
        return [], []
    every = max(2, int(every))
    off = int(offset) % every
    mask = (np.arange(u.size) % every) == off
    # крайние уровни в проверку не отдаём: за пределами набора интерполятор
    # экстраполирует, и невязка там мерит не то, что мы хотим измерить
    if u.size >= 3:
        mask[0] = False
        mask[-1] = False
    return u[~mask].tolist(), u[mask].tolist()


# --- невязка -------------------------------------------------------------

def residuals(xs, ys, z_true, arr, gt, bilinear=True):
    """Невязка в точках: отметка горизонтали минус значение ЦМР.

    Знак выбран так, чтобы положительная невязка означала «ЦМР ниже
    горизонтали», это привычнее читается на карте.
    """
    z_dem = sample_grid_points(arr, gt, np.asarray(xs, dtype=float),
                               np.asarray(ys, dtype=float), bilinear)
    return np.asarray(z_true, dtype=float) - z_dem, z_dem


def residual_stats(res, interval=None):
    """Сводка по невязкам. Пустой или полностью нечисловой набор даёт None.

    Считаются и смещение (среднее), и разброс (СКО), потому что это разные
    болезни. Ненулевое среднее означает систематический сдвиг поверхности,
    большое СКО при нулевом среднем - шум или срезание форм. Медиана модуля
    устойчива к единичным выбросам, максимум показывает худшее место.

    Доля невязок больше половины сечения - практическая метрика: если она
    заметна, горизонталь, проведённая по такой ЦМР, встанет не туда, где была
    исходная.
    """
    r = np.asarray(res, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return None
    a = np.abs(r)
    out = {
        "n": int(r.size),
        "mean": float(np.mean(r)),
        "std": float(np.std(r, ddof=1)) if r.size > 1 else 0.0,
        "rmse": float(np.sqrt(np.mean(r * r))),
        "median_abs": float(np.median(a)),
        "p90_abs": float(np.percentile(a, 90)),
        "max_abs": float(np.max(a)),
        "min": float(np.min(r)),
        "max": float(np.max(r)),
    }
    if interval and interval > 0:
        out["interval"] = float(interval)
        out["over_half"] = float(np.mean(a > 0.5 * interval))
        out["over_full"] = float(np.mean(a > interval))
    return out


def histogram(res, bins=41):
    """Гистограмма невязок: центры интервалов и частоты. Для отчёта."""
    r = np.asarray(res, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return np.array([]), np.array([])
    lo, hi = float(np.min(r)), float(np.max(r))
    if hi <= lo:
        lo, hi = lo - 0.5, hi + 0.5
    edges = np.linspace(lo, hi, int(max(5, bins)) + 1)
    cnt, edges = np.histogram(r, bins=edges)
    centers = 0.5 * (edges[:-1] + edges[1:])
    return centers, cnt


def by_level(levels, res):
    """Невязка в разрезе отметок: для каждого уровня среднее и СКО.

    Нужна, чтобы увидеть систематику по высоте. Если невязка растёт к вершинам
    или к тальвегам, это говорит о срезании форм, а не о случайном шуме.
    """
    lv = np.asarray(levels, dtype=float)
    r = np.asarray(res, dtype=float)
    ok = np.isfinite(lv) & np.isfinite(r)
    lv, r = lv[ok], r[ok]
    out = []
    for u in np.unique(lv):
        sel = r[lv == u]
        if sel.size == 0:
            continue
        out.append({"level": float(u), "n": int(sel.size),
                    "mean": float(np.mean(sel)),
                    "std": float(np.std(sel, ddof=1)) if sel.size > 1 else 0.0,
                    "max_abs": float(np.max(np.abs(sel)))})
    return out


def verdict(stats_in, stats_out=None):
    """Короткий разбор для отчёта: что означают полученные числа.

    Возвращает список строк-ключей, текст собирает вызывающая сторона, чтобы
    ядро не занималось переводом. Ключи:
      bias        - заметное систематическое смещение
      spread      - разброс велик относительно сечения
      overshoot   - заметная доля точек мимо на половину сечения
      holdout_gap - воспроизведение входа много лучше предсказания
      clean       - придраться не к чему
    """
    keys = []
    if stats_in is None:
        return keys
    iv = stats_in.get("interval")
    if iv:
        if abs(stats_in["mean"]) > 0.1 * iv:
            keys.append("bias")
        if stats_in["std"] > 0.5 * iv:
            keys.append("spread")
        if stats_in.get("over_half", 0.0) > 0.05:
            keys.append("overshoot")
    if stats_out is not None and stats_in["rmse"] > 0:
        if stats_out["rmse"] > 3.0 * stats_in["rmse"]:
            keys.append("holdout_gap")
    if not keys:
        keys.append("clean")
    return keys


# --- террасинг -----------------------------------------------------------

def profile_curvature(z, cell):
    """Вертикальная (профильная) кривизна: вторая производная вдоль склона.

    Считается по классическим формулам Zevenbergen-Thorne на окне 3x3 через
    центральные разности. Положительная кривизна означает вогнутость вдоль
    склона, отрицательная - выпуклость.

    Именно эта величина выдаёт террасинг. У поверхности, построенной по
    горизонталям плохим интерполятором, склон идёт ступенями: полка возле
    уровня горизонтали, затем резкий сброс к следующему. На полке кривизна
    почти нулевая, на сбросе даёт всплеск, и вся картина полосами повторяет
    рисунок горизонталей.

    О расходе памяти. Массив центрируется и считается в float32, буферы
    переиспользуются. Это не косметика: на матрице в 16.5 млн ячеек (132 МБ
    во float64) прямолинейная реализация держала около десятка полных копий и
    доходила до полутора гигабайт, а такие матрицы у людей рабочие.
    Центрирование на результат не влияет, производные не зависят от
    постоянного слагаемого, зато возвращает точность, которая потерялась бы
    при переходе к float32 на отметках в сотни метров.
    """
    z = np.asarray(z)
    fin = np.isfinite(z)
    if not fin.any():
        return np.full(z.shape, np.nan, dtype=np.float32)
    off = np.float32(float(np.mean(z[fin])))
    zc = np.subtract(z, off, dtype=np.float32)
    c = np.float32(float(cell) or 1.0)
    nan = np.float32(np.nan)

    zx = np.full(z.shape, nan, dtype=np.float32)
    zy = np.full(z.shape, nan, dtype=np.float32)
    zx[1:-1, 1:-1] = (zc[1:-1, 2:] - zc[1:-1, :-2]) / (2.0 * c)
    zy[1:-1, 1:-1] = (zc[2:, 1:-1] - zc[:-2, 1:-1]) / (2.0 * c)

    # числитель собираем по слагаемым, каждый временный кусок освобождаем
    num = np.full(z.shape, nan, dtype=np.float32)
    t = (zc[1:-1, 2:] - 2.0 * zc[1:-1, 1:-1] + zc[1:-1, :-2]) / (c * c)
    num[1:-1, 1:-1] = zx[1:-1, 1:-1] * zx[1:-1, 1:-1] * t
    t = (zc[2:, 1:-1] - 2.0 * zc[1:-1, 1:-1] + zc[:-2, 1:-1]) / (c * c)
    num[1:-1, 1:-1] += zy[1:-1, 1:-1] * zy[1:-1, 1:-1] * t
    t = (zc[2:, 2:] - zc[2:, :-2] - zc[:-2, 2:] + zc[:-2, :-2]) / (4.0 * c * c)
    num[1:-1, 1:-1] += 2.0 * zx[1:-1, 1:-1] * zy[1:-1, 1:-1] * t
    del t, zc

    p = zx                      # дальше zx уже не нужен, считаем в него же
    np.multiply(zx, zx, out=p)
    np.multiply(zy, zy, out=zy)
    np.add(p, zy, out=p)
    del zy
    with np.errstate(invalid="ignore", divide="ignore"):
        denom = np.add(p, np.float32(1.0))
        np.power(denom, np.float32(1.5), out=denom)
        np.multiply(denom, p, out=denom)
        np.divide(num, denom, out=num)
        np.negative(num, out=num)
    del denom
    kv = num
    kv[~np.isfinite(kv)] = np.nan
    # на плоских местах кривизна вдоль склона не определена
    kv[p < np.float32(1e-12)] = np.nan
    return kv


def flat_mask(z, cell, interval, min_drop_frac=0.01):
    """Ячейки, которые нельзя пускать в статистику: перепад ниже шума.

    Порог задаётся не абсолютным уклоном, а долей сечения рельефа: ячейка
    считается плоской, если перепад высот на ней меньше min_drop_frac от
    сечения. При сечении 0.5 м и доле 0.01 это 5 мм на ячейку.

    Зачем нужно. Водная гладь, залитые площадки, зоны без данных дают
    околонулевой уклон и отметку, стоящую на одном месте. Такая масса
    ячеек перекашивает любую статистику по отметкам: на реальной матрице с
    водохранилищем на 45 процентов площади индекс притяжения падал вдвое
    ниже единицы и показывал ложное благополучие. Уклон там задан шумом в
    миллиметрах, диагностировать по нему нечего.
    """
    z = np.asarray(z, dtype=float)
    if not interval or interval <= 0 or min_drop_frac is None \
            or min_drop_frac <= 0:
        return np.zeros(z.shape, dtype=bool)
    drop = drop_per_cell(z, cell)
    thr = np.float32(float(min_drop_frac) * float(interval))
    with np.errstate(invalid="ignore"):
        flat = drop < thr
    flat[~np.isfinite(drop)] = False
    return flat


def level_attraction(z, interval, base=0.0, band=0.1, cell=None,
                     min_drop_frac=0.01):
    """Индекс притяжения отметок к уровням горизонталей.

    Прямая проверка террасинга по самим отметкам, без производных. У здоровой
    поверхности отметки между соседними уровнями распределены более-менее
    равномерно, поэтому доля ячеек, попавших в узкую полосу вокруг уровня,
    близка к ширине этой полосы. У террасированной поверхности отметки липнут
    к уровням, и доля заметно выше.

    Если задан cell, из статистики исключаются околоплоские ячейки (см.
    flat_mask): на матрицах с водоёмами они дают ложную картину.

    Возвращает (доля, ожидаемая доля, отношение, доля исключённых). Отношение
    около единицы - признаков террасинга нет, два и больше - есть.
    """
    z = np.asarray(z, dtype=float)
    fin = np.isfinite(z)
    if fin.sum() == 0 or not interval or interval <= 0:
        return None
    skipped = 0.0
    if cell:
        flat = flat_mask(z, cell, interval, min_drop_frac)
        use = fin & ~flat
        if use.sum() >= 100:      # на крохах статистики смысла нет
            skipped = float(np.mean(flat[fin]))
            fin = use
    v = z[fin]
    frac = np.abs(((v - float(base)) / float(interval) + 0.5) % 1.0 - 0.5)
    share = float(np.mean(frac <= float(band)))
    expect = 2.0 * float(band)
    return (share, expect, (share / expect if expect > 0 else float("nan")),
            skipped)


def phase_histogram(z, interval, base=0.0, bins=36):
    """Распределение отметок по фазе внутри сечения, от -0.5 до 0.5.

    Ровная гистограмма - террасинга нет. Пик в нуле означает, что отметки
    собираются у уровней горизонталей, то есть поверхность ступенчатая.
    """
    z = np.asarray(z, dtype=float)
    z = z[np.isfinite(z)]
    if z.size == 0 or not interval or interval <= 0:
        return np.array([]), np.array([])
    ph = ((z - float(base)) / float(interval) + 0.5) % 1.0 - 0.5
    cnt, edges = np.histogram(ph, bins=int(max(6, bins)), range=(-0.5, 0.5))
    return 0.5 * (edges[:-1] + edges[1:]), cnt


def terracing_stats(z, cell, interval, base=0.0, band=0.1,
                    min_drop_frac=0.01):
    """Сводка по террасингу: кривизна плюс притяжение к уровням.

    Околоплоские ячейки в притяжение не идут, их доля возвращается полем
    flat_skipped.
    """
    kv = profile_curvature(z, cell)
    f = kv[np.isfinite(kv)]
    out = {"n_curv": int(f.size)}
    if f.size:
        out["curv_mean_abs"] = float(np.mean(np.abs(f)))
        out["curv_p95_abs"] = float(np.percentile(np.abs(f), 95))
        out["curv_max_abs"] = float(np.max(np.abs(f)))
    la = level_attraction(z, interval, base, band, cell, min_drop_frac)
    if la is not None:
        (out["attract_share"], out["attract_expect"], out["attract_ratio"],
         out["flat_skipped"]) = la
    return out


def terracing_verdict(stats, ratio_warn=1.5, ratio_bad=2.0):
    """Ключи разбора для отчёта: terraced, suspect, clean, unknown."""
    r = stats.get("attract_ratio") if stats else None
    if r is None or not np.isfinite(r):
        return ["unknown"]
    if r >= ratio_bad:
        return ["terraced"]
    if r >= ratio_warn:
        return ["suspect"]
    return ["clean"]


def flat_level_hits(z, cell, levels, interval, min_drop_frac=0.01):
    """Уровни, попавшие в плоские площадки, и число задетых ячеек.

    Зачем. Изолиния на площадке с околонулевым уклоном определяется не
    рельефом, а шумом: она рассыпается на множество мелких колец и выглядит
    как одна утолщённая линия. Классический источник - водная гладь, где
    отметка стоит на месте с точностью до миллиметров.

    Считается за один проход. Порог перепада thr = min_drop_frac * сечение
    задаёт и плоскость ячейки, и окно вокруг уровня: если внутри ячейки
    поверхность меняется меньше чем на thr, уровень может её пересечь только
    когда отметка отличается от уровня меньше чем на thr.

    Возвращает список словарей по каждому уровню, отсортированный по числу
    задетых ячеек убыванию. Пустой список означает, что придраться не к чему.
    """
    z = np.asarray(z, dtype=float)
    if not interval or interval <= 0 or not len(levels):
        return []
    thr = float(min_drop_frac) * float(interval)
    if thr <= 0:
        return []
    flat = flat_mask(z, cell, interval, min_drop_frac)
    v = z[flat & np.isfinite(z)]
    if v.size == 0:
        return []
    v = np.sort(v)
    n_valid = int(np.isfinite(z).sum())
    out = []
    for L in levels:
        lo = np.searchsorted(v, L - thr, side="left")
        hi = np.searchsorted(v, L + thr, side="right")
        n = int(hi - lo)
        if n <= 0:
            continue
        out.append({"level": float(L), "n_flat": n,
                    "share_valid": n / float(max(n_valid, 1))})
    out.sort(key=lambda d: -d["n_flat"])
    return out


# --- уверенность горизонтали --------------------------------------------

def drop_per_cell(z, cell):
    """Перепад высот на ячейку: модуль градиента, умноженный на размер ячейки.

    Это и есть сигнал, с которым сравнивается шум матрицы. В отличие от уклона
    величина имеет размерность высоты, поэтому её можно прямо сопоставлять с
    сечением рельефа и с точностью данных.

    Неочевидное свойство: результат не зависит от объявленного размера ячейки,
    деление на шаг при взятии производной и умножение на шаг сокращаются.
    Величина остаётся свойством данных, а не системы координат.

    Считается в float32 с центрированием и без промежуточных полных копий:
    функция вызывается на рабочих матрицах в десятки миллионов ячеек.
    """
    z = np.asarray(z)
    fin = np.isfinite(z)
    if not fin.any():
        return np.full(z.shape, np.nan, dtype=np.float32)
    off = np.float32(float(np.mean(z[fin])))
    zc = np.subtract(z, off, dtype=np.float32)
    nan = np.float32(np.nan)

    gx = np.full(z.shape, nan, dtype=np.float32)
    gy = np.full(z.shape, nan, dtype=np.float32)
    # центральные разности внутри, односторонние по краям - как у np.gradient
    gx[:, 1:-1] = (zc[:, 2:] - zc[:, :-2]) * np.float32(0.5)
    gx[:, 0] = zc[:, 1] - zc[:, 0]
    gx[:, -1] = zc[:, -1] - zc[:, -2]
    gy[1:-1, :] = (zc[2:, :] - zc[:-2, :]) * np.float32(0.5)
    gy[0, :] = zc[1, :] - zc[0, :]
    gy[-1, :] = zc[-1, :] - zc[-2, :]
    del zc
    np.multiply(gx, gx, out=gx)
    np.multiply(gy, gy, out=gy)
    np.add(gx, gy, out=gx)
    del gy
    return np.sqrt(gx, out=gx)


def confident_runs(flags, min_run=3):
    """Разбить вершины линии на уверенные и неуверенные участки.

    flags: True там, где вершина неуверенная (перепад ниже порога).

    Одиночная подозрительная вершина линию не рвёт: рвут только серии длиной
    от min_run подряд. Иначе случайная ячейка на нормальном склоне крошила бы
    горизонталь без всякой причины.

    Возвращает (keep, cut), каждый элемент это пара индексов включительно.
    Участки строятся по вершинам, поэтому соседние куски делят граничную
    вершину и разрыва в геометрии не возникает.
    """
    f = np.asarray(flags, dtype=bool)
    n = f.size
    if n == 0:
        return [], []
    bad = np.zeros(n, dtype=bool)
    i = 0
    while i < n:
        if not f[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and f[j + 1]:
            j += 1
        if (j - i + 1) >= int(max(1, min_run)):
            bad[i:j + 1] = True
        i = j + 1

    keep, cut = [], []
    i = 0
    while i < n:
        state = bad[i]
        j = i
        while j + 1 < n and bad[j + 1] == state:
            j += 1
        a, b = i, j
        # соседние куски делим по общей вершине, чтобы линия не рвалась
        if state:
            a = max(0, i - 1)
            b = min(n - 1, j + 1)
            if b > a:
                cut.append((a, b))
        else:
            if b > a:
                keep.append((a, b))
        i = j + 1
    return keep, cut


def line_confidence(drops, thr):
    """Сводка уверенности по линии: минимальный и средний перепад на ячейку."""
    d = np.asarray(drops, dtype=float)
    d = d[np.isfinite(d)]
    if d.size == 0:
        return None
    return {"drop_min": float(np.min(d)), "drop_mean": float(np.mean(d)),
            "n_low": int(np.sum(d < float(thr)))}
