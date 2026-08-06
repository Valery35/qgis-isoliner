# -*- coding: utf-8 -*-
"""Тесты демонстрационного разреза верхнекамского типа (demo_stack).

Запуск: python test_demo_stack.py.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import demo_stack as ds  # noqa: E402

SALT_TOTAL = sum(r[2] for r in ds.SALT)
FOLD = {"xc": 250.0, "zc": ds.DEFAULT_TOP - 0.25 * SALT_TOTAL,
        "r": 0.45 * SALT_TOTAL, "turn": 190.0}
DOME = {"xc": 500.0, "yc": 140.0, "r": 0.85 * SALT_TOTAL,
        "ry": 0.68 * SALT_TOTAL,
        "up": ds.DEFAULT_LEVEL - ds.DEFAULT_TOP + 0.10 * SALT_TOTAL}
Z_TOP = ds.DEFAULT_RELIEF + 25.0
DEPTH = Z_TOP - (ds.DEFAULT_TOP - 1.8 * SALT_TOTAL)


def test_column_matches_the_reference():
    """Колонка та же, что в справочнике: порядок, коды, вид тела."""
    codes = [r[0] for r in ds.COLUMN]
    assert len(codes) == 36, len(codes)
    assert codes[:6] == ["Q", "ПЦТ", "ТКТ", "СМТ", "ПП", "ПКС"]
    assert codes[-3:] == ["ПДКС", "МГ", "НКС"]
    for code in ("И-К", "Б-В", "А'-КрI", "КрI-КрII"):
        row = next(r for r in ds.COLUMN if r[0] == code)
        assert row[1] == "междупластье", row
    assert [r[0] for r in ds.COVER] == ["Q", "ПЦТ", "ТКТ", "СМТ"]
    assert ds.SALT[0][0] == "ПП"


def test_quiet_part_is_ordinary():
    """Вне складки и купола колонка проходится сверху вниз по одному разу."""
    iv = ds.hole_intervals(60.0, Z_TOP, DEPTH, y=420.0)
    codes = [c for _f, _t, c in iv]
    assert codes[:5] == ["Q", "ПЦТ", "ТКТ", "СМТ", "ПП"]
    assert codes == list(dict.fromkeys(codes)), "тело вскрыто дважды"


def test_thickness_matches_the_setting():
    """Мощности соляных тел в спокойной части равны заданным."""
    iv = ds.hole_intervals(60.0, Z_TOP, DEPTH, step=0.05, y=420.0)
    thk = {c: t - f for f, t, c in iv}
    for code, _body, want, _col in ds.SALT:
        assert abs(thk[code] - want) < 0.15, (code, thk[code], want)


def test_marker_clay_survives_the_sampling():
    """Маркирующая глина в шестьдесят сантиметров не проваливается.

    Шаг по стволу мельче её мощности, иначе тонкое тело исчезает из
    модели бурения, а на разрезе появляется беспричинный разрыв.
    """
    iv = ds.hole_intervals(60.0, Z_TOP, DEPTH, y=420.0)
    assert "МГ" in [c for _f, _t, c in iv]


def test_cover_lies_on_the_salt():
    """Заполняющая толща доходит до кровли соли без зазора и без нахлёста."""
    iv = ds.hole_intervals(60.0, Z_TOP, DEPTH, y=420.0)
    by = {c: (f, t) for f, t, c in iv}
    assert abs(by[ds.FILL_CODE][1] - by["ПП"][0]) < 0.3, by[ds.FILL_CODE]


def test_fold_gives_three_entries():
    """У замка складки одно тело вскрывается трижды.

    Это и есть случай, ради которого демо делалось: над точкой в плане
    несколько кровель, и z(x, y) перестаёт существовать как функция.
    """
    best = 0
    for x in np.arange(FOLD["xc"] - 40.0, FOLD["xc"] + 40.0, 5.0):
        iv = ds.hole_intervals(float(x), Z_TOP, DEPTH, fold=FOLD)
        best = max(best, max(ds.count_entries(iv).values()))
    assert best >= 3, best


def test_dome_lifts_the_salt():
    """Над сводом купола кровля соли выше, чем вдали от него."""
    near = ds.hole_intervals(DOME["xc"], Z_TOP, DEPTH, dome=DOME,
                             y=DOME["yc"])
    far = ds.hole_intervals(60.0, Z_TOP, DEPTH, dome=DOME, y=420.0)
    n1 = next(f for f, _t, c in near if c in [r[0] for r in ds.SALT])
    f1 = next(f for f, _t, c in far if c in [r[0] for r in ds.SALT])
    assert n1 < f1 - 20.0, (n1, f1)


def test_nothing_survives_above_the_level():
    """Соли выше уровня растворения нет нигде на площади."""
    for x in (200.0, 400.0, 500.0, 560.0):
        for y in (60.0, 140.0, 300.0):
            zc = ds.level_at(x, y)
            idx = ds.column_at(x, [zc + 0.5], dome=DOME, y=y)
            assert idx[0] == -1, (x, y)


def test_erosion_cuts_the_whole_column():
    """Срез снимает не одно тело, а всё, что поднялось выше уровня.

    Над сводом первое соляное тело исчезает целиком, и под зеркало
    выходит следующее за ним.
    """
    surf = ds.surfaces(40, 30, 15.0, dome=DOME)
    sub = ds.subcrop_map(surf)
    kinds = set(int(v) for v in np.unique(sub))
    assert 0 in kinds, "в стороне от купола должно уцелеть первое тело"
    assert any(k > 0 for k in kinds), "над сводом должно выйти следующее"


def test_cut_leaves_no_holes_in_the_column():
    """Срез не выбрасывает тело в nodata: колонка полна по всей площади.

    Выброшенное тело обрывалось на разрезе отвесной стенкой по границе
    ячейки, и чертёж шёл ступенями. Сведение к нулю даёт клин.
    """
    surf = ds.surfaces(40, 30, 15.0, dome=DOME)
    for row in ds.SALT:
        top, bot = surf[row[0]]
        assert np.isfinite(top).all() and np.isfinite(bot).all(), row[0]
        assert float(np.nanmin(top - bot)) > -0.05, row[0]


def test_truncation_has_no_cliff():
    """У среза есть промежуточные мощности с каждой стороны.

    Ступени на чертеже брались отсюда: тело выбрасывалось в nodata, и
    между полной мощностью и её отсутствием не было ничего. Клин тонкого
    тела на пологом куполе занимает одну-две ячейки, но обрыва за одну
    ячейку быть не должно ни слева, ни справа от нуля.
    """
    cell = 15.0
    surf = ds.surfaces(40, 30, cell, dome=DOME)
    thk = (surf["ПП"][0] - surf["ПП"][1])[int(DOME["yc"] / cell)]
    full = next(r[2] for r in ds.SALT if r[0] == "ПП")
    zero = np.nonzero(thk < 0.05)[0]
    assert zero.size, "срез не сработал"
    lo, hi = int(zero[0]), int(zero[-1])
    left, right = thk[lo - 1], thk[hi + 1]
    assert 0.05 < left < 0.9 * full, (left, thk.round(1).tolist())
    assert 0.05 < right < 0.9 * full, (right, thk.round(1).tolist())


def test_fill_thins_over_the_dome():
    """Заполняющая толща над куполом тоньше, чем в стороне от него."""
    cell = 15.0
    surf = ds.surfaces(40, 30, cell, dome=DOME)
    thk = surf[ds.FILL_CODE][0] - surf[ds.FILL_CODE][1]
    j = int(DOME["xc"] / cell)
    i = int(DOME["yc"] / cell)
    assert thk[i, j] < float(np.nanmax(thk)) - 20.0, thk[i, j]


def test_waterproof_thins_over_the_dome():
    """Водозащитная толща над сводом тоньше: часть соли растворена."""
    cell = 15.0
    surf = ds.surfaces(40, 30, cell, dome=DOME)
    vzt = ds.waterproof_thickness(surf)
    j, i = int(DOME["xc"] / cell), int(DOME["yc"] / cell)
    assert vzt[i, j] < float(np.nanmax(vzt)) - 10.0, vzt[i, j]
    assert float(np.nanmin(vzt)) > 0.0


def test_wedge_thins_the_bed():
    """Выклинивание сводит мощность к нулю от x0 к x1."""
    surf = ds.surfaces(60, 10, 20.0,
                       wedge={"bed": "КрII", "x0": 900.0, "x1": 200.0})
    thk = surf["КрII"][0] - surf["КрII"][1]
    assert float(np.nanmean(thk[:, 5])) < 1.0
    assert float(np.nanmean(thk[:, -5])) > 3.0


def test_wedge_boundary_is_bent():
    """Граница выклинивания изогнута, а не идёт по меридиану."""
    surf = ds.surfaces(60, 30, 20.0,
                       wedge={"bed": "КрII", "x0": 900.0, "x1": 200.0})
    thk = surf["КрII"][0] - surf["КрII"][1]
    edge = [int(np.argmin(np.abs(thk[i] - 0.5))) for i in range(thk.shape[0])]
    assert max(edge) - min(edge) >= 2, edge


def test_surfaces_are_built_from_first_entry():
    """В зоне складки грид берёт первое сверху вскрытие."""
    cell = 20.0
    surf = ds.surfaces(40, 20, cell, fold=FOLD)
    top = surf["ПП"][0]
    assert np.isfinite(top).all()


def test_no_negative_thickness_in_quiet_part():
    """Вне складки отрицательных мощностей нет."""
    surf = ds.surfaces(30, 10, 20.0)
    for row in ds.SALT:
        thk = surf[row[0]][0] - surf[row[0]][1]
        assert float(np.nanmin(thk)) > -0.05, row[0]


def test_axis_of_vertical_hole_matches_the_old_way():
    """Ось с углом девяносто даёт то же, что вертикальная скважина.

    Проверка нужна не сама по себе: она страхует переход на общий путь.
    Если наклонная ось начнёт врать, вертикальный случай покажет это
    первым, потому что ответ для него известен.
    """
    x, y = 1200.0, 800.0
    z0 = ds.DEFAULT_RELIEF + 25.0
    depth = 240.0
    a = ds.hole_intervals(x, z0, depth, y=y)
    d, xs, ys, zs = ds.axis_points(x, y, z0, 0.0, 90.0, depth)
    b = ds.hole_intervals_along(d, xs, ys, zs)
    assert [c for _, _, c in a] == [c for _, _, c in b]


def test_inclined_hole_moves_away_from_the_collar():
    """У наклонного ствола забой уходит в сторону, и вскрытие вместе с ним.

    Это и есть причина, по которой ось нужна модели: считать пласт под
    устьем у наклонной скважины неверно.
    """
    x, y = 1200.0, 800.0
    z0 = ds.DEFAULT_RELIEF + 25.0
    d, xs, ys, zs = ds.axis_points(x, y, z0, 90.0, 60.0, 240.0)
    assert abs(float(xs[-1] - xs[0]) - 240.0 * np.cos(np.radians(60.0))) < 1e-6
    assert abs(float(ys[-1] - ys[0])) < 1e-6
    assert float(zs[-1]) < float(zs[0])
    iv = ds.hole_intervals_along(d, xs, ys, zs)
    # устье в демо поднято над рельефом, поэтому первый интервал
    # начинается там, где ствол входит в породу, а не с нуля
    assert iv and iv[0][0] > 0.0
    assert all(a < b for a, b, _ in iv)


def test_upward_hole_rises_along_the_axis():
    """У восходящего ствола отметка вдоль оси растёт.

    Подземные скважины бурятся из выработки вверх, и правило «отметка
    равна отметке устья минус глубина» для них неверно.
    """
    d, xs, ys, zs = ds.axis_points(1200.0, 800.0, 120.0, 0.0, -70.0, 60.0)
    assert float(zs[-1]) > float(zs[0])
    assert abs(float(zs[-1] - zs[0]) - 60.0 * np.sin(np.radians(70.0))) < 1e-6


def test_cover_wedges_out_and_bodies_reach_the_surface():
    """У левого края покровные сходят на нет, и тело выходит наружу.

    Без этого в демо ни одно тело не выходит на дневную поверхность, и
    следу выхода взяться неоткуда - а он нужен и для расчёта залегания, и
    как достоверный ноль мощности при сборке.
    """
    assert ds.cover_scale(ds.COVER_WEDGE_X * 2) == 1.0
    assert ds.cover_scale(0.0) == 0.0
    assert 0.0 < ds.cover_scale(ds.COVER_WEDGE_X / 2.0) < 1.0

    surf = ds.surfaces(80, 60, 25.0)
    relief = surf[ds.COLUMN[0][0]][0]
    found = False
    for code, _b, _t, _c in ds.COLUMN[1:]:
        top = surf[code][0]
        if float(np.nanmin(np.abs(top - relief))) < 1.5:
            found = True
            break
    assert found, "ни одно тело не выходит на поверхность"


def test_outcrop_points_and_lines_lie_on_the_relief():
    """Точки следа выхода лежат на рельефе, цепочки идут по нему же."""
    cell = 25.0
    surf = ds.surfaces(80, 60, cell)
    relief = surf[ds.COLUMN[0][0]][0]
    code = None
    for c, _b, _t, _col in ds.COLUMN[1:]:
        if ds.outcrop_points(surf, c, cell, tol=1.5):
            code = c
            break
    assert code is not None

    pts = ds.outcrop_points(surf, code, cell, tol=1.5)
    for x, y, z, cd in pts:
        j = int(x / cell)
        i = int(y / cell)
        assert abs(z - float(relief[i, j])) < 1e-6
        assert cd == code

    lines = ds.outcrop_lines(surf, code, cell, tol=1.5)
    assert all(len(ch) >= 2 for ch in lines)


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
