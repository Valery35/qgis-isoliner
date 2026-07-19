# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты ядра валидации рельефа:
#     python grid_isolines/tests/test_validate_core.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import validate_core as vc  # noqa: E402


def _plane(a, b, c, nx=60, ny=50, cell=10.0, x0=0.0, y0=0.0):
    """Наклонная плоскость z = a*x + b*y + c в центрах ячеек."""
    gt = (x0, cell, 0.0, y0 + ny * cell, 0.0, -cell)
    j, i = np.mgrid[0:ny, 0:nx]
    cx = x0 + (i + 0.5) * cell
    cy = y0 + ny * cell - (j + 0.5) * cell
    return a * cx + b * cy + c, gt


# --- разбиение линии -----------------------------------------------------

def test_densify_keeps_vertices():
    v = [(0.0, 0.0), (0.0, 33.0), (0.0, 100.0)]
    xs, ys = vc.densify_polyline(v, 10.0)
    for (vx, vy) in v:
        assert np.any((np.abs(xs - vx) < 1e-9) & (np.abs(ys - vy) < 1e-9)), \
            (vx, vy)


def test_densify_without_step_is_vertices_only():
    v = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    xs, ys = vc.densify_polyline(v, 0.0)
    assert len(xs) == 3, len(xs)


def test_densify_adds_points_on_long_leg():
    v = [(0.0, 0.0), (100.0, 0.0)]
    xs, _ys = vc.densify_polyline(v, 10.0)
    assert len(xs) == 11, len(xs)


def test_densify_degenerate_input():
    xs, ys = vc.densify_polyline([(5.0, 7.0)], 10.0)
    assert len(xs) == 1 and xs[0] == 5.0 and ys[0] == 7.0


# --- сечение рельефа -----------------------------------------------------

def test_contour_interval_regular():
    assert abs(vc.contour_interval([100, 105, 110, 115, 105]) - 5.0) < 1e-9


def test_contour_interval_needs_two_levels():
    assert vc.contour_interval([100.0, 100.0]) is None
    assert vc.contour_interval([]) is None


def test_contour_interval_ignores_nan():
    assert abs(vc.contour_interval([100, float("nan"), 102]) - 2.0) < 1e-9


# --- разделение на построение и проверку --------------------------------

def test_split_holds_out_every_nth_level():
    levels = list(range(100, 141, 5))          # 9 уровней
    build, check = vc.split_levels(levels, every=3)
    assert set(build) | set(check) == set(float(x) for x in levels)
    assert not (set(build) & set(check))
    assert check, "проверочный набор пуст"


def test_split_never_holds_out_edges():
    """Крайние уровни оставляем в построении: там была бы экстраполяция."""
    levels = [float(x) for x in range(0, 100, 10)]
    for every in (2, 3, 4, 5):
        build, check = vc.split_levels(levels, every=every)
        assert min(levels) in build, every
        assert max(levels) in build, every
        assert min(levels) not in check and max(levels) not in check


def test_split_is_deterministic():
    levels = [float(x) for x in range(0, 200, 5)]
    a = vc.split_levels(levels, every=4)
    b = vc.split_levels(levels, every=4)
    assert a == b


def test_split_offset_shifts_selection():
    levels = [float(x) for x in range(0, 100, 10)]
    _b0, c0 = vc.split_levels(levels, every=3, offset=0)
    _b1, c1 = vc.split_levels(levels, every=3, offset=1)
    assert c0 != c1


def test_split_empty():
    assert vc.split_levels([], every=3) == ([], [])


# --- невязка -------------------------------------------------------------

def test_residual_zero_on_exact_surface():
    arr, gt = _plane(0.02, -0.01, 50.0)
    xs = np.array([100.0, 250.0, 400.0])
    ys = np.array([100.0, 220.0, 330.0])
    z_true = 0.02 * xs - 0.01 * ys + 50.0
    res, z_dem = vc.residuals(xs, ys, z_true, arr, gt, True)
    assert np.allclose(res, 0.0, atol=1e-9), res
    assert np.allclose(z_dem, z_true, atol=1e-9)


