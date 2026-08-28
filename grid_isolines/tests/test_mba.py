# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты мультисеточных B-сплайнов. Ядро не тянет QGIS.
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import mba  # noqa: E402


def _ref_b(i, t):
    """Кубический B-сплайн отдельной записью, для сверки с векторной."""
    if i == 0:
        return (1 - t) ** 3 / 6.0
    if i == 1:
        return (3 * t ** 3 - 6 * t ** 2 + 4) / 6.0
    if i == 2:
        return (-3 * t ** 3 + 3 * t ** 2 + 3 * t + 1) / 6.0
    return t ** 3 / 6.0


def test_weights_sum_to_one():
    """Разбиение единицы: на нём держится вся конструкция.

    Если сумма весов не единица, поверхность поедет по уровню даже там,
    где данные постоянны.
    """
    w = mba._weights_1d(np.linspace(0.0, 1.0, 11))
    assert np.allclose(w.sum(axis=0), 1.0)
    assert np.all(w >= 0.0)


def test_vector_evaluation_matches_the_plain_formula():
    """Векторная оценка обязана совпасть с прямой суммой по носителю."""
    rng = np.random.default_rng(7)
    pts = rng.uniform(0, 10, (60, 2))
    vals = rng.normal(0, 1, 60)
    lat = mba.Lattice([0, 0], [10, 10], [4, 4]).fit(pts, vals)

    probe = rng.uniform(0.5, 9.5, (25, 2))
    got = lat.evaluate(probe)
    ref = []
    for p in probe:
        rel = (p - lat.lo) / lat.step
        base = np.clip(np.floor(rel).astype(int), 0, lat.n - 1)
        frac = rel - base
        s = 0.0
        for a in range(4):
            for b in range(4):
                s += (_ref_b(a, frac[0]) * _ref_b(b, frac[1])
                      * lat.coef[base[0] + a, base[1] + b])
        ref.append(s)
    assert np.allclose(got, np.array(ref), atol=1e-12)


def test_more_levels_come_closer_to_the_data():
    """Каждый уровень подхватывает остаток предыдущего.

    Это и есть управление сглаживанием: мало уровней - тренд, много -
    поверхность садится на замеры.
    """
    rng = np.random.default_rng(1)
    pts = rng.uniform(0, 100, (400, 2))
    vals = (np.sin(pts[:, 0] / 18.0) * 12 + np.cos(pts[:, 1] / 13.0) * 8)
    prev = None
    for lv in (1, 3, 5, 8):
        lat = mba.fit(pts, vals, grid=(2, 2), levels=lv)
        err = float(np.max(np.abs(mba.evaluate(lat, pts) - vals)))
        if prev is not None:
            assert err < prev, "уровень обязан уменьшать невязку"
        prev = err
    assert prev < 0.05


def test_anisotropic_lattice_stretches_the_influence():
    """Разные числа ячеек по осям дают вытянутое влияние.

    На разведочной сети, вытянутой по простиранию, это и нужно: замер
    расходится вдоль структуры дальше, чем поперёк.
    """
    pts = np.array([[50.0, 50.0]])
    vals = np.array([10.0])
    lat = mba.fit(pts, vals, lo=[0, 0], hi=[100, 100], grid=(2, 16),
                  levels=1)
    xs = np.array([25.0, 75.0])
    along = mba.evaluate(lat, np.column_stack([xs, np.full(2, 50.0)]))
    across = mba.evaluate(lat, np.column_stack([np.full(2, 50.0), xs]))
    assert np.all(along > 1.0), "вдоль длинной оси влияние должно доставать"
    assert np.all(across < 0.5), "поперёк оно обязано затухнуть"


def test_three_dimensions_work_by_the_same_code():
    """Размерность не зашита: объём считается тем же кодом."""
    rng = np.random.default_rng(3)
    pts = rng.uniform(0, 100, (1500, 3))
    vals = (np.sin(pts[:, 0] / 20) * 5 + np.cos(pts[:, 1] / 25) * 3
            + pts[:, 2] / 30.0)
    lat = mba.fit(pts, vals, grid=(2, 2, 2), levels=6)
    err = np.abs(mba.evaluate(lat, pts) - vals)
    assert float(err.max()) < 0.5
    assert lat[-1].ndim == 3


def test_grid_evaluation_matches_point_evaluation():
    """Оценка на сетке и по точкам обязаны давать одно и то же."""
    rng = np.random.default_rng(11)
    pts = rng.uniform(0, 50, (200, 2))
    vals = rng.normal(0, 3, 200)
    lat = mba.fit(pts, vals, grid=(2, 2), levels=4)
    ax = [np.linspace(5, 45, 9), np.linspace(5, 45, 7)]
    on_grid = mba.evaluate_grid(lat, ax)
    xx, yy = np.meshgrid(ax[0], ax[1], indexing="ij")
    by_pts = mba.evaluate(lat, np.column_stack([xx.ravel(), yy.ravel()]))
    assert np.allclose(on_grid.ravel(), by_pts)


