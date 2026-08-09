# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Разломы в кригинге: отбор соседей по видимости."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import kb2d  # noqa: E402


def _segs():
    """Вертикальный разлом от y=0 до y=26, затухающий выше."""
    return kb2d.fault_segments([[(20.0, 0.0), (20.0, 26.0)]])


def test_fault_stays_a_line_and_is_not_rasterized():
    """Разлом остаётся линией: звенья, а не ячейки.

    Растровая маска не воспроизводила диагональ и, что хуже, делала
    дырку в гриде. Возврата к сетке быть не должно.
    """
    segs = _segs()
    assert segs.shape == (1, 4)
    assert not hasattr(kb2d, "rasterize_faults"), \
        "растровая маска разломов вернулась"


def test_sight_is_blocked_across_and_open_around():
    """Сквозь разлом не видно, вокруг его конца видно.

    Это и есть причина, по которой затухающий разлом не требует, чтобы
    линия рассекала площадь: влияние огибает конец само.
    """
    s = _segs()
    assert not kb2d.visible(10, 10, 30, 10, s)
    assert kb2d.visible(10, 35, 30, 35, s)
    assert kb2d.visible(5, 10, 15, 10, s)


def test_cell_on_the_line_sees_its_wing():
    """Точка оценки НА линии видит замеры, а не остаётся слепой.

    Сторож против самоблокировки. Пока барьер был растровой маской, луч
    начинался в барьерной ячейке, первая же проверка попадала в маску, и
    такая ячейка не видела ни одного замера из всей площади. Она уходила
    в nodata, и вдоль разлома в гриде оставалась ступенчатая щель, из
    которой потом росла лесенка полос в 1.04.
    """
    s = _segs()
    rng = np.random.default_rng(0)
    xd = rng.uniform(0.0, 40.0, 200)
    yd = rng.uniform(0.0, 40.0, 200)
    seen = kb2d.visible_mask(20.0, 10.0, xd, yd, s)
    assert seen.sum() > 0, "точка на линии не видит ни одного замера"
    # Видит она при этом не всё подряд: замеры за линией отсечены у любой
    # точки, лежащей рядом с ней.
    aside = kb2d.visible_mask(19.5, 10.0, xd, yd, s)
    assert aside.sum() < len(xd), "разлом перестал отсекать"


def test_wing_follows_the_side_of_the_point():
    """Крыло определяется стороной точки, отдельного разбиения не нужно."""
    s = _segs()
    xd = np.array([5.0, 35.0])
    yd = np.array([10.0, 10.0])
    left = kb2d.visible_mask(10.0, 10.0, xd, yd, s)
    right = kb2d.visible_mask(30.0, 10.0, xd, yd, s)
    assert left.tolist() == [True, False]
    assert right.tolist() == [False, True]


def test_measurement_on_the_line_is_seen_from_both_wings():
    """Замер, стоящий точно на разломе, виден с обеих сторон.

    Скважина на линии принадлежит обоим крыльям, отбрасывать её незачем.
    """
    s = _segs()
    xd = np.array([20.0])
    yd = np.array([10.0])
    assert kb2d.visible_mask(10.0, 10.0, xd, yd, s)[0]
    assert kb2d.visible_mask(30.0, 10.0, xd, yd, s)[0]


def test_fault_keeps_the_step_sharp():
    """Через разлом ступень не размазывается, за его концом смыкается.

    Без разлома кригинг сглаживает скачок между блоками, и это верно для
    сплошного поля. Разлом говорит, что поля два.
    """
    rng = np.random.default_rng(1)
    xd = rng.uniform(0, 40, 80)
    yd = rng.uniform(0, 40, 80)
    vrd = np.where(xd < 20, 100.0, 120.0) + rng.normal(0, 0.3, 80)
    vg = kb2d.Variogram(0.1, [dict(it=1, cc=100.0, aa=25.0, ang=0.0,
                                   anis=1.0)])
    kw = dict(vg=vg, ktype=1, skmean=0.0, ndmin=2, ndmax=16,
              rad2=40.0 ** 2, nodata=-9999.0, xmn=0.0, ymn=0.0, cell=1.0,
              nx=40, ny=40)
    plain = kb2d.build_grid(xd, yd, vrd, **kw)
    cut = kb2d.build_grid(xd, yd, vrd, fault_segs=_segs(), **kw)

    def jump(g, y):
        row = g[40 - 1 - y]
        return float(row[21]) - float(row[18])

    assert jump(cut, 10) > jump(plain, 10) + 5.0
    assert jump(cut, 10) > 15.0
    assert jump(cut, 35) < jump(cut, 10) / 2.0