def test_residual_sign_dem_below_contour():
    """Положительная невязка означает, что ЦМР ниже горизонтали."""
    arr, gt = _plane(0.0, 0.0, 100.0)
    res, _ = vc.residuals([100.0], [100.0], [103.0], arr, gt, True)
    assert res[0] > 0 and abs(res[0] - 3.0) < 1e-9


def test_residual_outside_grid_is_nan():
    arr, gt = _plane(0.0, 0.0, 100.0)
    res, _ = vc.residuals([-9999.0], [-9999.0], [100.0], arr, gt, True)
    assert np.isnan(res[0])


# --- статистика ----------------------------------------------------------

def test_stats_basic_numbers():
    r = np.array([-1.0, 0.0, 1.0, 2.0])
    s = vc.residual_stats(r)
    assert s["n"] == 4
    assert abs(s["mean"] - 0.5) < 1e-12
    assert abs(s["max_abs"] - 2.0) < 1e-12
    assert abs(s["rmse"] - np.sqrt(np.mean(r * r))) < 1e-12


def test_stats_ignores_nan_and_empty():
    s = vc.residual_stats([1.0, float("nan"), 3.0])
    assert s["n"] == 2
    assert vc.residual_stats([]) is None
    assert vc.residual_stats([float("nan")]) is None


def test_stats_interval_shares():
    # сечение 5, половина 2.5: два значения из четырёх выходят за половину
    r = [0.1, 0.2, 3.0, 6.0]
    s = vc.residual_stats(r, interval=5.0)
    assert abs(s["over_half"] - 0.5) < 1e-12, s["over_half"]
    assert abs(s["over_full"] - 0.25) < 1e-12, s["over_full"]


def test_stats_single_point_has_zero_std():
    s = vc.residual_stats([2.0])
    assert s["n"] == 1 and s["std"] == 0.0


# --- гистограмма и разрез по уровням ------------------------------------

def test_histogram_counts_all_points():
    r = np.random.default_rng(1).normal(0.0, 1.0, 500)
    centers, cnt = vc.histogram(r, bins=20)
    assert cnt.sum() == 500
    assert len(centers) == len(cnt) == 20


def test_histogram_constant_input():
    centers, cnt = vc.histogram([2.0, 2.0, 2.0], bins=10)
    assert cnt.sum() == 3


def test_histogram_empty():
    centers, cnt = vc.histogram([])
    assert len(centers) == 0 and len(cnt) == 0


def test_by_level_groups_and_counts():
    lv = [100, 100, 105, 105, 105]
    r = [1.0, -1.0, 2.0, 2.0, 2.0]
    rows = vc.by_level(lv, r)
    assert len(rows) == 2
    d = {row["level"]: row for row in rows}
    assert d[100.0]["n"] == 2 and abs(d[100.0]["mean"]) < 1e-12
    assert d[105.0]["n"] == 3 and abs(d[105.0]["mean"] - 2.0) < 1e-12


# --- разбор --------------------------------------------------------------

def test_verdict_clean_on_good_numbers():
    s = vc.residual_stats([0.05, -0.03, 0.02, 0.01], interval=5.0)
    assert vc.verdict(s) == ["clean"]


def test_verdict_flags_bias():
    s = vc.residual_stats([2.0, 2.1, 1.9, 2.0], interval=5.0)
    assert "bias" in vc.verdict(s)


def test_verdict_flags_overshoot():
    s = vc.residual_stats([0.0, 0.0, 4.0, 4.0], interval=5.0)
    assert "overshoot" in vc.verdict(s)


def test_verdict_flags_holdout_gap():
    """Главный сюжет блока: вход воспроизводится хорошо, отложенное - плохо."""
    s_in = vc.residual_stats([0.01, -0.01, 0.02], interval=5.0)
    s_out = vc.residual_stats([1.5, -1.4, 1.6], interval=5.0)
    assert "holdout_gap" in vc.verdict(s_in, s_out)


