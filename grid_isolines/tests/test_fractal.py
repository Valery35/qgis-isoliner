# -*- coding: utf-8 -*-
"""Тесты фрактальной размерности (вариограммный метод)."""
import sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from grid_isolines.fractal import (fractal_dimension_map,
                                   fractal_dimension_global, _boxsum)


def test_boxsum_exact():
    rng = np.random.default_rng(1)
    a = rng.random((30, 40)); r = 3
    ref = np.zeros_like(a)
    for i in range(30):
        for j in range(40):
            ref[i, j] = a[max(0, i-r):i+r+1, max(0, j-r):j+r+1].sum()
    assert np.allclose(_boxsum(a, r), ref)


def test_white_noise_D3():
    rng = np.random.default_rng(2)
    D, _ = fractal_dimension_global(rng.normal(0, 1, (160, 160)))
    assert D > 2.85, D


def test_smooth_D2():
    xx, yy = np.meshgrid(np.linspace(0, 6, 160), np.linspace(0, 6, 160))
    D, _ = fractal_dimension_global(np.sin(xx) * np.cos(yy))
    assert D < 2.15, D


def test_map_separates_and_nan():
    rng = np.random.default_rng(3)
    xx, yy = np.meshgrid(np.linspace(0, 6, 160), np.linspace(0, 6, 160))
    zs = np.sin(xx) * np.cos(yy)
    z = np.where(xx < 3, zs * 3, rng.normal(0, 1, zs.shape))
    z[70:90, 70:90] = np.nan
    D, H = fractal_dimension_map(z, window=10, max_lag=4)
    assert np.nanmean(D[:, :60]) < 2.3
    assert np.nanmean(D[:, 100:]) > 2.8
    assert np.isfinite(D).sum() > 0.7 * D.size
    hh = H[np.isfinite(H)]
    assert hh.min() >= 0.0 and hh.max() <= 1.0


def _sierpinski(n):
    m = np.ones((1, 1), bool)
    for _ in range(n):
        z = np.zeros((m.shape[0] * 3,) * 2, bool)
        for i in range(3):
            for j in range(3):
                if not (i == 1 and j == 1):
                    z[i*m.shape[0]:(i+1)*m.shape[0],
                      j*m.shape[1]:(j+1)*m.shape[1]] = m
        m = z
    return m


def test_box_counting_references():
    from grid_isolines.fractal import box_count_dimension
    D, _s, _c = box_count_dimension(_sierpinski(5), sizes=[81, 27, 9, 3])
    assert abs(D - 1.8928) < 0.02, D          # ковёр Серпинского
    full = np.zeros((256, 256), bool); full[40:210, 50:220] = True
    D2, _s, _c = box_count_dimension(full)
    assert abs(D2 - 2.0) < 0.1, D2            # пятно (краевое смещение ±0.1)
    line = np.zeros((256, 256), bool)
    for k in range(250):
        line[k, k] = True
    D1, _s, _c = box_count_dimension(line)
    assert abs(D1 - 1.0) < 0.1, D1            # линия


def _koch(n):
    pts = np.array([[0.0, 0.0], [1.0, 0.0]])
    rot = np.array([[0.5, -np.sqrt(3)/2], [np.sqrt(3)/2, 0.5]])
    for _ in range(n):
        out = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            v = b - a
            p1 = a + v/3; p2 = a + 2*v/3
            out += [p1, p1 + rot @ (v/3), p2, b]
        pts = np.array(out)
    return pts


def test_divider_references():
    from grid_isolines.fractal import divider_dimension
    Dk, _r, _s = divider_dimension(_koch(5))
    assert abs(Dk - 1.2619) < 0.06, Dk        # кривая Коха
    Ds, _r, _s = divider_dimension(
        np.array([[0.0, 0.0], [3.0, 0.9], [7.0, 2.1], [10.0, 3.0]]))
    assert abs(Ds - 1.0) < 0.05, Ds           # почти прямая ломаная
    Dt, _r, _s = divider_dimension(
        np.column_stack([np.linspace(0, 10, 50),
                         np.linspace(0, 3, 50)]))
    assert abs(Dt - 1.0) < 0.01, Dt           # строго прямая


