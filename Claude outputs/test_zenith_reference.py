# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Отсчёт вертикали в инклинометрии определяется по данным.

Соглашение в базах разное. Где-то зенит отсчитывают от вертикали, и
вертикальная скважина это ноль. Где-то угол наклона отсчитывают от
горизонта со знаком вниз, и та же скважина это минус девяносто.

Ядро строит ось в соглашении «ноль это вертикаль». Выгрузка соглашение
не нормализует, поэтому таблица со вторым соглашением, принятая как
есть, разворачивала бы каждую скважину на девяносто градусов, и молча:
числа при этом остаются правдоподобными.

Гадать по первой строке нельзя, первой может стоять единственная
наклонная скважина. Смотрим, к чему жмётся большинство замеров.

Проверка идёт без QGIS.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

from grid_isolines.drillhole_core import (  # noqa: E402
    axis_from_survey, read_surveys, to_zenith, vertical_reference)


def test_zero_convention_is_recognized():
    assert vertical_reference([0.0, 0.2, 0.0, 1.1, 35.0]) == 0.0


def test_minus_ninety_convention_is_recognized():
    assert vertical_reference([-90.0, -89.8, -90.0, -55.0]) == -90.0


def test_plus_ninety_convention_is_recognized():
    assert vertical_reference([90.0, 89.7, 90.0, 61.0]) == 90.0


def test_a_single_inclined_hole_does_not_decide():
    """Одна наклонная скважина среди вертикальных ничего не решает."""
    values = [70.0] + [0.0] * 30
    assert vertical_reference(values) == 0.0


def test_empty_data_falls_back_to_the_core_convention():
    assert vertical_reference([]) == 0.0
    assert vertical_reference([None]) == 0.0


def test_conversion_maps_vertical_to_zero_and_horizontal_to_ninety():
    for ref in (-90.0, 90.0):
        assert abs(to_zenith(ref, ref) - 0.0) < 1e-12
        assert abs(to_zenith(0.0, ref) - 90.0) < 1e-12
    assert to_zenith(17.0, 0.0) == 17.0


def test_reader_normalizes_and_reports_the_reference():
    rows = [("s1", 0.0, 30.0, -90.0),
            ("s1", 100.0, 30.0, -88.0),
            ("s2", 0.0, 10.0, -90.0)]
    summary = {}
    out = read_surveys(rows, summary)
    assert summary["survey_vertical_ref"] == -90.0
    assert abs(out["s1"][0][2] - 0.0) < 1e-12
    assert abs(out["s1"][1][2] - 2.0) < 1e-12


def test_zero_convention_data_is_left_untouched():
    """Прежние данные обязаны читаться ровно как раньше."""
    rows = [("s1", 0.0, 30.0, 0.0), ("s1", 100.0, 30.0, 2.0)]
    summary = {}
    out = read_surveys(rows, summary)
    assert summary["survey_vertical_ref"] == 0.0
    assert out["s1"] == [(0.0, 30.0, 0.0), (100.0, 30.0, 2.0)]


def test_the_same_hole_gives_the_same_axis_in_both_conventions():
    """Тот же ствол, записанный двумя соглашениями, даёт ту же ось.

    Это и есть смысл нормализации: результат зависит от скважины, а не
    от того, как в базе принято писать угол.
    """
    zero = [("h", 0.0, 45.0, 0.0), ("h", 200.0, 45.0, 10.0)]
    minus = [("h", 0.0, 45.0, -90.0), ("h", 200.0, 45.0, -80.0)]
    a = axis_from_survey(0.0, 0.0, 100.0, read_surveys(zero, {})["h"])
    b = axis_from_survey(0.0, 0.0, 100.0, read_surveys(minus, {})["h"])
    assert len(a) == len(b)
    for (m1, x1, y1, z1), (m2, x2, y2, z2) in zip(a, b):
        assert abs(m1 - m2) < 1e-9
        assert abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9
        assert abs(z1 - z2) < 1e-9