def test_verdict_no_stats():
    assert vc.verdict(None) == []


# --- сквозной сюжет ------------------------------------------------------

def test_holdout_is_harder_than_input_reproduction():
    """Плоскость ЦМР против горизонталей синусоидального рельефа.

    Проверяем не абсолютные числа, а само утверждение, ради которого блок
    сделан: невязка на отложенных уровнях больше, чем на тех, что подавались.
    """
    nx = ny = 80
    cell = 10.0
    gt = (0.0, cell, 0.0, ny * cell, 0.0, -cell)
    j, i = np.mgrid[0:ny, 0:nx]
    cx = (i + 0.5) * cell
    cy = ny * cell - (j + 0.5) * cell
    # «истинный» рельеф и заведомо сглаженная ЦМР от него
    true_z = 100.0 + 0.05 * cx + 8.0 * np.sin(cy / 90.0)
    dem = 100.0 + 0.05 * cx          # ЦМР потеряла волну целиком

    rng = np.random.default_rng(7)
    xs = rng.uniform(50.0, nx * cell - 50.0, 400)
    ys = rng.uniform(50.0, ny * cell - 50.0, 400)
    z_true = 100.0 + 0.05 * xs + 8.0 * np.sin(ys / 90.0)
    res, _ = vc.residuals(xs, ys, z_true, dem, gt, True)
    s = vc.residual_stats(res, interval=5.0)
    assert s["n"] == 400
    # потерянная волна обязана вылезти в разбросе и в доле промахов
    assert s["std"] > 1.0, s["std"]
    assert s["over_half"] > 0.3, s["over_half"]
    assert "spread" in vc.verdict(s)
    assert true_z.shape == dem.shape


# --- террасинг -----------------------------------------------------------

def _cone(n=120, cell=10.0, slope=0.06, top=200.0):
    y, x = np.mgrid[0:n, 0:n]
    r = np.hypot(x - n / 2.0, y - n / 2.0) * cell
    return top - slope * r


def test_curvature_zero_on_plane():
    z = _cone()[:1, :1]  # заглушка формы не годится, берём настоящую плоскость
    n = 60
    j, i = np.mgrid[0:n, 0:n]
    z = 100.0 + 0.05 * i * 10.0 + 0.02 * j * 10.0
    kv = vc.profile_curvature(z, 10.0)
    inner = kv[2:-2, 2:-2]
    # Порог под float32: расчёт ведётся в одинарной точности ради памяти, на
    # рабочих матрицах это разница между 1.5 ГБ и парой сотен мегабайт.
    # Кривизна измеряется в обратных метрах, и 1e-6 это ноль по любому
    # физическому смыслу.
    assert np.nanmax(np.abs(inner)) < 1e-6, np.nanmax(np.abs(inner))


def test_curvature_edges_are_nan():
    kv = vc.profile_curvature(_cone(), 10.0)
    assert np.all(np.isnan(kv[0, :])) and np.all(np.isnan(kv[-1, :]))
    assert np.all(np.isnan(kv[:, 0])) and np.all(np.isnan(kv[:, -1]))


def test_curvature_flat_area_is_nan():
    z = np.full((40, 40), 50.0)
    kv = vc.profile_curvature(z, 10.0)
    assert np.all(np.isnan(kv)), "на плоскости кривизна вдоль склона не определена"


def test_curvature_spikes_on_terraced_surface():
    z = _cone()
    terr = np.round(z / 5.0) * 5.0
    a = vc.terracing_stats(z, 10.0, 5.0)
    b = vc.terracing_stats(terr, 10.0, 5.0)
    assert b["curv_p95_abs"] > a["curv_p95_abs"], (a["curv_p95_abs"],
                                                   b["curv_p95_abs"])


