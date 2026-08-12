# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Smoke-тесты движка кригинга kb2d. Движок не зависит от QGIS, поэтому тесты
# запускаются без QGIS:
#     python -m pytest grid_isolines/tests/         (если есть pytest)
#     python grid_isolines/tests/test_kb2d.py       (запуск напрямую)
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402


def _grid(xs, ys, vs, cell=10.0, ktype=1, nugget=0.0, rad=1e18):
    vg = kb2d.Variogram(nugget, [{"it": 1, "cc": 1.0, "aa": 15.0,
                                   "ang": 0.0, "anis": 1.0}])
    xmin, ymin = float(xs.min()), float(ys.min())
    w, h = float(xs.max()) - xmin, float(ys.max()) - ymin
    nx = max(int(math.ceil(w / cell)), 1) + 1
    ny = max(int(math.ceil(h / cell)), 1) + 1
    return kb2d.build_grid(xs, ys, vs, vg, ktype, 0.0, 1, 24, rad, -9999.0,
                           xmin, ymin, cell, nx, ny)


def test_exact_reproduction_at_nodes():
    """Ординарный кригинг (наггет 0) точно воспроизводит значения в узлах."""
    xs = np.array([0., 10., 0., 10.]); ys = np.array([0., 0., 10., 10.])
    vs = np.array([1., 2., 3., 4.])
    g = _grid(xs, ys, vs, cell=10.0)
    assert np.allclose(g, [[3., 4.], [1., 2.]], atol=1e-6), g


def test_no_overshoot_on_smooth_field():
    """Оценка не выходит за диапазон данных на гладком наборе."""
    rng = np.random.default_rng(0)
    xs = rng.uniform(0, 100, 60); ys = rng.uniform(0, 100, 60)
    vs = 0.05 * xs + 0.03 * ys
    g = _grid(xs, ys, vs, cell=5.0)
    v = g[g != -9999.0]
    assert v.min() >= vs.min() - 1e-6
    assert v.max() <= vs.max() + 1e-6


def test_nodata_when_too_far():
    """Если в радиусе поиска нет ни одной точки - узел остаётся nodata."""
    xs = np.array([0., 1.]); ys = np.array([0., 1.]); vs = np.array([5., 6.])
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 5.0,
                               "ang": 0.0, "anis": 1.0}])
    # узел в (1000,1000), радиус поиска маленький -> нет соседей -> nodata
    g = kb2d.build_grid(xs, ys, vs, vg, 1, 0.0, 1, 24, 10.0 * 10.0, -9999.0,
                        1000.0, 1000.0, 10.0, 1, 1)
    assert g[0, 0] == -9999.0


def test_duplicate_points_are_handled():
    """Совпадающие точки усредняются заранее (в algorithms); движок не должен
    падать на близких точках и давать конечный результат."""
    xs = np.array([0., 0., 20.]); ys = np.array([0., 0., 0.])
    vs = np.array([10., 20., 30.])  # дубль (0,0) усреднён до 15 вызывающим кодом
    xs2 = np.array([0., 20.]); ys2 = np.array([0., 0.]); vs2 = np.array([15., 30.])
    g = _grid(xs2, ys2, vs2, cell=10.0)
    assert np.isfinite(g[g != -9999.0]).all()


def test_variogram_spherical_monotonic():
    """Сферическая ковариация убывает с расстоянием и зануляется за радиусом."""
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 10.0,
                               "ang": 0.0, "anis": 1.0}])
    c0 = vg.cova2(0.0, 0.0)
    c5 = vg.cova2(5.0, 0.0)
    c10 = vg.cova2(10.0, 0.0)
    c20 = vg.cova2(20.0, 0.0)
    assert c0 > c5 > c10
    assert abs(c20) < 1e-9          # за радиусом корреляции - ноль


def test_variance_zero_at_node_and_nonneg():
    """Дисперсия кригинга = 0 в узле-пробе и неотрицательна везде."""
    rng = np.random.default_rng(1)
    xs = rng.uniform(0, 100, 40); ys = rng.uniform(0, 100, 40)
    vs = 0.04 * xs - 0.02 * ys
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 30.0,
                               "ang": 0.0, "anis": 1.0}])
    # узел точно на первой пробе -> дисперсия 0
    e, v = kb2d.krige_point(xs[0], ys[0], xs, ys, vs, vg, 1, 0.0,
                            1, 24, 1e18, -9999.0, return_var=True)
    assert abs(v) < 1e-9 and abs(e - vs[0]) < 1e-6
    # по всей сетке дисперсия (через стд.ошибку) неотрицательна
    g, se = kb2d.build_grid(xs, ys, vs, vg, 1, 0.0, 1, 24, 1e18, -9999.0,
                            0.0, 0.0, 5.0, 21, 21, with_variance=True)
    sev = se[se != -9999.0]
    assert (sev >= 0).all()


