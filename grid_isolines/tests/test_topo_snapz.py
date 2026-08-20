# -*- coding: utf-8 -*-
"""Тесты снятия отметок с примыкающих горизонталей (topo_snapz).

Запуск: python test_topo_snapz.py.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import topo_snapz as sz  # noqa: E402


def test_crossing_contours_give_profile():
    """Бровка поперёк горизонталей: профиль линеен между пересечениями."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(20.0, -10.0), (20.0, 10.0)], "z": 110.0},
                {"pts": [(60.0, -10.0), (60.0, 10.0)], "z": 112.0},
                {"pts": [(90.0, -10.0), (90.0, 10.0)], "z": 113.5}]
    done, skipped = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert not skipped and len(done) == 1
    zs = done[0]["zs"]
    assert done[0]["n_samples"] == 3
    assert abs(zs[0] - 110.0) < 1e-9          # до первой точки константа
    assert abs(zs[-1] - 113.5) < 1e-9         # после последней константа
    # в середине линейная интерполяция: вершин у линии две, проверим
    # плотнее через профиль по дуге
    dense = [(x, 0.0) for x in range(0, 101, 10)]
    done2, _ = sz.snap_elevations([{"pts": dense}], contours, tol=0.5)
    zs2 = done2[0]["zs"]
    assert abs(zs2[4] - 111.0) < 1e-9         # x=40: середина 110..112


def test_node_ends_within_tolerance():
    """Горизонтали, доведённые до линии узлами: концы в допуске работают."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(30.0, 0.3), (30.0, 20.0)], "z": 105.0},
                {"pts": [(70.0, -0.4), (70.0, -20.0)], "z": 107.0}]
    done, skipped = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert not skipped
    assert done[0]["n_samples"] == 2
    dense = [(x, 0.0) for x in range(0, 101, 5)]
    done2, _ = sz.snap_elevations([{"pts": dense}], contours, tol=0.5)
    zs = done2[0]["zs"]
    assert abs(zs[10] - 106.0) < 1e-9         # x=50 между 105 и 107


def test_out_of_tolerance_is_ignored():
    """Конец горизонтали дальше допуска не считается примыканием."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(30.0, 2.0), (30.0, 20.0)], "z": 105.0}]
    done, skipped = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert not done and len(skipped) == 1
    assert "не примыкает" in skipped[0]["reason"]


def test_single_contour_gives_constant():
    """Одна примкнувшая горизонталь - постоянная отметка по всей линии."""
    line = [(0.0, 0.0), (50.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(40.0, -5.0), (40.0, 5.0)], "z": 108.0}]
    done, _ = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert done[0]["n_samples"] == 1
    assert np.allclose(done[0]["zs"], 108.0)


def test_extra_fields_preserved():
    """Служебные поля линии (link, kind) проходят сквозь ядро."""
    line = {"pts": [(0.0, 0.0), (10.0, 0.0)], "link": "a", "kind": "brow"}
    contours = [{"pts": [(5.0, -1.0), (5.0, 1.0)], "z": 100.0}]
    done, _ = sz.snap_elevations([line], contours, tol=0.5)
    assert done[0]["link"] == "a" and done[0]["kind"] == "brow"


def test_contour_without_z_skipped():
    """Горизонталь без отметки не участвует."""
    line = [(0.0, 0.0), (100.0, 0.0)]
    contours = [{"pts": [(30.0, -5.0), (30.0, 5.0)], "z": None},
                {"pts": [(60.0, -5.0), (60.0, 5.0)], "z": 111.0}]
    done, _ = sz.snap_elevations([{"pts": line}], contours, tol=0.5)
    assert done and done[0]["n_samples"] == 1
    assert np.allclose(done[0]["zs"], 111.0)


def test_curved_line_arc_parametrisation():
    """Г-образная бровка: интерполяция идёт по дуге, а не по прямой."""
    contours = [{"pts": [(10.0, -5.0), (10.0, 5.0)], "z": 100.0},
                {"pts": [(45.0, 40.0), (55.0, 40.0)], "z": 109.0}]
    dense = ([(x, 0.0) for x in range(0, 51, 5)] +
             [(50.0, y) for y in range(5, 51, 5)])
    done, _ = sz.snap_elevations([{"pts": dense}], contours, tol=0.5)
    zs = done[0]["zs"]
    # дуга до угла 50, до второй точки 90; точка s=50 (угол):
    # (50-10)/(90-10) = 0.5 пути, отметка 104.5
    i_corner = 10
    assert abs(zs[i_corner] - 104.5) < 1e-6


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


