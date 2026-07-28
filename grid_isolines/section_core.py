# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Ядро построения геологического разреза. Чистая математика и геометрия.

Модуль намеренно не импортирует QGIS и Qt. Ему на вход подают список вершин
линии, массивы поверхностей с геотрансформами и числовые параметры, на выход
он отдаёт готовые кольца полигонов, угловые точки, отметки осей, ячейки
таблицы и габариты чертежа в координатах «расстояние - высота».

Так ядро зовут двое: инструмент Processing 4.01 и оконная форма из меню.
Логика одна, дублирования нет. Тесты работают с ядром напрямую, без QGIS и
без копий помощников в файле теста.

Соглашение о координатах чертежа. Ось X - расстояние вдоль линии от начала,
ось Y - высота, умноженная на vex. Ноль чертежа лежит в начале линии на
нулевой отметке. Раскладка нескольких разрезов сдвигает готовые габариты,
самих координат внутри разреза не трогая.
"""
import math

import numpy as np


# --- выборка растра ------------------------------------------------------

def sample_grid_points(arr, gt, xs, ys, bilinear=True):
    """Выборка значений растра в точках (xs, ys). arr содержит nan на месте
    nodata. Возвращает массив z (nan вне покрытия). Билинейно или ближайший."""
    ny, nx = arr.shape
    fx = (xs - gt[0]) / gt[1] - 0.5
    fy = (ys - gt[3]) / gt[5] - 0.5

    def gather(ix, iy):
        out = np.full(len(xs), np.nan)
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        out[ok] = arr[iy[ok], ix[ok]]
        return out

    if not bilinear:
        return gather(np.round(fx).astype(int), np.round(fy).astype(int))
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    tx = fx - x0
    ty = fy - y0
    v00 = gather(x0, y0)
    v10 = gather(x0 + 1, y0)
    v01 = gather(x0, y0 + 1)
    v11 = gather(x0 + 1, y0 + 1)
    return (v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
            + v01 * (1 - tx) * ty + v11 * tx * ty)


def valid_runs(mask):
    """Непрерывные участки True длиной >= 2 точек: список (i0, i1) включительно."""
    out = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= 2:
                out.append((i, j - 1))
            i = j
        else:
            i += 1
    return out


# --- геометрия ломаной ---------------------------------------------------

def vertex_distances(vertices):
    """Накопленные расстояния по вершинам ломаной. Первая вершина - ноль."""
    dd = [0.0]
    for i in range(1, len(vertices)):
        dd.append(dd[-1] + math.hypot(vertices[i][0] - vertices[i - 1][0],
                                      vertices[i][1] - vertices[i - 1][1]))
    return dd


def polyline_length(vertices):
    """Длина ломаной по списку вершин."""
    if len(vertices) < 2:
        return 0.0
    return vertex_distances(vertices)[-1]


def segment_azimuths(vertices):
    """Азимуты отрезков ломаной в градусах от севера по часовой стрелке."""
    out = []
    for i in range(len(vertices) - 1):
        dx = vertices[i + 1][0] - vertices[i][0]
        dy = vertices[i + 1][1] - vertices[i][1]
        out.append((math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0)
    return out


def interpolate_polyline(vertices, dists):
    """Точки ломаной на заданных расстояниях от начала.

    Повторяет поведение QgsGeometry.interpolate для простой линии: до начала
    отдаёт первую вершину, за концом - последнюю. Нужна, чтобы ядро считало
    трассу без QGIS.
    """
    vd = vertex_distances(vertices)
    total = vd[-1] if vd else 0.0
    n = len(dists)
    xs = np.empty(n)
    ys = np.empty(n)
    # вершины отсортированы по расстоянию, поэтому ищем позицию поиском по
    # отсортированному массиву, а не перебором по каждому пикету
    idx = np.searchsorted(np.asarray(vd), np.asarray(dists, dtype=float),
                          side="right") - 1
    idx = np.clip(idx, 0, max(len(vertices) - 2, 0))
    for k in range(n):
        di = float(dists[k])
        if di <= 0.0 or total <= 0.0:
            xs[k], ys[k] = vertices[0][0], vertices[0][1]
            continue
        if di >= total:
            xs[k], ys[k] = vertices[-1][0], vertices[-1][1]
            continue
        i = int(idx[k])
        seg = vd[i + 1] - vd[i]
        t = 0.0 if seg <= 0 else (di - vd[i]) / seg
        xs[k] = vertices[i][0] + t * (vertices[i + 1][0] - vertices[i][0])
        ys[k] = vertices[i][1] + t * (vertices[i + 1][1] - vertices[i][1])
    return xs, ys


def stations(vertices, step):
    """Пикеты вдоль линии: равномерная сетка плюс вершины ломаной.

    Вершины обязаны быть пикетами, иначе равномерная сетка почти никогда не
    попадает в излом и профиль срезал бы угол хордой до полушага.
    """
    length = polyline_length(vertices)
    if length <= 0:
        return np.array([0.0])
    nseg = max(2, int(math.ceil(length / step)))
    d = np.linspace(0.0, length, nseg + 1)
    if len(vertices) > 2:
        vd = vertex_distances(vertices)
        d = np.unique(np.concatenate([d, np.array(vd)]))
        d = d[(d >= 0.0) & (d <= length)]
        tol = max(length * 1e-9, 1e-9)
        keep = np.concatenate([[True], np.diff(d) > tol])
        d = d[keep]
    return d


# --- вертикальный масштаб ------------------------------------------------

# режимы вертикального масштаба
VMODE_ASPECT = 0     # отношение габаритов чертежа, ширина:высота
VMODE_FACTOR = 1     # прямой множитель
VMODE_SCALES = 2     # отношение масштабов Г:В, запись 1:N


def vex_from_mode(mode, value, length, dz):
    """Множитель вертикального преувеличения по режиму и введённому значению.

    VMODE_ASPECT: значение это желаемое отношение ширины чертежа к высоте,
    множитель считается из длины линии и размаха высот.
    VMODE_FACTOR: значение это сам множитель.
    VMODE_SCALES: значение это N из записи Г:В = 1:N, то есть вертикальный
    масштаб крупнее горизонтального в N раз. Множитель равен N.
    """
    if mode == VMODE_ASPECT:
        if dz and dz > 0 and value > 0:
            return length / (value * dz)
        return 1.0
    return float(value) or 1.0


def scale_caption_parts(mode, value, vex):
    """Числа для подписи масштаба. Текст собирает вызывающая сторона, чтобы
    ядро не занималось переводом.

    Возвращает (kind, value, vex), где kind это 'aspect', 'factor' или
    'scales'.
    """
    kind = {VMODE_ASPECT: "aspect", VMODE_FACTOR: "factor",
            VMODE_SCALES: "scales"}.get(mode, "factor")
    return kind, float(value), float(vex)


# --- отметки осей --------------------------------------------------------

def nice_ticks(lo, hi, n):
    """Хорошо округлённые отметки между lo и hi. Шаг выбирается из ряда
    1, 2, 2.5, 5, 10 (×10^k) так, чтобы количество отметок было ближе всего к n."""
    if not (hi > lo) or n < 2:
        return []
    raw = (hi - lo) / (n - 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    best = None
    for f in (1, 2, 2.5, 5, 10):
        step = f * mag
        start = math.ceil(lo / step) * step
        cnt = int(math.floor((hi - start) / step + 1e-9)) + 1
        if cnt < 1:
            continue
        score = abs(cnt - n)
        if best is None or score < best[0]:
            best = (score, step)
    step = best[1]
    v = math.ceil(lo / step) * step
    ticks = []
    while v <= hi + step * 1e-6:
        ticks.append(round(v, 6))
        v += step
    return ticks


# --- кольца полос пластов ------------------------------------------------

def bed_ring_2d(d, ztop, zbot, vex, i0, i1):
    """Замкнутое кольцо полосы пласта в осях чертежа: верх слева направо,
    низ обратно."""
    idx = range(i0, i1 + 1)
    ridx = range(i1, i0 - 1, -1)
    ring = [(float(d[i]), float(ztop[i] * vex)) for i in idx]
    ring += [(float(d[i]), float(zbot[i] * vex)) for i in ridx]
    ring.append((ring[0][0], ring[0][1]))
    return ring


def bed_ring_3d(xs, ys, ztop, zbot, i0, i1):
    """Замкнутое кольцо вертикальной стенки в реальных координатах (x, y, z)."""
    idx = range(i0, i1 + 1)
    ridx = range(i1, i0 - 1, -1)
    pts = [(float(xs[i]), float(ys[i]), float(ztop[i])) for i in idx]
    pts += [(float(xs[i]), float(ys[i]), float(zbot[i])) for i in ridx]
    pts.append((pts[0][0], pts[0][1], pts[0][2]))
    return pts


def ring_area(ring):
    """Площадь замкнутого кольца по формуле шнуровки. Отрицательная площадь
    означает обратный обход, для проверки на вырожденность важен модуль."""
    s = 0.0
    for i in range(len(ring) - 1):
        s += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1]
    return abs(s) * 0.5


def ring_is_degenerate(ring):
    """Кольцо непригодно для полигона: мало точек, не-конечные координаты или
    нулевая площадь. Именно такие объекты давали на удалённых машинах эффект
    «атрибуты есть, геометрии не видно»."""
    if ring is None or len(ring) < 4:
        return True
    for pt in ring:
        for c in pt[:2]:
            if not math.isfinite(c):
                return True
    return ring_area(ring) <= 0.0


# --- таблица углов -------------------------------------------------------

# доли высоты рамки и длины линии для блока таблицы под чертежом
TABLE_GAP_FRAC = 0.06
TABLE_ROW_FRAC = 0.07
TABLE_LABEL_FRAC = 0.06
TABLE_ROWS = 2


def table_cells(corner_d, seg_az, ybot, ytop, length,
                labels=("d", "Аз")):
    """Ячейки таблицы под чертежом: слева столбец подписей, дальше по столбцу
    на каждый отрезок между углами. Верхняя строка - длина, нижняя - азимут.

    Возвращает список (text, ring), кольца замкнуты.
    """
    out = []
    n = len(corner_d)
    if n < 2:
        return out
    h = ytop - ybot
    gap = TABLE_GAP_FRAC * h
    rowh = TABLE_ROW_FRAC * h
    wlbl = TABLE_LABEL_FRAC * length if length > 0 else 1.0
    top = ybot - gap

    def cell(cx0, cx1, ry0, ry1, text):
        out.append((text, [(cx0, ry0), (cx1, ry0), (cx1, ry1),
                           (cx0, ry1), (cx0, ry0)]))

    for r, txt in enumerate(labels):
        cell(-wlbl, 0.0, top - (r + 1) * rowh, top - r * rowh, txt)
    for k in range(n - 1):
        x0 = float(corner_d[k])
        x1 = float(corner_d[k + 1])
        seglen = corner_d[k + 1] - corner_d[k]
        for r, val in ((0, "%.2f" % seglen), (1, "%.2f" % seg_az[k])):
            cell(x0, x1, top - (r + 1) * rowh, top - r * rowh, val)
    return out


def table_extent(ybot, ytop, length):
    """Габарит блока таблицы: левый край столбца подписей и нижняя граница."""
    h = ytop - ybot
    wlbl = TABLE_LABEL_FRAC * length if length > 0 else 1.0
    bottom = ybot - TABLE_GAP_FRAC * h - TABLE_ROWS * TABLE_ROW_FRAC * h
    return -wlbl, bottom


# --- сборка разреза ------------------------------------------------------

class SectionResult(object):
    """Готовый разрез в координатах чертежа. Простой контейнер без QGIS."""

    __slots__ = ("length", "step", "vex", "d", "xs", "ys", "zs",
                 "zmin", "zmax", "frame_zmin", "frame_zmax", "ytop", "ybot",
                 "beds", "corners", "seg_az", "ticks", "table",
                 "bbox_frame", "bbox_full", "n_degenerate")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))

    @property
    def width(self):
        return self.bbox_full[2] - self.bbox_full[0]

    @property
    def height(self):
        return self.bbox_full[3] - self.bbox_full[1]


class SectionSamples(object):
    """Профиль вдоль линии до применения вертикального масштаба.

    Выборка растров это самая дорогая часть, а общий вертикальный масштаб
    пакета известен только после того, как просмотрены все линии. Поэтому
    выборка отделена от сборки: сначала считаем профили, потом по ним
    выбираем единый vex, потом собираем чертежи без повторной выборки.
    """

    __slots__ = ("length", "step", "d", "xs", "ys", "zs",
                 "zmin", "zmax", "dz", "vertices", "names")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def sample_section(vertices, surfaces, step=0.0, bilinear=True):
    """Профиль вдоль линии: пикеты, координаты, отметки всех поверхностей."""
    if len(vertices) < 2:
        raise ValueError("нужно минимум две вершины линии")
    if len(surfaces) < 1:
        raise ValueError("нужна хотя бы одна поверхность")
    length = polyline_length(vertices)
    if length <= 0:
        raise ValueError("длина линии равна нулю")
    if step is None or step <= 0:
        cells = [abs(gt[1]) or 1.0 for (_a, gt, _n) in surfaces]
        step = min(cells) if cells else 1.0
    d = stations(vertices, step)
    xs, ys = interpolate_polyline(vertices, d)
    zs = [sample_grid_points(a, gt, xs, ys, bilinear)
          for (a, gt, _n) in surfaces]
    allz = np.concatenate(zs)
    if np.isfinite(allz).any():
        zmn = float(np.nanmin(allz))
        zmx = float(np.nanmax(allz))
        dz = zmx - zmn
    else:
        zmn, zmx, dz = 0.0, 1.0, 0.0
    return SectionSamples(length=length, step=step, d=d, xs=xs, ys=ys, zs=zs,
                          zmin=zmn, zmax=zmx, dz=dz, vertices=list(vertices),
                          names=[n for (_a, _g, n) in surfaces])


def common_vex(samples, mode, value):
    """Единый вертикальный масштаб на набор разрезов.

    Множитель и отношение масштабов Г:В не зависят от данных, поэтому общие по
    определению. В режиме отношения габаритов чертежа одним числом всем угодить
    нельзя, поэтому берём самую длинную линию и общий по набору размах высот:
    заданное отношение получает самый широкий чертёж, остальные выходят выше
    него, но в едином масштабе и потому сопоставимы.
    """
    if not samples:
        return 1.0
    if mode != VMODE_ASPECT:
        return vex_from_mode(mode, value, 0.0, 0.0)
    length = max(s.length for s in samples)
    zmn = min(s.zmin for s in samples)
    zmx = max(s.zmax for s in samples)
    return vex_from_mode(VMODE_ASPECT, value, length, max(zmx - zmn, 0.0))


def build_section(vertices, surfaces, step=0.0, vmode=VMODE_ASPECT,
                  vscale=10.0, bilinear=True, naxes=5, pad_frac=0.05,
                  vex=None, with_table=True, table_labels=("d", "Аз"),
                  samples=None, zbase=None):
    """Построить разрез по линии и стопке поверхностей сверху вниз.

    vertices: список (x, y) вершин линии.
    surfaces: список (arr, gt, name), arr с nan на месте nodata, порядок
        сверху вниз. N поверхностей дают N-1 пластов. Одна поверхность это
        законный случай: пластов нет, остаётся линия рельефа с рамкой, осями
        и определением разреза. Геологический разрез обычно с этого и
        начинается, а пласты появляются позже.
    zbase: отметка низа рамки. Пусто - рамка по данным. С одной поверхностью
        рамка иначе обжимает рельеф и рисовать под ним негде.
    step: шаг выборки в единицах карты, 0 или меньше - по минимальной ячейке.
    vex: готовый множитель. Если задан, vmode и vscale не используются. Так
        пакет из нескольких разрезов получает единый вертикальный масштаб.
    samples: готовый профиль от sample_section. Если задан, растры повторно не
        выбираются.

    Возвращает SectionResult. Исключений не бросает: пустой разрез виден по
    пустому списку beds, решение принимает вызывающая сторона.
    """
    sm = samples if samples is not None else sample_section(
        vertices, surfaces, step, bilinear)
    length, step = sm.length, sm.step
    d, xs, ys, zs = sm.d, sm.xs, sm.ys, sm.zs
    zmn, zmx, dz = sm.zmin, sm.zmax, sm.dz
    vertices = sm.vertices
    names = sm.names

    # Низ рамки задан вручную: опускаем нижнюю границу до него и пересчитываем
    # размах. Именно размах, а не только рамку: в режиме отношения габаритов
    # вертикальный масштаб считается по нему, и чертёж должен быть в масштабе
    # той рамки, которую человек увидит.
    if zbase is not None:
        zb = float(zbase)
        if zb < zmn:
            zmn = zb
            dz = zmx - zmn

    if vex is None:
        vex = vex_from_mode(vmode, vscale, length, dz)
    vex = float(vex) or 1.0

    pad = pad_frac * (zmx - zmn if zmx > zmn else 1.0)
    frame_zmin, frame_zmax = zmn - pad, zmx + pad
    ytop = frame_zmax * vex
    ybot = frame_zmin * vex

    # пласты: полосы между соседними поверхностями
    beds = []
    n_degenerate = 0
    for k in range(len(zs) - 1):
        ztop, zbot = zs[k], zs[k + 1]
        valid = np.isfinite(ztop) & np.isfinite(zbot) & (ztop > zbot)
        if not valid.any():
            continue
        t_mean = float(np.nanmean((ztop - zbot)[valid]))
        runs = []
        for (i0, i1) in valid_runs(valid):
            ring = bed_ring_2d(d, ztop, zbot, vex, i0, i1)
            bad = ring_is_degenerate(ring)
            if bad:
                n_degenerate += 1
            runs.append({"i0": i0, "i1": i1, "ring2d": ring,
                         "ring3d": bed_ring_3d(xs, ys, ztop, zbot, i0, i1),
                         "degenerate": bad})
        beds.append({"bed": k + 1, "top": names[k], "bot": names[k + 1],
                     "t_mean": t_mean, "runs": runs})

    # углы ломаной
    vd = vertex_distances(vertices)
    seg_az = segment_azimuths(vertices)
    corners = []
    for i in range(len(vertices)):
        az = seg_az[i] if i < len(seg_az) else (seg_az[-1] if seg_az else 0.0)
        corners.append({"num": i + 1, "d": round(vd[i], 2),
                        "x": round(vertices[i][0], 2),
                        "y": round(vertices[i][1], 2),
                        "az": round(az, 2)})

    ticks = nice_ticks(zmn, zmx, naxes or 5)
    table = table_cells(vd, seg_az, ybot, ytop, length, table_labels) \
        if with_table else []

    bbox_frame = (0.0, ybot, length, ytop)
    tx, tybot = table_extent(ybot, ytop, length)
    if with_table and len(vertices) >= 2:
        bbox_full = (tx, tybot, length, ytop)
    else:
        bbox_full = bbox_frame

    return SectionResult(
        length=length, step=step, vex=vex, d=d, xs=xs, ys=ys, zs=zs,
        zmin=zmn, zmax=zmx, frame_zmin=frame_zmin, frame_zmax=frame_zmax,
        ytop=ytop, ybot=ybot, beds=beds, corners=corners, seg_az=seg_az,
        ticks=ticks, table=table, bbox_frame=bbox_frame, bbox_full=bbox_full,
        n_degenerate=n_degenerate)


# --- порядок слоёв в группе дерева --------------------------------------

def order_key(raw):
    """Ключ сортировки слоя по номеру, записанному в свойство слоя.

    Слои с номером идут первыми по возрастанию номера. Слои без номера или с
    испорченным значением уходят в конец с одинаковым ключом, а поскольку
    сортировка устойчивая, их взаимный порядок сохраняется. Так чужие слои и
    выходы прошлых прогонов не перемешиваются между собой.
    """
    try:
        return (0, int(raw))
    except (TypeError, ValueError):
        return (1, 0)


def order_sorted(items, raw_of=lambda x: x):
    """Устойчивая сортировка по order_key. raw_of достаёт номер из элемента."""
    return sorted(items, key=lambda it: order_key(raw_of(it)))


# --- раскладка нескольких разрезов --------------------------------------

LAYOUT_STACK = 0     # стопкой сверху вниз, общий ноль расстояний
LAYOUT_ROW = 1       # в ряд слева направо, общая отметка высоты
LAYOUT_GRID = 2      # сеткой, число столбцов задаётся


def layout_offsets(bboxes, mode=LAYOUT_STACK, ncols=2, gap_frac=0.15):
    """Смещения габаритов на общем чертеже.

    Правило простое: раскладка идёт по регулярной решётке с шагом, взятым от
    самого крупного габарита плюс зазор. Пересечений нет по построению, а
    колонки и строки выровнены, чего не даёт укладка вплотную к предыдущему.

    Общий ноль сохраняется по поперечной оси. Стопкой все разрезы стоят левым
    краем на нуле расстояний, в ряд у всех совпадает отметка высоты. В сетке
    строка сохраняет общую отметку высоты, столбец - общий ноль расстояний.

    Первый разрез всегда получает нулевое смещение, поэтому одиночный прогон
    совпадает с прежним поведением до координаты.
    """
    n = len(bboxes)
    if n == 0:
        return []
    widths = [b[2] - b[0] for b in bboxes]
    heights = [b[3] - b[1] for b in bboxes]
    maxw = max(widths) if widths else 0.0
    maxh = max(heights) if heights else 0.0
    gap_x = gap_frac * maxw
    gap_y = gap_frac * maxh
    pitch_x = maxw + gap_x
    pitch_y = maxh + gap_y

    if mode == LAYOUT_ROW:
        cols = n
    elif mode == LAYOUT_GRID:
        cols = max(1, int(ncols))
    else:
        cols = 1

    out = []
    for i in range(n):
        row = i // cols
        col = i % cols
        out.append((col * pitch_x, -row * pitch_y))
    return out


def offset_bbox(bbox, off):
    """Габарит со смещением."""
    return (bbox[0] + off[0], bbox[1] + off[1],
            bbox[2] + off[0], bbox[3] + off[1])


def union_bbox(bboxes):
    """Общий габарит набора. Пустой набор даёт None."""
    if not bboxes:
        return None
    return (min(b[0] for b in bboxes), min(b[1] for b in bboxes),
            max(b[2] for b in bboxes), max(b[3] for b in bboxes))


def merge_field_names(per_layer, reserved=()):
    """Сводит поля нескольких слоёв в один общий набор.

    Инструмент пересечения принимает сразу несколько слоёв, и схемы у них
    разные. Чтобы вынести атрибуты на разрез, нужен один набор колонок на
    все слои: поле, которого у слоя нет, останется пустым.

    Имена, совпадающие со служебными (sec, d, label и прочие), переименовываются
    с суффиксом: иначе атрибут объекта затрёт координату разреза, и человек
    этого не заметит. Совпадение имён между слоями считается одним полем, тип
    берётся от первого слоя - разнотипицу тут разрешить нельзя, а терять
    данные хуже, чем сложить их в одну колонку.

    per_layer: список списков имён, по одному на слой.
    reserved:  имена, которые занимать нельзя.

    Возвращает (names, maps), где names это итоговый список колонок, а maps -
    по списку на слой: maps[i][k] это индекс поля слоя i для колонки k, либо
    -1, если у слоя такого поля нет.
    """
    taken = {str(r).lower() for r in reserved}
    names = []
    origin = []            # исходное имя для каждой колонки
    seen = {}              # исходное имя -> индекс колонки
    for src in per_layer:
        for nm in src:
            key = str(nm)
            if key in seen:
                continue
            out = key
            n = 1
            while out.lower() in taken:
                n += 1
                out = "%s_%d" % (key, n)
            taken.add(out.lower())
            seen[key] = len(names)
            names.append(out)
            origin.append(key)
    maps = []
    for src in per_layer:
        idx = {str(nm): k for k, nm in enumerate(src)}
        maps.append([idx.get(o, -1) for o in origin])
    return names, maps


def profile_from_lines(parts, keep_high=True):
    """Сводит линии на чертеже в одну возрастающую по X ломаную.

    Линии обрезки приходят слоем, и частей может быть несколько: разрывы по
    отсутствию данных, отдельные объекты на каждый разрез. Для обрезки нужен
    один профиль, по которому можно спросить высоту в любой станции.

    Совпадающие X встречаются на вертикальных участках. Для верхней кромки
    берётся наибольшая высота, для нижней наименьшая: срезать лишнее хуже,
    чем оставить лишнее видимым. Тем же правилом слой из нескольких
    поверхностей сводится к огибающей, верхней или нижней.
    """
    pts = []
    for xs, ys in parts:
        for x, y in zip(xs, ys):
            if x == x and y == y:
                pts.append((float(x), float(y)))
    if not pts:
        return None
    pts.sort()
    ox, oy = [], []
    for x, y in pts:
        if ox and abs(x - ox[-1]) <= 1e-9:
            if (y > oy[-1]) if keep_high else (y < oy[-1]):
                oy[-1] = y
            continue
        ox.append(x)
        oy.append(y)
    return np.asarray(ox, dtype=float), np.asarray(oy, dtype=float)


def profile_y_at(prof, x):
    """Высота профиля в станции x. За краями держится крайнее значение."""
    if prof is None:
        return None
    px, py = prof
    if px.size == 0:
        return None
    return float(np.interp(float(x), px, py))


def band_nodes(profs, x1, x2):
    """Станции, в которых считаются кромки полосы.

    Концы отрезка плюс узлы всех поданных ломаных внутри него. Набор общий
    на верх и низ, иначе кромки, посчитанные каждая по своим станциям,
    пересекались бы между узлами и кольцо завязалось бы бантиком.
    """
    x1, x2 = float(min(x1, x2)), float(max(x1, x2))
    inner = set()
    for prof in profs:
        if prof is None:
            continue
        for v in prof[0]:
            v = float(v)
            if x1 < v < x2:
                inner.add(v)
    return [x1] + sorted(inner) + [x2]


def band_ring(bot, top):
    """Кольцо полосы: низ слева направо, верх обратно, замыкание в начало."""
    ring = [(float(x), float(y)) for x, y in bot]
    ring += [(float(x), float(y)) for x, y in reversed(top)]
    ring.append(ring[0])
    return ring


def clamp_below(bot, top):
    """Низ не поднимается выше верха.

    Там, где линия низа прошла над рельефом, полоса схлопывается в ноль, а не
    выворачивается: кольцо с перехлёстом кромок дало бы бантик вместо полосы.
    Кромки обязаны стоять в одних станциях, их даёт band_nodes.
    """
    return [(x, min(y, ty)) for (x, y), (_tx, ty) in zip(bot, top)]


def band_is_flat(bot, top, tol=1e-9):
    """Полоса схлопнулась целиком: везде низ дошёл до верха."""
    return all(ty - y <= tol for (_x, y), (_tx, ty) in zip(bot, top))


def edge_along(prof, xs, ylim, upper=True):
    """Кромка полосы по ломаной, зажатая уровнем рамки.

    Верхняя кромка идёт по профилю там, где он ниже уровня, нижняя там, где
    он выше. Без профиля кромка остаётся прямой по уровню, то есть поведение
    прежнее. Возвращает список точек слева направо.
    """
    if prof is None:
        return [(float(x), float(ylim)) for x in xs]
    out = []
    for x in xs:
        y = profile_y_at(prof, x)
        out.append((float(x), float(min(ylim, y) if upper else max(ylim, y))))
    return out
