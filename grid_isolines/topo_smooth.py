# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Сглаживание рельефа с сохранением структурных линий (FPDEMS) на
чистом NumPy. Метод Линдсея и др. (2019): убирает избыточную
шероховатость спутниковых ЦМР, не заваливая бровки, стенки террас и
берега рек.

Lindsay J.B., Francioni A., Cockburn J.M.H. (2019). LiDAR DEM
Smoothing and the Preservation of Drainage Features. Remote Sensing,
11(16), 1926. https://doi.org/10.3390/rs11161926

Идея. Обычные фильтры (среднее, медиана, Гаусс) размывают всё
подряд. FPDEMS работает не с высотами, а с полем нормалей
поверхности. Три шага. Первый: для каждой ячейки строим 3D-нормаль
локальной плоскости. Второй: сглаживаем поле нормалей билатеральным
фильтром, где вес соседа тем больше, чем ближе его нормаль к нормали
центра. На бровке нормали по сторонам разные, поэтому сглаживание
через бровку подавлено, грань сохраняется. Третий: итеративно
подгоняем высоты так, чтобы их градиент соответствовал сглаженному
полю нормалей.
"""

import numpy as np

DEFAULT_NORM_ITERS = 5      # проходов сглаживания поля нормалей
DEFAULT_ELEV_ITERS = 2      # проходов подгонки высот
DEFAULT_FILTER_SIZE = 11    # сторона окна фильтра нормалей, ячеек
DEFAULT_NORM_DIFF = 15.0    # порог различия нормалей, градусы


def _surface_normals(z, cell):
    """Единичные нормали локальной плоскости в каждой ячейке.

    Наклон плоскости через центральные разности (как в Horn, но по
    двум соседям). Возвращает (nx, ny, nz), нормаль направлена вверх.
    """
    dzdx = np.zeros_like(z)
    dzdy = np.zeros_like(z)
    dzdx[:, 1:-1] = (z[:, 2:] - z[:, :-2]) / (2.0 * cell)
    dzdx[:, 0] = (z[:, 1] - z[:, 0]) / cell
    dzdx[:, -1] = (z[:, -1] - z[:, -2]) / cell
    dzdy[1:-1, :] = (z[:-2, :] - z[2:, :]) / (2.0 * cell)
    dzdy[0, :] = (z[0, :] - z[1, :]) / cell
    dzdy[-1, :] = (z[-2, :] - z[-1, :]) / cell
    # ограничим наклон, чтобы вертикальные стенки не давали
    # вырожденную нормаль (nz->0) и переполнение при фильтрации
    max_slope = 10.0
    dzdx = np.clip(dzdx, -max_slope, max_slope)
    dzdy = np.clip(dzdy, -max_slope, max_slope)
    # нормаль к поверхности z=f(x,y): (-dz/dx, -dz/dy, 1), нормируем
    nx = -dzdx
    ny = -dzdy
    nz = np.ones_like(z)
    norm = np.sqrt(nx * nx + ny * ny + nz * nz)
    return nx / norm, ny / norm, nz / norm


def _smooth_normals(nx, ny, nz, mask, iters, half, cos_thresh):
    """Билатеральное сглаживание поля нормалей, разделимое по осям.

    Полный перебор окна (2half+1)^2 дорог. Приближаем разделимым
    проходом: сначала вдоль строк, затем вдоль столбцов. Вес соседа
    ненулевой, только если его нормаль ближе порога (скалярное
    произведение выше cos_thresh), поэтому грани сохраняются. Для
    билатерального фильтра разделимость - стандартное быстрое
    приближение.
    """
    valid = mask.astype(np.float64)

    def _pass_axis(nx, ny, nz, axis):
        ax = nx * valid
        ay = ny * valid
        az = nz * valid
        wsum = valid.copy()
        for d in range(1, half + 1):
            for sign in (d, -d):
                dr, dc = (sign, 0) if axis == 0 else (0, sign)
                snx = _shift(nx, dr, dc)
                sny = _shift(ny, dr, dc)
                snz = _shift(nz, dr, dc)
                sm = _shift(valid, dr, dc)
                dot = nx * snx + ny * sny + nz * snz
                w = sm * (dot >= cos_thresh)
                ax += snx * w
                ay += sny * w
                az += snz * w
                wsum += w
        good = wsum > 0
        inv = np.where(good, 1.0 / np.maximum(wsum, 1e-9), 0.0)
        ox = np.where(good, ax * inv, nx)
        oy = np.where(good, ay * inv, ny)
        oz = np.where(good, az * inv, nz)
        norm = np.maximum(np.sqrt(ox * ox + oy * oy + oz * oz), 1e-9)
        return ox / norm, oy / norm, oz / norm

    for _ in range(iters):
        nx, ny, nz = _pass_axis(nx, ny, nz, 0)
        nx, ny, nz = _pass_axis(nx, ny, nz, 1)
    return nx, ny, nz


def _shift(a, dr, dc):
    """Значение соседа (r+dr, c+dc) в позиции (r, c), край дублируется.

    Корректно для любого смещения: используем np.roll с последующей
    починкой краёв копией граничной линии на всю сдвинутую полосу.
    """
    out = np.roll(a, (-dr, -dc), axis=(0, 1))
    if dr > 0:
        out[-dr:, :] = a[-1:, :]
    elif dr < 0:
        out[:-dr, :] = a[:1, :]
    if dc > 0:
        out[:, -dc:] = out[:, -dc - 1:-dc]
    elif dc < 0:
        out[:, :-dc] = out[:, -dc:-dc + 1]
    return out


def _update_elevations(z, nx, ny, nz, cell, mask, strength=0.5):
    """Один устойчивый шаг подгонки высот под поле нормалей.

    Читает только исходные высоты z (не свои обновления), поэтому не
    расходится. Для каждого соседа предсказание высоты центра = высота
    соседа плюс перепад по среднему целевому наклону между ними.
    Высота сдвигается к среднему предсказаний с коэффициентом strength.
    Осью x считаем столбцы (растёт вправо), осью y строки (север
    сверху, y убывает вниз).
    """
    nz_safe = np.where(np.abs(nz) > 0.15, nz, np.sign(nz) * 0.15 + 1e-9)
    max_slope = 10.0
    gx = np.clip(-nx / nz_safe, -max_slope, max_slope)
    gy = np.clip(-ny / nz_safe, -max_slope, max_slope)
    acc = np.zeros_like(z)
    cnt = np.zeros_like(z)
    for dr, dc in ((0, -1), (0, 1), (-1, 0), (1, 0)):
        zn = _shift(z, dr, dc)
        gxn = _shift(gx, dr, dc)
        gyn = _shift(gy, dr, dc)
        mn = _shift(mask.astype(np.float64), dr, dc)
        dx = (-dc) * cell
        dy = (dr) * cell
        step = dx * 0.5 * (gx + gxn) + dy * 0.5 * (gy + gyn)
        acc += (zn + step) * mn
        cnt += mn
    pred = np.where(cnt > 0, acc / np.maximum(cnt, 1e-9), z)
    return np.where(mask, z + strength * (pred - z), z)


def smooth_fpdems(z, cell, nodata_mask=None,
                  norm_iters=DEFAULT_NORM_ITERS,
                  elev_iters=DEFAULT_ELEV_ITERS,
                  filter_size=DEFAULT_FILTER_SIZE,
                  norm_diff_deg=DEFAULT_NORM_DIFF,
                  feedback=None):
    """Сгладить ЦМР методом FPDEMS.

    z: массив высот float. cell: размер ячейки, м. nodata_mask:
    булев массив недействительных ячеек (не участвуют и не меняются).
    norm_iters: проходов сглаживания нормалей внутри каждого шага.
    elev_iters: число внешних шагов пересборки высот (2-5 обычно
    достаточно). filter_size: сторона окна нормалей (нечётная).
    norm_diff_deg: порог различия нормалей в градусах, меньше -
    агрессивнее сохраняет грани, больше - сильнее гладит.

    Возвращает сглаженный массив float64. Бровки, тальвеги и берега
    сохраняются, плоскости выглаживаются.
    """
    z = np.asarray(z, dtype=np.float64)
    if nodata_mask is None:
        nodata_mask = ~np.isfinite(z)
    mask = ~nodata_mask
    if not mask.any():
        return z.copy()
    half = max(1, int(filter_size) // 2)
    cos_thresh = float(np.cos(np.radians(norm_diff_deg)))

    out = z.copy()
    if nodata_mask.any():
        out[nodata_mask] = float(np.mean(z[mask]))

    steps = max(1, int(elev_iters))
    for k in range(steps):
        nx, ny, nz = _surface_normals(out, cell)
        nx, ny, nz = _smooth_normals(nx, ny, nz, mask, norm_iters, half,
                                     cos_thresh)
        out = _update_elevations(out, nx, ny, nz, cell, mask, strength=0.5)
        if feedback is not None:
            if feedback.isCanceled():
                break
            feedback.pushInfo("FPDEMS: шаг %d из %d" % (k + 1, steps))
    out[nodata_mask] = z[nodata_mask]
    return out


# ---------------------------------------------------------------------------
#  Сглаживание с ограничением: лечение террасинга без потери горизонталей
# ---------------------------------------------------------------------------

def _fill_odd_reflect(buf, z):
    """Заполнить рамку буфера нечётным отражением, продолжая наклон линейно.

    Повтор крайнего значения загибал бы линейный склон у границы растра, и
    загиб диффундировал бы внутрь с каждой итерацией.
    """
    buf[1:-1, 1:-1] = z
    buf[0, 1:-1] = 2.0 * z[0, :] - z[1, :] if z.shape[0] > 1 else z[0, :]
    buf[-1, 1:-1] = 2.0 * z[-1, :] - z[-2, :] if z.shape[0] > 1 else z[-1, :]
    buf[1:-1, 0] = 2.0 * z[:, 0] - z[:, 1] if z.shape[1] > 1 else z[:, 0]
    buf[1:-1, -1] = 2.0 * z[:, -1] - z[:, -2] if z.shape[1] > 1 else z[:, -1]
    buf[0, 0] = buf[0, 1]
    buf[0, -1] = buf[0, -2]
    buf[-1, 0] = buf[-1, 1]
    buf[-1, -1] = buf[-1, -2]


def smooth_clamped(z, interval, iters=50, band=0.5, nodata_mask=None):
    """Убрать ступени, не сдвинув горизонтали.

    Поверхность сглаживается итеративно, но каждой точке запрещено уходить от
    исходного значения дальше band долей сечения. По умолчанию это половина
    сечения, то есть ровно ошибка квантования: поверхность, построенная по
    горизонталям с шагом interval, и так известна с этой точностью.

    Отсюда два свойства, ради которых всё и делается. Метод не может выдумать
    формы тоньше, чем позволяет исходное сечение, потому что размах правки
    ограничен сверху. И он не может перекинуть точку через горизонталь: сдвиг
    меньше половины шага между уровнями.

    Чего метод не делает: он не возвращает то, чего в данных не было. Если
    овраг срезан при построении рельефа, сглаживание его не восстановит.

    Считается в float32 с центрированием, веса соседей и буферы заводятся один
    раз на весь прогон. Матрицы в десятки миллионов ячеек тут обычное дело, и
    прямолинейная реализация с выделением памяти на каждой итерации доходила
    до двух гигабайт.

    Параметры:
        z         - двумерный массив отметок
        interval  - сечение рельефа, по которому строился рельеф
        iters     - число итераций, больше значит глаже
        band      - допустимый сдвиг в долях сечения
        nodata_mask - True там, где данных нет

    Возвращает новый массив, вход не меняется.
    """
    z = np.asarray(z)
    if not interval or interval <= 0:
        raise ValueError("interval must be positive")
    iters = int(max(0, iters))
    if iters == 0:
        return np.array(z, dtype=float, copy=True)

    valid = np.isfinite(z)
    if nodata_mask is not None:
        valid &= ~np.asarray(nodata_mask, dtype=bool)
    if not valid.any():
        return np.array(z, dtype=float, copy=True)

    off = np.float32(float(np.mean(np.asarray(z)[valid])))
    cur = np.where(valid, np.subtract(z, off, dtype=np.float32),
                   np.float32(0.0)).astype(np.float32, copy=False)
    z0 = cur.copy()
    delta = np.float32(float(band) * float(interval))
    lo = np.subtract(z0, delta)
    hi = np.add(z0, delta)
    del z0

    ny, nx = cur.shape
    buf = np.empty((ny + 2, nx + 2), dtype=np.float32)
    acc = np.empty_like(cur)

    # веса от маски считаются один раз: маска по ходу не меняется
    vv = valid.astype(np.float32)
    _fill_odd_reflect(buf, vv)
    w = (buf[:-2, 1:-1] + buf[2:, 1:-1] + buf[1:-1, :-2] + buf[1:-1, 2:]
         + np.float32(4.0) * vv)
    np.maximum(w, np.float32(1e-12), out=w)
    del vv

    for _ in range(iters):
        np.multiply(cur, valid, out=cur)
        _fill_odd_reflect(buf, cur)
        np.add(buf[:-2, 1:-1], buf[2:, 1:-1], out=acc)
        np.add(acc, buf[1:-1, :-2], out=acc)
        np.add(acc, buf[1:-1, 2:], out=acc)
        np.add(acc, np.float32(4.0) * cur, out=acc)
        np.divide(acc, w, out=acc)
        np.clip(acc, lo, hi, out=acc)
        cur, acc = acc, cur

    out = np.asarray(z, dtype=float).copy()
    out[valid] = cur[valid].astype(float) + float(off)
    return out
