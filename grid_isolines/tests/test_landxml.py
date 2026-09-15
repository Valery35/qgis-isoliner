# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Чтение и запись LandXML.

Формат принят обменным у программ обработки тахеометрической съёмки:
Credo, Trimble Business Center, Topcon Magnet, Leica Infinity, Civil 3D.
В QGIS поперечники с пикетами не читает никто, а у нас под них уже есть
модель: 6.03 принимает пары расстояние-отметка, 4.06 режет разрезом
трёхмерные грани.

Здесь проверяется ядро, без QGIS. Настоящих выгрузок на момент написания
не было, примеры собраны по схеме 1.2 руками. Поэтому особенно важны
проверки того, что читатель терпим: файл без пространства имён, файл в
футах, файл с чужими разделами.

Три ловушки, каждая из которых молча портит результат:

Порядок координат. В схеме точка записана как «север, восток», то есть
Y раньше X. Перепутанный порядок даёт зеркальный поворот, и заметить его
на площадке в сто метров труднее, чем кажется.

Единицы. Файл в футах читается без единой ошибки и даёт отметки втрое
меньше. Проверяется пересчёт и то, что единицы вообще прочитаны.

Кривые. Дуга в ломаную спрямляется, и спрямлённая дуга короче настоящей.
Допуск задаётся стрелкой прогиба, и он обязан соблюдаться.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))

from grid_isolines import landxml  # noqa: E402

HEAD = ('<?xml version="1.0"?>\n'
        '<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" '
        'version="1.2">\n'
        '<Units><Metric linearUnit="meter" areaUnit="squareMeter"/></Units>\n')
TAIL = '</LandXML>\n'


def _doc(body, head=HEAD):
    return landxml.loads(head + body + TAIL)


# --- порядок координат и единицы ----------------------------------------

def test_point_is_read_as_north_east():
    d = _doc('<CgPoints><CgPoint name="1" code="KRV">100.0 200.0 15.5'
             '</CgPoint></CgPoints>')
    p = d.points[0]
    assert (p["x"], p["y"], p["z"]) == (200.0, 100.0, 15.5), (
        "порядок координат: в схеме сначала север, потом восток")
    assert p["name"] == "1"
    assert p["code"] == "KRV"


def test_coordinate_order_can_be_switched():
    """Не все пишут по схеме, поэтому порядок задаётся вызовом."""
    d = _doc('<CgPoints><CgPoint name="1">100.0 200.0 15.5</CgPoint>'
             '</CgPoints>', )
    d2 = landxml.loads(HEAD + '<CgPoints><CgPoint name="1">100.0 200.0 15.5'
                       '</CgPoint></CgPoints>' + TAIL, north_first=False)
    assert d.points[0]["x"] == 200.0
    assert d2.points[0]["x"] == 100.0


def test_feet_are_converted_to_metres():
    head = ('<?xml version="1.0"?>\n'
            '<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2">\n'
            '<Units><Imperial linearUnit="foot" areaUnit="squareFoot"/>'
            '</Units>\n')
    d = _doc('<CgPoints><CgPoint name="1">0 0 100.0</CgPoint></CgPoints>',
             head=head)
    assert abs(d.points[0]["z"] - 30.48) < 1e-9, (
        "футы не пересчитаны, отметки останутся втрое меньше")
    assert d.units["linear"] == "foot"
    assert abs(d.units["to_metre"] - 0.3048) < 1e-12


def test_us_survey_foot_is_not_the_same_as_foot():
    head = ('<?xml version="1.0"?><LandXML>'
            '<Units><Imperial linearUnit="USSurveyFoot"/></Units>')
    d = _doc('<CgPoints><CgPoint name="1">0 0 1</CgPoint></CgPoints>',
             head=head)
    assert abs(d.points[0]["z"] - 1200.0 / 3937.0) < 1e-12


def test_missing_units_are_reported_not_guessed():
    head = '<?xml version="1.0"?><LandXML>'
    d = _doc('<CgPoints><CgPoint name="1">0 0 1</CgPoint></CgPoints>',
             head=head)
    assert d.units["linear"] is None
    assert any("единиц" in w.lower() for w in d.warnings), d.warnings
    assert d.points[0]["z"] == 1.0, "без единиц числа берутся как есть"