def test_stderr_grows_away_from_data():
    """Стандартная ошибка дальше от данных не меньше, чем вблизи."""
    xs = np.array([40., 50., 60., 50.]); ys = np.array([50., 40., 50., 60.])
    vs = np.array([1., 2., 3., 2.])
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 20.0,
                               "ang": 0.0, "anis": 1.0}])
    se_near = kb2d.krige_point(50., 50., xs, ys, vs, vg, 1, 0.0,
                               1, 24, 1e18, -9999.0, return_var=True)[1]
    se_far = kb2d.krige_point(50., 120., xs, ys, vs, vg, 1, 0.0,
                              1, 24, 1e18, -9999.0, return_var=True)[1]
    assert se_far >= se_near


def test_clip_outliers_cut_and_cap():
    """Отсев удаляет точки вне границ; срезка прижимает к границе."""
    v = np.array([-35.0, 1.0, 2.0, 3.0, 4.0, 122.0])
    # отсев по абсолютным границам [0; 30]
    out, keep, lo, hi = kb2d.clip_outliers(v, vmin=0.0, vmax=30.0, cap=False)
    assert lo == 0.0 and hi == 30.0
    assert keep.tolist() == [False, True, True, True, True, False]
    # срезка к [0; 30]
    out, keep, lo, hi = kb2d.clip_outliers(v, vmin=0.0, vmax=30.0, cap=True)
    assert keep.all()
    assert out.min() == 0.0 and out.max() == 30.0
    # без фильтра - ничего не меняется
    out, keep, lo, hi = kb2d.clip_outliers(v)
    assert keep.all() and np.array_equal(out, v)
    assert lo == float("-inf") and hi == float("inf")


def test_clip_outliers_percentile():
    """Перцентильная обрезка симметрична: pct=10 -> [p10; p90]."""
    v = np.arange(0.0, 101.0)               # 0..100
    out, keep, lo, hi = kb2d.clip_outliers(v, pct=10.0, cap=False)
    assert abs(lo - 10.0) < 1e-6 and abs(hi - 90.0) < 1e-6
    assert keep.sum() == 81                 # 10..90 включительно


def test_cross_validation_loo():
    """LOO на гладком поле: ошибки малы, дисперсии положительны, длины верны."""
    rng = np.random.default_rng(3)
    xs = rng.uniform(0, 100, 50); ys = rng.uniform(0, 100, 50)
    vs = 0.05 * xs + 0.02 * ys
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 40.0,
                               "ang": 0.0, "anis": 1.0}])
    est, var = kb2d.cross_validate(xs, ys, vs, vg, 1, 0.0, 1, 16,
                                   1e18, -9999.0)
    assert len(est) == 50 and len(var) == 50
    ok = est != -9999.0
    assert ok.sum() >= 40
    err = est[ok] - vs[ok]
    rmse = float(np.sqrt(np.mean(err ** 2)))
    assert rmse < 5.0                        # на гладком тренде ошибка мала
    assert (var[ok] >= 0).all()


def test_experimental_variogram_basic():
    """Экспериментальная вариограмма растёт от малых лагов и даёт число пар."""
    rng = np.random.default_rng(5)
    n = 400
    xs = rng.uniform(0, 500, n); ys = rng.uniform(0, 500, n)
    vs = 8.0 * np.sin(xs / 90.0) + 6.0 * np.cos(ys / 80.0)
    ev = kb2d.experimental_variogram(xs, ys, vs, n_lags=12, maxlag=250)
    assert len(ev["lag"]) == len(ev["gamma"]) == len(ev["npairs"])
    assert (ev["npairs"] > 0).all()
    assert np.all(np.isfinite(ev["gamma"]))
    # на структурированном поле ближний лаг меньше дальнего
    assert ev["gamma"][0] < ev["gamma"][-1]


def test_experimental_variogram_subsample():
    """При большом числе точек включается подвыборка по лимиту пар."""
    rng = np.random.default_rng(1)
    n = 3000
    xs = rng.uniform(0, 1000, n); ys = rng.uniform(0, 1000, n)
    vs = rng.normal(0, 1, n)
    ev = kb2d.experimental_variogram(xs, ys, vs, n_lags=10, maxlag=500,
                                     max_pairs=1_000_000)
    assert ev["subsampled"] and ev["n_used"] < n


