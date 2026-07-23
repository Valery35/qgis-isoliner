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

    def _call_orient(self, flip):
        """Исполнить _orient_uphill на заглушках и вернуть формулу up_side.

        Из QGIS функция берёт только processing, поэтому её исходник
        выполняется в контейнере, и видно, какое выражение она собирает.
        """
        ns = {"_tr": lambda s: s}
        exec(compile(self._fn("_orient_uphill"), "orient", "exec"), ns)  # nosec
        seen = []

        class _P(object):
            def run(self, name, params, **kw):
                seen.append(params)
                return {"OUTPUT": "tmp"}

        class _F(object):
            def pushInfo(self, msg):
                pass

        ns["_orient_uphill"](_P(), "in", ("rid", 1, 5.0), None, _F(),
                             flip=flip)
        return seen[0]["FORMULA"]

    def test_flip_inverts_label_orientation(self):
        """«Перевернуть» разворачивает и подписи, а не одни бергштрихи.

        Подпись в QGIS отсчитывает верх текста от направления линии,
        поэтому единственный рычаг здесь - направление геометрии. При
        перевороте условие сохранения линии инвертируется.
        """
        normal = self._call_orient(0)
        flipped = self._call_orient(-1)
        self.assertIn("WHEN @vr >= @vl THEN 1 ELSE 0 END", normal)
        self.assertIn("WHEN @vr >= @vl THEN 0 ELSE 1 END", flipped)
        # «не переворачивать» ведёт себя как автоматический выбор
        self.assertEqual(self._call_orient(1), normal)
        # опрос растра по обе стороны линии не изменился
        for e in (normal, flipped):
            self.assertIn("radians(@a + 90)", e)
            self.assertIn("radians(@a - 90)", e)

    def test_flip_applied_once_not_twice(self):
        """Переворот применяется ровно один раз.

        Разворот геометрии сам меняет местами лево и право, а значит и
        знак dn_sign. Если после него применить переворот ещё и в
        _add_slope_side, два переворота погасят друг друга и штрихи
        вернутся на прежнюю сторону: починка подписей сломала бы
        бергштрихи.
        """
        body = self._fn("_finalize_lines")
        self.assertIn("flip=hatch_flip", body)
        self.assertIn("flip=0 if uphill_done else hatch_flip", body)
        # флаг ставится только после удавшегося разворота: если разворот
        # упал, геометрия осталась прежней и переворот обязан достаться
        # бергштрихам
        self.assertLess(body.index("_orient_uphill(processing"),
                        body.index("uphill_done = True"))
        self.assertEqual(body.count("uphill_done = False"), 1)

    def test_switch_name_covers_labels(self):
        """Имя переключателя больше не обещает одни бергштрихи."""
        alg = os.path.join(os.path.dirname(HERE), "algorithms.py")
        with open(alg, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('self.tr("Сторона бергштрихов и подписей")', src)

    def test_tool_exposes_option(self):
        alg = os.path.join(os.path.dirname(HERE), "algorithms.py")
        with open(alg, encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn('UPHILL = "UPHILL"', src)
        self.assertIn("вверх по склону", src)


if __name__ == "__main__":
    unittest.main()
