# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Отметка уреза на меандрирующей реке.

Дефект нашёлся по отчёту В. Швалева: вдоль урезов в режиме изолинии
Topo2Raster дорисовывал бугры. Причина оказалась не в сглаживании и не в
растровой маске контура, а в самой отметке уреза.

Высота уреза интерполировалась обратными взвешенными расстояниями по
ВСЕМ вершинам кольца сразу. На меандре противоположный берег близко в
пространстве, но далеко по реке и с другой отметкой, поэтому уровень
подтягивался к чужому. Ошибка растёт с падением реки: при падении 15 м на
лист она доходила до 0.9 м с размахом 1.8 м, и вдоль уреза шли бугры.

Интерполяция стала локальной, по ближайшим вершинам. Здесь это
закреплено числом: считается отклонение отметки в ячейках уреза от
истинного уровня, заданного падением вдоль оси реки.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import topo_t2r as t2r  # noqa: E402

CELL = 5.0
X0 = 0.0
Y_TOP = 700.0
SHAPE = (140, 240)


def _river(amp, wave, drop, half=45.0):
    """Меандр с падением уреза вниз по течению: (кольцо, Z вершин, ось)."""
    xs = np.linspace(50.0, 1150.0, 240)
    axis = 350.0 + amp * np.sin(xs / wave)
    zline = np.linspace(155.0, 155.0 - drop, len(xs))
    left = [(x, y + half) for x, y in zip(xs, axis)]
    right = [(x, y - half) for x, y in zip(xs, axis)][::-1]
    ring = np.array(left + right + [left[0]])
    rz = np.array(list(zline) + list(zline[::-1]) + [zline[0]])
    return ring, rz, xs, zline


def _edge_error(amp, wave, drop):
    """Отклонение отметки уреза от истинного уровня, в метрах."""
    ring, rz, xs, zline = _river(amp, wave, drop)
    mask = t2r.polygon_mask([ring], X0, Y_TOP, CELL, SHAPE)
    surf = t2r._interp_ring_surface([ring], [rz], mask, X0, Y_TOP, CELL, SHAPE)
    edge = t2r.mask_edge(mask)
    rr, cc = np.nonzero(edge)
    assert rr.size > 100, "урез не попал в сетку, проверка бессмысленна"
    true = np.interp(X0 + (cc + 0.5) * CELL, xs, zline)
    return surf[rr, cc] - true


def test_water_level_follows_its_own_bank_on_a_meander():
    """Урез держит свой уровень, а не подтягивается с другого берега.

    Крутой меандр с падением 15 м на лист - тот случай, где глобальная
    интерполяция давала ошибку до 0.9 м и размах 1.8 м.
    """
    err = _edge_error(200.0, 90.0, 15.0)
    assert np.abs(err).max() < 0.45, (
        "отметка уреза уходит на %.2f м" % np.abs(err).max())
    assert err.max() - err.min() < 0.9, (
        "размах отметки уреза %.2f м" % (err.max() - err.min()))


def test_gentle_meander_is_almost_exact():
    """На пологом меандре отметка уреза практически точна."""
    err = _edge_error(140.0, 200.0, 5.0)
    assert np.abs(err).max() < 0.2


def test_error_does_not_explode_with_the_drop():
    """Ошибка растёт с падением реки медленнее, чем само падение.

    Это и отличает локальную интерполяцию от глобальной: у глобальной
    ошибка была пропорциональна падению, потому что подтягивался уровень
    с другого конца реки.
    """
    small = np.abs(_edge_error(200.0, 90.0, 5.0)).max()
    big = np.abs(_edge_error(200.0, 90.0, 15.0)).max()
    assert big < small * 3.0 + 0.05, (
        "ошибка растёт как падение: %.2f против %.2f" % (small, big))


def test_flat_lake_stays_flat():
    """Озеро с одинаковыми Z по вершинам даёт ровную плоскость.

    Локализация не должна ломать простой случай.
    """
    ring, _, _, _ = _river(140.0, 200.0, 0.0)
    rz = np.full(len(ring), 151.5)
    mask = t2r.polygon_mask([ring], X0, Y_TOP, CELL, SHAPE)
    surf = t2r._interp_ring_surface([ring], [rz], mask, X0, Y_TOP, CELL, SHAPE)
    inside = surf[mask]
    assert np.abs(inside - 151.5).max() < 1e-9, "плоское озеро перестало быть плоским"


def test_nearest_count_is_a_named_constant():
    """Число ближайших вершин задано осознанно, а не вшито в выражение."""
    assert isinstance(t2r.K_RING_NEAREST, int)
    assert 4 <= t2r.K_RING_NEAREST <= 16


# --- продольный профиль русла по отметкам дна ------------------------------