# --- вершины в точках встречи ---------------------------------------------

def test_intermediate_contours_are_not_lost_on_a_coarse_line():
    """Средние горизонтали доходят до результата, а не пропадают.

    Дефект нашёлся при проверочном проходе по группе 2. Отметки ставились
    только в существующие вершины линии, а горизонтали пересекают её где
    придётся. Прямая бровка из двух вершин, пересекающая три горизонтали,
    получала ровный скат от первой отметки к последней, и средняя
    пропадала молча.

    Существующие тесты дефект не ловили: во всех линия была достаточно
    частой, чтобы вершина нашлась рядом с каждой встречей.
    """
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    contours = [dict(pts=np.array([[x, -10.0], [x, 10.0]]), z=z)
                for x, z in ((20.0, 150.0), (50.0, 151.0), (80.0, 152.0))]
    done, skipped = sz.snap_elevations([{"pts": line}], contours, tol=1.0)
    assert done and not skipped
    d = done[0]
    assert len(d["pts"]) > len(line), "вершины в точках встречи не вставлены"
    assert 151.0 in list(np.round(d["zs"], 6)), "средняя горизонталь потерялась"
    assert d["n_samples"] == 3


def test_densify_keeps_the_original_vertices_and_order():
    """Вставка вершин не трогает исходные и не меняет порядок."""
    line = np.array([[0.0, 0.0], [30.0, 0.0], [30.0, 40.0]])
    dense, cum = sz.densify_at(line, [10.0, 45.0])
    assert len(dense) == 5
    for v in line:
        assert any(np.allclose(v, d) for d in dense), "исходная вершина пропала"
    assert np.all(np.diff(cum) >= -1e-12), "дуговая координата пошла назад"


def test_densify_ignores_positions_outside_the_line():
    """Позиции за пределами линии вершин не добавляют."""
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    dense, _ = sz.densify_at(line, [-5.0, 0.0, 100.0, 250.0])
    assert len(dense) == len(line)


def test_geometry_and_elevations_stay_the_same_length():
    """Вершин и отметок поровну: иначе инструмент соберёт кривую геометрию."""
    line = np.array([[0.0, 0.0], [60.0, 0.0], [60.0, 60.0]])
    contours = [dict(pts=np.array([[x, -10.0], [x, 10.0]]), z=z)
                for x, z in ((15.0, 150.0), (45.0, 151.0))]
    done, _ = sz.snap_elevations([{"pts": line}], contours, tol=1.0)
    d = done[0]
    assert len(d["pts"]) == len(d["zs"])


# --- высотные отметки и расстановка вершин ---------------------------------

def _pt_line():
    return np.array([[0.0, 0.0], [100.0, 0.0]])


def test_spot_heights_are_a_source_of_elevation():
    """Точка высот у линии даёт опорную отметку.

    Топоплан из Автокада часто приходит без Z у бровок и откосов, а
    отметки на нём есть отдельными точками. Точка линию не пересекает,
    поэтому встречей считается близость.
    """
    got = sz.gather_point_samples(_pt_line(),
                                  np.array([(50.0, 0.8, 151.0),
                                            (65.0, -0.5, 151.5)]), tol=2.0)
    assert [(round(s, 3), z) for s, z in got] == [(50.0, 151.0),
                                                  (65.0, 151.5)]


def test_far_spot_heights_are_ignored():
    got = sz.gather_point_samples(_pt_line(),
                                  np.array([(50.0, 40.0, 160.0)]), tol=2.0)
    assert got == []


