# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты пересечения TIN с разрезом. Движок не зависит от QGIS:
#     python grid_isolines/tests/test_tin.py
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402


def _flat_tin():
    """Плоская наклонная TIN над квадратом 0..10, z = 10 + 0.5x."""
    tris = []
    for ix in range(10):
        for iy in range(10):
            x0, x1 = float(ix), float(ix + 1)
            y0, y1 = float(iy), float(iy + 1)

            def zf(x):
                return 10.0 + 0.5 * x
            A = (x0, y0, zf(x0)); B = (x1, y0, zf(x1))
            C = (x1, y1, zf(x1)); D = (x0, y1, zf(x0))
            tris.append((A, B, C)); tris.append((A, C, D))
    return tris


def _fold_tin():
    """Опрокинутая складка: лента-полукольцо, заворачивающаяся под себя.
    Центральная линия (x, z) многозначна по x."""
    th = np.linspace(0.0, 1.45 * math.pi, 40)
    cx = 5.0 + 4.0 * np.cos(th)
    cz = 4.0 * np.sin(th)
    tris = []
    for i in range(len(th) - 1):
        A = (cx[i], -5.0, cz[i]); B = (cx[i], 5.0, cz[i])
        C = (cx[i + 1], -5.0, cz[i + 1]); D = (cx[i + 1], 5.0, cz[i + 1])
        tris.append((A, B, C)); tris.append((B, D, C))
    return tris, cx, cz


def test_flat_tin_matches_plane():
    """На плоской TIN трасса воспроизводит z = 10 + 0.5x вдоль линии y=5."""
    tris = _flat_tin()
    poly = [(0.0, 5.0), (10.0, 5.0)]
    segs = kb2d.tin_section_trace(poly, tris)
    assert len(segs) > 0
    for d0, z0, d1, z1 in segs:
        assert abs(z0 - (10.0 + 0.5 * d0)) < 1e-6
        assert abs(z1 - (10.0 + 0.5 * d1)) < 1e-6


def test_overhang_multivalued():
    """Опрокинутая складка даёт несколько отметок над одной станцией."""
    tris, cx, cz = _fold_tin()
    poly = [(-1.0, 0.0), (11.0, 0.0)]      # станция d = x + 1
    segs = kb2d.tin_section_trace(poly, tris)
    assert len(segs) > 0
    import collections
    buckets = collections.defaultdict(set)
    for d0, z0, d1, z1 in segs:
        dm = round((0.5 * (d0 + d1)) * 2) / 2
        buckets[dm].add(round(0.5 * (z0 + z1), 1))
    multi = [d for d, zs in buckets.items() if len(zs) >= 2]
    assert len(multi) > 0, "нависание не воспроизвелось"


def test_trace_within_model_range():
    tris, cx, cz = _fold_tin()
    segs = kb2d.tin_section_trace([(-1.0, 0.0), (11.0, 0.0)], tris)
    zall = [z for s in segs for z in (s[1], s[3])]
    assert min(zall) >= cz.min() - 1e-6
    assert max(zall) <= cz.max() + 1e-6


def test_no_crossing_returns_empty():
    """Линия вне TIN не даёт трассы."""
    tris = _flat_tin()
    segs = kb2d.tin_section_trace([(100.0, 100.0), (110.0, 100.0)], tris)
    assert segs == []


def test_fan_triangulate_quad():
    quad = [(0, 0, 1), (1, 0, 2), (1, 1, 3), (0, 1, 4)]
    tr = kb2d.fan_triangulate(quad)
    assert len(tr) == 2
    assert tr[0] == ((0, 0, 1), (1, 0, 2), (1, 1, 3))


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
