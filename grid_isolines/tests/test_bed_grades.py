# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""4.13 сводит пробы к одному числу на скважину, не теряя охвата.

Инструмент замыкает цепочку «база - разрез - карта»: пробы опробования
приводятся к интервалам заданного пласта и кладутся в точку устья, откуда
их берёт кригинг. Сама математика живёт в ядре и проверяется в
test_composite. Здесь проверяется то, что делает уже инструмент: сведение
нескольких интервалов одного пласта в одно число и правила, записанные в
его тексте.

Разбор идёт по исходнику, без запуска QGIS.
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(PKG))

from grid_isolines.drillhole_core import composite  # noqa: E402


def _class_source(name):
    with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return ast.get_source_segment(text, node) or ""
    raise AssertionError("класс %s пропал из algorithms.py" % name)


def _merge(rows, comps):
    """То же сведение, что делает инструмент: вес это длина с данными."""
    total = sum(r["length"] for r in rows)
    vals, covs = {}, {}
    for c in comps:
        wsum = sum(r["length"] * r["cover"][c] for r in rows)
        if wsum > 0:
            vsum = sum(r["length"] * r["cover"][c] * r["values"][c]
                       for r in rows if r["values"][c] is not None)
            vals[c] = vsum / wsum
        else:
            vals[c] = None
        covs[c] = wsum / total
    return vals, covs


def test_bed_met_twice_is_reduced_to_one_number():
    """Пласт вскрыт стволом дважды: оба интервала весят по длине с данными."""
    samples = [(0.0, 10.0, {"kcl": 10.0}),
               (100.0, 130.0, {"kcl": 30.0})]
    targets = [(0.0, 10.0), (100.0, 130.0)]
    rows = composite(samples, targets, components=["kcl"])
    vals, covs = _merge(rows, ["kcl"])
    # (10·10 + 30·30) / 40 = 25, а простое среднее по интервалам дало бы 20
    assert abs(vals["kcl"] - 25.0) < 1e-9
    assert abs(covs["kcl"] - 1.0) < 1e-9


def test_component_missing_in_one_of_the_two_intervals():
    """Охват по компоненту падает, среднее считается по обеспеченной части."""
    samples = [(0.0, 10.0, {"kcl": 10.0, "br": 0.2}),
               (100.0, 130.0, {"kcl": 30.0})]
    targets = [(0.0, 10.0), (100.0, 130.0)]
    rows = composite(samples, targets, components=["kcl", "br"])
    vals, covs = _merge(rows, ["kcl", "br"])
    assert abs(vals["br"] - 0.2) < 1e-9, "пропуск посчитан нулём"
    assert abs(covs["br"] - 0.25) < 1e-9, "охват по брому не четверть"
    assert abs(covs["kcl"] - 1.0) < 1e-9


def test_hole_without_samples_gives_none_not_zero():
    rows = composite([], [(0.0, 10.0)], components=["kcl"])
    vals, covs = _merge(rows, ["kcl"])
    assert vals["kcl"] is None
    assert covs["kcl"] == 0.0


def test_tool_is_registered_and_numbered():
    body = _class_source("BedGradesAtCollarsAlgorithm")
    assert '"4.13 Содержания по пласту в устьях"' in body, (
        "имя инструмента изменилось, а руководство сверяется с панелью")
    assert 'def name(self): return "bed_grades_at_collars"' in body
    with open(os.path.join(PKG, "algorithms.py"), encoding="utf-8") as fh:
        text = fh.read()
    at = text.find("ALGORITHMS = [")
    assert "BedGradesAtCollarsAlgorithm," in text[at:], (
        "инструмент не внесён в список ALGORITHMS и в панели не появится")


def test_missing_grade_is_not_substituted_by_zero():
    """В коде чтения проб не должно быть подстановки нуля.

    Именно здесь ошибка стоила бы дороже всего: пустая ячейка означает,
    что компонент не определяли, а не что его нет в породе.
    """
    body = _class_source("BedGradesAtCollarsAlgorithm")
    at = body.find("for c in comps:")
    assert at != -1, "чтение содержаний пропало"
    block = body[at:at + 300]
    assert "parse_num" in block
    assert "if num is not None" in block, (
        "значение кладётся в пробу без проверки на пустоту")
    assert "0.0)" not in block and "or 0" not in block, (
        "в чтении содержаний появилась подстановка нуля")


def test_coverage_is_per_component_not_one_number():
    body = _class_source("BedGradesAtCollarsAlgorithm")
    assert '"cov_" + c' in body, (
        "охват перестал считаться по каждому компоненту отдельно")
    assert 'QgsField("cover"' in body, "общий охват пласта пропал"


def test_low_coverage_rows_are_kept_and_reported():
    """Отбор за пользователя не делается, но о числе таких скважин говорят."""
    body = _class_source("BedGradesAtCollarsAlgorithm")
    assert "с охватом ниже порога" in body, "низкий охват не попадает в журнал"
    assert "pushWarning" in body
    assert "continue" not in body[body.find("cover_any < min_cover"):
                                  body.find("cover_any < min_cover") + 200], (
        "скважина с низким охватом выбрасывается из слоя")


def test_overlapping_samples_reach_the_log():
    body = _class_source("BedGradesAtCollarsAlgorithm")
    assert "с перекрытием проб" in body, (
        "перекрытия проб снова замалчиваются")