def test_fit_variogram_recovers_structure():
    """Подбор на поле с реальным плато: силл ~ дисперсии, радиус положителен."""
    rng = np.random.default_rng(5)
    G, w = 140, 12
    white = rng.normal(0, 1, (G, G))
    ker = np.ones((w, w)) / (w * w)
    fld = np.fft.ifft2(np.fft.fft2(white) *
                       np.fft.fft2(ker, s=white.shape)).real
    fld = (fld - fld.mean()) / fld.std() * 5.0
    n = 600
    ix = rng.integers(0, G, n); iy = rng.integers(0, G, n)
    xs = ix * 10.0; ys = iy * 10.0
    vs = fld[iy, ix] + rng.normal(0, 0.8, n)
    ev = kb2d.experimental_variogram(xs, ys, vs, n_lags=18, maxlag=700)
    f = kb2d.fit_variogram(ev["lag"], ev["gamma"], ev["npairs"], model="auto")
    assert f is not None
    assert f["nugget"] >= 0 and f["sill"] > 0 and f["range"] > 0
    assert f["r2"] > 0.5
    # плато не превышает наблюдённую вариограмму (защита от «убегания» силла)
    assert f["nugget"] + f["sill"] <= 1.16 * float(ev["gamma"].max())


def test_fit_sill_cap_holds():
    """Силл-плато не превышает заданный cap даже на «убегающем» гладком поле."""
    rng = np.random.default_rng(11)
    n = 400
    xs = rng.uniform(0, 1000, n); ys = rng.uniform(0, 1000, n)
    vs = 12.0 * np.sin(xs / 200.0) + 9.0 * np.cos(ys / 160.0)
    ev = kb2d.experimental_variogram(xs, ys, vs, n_lags=16, maxlag=600)
    cap = 1.15 * float(ev["gamma"].max())
    f = kb2d.fit_variogram(ev["lag"], ev["gamma"], ev["npairs"], model="auto")
    assert f["nugget"] + f["sill"] <= cap + 1e-6


def test_model_curve_zero_at_origin():
    """Кривая γ(h) модели начинается с нуля; анизотропия даёт вторую ветвь."""
    vg = kb2d.Variogram(1.5, [{"it": 1, "cc": 10.0, "aa": 100.0,
                               "ang": 0.0, "anis": 1.0}])
    out = kb2d.model_curve(vg, 300)
    assert len(out) == 2 and abs(out[1][0]) < 1e-6
    vg2 = kb2d.Variogram(1.5, [{"it": 1, "cc": 10.0, "aa": 100.0,
                                "ang": 30.0, "anis": 0.4}])
    assert len(kb2d.model_curve(vg2, 300)) == 3


def test_variogram_map_symmetry_center():
    """Вариограммная карта точечно-симметрична γ(h)=γ(−h), центр = 0."""
    import numpy as np
    rng = np.random.default_rng(0)
    n = 200
    xs = rng.uniform(0, 1000, n)
    ys = rng.uniform(0, 1000, n)
    m = kb2d.variogram_map(xs, ys, xs.copy(), n_bins=12, min_pairs=2)
    g = m["grid"]
    c = g.shape[0] // 2
    assert abs(g[c, c]) < 1e-9                      # центр = лаг 0
    diff = np.abs(g - g[::-1, ::-1])
    assert np.nanmax(diff) < 1e-6                    # симметрия


def test_variogram_map_anisotropy_axis():
    """Тренд по x -> главная ось непрерывности С-Ю (азимут ~0/180)."""
    import numpy as np
    rng = np.random.default_rng(1)
    n = 400
    xs = rng.uniform(0, 1000, n)
    ys = rng.uniform(0, 1000, n)
    m = kb2d.variogram_map(xs, ys, xs.copy(), n_bins=15, min_pairs=3)
    assert m["resolved"]
    az = m["azimuth"] % 180.0
    assert az < 20.0 or az > 160.0
    assert m["anis"] < 0.9


def test_variogram_map_noise_unresolved():
    """Чистый шум: структура не разрешается -> resolved=False, anis=1."""
    import numpy as np
    rng = np.random.default_rng(2)
    n = 400
    xs = rng.uniform(0, 1000, n)
    ys = rng.uniform(0, 1000, n)
    m = kb2d.variogram_map(xs, ys, rng.normal(0, 1, n), n_bins=15, min_pairs=3)
    assert m["resolved"] is False
    assert abs(m["anis"] - 1.0) < 1e-9


