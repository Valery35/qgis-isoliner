# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Векторные границы по вероятностям индикаторного кригинга (3.01).
# Задача пришла с ВКМКС: минтип В опасен по ГДЯ, Н нет, и для планирования
# нужна не одна линия «где победил В», а полоса перехода - уверенно нет,
# спорно, уверенно да.
#     python grid_isolines/tests/test_prob_contours.py
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "algorithms.py")


class ProbLevels(unittest.TestCase):
    """Разбор строки уровней."""

    @classmethod
    def setUpClass(cls):
        with open(SRC, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        fn = [n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_parse_levels"]
        assert fn, "_parse_levels не найдена"
        body = ast.get_source_segment(src, fn[0])
        # снимаем декоратор staticmethod: разбираем чистую функцию
        ns = {}
        exec(compile(body, "levels", "exec"), ns)  # nosec
        cls.parse = staticmethod(ns["_parse_levels"])
        cls.src = src

    def test_default_string(self):
        self.assertEqual(self.parse("0.25 0.5 0.75"), [0.25, 0.5, 0.75])

    def test_separators_and_order(self):
        """Запятая и точка с запятой годятся, порядок наводится сам."""
        self.assertEqual(self.parse("0.75, 0.25; 0.5"), [0.25, 0.5, 0.75])

    def test_duplicates_removed(self):
        self.assertEqual(self.parse("0.5 0.5 0.50"), [0.5])

    def test_endpoints_dropped(self):
        """0 и 1 отбрасываются: контур по краю диапазона бесполезен."""
        self.assertEqual(self.parse("0 0.5 1"), [0.5])
        self.assertEqual(self.parse("-0.2 1.5"), [])

    def test_garbage_is_ignored_not_fatal(self):
        self.assertEqual(self.parse("0.3 abc 0.7"), [0.3, 0.7])
        self.assertEqual(self.parse(""), [])
        self.assertEqual(self.parse(None), [])


class Bands(unittest.TestCase):
    """Полосы, подписи и цвета легенды."""

    @classmethod
    def setUpClass(cls):
        with open(SRC, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        ns = {}
        for name in ("_bands_from_levels", "_band_colors", "_band_formula"):
            fn = [n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == name][0]
            exec(compile(ast.get_source_segment(src, fn), name, "exec"),  # nosec
                 ns)

        class _Alg(object):
            _PROB_RAMP = ("#1a9850", "#91cf60", "#d9ef8b",
                          "#fee08b", "#fc8d59", "#d73027")
            _band_colors = staticmethod(ns["_band_colors"])

        ns["CategoricalIndicatorAlgorithm"] = _Alg
        cls.ns = ns
        cls.src = src

    def test_three_levels_give_four_bands(self):
        """Легенда должна иметь ровно столько категорий, сколько полос.

        Раскраска по одной границе давала пять категорий на три уровня -
        отсюда и «лишние уровни» в легенде.
        """
        got = self.ns["_bands_from_levels"]([0.25, 0.5, 0.75])
        self.assertEqual([b[2] for b in got],
                         ["0 - 0.25", "0.25 - 0.5", "0.5 - 0.75", "0.75 - 1"])
        self.assertEqual(got[0][0], 0.0)
        self.assertEqual(got[-1][1], 1.0)

    def test_labels_have_no_trailing_zeros(self):
        """%g даёт «0.5», а не «0.50» - подпись читает человек."""
        got = self.ns["_bands_from_levels"]([0.1, 0.9])
        self.assertEqual([b[2] for b in got],
                         ["0 - 0.1", "0.1 - 0.9", "0.9 - 1"])

    def test_single_level_still_works(self):
        got = self.ns["_bands_from_levels"]([0.5])
        self.assertEqual([b[2] for b in got], ["0 - 0.5", "0.5 - 1"])

    def test_colors_run_green_to_red(self):
        c = self.ns["_band_colors"](4)
        self.assertEqual(len(c), 4)
        self.assertEqual(c[0], "#1a9850")     # уверенно нет
        self.assertEqual(c[-1], "#d73027")    # уверенно да
        self.assertEqual(len(set(c)), 4)      # полосы различимы

    def test_colors_scale_to_any_band_count(self):
        for n in (2, 3, 5, 6):
            c = self.ns["_band_colors"](n)
            self.assertEqual(len(c), n)
            self.assertEqual(c[0], "#1a9850")
            self.assertEqual(c[-1], "#d73027")
        self.assertEqual(len(self.ns["_band_colors"](1)), 1)

    def test_formula_matches_by_upper_bound(self):
        """Сопоставление по верхней границе: у нижней крайние полосы зажаты."""
        bands = self.ns["_bands_from_levels"]([0.25, 0.5, 0.75])
        f = self.ns["_band_formula"](bands)
        self.assertTrue(f.startswith("CASE"))
        self.assertTrue(f.endswith("END"))
        self.assertIn('"P_MAX" <= 0.250001', f)
        self.assertNotIn("P_MIN", f)
        # последняя полоса ловится через ELSE, отдельного WHEN у неё нет
        self.assertEqual(f.count("WHEN"), len(bands) - 1)
        self.assertIn("ELSE '0.75 - 1'", f)

    def test_formula_escapes_quotes(self):
        f = self.ns["_band_formula"]([(0.0, 1.0, "a'b")])
        self.assertIn("a''b", f)


class Wiring(unittest.TestCase):
    """Обвязка инструмента 3.01."""

    @classmethod
    def setUpClass(cls):
        with open(SRC, encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_outputs_are_optional(self):
        """Старое поведение не меняется: векторы по умолчанию не строятся."""
        i = self.src.index('self.OUTPUT_LINES, self.tr(')
        seg = self.src[i:i + 260]
        self.assertIn("optional=True", seg)
        self.assertIn("createByDefault=False", seg)
        j = self.src.index('self.OUTPUT_BANDS, self.tr(')
        seg = self.src[j:j + 260]
        self.assertIn("optional=True", seg)
        self.assertIn("createByDefault=False", seg)

    def test_built_from_probabilities_not_zones(self):
        """Ключевое решение: источник - канал вероятностей.

        Карта зон хранит только победителя в ячейке, положение границы
        внутри ячейки в ней уже потеряно, и контур такой карты идёт
        ступенями по краям ячеек.
        """
        i = self.src.index("def _prob_contours(")
        body = self.src[i:self.src.index("\n    @staticmethod", i)]
        self.assertIn("prob_path", body)
        self.assertNotIn("zone_path", body)
        self.assertNotIn("zone,", body)

    def test_class_is_written_into_attributes(self):
        """Без имени класса полосы после слияния неразличимы."""
        self.assertIn('"FIELD_NAME": "class"', self.src)
        self.assertIn("def _tag_class(", self.src)

    def test_unknown_class_is_a_clear_error(self):
        i = self.src.index("def _prob_contours(")
        body = self.src[i:i + 4000]
        self.assertIn("не найден", body)
        self.assertIn("QgsProcessingException", body)

    def test_single_class_avoids_merge(self):
        """Один класс - без слияния: лишний прогон и лишние поля ни к чему."""
        i = self.src.index("def _collect(")
        body = self.src[i:i + 900]
        self.assertIn("len(paths) == 1", body)
        self.assertIn("native:mergevectorlayers", body)
        # запись в выход общая для обеих веток и живёт в чистке
        self.assertIn("native:savefeatures", self.src[self.src.index("def _tidy("):])

    def test_merge_housekeeping_fields_are_dropped(self):
        """layer и path дописывает слияние: имя и URI временного слоя.

        В готовом слое это мусор - класс уже лежит в поле class.
        """
        i = self.src.index("def _tidy(")
        body = self.src[i:self.src.index("\n    @staticmethod", i)]
        self.assertIn('"layer", "path"', body)
        self.assertIn("native:deletecolumn", body)
        # чистка не должна падать, когда полей нет (один класс, без слияния)
        self.assertIn("if n in names", body)
        self.assertIn("if drop:", body)

    def test_band_bounds_renamed_and_clamped(self):
        """ELEV_MIN/ELEV_MAX это отметки, а здесь доли вероятности.

        Крайние полосы вдобавок выходят за диапазон на тысячные, и в
        атрибуте появляется отрицательная вероятность.
        """
        i = self.src.index("def _tidy(")
        body = self.src[i:self.src.index("\n    @staticmethod", i)]
        self.assertIn('("ELEV_MIN", "P_MIN")', body)
        self.assertIn('("ELEV_MAX", "P_MAX")', body)
        self.assertIn('max(0, min(1, ', body)
        # переименование только для полигонов, у линий уровень лежит в P
        self.assertIn("gtype == 2", body)

    def test_tidy_runs_for_single_class_too(self):
        """Один класс идёт мимо слияния, но чистку проходит наравне."""
        i = self.src.index("def _collect(")
        body = self.src[i:self.src.index("\n    def _process", i)]
        self.assertIn("merged = paths[0]", body)
        self.assertIn("_tidy(", body)
        # запись в выход теперь одна, общая для обеих веток
        self.assertEqual(body.count("native:savefeatures"), 0)

    def test_legend_is_attached_by_band_not_by_one_bound(self):
        """Категориальный отрисовщик вешается на поле band.

        По одной границе категорий выходило больше, чем полос: полоса,
        разбитая на несколько кусков, добавляла лишнюю строку в легенду.
        """
        i = self.src.index("def _prob_contours(")
        body = self.src[i:self.src.index("\n    @staticmethod", i)]
        self.assertIn('_attach_categories(', body)
        self.assertIn('"band"', body)
        self.assertIn("_band_colors(len(bands))", body)
        # линиям легенда не навязывается
        self.assertIn("if gtype == 2:", body)

    def test_band_field_is_text(self):
        i = self.src.index("def _tidy(")
        body = self.src[i:self.src.index("\n    @staticmethod", i)]
        j = body.index('"FIELD_NAME": "band"')
        self.assertIn('"FIELD_TYPE": 2', body[j:j + 120])

    def test_reuses_existing_contour_machinery(self):
        """Сглаживание и полигоны берутся готовые, а не пишутся заново."""
        i = self.src.index("def _prob_contours(")
        body = self.src[i:i + 4000]
        self.assertIn("isolines_and_polygons(", body)


if __name__ == "__main__":
    unittest.main()
