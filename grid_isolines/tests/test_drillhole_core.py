# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты ядра данных бурения. Ядро не тянет QGIS, поэтому тест работает с
# настоящим кодом плагина, а не с копией помощников:
#     python grid_isolines/tests/test_drillhole_core.py
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import drillhole_core as dh  # noqa: E402


# --- вспомогательные данные ---------------------------------------------

def _demo():
    """Два устья и набор интервалов со всеми видами грязи из контракта."""
    s = dh.ReadSummary()
    collars = dh.read_collars([
        ("СКРУ-791", 1000.0, 2000.0, 150.0, 300.0),
        ("БКРУ-791", 1100.0, 2000.0, 148.0, None),      # забой не задан
        ("", 1200.0, 2000.0, 140.0, 100.0),             # без hole_id
        ("Х-1", 1300.0, 2000.0, None, 100.0),           # без отметки
        ("СКРУ-791", 1400.0, 2000.0, 100.0, 100.0),     # повтор, отброшен
    ], s)
    intervals = dh.read_intervals([
        ("СКРУ-791", 10.0, 40.0, "КрII"),
        ("СКРУ-791", 60.0, 40.0, "АБ"),                  # перепутано
        ("СКРУ-791", 60.0, 60.0, "Пд"),                  # нулевая длина
        ("СКРУ-791", "70,5", "80", "В", {"kcl": 23.4}),  # запятая и строки
        ("СКРУ-791", 290.0, 320.0, "Гл"),                # за забоем
        ("БКРУ-791", 5.0, 25.0, ""),                     # без кода
        ("БКРУ-791", 20.0, 30.0, "АБ"),                  # перехлёст
        ("НЕТ-1", 0.0, 10.0, "АБ"),                      # без устья
        ("СКРУ-791", None, 10.0, "АБ"),                  # пустая глубина
    ], s)
    holes = dh.assemble(collars, intervals, s)
    return s, collars, holes


# --- разбор значений -----------------------------------------------------

def test_parse_num_tolerant():
    assert dh.parse_num("70,5") == 70.5
    assert dh.parse_num(" 1 234,5 ") == 1234.5
    assert dh.parse_num(3) == 3.0
    assert dh.parse_num(None) is None
    assert dh.parse_num("") is None
    assert dh.parse_num("abc") is None
    assert dh.parse_num(float("nan")) is None
    assert dh.parse_num(float("inf")) is None
    assert dh.parse_num(True) is None


def test_parse_id():
    assert dh.parse_id(" СКРУ-791 ") == "СКРУ-791"
    assert dh.parse_id(791.0) == "791"
    assert dh.parse_id(791) == "791"
    assert dh.parse_id("") is None
    assert dh.parse_id("   ") is None
    assert dh.parse_id(None) is None


def test_find_field():
    names = ["Hole_ID", "FROM", "To", "Litho", "extra"]
    assert dh.find_field(names, dh.INTERVAL_ID) == "Hole_ID"
    assert dh.find_field(names, dh.INTERVAL_FROM) == "FROM"
    assert dh.find_field(names, dh.INTERVAL_TO) == "To"
    assert dh.find_field(names, dh.INTERVAL_CODE) == "Litho"
    assert dh.find_field(["a", "b"], dh.COLLAR_Z) is None
    # контрактное имя выигрывает у синонима
    assert dh.find_field(["depth", "eoh"], dh.COLLAR_EOH) == "eoh"
    # подпись устья: number первым, синонимы name и label, иначе ничего
    assert dh.find_field(["Name", "Number"], dh.COLLAR_LABEL) == "Number"
    assert dh.find_field(["Label", "x"], dh.COLLAR_LABEL) == "Label"
    assert dh.find_field(["hole_id", "z"], dh.COLLAR_LABEL) is None


