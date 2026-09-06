# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Откат кригинга на обратные расстояния должен быть виден.

Когда система кригинга не решается или даёт неразумную оценку, ядро
считает ячейку по обратным расстояниям до тех же соседей и берёт
наибольшую возможную дисперсию. Это разумный запас, но раньше он
срабатывал молча: карта выглядела обычной, хотя часть её посчитана не
тем методом, который заказан, и карта стандартной ошибки там завышена.

Через окно инструмента вырожденную систему подать не удалось, поэтому
отказ решателя здесь подставляется прямо: проверяется не то, что он
случается, а то, что случившийся отказ сосчитан и доложен.

Проверка идёт без QGIS.
"""
import ast
import os
import sys
from unittest import mock

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from grid_isolines import kb2d  # noqa: E402

PKG = os.path.dirname(HERE)


def _samples():
    xs = np.array([0.0, 100.0, 0.0, 100.0])
    ys = np.array([0.0, 0.0, 100.0, 100.0])
    vs = np.array([10.0, 20.0, 30.0, 40.0])
    return xs, ys, vs


def _vg():
    return kb2d.Variogram(1.0, [dict(it=1, cc=99.0, aa=500.0,
                                     ang=0.0, anis=1.0)])


def test_fallback_is_counted():
    """Отказ решателя увеличивает счётчик, а не проходит незамеченным."""
    xs, ys, vs = _samples()
    tally = [0]
    with mock.patch.object(np.linalg, "solve",
                           side_effect=np.linalg.LinAlgError("подстановка")):
        est, var = kb2d._solve_point(30.0, 40.0, xs, ys, vs, _vg(),
                                     1, 0.0, 1, 4, 1e18, -9999.0,
                                     tally=tally)
    assert tally[0] == 1, "откат сработал, но не сосчитан"
    assert np.isfinite(est)


def test_fallback_value_is_inverse_distance():
    """Значение запаса это именно обратные расстояния до тех же соседей.

    Иначе счётчик считал бы одно, а карта показывала другое.
    """
    xs, ys, vs = _samples()
    xloc, yloc = 30.0, 40.0
    h2 = (xs - xloc) ** 2 + (ys - yloc) ** 2
    wts = 1.0 / np.maximum(h2, kb2d.EPS)
    expected = float(np.dot(wts, vs) / wts.sum())
    with mock.patch.object(np.linalg, "solve",
                           side_effect=np.linalg.LinAlgError("подстановка")):
        est, _ = kb2d._solve_point(xloc, yloc, xs, ys, vs, _vg(),
                                   1, 0.0, 1, 4, 1e18, -9999.0)
    assert abs(est - expected) < 1e-9


def test_grid_reports_share_of_fallback_cells():
    """build_grid складывает счётчик в stats вместе с числом ячеек.

    Без числа оценённых ячеек счётчик нельзя перевести в долю площади, а
    доля и есть то, по чему решают, доверять карте или переделывать
    вариограмму.
    """
    xs, ys, vs = _samples()
    stats = {}
    with mock.patch.object(np.linalg, "solve",
                           side_effect=np.linalg.LinAlgError("подстановка")):
        grid = kb2d.build_grid(xs, ys, vs, _vg(), 1, 0.0, 1, 4, 1e18,
                               -9999.0, 10.0, 10.0, 40.0, 3, 3,
                               stats=stats, use_cache=False)
    assert stats["est_cells"] == 9
    assert stats["idw_cells"] == 9, (
        "не все ячейки запаса сосчитаны: %r" % stats)
    assert np.all(grid != -9999.0)


def test_clean_run_reports_zero_not_silence():
    """На здоровых данных счётчик равен нулю и всё равно присутствует.

    Ноль отличает «запас ни разу не понадобился» от «счётчик забыли».
    """
    xs, ys, vs = _samples()
    stats = {}
    kb2d.build_grid(xs, ys, vs, _vg(), 1, 0.0, 1, 4, 1e18, -9999.0,
                    10.0, 10.0, 40.0, 3, 3, stats=stats, use_cache=False)
    assert stats["idw_cells"] == 0
    assert stats["est_cells"] == 9


def test_tool_puts_the_count_in_the_log():
    """Инструмент кригинга обязан вынести это в журнал.

    Ядро считает, но пользователь читает журнал. Разбор по AST, без
    запуска QGIS.
    """
    with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_kriging_to_tiff":
            body = ast.get_source_segment(text, node) or ""
            break
    else:
        raise AssertionError("_run_kriging_to_tiff пропала из algorithms.py")
    assert 'kstats.get("idw_cells"' in body, (
        "инструмент снова не смотрит счётчик отката")
    at = body.find('kstats.get("idw_cells"')
    tail = body[at:at + 1400]
    assert "pushWarning" in tail, "заметная доля отката идёт не предупреждением"
    assert "pushInfo" in tail, "единичные ячейки отката нигде не названы"