def test_variogram_map_range_capped():
    """Длинный главный радиус (больше окна) -> range_capped=True, оценка нижняя.
    Короткий радиус (структура внутри окна) -> range_capped=False."""
    import numpy as np
    import math
    # Сильно анизотропное поле: непрерывность вдоль 145°, ранг больше окна.
    rng = np.random.default_rng(3)
    n = 300
    xs = rng.uniform(0, 12000, n)
    ys = rng.uniform(0, 12000, n)
    a = math.radians(145.0)
    ux, uy = math.sin(a), math.cos(a)
    px, py = math.cos(a), -math.sin(a)
    u = xs * ux + ys * uy
    p = xs * px + ys * py
    vs = 0.0008 * u + 0.02 * p + rng.normal(0, 2, n)
    m = kb2d.variogram_map(xs, ys, vs, n_bins=15, min_pairs=3)
    assert m["resolved"]
    assert m["range_capped"] is True
    assert abs(m["range_major"] - m["maxlag"]) < 1e-6     # упёрся в окно

    # Короткая изотропная структура (FFT-сглаженный шум): ранг внутри окна,
    # γ выходит на полку -> не capped.
    rng2 = np.random.default_rng(5)
    G, w = 160, 16
    white = rng2.normal(0, 1, (G, G))
    ker = np.ones((w, w)) / (w * w)
    fld = np.fft.ifft2(np.fft.fft2(white) *
                       np.fft.fft2(ker, s=white.shape)).real
    fld = (fld - fld.mean()) / fld.std() * 5.0
    n2 = 800
    ix = rng2.integers(0, G, n2)
    iy = rng2.integers(0, G, n2)
    xs2 = ix * 10.0
    ys2 = iy * 10.0
    vs2 = fld[iy, ix] + rng2.normal(0, 0.5, n2)
    m2 = kb2d.variogram_map(xs2, ys2, vs2, n_bins=15, min_pairs=3)
    assert m2["resolved"]
    assert m2["range_capped"] is False
    assert m2["range_major"] < m2["maxlag"]


def test_data_warnings_few_points():
    w = dict(kb2d.data_warnings([0, 1, 2], [0, 1, 2], [5, 6, 7], min_points=8))
    assert w.get("few_points") == 3


def test_data_warnings_duplicates():
    xs = [0.0, 0.0, 1.0, 1.0, 1.0]
    ys = [0.0, 0.0, 1.0, 1.0, 1.0]
    w = dict(kb2d.data_warnings(xs, ys, [1, 2, 3, 4, 5], min_points=1))
    assert w.get("duplicates") == 3          # 5 точек, 2 уникальные позиции


def test_data_warnings_constant_and_clean():
    n = 20
    xs = list(range(n))
    cw = dict(kb2d.data_warnings(xs, xs, [3.0] * n))
    assert "constant" in cw
    clean = kb2d.data_warnings(xs, xs, [float(i) for i in range(n)])
    assert clean == []                       # разные координаты и значения


def test_block_offsets_cover_cell():
    """Точки дискретизации блока симметричны и лежат внутри ячейки."""
    bx, by = kb2d.block_offsets(20.0, 4, 4)
    assert len(bx) == 16
    assert abs(bx.mean()) < 1e-9 and abs(by.mean()) < 1e-9   # центрированы
    assert bx.max() < 10.0 and bx.min() > -10.0              # внутри ячейки
    assert (bx.max() - bx.min()) < 20.0


def test_block_block_cov_below_point():
    """Блок-блок ковариация Cbb положительна и ниже точечной C(0); 1×1 = C(0)."""
    vg = kb2d.Variogram(0.5, [{"it": 1, "cc": 1.0, "aa": 50.0,
                               "ang": 0.0, "anis": 1.0}])
    bx, by = kb2d.block_offsets(20.0, 4, 4)
    cbb = kb2d.block_block_cov(vg, bx, by)
    assert 0.0 < cbb < vg.maxcov
    bx1, by1 = kb2d.block_offsets(20.0, 1, 1)        # блок 1×1 -> точка
    assert abs(kb2d.block_block_cov(vg, bx1, by1) - vg.maxcov) < 1e-12


def test_block_disc1_equals_point():
    """build_grid с ndisc=1 байт-в-байт совпадает с точечным (по умолчанию)."""
    rng = np.random.default_rng(7)
    xs = rng.uniform(0, 100, 40); ys = rng.uniform(0, 100, 40)
    vs = 0.05 * xs + 0.03 * ys
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 40.0,
                               "ang": 0.0, "anis": 1.0}])
    g_def = kb2d.build_grid(xs, ys, vs, vg, 1, 0.0, 1, 24, 1e18, -9999.0,
                            0.0, 0.0, 5.0, 21, 21)
    g_d1 = kb2d.build_grid(xs, ys, vs, vg, 1, 0.0, 1, 24, 1e18, -9999.0,
                           0.0, 0.0, 5.0, 21, 21, ndisc=1)
    assert np.array_equal(g_def, g_d1)