def test_resolve_field():
    names = ["Hole_ID", "elev", "TD", "note"]
    # выбор пользователя уважается, регистр не важен
    assert dh.resolve_field(names, "hole_id", dh.COLLAR_ID) == "Hole_ID"
    assert dh.resolve_field(names, "note", dh.COLLAR_ID) == "note"
    # выбранного поля в слое нет - автопоиск по синонимам
    assert dh.resolve_field(names, "z", dh.COLLAR_Z) == "elev"
    assert dh.resolve_field(names, "", dh.COLLAR_EOH) == "TD"
    assert dh.resolve_field(names, None, dh.COLLAR_EOH) == "TD"
    # не нашлось ничего
    assert dh.resolve_field(["a"], "", dh.INTERVAL_FROM) is None


# --- чтение устий --------------------------------------------------------

def test_read_collars_rules():
    s, collars, _ = _demo()
    assert set(collars) == {"СКРУ-791", "БКРУ-791"}
    assert s.collar_total == 5
    assert s.collar_kept == 2
    assert s.collar_no_id == 1
    assert s.collar_bad_z == 1
    assert s.collar_dup == 1
    assert s.collar_no_eoh == 1
    # повтор не затирает первое устье
    assert collars["СКРУ-791"].z == 150.0
    assert math.isnan(collars["БКРУ-791"].eoh)


# --- чтение интервалов ----------------------------------------------------

def test_read_intervals_rules():
    s, _, holes = _demo()
    assert s.int_total == 9
    # 7 прочитано, минус сирота на сборке = 6 дошло до чертежа
    assert s.int_kept == 6
    assert s.int_bad_depth == 1
    assert s.int_zero == 1
    assert s.int_swapped == 1
    assert s.int_no_code == 1
    assert s.int_orphan == 1
    assert s.int_beyond_eoh == 1
    assert s.int_overlap == 1
    # перепутанные глубины переставлены
    it = [i for i in holes["СКРУ-791"] if i.code == "АБ"][0]
    assert (it.frm, it.to) == (40.0, 60.0)
    # запятая разобрана, прочие колонки доехали
    it = [i for i in holes["СКРУ-791"] if i.code == "В"][0]
    assert (it.frm, it.to) == (70.5, 80.0)
    assert it.extra == {"kcl": 23.4}


def test_assemble_sorted_and_kept_as_is():
    _, _, holes = _demo()
    its = holes["СКРУ-791"]
    assert [i.frm for i in its] == sorted(i.frm for i in its)
    # интервал за забоем не отрезан и не спрятан
    assert its[-1].to == 320.0
    # перехлёст у БКРУ остался как есть
    b = holes["БКРУ-791"]
    assert (b[0].to, b[1].frm) == (25.0, 20.0)


def test_hole_depth():
    _, collars, holes = _demo()
    assert dh.hole_depth(collars["СКРУ-791"], holes["СКРУ-791"]) == 300.0
    # забой не задан, берём низ интервалов
    assert dh.hole_depth(collars["БКРУ-791"], holes["БКРУ-791"]) == 30.0
    assert dh.hole_depth(collars["БКРУ-791"], []) == 0.0


# --- развёртка -----------------------------------------------------------

def test_unfold():
    assert dh.unfold(150.0, 10.0, 40.0) == (140.0, 110.0)
    assert dh.unfold(0.0, 5.0, 6.0) == (-5.0, -6.0)


def test_intervals_from_levels():
    z = 150.0
    levels = [148.0, 130.0, 124.0, float("nan"), 100.0, 80.0]
    codes = ["Q", "Пр1", "В2", "Пр2", "В3"]
    out = dh.intervals_from_levels(z, levels, codes)
    # интервалы у nan-границы выпали, остальные с глубинами от устья
    assert out == [(2.0, 20.0, "Q"), (20.0, 26.0, "Пр1"),
                   (50.0, 70.0, "В3")]
    # выклинившийся пласт (нулевая мощность) пропускается
    out = dh.intervals_from_levels(10.0, [8.0, 5.0, 5.0, 1.0],
                                   ["a", "b", "c"])
    assert [c for _, _, c in out] == ["a", "c"]
    # ход обратен unfold
    frm, to, _ = out[0]
    assert dh.unfold(10.0, frm, to) == (8.0, 5.0)


# --- порядок и цвет кодов -------------------------------------------------

