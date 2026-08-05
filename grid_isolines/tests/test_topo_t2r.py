# -*- coding: utf-8 -*-
"""Тесты ядра Topo2Raster."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import topo_t2r as t2r  # noqa: E402


def _grid_xy(z, x0, y_top, cell):
    ny, nx = z.shape
    xs = x0 + (np.arange(nx) + 0.5) * cell
    ys = y_top - (np.arange(ny) + 0.5) * cell
    return xs, ys


class TestPointWeights(unittest.TestCase):
    """Вес образца при усреднении внутри ячейки.

    Точки высот и вершины уплотнённых изолиний идут в 2.03 одним потоком
    жёстких узлов. При совпадении в ячейке простое среднее уравнивает
    измерение и оцифровку в правах, хотя вершина горизонтали врёт в
    пределах половины сечения. Вес это исправляет, не трогая схему
    релаксации.
    """

    def test_weight_decides_shared_cell(self):
        # обе точки заведомо в одной ячейке: строка 4, колонка 5
        meas = [10.2, 10.2, 100.0]
        dig = [10.8, 10.8, 110.0]
        plain = np.array([meas, dig], dtype=np.float64)
        pin, val = t2r._pin_from_points(plain, 0.0, 20.0, 2.0, (10, 10))
        self.assertAlmostEqual(float(val[pin][0]), 105.0, places=9)

        weighted = np.array([meas + [5.0], dig + [1.0]], dtype=np.float64)
        pin2, val2 = t2r._pin_from_points(weighted, 0.0, 20.0, 2.0, (10, 10))
        got = float(val2[pin2][0])
        self.assertAlmostEqual(got, (5 * 100.0 + 110.0) / 6.0, places=9)
        self.assertLess(got, 102.0)

    def test_three_columns_unchanged(self):
        rng = np.random.default_rng(5)
        p3 = np.column_stack([rng.uniform(0, 20, 40), rng.uniform(0, 20, 40),
                              rng.uniform(100, 110, 40)])
        p4 = np.column_stack([p3, np.ones(40)])
        a = t2r._pin_from_points(p3, 0.0, 20.0, 2.0, (10, 10))
        b = t2r._pin_from_points(p4, 0.0, 20.0, 2.0, (10, 10))
        self.assertTrue(np.array_equal(a[0], b[0]))
        self.assertTrue(np.allclose(a[1], b[1]))

    def test_bad_weights_fall_back_to_one(self):
        pts = np.array([[5.0, 5.0, 100.0, 0.0],
                        [5.1, 5.1, 102.0, -3.0],
                        [5.2, 5.2, 104.0, np.nan]], dtype=np.float64)
        pin, val = t2r._pin_from_points(pts, 0.0, 20.0, 2.0, (10, 10))
        self.assertAlmostEqual(float(val[pin][0]), 102.0, places=9)


class TestGeometryHelpers(unittest.TestCase):

    def test_polygon_mask_square(self):
        rings = [np.array([[10.0, 10.0], [90.0, 10.0],
                           [90.0, 90.0], [10.0, 90.0]])]
        m = t2r.polygon_mask(rings, 0.0, 100.0, 10.0, (10, 10))
        # квадрат 10..90: строки и столбцы 1..8
        self.assertTrue(m[1:9, 1:9].all())
        self.assertFalse(m[0, :].any())
        self.assertFalse(m[:, 0].any())
        self.assertFalse(m[9, :].any())

    def test_polygon_mask_hole(self):
        outer = np.array([[0.0, 0.0], [100.0, 0.0],
                          [100.0, 100.0], [0.0, 100.0]])
        hole = np.array([[40.0, 40.0], [60.0, 40.0],
                         [60.0, 60.0], [40.0, 60.0]])
        m = t2r.polygon_mask([outer, hole], 0.0, 100.0, 10.0, (10, 10))
        self.assertTrue(m[1, 1])
        self.assertFalse(m[5, 5])  # в дыре

    def test_polyline_cells_dedup(self):
        xy = np.array([[5.0, 95.0], [95.0, 95.0]])  # верхняя строка
        flat = t2r.polyline_cells(xy, 0.0, 100.0, 10.0, (10, 10))
        self.assertEqual(flat[0], 0)
        self.assertEqual(flat[-1], 9)
        self.assertEqual(len(flat), 10)
        self.assertEqual(len(set(flat.tolist())), 10)


class TestPlane(unittest.TestCase):

    def test_recover_tilted_plane(self):
        rng = np.random.default_rng(3)
        n = 200
        x = rng.uniform(0, 1000, n)
        y = rng.uniform(0, 1000, n)
        zv = 100.0 + 0.05 * x + 0.02 * y
        pts = np.column_stack([x, y, zv])
        z, x0, y_top = t2r.topo2raster(
            pts, None, None, None, (0, 0, 1000, 1000), 20.0, iterations=80)
        xs, ys = _grid_xy(z, x0, y_top, 20.0)
        want = 100.0 + 0.05 * xs[None, :] + 0.02 * ys[:, None]
        inner = (slice(5, -5), slice(5, -5))
        err = np.abs(z - want)[inner]
        self.assertLess(float(np.median(err)), 2.0)

    def test_needs_points(self):
        with self.assertRaises(t2r.Topo2RasterError):
            t2r.topo2raster(None, None, None, None, (0, 0, 100, 100), 10.0)


class TestContours(unittest.TestCase):

    def test_cone_between_contours(self):
        # концентрические изолинии конуса, уплотнённые в точки
        cx, cy = 500.0, 500.0
        pts = []
        for radius, zv in ((100, 90.0), (200, 80.0), (300, 70.0),
                           (400, 60.0)):
            ang = np.linspace(0, 2 * np.pi, 180, endpoint=False)
            for a in ang:
                pts.append((cx + radius * np.cos(a),
                            cy + radius * np.sin(a), zv))
        pts = np.array(pts)
        z, x0, y_top = t2r.topo2raster(
            pts, None, None, None, (0, 0, 1000, 1000), 10.0, iterations=60)
        xs, ys = _grid_xy(z, x0, y_top, 10.0)
        rr = np.hypot(xs[None, :] - cx, ys[:, None] - cy)
        band = (rr > 110) & (rr < 190)
        self.assertTrue((z[band] < 90.5).all())
        self.assertTrue((z[band] > 79.5).all())
        # радиальная монотонность по лучу на восток
        row = np.argmin(np.abs(ys - cy))
        cols = (xs > cx + 110) & (xs < cx + 390)
        prof = z[row, cols]
        self.assertLess(prof[-1], prof[0])


class TestStreams(unittest.TestCase):

    def test_stream_descends(self):
        # плоская плита 50 м, тальвег с юга на север: принудительное падение
        pts = np.array([[x, y, 50.0]
                        for x in (5.0, 995.0) for y in np.arange(5, 1000, 90)])
        stream = [np.array([[500.0, 10.0], [500.0, 990.0]])]
        z, x0, y_top = t2r.topo2raster(
            pts, stream, None, None, (0, 0, 1000, 1000), 20.0,
            iterations=40, min_drop=0.05)
        flat = t2r.polyline_cells(stream[0], x0, y_top, 20.0, z.shape)
        prof = z.ravel()[flat]
        self.assertTrue((np.diff(prof) <= -0.05 + 1e-9).all())


class TestLakes(unittest.TestCase):

    def _bowl_points(self):
        rng = np.random.default_rng(5)
        n = 400
        x = rng.uniform(0, 1000, n)
        y = rng.uniform(0, 1000, n)
        zv = 10.0 + 0.06 * np.hypot(x - 500, y - 500)
        return np.column_stack([x, y, zv])

    def test_lake_fixed_level(self):
        rings = [np.array([[400.0, 400.0], [600.0, 400.0],
                           [600.0, 600.0], [400.0, 600.0]])]
        z, x0, y_top = t2r.topo2raster(
            self._bowl_points(), None, None, [(rings, 12.5)],
            (0, 0, 1000, 1000), 20.0, iterations=40)
        m = t2r.polygon_mask(rings, x0, y_top, 20.0, z.shape)
        self.assertTrue(np.allclose(z[m], 12.5))

    def test_lake_auto_level_flat(self):
        rings = [np.array([[400.0, 400.0], [600.0, 400.0],
                           [600.0, 600.0], [400.0, 600.0]])]
        z, x0, y_top = t2r.topo2raster(
            self._bowl_points(), None, None, [(rings, None)],
            (0, 0, 1000, 1000), 20.0, iterations=40)
        m = t2r.polygon_mask(rings, x0, y_top, 20.0, z.shape)
        vals = z[m]
        self.assertLess(float(vals.std()), 1e-6)  # плоскость
        ring = t2r._shore_ring(m)
        self.assertLessEqual(float(vals.max()),
                             float(z[ring].min()) + 1e-6)


class TestBreaklines(unittest.TestCase):

    def test_cliff_jump(self):
        # запад приколот к 100, восток к 0, обрыв по меридиану 500
        pts = []
        for y in np.arange(10, 1000, 40):
            for x in (50.0, 250.0, 450.0):
                pts.append((x, y, 100.0))
            for x in (550.0, 750.0, 950.0):
                pts.append((x, y, 0.0))
        pts = np.array(pts)
        cliff = [np.array([[500.0, -10.0], [500.0, 1010.0]])]
        z, x0, y_top = t2r.topo2raster(
            pts, None, cliff, None, (0, 0, 1000, 1000), 20.0,
            iterations=60)
        cliff_col = int((500.0 - x0) / 20.0)
        west = z[:, cliff_col - 2]
        east = z[:, cliff_col + 2]
        self.assertTrue((west > 95.0).all())
        self.assertTrue((east < 5.0).all())

    def test_no_cliff_smooth(self):
        pts = []
        for y in np.arange(10, 1000, 40):
            pts.append((50.0, y, 100.0))
            pts.append((950.0, y, 0.0))
        pts = np.array(pts)
        z, _x0, _yt = t2r.topo2raster(
            pts, None, None, None, (0, 0, 1000, 1000), 20.0, iterations=80)
        mid = z[:, z.shape[1] // 2]
        self.assertTrue((mid > 20.0).all())
        self.assertTrue((mid < 80.0).all())


class TestDeterminism(unittest.TestCase):

    def test_repeatable(self):
        pts = np.array([[100.0, 100.0, 10.0], [900.0, 900.0, 90.0],
                        [100.0, 900.0, 40.0], [900.0, 100.0, 60.0]])
        a, _x, _y = t2r.topo2raster(pts, None, None, None,
                                    (0, 0, 1000, 1000), 25.0, iterations=30)
        b, _x, _y = t2r.topo2raster(pts, None, None, None,
                                    (0, 0, 1000, 1000), 25.0, iterations=30)
        self.assertTrue(np.array_equal(a, b))


class TestVariableShoreline(unittest.TestCase):
    """Переменный урез озёр: три приоритета высоты."""

    def _base_points(self):
        pts = []
        for x in (0, 3000):
            for y in (0, 3000):
                pts.append([x, y, 120 - x / 3000.0 * 20])
        return np.array(pts, float)

    def _ring(self):
        return np.array([[500, 1400], [2500, 1400], [2500, 1600],
                         [500, 1600], [500, 1400]], float)

    def _val(self, z, x0, ytop, xm, ym):
        c = int((xm - x0) / 30)
        r = int((ytop - ym) / 30)
        return z[r, c]

    def test_sloped_shoreline_from_ring_z(self):
        # Z вершин падают с запада на восток - урез наклонный
        ring_z = np.array([102, 99, 99, 102, 102], float)
        lakes = [([self._ring()], None, [ring_z])]
        z, x0, yt = t2r.topo2raster(self._base_points(), [], [], lakes,
                                    (0, 0, 3000, 3000), cell=30)
        west = self._val(z, x0, yt, 600, 1500)
        east = self._val(z, x0, yt, 2400, 1500)
        self.assertGreater(west, east + 1.5)

    def test_flat_lake_from_equal_ring_z(self):
        ring_z = np.array([100, 100, 100, 100, 100], float)
        lakes = [([self._ring()], None, [ring_z])]
        z, x0, yt = t2r.topo2raster(self._base_points(), [], [], lakes,
                                    (0, 0, 3000, 3000), cell=30)
        west = self._val(z, x0, yt, 600, 1500)
        east = self._val(z, x0, yt, 2400, 1500)
        self.assertLess(abs(west - east), 0.5)

    def test_legacy_tuple_still_works(self):
        # старый формат (кольца, z) без ring_z
        lakes = [([self._ring()], 105.0)]
        z, x0, yt = t2r.topo2raster(self._base_points(), [], [], lakes,
                                    (0, 0, 3000, 3000), cell=30)
        val = self._val(z, x0, yt, 1500, 1500)
        self.assertAlmostEqual(val, 105.0, delta=0.5)



class LakeModeTests(unittest.TestCase):
    """Урез в двух режимах: плоскость и изолиния."""

    def test_lake_as_isoline_lets_the_bed_show(self):
        """Урез изолинией закрепляет берег, но не заливает площадь.

        Плоскость нужна зеркалу озера, изолиния - реке: внутри контура
        должно прорисовываться дно по точкам промеров и тальвегу. Раньше
        урез всегда прибивал всю площадь, и построить ЦМР с дном было
        нельзя.
        """
        pts = [(5.0, 5.0, 100.0), (55.0, 5.0, 100.0), (5.0, 55.0, 100.0),
               (55.0, 55.0, 100.0), (30.0, 30.0, 90.0)]
        ring = [np.array([[20., 20.], [40., 20.], [40., 40.], [20., 40.],
                          [20., 20.]])]
        ext = (0.0, 0.0, 60.0, 60.0)

        flat = t2r.topo2raster(pts, [], [], [(ring, 95.0, None, True)],
                               ext, 2.0, iterations=60)
        flat = flat[0] if isinstance(flat, tuple) else flat
        iso = t2r.topo2raster(pts, [], [], [(ring, 95.0, None, False)],
                              ext, 2.0, iterations=60)
        iso = iso[0] if isinstance(iso, tuple) else iso

        ny, nx = flat.shape
        inner = (slice(ny // 3, 2 * ny // 3), slice(nx // 3, 2 * nx // 3))
        self.assertLess(abs(float(flat[inner].max() - flat[inner].min())),
                        1e-6)
        self.assertLess(float(iso[inner].min()),
                        float(flat[inner].min()) - 1.0)

    def test_mask_edge_keeps_only_the_boundary(self):
        """Контур маски это её ячейки, у которых есть сосед снаружи."""
        m = np.zeros((7, 7), dtype=bool)
        m[2:5, 2:5] = True
        e = t2r.mask_edge(m)
        self.assertEqual(int(e.sum()), 8)
        self.assertFalse(bool(e[3, 3]))
        self.assertEqual(int((e & ~m).sum()), 0)


if __name__ == "__main__":
    unittest.main()
