# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Сторона бергштрихов:
#     python grid_isolines/tests/test_hatch_side.py
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(HERE), "isolines.py")
ALG = os.path.join(os.path.dirname(HERE), "algorithms.py")


def _fn(name, path=SRC):
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    found = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
             and n.name == name]
    assert found, "функция %s не найдена" % name
    return ast.get_source_segment(src, found[0])


def test_no_guessing_by_qgis_version():
    """Переворот по номеру версии убран как неверный.

    Гипотеза «в QGIS 4 сторона перевернулась» проверена на живой машине с
    QGIS 4.0.3: с переворотом штрихи легли вверх по склону, без переворота
    верно. Возврата к угадыванию быть не должно.
    """
    body = _fn("_add_slope_side")
    assert "ver >= 40000" not in body, "переворот по версии вернулся"
    assert "40000" not in body, "версия не должна влиять на знак"


def test_manual_override_exists():
    body = _fn("_add_slope_side")
    assert "flip > 0" in body and "flip < 0" in body


def test_choice_is_printed_to_screen():
    """Выбор должен быть виден в журнале: это единственный канал отладки."""
    body = _fn("_add_slope_side")
    assert "pushInfo" in body
    assert "Бергштрихи" in body


def test_side_is_computed_after_orientation():
    """Разворот линий меняет местами лево и право.

    Если сторону склона посчитать до разворота, у развёрнутых линий штрихи
    лягут вверх по склону. Проявляется только при одновременно включённых
    депрессионном стиле и топографических подписях.
    """
    body = _fn("_finalize_lines")
    i_up = body.find("_orient_uphill(")
    i_dn = body.find("_add_slope_side(")
    assert -1 < i_up < i_dn, (i_up, i_dn)


def test_tool_exposes_the_switch():
    with open(ALG, encoding="utf-8") as fh:
        src = fh.read()
    assert 'HATCH = "HATCH"' in src
    assert "Сторона бергштрихов" in src
    # переключатель должен доходить до обеих веток, с полигонами и без
    assert src.count("hatch_flip={0: 0, 1: 1, 2: -1}") == 2


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL %s: %s" % (name, exc))
    print("%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
