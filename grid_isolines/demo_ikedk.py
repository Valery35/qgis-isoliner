# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Демо-набор к 3.01 и 3.02 (3.09). Чистый NumPy, без QGIS.

Синтетический участок калийного пласта, где истинные поля известны
заранее, поэтому ошибку каждого метода видно прямо на карте.

- Маркирующий горизонт известен всюду (растр). Это дрейф для 3.02: в
  жизни его дают сейсмика, плотная старая сеть или соседний пласт.
- Кровля пласта повторяет маркирующий горизонт на расстоянии около 25 м,
  плюс небольшие местные отклонения. Скважины знают кровлю только в
  своих точках.
- Минеральный тип пласта: сильвинит, частичное и полное замещение.
  Зоны замещения вытянуты, как на руднике, полное замещение лежит
  внутри частичного.
- Сеть скважин неравномерная, и в ней можно оставить разрыв над
  складкой маркирующего горизонта. В разрыве обычный кригинг теряет
  складку, а кригинг с дрейфом её воспроизводит.

Всё детерминировано зерном.
"""
import math

import numpy as np

CLASSES = ("сильвинит", "частичное замещение", "полное замещение")
CLASSES_EN = ("sylvinite", "partial replacement", "full replacement")
# Доли классов по площади: порог по квантилям поля замещения.
SHARE_PARTIAL = 0.22
SHARE_FULL = 0.10
ROOF_OFFSET = -25.0           # кровля ниже маркирующего горизонта, м
ROOF_RESID = 1.2              # СКО местных отклонений кровли, м
GRID_N = 200                  # ячеек по длинной стороне истинных полей
# Радиус корреляции поля замещения в долях GRID_N. При 0.07 на охвате 6 км
# зона шириной около 400 м и длиной 1.2 км, это близко к шагу сети. При
# 0.05 зоны уже шага сети, скважины в них почти не попадают, и 3.01
# находит меньше половины полного замещения.
ZONE_CORR = 0.07


def smooth_field(shape, corr_cells, rng, aniso=1.0, angle_deg=0.0):
    """Гладкое случайное поле с нулевым средним и единичным СКО.

    Белый шум сглаживается гауссовым ядром в частотной области. Радиус
    корреляции corr_cells в ячейках, aniso растягивает поле вдоль
    направления angle_deg (от оси x против часовой стрелки)."""
    ny, nx = shape
    noise = rng.standard_normal((ny, nx))
    ky = np.fft.fftfreq(ny)[:, None]
    kx = np.fft.fftfreq(nx)[None, :]
    a = math.radians(angle_deg)
    ku = kx * math.cos(a) + ky * math.sin(a)          # вдоль вытянутости
    kv = -kx * math.sin(a) + ky * math.cos(a)
    s = float(corr_cells)
    kern = np.exp(-2.0 * (math.pi * s) ** 2 * ((ku * aniso) ** 2 + kv ** 2))
    f = np.real(np.fft.ifft2(np.fft.fft2(noise) * kern))
    f -= f.mean()
    sd = f.std()
    return f / sd if sd > 0 else f


def grid_spec(xmin, ymin, xmax, ymax, n=GRID_N):
    """Сетка истинных полей: (cell, nx, ny) по длинной стороне охвата.

    Число ячеек округляется вверх, от левого нижнего угла, как у гридов
    группы 1 и 3. Тогда грид кригинга с той же ячейкой и охватом ложится
    на истинные поля ячейка в ячейку, и растр дрейфа покрывает его весь."""
    w, h = float(xmax - xmin), float(ymax - ymin)
    cell = max(w, h) / float(n)
    nx = max(int(math.ceil(w / cell - 1e-9)), 8)
    ny = max(int(math.ceil(h / cell - 1e-9)), 8)
    return cell, nx, ny


def _wells(rng, xmin, ymin, w, h, n, gap):
    """Сеть скважин: разреженная решётка с разбросом плюс сгущение на
    участке отработки. Скважины внутри разрыва (эллипс gap) убираются."""
    side = math.sqrt(w * h / max(n * 0.75, 1.0))
    xs, ys = [], []
    gx = np.arange(xmin + side / 2.0, xmin + w, side)
    gy = np.arange(ymin + side / 2.0, ymin + h, side)
    for y in gy:
        for x in gx:
            xs.append(x + rng.uniform(-0.3, 0.3) * side)
            ys.append(y + rng.uniform(-0.3, 0.3) * side)
    # сгущение: участок, разбуренный плотнее (поле отработки)
    cx, cy = xmin + 0.25 * w, ymin + 0.3 * h
    k = max(n - len(xs), int(0.25 * n))
    xs.extend(cx + rng.normal(0.0, 0.07 * w, k))
    ys.extend(cy + rng.normal(0.0, 0.07 * h, k))
    xs = np.clip(np.array(xs), xmin + 0.01 * w, xmin + 0.99 * w)
    ys = np.clip(np.array(ys), ymin + 0.01 * h, ymin + 0.99 * h)
    if gap is not None:
        gx0, gy0, ga, gb = gap
        inside = ((xs - gx0) / ga) ** 2 + ((ys - gy0) / gb) ** 2 <= 1.0
        xs, ys = xs[~inside], ys[~inside]
    return xs, ys


def _sample(grid, xmin, ymax, cell, xs, ys):
    """Значение грида (строки сверху вниз) в точках, ближайшая ячейка."""
    ny, nx = grid.shape
    c = np.clip(((xs - xmin) / cell).astype(int), 0, nx - 1)
    r = np.clip(((ymax - ys) / cell).astype(int), 0, ny - 1)
    return grid[r, c]


def make_dataset(xmin, ymin, xmax, ymax, n_wells=80, gap=True, seed=1):
    """Набор для 3.09.

    Возвращает словарь:
    gt - геотрансформ GDAL истинных растров (строки сверху вниз);
    marker - маркирующий горизонт, м (дрейф);
    roof - истинная кровля, м;
    classes - истинный код класса 0, 1, 2 (индекс в CLASSES);
    wells - список (x, y, имя, кровля, код класса);
    gap - (cx, cy, a, b) эллипс разрыва сети или None;
    stats - сводка для журнала."""
    rng = np.random.default_rng(int(seed) if seed else None)
    w, h = float(xmax - xmin), float(ymax - ymin)
    cell, nx, ny = grid_spec(xmin, ymin, xmax, ymax)
    xc = xmin + (np.arange(nx) + 0.5) * cell
    yc = ymin + ny * cell - (np.arange(ny) + 0.5) * cell
    X, Y = np.meshgrid((xc - xmin) / w, (yc - ymin) / h)

    # Маркирующий горизонт: региональное падение, пологая складка поперёк
    # участка и мелкие формы.
    regional = -150.0 - 18.0 * X + 6.0 * Y
    fold_cx, fold_cy = 0.68, 0.62
    fold = 9.0 * np.exp(-(((X - fold_cx) / 0.10) ** 2
                          + ((Y - fold_cy) / 0.22) ** 2))
    trough = -6.0 * np.exp(-(((X - 0.45) / 0.20) ** 2
                             + ((Y - 0.25) / 0.08) ** 2))
    small = 2.5 * smooth_field((ny, nx), 0.06 * GRID_N, rng)
    marker = regional + fold + trough + small

    resid = ROOF_RESID * smooth_field((ny, nx), 0.10 * GRID_N, rng)
    roof = marker + ROOF_OFFSET + resid

    # Поле замещения: вытянутые зоны, полное замещение внутри частичного.
    q = smooth_field((ny, nx), ZONE_CORR * GRID_N, rng, aniso=3.0,
                     angle_deg=35.0)
    t_partial = np.quantile(q, 1.0 - SHARE_PARTIAL - SHARE_FULL)
    t_full = np.quantile(q, 1.0 - SHARE_FULL)
    classes = np.zeros(q.shape, dtype=np.int32)
    classes[q > t_partial] = 1
    classes[q > t_full] = 2

    gap_ell = None
    if gap:
        gap_ell = (xmin + fold_cx * w, ymin + fold_cy * h, 0.16 * w, 0.27 * h)
    xs, ys = _wells(rng, xmin, ymin, w, h, int(n_wells), gap_ell)
    ymax_g = ymin + ny * cell
    roof_w = _sample(roof, xmin, ymax_g, cell, xs, ys)
    cls_w = _sample(classes, xmin, ymax_g, cell, xs, ys)
    wells = []
    for k in range(len(xs)):
        wells.append((float(xs[k]), float(ys[k]), "%d" % (101 + k),
                      round(float(roof_w[k]), 2), int(cls_w[k])))

    shares = [float((classes == i).mean()) for i in range(3)]
    in_wells = [int((cls_w == i).sum()) for i in range(3)]
    r = np.corrcoef(_sample(marker, xmin, ymax_g, cell, xs, ys), roof_w)[0, 1]
    stats = {"n_wells": len(wells), "cell": cell, "nx": nx, "ny": ny,
             "shares": shares, "in_wells": in_wells,
             "roof_min": float(roof.min()), "roof_max": float(roof.max()),
             "corr": float(r), "fold_amp": 9.0}
    gt = (xmin, cell, 0.0, ymax_g, 0.0, -cell)
    return {"gt": gt, "marker": marker, "roof": roof, "classes": classes,
            "wells": wells, "gap": gap_ell, "stats": stats}


def ellipse_ring(cx, cy, a, b, n=72):
    """Замкнутое кольцо эллипса для контура разрыва."""
    pts = [(cx + a * math.cos(2 * math.pi * k / n),
            cy + b * math.sin(2 * math.pi * k / n)) for k in range(n)]
    return pts + [pts[0]]


def rmse(est, truth, mask=None):
    """Среднеквадратичная ошибка по общим значимым ячейкам."""
    est = np.asarray(est, dtype=float)
    truth = np.asarray(truth, dtype=float)
    ok = np.isfinite(est) & np.isfinite(truth)
    if mask is not None:
        ok &= np.asarray(mask, dtype=bool)
    if not ok.any():
        return float("nan")
    return float(np.sqrt(np.mean((est[ok] - truth[ok]) ** 2)))
