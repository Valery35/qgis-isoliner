# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Диапазон пояса читается из уровней ограничивающих изолиний:
#     python grid_isolines/tests/test_belt_band.py
#
# Дефект, ради которого написан файл. Диапазон поясу назначался выборкой
# растра в одной репрезентативной точке. Геометрия пояса при этом приходит
# от контурера, который проводит изолинию интерполяцией между центрами
# ячеек, а выборка отдаёт значение самой ячейки. Пока пояс шире ячейки,
# разница незаметна. Когда пояс целиком лежит внутри одной ячейки,
# значение этой ячейки принадлежит соседнему диапазону, и пояс получает
# чужой.
#
# Найдено на данных Д. Березина, 22 скважины на участке 563 на 735 м,
# сечение 1 м. По кригингу 72 уровня на 50 ячеек и два пояса с чужим
# диапазоном, по минимальной кривизне 97 уровней на 50 ячеек и
# пятьдесят четыре. Числа в тестах взяты из тех прогонов.
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "isolines.py")


def _load(name):
    """Функцию из isolines.py без импорта модуля: QGIS тут нет."""
    with open(SRC, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name == name:
            ns = {}
            exec(compile(ast.Module(body=[n], type_ignores=[]),
                         "<isolines>", "exec"), ns)      # nosec B102
            return ns[name]
    raise AssertionError("функция %s не найдена в isolines.py" % name)


band = _load("belt_band_from_levels")
KRIG = [float(v) for v in range(224, 296)]       # кригинг, 224…295
MC = [float(v) for v in range(205, 302)]         # мин. кривизна, 205…301


def idx_of(levels, upper):
    """Индекс диапазона, у которого верхняя граница равна upper."""
    return levels.index(float(upper))


class BoundedByTwoContours(unittest.TestCase):
    """Две изолинии по сторонам дают диапазон точно, выборка не нужна."""

    def test_krig_fid17(self):
        """Пояс между 253 и 254, выборка дала 252…253."""
        got, src = band({253.0: 40.0, 254.0: 38.0}, KRIG,
                        fallback=idx_of(KRIG, 253))
        self.assertEqual(got, idx_of(KRIG, 254))
        self.assertEqual(src, "lines")

    def test_krig_fid67(self):
        """Пояс между 257 и 258, выборка дала 256…257."""
        got, src = band({257.0: 30.0, 258.0: 29.0}, KRIG,
                        fallback=idx_of(KRIG, 257))
        self.assertEqual(got, idx_of(KRIG, 258))
        self.assertEqual(src, "lines")

    def test_mc_fid103(self):
        """Пояс между 279 и 280, выборка дала 280…281."""
        got, src = band({279.0: 90.0, 280.0: 88.0}, MC,
                        fallback=idx_of(MC, 281))
        self.assertEqual(got, idx_of(MC, 280))
        self.assertEqual(src, "lines")

    def test_mc_fid79_sample_was_low(self):
        """Сдвиг бывает в обе стороны: выборка дала 254…255 против 255…256."""
        got, src = band({255.0: 60.0, 256.0: 58.0}, MC,
                        fallback=idx_of(MC, 255))
        self.assertEqual(got, idx_of(MC, 256))
        self.assertEqual(src, "lines")

    def test_lines_win_even_without_a_sample(self):
        got, src = band([261.0, 262.0], MC, fallback=None)
        self.assertEqual(got, idx_of(MC, 262))
        self.assertEqual(src, "lines")

    def test_order_of_levels_does_not_matter(self):
        a, _ = band({280.0: 10.0, 279.0: 11.0}, MC, fallback=None)
        b, _ = band({279.0: 11.0, 280.0: 10.0}, MC, fallback=None)
        self.assertEqual(a, b)


class TripleNode(unittest.TestCase):
    """Касание третьего уровня в узле не спорит с настоящими сторонами."""

    def test_short_touch_loses_to_the_two_sides(self):
        got, src = band({270.0: 120.0, 271.0: 118.0, 272.0: 0.004}, MC,
                        fallback=idx_of(MC, 272))
        self.assertEqual(got, idx_of(MC, 271))
        self.assertEqual(src, "lines")

    def test_zero_length_touch_is_dropped(self):
        got, src = band({270.0: 120.0, 271.0: 118.0, 272.0: 0.0}, MC,
                        fallback=None)
        self.assertEqual(got, idx_of(MC, 271))


class BoundedByTheOutline(unittest.TestCase):
    """Одна изолиния и контур области: выбор из двух, решает выборка."""

    def test_sample_below_the_contour(self):
        k = idx_of(MC, 240.0)
        got, src = band({240.0: 50.0}, MC, fallback=k)
        self.assertEqual(got, k)
        self.assertEqual(src, "sample")

    def test_sample_above_the_contour(self):
        k = idx_of(MC, 240.0)
        got, src = band({240.0: 50.0}, MC, fallback=k + 1)
        self.assertEqual(got, k + 1)

    def test_far_sample_is_pulled_to_the_nearest_of_two(self):
        """Выборка не может увести пояс дальше соседнего диапазона."""
        k = idx_of(MC, 240.0)
        got, _ = band({240.0: 50.0}, MC, fallback=k + 7)
        self.assertEqual(got, k + 1)
        got, _ = band({240.0: 50.0}, MC, fallback=max(k - 7, 0))
        self.assertEqual(got, k)

    def test_one_contour_without_a_sample_gives_nothing(self):
        got, src = band({240.0: 50.0}, MC, fallback=None)
        self.assertIsNone(got)
        self.assertIsNone(src)


class FallsBackHonestly(unittest.TestCase):
    """Там, где линии молчат или спорят, работает выборка."""

    def test_no_contours_at_all(self):
        got, src = band({}, MC, fallback=3)
        self.assertEqual((got, src), (3, "sample"))

    def test_levels_through_one_are_not_trusted(self):
        """Соседние стороны не могут отличаться на два уровня."""
        got, src = band({270.0: 10.0, 272.0: 10.0}, MC, fallback=5)
        self.assertEqual((got, src), (5, "sample"))

    def test_unknown_level_is_ignored(self):
        got, src = band({999.0: 10.0}, MC, fallback=5)
        self.assertEqual((got, src), (5, "sample"))

    def test_empty_levels(self):
        got, src = band({270.0: 10.0}, [], fallback=2)
        self.assertEqual((got, src), (2, "sample"))


class IndexMeaning(unittest.TestCase):
    """Индекс той же нумерации, что у numpy.digitize по уровням."""

    def test_index_matches_digitize(self):
        try:
            import numpy as np
        except ImportError:
            self.skipTest("numpy недоступен")
        lv = MC
        for upper in (210.0, 250.0, 301.0):
            k = idx_of(lv, upper)
            got, _ = band({upper - 1.0: 10.0, upper: 9.0}, lv, fallback=None)
            self.assertEqual(got, k)
            # значение внутри пояса даёт digitize тот же индекс
            inside = upper - 0.5
            self.assertEqual(int(np.digitize([inside], np.asarray(lv))[0]), k)


if __name__ == "__main__":
    unittest.main(verbosity=2)
