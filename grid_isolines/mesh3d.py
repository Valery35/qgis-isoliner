# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Регулярный грид -> треугольный меш: массивы вершин/граней и запись 2DM.

Чистый NumPy, без импорта QGIS - модуль проверяется headless-тестами
(tests/test_mesh3d.py). Узлы берутся в центрах ячеек, ячейки без данных
(NaN) пропускаются: узел не пишется, треугольники строятся только по
квадратам, все четыре угла которых валидны.
"""
import numpy as np


def grid_to_mesh_arrays(arr, gt, zscale=1.0, zoffset=0.0, step=1):
    """Строит меш по гриду. arr - 2D массив (NaN = нет данных), gt - GDAL
    geotransform (6 чисел). Z вершины = значение ячейки * zscale + zoffset.
    step > 1 прореживает узлы. Возвращает (verts, faces): verts - float64
    (N, 3), faces - int64 (M, 3) с нулевой базой индексов."""
    a = np.asarray(arr, dtype=float)
    step = max(1, int(step))
    rows = np.arange(0, a.shape[0], step)
    cols = np.arange(0, a.shape[1], step)
    a = a[np.ix_(rows, cols)]
    ny, nx = a.shape
    if ny < 2 or nx < 2:
        raise ValueError("grid too small")
    xs = gt[0] + (cols + 0.5) * gt[1]
    ys = gt[3] + (rows + 0.5) * gt[5]
    valid = np.isfinite(a)
    n = int(valid.sum())
    if n == 0:
        raise ValueError("no data")
    idx = np.full(a.shape, -1, dtype=np.int64)
    idx[valid] = np.arange(n)
    z = a * float(zscale) + float(zoffset)

    q = valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]
    n00 = idx[:-1, :-1][q]
    n01 = idx[:-1, 1:][q]
    n10 = idx[1:, :-1][q]
    n11 = idx[1:, 1:][q]
    if len(n00):
        faces = np.vstack([np.column_stack([n00, n01, n11]),
                           np.column_stack([n00, n11, n10])])
    else:
        faces = np.empty((0, 3), dtype=np.int64)

    ij = np.argwhere(valid)
    verts = np.column_stack([xs[ij[:, 1]], ys[ij[:, 0]], z[valid]])
    return verts, faces


def sample_bilinear(arr, gt, x, y):
    """Билинейная выборка грида в точках (x, y). arr - 2D массив (NaN =
    нет данных), gt - GDAL geotransform. Вне грида и на NaN-углах - NaN.
    Возвращает массив значений той же длины, что x."""
    a = np.asarray(arr, dtype=float)
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    # координаты в ячейках относительно центров
    fc = (x - gt[0]) / gt[1] - 0.5
    fr = (y - gt[3]) / gt[5] - 0.5
    ny, nx = a.shape
    out = np.full(x.shape, np.nan)
    ok = (fc >= 0) & (fc <= nx - 1) & (fr >= 0) & (fr <= ny - 1)
    if not ok.any():
        return out
    c0 = np.minimum(np.floor(fc).astype(int), nx - 2)
    r0 = np.minimum(np.floor(fr).astype(int), ny - 2)
    tc = fc - c0
    tr = fr - r0
    c, r, u, v = c0[ok], r0[ok], tc[ok], tr[ok]
    q00 = a[r, c]
    q01 = a[r, c + 1]
    q10 = a[r + 1, c]
    q11 = a[r + 1, c + 1]
    val = (q00 * (1 - u) * (1 - v) + q01 * u * (1 - v)
           + q10 * (1 - u) * v + q11 * u * v)
    out[ok] = val
    return out


def grid_to_2dm(arr, gt, path, zscale=1.0, zoffset=0.0, step=1):
    """Пишет грид в 2DM (читается MDAL/QGIS). Параметры как у
    grid_to_mesh_arrays. Возвращает (узлов, треугольников)."""
    verts, faces = grid_to_mesh_arrays(arr, gt, zscale, zoffset, step)
    n = len(verts)
    nd = np.column_stack([np.arange(1, n + 1), verts])
    et = np.column_stack([np.arange(1, len(faces) + 1), faces + 1,
                          np.ones(len(faces), dtype=np.int64)])
    with open(path, "w", encoding="ascii", newline="\n") as f:
        f.write("MESH2D\n")
        np.savetxt(f, et, fmt="E3T %d %d %d %d %d")
        np.savetxt(f, nd, fmt="ND %d %.6f %.6f %.6f")
    return n, int(len(faces))
