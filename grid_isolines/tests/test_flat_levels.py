# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Диагностика уровней, попавших в плоские площадки:
#     python grid_isolines/tests/test_flat_levels.py
import ast
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import validate_core as vc  # noqa: E402


def _slope_with_lake(n=200, cell=5.0, lake_z=399.80, noise=0.027, seed=0):
    """Склон, у которого нижняя половина залита водой на постоянной отметке.

    Числа взяты с натуры: на матрице заказчика водная гладь занимала 44.5
    процента площади, стояла на отметке около 399.8 и гуляла в пределах СКО
    2.7 см. Именно такая гладь и рождает утолщённую горизонталь.
    """
    rng = np.random.default_rng(seed)
    j, i = np.mgrid[0:n, 0:n]
    z = 399.0 + 0.02 * j * cell
    lake = j < n // 2
    # Шум глади ПРОСТРАНСТВЕННО СВЯЗНЫЙ, а не белый. Это принципиально: у
    # настоящей воды в матрице локальная шероховатость миллиметровая, а размах
    # по всей глади сантиметровый. Белый шум дал бы большой перепад между
    # соседними ячейками, и площадка перестала бы считаться плоской, хотя
    # глазами она плоская.
    k = 10
    coarse = rng.normal(0.0, noise, (n // k + 1, n // k + 1))
    field = np.repeat(np.repeat(coarse, k, axis=0), k, axis=1)[:n, :n]
    z[lake] = lake_z + field[lake]
    return z, lake


def test_finds_level_inside_the_lake():
    z, lake = _slope_with_lake()
    levels = [399.0 + 0.2 * k for k in range(12)]
    hits = vc.flat_level_hits(z, 5.0, levels, 0.2)
    assert hits, "уровень внутри глади не найден"
    worst = hits[0]
    assert abs(worst["level"] - 399.8) < 1e-9, worst["level"]
    assert worst["n_flat"] > 100, worst["n_flat"]
    # уровень внутри глади должен быть на порядок хуже соседних
    others = [h["n_flat"] for h in hits[1:]] or [0]
    assert worst["n_flat"] > 10 * max(others), (worst["n_flat"], others)


def test_level_outside_the_lake_is_clean():
    z, _ = _slope_with_lake()
    hits = vc.flat_level_hits(z, 5.0, [400.4, 401.0], 0.2)
    assert not [h for h in hits if h["n_flat"] > 100], hits


def test_no_hits_on_a_clean_slope():
    n = 150
    j, i = np.mgrid[0:n, 0:n]
    z = 100.0 + 0.05 * j * 5.0
    hits = vc.flat_level_hits(z, 5.0, [102.0, 104.0, 106.0], 0.5)
    assert not hits, hits


def test_sorted_by_severity():
    z, _ = _slope_with_lake()
    levels = [399.0 + 0.2 * k for k in range(12)]
    hits = vc.flat_level_hits(z, 5.0, levels, 0.2)
    counts = [h["n_flat"] for h in hits]
    assert counts == sorted(counts, reverse=True), counts


def test_share_is_of_valid_cells():
    z, lake = _slope_with_lake()
    hits = vc.flat_level_hits(z, 5.0, [399.8], 0.2)
    assert 0.0 < hits[0]["share_valid"] <= 1.0
    assert abs(hits[0]["share_valid"] - hits[0]["n_flat"] / z.size) < 1e-9


def test_empty_inputs():
    z, _ = _slope_with_lake()
    assert vc.flat_level_hits(z, 5.0, [], 0.2) == []
    assert vc.flat_level_hits(z, 5.0, [399.8], 0.0) == []


def test_nan_only_raster():
    z = np.full((40, 40), np.nan)
    assert vc.flat_level_hits(z, 5.0, [10.0], 0.5) == []


def test_wired_into_contouring():
    """Диагностика обязана вызываться до gdal:contour, а не после."""
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "isolines.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
          and n.name == "_contour_lines"][0]
    body = ast.get_source_segment(src, fn)
    i_warn = body.find("_warn_flat_levels(")
    i_run = body.find("gdal:contour")
    assert i_warn > -1, "диагностика не вызывается"
    assert i_warn < i_run, "диагностика должна идти до контуринга"


def test_diagnostics_never_break_the_run():
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "isolines.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    fn = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
          and n.name == "_warn_flat_levels"][0]
    body = ast.get_source_segment(src, fn)
    assert "except Exception" in body
    assert "max_cells" in body, "нужен предохранитель по размеру растра"


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