def test_even_a_constant_needs_levels():
    """Постоянные данные воспроизводятся не сразу, а с уровнями.

    Это свойство метода, а не изъян счёта: MBA приближает, а не
    интерполирует. На одном уровне краевые коэффициенты недобирают вклад,
    и поверхность у края проседает; каждый следующий уровень подхватывает
    остаток. Отсюда практический вывод: тренд одним-двумя уровнями строить
    можно, а вот ждать от него точного попадания в замеры нельзя.
    """
    rng = np.random.default_rng(5)
    pts = rng.uniform(0, 20, (150, 2))
    vals = np.full(150, 7.5)
    errs = []
    for lv in (1, 3, 5, 8):
        lat = mba.fit(pts, vals, grid=(2, 2), levels=lv)
        errs.append(float(np.max(np.abs(mba.evaluate(lat, pts) - 7.5))))
    assert errs == sorted(errs, reverse=True), "уровни обязаны сходиться"
    assert errs[0] > 1.0, "на одном уровне отклонение заметное"
    assert errs[-1] < 0.01, "к восьмому уровню константа воспроизведена"


def test_linear_trend_is_reproduced_with_levels():
    """Линейный тренд тоже набирается уровнями, а не берётся сразу."""
    rng = np.random.default_rng(5)
    pts = rng.uniform(0, 20, (150, 2))
    vals = 3.0 + 0.5 * pts[:, 0]
    lat = mba.fit(pts, vals, grid=(2, 2), levels=8)
    assert float(np.max(np.abs(mba.evaluate(lat, pts) - vals))) < 0.01


def test_report_shows_what_each_level_added():
    """Отчёт по уровням: по нему видно, когда дробить пора прекратить."""
    rng = np.random.default_rng(2)
    pts = rng.uniform(0, 100, (300, 2))
    vals = np.sin(pts[:, 0] / 15.0) * 10
    lat = mba.fit(pts, vals, grid=(2, 2), levels=5)
    rep = mba.levels_report(lat, pts, vals)
    assert len(rep) == 5
    assert rep[0]["rms"] > rep[-1]["rms"]
    assert rep[0]["cells"] == (2, 2) and rep[-1]["cells"] == (32, 32)


def test_tolerance_stops_the_refinement():
    """Достигнутый допуск прекращает дробление раньше срока."""
    rng = np.random.default_rng(4)
    pts = rng.uniform(0, 10, (100, 2))
    vals = np.full(100, 3.0)
    loose = mba.fit(pts, vals, grid=(2, 2), levels=10, tol=0.5)
    mid = mba.fit(pts, vals, grid=(2, 2), levels=10, tol=1e-2)
    assert len(loose) == 2 and len(mid) == 5
    # предел по уровням остаётся жёстким: допуск может быть недостижим
    tight = mba.fit(pts, vals, grid=(2, 2), levels=10, tol=1e-12)
    assert len(tight) == 10


def test_degenerate_axis_does_not_break():
    """Все точки на одной линии: ось без протяжённости получает толщину."""
    pts = np.column_stack([np.linspace(0, 10, 30), np.zeros(30)])
    vals = np.linspace(0, 5, 30)
    lat = mba.fit(pts, vals, grid=(2, 2), levels=3)
    got = mba.evaluate(lat, pts)
    assert np.all(np.isfinite(got))


def test_empty_input_is_refused():
    try:
        mba.fit(np.zeros((0, 2)), np.zeros(0))
    except ValueError:
        pass
    else:
        raise AssertionError("пустой вход должен быть отказом")


def test_mismatched_lengths_are_refused():
    try:
        mba.fit(np.zeros((5, 2)), np.zeros(4))
    except ValueError:
        pass
    else:
        raise AssertionError("несовпадение длин должно быть отказом")


def test_coincident_points_keep_a_residual():
    """Два замера в одной точке с разными значениями дают среднее.

    Дробление решётки такую невязку не убирает: аппроксиматору нечего
    выбрать между двумя значениями в одной координате. Отсюда две вещи,
    которые видит пользователь: невязка застревает на ненулевом значении и
    может подрастать на промежуточных уровнях. В журнале инструмента об
    этом сказано прямо, потому что причина не в методе, а в данных - обычно
    это две скважины с одинаковыми координатами или дубли в таблице.
    """
    pts = np.array([[10.0, 10.0], [10.0, 10.0], [90.0, 90.0]])
    vals = np.array([0.0, 20.0, 5.0])
    lat = mba.fit(pts, vals, lo=[0, 0], hi=[100, 100], grid=(4, 4),
                  levels=10)
    got = mba.evaluate(lat, pts)
    assert abs(got[0] - 10.0) < 0.5 and abs(got[1] - 10.0) < 0.5
    assert abs(got[2] - 5.0) < 0.5