def test_block_kriging_lowers_variance_far():
    """Вдали от данных блочный кригинг даёт меньшую дисперсию, чем точечный,
    а оценка остаётся близкой (блок усредняет ту же поверхность)."""
    xs = np.array([0., 100., 0., 100.]); ys = np.array([0., 0., 100., 100.])
    vs = np.array([1., 2., 3., 4.])
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 200.0,
                               "ang": 0.0, "anis": 1.0}])
    e_p, v_p = kb2d._solve_point(50., 50., xs, ys, vs, vg, 1, 0.0,
                                 1, 24, 1e18, -9999.0)
    bdx, bdy = kb2d.block_offsets(20.0, 4, 4)
    cbb = kb2d.block_block_cov(vg, bdx, bdy)
    e_b, v_b = kb2d._solve_point(50., 50., xs, ys, vs, vg, 1, 0.0,
                                 1, 24, 1e18, -9999.0,
                                 bdx=bdx, bdy=bdy, cbb=cbb)
    assert v_b < v_p                          # блок: дисперсия ниже точечной
    assert abs(e_b - e_p) < 0.5               # оценки близки


def test_block_kriging_no_overshoot():
    """Блочная оценка по сетке не выходит за диапазон данных и неотрицательна
    по стандартной ошибке."""
    rng = np.random.default_rng(9)
    xs = rng.uniform(0, 100, 60); ys = rng.uniform(0, 100, 60)
    vs = 0.04 * xs + 0.02 * ys
    vg = kb2d.Variogram(0.0, [{"it": 1, "cc": 1.0, "aa": 50.0,
                               "ang": 0.0, "anis": 1.0}])
    g, se = kb2d.build_grid(xs, ys, vs, vg, 1, 0.0, 1, 24, 1e18, -9999.0,
                            0.0, 0.0, 5.0, 21, 21, with_variance=True, ndisc=4)
    v = g[g != -9999.0]
    assert v.min() >= vs.min() - 1e-5
    assert v.max() <= vs.max() + 1e-5
    sev = se[se != -9999.0]
    assert (sev >= 0).all()


def test_categorical_indicator_grids():
    import numpy as _np
    rng = _np.random.default_rng(0)

    def blob(cx, cy, n, lab):
        return cx + rng.normal(0, 80, n), cy + rng.normal(0, 80, n), [lab] * n
    xa, ya, la = blob(200, 200, 60, "A")
    xb, yb, lb = blob(800, 250, 60, "B")
    xc, yc, lc = blob(500, 750, 60, "C")
    xd = _np.concatenate([xa, xb, xc])
    yd = _np.concatenate([ya, yb, yc])
    lab = _np.array(la + lb + lc, dtype=object)
    classes = ["A", "B", "C"]
    probs, zone, conf = kb2d.categorical_indicator_grids(
        xd, yd, lab, classes, xmn=0, ymn=0, cell=25.0, nx=40, ny=40,
        ndmin=2, ndmax=16)
    assert probs.shape == (40, 40, 3) and zone.shape == (40, 40)

    def zone_at(x, y):                       # build_grid: row 0 = север (верх)
        row = zone.shape[0] - 1 - int(round(y / 25.0))
        col = int(round(x / 25.0))
        row = min(max(row, 0), zone.shape[0] - 1)
        col = min(max(col, 0), zone.shape[1] - 1)
        return classes[zone[row, col]] if zone[row, col] >= 0 else None

    assert zone_at(200, 200) == "A"          # центр каждого кластера -> свой класс
    assert zone_at(800, 250) == "B"
    assert zone_at(500, 750) == "C"
    valid = zone >= 0
    ssum = _np.clip(probs, 0, 1).sum(2)[valid]
    assert _np.allclose(ssum, 1.0, atol=1e-5)   # нормировка к единице
    cv = conf[valid]
    assert cv.min() >= 0.0 and cv.max() <= 1.0  # уверенность в [0,1]


def test_rescale_nugget_keeps_total_variance():
    """Перенос доли самородка не трогает общую дисперсию и радиусы."""
    from kb2d import Variogram, rescale_nugget
    vg = Variogram(0.4, [{"it": 1, "cc": 0.6, "aa": 300.0,
                          "ang": 0.0, "anis": 1.0}])
    for frac in (0.0, 0.25, 1.0):
        out = rescale_nugget(vg, frac)
        assert abs((out.c0 + sum(out.cc)) - 1.0) < 1e-9
        assert abs(out.c0 - frac) < 1e-9
        assert out.aa == vg.aa
    assert rescale_nugget(vg, None) is vg


