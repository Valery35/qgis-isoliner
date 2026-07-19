# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Топографическая ориентация горизонталей:
#     python grid_isolines/tests/test_isolines_uphill.py
import ast
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "isolines.py")


class UphillOrientation(unittest.TestCase):
    """Разворот горизонталей вверх по склону и его связь с бергштрихами."""

    @classmethod
    def setUpClass(cls):
        with open(SRC, encoding="utf-8") as fh:
            cls.src = fh.read()
        cls.tree = ast.parse(cls.src)

    def _fn(self, name):
        found = [n for n in ast.walk(self.tree)
                 if isinstance(n, ast.FunctionDef) and n.name == name]
        self.assertTrue(found, "функция %s не найдена" % name)
        return ast.get_source_segment(self.src, found[0])

    def test_orient_uphill_exists(self):
        self.assertIn("def _orient_uphill(", self.src)

    def test_orientation_runs_before_slope_side(self):
        """Порядок принципиален.

        Разворот линии меняет местами лево и право. Если dn_sign посчитан до
        разворота, у развёрнутых линий бергштрихи лягут вверх по склону.
        Дефект виден только когда одновременно включены депрессионный стиль и
        топографические подписи, поэтому закрепляем порядок тестом.
        """
        body = self._fn("_finalize_lines")
        i_up = body.find("_orient_uphill(")
        i_dn = body.find("_add_slope_side(")
        self.assertGreater(i_up, -1, "разворот не вызывается")
        self.assertGreater(i_dn, -1, "сторона склона не вычисляется")
        self.assertLess(i_up, i_dn,
                        "разворот обязан идти до расчёта стороны склона")

    def test_orientation_side_is_verified_by_experiment(self):
        """Сторона проверена на живой машине, а не выведена из предположения.

        Первоначально считалось, что верх подписи смотрит в сторону высокой
        стороны, когда та слева. На QGIS 4.0.3 подписи встали в сторону
        убывания, значит верно обратное: высокая сторона должна быть справа.
        """
        body = self._fn("_orient_uphill")
        self.assertIn("@vr >= @vl", body)
        self.assertNotIn("@vl >= @vr", body)
        self.assertIn("reverse($geometry)", body)

    def test_orientation_keeps_flag_field(self):
        body = self._fn("_orient_uphill")
        self.assertIn("up_side", body)
        self.assertNotIn("up_left", body)

    def test_orientation_failure_is_not_fatal(self):
        """Не получилось развернуть - выходим с обычными линиями."""
        body = self._fn("_finalize_lines")
        seg = body[body.find("_orient_uphill(") - 200:]
        self.assertIn("pushWarning", seg)

    def test_tool_exposes_option(self):
        alg = os.path.join(os.path.dirname(HERE), "algorithms.py")
        with open(alg, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('UPHILL = "UPHILL"', src)
        self.assertIn("вверх по склону", src)


if __name__ == "__main__":
    unittest.main()
