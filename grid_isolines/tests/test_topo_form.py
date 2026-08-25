# -*- coding: utf-8 -*-
"""Тесты ядра поверхности между структурными линиями (topo_form).

Список проверок - из постановки (пятая редакция), раздел «Как проверяется».
Запуск: python test_topo_form.py.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import topo_form as tf  # noqa: E402


def _straight_slope(ny=40, nx=60, x_top=15, x_bot=35, z_top=120.0,
                    z_bot=100.0):
    """Прямой откос: верх и низ вертикальными линиями, отметки постоянные."""
    shape = (ny, nx)
    top = [{"pts": [(x_top, 2), (x_top, ny - 3)], "z": z_top}]
    bot = [{"pts": [(x_bot, 2), (x_bot, ny - 3)], "z": z_bot}]
    tm, tv, _ = tf.rasterize_side(shape, top)
    bm, bv, _ = tf.rasterize_side(shape, bot)
    return shape, tm, tv, bm, bv


def test_straight_slope_is_ruled():
    """Прямой откос: поверхность строго линейчата, уклон постоянен."""
    shape, tm, tv, bm, bv = _straight_slope()
    r = tf.form_surface(tm, tv, bm, bv)
    row = 20
    zs = r["z"][row, 15:36]
    diffs = np.diff(zs)
    assert abs(zs[0] - 120.0) < 1e-9
    assert abs(zs[-1] - 100.0) < 1e-9
    assert np.allclose(diffs, diffs[0], atol=1e-6)   # прямая


def test_elevations_reproduced_exactly():
    """Отметки на самих объектах воспроизводятся точно."""
    shape, tm, tv, bm, bv = _straight_slope()
    r = tf.form_surface(tm, tv, bm, bv)
    assert np.allclose(r["z"][tm], 120.0)
    assert np.allclose(r["z"][bm], 100.0)


def test_alpha_bounded_and_monotone():
    """Вес в теле лежит в [0, 1] и меняется поперёк монотонно."""
    shape, tm, tv, bm, bv = _straight_slope()
    r = tf.form_surface(tm, tv, bm, bv)
    a = r["alpha"]
    assert float(np.nanmin(a)) >= 0.0 and float(np.nanmax(a)) <= 1.0
    row = a[20, 15:36]
    assert (np.diff(row) <= 1e-9).all()              # к низу убывает


def test_pit_cone():
    """Яма: замкнутая бровка и точка на дне дают конус с вершиной в точке."""
    ny = nx = 61
    shape = (ny, nx)
    cx = cy = 30
    ang = np.linspace(0, 2 * np.pi, 120)
    ring = [(cx + 20 * np.cos(a), cy + 20 * np.sin(a)) for a in ang]
    top = [{"pts": ring, "z": 110.0}]
    bot = [{"pts": [(cx, cy)], "z": 100.0}]
    tm, tv, _ = tf.rasterize_side(shape, top)
    bm, bv, _ = tf.rasterize_side(shape, bot)
    r = tf.form_surface(tm, tv, bm, bv)
    assert abs(r["z"][cy, cx] - 100.0) < 1e-9        # вершина ровно в точке
    prof = r["z"][cy, cx:cx + 20]
    d = np.diff(prof)
    assert (d >= -1e-9).all()                        # радиус монотонен
    assert np.allclose(d[2:-2], d[2], atol=0.15)     # почти прямой


def test_ditch_two_crests_one_floor():
    """Канава: две бровки и дно, каждая половина повторяет одиночный откос."""
    ny, nx = 40, 61
    shape = (ny, nx)
    top = [{"pts": [(10, 2), (10, ny - 3)], "z": 110.0},
           {"pts": [(50, 2), (50, ny - 3)], "z": 110.0}]
    bot = [{"pts": [(30, 2), (30, ny - 3)], "z": 100.0}]
    tm, tv, _ = tf.rasterize_side(shape, top)
    bm, bv, _ = tf.rasterize_side(shape, bot)
    r = tf.form_surface(tm, tv, bm, bv)
    row = 20
    left = r["z"][row, 10:31]
    right = r["z"][row, 30:51][::-1]
    assert np.allclose(left, right, atol=1e-6)       # половины зеркальны
    assert abs(r["z"][row, 30] - 100.0) < 1e-9       # шва по дну нет


def test_converging_gully_no_nan():
    """Сходящаяся промоина: NaN нет, глубина убывает к точке схождения."""
    ny, nx = 40, 80
    shape = (ny, nx)
    # бровки сходятся в (5, 20) и расходятся к x=70
    top = [{"pts": [(5, 20), (70, 12)], "z": 105.0},
           {"pts": [(5, 20), (70, 28)], "z": 105.0}]
    bot = [{"pts": [(5, 20, 105.0), (70, 20, 100.0)]}]
    tm, tv, _ = tf.rasterize_side(shape, top)
    bm, bv, _ = tf.rasterize_side(shape, bot)
    r = tf.form_surface(tm, tv, bm, bv)
    band = r["z"][10:31, 5:71]
    assert np.isfinite(band).all()                   # NaN не родился
    # глубина против плоской крышки 105: у схождения ~0, к устью растёт
    depth_head = 105.0 - r["z"][20, 8]
    depth_mouth = 105.0 - r["z"][20, 68]
    assert depth_head < 0.6
    assert depth_mouth > 3.0


def test_seam_reports_mismatch():
    """Разведённые отметки в точке схождения дают ненулевой seam."""
    ny, nx = 30, 60
    shape = (ny, nx)
    top = [{"pts": [(5, 15), (55, 8)], "z": 105.5}]  # бровка на 105.5
    bot = [{"pts": [(5, 15, 105.0), (55, 15, 100.0)]}]   # тальвег от 105.0
    tm, tv, _ = tf.rasterize_side(shape, top)
    bm, bv, _ = tf.rasterize_side(shape, bot)
    r = tf.form_surface(tm, tv, bm, bv)
    assert float(r["seam"].max()) >= 0.45            # ~0.5 м расхождение


def test_shape_function_keeps_edges():
    """Любая функция формы с f(0)=0 и f(1)=1 не трогает отметки на линиях."""
    shape, tm, tv, bm, bv = _straight_slope()
    for kind in (tf.SHAPE_LINEAR, tf.SHAPE_SMOOTH):
        r = tf.form_surface(tm, tv, bm, bv, shape_kind=kind)
        assert np.allclose(r["z"][tm], 120.0)
        assert np.allclose(r["z"][bm], 100.0)
    assert tf.shape_function(0.0) == 0.0 and tf.shape_function(1.0) == 1.0
    assert tf.shape_function(0.0, tf.SHAPE_SMOOTH) == 0.0
    assert tf.shape_function(1.0, tf.SHAPE_SMOOTH) == 1.0


def test_curved_crest_deviation_measured():
    """Изогнутый откос с переменной отметкой: отклонение от линейчатой
    поверхности выводится числом (и на прямых участках мало)."""
    ny, nx = 60, 60
    shape = (ny, nx)
    # Г-образная бровка с ростом отметки вдоль, подошва со смещением
    crest_pts = [(10, 10, 110.0), (40, 10, 113.0), (40, 40, 116.0)]
    toe_pts = [(10, 20, 100.0), (30, 20, 100.0), (30, 40, 100.0)]
    tm, tv, _ = tf.rasterize_side(shape, [{"pts": crest_pts}])
    bm, bv, _ = tf.rasterize_side(shape, [{"pts": toe_pts}])
    r = tf.form_surface(tm, tv, bm, bv)
    # аналитическая линейчатая на прямом участке (столбцы 12..18, строка 25):
    # z = 113 + (116-113)*(row дальше)... проще: поперечник между бровкой
    # x=40? Берём прямой участок первого плеча: строка r=25 не годится.
    # Прямой поперечник: колонка 15 лежит между бровкой (10,10)-(40,10) и
    # подошвой (10,20)-(30,20); вдоль колонки z линейен от crest к toe.
    col = 15
    z_c = 110.0 + 3.0 * (col - 10) / 30.0            # отметка бровки тут
    prof = r["z"][10:21, col]
    ruled = np.linspace(z_c, 100.0, 11)
    dev_straight = float(np.abs(prof - ruled).max())
    assert dev_straight < 0.35                       # на прямом почти ноль
    # залом на медиальной оси у внутреннего угла: меряем неотрицательное
    # число и печатаем, порога нет - это цена метода, а не дефект
    kink_zone = r["z"][22:38, 22:38]
    gy, gx = np.gradient(kink_zone)
    kink = float(np.hypot(gx, gy).max())
    print("      [мера] отклонение на прямом %.3f, макс градиент у угла %.3f"
          % (dev_straight, kink))
    assert np.isfinite(kink)


def test_no_sources_gives_inf_and_nan():
    """Пустая сторона: расстояние inf, отметка NaN, ничего не падает."""
    shape = (20, 20)
    m = np.zeros(shape, dtype=bool)
    v = np.full(shape, np.nan)
    d, z = tf.distance_with_source(m, v)
    assert np.isinf(d).all()
    assert np.isnan(z).all()


def test_distance_matches_bruteforce():
    """Точность преобразования: против прямого перебора на случайной маске."""
    rng = np.random.default_rng(3)
    shape = (30, 40)
    m = np.zeros(shape, dtype=bool)
    pts = [(int(rng.integers(0, 30)), int(rng.integers(0, 40)))
           for _ in range(12)]
    v = np.full(shape, np.nan)
    for i, (r, c) in enumerate(pts):
        m[r, c] = True
        v[r, c] = 100.0 + i
    d, z = tf.distance_with_source(m, v)
    yy, xx = np.mgrid[0:30, 0:40]
    stack = np.stack([np.hypot(yy - r, xx - c) for r, c in pts])
    d_ref = stack.min(axis=0)
    assert np.allclose(d, d_ref, atol=1e-9)
    # отметка соответствует одному из ближайших источников
    near = np.abs(stack - d_ref[None]) < 1e-9
    z_pts = np.array([100.0 + i for i in range(len(pts))])
    ok = np.zeros(shape, dtype=bool)
    for i in range(len(pts)):
        ok |= near[i] & (np.abs(z - z_pts[i]) < 1e-9)
    assert ok.all()


def test_body_straight_slope_is_band():
    """Тело прямого откоса - полоса между линиями, наружу не течёт."""
    shape, tm, tv, bm, bv = _straight_slope()
    r = tf.form_surface(tm, tv, bm, bv)
    body = tf.body_mask(tm, bm, r["d_top"], r["d_bot"])
    rows, cols = np.nonzero(body)
    assert cols.min() >= 14 and cols.max() <= 36     # полоса 15..35 + допуск
    inside = body[10:30, 17:34]
    assert inside.mean() > 0.95                      # внутри сплошное тело


def test_body_cone_filled_ring_hollow():
    """Конус заполнен внутри бровки, кольцо не трогает свою внутренность."""
    ny = nx = 61
    shape = (ny, nx)
    ang = np.linspace(0, 2 * np.pi, 180)
    ring = [(30 + 20 * np.cos(a), 30 + 20 * np.sin(a)) for a in ang]
    # яма: кольцо + точка в центре
    tm, tv, _ = tf.rasterize_side(shape, [{"pts": ring, "z": 110.0}])
    bm, bv, _ = tf.rasterize_side(shape, [{"pts": [(30, 30)], "z": 100.0}])
    r = tf.form_surface(tm, tv, bm, bv)
    body = tf.body_mask(tm, bm, r["d_top"], r["d_bot"])
    assert body[30, 30]                              # центр в теле
    assert body[25:36, 25:36].all()                  # середина сплошная
    assert not body[2, 2]                            # угол снаружи

    # кольцо: две замкнутые, тело между ними, внутренность пустая
    inner = [(30 + 8 * np.cos(a), 30 + 8 * np.sin(a)) for a in ang]
    tm2, tv2, _ = tf.rasterize_side(shape, [{"pts": ring, "z": 110.0}])
    bm2, bv2, _ = tf.rasterize_side(shape, [{"pts": inner, "z": 100.0}])
    r2 = tf.form_surface(tm2, tv2, bm2, bv2)
    body2 = tf.body_mask(tm2, bm2, r2["d_top"], r2["d_bot"])
    assert body2[30, 44]                             # кольцо в теле
    assert not body2[30, 30]                         # внутренность пуста
    assert not body2[2, 2]


def test_body_converging_narrows():
    """У сходящейся промоины тело сходится вместе с шириной."""
    ny, nx = 40, 80
    shape = (ny, nx)
    top = [{"pts": [(5, 20), (70, 12)], "z": 105.0},
           {"pts": [(5, 20), (70, 28)], "z": 105.0}]
    bot = [{"pts": [(5, 20, 105.0), (70, 20, 100.0)]}]
    tm, tv, _ = tf.rasterize_side(shape, top)
    bm, bv, _ = tf.rasterize_side(shape, bot)
    r = tf.form_surface(tm, tv, bm, bv)
    body = tf.body_mask(tm, bm, r["d_top"], r["d_bot"])
    w_head = int(body[:, 10].sum())
    w_mouth = int(body[:, 65].sum())
    assert w_mouth > w_head                          # к устью шире
    assert not body[:, 76:].any()                    # за устьем тела нет


def test_collect_forms_and_orphans():
    """Разбор по полю связи: формы собираются, одинокие стороны с причиной."""
    top = [{"pts": [(0, 0)], "z": 1.0, "link": "a"},
           {"pts": [(0, 0)], "z": 1.0, "link": "b"}]
    bot = [{"pts": [(1, 1)], "z": 0.0, "link": "a"},
           {"pts": [(1, 1)], "z": 0.0, "link": "c"},
           {"pts": [(2, 2)], "z": 0.0}]              # без link - не в формы
    forms, orphans = tf.collect_forms(top, bot)
    assert [f[0] for f in forms] == ["a"]
    sides = sorted((o["side"], o["link"]) for o in orphans)
    assert sides == [("bot", "c"), ("top", "b")]


def test_constraints_for_t2r():
    """Мост к 2.03: точки тела и барьеры границы, вырезка по охвату."""
    extent = (0.0, 0.0, 200.0, 100.0)
    top = [{"pts": [(60, 40), (140, 40)], "z": 120.0, "link": "s"}]
    bot = [{"pts": [(60, 60), (140, 60)], "z": 100.0, "link": "s"}]
    out = tf.forms_to_constraints(top, bot, extent, cell=2.0)
    pts = out["points"]
    assert pts.shape[0] > 100
    assert pts[:, 0].min() > 40 and pts[:, 0].max() < 160   # вырезка локальна
    assert pts[:, 2].max() <= 120.0 + 1e-6
    assert pts[:, 2].min() >= 100.0 - 1e-6
    assert out["barriers"]                            # граница есть
    rep = out["report"][0]
    assert rep["link"] == "s" and rep["n_body"] == pts.shape[0]


def test_constraints_feed_topo2raster():
    """Интеграция: форма через жёсткие узлы доживает до рельефа 2.03.

    Прогон настоящего topo2raster: форма плюс редкие фоновые точки.
    Внутри тела поверхность обязана держать отметки формы, вне тела -
    не разрушиться.
    """
    import topo_t2r as t2r
    extent = (0.0, 0.0, 120.0, 80.0)
    top = [{"pts": [(30, 30), (90, 30)], "z": 120.0, "link": "s"}]
    bot = [{"pts": [(30, 50), (90, 50)], "z": 100.0, "link": "s"}]
    out = tf.forms_to_constraints(top, bot, extent, cell=2.0)
    bg = np.array([[5.0, 5.0, 118.0], [115.0, 5.0, 118.0],
                   [5.0, 75.0, 102.0], [115.0, 75.0, 102.0]])
    points = np.concatenate([bg, out["points"]], axis=0)
    z, x0, y_top = t2r.topo2raster(points, [], out["barriers"], [],
                                   extent, 2.0, iterations=40)
    def rc(y, x):
        # индекс ячейки, в центре которой лежит точка (центры на +0.5)
        return (int(round((y_top - y) / 2.0 - 0.5)),
                int(round((x - x0) / 2.0 - 0.5)))
    r_mid, c_mid = rc(40.0, 60.0)
    r_top_, _ = rc(30.0, 60.0)
    r_bot_, _ = rc(50.0, 60.0)
    assert abs(z[r_mid, c_mid] - 110.0) < 2.1        # середина, +-ячейка
    assert abs(z[r_top_, c_mid] - 120.0) < 1.0       # бровка держится
    assert abs(z[r_bot_, c_mid] - 100.0) < 1.0       # подошва держится
    prof = z[min(r_top_, r_bot_):max(r_top_, r_bot_) + 1, c_mid]
    assert (np.diff(prof) < 0).all() or (np.diff(prof) > 0).all()
    assert np.isfinite(z).all()


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


def test_rasterize_keeps_the_order_of_the_walk():
    """При попадании двух точек в одну ячейку побеждает последняя.

    Векторная растеризация идёт группами сегментов равной густоты, и
    группировка переставляет сегменты. Без возврата исходной очерёдности
    в ячейке оседает отметка не того сегмента: маска сходится, а значения
    расходятся, и на форме это выглядит как случайный выброс.
    """
    # два сегмента через одну и ту же ячейку, разной длины и разного z
    feats = [{"pts": [(0.0, 0.0, 10.0), (9.0, 0.0, 10.0),
                      (9.5, 0.0, 20.0), (0.0, 0.0, 20.0)], "z": None}]
    mask, values, skipped = tf.rasterize_side((20, 20), feats, cell=1.0)
    assert skipped == 0
    # последним обходом идёт возврат с отметкой 20
    assert abs(float(values[0, 0]) - 20.0) < 1e-9


def test_rasterize_handles_a_single_point_feature():
    feats = [{"pts": [(3.0, 4.0, 55.0)], "z": None}]
    mask, values, _ = tf.rasterize_side((10, 10), feats, cell=1.0)
    assert bool(mask[4, 3]) and abs(float(values[4, 3]) - 55.0) < 1e-9


def test_rasterize_takes_the_feature_z_without_vertex_z():
    feats = [{"pts": [(0.0, 0.0), (5.0, 0.0)], "z": 7.0}]
    mask, values, _ = tf.rasterize_side((10, 10), feats, cell=1.0)
    assert int(mask.sum()) >= 5
    assert np.allclose(values[mask], 7.0)


def test_rasterize_drops_points_outside_the_grid():
    """Точка за краем растра выбрасывается, а не заворачивается по краю."""
    feats = [{"pts": [(-50.0, -50.0, 1.0), (-40.0, -40.0, 1.0)], "z": None}]
    mask, _values, skipped = tf.rasterize_side((10, 10), feats, cell=1.0)
    assert int(mask.sum()) == 0 and skipped == 0