def test_unnormalized_minus_ninety_would_have_been_wrong():
    """Контроль: без нормализации ствол уехал бы на девяносто градусов.

    Без этого теста предыдущий мог бы проходить и на сломанной
    нормализации, если бы она вообще ничего не меняла.
    """
    raw = [(0.0, 45.0, -90.0), (200.0, 45.0, -80.0)]
    axis = axis_from_survey(0.0, 0.0, 100.0, raw)
    _md, x, y, z = axis[-1]
    # зенит -90 это «горизонталь вниз» в соглашении ядра: ствол ушёл вбок
    assert math.hypot(x, y) > 100.0
    assert z > 0.0


def test_survey_stats_counts_what_the_log_needs():
    """Снос забоя, скважины без замеров и шаг между замерами."""
    from grid_isolines.drillhole_core import Collar, survey_stats
    collars = {
        "h1": Collar("h1", 0.0, 0.0, 100.0, 200.0),
        "h2": Collar("h2", 50.0, 0.0, 100.0, 200.0),
        "h3": Collar("h3", 90.0, 0.0, 100.0, 200.0),
    }
    surveys = {
        "h1": [(0.0, 90.0, 0.0), (200.0, 90.0, 0.0)],      # вертикальная
        "h2": [(0.0, 90.0, 10.0), (200.0, 90.0, 10.0)],    # наклонная
    }
    st = survey_stats(collars, surveys)
    assert st["holes"] == 2
    assert st["stations"] == 4
    assert st["without"] == 1, "скважина без замеров не сосчитана"
    assert st["tilted"] == 1
    assert st["max_offset_hole"] == "h2"
    # 200 м по стволу под 10 градусов дают около 34.7 м в плане
    assert 34.0 < st["max_offset"] < 35.5, st["max_offset"]
    assert abs(st["max_gap"] - 200.0) < 1e-9


def test_vertical_data_give_zero_offset():
    """На вертикальных данных снос нулевой: браться за траекторию незачем."""
    from grid_isolines.drillhole_core import Collar, survey_stats
    collars = {"h": Collar("h", 10.0, 20.0, 100.0, 150.0)}
    st = survey_stats(collars, {"h": [(0.0, 0.0, 0.0), (150.0, 0.0, 0.0)]})
    assert st["max_offset"] < 1e-9
    assert st["tilted"] == 0


# --- автопоиск полей инклинометрии ---------------------------------------
#
# Контракт выгрузки (Геоконструктор 1.17.0, представление LF_Survey):
# таблица survey несёт hole_id, depth, dip, azimuth. Автопоиск обязан
# находить их без ручного указания, иначе инструмент отказывается считать
# на штатных данных.

SURVEY_FIELDS = ["hole_id", "skvag_id", "depth", "dip", "azimuth"]

WANTED = (("скважина", ("hole_id", "id", "well", "скважина")),
          ("глубина", ("md", "depth", "глубина")),
          ("азимут", ("azi", "azimuth", "азимут")),
          ("зенит", ("inc", "zenith", "dip", "зенит", "угол", "наклон")))


def test_survey_fields_of_the_export_are_found_by_themselves():
    from grid_isolines.drillhole_core import resolve_field
    found = {label: resolve_field(SURVEY_FIELDS, None, cands)
             for label, cands in WANTED}
    assert found == {"скважина": "hole_id", "глубина": "depth",
                     "азимут": "azimuth", "зенит": "dip"}, found


def test_candidate_list_is_accepted_not_treated_as_a_dictionary_key():
    """Список кандидатов раньше уходил ключом в словарь синонимов.

    Там его не было, поиск шёл по одному кандидату - самому списку, и
    совпасть с именем поля список не мог никогда.
    """
    from grid_isolines.drillhole_core import find_field
    assert find_field(["depth"], ("md", "depth")) == "depth"
    assert find_field(["MD"], ["md"]) == "MD", "регистр должен игнорироваться"
    assert find_field(["прочее"], ("md", "depth")) is None