def test_file_without_namespace_is_read():
    """Часть программ пишет LandXML без пространства имён."""
    d = _doc('<CgPoints><CgPoint name="1">1 2 3</CgPoint></CgPoints>',
             head='<?xml version="1.0"?><LandXML><Units>'
                  '<Metric linearUnit="meter"/></Units>')
    assert len(d.points) == 1


def test_crs_is_read_and_reported():
    d = _doc('<CoordinateSystem name="MSK-59" epsgCode="3857"/>'
             '<CgPoints><CgPoint name="1">1 2 3</CgPoint></CgPoints>')
    assert d.crs["name"] == "MSK-59"
    assert d.crs["epsg"] == 3857


# --- линии, поверхности --------------------------------------------------

def test_plan_feature_keeps_name_and_vertices():
    d = _doc('<PlanFeatures name="набор">'
             '<PlanFeature name="бровка" desc="верх">'
             '<CoordGeom><Line><Start>0 0 10</Start><End>0 100 12</End>'
             '</Line></CoordGeom></PlanFeature></PlanFeatures>')
    f = d.lines[0]
    assert f["name"] == "бровка"
    assert f["desc"] == "верх"
    assert f["coords"][0] == (0.0, 0.0, 10.0)
    assert f["coords"][-1] == (100.0, 0.0, 12.0)


def test_surface_faces_and_invisible_ones():
    d = _doc('<Surfaces><Surface name="рельеф"><Definition surfType="TIN">'
             '<Pnts><P id="1">0 0 1</P><P id="2">0 10 2</P>'
             '<P id="3">10 0 3</P><P id="4">10 10 4</P></Pnts>'
             '<Faces><F>1 2 3</F><F i="1">2 3 4</F></Faces>'
             '</Definition></Surface></Surfaces>')
    s = d.surfaces[0]
    assert s["name"] == "рельеф"
    assert len(s["points"]) == 4
    assert len(s["faces"]) == 1, "невидимая грань попала в поверхность"
    assert s["skipped_faces"] == 1
    tri = [s["points"][i] for i in s["faces"][0]]
    assert tri[0] == (0.0, 0.0, 1.0)


def test_face_with_unknown_point_is_dropped_not_crashing():
    d = _doc('<Surfaces><Surface name="s"><Definition>'
             '<Pnts><P id="1">0 0 1</P><P id="2">0 1 2</P></Pnts>'
             '<Faces><F>1 2 9</F></Faces></Definition></Surface></Surfaces>')
    assert d.surfaces[0]["faces"] == []
    assert any("гран" in w for w in d.warnings), d.warnings


# --- трасса, кривые, профиль, поперечники --------------------------------

def test_alignment_line_gives_two_vertices():
    d = _doc('<Alignments><Alignment name="ось" length="100" staStart="0">'
             '<CoordGeom><Line><Start>0 0</Start><End>0 100</End></Line>'
             '</CoordGeom></Alignment></Alignments>')
    a = d.alignments[0]
    assert a["name"] == "ось"
    assert a["sta_start"] == 0.0
    assert len(a["coords"]) == 2


def test_curve_is_densified_within_the_sagitta_tolerance():
    """Четверть окружности радиусом 100 при допуске 0,05 метра."""
    d = _doc('<Alignments><Alignment name="a">'
             '<CoordGeom><Curve rot="ccw" radius="100">'
             '<Start>0 100</Start><Center>0 0</Center><End>100 0</End>'
             '</Curve></CoordGeom></Alignment></Alignments>')
    pts = d.alignments[0]["coords"]
    assert len(pts) > 4, "дуга не разбита вовсе"
    # каждая хорда не должна отходить от дуги дальше допуска
    worst = 0.0
    for (x0, y0), (x1, y1) in zip([(p[0], p[1]) for p in pts],
                                  [(p[0], p[1]) for p in pts[1:]]):
        half = math.hypot(x1 - x0, y1 - y0) / 2.0
        worst = max(worst, 100.0 - math.sqrt(max(100.0 ** 2 - half ** 2, 0.0)))
    assert worst <= landxml.DEFAULT_SAG + 1e-9, worst
    # концы дуги стоят точно, а не приближённо
    assert abs(pts[0][0] - 100.0) < 1e-9 and abs(pts[0][1] - 0.0) < 1e-9
    assert abs(pts[-1][0] - 0.0) < 1e-9 and abs(pts[-1][1] - 100.0) < 1e-9