def test_minkowski_references():
    from grid_isolines.fractal import minkowski_dimension
    # K=6: две лишние ступени у Коха-5 ушли бы ниже масштаба построения
    Dk, r2, _s, _c = minkowski_dimension([_koch(5)], n_sizes=6)
    assert abs(Dk - 1.2619) < 0.06, Dk
    assert r2 > 0.99, r2
    line = np.column_stack([np.linspace(0, 10, 60), np.linspace(0, 3, 60)])
    Ds, _r, _s, _c = minkowski_dimension([line], n_sizes=6)
    assert abs(Ds - 1.0) < 0.05, Ds
    sq = np.array([[0, 0], [10, 0], [10, 10], [0, 10], [0, 0.0]])
    Dq, _r, _s, _c = minkowski_dimension([sq], n_sizes=6)
    assert abs(Dq - 1.0) < 0.09, Dq


def test_minkowski_offsets_min_cover():
    from grid_isolines.fractal import minkowski_dimension
    # минимальное покрытие: со сдвигами счёт на каждой ступени не больше,
    # чем у выровненной сетки, а D устойчиво по зерну
    _d, _r, s1, c1 = minkowski_dimension([_koch(4)], n_sizes=5, offsets=1)
    _d, _r, s3, c3 = minkowski_dimension([_koch(4)], n_sizes=5, offsets=4,
                                         seed=2)
    assert all(b <= a for a, b in zip(c1, c3)), (c1, c3)
    ds = [minkowski_dimension([_koch(4)], n_sizes=5, offsets=3, seed=s)[0]
          for s in range(6)]
    assert np.std(ds) < 0.01, np.std(ds)


def _river(rough, depth, seed):
    rng = np.random.default_rng(seed)
    pts = [np.array([0.0, 0.0]), np.array([1000.0, 180.0])]
    for _ in range(depth):
        out = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            m = (a + b) / 2.0
            d = b - a
            n = np.array([-d[1], d[0]])
            L = float(np.hypot(*d))
            m = m + n / (L + 1e-12) * rng.normal(0, rough * L)
            out += [m, b]
        pts = out
    return np.array(pts)


def test_minkowski_near_smooth_river():
    """Слабошершавая линия обязана давать D чуть выше 1, не ниже:
    регресс на эталоне выявил бы смещение оценщика на коротких линиях."""
    from grid_isolines.fractal import minkowski_dimension
    vals = [minkowski_dimension([_river(0.10, 3, s)])[0] for s in range(12)]
    m = float(np.mean(vals))
    assert 0.99 < m < 1.05, m
    assert min(vals) > 0.96, min(vals)


def _midline(rough, depth, seed):
    rng = np.random.default_rng(seed)
    pts = [np.array([0.0, 0.0]), np.array([100.0, 20.0])]
    for _ in range(depth):
        out = [pts[0]]
        for a, b in zip(pts[:-1], pts[1:]):
            m = (a + b) / 2
            d = b - a
            nrm = np.array([-d[1], d[0]])
            L = float(np.hypot(*d))
            m = m + nrm / (L + 1e-12) * rng.normal(0, rough * L)
            out += [m, b]
        pts = out
    return np.array(pts)


def test_minkowski_near_smooth_river():
    """Слабо изрезанная линия (демо-река): D чуть выше 1, не ниже.
    Регрессия от 06.07.2026: неверная лесенка давала 0.85-0.95."""
    from grid_isolines.fractal import minkowski_dimension
    ds = [minkowski_dimension([_midline(0.10, 3, s)])[0] for s in range(10)]
    m = float(np.mean(ds))
    assert 0.99 < m < 1.05, ds
    assert min(ds) > 0.96, ds


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn()
            print("OK", name)
    print("all fractal tests passed")