def test_zero_nugget_holds_value_next_to_the_borehole():
    """Самородок роняет оценку в шаге от скважины, ноль её удерживает.

    Ровно в точке замера кригинг точен при любом самородке, но ячейка почти
    никогда не садится на устье. В пяти метрах от скважины подобранный
    самородок 0.5 уводит вероятность под порог 0.5, а обнулённый оставляет её
    при своём классе. Это и есть механизм, из-за которого скважина «В»
    оказывалась вне своей зоны.
    """
    from kb2d import Variogram, build_grid, rescale_nugget
    xd = np.array([100.0, 300.0, 500.0, 300.0])
    yd = np.array([100.0, 100.0, 300.0, 500.0])
    zd = np.array([1.0, 0.0, 0.0, 0.0])            # индикатор 0/1
    base = Variogram(0.5, [{"it": 1, "cc": 0.5, "aa": 400.0,
                            "ang": 0.0, "anis": 1.0}])

    def near_first_point(vg):
        # узел грида в 5 м от скважины, строка 30 отвечает нижнему краю
        g = build_grid(xd, yd, zd, vg, 0, 0.0, 1, 8, 1e12, -9999.0,
                       105.0, 105.0, 20.0, 31, 31)
        return float(g[30, 0])

    def at_first_point(vg):
        g = build_grid(xd, yd, zd, vg, 0, 0.0, 1, 8, 1e12, -9999.0,
                       100.0, 100.0, 20.0, 31, 31)
        return float(g[30, 0])

    assert abs(at_first_point(base) - 1.0) < 1e-6          # в самой точке точен
    assert near_first_point(base) < 0.5                    # рядом провалился
    assert near_first_point(rescale_nugget(base, 0.0)) > 0.9


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()


def test_covariance_vector_form_matches_the_scalar_one():
    """Векторная ковариация совпадает со скалярной до машинного нуля.

    Матрица системы кригинга строится разом через cova2_array, а не
    двойным циклом с вызовом cova2 на каждую пару: при двадцати четырёх
    соседях это было 276 питоновских вызовов на каждую ячейку сетки, и
    именно они съедали время. Ускорение имеет смысл только при точном
    совпадении, поэтому оно закреплено тестом.
    """
    rng = np.random.default_rng(0)
    worst = 0.0
    for it in (1, 2, 3):
        for anis in (1.0, 0.4, 2.5):
            for ang in (0.0, 35.0, 120.0):
                vg = kb2d.Variogram(1.7, [dict(it=it, cc=12.0, aa=800.0,
                                               ang=ang, anis=anis)])
                dx = rng.uniform(-3000.0, 3000.0, 200)
                dy = rng.uniform(-3000.0, 3000.0, 200)
                a = vg.cova2_array(dx, dy)
                b = np.array([vg.cova2(float(x), float(y))
                              for x, y in zip(dx, dy)])
                worst = max(worst, float(np.abs(a - b).max()))
    assert worst < 1e-12, "векторная ковариация разошлась: %.3g" % worst


def test_nested_structures_match_too():
    """Вложенные структуры считаются одинаково обеими формами."""
    vg = kb2d.Variogram(0.5, [dict(it=1, cc=8.0, aa=500.0, ang=20.0, anis=0.6),
                              dict(it=2, cc=4.0, aa=2000.0, ang=70.0,
                                   anis=1.5)])
    rng = np.random.default_rng(1)
    dx = rng.uniform(-4000.0, 4000.0, 300)
    dy = rng.uniform(-4000.0, 4000.0, 300)
    a = vg.cova2_array(dx, dy)
    b = np.array([vg.cova2(float(x), float(y)) for x, y in zip(dx, dy)])
    assert float(np.abs(a - b).max()) < 1e-12


def test_nearest_selection_matches_a_full_sort():
    """Отбор ближайших через argpartition даёт тот же результат.

    Полная сортировка выборки для каждой ячейки не нужна: берётся ndmax
    ближайших. На густой сети это удваивало время.
    """
    rng = np.random.default_rng(2)
    for npts in (30, 300, 3000):
        h2 = rng.uniform(0.0, 1e6, npts)
        for ndmax in (4, 12, 24):
            full = np.argsort(h2)[:ndmax]
            cut = np.argpartition(h2, min(ndmax, npts - 1))[:ndmax]
            part = cut[np.argsort(h2[cut])]
            assert np.allclose(h2[full], h2[part]), (
                "отбор ближайших разошёлся при %d замерах" % npts)


def test_grid_matches_a_direct_solve_cell_by_cell():
    """Грид совпадает с прямым пересчётом ячейки, в пределах float32.

    Сторож на всю связку ускорений: векторизация системы и отбор
    ближайших не должны менять ни одной оценки. Допуск взят по шагу
    float32, в котором хранится грид.
    """
    rng = np.random.default_rng(5)
    ext = 2000.0
    xd = rng.uniform(0.0, ext, 200)
    yd = rng.uniform(0.0, ext, 200)
    vd = rng.normal(100.0, 7.0, 200)
    vg = kb2d.Variogram(1.0, [dict(it=1, cc=15.0, aa=900.0, ang=0.0,
                                   anis=1.0)])
    nx = ny = 40
    cell = ext / nx
    rad2 = (3.0 * ext) ** 2
    for ktype in (0, 1):
        for ndmax in (4, 12, 24):
            grid = kb2d.build_grid(xd, yd, vd, vg=vg, ktype=ktype,
                                   skmean=100.0, ndmin=1, ndmax=ndmax,
                                   rad2=rad2, nodata=-9999.0, xmn=0.0,
                                   ymn=0.0, cell=cell, nx=nx, ny=ny)
            for r, c in ((3, 7), (20, 20), (35, 11)):
                xloc = c * cell
                yloc = (ny - r - 1) * cell
                want, _ = kb2d._solve_point(xloc, yloc, xd, yd, vd, vg, ktype,
                                            100.0, 1, ndmax, rad2, -9999.0)
                assert abs(float(grid[r, c]) - float(want)) < 1e-4, (
                    "ячейка (%d, %d) разошлась с прямым пересчётом" % (r, c))