def test_tighter_tolerance_gives_more_vertices():
    body = ('<Alignments><Alignment name="a"><CoordGeom>'
            '<Curve rot="ccw" radius="100"><Start>0 100</Start>'
            '<Center>0 0</Center><End>100 0</End></Curve>'
            '</CoordGeom></Alignment></Alignments>')
    rough = landxml.loads(HEAD + body + TAIL, sagitta=0.5)
    fine = landxml.loads(HEAD + body + TAIL, sagitta=0.01)
    assert len(fine.alignments[0]["coords"]) > \
        len(rough.alignments[0]["coords"])


def test_curve_without_centre_falls_back_to_chord_and_says_so():
    d = _doc('<Alignments><Alignment name="a"><CoordGeom>'
             '<Curve rot="cw"><Start>0 0</Start><End>0 100</End></Curve>'
             '</CoordGeom></Alignment></Alignments>')
    assert len(d.alignments[0]["coords"]) == 2
    assert any("хорд" in w for w in d.warnings), d.warnings


def test_spiral_is_chorded_and_counted():
    """Переходная кривая спрямляется намеренно, и об этом говорят."""
    d = _doc('<Alignments><Alignment name="a"><CoordGeom>'
             '<Spiral length="50" radiusStart="INF" radiusEnd="100" '
             'spiType="clothoid"><Start>0 0</Start><End>0 50</End>'
             '</Spiral></CoordGeom></Alignment></Alignments>')
    assert len(d.alignments[0]["coords"]) == 2
    assert d.spirals == 1
    assert any("переходн" in w.lower() for w in d.warnings), d.warnings


def test_profile_is_read_as_station_elevation():
    d = _doc('<Alignments><Alignment name="a" length="100">'
             '<Profile name="p"><ProfAlign name="pa">'
             '<PVI>0 100.0</PVI><PVI>50 102.5</PVI><PVI>100 99.0</PVI>'
             '</ProfAlign></Profile></Alignment></Alignments>')
    prof = d.alignments[0]["profile"]
    assert prof == [(0.0, 100.0), (50.0, 102.5), (100.0, 99.0)]


def test_cross_sections_carry_station_offset_and_elevation():
    d = _doc('<Alignments><Alignment name="a"><CrossSects>'
             '<CrossSect sta="120.5" name="ПК1+20">'
             '<CrossSectSurf name="земля">'
             '<CrossSectPnt>-5.0 100.0</CrossSectPnt>'
             '<CrossSectPnt>0.0 101.2</CrossSectPnt>'
             '<CrossSectPnt>5.0 100.4</CrossSectPnt>'
             '</CrossSectSurf></CrossSect></CrossSects></Alignment>'
             '</Alignments>')
    xs = d.alignments[0]["cross_sects"]
    assert len(xs) == 1
    assert xs[0]["sta"] == 120.5
    assert xs[0]["name"] == "ПК1+20"
    surf = xs[0]["surfaces"][0]
    assert surf["name"] == "земля"
    assert surf["points"] == [(-5.0, 100.0), (0.0, 101.2), (5.0, 100.4)]


def test_cross_section_offsets_are_not_swapped_by_the_point_order_rule():
    """Пара в поперечнике это смещение и отметка, а не север и восток.

    Правило «сначала север» относится к точкам на местности. Применить его
    к поперечнику значит поменять местами смещение и отметку, и чертёж
    ляжет набок.
    """
    d = landxml.loads(
        HEAD + '<Alignments><Alignment name="a"><CrossSects>'
        '<CrossSect sta="0"><CrossSectSurf name="s">'
        '<CrossSectPnt>-3.0 250.0</CrossSectPnt></CrossSectSurf>'
        '</CrossSect></CrossSects></Alignment></Alignments>' + TAIL,
        north_first=False)
    p = d.alignments[0]["cross_sects"][0]["surfaces"][0]["points"][0]
    assert p == (-3.0, 250.0)