def test_meeting_moves_onto_a_nearby_existing_vertex():
    """Рядом стоящая вершина принимает отметку, новая не добавляется.

    Правило В. Швалева: линия не должна распухать лишними узлами там, где
    вершина уже есть.
    """
    line = np.array([[0.0, 0.0], [20.3, 0.0], [100.0, 0.0]])
    adds, fixed = sz.plan_vertices(line, [(20.0, 150.0)], snap_tol=1.0,
                                   min_step=0.0)
    assert adds == [], "вершина добавлена там, где уже была своя"
    assert abs(fixed[0][0] - 20.3) < 1e-9, "проба не переехала на вершину"


def test_meeting_far_from_any_vertex_inserts_one():
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    adds, _ = sz.plan_vertices(line, [(50.0, 151.0)], snap_tol=1.0,
                               min_step=0.0)
    assert adds == [50.0]


def test_inserted_vertices_are_thinned_by_the_step():
    """Две вставки подряд не ближе заданного шага."""
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    samples = [(float(x), 150.0) for x in range(10, 95, 5)]
    adds, _ = sz.plan_vertices(line, samples, snap_tol=0.1, min_step=20.0)
    assert adds and all(b - a >= 20.0 - 1e-9
                        for a, b in zip(adds[:-1], adds[1:]))
    assert len(adds) < len(samples), "прореживание не сработало"


def test_thinned_out_sample_still_shapes_the_profile():
    """Проба без своей вершины не пропадает из профиля.

    Это и означает «дальше допуска - интерполировать»: соседние вершины
    к такой пробе тянутся.
    """
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    samples = [(10.0, 150.0), (50.0, 155.0), (90.0, 150.0)]
    adds, fixed = sz.plan_vertices(line, samples, snap_tol=0.1,
                                   min_step=100.0)
    assert len(adds) == 1, "прореживание оставило больше одной вставки"
    assert len(fixed) == 3, "проба выпала из ряда"


def test_points_and_contours_work_together():
    """Горизонтали и точки высот дают общий ряд опорных отметок."""
    line = _pt_line()
    contours = [dict(pts=np.array([[x, -10.0], [x, 10.0]]), z=z)
                for x, z in ((20.0, 150.0), (80.0, 152.0))]
    pts = np.array([(50.0, 0.8, 151.0)])
    done, _ = sz.snap_elevations([{"pts": line}], contours, tol=1.0,
                                 points=pts, pt_tol=2.0)
    d = done[0]
    assert d["n_samples"] == 3
    assert 151.0 in list(np.round(d["zs"], 6)), "отметка точки потерялась"


# --- замкнутые контуры -----------------------------------------------------

RING = np.array([[0.0, 0.0], [100.0, 0.0], [100.0, 100.0], [0.0, 100.0],
                 [0.0, 0.0]])


def test_ring_has_no_step_at_the_seam():
    """У кольца отметка идёт через замыкание, а не обрывается.

    Предложение В. Швалева профилировать полигоны. Дуговая координата у
    кольца циклическая: держать постоянную отметку за крайними пробами,
    как у открытой линии, значило бы оставить ступень ровно в том месте,
    где контур смыкается сам с собой.
    """
    samples = [(50.0, 150.0), (150.0, 152.0), (250.0, 154.0)]
    zc, _ = sz.profile_from_samples(RING, samples, closed=True)
    assert abs(zc[0] - zc[-1]) < 1e-9, "стык кольца разошёлся"
    zo, _ = sz.profile_from_samples(RING, samples, closed=False)
    assert abs(zo[0] - zo[-1]) > 1.0, "проверка потеряла смысл: ступени нет"


def test_ring_value_at_the_seam_comes_from_both_sides():
    """Отметка на замыкании считается по пробам с обеих сторон кольца.

    Кольцо периметром 400, пробы на 50 и 250. Через замыкание до пробы на
    250 идти 150 единиц назад, а до пробы на 50 всего 50 вперёд, поэтому
    отметка на стыке лежит не посередине, а ближе к ближней пробе:
    154 + (150/200)·(150-154) = 151.

    Число выведено, а не подсмотрено: первая моя запись теста ждала
    среднего и была неверной.
    """
    zc, _ = sz.profile_from_samples(RING, [(50.0, 150.0), (250.0, 154.0)],
                                    closed=True)
    assert abs(zc[0] - 151.0) < 1e-9, "на стыке %.3f вместо 151" % zc[0]