def _chain_case(anchors, n=40, start=152.0, drop=0.01):
    """Цепочка тальвега с закреплёнными отметками дна."""
    z = np.full((1, n), start)
    chain = np.arange(n)
    pin = np.zeros((1, n), dtype=bool)
    pv = np.zeros((1, n))
    for i, v in anchors:
        pin[0, i] = True
        pv[0, i] = v
        z[0, i] = v
    bounds = [t2r.stream_bounds(chain, pin, pv, drop)]
    for _ in range(50):
        t2r._enforce_streams(z, [chain], drop, bounds)
        z[0, pin[0]] = pv[0, pin[0]]
    return z[0], pin[0]


def test_profile_flows_from_one_depth_point_to_the_next():
    """Между отметками дна профиль идёт линией, а не срезается мимо.

    Отчёт В. Швалева по продольному профилю. Принуждение тальвега умело
    только опускать: отметку выше текущего профиля оно игнорировало, и
    пикет оставался одиноким пиком, а следующий подпирался ступенькой. На
    профиле это ровное дно с двумя иглами вместо ската от точки к точке.
    """
    z, pin = _chain_case([(5, 153.0), (30, 151.0)])
    inner = np.diff(z[5:31])
    assert np.allclose(inner, inner[0], atol=1e-9), (
        "между отметками профиль не линеен")
    assert inner[0] < 0.0, "между отметками профиль не падает"
    assert abs(z[5] - 153.0) < 1e-9 and abs(z[30] - 151.0) < 1e-9


def test_depth_points_are_not_lonely_spikes():
    """Сосед отметки дна не проваливается на всю её высоту."""
    z, _ = _chain_case([(5, 153.0), (30, 151.0)])
    assert z[5] - z[6] < 0.2, (
        "за отметкой дна профиль падает на %.2f м" % (z[5] - z[6]))


def test_descent_stays_monotone_along_the_whole_chain():
    """Падение вниз по течению остаётся монотонным."""
    z, _ = _chain_case([(5, 153.0), (30, 151.0)])
    assert np.all(np.diff(z) <= 1e-9), "профиль пошёл вверх по течению"


def test_chain_without_anchors_behaves_as_before():
    """Без отметок дна поведение прежнее: монотонная срезка."""
    z, _ = _chain_case([])
    assert np.all(np.diff(z) <= 1e-9)
    assert z[0] >= z[-1]


def test_anchor_above_the_carved_profile_lifts_the_reach():
    """Отметка выше срезанного профиля поднимает участок выше себя.

    Раньше она игнорировалась: принуждение умело только опускать.
    """
    z, _ = _chain_case([(20, 155.0)], start=152.0)
    assert z[10] > 152.0, "участок выше отметки не поднялся"
    assert abs(z[20] - 155.0) < 1e-9


# --- профилирование уреза по точкам высот -----------------------------------

def _channel(drop=5.0, n=80):
    """Прямой канал: кольцо уреза и истинные отметки его вершин."""
    xs = np.linspace(0.0, 1000.0, n)
    left = [(x, 20.0) for x in xs]
    right = [(x, -20.0) for x in xs][::-1]
    ring = np.array(left + right + [left[0]])
    true = np.interp(ring[:, 0], [0.0, 1000.0], [155.0, 155.0 - drop])
    return ring, xs, true


def _bank_points(xs, drop=5.0, offset=1.5):
    """Отметки уреза точками высот по обе стороны от контура."""
    out = []
    for x in xs[::4]:
        z = float(np.interp(x, [0.0, 1000.0], [155.0, 155.0 - drop]))
        out.append((x, 20.0 + offset, z))
        out.append((x, -20.0 - offset, z))
    return out


def test_water_edge_takes_its_elevation_from_nearby_spot_heights():
    """Урез профилируется по точкам у контура, с уклоном вниз по течению.

    Алгоритм В. Швалева, который он выполнял руками: контур отрисован без
    Z, а отметки уреза стоят обычными точками рядом с берегом.
    """
    ring, xs, true = _channel()
    pts = np.array(_bank_points(xs))
    rz, n = t2r.profile_ring_from_points([ring], pts, tol=3.0)
    assert rz is not None and n > 0
    err = np.abs(rz[0] - true)
    assert err.max() < 0.5, "отметка уреза уходит на %.2f м" % err.max()
    assert err.mean() < 0.1


def test_far_points_do_not_drag_the_water_edge_up():
    """Точки на берегу, дальше допуска, в профилирование не идут.

    Иначе урез подтянуло бы к отметкам горизонталей, и вся затея
    потеряла бы смысл.
    """
    ring, xs, true = _channel()
    pts = _bank_points(xs)
    for x in xs[::6]:
        pts.append((x, 120.0, 165.0))          # берег заметно выше
    rz, _ = t2r.profile_ring_from_points([ring], np.array(pts), tol=3.0)
    assert rz[0].max() <= 155.0 + 1e-6, "урез подтянуло к берегу"