# --- чужие разделы -------------------------------------------------------

def test_unsupported_sections_are_counted_and_named():
    d = _doc('<Parcels><Parcel name="1"/><Parcel name="2"/></Parcels>'
             '<PipeNetworks><PipeNetwork name="n"/></PipeNetworks>'
             '<CgPoints><CgPoint name="1">1 2 3</CgPoint></CgPoints>')
    assert d.unsupported.get("Parcels") == 1
    assert d.unsupported.get("PipeNetworks") == 1
    assert len(d.points) == 1, "чужой раздел помешал читать свои"


def test_empty_file_is_not_an_error():
    d = _doc("")
    assert d.points == [] and d.surfaces == [] and d.alignments == []


def test_broken_xml_raises_a_readable_error():
    try:
        landxml.loads("<LandXML><CgPoints>")
    except landxml.LandXmlError as exc:
        assert "XML" in str(exc) or "xml" in str(exc)
    else:
        raise AssertionError("испорченный файл прочитался молча")


# --- запись --------------------------------------------------------------

def test_round_trip_keeps_the_numbers():
    doc = landxml.Document()
    doc.crs = {"name": "MSK-59", "epsg": 3857}
    doc.points = [{"name": "1", "code": "KRV", "desc": "",
                   "x": 200.0, "y": 100.0, "z": 15.5}]
    doc.lines = [{"name": "бровка", "desc": "",
                  "coords": [(0.0, 0.0, 10.0), (100.0, 0.0, 12.0)]}]
    doc.surfaces = [{"name": "s", "desc": "",
                     "points": [(0.0, 0.0, 1.0), (0.0, 10.0, 2.0),
                                (10.0, 0.0, 3.0)],
                     "faces": [(0, 1, 2)], "skipped_faces": 0}]
    doc.alignments = [{"name": "ось", "desc": "", "sta_start": 0.0,
                       "length": 100.0,
                       "coords": [(0.0, 0.0, None), (0.0, 100.0, None)],
                       "profile": [(0.0, 50.0), (100.0, 52.0)],
                       "cross_sects": [{"sta": 10.0, "name": "ПК0+10",
                                        "surfaces": [{"name": "земля",
                                                      "points": [(-2.0, 5.0),
                                                                 (2.0, 6.0)]}]}]}]
    back = landxml.loads(landxml.dumps(doc))
    assert back.points[0]["x"] == 200.0 and back.points[0]["y"] == 100.0
    assert back.points[0]["code"] == "KRV"
    assert back.lines[0]["coords"][-1] == (100.0, 0.0, 12.0)
    assert len(back.surfaces[0]["faces"]) == 1
    a = back.alignments[0]
    assert a["profile"] == [(0.0, 50.0), (100.0, 52.0)]
    assert a["cross_sects"][0]["surfaces"][0]["points"] == [(-2.0, 5.0),
                                                            (2.0, 6.0)]
    assert back.crs["epsg"] == 3857


def test_written_file_declares_metres():
    doc = landxml.Document()
    doc.points = [{"name": "1", "code": "", "desc": "",
                   "x": 1.0, "y": 2.0, "z": 3.0}]
    text = landxml.dumps(doc)
    assert 'linearUnit="meter"' in text
    assert "LandXML-1.2" in text


def test_written_point_is_north_first():
    """Пишем по схеме, иначе принимающая программа зеркалит площадку."""
    doc = landxml.Document()
    doc.points = [{"name": "1", "code": "", "desc": "",
                   "x": 200.0, "y": 100.0, "z": 5.0}]
    text = landxml.dumps(doc)
    assert ">100.0 200.0 5.0<" in text.replace("  ", " ")


# --- пикетаж -------------------------------------------------------------

AXIS = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]


def test_station_runs_along_the_polyline():
    at = landxml.point_at_station(AXIS, 150.0)
    assert abs(at[0] - 100.0) < 1e-9 and abs(at[1] - 50.0) < 1e-9
    assert abs(at[2] - 0.0) < 1e-9 and abs(at[3] - 1.0) < 1e-9


def test_station_start_is_taken_into_account():
    at = landxml.point_at_station(AXIS, 1050.0, sta_start=1000.0)
    assert abs(at[0] - 50.0) < 1e-9 and abs(at[1] - 0.0) < 1e-9


