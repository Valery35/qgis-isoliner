# -*- coding: utf-8 -*-
"""Тесты сглаживания FPDEMS: шум уходит, бровка стоит."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import topo_smooth as ts  # noqa: E402


class TestFpdems(unittest.TestCase):

    def test_noise_reduced_on_plane(self):
        # наклонная плоскость плюс шум: после сглаживания шум меньше
        rng = np.random.default_rng(1)
        ny, nx = 60, 60
        base = np.arange(nx)[None, :] * 0.5 + np.arange(ny)[:, None] * 0.3
        noise = rng.normal(0, 1.0, (ny, nx))
        z = base + noise
        out = ts.smooth_fpdems(z, cell=10.0, norm_iters=6, elev_iters=8)
        # остаточное отклонение от гладкой основы уменьшилось
        r_before = float(np.std((z - base)[5:-5, 5:-5]))
        r_after = float(np.std((out - base)[5:-5, 5:-5]))
        self.assertLess(r_after, r_before)

    def test_step_preserved(self):
        # резкая бровка: перепад высоты сохраняется, не заваливается
        ny, nx = 50, 50
        z = np.zeros((ny, nx))
        z[:, nx // 2:] = 40.0            # ступень 40 м по меридиану
        rng = np.random.default_rng(2)
        z = z + rng.normal(0, 0.4, (ny, nx))
        out = ts.smooth_fpdems(z, cell=10.0, norm_iters=5, elev_iters=5,
                               norm_diff_deg=10.0)
        left = out[10:-10, nx // 2 - 3]
        right = out[10:-10, nx // 2 + 3]
        jump = float(np.mean(right) - np.mean(left))
        # ступень около 40 м должна сохраниться (низкочастотный фильтр
        # размыл бы её вдвое и сильнее)
        self.assertGreater(jump, 34.0)

    def test_nodata_untouched(self):
        z = np.ones((20, 20)) * 100.0
        mask = np.zeros((20, 20), dtype=bool)
        mask[5, 5] = True
        z[5, 5] = -9999.0
        out = ts.smooth_fpdems(z, cell=30.0, nodata_mask=mask)
        self.assertEqual(out[5, 5], -9999.0)

    def test_flat_stays_flat(self):
        z = np.full((30, 30), 250.0)
        out = ts.smooth_fpdems(z, cell=30.0)
        self.assertTrue(np.allclose(out, 250.0, atol=1e-6))

    def test_deterministic(self):
        rng = np.random.default_rng(3)
        z = rng.normal(100, 5, (40, 40))
        a = ts.smooth_fpdems(z, cell=20.0)
        b = ts.smooth_fpdems(z, cell=20.0)
        self.assertTrue(np.array_equal(a, b))

    def test_shape_kept(self):
        z = np.random.default_rng(4).normal(0, 1, (33, 47))
        out = ts.smooth_fpdems(z, cell=10.0)
        self.assertEqual(out.shape, (33, 47))


if __name__ == "__main__":
    unittest.main()