def test_grid_has_no_holes_along_the_fault():
    """Вдоль разлома в гриде не остаётся ни одной пустой ячейки.

    Главный сторож этой правки. Разлом взят косым: на диагонали растровая
    маска давала ступеньки, и именно там появлялась щель.
    """
    rng = np.random.default_rng(2)
    xd = rng.uniform(0, 40, 120)
    yd = rng.uniform(0, 40, 120)
    vrd = np.where(yd > xd, 100.0, 130.0) + rng.normal(0, 0.3, 120)
    vg = kb2d.Variogram(0.1, [dict(it=1, cc=100.0, aa=25.0, ang=0.0,
                                   anis=1.0)])
    segs = kb2d.fault_segments([[(2.0, 2.0), (38.0, 38.0)]])
    grid = kb2d.build_grid(xd, yd, vrd, vg=vg, ktype=1, skmean=0.0,
                           ndmin=1, ndmax=16, rad2=40.0 ** 2,
                           nodata=-9999.0, xmn=0.0, ymn=0.0, cell=1.0,
                           nx=40, ny=40, fault_segs=segs)
    assert not (grid == -9999.0).any(), \
        "вдоль разлома остались пустые ячейки: %d" % int((grid == -9999.0).sum())


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("ok: %s" % name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL: %s - %s" % (name, exc))
    print("\n%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_run())


def _scatter(n=200, seed=0):
    rng = np.random.default_rng(seed)
    return (rng.uniform(0.0, 60.0, n), rng.uniform(0.0, 60.0, n),
            rng.normal(100.0, 5.0, n))


def _grid_kw():
    vg = kb2d.Variogram(0.1, [dict(it=1, cc=100.0, aa=25.0, ang=0.0,
                                   anis=1.0)])
    return dict(vg=vg, ktype=1, skmean=0.0, ndmin=1, ndmax=16,
                rad2=(3 * 60.0) ** 2, nodata=-9999.0, xmn=0.0, ymn=0.0,
                cell=1.0, nx=60, ny=60)


def test_u_shaped_fault_blocks_from_outside_and_opens_at_the_mouth():
    """U-образный разлом: снаружи стенка отсекает, изнутри видно устье.

    Проверка по предложению Ивана Иванова: вогнутый барьер это классическое
    место, где подходы с трассировкой лучей ошибаются. Стенки U стоят на
    x = 20 и x = 40, дно на y = 15, устье открыто вверх.
    """
    segs = kb2d.fault_segments([[(20.0, 50.0), (20.0, 15.0),
                                 (40.0, 15.0), (40.0, 50.0)]])
    rng = np.random.default_rng(1)
    xd = rng.uniform(0.0, 60.0, 400)
    yd = rng.uniform(0.0, 60.0, 400)
    inside = (xd > 20.0) & (xd < 40.0) & (yd > 15.0) & (yd < 50.0)

    out_side = kb2d.visible_mask(10.0, 20.0, xd, yd, segs)
    assert int((out_side & inside).sum()) == 0, (
        "снаружи U видно внутренность: стенка не держит")

    deep = kb2d.visible_mask(30.0, 20.0, xd, yd, segs)
    near_mouth = kb2d.visible_mask(30.0, 48.0, xd, yd, segs)
    assert deep[inside].all(), "изнутри U не видно собственных замеров"
    # чем ближе к устью, тем шире конус наружу
    assert int((near_mouth & ~inside).sum()) > int((deep & ~inside).sum()), (
        "конус видимости через устье не расширяется к устью")


def test_zigzag_fault_leaves_no_holes_while_folds_are_coarser_than_the_cell():
    """Змейка с крупной амплитудой пустых ячеек не даёт.

    Второй случай из предложения Иванова. Пока складка крупнее ячейки,
    у каждой ячейки остаётся своё крыло с замерами.
    """
    xd, yd, vd = _scatter()
    zig = [[(5.0 + 5.0 * i, 30.0 + (20.0 if i % 2 else -20.0))
            for i in range(11)]]
    grid = kb2d.build_grid(xd, yd, vd,
                           fault_segs=kb2d.fault_segments(zig), **_grid_kw())
    assert not (grid == -9999.0).any(), (
        "змейка с крупной складкой оставила пустые ячейки")


def test_folds_finer_than_the_cell_create_pockets_and_that_is_expected():
    """Складка мельче ячейки даёт карманы без замеров, и это законно.

    Ячейка оказывается заперта в складке, где нет ни одного замера.
    Оценивать там не из чего, поэтому она честно остаётся пустой, а
    инструмент 1.02 предупреждает об этом в журнале. Тест закрепляет
    поведение как известное, чтобы оно не выглядело сюрпризом.
    """
    xd, yd, vd = _scatter()
    fine = [[(5.0 + 0.5 * i, 30.0 + (15.0 if i % 2 else -15.0))
             for i in range(101)]]
    grid = kb2d.build_grid(xd, yd, vd,
                           fault_segs=kb2d.fault_segments(fine), **_grid_kw())
    holes = int((grid == -9999.0).sum())
    assert holes > 0, "мелкая складка карманов не дала, проверка потеряла смысл"
    # карманы локальны: это не развал грида
    assert holes < grid.size // 4, "пустых ячеек стало непозволительно много"


def test_pockets_are_filled_from_the_neighbours():
    """Запертые ячейки заполняются по соседям, дырок не остаётся.

    Дырка в гриде дороже небольшого сдвига: по ней рвутся изолинии,
    разваливаются пояса и искажаются объёмы. Значение соседней ячейки
    даёт ошибку геометрии не больше размера самого кармана.
    """
    xd, yd, vd = _scatter()
    fine = [[(5.0 + 0.5 * i, 30.0 + (15.0 if i % 2 else -15.0))
             for i in range(101)]]
    grid = kb2d.build_grid(xd, yd, vd,
                           fault_segs=kb2d.fault_segments(fine), **_grid_kw())
    holes = int((grid == -9999.0).sum())
    assert holes > 0, "карманов не получилось, проверка потеряла смысл"
    filled_grid, filled, width = kb2d.fill_pockets(grid, -9999.0)
    assert not (filled_grid == -9999.0).any(), "после заполнения остались дырки"
    assert filled == holes, "заполнено не столько, сколько было пусто"
    assert width <= 2, "карман оказался шире двух ячеек: %d" % width


def test_filled_values_stay_within_the_data_range():
    """Заполненные значения не выходят за пределы рассчитанного поля.

    Среднее по соседям не может вынести ячейку за диапазон, но сторож
    нужен: волна идёт по уже заполненным ячейкам, и ошибка могла бы
    накапливаться от прохода к проходу.
    """
    xd, yd, vd = _scatter(seed=4)
    fine = [[(5.0 + 0.25 * i, 30.0 + (15.0 if i % 2 else -15.0))
             for i in range(201)]]
    grid = kb2d.build_grid(xd, yd, vd,
                           fault_segs=kb2d.fault_segments(fine), **_grid_kw())
    good = grid[grid != -9999.0]
    out, _, _ = kb2d.fill_pockets(grid, -9999.0)
    assert out.min() >= good.min() - 1e-9, "заполнение ушло ниже поля"
    assert out.max() <= good.max() + 1e-9, "заполнение ушло выше поля"


def test_fill_leaves_a_clean_grid_untouched():
    """Грид без пустот заполнение не трогает вовсе."""
    xd, yd, vd = _scatter(seed=6)
    grid = kb2d.build_grid(xd, yd, vd, **_grid_kw())
    assert not (grid == -9999.0).any()
    out, filled, width = kb2d.fill_pockets(grid, -9999.0)
    assert filled == 0 and width == 0
    assert np.array_equal(out, grid), "чистый грид изменился при заполнении"


def test_crossing_faults_need_no_relation_matrix():
    """Пересечения барьер обрабатывает сам, без матрицы отношений.

    Матрица отношений между разломами нужна подходу через дрейф, где
    функция разлома задана на всей площади и её надо где-то обрывать.
    Здесь барьер локален: луч блокирует то звено, которое он пересёк.
    """
    rng = np.random.default_rng(0)
    xd = rng.uniform(0.0, 60.0, 400)
    yd = rng.uniform(0.0, 60.0, 400)
    other = (xd > 30.0) & (yd > 30.0)
    for name, lines in (
            ("X", [[(30.0, 0.0), (30.0, 60.0)], [(0.0, 30.0), (60.0, 30.0)]]),
            ("T", [[(30.0, 0.0), (30.0, 30.0)], [(0.0, 30.0), (60.0, 30.0)]])):
        segs = kb2d.fault_segments(lines)
        seen = kb2d.visible_mask(15.0, 15.0, xd, yd, segs)
        assert int((seen & other).sum()) == 0, (
            "%s: из своего сектора видно чужой" % name)


def test_gap_in_digitising_leaks_and_is_reported():
    """Недоведённый конец пропускает замеры, и об этом сказано.

    Замер прямой: два ряда замеров по разные стороны вертикали, точка
    оценки у самого стыка. Без щели не проходит ни один, с щелью проходят.
    """
    ys = np.linspace(20.0, 29.0, 40)
    xd = np.concatenate([np.full(40, 26.0), np.full(40, 34.0)])
    yd = np.concatenate([ys, ys])
    right = np.array([False] * 40 + [True] * 40)

    def leaked(gap):
        lines = [[(30.0, 0.0), (30.0, 30.0 - gap)],
                 [(0.0, 30.0), (60.0, 30.0)]]
        segs = kb2d.fault_segments(lines)
        return sum(int((kb2d.visible_mask(26.0, y, xd, yd, segs) & right).sum())
                   for y in (22.0, 26.0, 28.0, 29.5))

    assert leaked(0.0) == 0, "сомкнутый стык пропускает замеры"
    assert leaked(5.0) > leaked(1.0) > 0, "щель не пропускает или не растёт"

    # и то же самое обязано быть названо в отчёте
    joined, gaps = kb2d.junction_report(
        [[(30.0, 0.0), (30.0, 25.0)], [(0.0, 30.0), (60.0, 30.0)]], tol=10.0)
    assert joined == 0 and len(gaps) == 1
    assert abs(gaps[0][2] - 5.0) < 1e-6, "зазор измерен неверно"


def test_closed_junction_is_counted_not_reported_as_gap():
    """Сомкнутый T-стык считается стыком, а не щелью."""
    joined, gaps = kb2d.junction_report(
        [[(30.0, 0.0), (30.0, 30.0)], [(0.0, 30.0), (60.0, 30.0)]], tol=10.0)
    assert joined == 1 and not gaps


def test_independent_faults_are_not_called_junctions():
    """Разлом, ни к чему не примыкающий, в отчёт не попадает."""
    joined, gaps = kb2d.junction_report(
        [[(10.0, 0.0), (10.0, 60.0)], [(50.0, 0.0), (50.0, 60.0)]], tol=10.0)
    assert joined == 0 and not gaps


def _ref_visible(xloc, yloc, xs, ys, segs):
    """Прежняя реализация видимости: полная матрица, без отсева."""
    ax = segs[:, 0][:, None]
    ay = segs[:, 1][:, None]
    bx = segs[:, 2][:, None]
    by = segs[:, 3][:, None]
    d1 = (bx - ax) * (yloc - ay) - (by - ay) * (xloc - ax)
    d2 = (bx - ax) * (ys - ay) - (by - ay) * (xs - ax)
    rx, ry = xs - xloc, ys - yloc
    d3 = rx * (ay - yloc) - ry * (ax - xloc)
    d4 = rx * (by - yloc) - ry * (bx - xloc)
    return ~(((d1 * d2) < 0.0) & ((d3 * d4) < 0.0)).any(axis=0)


def test_bbox_prefilter_does_not_change_a_single_answer():
    """Отсев по габаритам ускоряет и НЕ меняет результат.

    Ускорение бессмысленно, если оно хоть где-то меняет ответ. Проверка
    случайная и широкая: разное число звеньев, разное число замеров,
    разное положение точки оценки.
    """
    rng = np.random.default_rng(7)
    checked = 0
    for _ in range(300):
        nseg = int(rng.integers(1, 40))
        pts = rng.uniform(0.0, 100.0, (nseg + 1, 2))
        segs = kb2d.fault_segments([[tuple(p) for p in pts]])
        n = int(rng.integers(1, 80))
        xs = rng.uniform(0.0, 100.0, n)
        ys = rng.uniform(0.0, 100.0, n)
        px, py = rng.uniform(0.0, 100.0, 2)
        got = kb2d.visible_mask(px, py, xs, ys, segs)
        want = _ref_visible(px, py, xs, ys, segs)
        assert np.array_equal(got, want), "отсев изменил ответ"
        checked += n
    assert checked > 5000, "проверка вышла слишком узкой"


def test_prefilter_survives_a_far_away_network():
    """Сеть разломов в стороне от пучка лучей не блокирует ничего.

    Здесь отсев снимает все звенья до расчёта, и это ровно тот путь,
    который даёт основной выигрыш.
    """
    far = kb2d.fault_segments([[(1000.0 + i, 1000.0) for i in range(60)]])
    xs = np.linspace(0.0, 10.0, 50)
    ys = np.zeros(50)
    assert kb2d.visible_mask(5.0, 5.0, xs, ys, far).all()


def test_indicator_kriging_breaks_the_class_boundary_at_a_fault():
    """Граница категории рвётся на разломе так же, как поверхность.

    Замеры разрежены у самой линии, полоса вокруг неё пустая. Без барьера
    вероятность класса плавно съезжает через разрыв, которого пласт не
    знает. С барьером получается ступень.
    """
    rng = np.random.default_rng(3)
    xd = np.concatenate([rng.uniform(0.0, 25.0, 120),
                         rng.uniform(35.0, 60.0, 120)])
    yd = rng.uniform(0.0, 60.0, 240)
    labels = np.where(xd < 30.0, "соль", "глина")
    classes = ["соль", "глина"]
    segs = kb2d.fault_segments([[(30.0, 0.0), (30.0, 60.0)]])
    kw = dict(xmn=0.0, ymn=0.0, cell=1.0, nx=60, ny=60, ndmin=1, ndmax=16,
              radius=40.0, nodata=-9999.0)
    plain, _, _ = kb2d.categorical_indicator_grids(xd, yd, labels, classes, **kw)
    cut, _, _ = kb2d.categorical_indicator_grids(xd, yd, labels, classes,
                                                 fault_segs=segs, **kw)
    row = 60 - 1 - 30
    # столбцы 29 и 31 лежат по разные стороны линии, 30 - на самой линии
    jump_plain = float(plain[row, 29, 0] - plain[row, 31, 0])
    jump_cut = float(cut[row, 29, 0] - cut[row, 31, 0])
    assert jump_cut > 0.8, "барьер не держит границу класса: %.2f" % jump_cut
    assert jump_cut > jump_plain + 0.5, (
        "с разломом граница не резче: %.2f против %.2f"
        % (jump_cut, jump_plain))


def test_indicator_kriging_without_faults_is_unchanged():
    """Пустой список разломов ничего не меняет."""
    rng = np.random.default_rng(11)
    xd = rng.uniform(0.0, 60.0, 200)
    yd = rng.uniform(0.0, 60.0, 200)
    labels = np.where(xd + yd < 60.0, "a", "b")
    kw = dict(xmn=0.0, ymn=0.0, cell=2.0, nx=30, ny=30, ndmin=1, ndmax=12,
              radius=40.0, nodata=-9999.0)
    a, za, _ = kb2d.categorical_indicator_grids(xd, yd, labels, ["a", "b"], **kw)
    b, zb, _ = kb2d.categorical_indicator_grids(xd, yd, labels, ["a", "b"],
                                                fault_segs=None, **kw)
    assert np.array_equal(a, b) and np.array_equal(za, zb)