def test_level_attraction_smooth_is_one():
    z = _cone()
    share, expect, ratio, skipped = vc.level_attraction(z, 5.0)
    assert abs(expect - 0.2) < 1e-12
    assert 0.8 < ratio < 1.3, ratio
    assert skipped == 0.0, "без cell плоские не исключаются"


def test_level_attraction_terraced_is_high():
    z = np.round(_cone() / 5.0) * 5.0
    _s, _e, ratio, _sk = vc.level_attraction(z, 5.0)
    assert ratio > 3.0, ratio


def test_flat_area_skews_index_and_mask_fixes_it():
    """Главная находка на матрице заказчика.

    Половина площади - водная гладь с околонулевым уклоном. Без исключения
    таких ячеек индекс притяжения уезжает вниз и показывает ложное
    благополучие, потому что вся масса отметок стоит в одной фазе.
    """
    cone = _cone(n=160, cell=10.0)
    z = cone.copy()
    # заливаем половину площади «водой» на постоянной отметке с шумом в мм
    rng = np.random.default_rng(0)
    water = np.zeros(z.shape, dtype=bool)
    water[:80, :] = True
    z[water] = 399.78 + rng.normal(0.0, 0.004, int(water.sum()))

    _s, _e, r_naive, sk0 = vc.level_attraction(z, 0.5)
    _s, _e, r_fixed, sk1 = vc.level_attraction(z, 0.5, cell=10.0)
    _s, _e, r_land, _sk = vc.level_attraction(cone[80:, :], 0.5, cell=10.0)
    assert sk0 == 0.0
    assert sk1 > 0.3, sk1
    assert abs(r_fixed - r_land) < abs(r_naive - r_land), (r_naive, r_fixed,
                                                           r_land)


def test_flat_mask_threshold_is_fraction_of_interval():
    """Порог задаётся долей сечения, а не абсолютным уклоном."""
    n = 60
    j, i = np.mgrid[0:n, 0:n]
    z = 100.0 + 0.0001 * i * 5.0        # перепад 0.5 мм на ячейку
    flat = vc.flat_mask(z, 5.0, 0.5, 0.01)   # порог 5 мм
    assert flat[2:-2, 2:-2].all()
    steep = 100.0 + 0.02 * i * 5.0      # перепад 100 мм на ячейку
    assert not vc.flat_mask(steep, 5.0, 0.5, 0.01)[2:-2, 2:-2].any()


def test_flat_mask_off_when_disabled():
    z = np.full((30, 30), 50.0)
    assert not vc.flat_mask(z, 5.0, 0.5, 0.0).any()


def test_level_attraction_keeps_stats_when_almost_all_flat():
    """Если после отсева осталась горстка ячеек, отсев не применяем."""
    z = np.full((20, 20), 100.0)
    res = vc.level_attraction(z, 0.5, cell=5.0)
    assert res is not None
    assert res[3] == 0.0


def test_level_attraction_needs_interval():
    assert vc.level_attraction(_cone(), 0.0) is None
    assert vc.level_attraction([], 5.0) is None


def test_phase_histogram_flat_for_smooth():
    centers, cnt = vc.phase_histogram(_cone(), 5.0, bins=20)
    assert cnt.sum() > 0
    # у гладкого конуса ни один столбец не должен доминировать втрое
    assert cnt.max() < 3.0 * np.mean(cnt), (cnt.max(), np.mean(cnt))


def test_phase_histogram_peaks_for_terraced():
    z = np.round(_cone() / 5.0) * 5.0
    centers, cnt = vc.phase_histogram(z, 5.0, bins=20)
    peak = int(np.argmax(cnt))
    assert abs(centers[peak]) < 0.1, centers[peak]


def test_terracing_verdict_levels():
    assert vc.terracing_verdict({"attract_ratio": 1.0}) == ["clean"]
    assert vc.terracing_verdict({"attract_ratio": 1.7}) == ["suspect"]
    assert vc.terracing_verdict({"attract_ratio": 4.0}) == ["terraced"]
    assert vc.terracing_verdict({}) == ["unknown"]


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
