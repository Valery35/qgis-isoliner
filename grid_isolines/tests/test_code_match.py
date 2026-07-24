# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Сопоставление кодов и разбор имён поверхностей.

Раньше эти правила обслуживали палитру Leapfrog, теперь - справочник
пластов. Источник цвета сменился, а написание кодов в данных осталось
прежним, поэтому правила пережили палитру и проверяются отдельно.

    python grid_isolines/tests/test_palette_lfc.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from grid_isolines import palette_lfc as pl  # noqa: E402


def test_strip_role():
    assert pl.strip_role("KpII_top") == "KpII"
    assert pl.strip_role("B_bottom") == "B"
    assert pl.strip_role("КрII_кровля") == "КрII"
    assert pl.strip_role("top_В") == "В"
    assert pl.strip_role("АБ") == "АБ"          # без роли не трогаем
    assert pl.strip_role("_top") == "_top"      # имя из одной роли не режем


def test_fold_homoglyphs():
    # латинские B, K, p из имён слоёв в кириллицу
    assert pl.fold_homoglyphs("B") == "В"
    assert pl.fold_homoglyphs("KpII") == "КрII"
    assert pl.fold_homoglyphs("КрII") == "КрII"   # уже кириллица


def test_normalize_and_loose():
    assert pl.normalize_code("KpII_top") == "крii"
    assert pl.normalize_code(" B_bottom ") == "в"
    # дефис различает пласт и межпластье, апостроф снимается только в loose
    assert pl.normalize_code("КрI-КрII") == "крi-крii"
    assert pl.loose_code("A'Б_top") == "аб"
    assert pl.loose_code("КрI-КрII") == "крi-крii"


def test_surface_role():
    assert pl.surface_role("KpII_top") == ("top", "KpII")
    assert pl.surface_role("KpII_bottom") == ("bottom", "KpII")
    assert pl.surface_role("В_кровля") == ("top", "В")
    assert pl.surface_role("В_подошва") == ("bottom", "В")
    assert pl.surface_role("рельеф") == (None, "рельеф")
    assert pl.surface_role("top_В") == ("top", "В")


def test_body_from_pair_bed():
    """Кровля и подошва одного имени это пласт."""
    assert pl.body_from_pair("KpII_top", "KpII_bottom") == ("KpII", "bed")
    assert pl.body_from_pair("B_top", "B_bottom") == ("B", "bed")
    # регистр и латинские двойники не мешают опознать пару
    assert pl.body_from_pair("КрII_top", "KpII_bottom")[1] == "bed"


def test_body_from_pair_interbed():
    """Подошва верхнего и кровля нижнего это межпластье, верхний первым."""
    code, kind = pl.body_from_pair("KpI_bottom", "KpII_top")
    assert (code, kind) == ("KpI-KpII", "interbed")
    code, kind = pl.body_from_pair("B_bottom", "Г_top")
    assert (code, kind) == ("B-Г", "interbed")


def test_body_from_pair_unknown():
    """Пара не по конвенции - ничего не выдумываем."""
    assert pl.body_from_pair("рельеф", "B_top") == (None, None)
    assert pl.body_from_pair("B_top", "KpII_top") == (None, None)
    assert pl.body_from_pair("B_bottom", "KpII_bottom") == (None, None)


if __name__ == "__main__":
    import unittest
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("[ok]", name)
    print("ALL TESTS PASSED")
