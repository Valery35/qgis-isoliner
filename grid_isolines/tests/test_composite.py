# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Приведение проб опробования к целевым интервалам.

Опробование нарезано своей сеткой, литология своей, и совпадают они
редко. Проба лежит внутри слоя, пересекает границу двух или покрывает
несколько, поэтому стыковать надо по глубинам, а не по номеру записи.

Главное правило проверяется здесь отдельно и не один раз: отсутствующий
компонент это отсутствие данных, а не ноль. Подстановка нуля даёт
заведомо ложное среднее, и тем опаснее, чем реже компонент встречается.
Урок ГДЯ, повторять его в другом инструменте незачем.

Проверка идёт без QGIS.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from grid_isolines.drillhole_core import (  # noqa: E402
    composite, composite_summary)


def test_sample_inside_the_target_gives_its_own_value():
    rows = composite([(10.0, 12.0, {"kcl": 30.0})], [(8.0, 14.0)])
    r = rows[0]
    assert r["values"]["kcl"] == 30.0
    assert abs(r["cover"]["kcl"] - 2.0 / 6.0) < 1e-12
    assert r["n_samples"] == 1


def test_sample_on_the_border_is_split_between_two_targets():
    """Проба поперёк границы делится по длине перекрытия."""
    samples = [(9.0, 11.0, {"kcl": 20.0})]
    rows = composite(samples, [(8.0, 10.0), (10.0, 12.0)])
    for r in rows:
        assert r["values"]["kcl"] == 20.0
        assert abs(r["cover"]["kcl"] - 0.5) < 1e-12


def test_several_samples_average_by_length():
    """Средневзвешенное, а не среднее арифметическое."""
    samples = [(0.0, 1.0, {"kcl": 10.0}),
               (1.0, 4.0, {"kcl": 20.0})]
    rows = composite(samples, [(0.0, 4.0)])
    # (1·10 + 3·20) / 4 = 17.5, а среднее по пробам дало бы 15
    assert abs(rows[0]["values"]["kcl"] - 17.5) < 1e-12
    assert abs(rows[0]["cover"]["kcl"] - 1.0) < 1e-12


def test_partial_overlap_is_weighted_partially():
    samples = [(0.0, 10.0, {"kcl": 10.0}),
               (10.0, 20.0, {"kcl": 30.0})]
    rows = composite(samples, [(5.0, 15.0)])
    assert abs(rows[0]["values"]["kcl"] - 20.0) < 1e-12


def test_missing_value_is_excluded_not_zeroed():
    """Тот самый урок ГДЯ.

    Одна проба без брома, вторая с бромом. Среднее по брому обязано
    равняться значению второй пробы, а не половине от него.
    """
    samples = [(0.0, 5.0, {"kcl": 10.0}),
               (5.0, 10.0, {"kcl": 20.0, "br": 0.4})]
    rows = composite(samples, [(0.0, 10.0)])
    r = rows[0]
    assert abs(r["values"]["br"] - 0.4) < 1e-12, (
        "пропуск посчитан нулём: получилось %r" % r["values"]["br"])
    assert abs(r["cover"]["br"] - 0.5) < 1e-12, (
        "охват по брому должен быть половиной интервала")
    assert abs(r["cover"]["kcl"] - 1.0) < 1e-12


def test_missing_component_everywhere_gives_none_not_zero():
    samples = [(0.0, 5.0, {"kcl": 10.0})]
    rows = composite(samples, [(0.0, 5.0)], components=["kcl", "br"])
    assert rows[0]["values"]["br"] is None
    assert rows[0]["cover"]["br"] == 0.0


def test_empty_overlap_gives_zero_cover():
    rows = composite([(100.0, 110.0, {"kcl": 30.0})], [(0.0, 10.0)])
    r = rows[0]
    assert r["n_samples"] == 0
    assert r["cover_any"] == 0.0
    assert r["values"]["kcl"] is None


def test_component_set_is_taken_from_the_data():
    """Состав колонок разный у разных предприятий, требовать нельзя."""
    samples = [(0.0, 1.0, {"kcl": 1.0, "nacl": 2.0})]
    rows = composite(samples, [(0.0, 1.0)])
    assert set(rows[0]["values"]) == {"kcl", "nacl"}


def test_overlapping_samples_are_counted_not_hidden():
    """Две пробы на одну глубину это спор в данных, о нём надо сказать."""
    samples = [(0.0, 6.0, {"kcl": 10.0}),
               (4.0, 10.0, {"kcl": 20.0})]
    rows = composite(samples, [(0.0, 10.0)])
    assert abs(rows[0]["overlap"] - 2.0) < 1e-12
    assert abs(rows[0]["cover_any"] - 1.0) < 1e-12, (
        "охват посчитан по сумме весов, а перекрытие досчиталось дважды")


def test_cover_any_never_exceeds_one():
    samples = [(0.0, 10.0, {"kcl": 1.0}), (0.0, 10.0, {"kcl": 3.0})]
    rows = composite(samples, [(0.0, 10.0)])
    assert rows[0]["cover_any"] <= 1.0 + 1e-12


def test_low_cover_flag_follows_the_threshold():
    samples = [(0.0, 2.0, {"kcl": 10.0})]
    assert composite(samples, [(0.0, 10.0)])[0]["low_cover"] is None
    assert composite(samples, [(0.0, 10.0)], min_cover=0.5)[0]["low_cover"]
    assert not composite(samples, [(0.0, 10.0)],
                         min_cover=0.1)[0]["low_cover"]


def test_reversed_and_broken_rows_are_tolerated():
    """Читатель терпимый: перепутанные глубины меняются местами."""
    samples = [(12.0, 10.0, {"kcl": 30.0}),      # from и to наоборот
               ("", 5.0, {"kcl": 99.0}),          # без глубины
               (1.0, 2.0, {"kcl": "нет"})]        # нечисловое значение
    rows = composite(samples, [(10.0, 12.0), (1.0, 2.0)])
    assert rows[0]["values"]["kcl"] == 30.0
    assert rows[1]["values"]["kcl"] is None


def test_zero_length_target_does_not_divide_by_zero():
    rows = composite([(0.0, 5.0, {"kcl": 10.0})], [(3.0, 3.0)])
    assert rows[0]["length"] == 0.0
    assert rows[0]["cover_any"] == 0.0


def test_target_key_travels_through():
    rows = composite([(0.0, 1.0, {"kcl": 5.0})], [(0.0, 1.0, "КрII")])
    assert rows[0]["key"] == "КрII"


def test_summary_counts_what_the_log_needs():
    samples = [(0.0, 6.0, {"kcl": 10.0}), (4.0, 10.0, {"kcl": 20.0})]
    rows = composite(samples,
                     [(0.0, 10.0), (100.0, 110.0), (0.0, 20.0)],
                     min_cover=0.6)
    s = composite_summary(rows)
    assert s["targets"] == 3
    assert s["empty"] == 1
    assert s["with_overlap"] == 2
    assert s["low_cover"] >= 1
    assert 0.0 < s["mean_cover"] < 1.0