def test_code_order_first_appearance_top_down():
    _, _, holes = _demo()
    order = dh.code_order(holes)
    # обход по hole_id: сначала БКРУ ("" затем АБ), потом СКРУ сверху вниз
    assert order == ["", "АБ", "КрII", "В", "Гл"]
    # детерминизм между вызовами
    assert dh.code_order(holes) == order


def test_code_color_deterministic():
    c1 = dh.code_color("КрII")
    assert c1 == dh.code_color("КрII")
    assert c1 != dh.code_color("АБ")
    assert len(c1) == 7 and c1[0] == "#"
    int(c1[1:], 16)


# --- проекция на ломаную --------------------------------------------------

def test_project_midside():
    verts = [(0.0, 0.0), (100.0, 0.0)]
    d, off = dh.project_to_polyline(verts, 30.0, 25.0)
    assert abs(d - 30.0) < 1e-9 and abs(off - 25.0) < 1e-9


def test_project_beyond_ends_clamped():
    verts = [(0.0, 0.0), (100.0, 0.0)]
    d, off = dh.project_to_polyline(verts, -30.0, 40.0)
    assert d == 0.0 and abs(off - 50.0) < 1e-9
    d, off = dh.project_to_polyline(verts, 130.0, 0.0)
    assert abs(d - 100.0) < 1e-9 and abs(off - 30.0) < 1e-9


def test_project_polyline_corner():
    verts = [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]
    # точка ближе ко второму сегменту
    d, off = dh.project_to_polyline(verts, 120.0, 50.0)
    assert abs(d - 150.0) < 1e-9 and abs(off - 20.0) < 1e-9
    # точка у самого угла
    d, off = dh.project_to_polyline(verts, 100.0, 0.0)
    assert abs(d - 100.0) < 1e-9 and off < 1e-9


def test_project_ignores_zero_segment():
    verts = [(0.0, 0.0), (0.0, 0.0), (100.0, 0.0)]
    d, off = dh.project_to_polyline(verts, 50.0, 10.0)
    assert abs(d - 50.0) < 1e-9 and abs(off - 10.0) < 1e-9


# --- колонки на чертёж ----------------------------------------------------

def test_columns_full_pipeline():
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    cnt = {}
    cols = dh.columns_for_section(collars, holes, verts, corridor=0.0,
                                  vex=10.0, counters=cnt)
    assert [c.hole_id for c in cols] == ["СКРУ-791", "БКРУ-791"]
    assert cnt["n_wells"] == 2 and cnt["n_outside"] == 0
    assert abs(cnt["min_off"] - 10.0) < 1e-9
    c = cols[0]
    assert abs(c.d - 100.0) < 1e-9 and abs(c.offset - 10.0) < 1e-9
    # первый интервал СКРУ: 10..40 от устья 150, vex 10
    ytop, ybot, it = c.segments[0]
    assert it.code == "КрII"
    assert abs(ytop - 1400.0) < 1e-9 and abs(ybot - 1100.0) < 1e-9
    # ствол от устья до забоя
    assert abs(c.stick[0] - 1500.0) < 1e-9
    assert abs(c.stick[1] - (150.0 - 300.0) * 10.0) < 1e-9
    assert c.ytop_label == c.stick[0]


def test_columns_corridor_cut():
    s, collars, holes = _demo()
    # линия проходит в 10 ед. от СКРУ, БКРУ за торцом на удалении ~51
    verts = [(900.0, 1990.0), (1050.0, 1990.0)]
    cnt = {}
    cols = dh.columns_for_section(collars, holes, verts, corridor=30.0,
                                  vex=1.0, counters=cnt)
    assert [c.hole_id for c in cols] == ["СКРУ-791"]
    assert cnt["n_wells"] == 1 and cnt["n_outside"] == 1
    assert abs(cnt["min_off"] - 10.0) < 1e-9


