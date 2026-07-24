# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Образец справочника пластов, поставляемый в templates/. Смысл теста:
# образец обязан читаться тем же кодом, что и справочник пользователя.
# Разъедется контракт колонок или синонимы - образец перестанет быть
# образцом, и человек получит файл, который инструмент не понимает.
#     python grid_isolines/tests/test_plast_template.py
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(MODULE))

from grid_isolines import plast_reference as pr  # noqa: E402

CSV = os.path.join(MODULE, "templates", "plast_reference_vkmks.csv")


class Template(unittest.TestCase):
    """Образец справочника ВКМКС."""

    @classmethod
    def setUpClass(cls):
        cls.summary = pr.ReadSummary()
        cls.ref = pr.Reference.from_csv(CSV, summary=cls.summary)

    def test_shipped_with_the_module(self):
        self.assertTrue(os.path.exists(CSV))
        self.assertTrue(os.path.exists(
            os.path.join(MODULE, "templates", "plast_reference_vkmks.xlsx")))

    def test_every_row_is_accepted(self):
        """Ни одной отброшенной строки: образец не имеет права быть кривым."""
        self.assertEqual(self.summary.total, 37)
        self.assertEqual(self.summary.kept, 37)
        self.assertEqual(self.summary.no_code, 0)
        self.assertEqual(self.summary.bad_order, 0)
        self.assertEqual(self.summary.bad_body, 0)
        self.assertEqual(self.summary.dup, 0)

    def test_spans_the_whole_section(self):
        """Сверху покровные отложения, снизу нижняя каменная соль."""
        beds = sorted(self.ref.beds, key=lambda b: b.order)
        self.assertEqual(beds[0].code, "Q")
        self.assertEqual(beds[-1].code, "НКС")
        # порядок сплошной и без повторов
        self.assertEqual([b.order for b in beds], list(range(1, 38)))

    def test_bodies_that_the_code_cannot_give(self):
        """Тела, которые из кода не выводятся - ради них справочник и нужен.

        АБ по написанию выглядит как «А плюс Б», но это цельный пласт.
        Б-В и А'-КрI выглядят как пласты, а это междупластья: пластов Б и
        А' в списке нет, есть АБ. Определяет геолог, не машина.
        """
        self.assertFalse(self.ref.get("АБ").is_interbed)
        self.assertTrue(self.ref.get("Б-В").is_interbed)
        self.assertTrue(self.ref.get("А'-КрI").is_interbed)

    def test_composite_bed_is_present(self):
        """КрIIIа+б это сумма трёх тел и самостоятельная единица картирования.

        В исходной палитре есть только составной код, отдельных КрIIIа,
        КрIIIа-б и КрIIIб в ней нет. В справочнике стоят и части, и целое:
        разрез строят на обеих гранулярностях.
        """
        c = self.ref.get("КрIIIа+б")
        self.assertIsNotNone(c)
        self.assertFalse(c.is_interbed)
        # стоит выше КрIIIб-в и ниже своих частей
        self.assertGreater(c.order, self.ref.get("КрIIIб").order)
        self.assertLess(c.order, self.ref.get("КрIIIб-в").order)
        # серый, как категория-ловушка «прочее»: цвет составного тела из
        # частей не выводится, его ставит человек
        self.assertEqual(c.color, "#969696")

    def test_composite_and_parts_share_one_order(self):
        """Цена соседства целого с частями, зафиксирована сознательно.

        Составное тело и его части лежат в одном линейном списке, поэтому
        между КрIIIб и КрIIIв теперь два шага, а не один, и пара границ
        мелкой гранулярности даёт «many» вместо имени междупластья. На
        крупной гранулярности всё сходится.
        """
        self.assertEqual(self.ref.between("КрIIIб", "КрIIIв"), "many")
        self.assertEqual(
            self.ref.between("КрIIIа+б", "КрIIIв").code, "КрIIIб-в")

    def test_tolerant_lookup_on_real_codes(self):
        """Написание кода в данных не обязано совпадать дословно."""
        ab = self.ref.get("АБ")
        for spelling in ("А'Б", "аб", " АБ "):
            self.assertEqual(self.ref.get(spelling), ab, spelling)

    def test_every_row_has_a_colour(self):
        for b in self.ref.beds:
            self.assertRegex(b.color or "", r"^#[0-9a-fA-F]{6}$", b.code)

    def test_interbed_between_two_roofs(self):
        """Между КрII и КрIIIа по порядку лежит междупластье КрII-КрIII."""
        got = self.ref.between("КрII", "КрIIIа")
        self.assertIsNotNone(got)
        self.assertEqual(got.code, "КрII-КрIII")
        self.assertTrue(got.is_interbed)


if __name__ == "__main__":
    unittest.main()