def test_too_small_tolerance_refuses_instead_of_guessing():
    """Не нашлось точек - урез остаётся прежним, а не выдумывается."""
    ring, xs, _ = _channel()
    rz, n = t2r.profile_ring_from_points([ring], np.array(_bank_points(xs)),
                                         tol=0.5)
    assert rz is None and n == 0


def test_point_between_two_water_bodies_goes_to_the_nearer_one():
    """Точка в допуске от двух водоёмов достаётся ближайшему.

    Отдать обоим значило бы поднять один урез отметкой другого.
    """
    a = np.array([(0.0, 0.0), (100.0, 0.0), (100.0, 10.0), (0.0, 10.0),
                  (0.0, 0.0)])
    b = np.array([(0.0, 30.0), (100.0, 30.0), (100.0, 40.0), (0.0, 40.0),
                  (0.0, 30.0)])
    # точка на 11 от первого и на 18 от второго
    pt = np.array([(50.0, 11.0, 150.0), (50.0, 29.0, 160.0)])
    rz_a, _ = t2r.profile_ring_from_points([a], pt, tol=3.0)
    rz_b, _ = t2r.profile_ring_from_points([b], pt, tol=3.0)
    assert rz_a is None and rz_b is None, "далёкие точки подобрались"
    near = np.array([(50.0, 11.0, 150.0), (50.0, 12.0, 150.0)])
    rz_a, n = t2r.profile_ring_from_points([a], near, tol=2.0)
    assert rz_a is not None and n == 2


def test_flat_pond_gets_one_level_from_its_points():
    """Пруд с одинаковыми отметками точек получает ровный урез."""
    ring = np.array([(0.0, 0.0), (100.0, 0.0), (100.0, 100.0), (0.0, 100.0),
                     (0.0, 0.0)])
    pts = np.array([(50.0, -1.5, 151.0), (101.5, 50.0, 151.0),
                    (50.0, 101.5, 151.0), (-1.5, 50.0, 151.0)])
    rz, n = t2r.profile_ring_from_points([ring], pts, tol=3.0)
    assert n == 4
    assert np.abs(rz[0] - 151.0).max() < 1e-9


# --- промеры дна на ось тальвега -------------------------------------------

AXIS = np.array([[x, 0.0] for x in np.linspace(0.0, 1000.0, 50)])


def _sections():
    """Створы по три промера: берег мельче, ось глубже."""
    pts = []
    for x, zc in ((100.0, 148.0), (400.0, 147.0), (700.0, 147.5),
                  (900.0, 146.0)):
        pts.append((x, -8.0, zc + 1.2))
        pts.append((x, 0.5, zc))
        pts.append((x, 8.0, zc + 0.9))
    return pts


def test_section_gives_the_axis_its_lowest_sounding():
    """Отметкой оси в створе становится минимум, а не среднее.

    Тальвег это линия наибольших глубин: его касается самый низкий
    промер створа. Взять среднее значило бы поднять дно до береговой
    глубины.
    """
    anchors, n = t2r.project_depth_points(AXIS, np.array(_sections()),
                                          tol=10.0)
    assert n == 12
    got = sorted(round(z, 3) for _s, z in anchors)
    assert got == [146.0, 147.0, 147.5, 148.0]


def test_soundings_far_from_the_axis_are_ignored():
    """Замер дальше допуска в продольный профиль не идёт."""
    pts = _sections() + [(500.0, 60.0, 155.0)]
    anchors, n = t2r.project_depth_points(AXIS, np.array(pts), tol=10.0)
    assert n == 12
    assert max(z for _s, z in anchors) < 149.0, "далёкий замер поднял профиль"


def test_zero_tolerance_switches_the_projection_off():
    anchors, n = t2r.project_depth_points(AXIS, np.array(_sections()),
                                          tol=0.0)
    assert anchors == [] and n == 0


def test_rises_against_the_fall_are_counted_not_flagged_one_by_one():
    """Подъёмы считаются итогом: плёсы и перекаты нормальны.

    Русло меняется год от года, поэтому подъём между промерами это не
    ошибка. Помечать каждый значило бы создать постоянный шум.
    """
    anchors, _ = t2r.project_depth_points(AXIS, np.array(_sections()),
                                          tol=10.0)
    ups, worst = t2r.against_the_fall(anchors)
    assert ups == 1, "плёс между 400 и 700 не замечен или замечен лишний"
    assert abs(worst - 0.5) < 1e-9


def test_a_falling_channel_reports_no_rises():
    """Ровно падающее русло не даёт ни одного участка против падения."""
    pts = [(x, 0.0, 150.0 - x / 200.0) for x in (100.0, 300.0, 600.0, 900.0)]
    anchors, _ = t2r.project_depth_points(AXIS, np.array(pts), tol=5.0)
    ups, worst = t2r.against_the_fall(anchors)
    assert ups == 0 and worst == 0.0