def test_closed_ring_is_detected_by_coinciding_ends():
    """Кольцо распознаётся само, отдельного флажка не нужно."""
    contours = [dict(pts=np.array([[x, -10.0], [x, 200.0]]), z=z)
                for x, z in ((25.0, 150.0), (75.0, 152.0))]
    done, _ = sz.snap_elevations([{"pts": RING}], contours, tol=1.0)
    d = done[0]
    assert abs(d["zs"][0] - d["zs"][-1]) < 1e-9, "стык кольца разошёлся"


def test_open_line_still_holds_its_ends():
    """Открытая линия по-прежнему держит отметку за крайними пробами.

    Экстраполировать уклон опасно, и для незамкнутой линии прежнее
    поведение остаётся.
    """
    line = np.array([[0.0, 0.0], [100.0, 0.0]])
    zs, _ = sz.profile_from_samples(line, [(20.0, 150.0), (80.0, 152.0)],
                                    closed=False)
    assert abs(zs[0] - 150.0) < 1e-9 and abs(zs[-1] - 152.0) < 1e-9


# --- сетка сегментов: та же выборка, но без перебора всех пар ----------------

def _slow_gather(line_pts, contours, tol):
    """Прежний перебор всех пар: эталон для сверки."""
    line_pts = np.asarray(line_pts, dtype=float)
    cum = sz._cum_length(line_pts)
    out = []
    for ct in contours:
        z = ct.get("z")
        if z is None:
            continue
        cpts = np.asarray(ct["pts"], dtype=float)
        if len(cpts) < 1:
            continue
        if (cpts[:, 0].max() < line_pts[:, 0].min() - tol
                or cpts[:, 0].min() > line_pts[:, 0].max() + tol
                or cpts[:, 1].max() < line_pts[:, 1].min() - tol
                or cpts[:, 1].min() > line_pts[:, 1].max() + tol):
            continue
        hit = False
        for j in range(len(cpts) - 1):
            for i in range(len(line_pts) - 1):
                r = sz._seg_intersect(line_pts[i], line_pts[i + 1],
                                      cpts[j], cpts[j + 1])
                if r is not None:
                    t, _u = r
                    seg = np.hypot(*(line_pts[i + 1] - line_pts[i]))
                    out.append((cum[i] + t * seg, float(z)))
                    hit = True
        if not hit:
            for end in (cpts[0], cpts[-1]):
                d, s = sz._point_to_polyline(end, line_pts, cum)
                if d <= tol:
                    out.append((s, float(z)))
    return out


def test_grid_gives_the_same_samples_as_the_full_search():
    """Сетка ускоряет, но не меняет результат.

    На топоплане контур дороги в тысячи вершин против трёх сотен
    горизонталей давал сотни миллионов пар отрезков, и профилирование
    одного кольца занимало семь минут. Сетка оставляет только соседей.
    Выборка встреч от этого меняться не имеет права.
    """
    rng = np.random.default_rng(3)
    for _case in range(25):
        n = int(rng.integers(3, 60))
        line = np.c_[np.cumsum(rng.normal(0, 3, n)),
                     np.cumsum(rng.normal(0, 3, n))]
        cs = []
        for k in range(int(rng.integers(1, 12))):
            m = int(rng.integers(2, 30))
            cs.append({"pts": np.c_[np.cumsum(rng.normal(0, 4, m)),
                                    np.cumsum(rng.normal(0, 4, m))],
                       "z": float(k)})
        want = sorted((round(s, 9), z) for s, z in _slow_gather(line, cs, 0.5))
        got = sorted((round(s, 9), z)
                     for s, z in sz.gather_samples(line, cs, 0.5))
        assert got == want


def test_grid_finds_a_crossing_far_from_the_line_start():
    """Встреча в конце длинной линии не теряется: ячейка своя у каждой.

    Горизонталь пересекает линию ровно в её вершине, и встречу дают оба
    смежных сегмента. Это поведение прежнего ядра: дубль с той же дуговой
    координатой и той же отметкой профилю не мешает.
    """
    line = np.c_[np.linspace(0, 1000, 501), np.zeros(501)]
    cs = [{"pts": np.array([[990.0, -5.0], [990.0, 5.0]]), "z": 12.0}]
    got = sz.gather_samples(line, cs, 0.5)
    assert got, "встреча у конца линии потеряна"
    assert all(abs(s - 990.0) < 1e-6 and z == 12.0 for s, z in got)


