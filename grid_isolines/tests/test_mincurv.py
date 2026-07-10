# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Headless-тесты решателя минимальной кривизны (без QGIS)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
from grid_isolines import mincurv as M  # noqa: E402


def test_grid_points_snap():
    xs = np.array([0.5, 9.5, 0.5])
    ys = np.array([0.5, 0.5, 9.5])
    vs = np.array([1.0, 2.0, 3.0])
    z0, fixed = M.grid_points(xs, ys, vs, xmin=0.0, ymin=0.0,
                              cell=1.0, nx=10, ny=10)
    assert fixed.sum() == 3
    # (0.5,0.5)->низ-лево (row9,col0); (0.5,9.5)->верх-лево (row0,col0)
    assert fixed[9, 0] and fixed[0, 0] and fixed[9, 9]
    assert abs(z0[9, 0] - 1.0) < 1e-9 and abs(z0[0, 0] - 3.0) < 1e-9


def test_constant_field():
    rng = np.random.default_rng(1)
    n = 50
    xs = rng.uniform(0, 30, n); ys = rng.uniform(0, 20, n)
    vs = np.full(n, 4.2)
    z0, fixed = M.grid_points(xs, ys, vs, 0.0, 0.0, 1.0, 30, 20)
    out, it, res = M.solve(z0, fixed, max_iter=20000, tol=1e-6, relax=1.85)
    assert np.max(np.abs(out - 4.2)) < 1e-3


def test_plane_hole_fill():
    ny, nx = 16, 16
    yy, xx = np.mgrid[0:ny, 0:nx]
    plane = 2.0 + 0.5 * xx - 0.3 * yy
    fixed = np.ones((ny, nx), dtype=bool)
    fixed[5:11, 5:11] = False
    z = plane.copy(); z[~fixed] = 0.0
    out, it, res = M.solve(z, fixed, max_iter=40000, tol=1e-8, relax=1.85)
    assert np.max(np.abs(out - plane)) < 1e-3
    assert it < 40000


def test_plane_scattered_rmse():
    rng = np.random.default_rng(0)
    n = 60
    xs = rng.uniform(0, 30, n); ys = rng.uniform(0, 20, n)
    vs = 2 + 0.5 * xs - 0.3 * ys
    z0, fixed = M.grid_points(xs, ys, vs, 0.0, 0.0, 1.0, 30, 20)
    out, it, res = M.solve(z0, fixed, max_iter=20000, tol=1e-6, relax=1.85)
    yy, xx = np.mgrid[0:20, 0:30]
    X = (xx + 0.5); Y = (20.0) - (yy + 0.5)
    plane = 2 + 0.5 * X - 0.3 * Y
    rmse = float(np.sqrt(np.mean((out - plane) ** 2)))
    assert rmse < 0.4


def test_fixed_nodes_honored():
    ny, nx = 10, 10
    z = np.zeros((ny, nx))
    fixed = np.zeros((ny, nx), dtype=bool)
    z[2, 3] = 10.0; fixed[2, 3] = True
    z[7, 6] = -5.0; fixed[7, 6] = True
    out, it, res = M.solve(z, fixed, max_iter=8000, tol=1e-6, relax=1.8)
    assert abs(out[2, 3] - 10.0) < 1e-9
    assert abs(out[7, 6] - (-5.0)) < 1e-9


def test_membrane_no_overshoot():
    ny, nx = 20, 20
    z = np.zeros((ny, nx))
    fixed = np.zeros((ny, nx), dtype=bool)
    z[3, 3] = 0.0; fixed[3, 3] = True
    z[3, 16] = 0.0; fixed[3, 16] = True
    z[16, 10] = 10.0; fixed[16, 10] = True
    memb, _, _ = M.solve(z, fixed, tension=1.0, max_iter=15000,
                         tol=1e-6, relax=1.8)
    assert memb.min() > -1e-6 and memb.max() < 10.0 + 1e-6


def test_sample_bilinear():
    g = np.array([[0.0, 1.0], [2.0, 3.0]])
    assert abs(M.sample_bilinear(g, 0, 0, 1.0, 2, 2, 0.5, 1.5) - 0.0) < 1e-9
    assert abs(M.sample_bilinear(g, 0, 0, 1.0, 2, 2, 1.5, 0.5) - 3.0) < 1e-9


def test_loo_plane_small_error():
    rng = np.random.default_rng(0)
    n = 80
    xs = rng.uniform(0, 30, n); ys = rng.uniform(0, 20, n)
    vs = 2 + 0.5 * xs - 0.3 * ys
    val = np.arange(0, n, 4)
    ests, zf = M.loo_estimates(xs, ys, vs, 0, 0, 1.0, 30, 20, val,
                               tol=1e-4, base_iter=80000, loo_iter=15000)
    err = ests - vs[val]
    assert np.sqrt(np.nanmean(err ** 2)) < 0.5


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("all %d tests passed" % len(fns))


if __name__ == "__main__":
    _run()