def test_columns_empty_corridor_reports_min_off():
    # диагностика пустого коридора: min_off говорит с экрана, узок коридор
    # или устья живут в другом координатном пространстве (урок 4.02: слой
    # collar в другой СК давал «вне коридора 3833» при верной карте)
    s, collars, holes = _demo()
    verts = [(900.0, 24000.0), (1500.0, 24000.0)]  # межсистемный сдвиг
    cnt = {}
    cols = dh.columns_for_section(collars, holes, verts, corridor=10.0,
                                  vex=1.0, counters=cnt)
    assert cols == []
    assert cnt["n_wells"] == 0 and cnt["n_outside"] == 2
    assert cnt["min_off"] > 20000.0


def test_columns_zclip_trim_and_skip():
    # рамка (0, 130): верхний интервал СКРУ подрезается по кровле, интервал
    # за забоем (отметки -140..-170) выпадает целиком, ствол и подпись
    # зажимаются рамкой
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    cnt = {}
    cols = dh.columns_for_section(collars, holes, verts, corridor=0.0,
                                  vex=1.0, counters=cnt, zclip=(0.0, 130.0))
    assert cnt["n_clip_cut"] >= 1 and cnt["n_clip_out"] >= 1
    c = [x for x in cols if x.hole_id == "СКРУ-791"][0]
    ytop, ybot, it = c.segments[0]
    assert it.code == "КрII"
    assert abs(ytop - 130.0) < 1e-9 and abs(ybot - 110.0) < 1e-9
    assert all(yt <= 130.0 + 1e-9 and yb >= -1e-9
               for yt, yb, _ in c.segments)
    # ствол: устье 150 зажато кровлей рамки, забой 150-300 зажат подошвой
    assert abs(c.stick[0] - 130.0) < 1e-9
    assert abs(c.stick[1] - 0.0) < 1e-9
    assert c.ytop_label == c.stick[0]


def test_columns_zclip_hole_entirely_outside():
    # рамка выше всех отметок: интервалы за рамкой, скважины выпадают целиком
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    cnt = {}
    cols = dh.columns_for_section(collars, holes, verts, corridor=0.0,
                                  vex=1.0, counters=cnt,
                                  zclip=(1000.0, 2000.0))
    assert cols == []
    assert cnt["n_wells"] == 0 and cnt["n_holes_out"] == 2


def test_columns_zclip_degenerate_ignored():
    # вырожденная рамка (zmax не больше zmin) молча выключает обрезку
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    a = dh.columns_for_section(collars, holes, verts, 0.0, 1.0)
    b = dh.columns_for_section(collars, holes, verts, 0.0, 1.0,
                               zclip=(5.0, 5.0))
    assert [c.hole_id for c in a] == [c.hole_id for c in b]
    assert a[0].stick == b[0].stick


def test_profile_y_interpolation_and_extent():
    parts = [[(0.0, 100.0), (100.0, 200.0)]]
    assert abs(dh.profile_y(parts, 50.0) - 150.0) < 1e-9
    assert abs(dh.profile_y(parts, 0.0) - 100.0) < 1e-9
    assert dh.profile_y(parts, 150.0) is None       # за краем линии
    assert dh.profile_y(None, 50.0) is None
    # две части накрывают x: для кровли верхняя, для подошвы нижняя
    two = parts + [[(0.0, 300.0), (100.0, 300.0)]]
    assert abs(dh.profile_y(two, 50.0, True) - 300.0) < 1e-9
    assert abs(dh.profile_y(two, 50.0, False) - 150.0) < 1e-9


def test_clip_columns_profile_top():
    # колонка d=100: сегменты 140..110 и 110..90, ствол 150..0. Кровля на
    # высоте 120 в этой позиции: верхний сегмент подрезается, ствол тоже
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    cnt = {}
    cols = dh.columns_for_section(collars, holes, verts, 0.0, 1.0, cnt)
    top = [[(0.0, 120.0), (700.0, 120.0)]]
    out = dh.clip_columns_profile(cols, top_parts=top, counters=cnt)
    c = [x for x in out if x.hole_id == "СКРУ-791"][0]
    ytop, ybot, it = c.segments[0]
    assert it.code == "КрII"
    assert abs(ytop - 120.0) < 1e-9 and abs(ybot - 110.0) < 1e-9
    assert abs(c.stick[0] - 120.0) < 1e-9
    assert c.ytop_label == c.stick[0]
    assert cnt["n_clip_cut"] >= 1