def test_grid_survives_a_single_vertex_line():
    assert sz.gather_samples(np.array([[0.0, 0.0]]), [], 0.5) == []


def test_source_gap_notices_a_quarrel_between_sources():
    """Спор источников считается числом, а не решается за пользователя.

    Высотные отметки и горизонтали ложатся на одну и ту же линию. Если
    они описывают одно и то же, значения совпадают. Расхождение в метры
    означает либо обрезки изолиний у контура, либо разные объекты:
    точки описывают полотно, горизонтали землю вокруг.
    """
    sc = [(0.0, 100.0), (10.0, 110.0), (20.0, 120.0)]
    sp = [(5.0, 105.0), (15.0, 115.0)]
    n, med, worst = sz.source_gap(sc, sp)
    assert (n, med, worst) == (2, 0.0, 0.0), "согласные источники не спорят"
    sp_bad = [(5.0, 100.0), (15.0, 109.0)]
    n, med, worst = sz.source_gap(sc, sp_bad)
    assert n == 2 and abs(med - 5.5) < 1e-9 and abs(worst - 6.0) < 1e-9


def test_source_gap_ignores_points_outside_the_contour_range():
    sc = [(10.0, 100.0), (20.0, 110.0)]
    assert sz.source_gap(sc, [(0.0, 50.0)]) == (0, 0.0, 0.0)
    assert sz.source_gap([], [(1.0, 2.0)]) == (0, 0.0, 0.0)


def test_snap_elevations_reports_the_gap():
    """Расхождение доезжает до вызывающего в результате прохода."""
    line = np.c_[np.linspace(0.0, 100.0, 11), np.zeros(11)]
    contours = [{"pts": np.array([[x, -5.0], [x, 5.0]]), "z": 100.0 + x / 10.0}
                for x in (10.0, 50.0, 90.0)]
    points = np.array([[50.0, 0.0, 90.0]])       # на 15 м ниже земли
    done, _skip = sz.snap_elevations([{"pts": line}], contours, 0.5,
                                     points=points, pt_tol=0.5)
    n, _med, worst = done[0]["src_gap"]
    assert n == 1 and worst > 10.0


# --- направление линии: всегда сверху вниз по склону ------------------------

def test_line_is_turned_downhill():
    """Первая вершина выше последней, чтобы стрелка ставилась стилем."""
    pts = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    p, z, flipped = sz.orient_downhill(pts, [100.0, 105.0, 110.0])
    assert flipped and z == [110.0, 105.0, 100.0]
    assert p[0] == (20.0, 0.0)


def test_downhill_line_is_left_alone():
    pts = [(0.0, 0.0), (10.0, 0.0)]
    p, z, flipped = sz.orient_downhill(pts, [110.0, 100.0])
    assert not flipped and p == pts and z == [110.0, 100.0]


def test_equal_ends_decided_by_the_halves():
    """У бровки концы бывают на одной отметке: решают половины.

    Сумму падений по ходу для этого брать нельзя: она телескопически
    равна разности концов и при равных концах всегда ноль.
    """
    pts = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0)]
    _p, _z, flipped = sz.orient_downhill(pts, [100.0, 101.0, 103.0, 100.0])
    assert flipped, "линия в целом поднимается, её надо развернуть"
    _p, _z, flipped = sz.orient_downhill(pts, [100.0, 99.0, 97.0, 100.0])
    assert not flipped


def test_flat_line_keeps_its_order():
    """Там, где направления нет, выдумывать его не надо."""
    pts = [(0.0, 0.0), (10.0, 0.0), (20.0, 0.0)]
    p, z, flipped = sz.orient_downhill(pts, [100.0, 100.0, 100.0])
    assert not flipped and p == pts and z == [100.0] * 3


def test_short_line_survives():
    assert sz.orient_downhill([(0.0, 0.0)], [100.0])[2] is False
    assert sz.orient_downhill([], [])[2] is False