def test_cell_mask_skips_cells_without_changing_values():
    """Маска расчёта не меняет ни одной оценки внутри себя.

    Ячейка вне маски обрезки всё равно уходит в nodata, поэтому считать
    её незачем. Ускорение имеет смысл только при полном совпадении
    значений: иначе это другой результат, а не тот же быстрее.
    """
    rng = np.random.default_rng(0)
    vg = kb2d.Variogram(2.0, [dict(it=1, cc=20.0, aa=3000.0, ang=0.0,
                                   anis=1.0)])
    nx = ny = 40
    cell = 100.0
    ext = nx * cell
    xd = rng.uniform(0.0, ext, 120)
    yd = rng.uniform(0.0, ext, 120)
    vd = rng.normal(100.0, 5.0, 120)
    kw = dict(vg=vg, ktype=1, skmean=0.0, ndmin=1, ndmax=16,
              rad2=(3 * ext) ** 2, nodata=-9999.0, xmn=0.0, ymn=0.0,
              cell=cell, nx=nx, ny=ny)
    full = kb2d.build_grid(xd, yd, vd, **kw)
    rows = np.arange(ny)[:, None]
    cols = np.arange(nx)[None, :]
    band = np.abs((ny - 1 - rows) - cols) < 8
    part = kb2d.build_grid(xd, yd, vd, cell_mask=band, **kw)
    assert np.array_equal(full[band], part[band]), "значения внутри разошлись"
    assert (part[~band] == -9999.0).all(), "вне маски осталось не nodata"
    assert band.mean() < 0.5, "проверка потеряла смысл: маска почти вся рамка"


def test_cell_mask_none_keeps_the_old_behaviour():
    rng = np.random.default_rng(1)
    vg = kb2d.Variogram(1.0, [dict(it=1, cc=10.0, aa=500.0, ang=0.0,
                                   anis=1.0)])
    xd = rng.uniform(0.0, 1000.0, 60)
    yd = rng.uniform(0.0, 1000.0, 60)
    vd = rng.normal(0.0, 3.0, 60)
    kw = dict(vg=vg, ktype=1, skmean=0.0, ndmin=1, ndmax=12,
              rad2=1e12, nodata=-9999.0, xmn=0.0, ymn=0.0, cell=50.0,
              nx=20, ny=20)
    a = kb2d.build_grid(xd, yd, vd, **kw)
    b = kb2d.build_grid(xd, yd, vd, cell_mask=None, **kw)
    assert np.array_equal(a, b)


# --- локальная анизотропия -------------------------------------------------

def test_axial_mean_treats_strike_as_an_axis():
    """Простирание это ось: период 180 градусов, а не 360.

    Азимуты 170 и 10 отличаются на двадцать градусов, а не на сто
    шестьдесят, и среднее у них ноль. Обычное среднее даёт девяносто, то
    есть перпендикуляр к истине, и ошибка тихая: карта вытянется поперёк
    структуры и будет выглядеть закономерной.
    """
    m, _s = kb2d.axial_mean([170.0, 10.0])
    assert min(abs(m - 0.0), abs(m - 180.0)) < 1e-6, "получилось %.1f" % m
    m, _s = kb2d.axial_mean([350.0, 10.0])
    assert min(abs(m - 0.0), abs(m - 180.0)) < 1e-6
    m, _s = kb2d.axial_mean([80.0, 85.0, 90.0])
    assert abs(m - 85.0) < 1e-6


def test_axial_mean_reports_strength():
    """Сила вытянутости отличает согласие от разнобоя."""
    _m, strong = kb2d.axial_mean([80.0, 82.0, 84.0])
    assert strong > 0.99
    _m, weak = kb2d.axial_mean([0.0, 45.0, 90.0, 135.0])
    assert weak < 0.05, "разнобой не распознан: %.3f" % weak
    _m, none = kb2d.axial_mean([])
    assert none == 0.0