def test_surface_on_grid_keeps_the_axes_straight():
    """Растр обязан совпасть с прямой оценкой в тех же точках.

    Здесь легко перепутать порядок осей: данные идут как (x, y), растр
    адресуется как (строка, столбец), то есть (y, x). Перепутанные оси
    дают картинку, которая выглядит правдоподобно и неверна везде.
    Проверка идёт на НЕквадратной сетке и несимметричной функции - на
    квадратной и симметричной ошибка не видна.
    """
    rng = np.random.default_rng(1)
    pts = np.column_stack([rng.uniform(0, 5900, 300),
                           rng.uniform(0, 5000, 300)])
    vals = -97.0 + np.sin(pts[:, 0] / 900.0) * 8 + pts[:, 1] / 900.0
    lat = mba.fit(pts, vals, lo=[0, 0], hi=[5900, 5000], grid=(4, 4),
                  levels=6)

    cell = 11.8
    nx, ny = 500, 425
    gt = (0.0, cell, 0.0, 5000.0, 0.0, -cell)
    got = mba.surface_on_grid(lat, gt, nx, ny)
    assert got.shape == (ny, nx)

    for r, c in ((0, 0), (100, 300), (424, 499), (5, 480)):
        x = gt[0] + cell * (c + 0.5)
        y = gt[3] - cell * (r + 0.5)
        direct = float(mba.evaluate(lat, np.array([[x, y]]))[0])
        assert abs(float(got[r, c]) - direct) < 1e-9, (r, c)


def test_first_row_is_north():
    """Строка 0 растра - северная, как принято в GeoTIFF."""
    pts = np.array([[10.0, 90.0], [10.0, 10.0], [90.0, 90.0], [90.0, 10.0]])
    vals = np.array([100.0, 0.0, 100.0, 0.0])          # север выше юга
    lat = mba.fit(pts, vals, lo=[0, 0], hi=[100, 100], grid=(2, 2), levels=8)
    gt = (0.0, 10.0, 0.0, 100.0, 0.0, -10.0)
    got = mba.surface_on_grid(lat, gt, 10, 10)
    assert got[0].mean() > got[-1].mean()


def test_clamp_holds_the_physical_range():
    """Срез к границам: содержание не бывает отрицательным.

    Между замерами гладкая поверхность за диапазон проскакивает - на
    логнормальных данных вроде содержаний она уходит на полторы тысячи
    ниже нуля.
    """
    rng = np.random.default_rng(6)
    pts = rng.uniform(0, 1000, (200, 2))
    vals = np.clip(rng.lognormal(6.0, 1.6, 200), 0, 50000)
    lat = mba.fit(pts, vals, grid=(4, 4), levels=7)
    surf = mba.surface_on_grid(lat, (0.0, 5.0, 0.0, 1000.0, 0.0, -5.0),
                               200, 200)
    assert surf.min() < 0.0, "проверка потеряла смысл: вылета нет"

    out, rep = mba.clamp(surf, 0.0, 50000.0)
    assert out.min() >= 0.0 and out.max() <= 50000.0
    assert rep["low"] > 0 and rep["worst_low"] > 100.0
    assert rep["total"] == 200 * 200


def test_clamp_keeps_holes_as_holes():
    """NaN - это ячейка вне области, а не вылет за границу."""
    a = np.array([np.nan, -5.0, 3.0, 99.0])
    out, rep = mba.clamp(a, 0.0, 10.0)
    assert np.isnan(out[0])
    assert out[1] == 0.0 and out[3] == 10.0
    assert rep["low"] == 1 and rep["high"] == 1
    assert rep["total"] == 3, "пустые ячейки не считаются"


def test_clamp_without_bounds_changes_nothing():
    a = np.array([-5.0, 0.0, 7.0])
    out, rep = mba.clamp(a, None, None)
    assert np.array_equal(out, a)
    assert rep["low"] == 0 and rep["high"] == 0


def test_clamp_reports_how_far_it_went():
    """Величина вылета важнее числа ячеек: одно дело сотая, другое - вдвое."""
    a = np.array([-30.0, -1.0, 5.0, 120.0])
    _out, rep = mba.clamp(a, 0.0, 100.0)
    assert rep["worst_low"] == 30.0
    assert rep["worst_high"] == 20.0
