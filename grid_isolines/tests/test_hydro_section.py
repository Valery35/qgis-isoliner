# -*- coding: utf-8 -*-
"""Тесты ядра створов и кривых расходов (hydro_section, demo_river).

Запуск: python test_hydro_section.py.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hydro_section as hs  # noqa: E402
import demo_river as dr  # noqa: E402


def test_wetted_geometry_rectangle():
    """Прямоугольный короб считается точно: A = b·h, P = b + 2·h."""
    d = [0.0, 0.0, 10.0, 10.0]
    z = [2.0, 0.0, 0.0, 2.0]
    a, b, p = hs.wetted_geometry(d, z, 1.5)
    assert abs(a - 15.0) < 1e-9, a
    assert abs(b - 10.0) < 1e-9, b
    assert abs(p - 13.0) < 1e-9, p


def test_wetted_geometry_triangle():
    """Урез внутри отрезка находится интерполяцией, а не по вершинам."""
    d = [0.0, 10.0]
    z = [0.0, 10.0]
    a, b, p = hs.wetted_geometry(d, z, 5.0)
    assert abs(a - 12.5) < 1e-9, a          # треугольник 5x5/2
    assert abs(b - 5.0) < 1e-9, b
    assert abs(p - 5.0 * 2 ** 0.5) < 1e-9, p


def test_dry_profile_gives_zero():
    """Сухой профиль даёт нули, а не ошибки."""
    a, b, p = hs.wetted_geometry([0, 10], [5.0, 6.0], 4.0)
    assert a == b == p == 0.0


def test_curve_matches_reference():
    """Кривая ядра совпадает с эталоном, посчитанным по фигурам.

    Это главный тест ветки: демо-створ собран из прямоугольника и
    плоскостей, эталон считается независимыми формулами. Расхождение
    выше допуска - ошибка ядра.
    """
    levels = np.arange(97.6, 101.4, 0.2)
    frags = dr.demo_fragments()
    got = hs.rating_curve(frags, dr.SLOPE, levels=levels)
    ref = dr.reference_curve(levels)
    for key in ("q_channel", "q_left", "q_right", "q_total"):
        dq = np.abs(got[key] - ref[key])
        scale = np.maximum(ref[key], 1e-6)
        rel = float(np.max(dq / scale))
        assert rel < 0.02, (key, rel)


def test_split_preserves_area():
    """Членение не теряет площадь: сумма фрагментов равна целому."""
    d, z = dr.demo_profile()
    frags = dr.demo_fragments()
    for lv in (99.0, 100.2, 101.0):
        whole = hs.wetted_geometry(d, z, lv)[0]
        parts = sum(hs.wetted_geometry(f.d, f.z, lv)[0] for f in frags)
        assert abs(whole - parts) < 1e-6, (lv, whole, parts)


def test_curve_is_monotone():
    """Кривая расходов не убывает по отметке."""
    frags = dr.demo_fragments()
    got = hs.rating_curve(frags, dr.SLOPE, step=0.1)
    assert np.all(np.diff(got["q_total"]) >= -1e-9)


def test_floodplain_kicks_in_at_bank():
    """Пойменные расходы включаются ровно на отметке бровки."""
    frags = dr.demo_fragments()
    got = hs.rating_curve(frags, dr.SLOPE, step=0.05)
    below = got["level"] < dr.Z_BANK - 1e-9
    assert np.all(got["q_left"][below] == 0.0)
    above = got["level"] > dr.Z_BANK + 0.1
    assert np.all(got["q_left"][above] > 0.0)


def test_level_for_q_roundtrip():
    """Прямой и обратный ход по кривой согласованы."""
    frags = dr.demo_fragments()
    curve = hs.rating_curve(frags, dr.SLOPE, step=0.05)
    for lv in (99.0, 100.0, 100.8):
        q = hs.q_for_level(curve, lv)
        back = hs.level_for_q(curve, q)
        assert back is not None and abs(back - lv) < 0.06, (lv, back)


def test_level_for_q_refuses_extrapolation():
    """Расход выше кривой не экстраполируется, а отклоняется."""
    frags = dr.demo_fragments()
    curve = hs.rating_curve(frags, dr.SLOPE, step=0.1)
    assert hs.level_for_q(curve, float(curve["q_total"][-1]) * 2.0) is None


def test_chain_slopes_recover_demo_slope():
    """Уклон по цепочке створов восстанавливает заданный в демо."""
    secs = dr.demo_sections(n=4)
    s = hs.chain_slopes([c["km"] for c in secs],
                        [c["z_bed"] for c in secs])
    assert np.allclose(s, dr.SLOPE, rtol=1e-6), s


def test_negative_slope_is_kept():
    """Отрицательный уклон не прячется: это признак ошибки данных."""
    s = hs.chain_slopes([0.0, 1.0], [100.0, 100.5])
    assert s[0] < 0.0


def test_gauge_flat_detects_water_surface():
    """Плоский участок на отметке ловится как признак зашитой глади."""
    d = np.arange(0.0, 50.0, 1.0)
    z = np.where((d > 15) & (d < 35), 99.0, 101.0)
    w = hs.gauge_flat_at_level(d, z, 99.0)
    assert w > 15.0, w
    assert hs.gauge_flat_at_level(d, z, 100.0) == 0.0


def test_divide_points_are_inserted():
    """Точка границы членения вставляется с интерполированной отметкой."""
    frags = hs.split_by_divides([0.0, 10.0], [0.0, 10.0], 3.0, 7.0,
                                0.05, 0.03, 0.05)
    left = frags[0]
    assert abs(left.d[-1] - 3.0) < 1e-9
    assert abs(left.z[-1] - 3.0) < 1e-9


def test_discharge_never_falls_with_level():
    """Расход не убывает при подъёме воды ни на участке, ни суммарно.

    Сторож против смещённой границы членения. Если граница уезжает с
    бровки в пойму, русло выше бровок набирает наклонный периметр без
    площади: гидравлический радиус падает, и расход на первой пойменной
    отметке уменьшается при поднявшейся воде. Физически это невозможно, и
    на живом прогоне выглядело ошибкой расчёта, хотя ошибка была в
    параметрах демо.
    """
    d, z = dr.demo_profile()
    frs = hs.split_by_divides(d, z, dr.FP_WIDTH, dr.FP_WIDTH + dr.CH_WIDTH,
                              dr.N_FLOOD, dr.N_CHANNEL, dr.N_FLOOD)
    cv = hs.rating_curve(frs, dr.SLOPE, step=0.1)
    for key in [k for k in cv if k.startswith("q_")]:
        dq = np.diff(cv[key])
        assert float(np.min(dq)) >= -1e-9, (key, float(np.min(dq)))


def test_floodplains_are_symmetric_in_demo():
    """У симметричной долины поймы дают одинаковый расход.

    Второй признак той же болезни: смещённая граница делает поймы
    неравными там, где профиль симметричен.
    """
    d, z = dr.demo_profile()
    frs = hs.split_by_divides(d, z, dr.FP_WIDTH, dr.FP_WIDTH + dr.CH_WIDTH,
                              dr.N_FLOOD, dr.N_CHANNEL, dr.N_FLOOD)
    cv = hs.rating_curve(frs, dr.SLOPE, step=0.1)
    diff = np.abs(cv["q_left"] - cv["q_right"])
    assert float(np.max(diff)) < 1e-6, float(np.max(diff))


def test_table_roundtrip_gives_same_curve():
    """Створы, собранные из таблицы промеров, дают ту же кривую.

    Импорт наработанных таблиц имеет смысл только если он ничего не
    теряет: пары расстояние-отметка обязаны восстановить профиль до той
    же кривой расходов, что дали исходные створы.
    """
    secs = dr.demo_sections(n=2)
    rows = [r for r in dr.survey_table(secs) if r["sec"].endswith("1")]
    d = np.asarray([r["dist"] for r in rows], float)
    z = np.asarray([r["elev"] for r in rows], float)
    frs_a = hs.split_by_divides(*dr.demo_profile(), dr.FP_WIDTH,
                                dr.FP_WIDTH + dr.CH_WIDTH,
                                dr.N_FLOOD, dr.N_CHANNEL, dr.N_FLOOD)
    frs_b = hs.split_by_divides(d, z, dr.FP_WIDTH,
                                dr.FP_WIDTH + dr.CH_WIDTH,
                                dr.N_FLOOD, dr.N_CHANNEL, dr.N_FLOOD)
    a = hs.rating_curve(frs_a, dr.SLOPE, step=0.25)
    b = hs.rating_curve(frs_b, dr.SLOPE, step=0.25)
    assert float(np.max(np.abs(a["q_total"] - b["q_total"]))) < 1e-9


def test_survey_table_carries_computation_props():
    """Таблица промеров несёт и расчётные свойства, а не только профиль.

    Без границ членения, шероховатостей и уклона импорт вернул бы
    геометрию, но не расчёт, и кривая по восстановленным створам разошлась
    бы с исходной. Существующие программы держат эти величины рядом с
    парами, значит и таблица обязана.
    """
    rows = dr.survey_table(dr.demo_sections(n=2))
    need = ("div_l", "div_r", "n_left", "n_channel", "n_right", "slope")
    for key in need:
        assert key in rows[0], key
    assert rows[0]["n_channel"] == dr.N_CHANNEL
    assert rows[0]["n_left"] == dr.N_FLOOD
    assert rows[0]["div_r"] == dr.FP_WIDTH + dr.CH_WIDTH


def test_probability_table_stays_inside_curve():
    """Учебные расходы обеспеченности лежат внутри кривой.

    Иначе обратный ход по кривой упрётся в её верх, уровень не найдётся и
    подвал чертежа окажется пустым - демо перестанет показывать то, ради
    чего сделано.
    """
    cv = hs.rating_curve(dr.demo_fragments(), dr.SLOPE, step=0.1)
    rows = dr.probability_table(cv)
    assert len(rows) == 3
    qmax = float(np.max(cv["q_total"]))
    for r in rows:
        assert 0.0 < r["q"] < qmax, r
        assert hs.level_for_q(cv, r["q"]) is not None, r
    assert [r["prob"] for r in rows] == [1.0, 5.0, 10.0]
    assert rows[0]["q"] > rows[1]["q"] > rows[2]["q"]


def test_observed_levels_fall_inside_profile():
    """Учебные наблюдённые уровни лежат в пределах профиля.

    Замер наносится на чертёж линией, и если отметка окажется выше бортов
    или ниже дна, линия уйдёт за поле и демо перестанет показывать то,
    ради чего сделано.
    """
    secs = dr.demo_sections(n=2)
    z = np.asarray(secs[0]["z"], float)
    rows = dr.observed_levels(secs)
    assert rows
    for r in rows:
        assert float(np.min(z)) < r["level"] < float(np.max(z)), r
        assert r["label"]


def test_valley_surface_matches_sections():
    """Поверхность долины совпадает со створами в их плоскостях.

    Полигон затопления режет эту поверхность, а кривая считается по
    створам. Если они разойдутся, инструменты будут говорить о двух
    похожих долинах вместо одной.
    """
    secs = dr.demo_sections(n=3)
    arr, (lx, cx, ly, cy) = dr.valley_surface(secs, cell=2.0, margin=0.0)
    for j, s in enumerate(secs):
        pos = (s["km"] - secs[0]["km"]) * 1000.0
        i = int(round((pos - ly) / cy))
        i = min(max(i, 0), arr.shape[0] - 1)
        xs = lx + cx * np.arange(arr.shape[1])
        want = np.interp(xs, np.asarray(s["d"], float),
                         np.asarray(s["z"], float))
        assert float(np.max(np.abs(arr[i] - want))) < 1e-6, j


def test_report_svg_is_valid_xml():
    """Картинки отчёта разбираются как XML и содержат саму кривую.

    Отчёт открывают в браузере, и битый SVG там просто не нарисуется, без
    единого сообщения об ошибке. Проверка вырезает рисование из кода
    инструмента и прогоняет его на демо.
    """
    import os
    import re
    import xml.etree.ElementTree as ET

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "algorithms.py"), encoding="utf-8").read()
    i = src.index("    @staticmethod\n    def _svg_curve")
    j = src.index("    def _write_html", i)
    code = "import numpy as np\n" + "\n".join(
        l[4:] if l.startswith("    ") else l for l in src[i:j].split("\n"))
    ns = {"_hs_wet_spans": hs.wet_spans}
    exec(code.replace("@staticmethod\n", ""), ns)

    frags = dr.demo_fragments()
    cv = hs.rating_curve(frags, dr.SLOPE, step=0.1)
    q1 = dr.probability_table(cv)[0]["q"]
    levels = [("УВВ1%", q1, hs.level_for_q(cv, q1))]
    for name, svg in (("curve", ns["_svg_curve"](cv, levels)),
                      ("profile", ns["_svg_profile"](frags, levels))):
        assert svg.startswith("<svg"), name
        ET.fromstring(svg)
        assert "<polyline" in svg, name


def test_report_body_embeds_pictures():
    """Отчёт 6.01 действительно вставляет картинки, а не только умеет их.

    Рисование уже было написано, а вызовы в теле отчёта не легли, и отчёт
    вышел без единой картинки. Проверка смотрит на код: блок pics
    собирается, фрагменты доезжают до отчёта, распаковка их принимает.
    """
    import os

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(root, "algorithms.py"), encoding="utf-8").read()
    i = src.index("class RatingCurveAlgorithm")
    j = src.index("\n\nclass ", i + 10)
    blk = src[i:j]
    assert "levels_found, frags))" in blk, "фрагменты не кладутся в отчёт"
    assert "levels_found, frags" in blk.split("def _write_html")[1], \
        "отчёт не принимает фрагменты"
    assert "self._svg_profile(frags" in blk, "профиль не рисуется"
    assert "self._svg_curve(cv" in blk, "кривая не рисуется"
    assert "class='pics'" in blk, "блок картинок не собирается"


def test_wet_spans_cut_levels_at_water_edge():
    """Уровень существует только между точками уреза.

    На чертеже линия уровня не тянется через всю ширину створа: на борта
    вода не заходит. Отмель посреди русла разрывает зеркало, и каждая
    часть живёт своим отрезком.

    Вертикальные звенья профиля проверяются отдельно: у стенки русла
    переход через урез происходит в той же координате, и пропуск такого
    звена терял весь участок.
    """
    d, z = dr.demo_profile()
    assert hs.wet_spans(d, z, 97.0) == []
    ch = hs.wet_spans(d, z, 98.0)
    assert len(ch) == 1
    assert abs(ch[0][0] - dr.FP_WIDTH) < 1e-9
    assert abs(ch[0][1] - (dr.FP_WIDTH + dr.CH_WIDTH)) < 1e-9
    wide = hs.wet_spans(d, z, 101.4)
    assert len(wide) == 1 and wide[0][1] - wide[0][0] > dr.CH_WIDTH

    d2 = np.array([0.0, 10.0, 20.0, 30.0, 40.0, 50.0])
    z2 = np.array([5.0, 0.0, 0.0, 3.0, 0.0, 5.0])
    two = hs.wet_spans(d2, z2, 2.0)
    assert len(two) == 2, two
    assert two[0][1] < two[1][0]


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print("ok:", n)
        except AssertionError as ex:
            failed += 1
            print("FAIL:", n, "-", ex)
    print("\n%d тестов, ошибок %d" % (len(fns), failed))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run()


# --- график кривой расходов в вектор ---------------------------------------

def _demo_curve():
    d = np.linspace(0.0, 200.0, 201)
    z = 153.0 + (d - 100.0) ** 2 / 1500.0
    frag = hs.Fragment(d=d, z=z, n=0.03, name="русло")
    return hs.rating_curve([frag], slope=0.0009, levels=None, step=0.5)


def test_curve_plot_maps_into_drawing_box():
    """Кривая ложится в заданный габарит чертежа.

    График живёт в СВОИХ осях: по горизонтали расход, по вертикали
    отметка. Смешивать их с координатами профиля нельзя - метры и
    кубометры в секунду несопоставимы, общего масштаба у них нет.
    """
    curve = _demo_curve()
    plot = hs.curve_plot(curve, "q_total", width=100.0, height=60.0)
    xs = [p[0] for p in plot["pts"]]
    ys = [p[1] for p in plot["pts"]]
    assert min(xs) >= -1e-9 and max(xs) <= 100.0 + 1e-6
    assert min(ys) >= -1e-9 and max(ys) <= 60.0 + 1e-6
    assert plot["xmin"] == 0.0, "ось расхода обязана начинаться с нуля"


def test_plot_ticks_by_step_gives_round_metres():
    """Шкала высот через метр - то, чего требует нормативный чертёж."""
    curve = _demo_curve()
    plot = hs.curve_plot(curve, "q_total", width=100.0, height=60.0)
    ticks = hs.plot_ticks(plot["ymin"], plot["ymax"], plot["sy"], step=1.0)
    vals = [v for v, _xy in ticks]
    assert all(abs(v - round(v)) < 1e-9 for v in vals)
    assert len(vals) >= 3


def test_plot_ticks_without_step_are_round_numbers():
    """Без шага значения выбираются округлёнными, а не произвольными."""
    ticks = hs.plot_ticks(0.0, 2270.0, 1.0, step=0.0, count=6)
    vals = [v for v, _xy in ticks]
    assert vals, "засечек нет вовсе"
    for v in vals:
        assert abs(v / 500.0 - round(v / 500.0)) < 1e-9, v


def test_curve_marks_give_the_discharge_for_a_level():
    """Засечка уровня несёт и отметку, и отвечающий ей расход.

    Именно так подписаны уровни обеспеченности на нормативных графиках.
    """
    curve = _demo_curve()
    plot = hs.curve_plot(curve, "q_total", width=100.0, height=60.0)
    marks = hs.curve_marks(plot, [("УВВ1%", 157.5)], curve, "q_total")
    assert len(marks) == 1
    m = marks[0]
    assert m["value"] > 0
    assert len(m["to_y"]) == 2 and len(m["to_x"]) == 2
    # пунктир к оси отметок идёт по горизонтали, к оси расходов по вертикали
    assert abs(m["to_y"][0][1] - m["to_y"][1][1]) < 1e-9
    assert abs(m["to_x"][0][0] - m["to_x"][1][0]) < 1e-9


def test_curve_marks_skip_a_level_outside_the_curve():
    """Отметка вне диапазона пропускается, а не рисуется в пустоте."""
    curve = _demo_curve()
    plot = hs.curve_plot(curve, "q_total", width=100.0, height=60.0)
    marks = hs.curve_marks(plot, [("нелепая", 9999.0)], curve, "q_total")
    assert marks == []


def test_curve_plot_refuses_a_degenerate_curve():
    assert hs.curve_plot({"q_total": [1.0], "level": [150.0]},
                         "q_total") is None


# --- скорость потока -------------------------------------------------------

def test_velocity_is_q_over_area():
    """Скорость это средняя по живому сечению, Q делить на A.

    Гидрологи вставляют график v(H) рядом с Q(H) и W(H), поэтому
    скорость хранится в кривой, а не пересчитывается на месте каждым
    потребителем по-своему.
    """
    frags = dr.demo_fragments()
    cv = hs.rating_curve(frags, dr.SLOPE, step=0.1)
    a = cv["area_total"]
    wet = a > 1e-9
    assert np.allclose(cv["v_total"][wet], cv["q_total"][wet] / a[wet])
    for f in frags:
        af = cv["area_" + f.name]
        w = af > 1e-9
        assert np.allclose(cv["v_" + f.name][w], cv["q_" + f.name][w] / af[w])


def test_velocity_is_zero_on_a_dry_part():
    """Сухой участок даёт ноль, а не деление на ноль."""
    frags = dr.demo_fragments()
    cv = hs.rating_curve(frags, dr.SLOPE, step=0.05)
    dry = cv["area_left"] <= 1e-12
    assert dry.any(), "в демо обязана быть отметка ниже бровки"
    assert np.all(cv["v_left"][dry] == 0.0)
    assert np.all(np.isfinite(cv["v_total"]))


def test_velocity_turns_over_on_the_floodplain():
    """Пойма добавляет площадь, но почти не добавляет расхода.

    Пока вода идёт руслом, скорость растёт с отметкой. Выше бровки
    прибавка площади обгоняет прибавку расхода, кривая скорости
    проходит через перелом и дальше падает. Это то, ради чего график
    скорости и рисуют: перелом виден глазом.
    """
    frags = dr.demo_fragments()
    cv = hs.rating_curve(frags, dr.SLOPE, step=0.05)
    lv, v = cv["level"], cv["v_total"]
    inside = (lv > dr.Z_BANK - 0.5) & (lv < dr.Z_BANK - 1e-9)
    assert np.all(np.diff(v[inside]) > 0), "в русле скорость обязана расти"
    top = int(np.argmax(v))
    assert lv[top] > dr.Z_BANK, "перелом лежит выше бровки"
    assert top < v.size - 1, "после перелома кривая обязана падать"
    assert float(v[-1]) < float(v[top])


def test_velocity_plots_like_the_other_two():
    """График по скорости строится тем же ядром, что расход и площадь."""
    curve = _demo_curve()
    for key in ("q_total", "area_total", "v_total"):
        plot = hs.curve_plot(curve, key, width=100.0, height=60.0)
        assert plot is not None, key
        xs = [p[0] for p in plot["pts"]]
        assert min(xs) >= -1e-9 and max(xs) <= 100.0 + 1e-6, key
    marks = hs.curve_marks(
        hs.curve_plot(curve, "v_total", width=100.0, height=60.0),
        [("УВВ1%", 157.5)], curve, "v_total")
    assert len(marks) == 1 and marks[0]["value"] > 0


# --- чертёж гидроствора: перегибы, пикеты, подвал ---------------------------

def test_breaks_keep_ends_and_divides():
    """Концы створа и границы участков в вертикалях есть всегда."""
    d = np.array([0.0, 10.0, 20.0, 30.0, 40.0])
    z = np.array([100.0, 99.0, 98.0, 97.0, 96.0])       # прямая, перегибов нет
    b = hs.break_indices(d, z, tol=0.02, keep=[20.0])
    assert b == [0, 2, 4]


def test_breaks_find_a_kink():
    d = np.array([0.0, 10.0, 20.0, 30.0])
    z = np.array([100.0, 99.0, 95.0, 94.0])
    assert hs.break_indices(d, z, tol=0.02) == [0, 1, 2, 3]


def test_breaks_zero_tolerance_takes_every_vertex():
    """Нулевой допуск означает вертикаль на каждой промерной точке."""
    d, z = dr.demo_profile()
    assert hs.break_indices(d, z, tol=0.0) == list(range(len(d)))


def test_breaks_thin_out_a_dense_profile():
    """Частый промер не превращает подвал в частокол."""
    d = np.linspace(0.0, 200.0, 201)
    z = 153.0 + (d - 100.0) ** 2 / 1500.0
    b = hs.break_indices(d, z, tol=0.05)
    assert 2 <= len(b) < 20, len(b)
    assert b[0] == 0 and b[-1] == len(d) - 1


def test_picket_counts_from_the_start_of_the_route():
    """Пикетаж идёт от начала трассы, а не от начала створа."""
    assert hs.picket_parts(70.0, start=4500.0) == (45, 70.0)
    assert hs.picket_parts(0.0) == (0, 0.0)
    n, rem = hs.picket_parts(99.999, start=0.0)
    assert (n, round(rem, 3)) == (1, 0.0), "полный пикет это следующий номер"


def test_footer_layout_puts_cells_under_their_place():
    """Ячейка стоит под своим местом створа, вертикали идут донизу."""
    d = np.array([0.0, 10.0, 20.0, 30.0])
    rows = [{"kind": "point", "key": "dist", "title": "Расстояние",
             "values": ["0", "10", "20", "30"]},
            {"kind": "span", "key": "part", "title": "Участок",
             "values": [(0.0, 20.0, "русло"), (20.0, 30.0, "пойма")]}]
    lay = hs.footer_layout(d, rows, [0, 2, 3], y_top=90.0, row_h=2.0)
    assert len(lay["rules"]) == 3 and lay["bottom"] == 86.0
    assert [v[0][0] for v in lay["verticals"]] == [0.0, 20.0, 30.0]
    for v in lay["verticals"]:
        assert v[0][1] == 90.0 and v[1][1] == lay["bottom"]
    pts = [c for c in lay["cells"] if c["key"] == "dist"]
    assert len(pts) == 3, "точечная строка пишется под вертикалями"
    spans = [c for c in lay["cells"] if c["key"] == "part"]
    assert [c["x"] for c in spans] == [10.0, 25.0], "полоса пишется по центру"


def test_footer_layout_clips_a_band_to_the_profile():
    """Полоса за пределами створа обрезается, а не уезжает за чертёж."""
    d = np.array([0.0, 50.0])
    rows = [{"kind": "span", "key": "veg", "title": "Растительность",
             "values": [(-20.0, 30.0, "лес"), (80.0, 90.0, "выгон")]}]
    lay = hs.footer_layout(d, rows, [0, 1], y_top=0.0, row_h=1.0)
    texts = [c["text"] for c in lay["cells"]]
    assert texts == ["лес"]
    assert lay["cells"][0]["span"] == (0.0, 30.0)


def test_footer_layout_survives_an_empty_request():
    lay = hs.footer_layout(np.array([0.0, 1.0]), [], [0, 1], 0.0, 1.0)
    assert lay["rules"] == [] and lay["cells"] == []


def test_demo_bands_cover_the_whole_section():
    """Полосы демо покрывают створ целиком и не наезжают друг на друга.

    Полоса нужна подвалу чертежа, и на демо она обязана быть готовым
    входом: без дыр между отрезками и без перекрытий, иначе ячейки лягут
    одна на другую.
    """
    secs = dr.demo_sections(n=2)
    rows = dr.bands_table(secs)
    assert rows, "полос нет вовсе"
    width = 2.0 * dr.FP_WIDTH + dr.CH_WIDTH
    by_row = {}
    for r in rows:
        by_row.setdefault((r["sec"], r["row"]), []).append(
            (r["dist_from"], r["dist_to"], r["text"]))
    assert len(by_row) == 4, "два створа по две строки"
    for key, spans in by_row.items():
        spans.sort()
        assert spans[0][0] == 0.0 and spans[-1][1] == width, key
        for (_a0, b0, _t0), (a1, _b1, _t1) in zip(spans, spans[1:]):
            assert abs(b0 - a1) < 1e-9, ("разрыв или перекрытие", key)


def test_demo_bands_are_ready_for_the_footer():
    """Отрезки демо ложатся в разметку подвала без обрезки."""
    secs = dr.demo_sections(n=1)
    d, _z = dr.demo_profile()
    spans = [(r["dist_from"], r["dist_to"], r["text"])
             for r in dr.bands_table(secs) if r["row"] == "Грунт"]
    lay = hs.footer_layout(
        d, [{"kind": "span", "key": "soil", "title": "Грунт",
             "values": spans}], [0, len(d) - 1], y_top=0.0, row_h=1.0)
    assert len(lay["cells"]) == len(spans)
    for c in lay["cells"]:
        assert c["span"][1] > c["span"][0]


def test_cell_carries_its_own_number():
    """У ячейки в числе лежит её собственное значение.

    Подпись идёт на чертёж, а число нужно оформлению и выборкам. Если в
    число класть отметку подвала, одну на весь створ, то подписи по
    этому полю у всех ячеек выйдут одинаковыми.
    """
    d = np.array([0.0, 10.0, 20.0])
    rows = [{"kind": "point", "key": "elev", "title": "Отметка",
             "values": ["99.50", "97.20", "99.80"],
             "nums": [99.5, 97.2, 99.8]},
            {"kind": "span", "key": "v", "title": "Скорость",
             "values": [(0.0, 10.0, "0.10", 0.1),
                        (10.0, 20.0, "1.17", 1.17)]},
            {"kind": "span", "key": "part", "title": "Участок",
             "values": [(0.0, 20.0, "русло")]}]
    lay = hs.footer_layout(d, rows, [0, 1, 2], y_top=0.0, row_h=1.0)
    nums = {}
    for c in lay["cells"]:
        nums.setdefault(c["key"], []).append(c.get("num"))
    assert nums["elev"] == [99.5, 97.2, 99.8]
    assert nums["v"] == [0.1, 1.17]
    assert nums["part"] == [None], "у названия участка числа не бывает"
    assert len(set(nums["elev"])) == 3, "числа обязаны различаться"


def test_cell_is_a_segment_of_its_full_width():
    """Ячейка идёт отрезком во всю ширину, а не штрихом у середины.

    Подпись тогда вешается стилем по линии и встаёт по центру ячейки
    сама. У строки по точкам ширина ячейки это промежуток до соседних
    вертикалей, у строки по участкам - сам участок.
    """
    d = np.array([0.0, 10.0, 30.0, 40.0])
    rows = [{"kind": "point", "key": "elev", "title": "Отметка",
             "values": ["1", "2", "3", "4"], "nums": [1.0, 2.0, 3.0, 4.0]},
            {"kind": "span", "key": "part", "title": "Участок",
             "values": [(0.0, 30.0, "русло"), (30.0, 40.0, "пойма")]}]
    lay = hs.footer_layout(d, rows, [0, 1, 2, 3], y_top=0.0, row_h=1.0)
    pts = [c["span"] for c in lay["cells"] if c["key"] == "elev"]
    assert pts == [(0.0, 5.0), (5.0, 20.0), (20.0, 35.0), (35.0, 40.0)]
    for a, b in pts:
        assert b > a or (a, b) == pts[0]
    spans = [c["span"] for c in lay["cells"] if c["key"] == "part"]
    assert spans == [(0.0, 30.0), (30.0, 40.0)]