def test_rotated_keeps_everything_but_the_azimuth():
    """Поворот меняет только направление главной оси."""
    vg = kb2d.Variogram(0.5, [dict(it=1, cc=10.0, aa=300.0, ang=0.0,
                                   anis=0.3)])
    r = vg.rotated(90.0)
    assert r.anis == vg.anis and r.cc == vg.cc and r.c0 == vg.c0
    # ковариация вдоль главной оси одна и та же, только ось другая
    assert abs(vg.cova2(0.0, 100.0) - r.cova2(100.0, 0.0)) < 1e-9
    assert vg.cova2(100.0, 0.0) < 1e-6, "проверка потеряла смысл"
    assert vg.rotated(90.0) is r, "кэш поворотов не работает"


def test_local_azimuth_follows_a_curved_band():
    """Оценка следует изогнутой полосе лучше, чем при одном азимуте.

    Предложение В. Швалева. Модельная задача: аномальная полоса,
    изогнутая по синусоиде, азимут задан в точках. Проверка числом, а не
    на глаз: корреляция с истинным полем.
    """
    rng = np.random.default_rng(0)
    n = 400
    xs = rng.uniform(0.0, 2000.0, n)
    ys = rng.uniform(0.0, 1200.0, n)

    def axis(x):
        return 500.0 + 300.0 * np.sin(x / 400.0)

    val = np.exp(-((ys - axis(xs)) / 120.0) ** 2) * 100.0 \
        + rng.normal(0.0, 4.0, n)
    slope = 300.0 / 400.0 * np.cos(xs / 400.0)
    azi = np.degrees(np.arctan2(1.0, slope)) % 180.0
    vg = kb2d.Variogram(5.0, [dict(it=1, cc=600.0, aa=700.0, ang=0.0,
                                   anis=0.25)])
    kw = dict(ktype=1, skmean=0.0, ndmin=4, ndmax=24, rad2=600.0 ** 2,
              nodata=-9999.0, xmn=0.0, ymn=0.0, cell=20.0, nx=100, ny=60)
    plain = kb2d.build_grid(xs, ys, val, vg=vg, **kw)
    local = kb2d.build_grid(xs, ys, val, vg=vg, azi=azi, **kw)
    gx, gy = np.meshgrid(np.arange(100) * 20.0, (59 - np.arange(60)) * 20.0)
    truth = np.exp(-((gy - axis(gx)) / 120.0) ** 2) * 100.0

    def score(g):
        m = g != -9999.0
        return float(np.corrcoef(g[m], truth[m])[0, 1])

    assert score(local) > score(plain) + 0.03, (
        "локальная %.3f против глобальной %.3f" % (score(local),
                                                   score(plain)))
    assert score(local) > 0.97


def test_azimuth_changes_nothing_without_anisotropy():
    """При круговой модели поворот не может ничего изменить.

    Контрольная проверка: если результат при anis=1 поехал, значит
    поворот применяется не туда.
    """
    rng = np.random.default_rng(3)
    xs = rng.uniform(0.0, 1000.0, 200)
    ys = rng.uniform(0.0, 1000.0, 200)
    v = rng.normal(50.0, 8.0, 200)
    vg = kb2d.Variogram(1.0, [dict(it=1, cc=60.0, aa=400.0, ang=0.0,
                                   anis=1.0)])
    kw = dict(ktype=1, skmean=50.0, ndmin=1, ndmax=16, rad2=800.0 ** 2,
              nodata=-9999.0, xmn=0.0, ymn=0.0, cell=25.0, nx=40, ny=40)
    plain = kb2d.build_grid(xs, ys, v, vg=vg, **kw)
    assert np.array_equal(plain, kb2d.build_grid(xs, ys, v, vg=vg, azi=None,
                                                 **kw))
    same = np.full(200, 45.0)
    assert np.allclose(plain, kb2d.build_grid(xs, ys, v, vg=vg, azi=same,
                                              **kw))


def test_scattered_azimuths_fall_back_to_the_global_model():
    """Разнобой направлений оставляет глобальную модель.

    Выдумывать направление там, где его нет в данных, неправильно:
    карта вытянется по случайности.
    """
    rng = np.random.default_rng(5)
    xs = rng.uniform(0.0, 1000.0, 150)
    ys = rng.uniform(0.0, 1000.0, 150)
    v = rng.normal(10.0, 2.0, 150)
    vg = kb2d.Variogram(0.5, [dict(it=1, cc=8.0, aa=400.0, ang=0.0,
                                   anis=0.3)])
    kw = dict(ktype=1, skmean=10.0, ndmin=1, ndmax=16, rad2=700.0 ** 2,
              nodata=-9999.0, xmn=0.0, ymn=0.0, cell=25.0, nx=40, ny=40)
    plain = kb2d.build_grid(xs, ys, v, vg=vg, **kw)
    noise = rng.uniform(0.0, 180.0, 150)
    assert np.allclose(plain, kb2d.build_grid(xs, ys, v, vg=vg, azi=noise,
                                              **kw)), \
        "при разнобое направление всё же применилось"