def test_offset_is_positive_to_the_right_of_travel():
    """Ход на восток, значит правая сторона это юг."""
    p = landxml.offset_point(AXIS, 50.0, 10.0)
    assert abs(p[0] - 50.0) < 1e-9 and abs(p[1] + 10.0) < 1e-9
    p = landxml.offset_point(AXIS, 50.0, -10.0)
    assert abs(p[1] - 10.0) < 1e-9


def test_station_and_offset_are_inverse_to_each_other():
    for sta, off in ((25.0, 7.0), (150.0, -3.5), (100.0, 2.0)):
        x, y = landxml.offset_point(AXIS, sta, off)
        got_sta, got_off = landxml.station_offset(AXIS, x, y)
        assert abs(got_sta - sta) < 1e-6, (sta, got_sta)
        assert abs(got_off - off) < 1e-6, (off, got_off)


def test_station_beyond_the_end_is_extrapolated_not_dropped():
    """Пикет поперечника выходит за последнюю вершину оси сплошь и рядом."""
    at = landxml.point_at_station(AXIS, 210.0)
    assert at is not None
    assert abs(at[0] - 100.0) < 1e-9 and abs(at[1] - 110.0) < 1e-9


def test_degenerate_axis_gives_none_not_a_crash():
    assert landxml.point_at_station([(0.0, 0.0)], 10.0) is None
    assert landxml.point_at_station([(0.0, 0.0), (0.0, 0.0)], 1.0) is None


# --- свой разборщик XML --------------------------------------------------
#
# Модули xml в плагин не берутся: сканер каталога их блокирует, потому что
# штатный разборщик поддаётся раздутым сущностям. Раз разбор свой, его
# границы надо стеречь отдельно.

def test_entity_declaration_is_refused():
    """Тот самый способ раздуть разбор до отказа машины."""
    bomb = ('<?xml version="1.0"?>'
            '<!DOCTYPE LandXML [<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            '<LandXML><CgPoints><CgPoint name="&b;">1 2 3</CgPoint>'
            '</CgPoints></LandXML>')
    try:
        landxml.loads(bomb)
    except landxml.LandXmlError as exc:
        assert "сущност" in str(exc)
    else:
        raise AssertionError("файл с объявлением сущностей прочитался")


def test_builtin_entities_are_expanded():
    d = _doc('<CgPoints><CgPoint name="&quot;1&quot; &amp; 2" '
             'desc="&lt;бровка&gt;">1 2 3</CgPoint></CgPoints>')
    assert d.points[0]["name"] == '"1" & 2'
    assert d.points[0]["desc"] == "<бровка>"


def test_numeric_reference_is_expanded():
    d = _doc('<CgPoints><CgPoint name="&#1050;1">1 2 3</CgPoint></CgPoints>')
    assert d.points[0]["name"] == "К1"


def test_comments_and_processing_instructions_are_skipped():
    d = _doc('<!-- пояснение --><CgPoints><!-- и тут -->'
             '<CgPoint name="1">1 2 3</CgPoint></CgPoints>')
    assert len(d.points) == 1


def test_namespace_prefix_is_stripped():
    d = landxml.loads(
        '<?xml version="1.0"?><lx:LandXML xmlns:lx="http://x">'
        '<lx:Units><lx:Metric linearUnit="meter"/></lx:Units>'
        '<lx:CgPoints><lx:CgPoint name="1">1 2 3</lx:CgPoint>'
        '</lx:CgPoints></lx:LandXML>')
    assert len(d.points) == 1


def test_mismatched_closing_tag_is_caught():
    try:
        landxml.loads("<LandXML><CgPoints></Alignments></LandXML>")
    except landxml.LandXmlError as exc:
        assert "закрыт" in str(exc)
    else:
        raise AssertionError("несовпадение тегов прошло молча")


def test_self_closing_tag_and_unquoted_attribute():
    d = _doc('<CoordinateSystem name="MSK" epsgCode=3857 />'
             '<CgPoints><CgPoint name="1">1 2 3</CgPoint></CgPoints>')
    assert d.crs["epsg"] == 3857