def test_named_keys_still_work():
    """Контроль: обычные ключи словаря синонимов не сломаны."""
    from grid_isolines.drillhole_core import find_field
    assert find_field(["Hole_ID", "Z"], "hole_id") == "Hole_ID"
    assert find_field(["Elev"], "z") == "Elev"


def test_the_tool_looks_for_dip_as_the_zenith_field():
    """4.02 обязана знать имя dip: так поле названо в выгрузке."""
    import ast
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(os.path.dirname(here), "algorithms.py"),
              encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if (isinstance(node, ast.ClassDef)
                and node.name == "DrillholesOnSectionAlgorithm"):
            body = ast.get_source_segment(text, node) or ""
            break
    else:
        raise AssertionError("класс 4.02 пропал из algorithms.py")
    at = body.find('"inc", "zenith"')
    assert at != -1, "кандидаты для зенитного угла пропали"
    assert '"dip"' in body[at:at + 200], (
        "имя dip не ищется, и штатная выгрузка снова потребует ручного "
        "указания полей")


# --- 3D-выход по траектории ----------------------------------------------

def _tool_source():
    import ast
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(os.path.dirname(here), "algorithms.py"),
              encoding="utf-8") as fh:
        text = fh.read()
    for node in ast.walk(ast.parse(text)):
        if (isinstance(node, ast.ClassDef)
                and node.name == "DrillholesOnSectionAlgorithm"):
            return ast.get_source_segment(text, node) or ""
    raise AssertionError("класс 4.02 пропал из algorithms.py")


def test_3d_output_follows_the_axis():
    """Раньше в 3D всегда стоял отрезок над устьем.

    Наклонная скважина выглядела вертикальной, и забой оказывался на
    десятки метров не там, где он есть. Это же место расходилось с
    Leapfrog, где та же инклинометрия учитывается.
    """
    body = _tool_source()
    at = body.find("n3 = n3_incl = 0")
    assert at != -1, "сборка 3D-слоя пропала"
    block = body[at:at + 2000]
    assert "axis_from_survey" in block, "3D снова строится без траектории"
    assert "point_at_depth" in block, "отметки берутся не с оси"
    assert "it.frm < md < it.to" in block, (
        "промежуточные узлы оси не идут в геометрию, дуга спрямляется в "
        "хорду")


def test_vertical_holes_keep_the_old_geometry():
    """Без замеров всё как было: отрезок над устьем и unfold."""
    body = _tool_source()
    at = body.find("n3 = n3_incl = 0")
    block = body[at:at + 2000]
    assert "_dh.unfold(c.z, it.frm, it.to)" in block, (
        "запасной путь для скважин без замеров пропал")
    assert "QgsPoint(c.x, c.y, zt)" in block


def test_the_share_of_axis_intervals_goes_to_the_log():
    body = _tool_source()
    assert "интервалов по оси инклинометрии" in body, (
        "по журналу не понять, сколько интервалов легло по траектории")


def test_axis_geometry_of_an_inclined_hole_is_not_vertical():
    """Числовая проверка самой оси, без QGIS.

    Инструментальную часть проверяет разбор выше, а здесь то, на чём она
    стоит: у наклонного ствола верх и низ интервала стоят в разных точках
    плана.
    """
    from grid_isolines.drillhole_core import axis_from_survey, point_at_depth
    st = [(0.0, 90.0, 15.0), (300.0, 90.0, 15.0)]
    axis = axis_from_survey(0.0, 0.0, 100.0, st, 300.0)
    x1, y1, z1 = point_at_depth(axis, 100.0)
    x2, y2, z2 = point_at_depth(axis, 200.0)
    assert abs(x1 - x2) > 20.0, "интервал стоит отвесно, ось не учтена"
    assert z1 > z2, "низ интервала оказался выше верха"
    # 100 м по стволу под 15 градусов дают около 96.6 м по вертикали
    assert 96.0 < (z1 - z2) < 97.0, z1 - z2
