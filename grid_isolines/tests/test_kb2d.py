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


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