def test_clip_columns_profile_out_of_extent_untouched():
    # линия кровли не накрывает позицию колонки: колонка не трогается
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    cols = dh.columns_for_section(collars, holes, verts, 0.0, 1.0)
    top = [[(5000.0, 120.0), (6000.0, 120.0)]]
    out = dh.clip_columns_profile(cols, top_parts=top)
    assert [c.stick for c in out] == [c.stick for c in cols]


def test_clip_columns_profile_drop_hole():
    # подошва выше всей колонки: скважина выпадает, счётчик колонок падает
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    cnt = {}
    cols = dh.columns_for_section(collars, holes, verts, 0.0, 1.0, cnt)
    bot = [[(0.0, 500.0), (700.0, 500.0)]]
    out = dh.clip_columns_profile(cols, bot_parts=bot, counters=cnt)
    assert out == []
    assert cnt["n_holes_out"] == len(cols)
    assert cnt["n_wells"] == 0


def test_clip_columns_profile_ring_envelope():
    # замкнутое кольцо полосы чертежа: верхняя огибающая режет верх, нижняя
    # низ - тот же profile_y, никакого отдельного кода для полигонов
    s, collars, holes = _demo()
    verts = [(900.0, 1990.0), (1500.0, 1990.0)]
    cols = dh.columns_for_section(collars, holes, verts, 0.0, 1.0)
    ring = [(0.0, 80.0), (700.0, 80.0), (700.0, 120.0), (0.0, 120.0),
            (0.0, 80.0)]
    out = dh.clip_columns_profile(cols, top_parts=[ring],
                                  bot_parts=[ring])
    c = [x for x in out if x.hole_id == "СКРУ-791"][0]
    assert abs(c.stick[0] - 120.0) < 1e-9
    assert abs(c.stick[1] - 80.0) < 1e-9
    assert all(80.0 - 1e-9 <= yb and yt <= 120.0 + 1e-9
               for yt, yb, _ in c.segments)


def test_columns_sorted_by_distance():
    s = dh.ReadSummary()
    collars = dh.read_collars([
        ("B", 80.0, 5.0, 10.0, 20.0),
        ("A", 20.0, -5.0, 10.0, 20.0),
    ], s)
    intervals = dh.read_intervals([
        ("A", 0.0, 5.0, "x"), ("B", 0.0, 5.0, "x")], s)
    holes = dh.assemble(collars, intervals, s)
    cols = dh.columns_for_section(collars, holes, [(0.0, 0.0), (100.0, 0.0)],
                                  0.0, 1.0)
    assert [c.hole_id for c in cols] == ["A", "B"]
    assert cols[0].d < cols[1].d


# --- сводка --------------------------------------------------------------

def test_summary_lines():
    s, _, _ = _demo()
    lines = s.lines()
    assert len(lines) == 3
    assert "2 из 5" in lines[0] and "6 из 9" in lines[0]
    assert "без устья: 1" in lines[1]
    assert "переставлено from и to: 1" in lines[2]
    assert "за забоем (как есть): 1" in lines[2]


def test_summary_clean_data_single_line():
    s = dh.ReadSummary()
    collars = dh.read_collars([("A", 0.0, 0.0, 10.0, 5.0)], s)
    intervals = dh.read_intervals([("A", 0.0, 5.0, "x")], s)
    dh.assemble(collars, intervals, s)
    lines = s.lines()
    assert len(lines) == 1
    assert "1 из 1" in lines[0]


def test_summary_lines_translator():
    s, _, _ = _demo()
    seen = []

    def tr(tpl):
        seen.append(tpl)
        return tpl

    ru = s.lines()
    assert s.lines(tr) == ru          # тождественный переводчик ничего не меняет
    # переводчику отданы шаблоны с %d, а не готовые строки с числами
    assert "Устьев принято %d из %d, интервалов %d из %d." in seen
    assert "интервалов без устья: %d" in seen
    assert all("%" in t for t in seen if "из" not in t or "%d" in t)


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
