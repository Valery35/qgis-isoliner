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
    ns = {}
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
