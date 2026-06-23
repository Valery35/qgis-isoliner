# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Smoke-тесты гидрогеологического модуля hydro (градиент напора, направление
# потока). Модуль не зависит от QGIS, запускается напрямую:
#     python grid_isolines/tests/test_hydro.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hydro  # noqa: E402

NODATA = -9999.0


def _plane(slope_x, slope_y, nx=20, ny=20, cell=10.0):
    """Растр напора как наклонная плоскость h = h0 + sx*x + sy*y.
    Строка 0 - север (y максимум), как в GeoTransform с dy<0."""
    ox, oy = 0.0, ny * cell
    cols = np.arange(nx)
    rows = np.arange(ny)
    X = ox + (cols + 0.5) * cell
    Y = oy + (rows + 0.5) * (-cell)
    XX, YY = np.meshgrid(X, Y)                # (ny, nx)
    h = 100.0 + slope_x * XX + slope_y * YY
    gt = (ox, cell, 0.0, oy, 0.0, -cell)
    return h, gt, cell


def test_gradient_magnitude_of_plane():
    """На плоскости с уклоном модуль градиента равен заданному уклону."""
    h, gt, cell = _plane(0.02, 0.0)          # напор растёт на восток
    mag, az = hydro.head_gradient(h, cell, cell, NODATA)
    m = mag[mag != NODATA]
    assert np.allclose(m, 0.02, atol=1e-6)


def test_flow_direction_down_gradient():
    """Напор растёт на восток -> поток течёт на запад (азимут ~270°)."""
    h, gt, cell = _plane(0.02, 0.0, nx=20, ny=20)
    mag, az = hydro.head_gradient(h, cell, cell, NODATA)
    a = az[az != NODATA]
    assert np.allclose(a, 270.0, atol=1e-6)


def test_flow_direction_north_south():
    """Напор растёт на север -> поток на юг (азимут 180°). И наоборот."""
    h, gt, cell = _plane(0.0, 0.03)          # h растёт с ростом y (на север)
    mag, az = hydro.head_gradient(h, cell, cell, NODATA)
    a = az[az != NODATA]
    assert np.allclose(a, 180.0, atol=1e-6)
    h2, _, _ = _plane(0.0, -0.03)            # напор растёт на юг -> поток на север
    _, az2 = hydro.head_gradient(h2, cell, cell, NODATA)
    a2 = az2[az2 != NODATA]
    assert np.allclose(a2, 0.0, atol=1e-6)


def test_diagonal_azimuth():
    """Уклон на северо-восток (h растёт на В и С) -> поток на юго-запад (225°)."""
    h, gt, cell = _plane(0.02, 0.02)
    mag, az = hydro.head_gradient(h, cell, cell, NODATA)
    a = az[az != NODATA]
    assert np.allclose(a, 225.0, atol=1e-6)
    m = mag[mag != NODATA]
    assert np.allclose(m, np.hypot(0.02, 0.02), atol=1e-6)


def test_flat_field_is_nodata_azimuth():
    """Плоское поле: градиент 0, направление не определено (nodata)."""
    h = np.full((10, 10), 50.0)
    mag, az = hydro.head_gradient(h, 10.0, 10.0, NODATA)
    assert np.allclose(mag[mag != NODATA], 0.0)
    assert np.all(az == NODATA)              # направление везде не определено


def test_nodata_propagates():
    """Ячейки nodata в исходнике дают nodata в производных растрах."""
    h, gt, cell = _plane(0.02, 0.0, nx=12, ny=12)
    h[0, 0] = NODATA                          # выбиваем угол
    mag, az = hydro.head_gradient(h, cell, cell, NODATA)
    assert mag[0, 0] == NODATA and az[0, 0] == NODATA
    # соседи по шаблону тоже становятся nodata, но основная часть валидна
    assert (mag != NODATA).sum() > 0.5 * mag.size


def test_flow_samples_thinning_and_values():
    """Прореживание векторного поля: координаты в пределах растра, значения
    совпадают с растрами, шаг прореживает."""
    h, gt, cell = _plane(0.02, 0.01, nx=40, ny=30)
    mag, az = hydro.head_gradient(h, cell, cell, NODATA)
    xs, ys, azs, grs = hydro.flow_samples(mag, az, gt, 5, NODATA)
    assert len(xs) == len(ys) == len(azs) == len(grs)
    # шаг 5 по сетке 40x30 -> заметно меньше числа валидных ячеек
    assert len(xs) < (mag != NODATA).sum()
    # координаты внутри охвата растра
    ox, dx, _, oy, _, dy = gt
    assert xs.min() >= ox and xs.max() <= ox + 40 * dx
    assert ys.max() <= oy and ys.min() >= oy + 30 * dy
    # азимут одинаков на плоскости (уклон на СВ -> поток на ЮЗ)
    assert np.allclose(azs, azs[0], atol=1e-6)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
