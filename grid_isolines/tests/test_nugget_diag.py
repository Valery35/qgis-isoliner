# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Разбор наггета и качество подбора вариограммы. Пришло из живого случая:
# 40 скважин, автоподбор дал R²=0.019 и наггет 72%, кригинг вернул ровное
# поле около среднего, и понять причину по логу было нельзя. Причиной
# оказались две пары близких скважин с несопоставимыми значениями.
#     python grid_isolines/tests/test_nugget_diag.py
import os
import sys
import unittest

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from grid_isolines import kb2d  # noqa: E402


class NuggetPairs(unittest.TestCase):
    """Поиск пар, формирующих наггет."""

    def _sample(self):
        # две тесные пары с большим скачком плюс далёкая одиночка
        xs = np.array([0.0, 10.0, 1000.0, 1005.0, 5000.0])
        ys = np.zeros(5)
        vs = np.array([-19.4, -176.9, 20.2, -50.8, 5.0])
        return xs, ys, vs

    def test_finds_both_pairs_ordered_by_contribution(self):
        xs, ys, vs = self._sample()
        got = kb2d.nugget_pairs(xs, ys, vs, 50.0, top=5)
        self.assertEqual(len(got), 2)
        # γ = 0.5*(Δv)^2, тяжёлая пара первой
        self.assertAlmostEqual(got[0][5], 0.5 * (-19.4 + 176.9) ** 2, places=3)
        self.assertAlmostEqual(got[1][5], 0.5 * (20.2 + 50.8) ** 2, places=3)
        self.assertGreater(got[0][5], got[1][5])
        # индексы, расстояние и значения возвращаются вместе со вкладом
        i, j, dist, vi, vj, _g = got[0]
        self.assertEqual((i, j), (0, 1))
        self.assertAlmostEqual(dist, 10.0, places=6)
        self.assertAlmostEqual(vi, -19.4, places=6)
        self.assertAlmostEqual(vj, -176.9, places=6)

    def test_maxdist_limits_the_search(self):
        """Дальние пары не при чём: наггет живёт на коротких расстояниях."""
        xs, ys, vs = self._sample()
        # пары разнесены на 10 и на 5 единиц
        self.assertEqual(kb2d.nugget_pairs(xs, ys, vs, 4.0, top=5), [])
        self.assertEqual(len(kb2d.nugget_pairs(xs, ys, vs, 7.0, top=5)), 1)
        self.assertEqual(len(kb2d.nugget_pairs(xs, ys, vs, 12.0, top=5)), 2)

    def test_top_caps_the_list(self):
        xs, ys, vs = self._sample()
        self.assertEqual(len(kb2d.nugget_pairs(xs, ys, vs, 50.0, top=1)), 1)

    def test_degenerate_input_is_quiet(self):
        xs, ys, vs = self._sample()
        self.assertEqual(kb2d.nugget_pairs(xs, ys, vs, 0.0), [])
        self.assertEqual(kb2d.nugget_pairs(xs, ys, vs, -1.0), [])
        self.assertEqual(kb2d.nugget_pairs([1.0], [1.0], [1.0], 10.0), [])

    def test_chunking_does_not_change_result(self):
        """Обход блоками - деталь реализации, ответ от неё не зависит."""
        rng = np.random.default_rng(11)
        n = 300
        xs = rng.uniform(0, 500, n)
        ys = rng.uniform(0, 500, n)
        vs = rng.normal(0, 10, n)
        whole = kb2d.nugget_pairs(xs, ys, vs, 40.0, top=7, chunk=10 ** 6)
        split = kb2d.nugget_pairs(xs, ys, vs, 40.0, top=7, chunk=7)
        self.assertEqual(whole, split)
        self.assertEqual(len(whole), 7)

    def test_pairs_are_upper_triangle_only(self):
        """Каждая пара названа один раз, а не дважды и не сама с собой."""
        rng = np.random.default_rng(5)
        n = 60
        xs = rng.uniform(0, 100, n)
        ys = rng.uniform(0, 100, n)
        vs = rng.normal(0, 5, n)
        got = kb2d.nugget_pairs(xs, ys, vs, 1000.0, top=n * n)
        seen = set()
        for i, j, _d, _vi, _vj, _g in got:
            self.assertLess(i, j)
            self.assertNotIn((i, j), seen)
            seen.add((i, j))


class FitQualityWarning(unittest.TestCase):
    """Предупреждения о негодном подборе: R² и доля наггета."""

    def setUp(self):
        alg = os.path.join(os.path.dirname(HERE), "algorithms.py")
        with open(alg, encoding="utf-8") as fh:
            self.src = fh.read()

    def _warn(self, fit):
        """Исполнить _warn_fit_quality на заглушке и собрать предупреждения."""
        i = self.src.index("def _warn_fit_quality(")
        tail = self.src[i:]
        body = tail[:tail.index("\ndef ", 1)]
        ns = {"_tr": lambda s: s}
        exec(compile(body, "warn", "exec"), ns)  # nosec
        got = []

        class _F(object):
            def pushWarning(self, msg):
                got.append(msg)

        ns["_warn_fit_quality"](_F(), fit)
        return got

    def test_live_case_trips_both_warnings(self):
        """Случай Валерия: R²=0.019 и наггет 72% - оба предупреждения."""
        got = self._warn({"r2": 0.019, "nugget": 376.1, "sill": 148.0})
        self.assertEqual(len(got), 2)
        self.assertTrue(any("R\u00b2" in m for m in got))
        self.assertTrue(any("72" in m for m in got))

    def test_good_fit_is_silent(self):
        self.assertEqual(
            self._warn({"r2": 0.93, "nugget": 30.0, "sill": 570.0}), [])

    def test_each_condition_fires_alone(self):
        # плохой R² при умеренном наггете
        only_r2 = self._warn({"r2": 0.05, "nugget": 100.0, "sill": 900.0})
        self.assertEqual(len(only_r2), 1)
        # хороший R² при задранном наггете
        only_nug = self._warn({"r2": 0.8, "nugget": 900.0, "sill": 100.0})
        self.assertEqual(len(only_nug), 1)

    def test_missing_or_broken_fit_is_quiet(self):
        self.assertEqual(self._warn(None), [])
        self.assertEqual(self._warn({}), [])
        self.assertEqual(self._warn({"r2": "нет", "nugget": 1, "sill": 1}), [])

    def test_wired_into_the_variogram_tool(self):
        """Предупреждение стоит там, где печатается подбор."""
        self.assertEqual(self.src.count("_warn_fit_quality(feedback, fit)"), 2)
        self.assertIn("_report_nugget_pairs(feedback, xs, ys, vs, ev, data_var)",
                      self.src)


if __name__ == "__main__":
    unittest.main()
