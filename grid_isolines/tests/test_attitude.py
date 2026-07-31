# -*- coding: utf-8 -*-
"""Тесты восстановления залегания по трёхмерному следу (attitude).

Запуск: python test_attitude.py.
"""

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import attitude as at  # noqa: E402


def _plane_points(dip_deg, az_deg, xs, ys, z0=100.0):
    """Точки, точно лежащие на плоскости с заданным залеганием."""
    d = math.radians(dip_deg)
    a = math.radians(az_deg)
    # спуск в направлении азимута: z убывает вдоль (sin a, cos a)
    gx = -math.tan(d) * math.sin(a)
    gy = -math.tan(d) * math.cos(a)
    return np.column_stack([xs, ys, z0 + gx * np.asarray(xs)
                            + gy * np.asarray(ys)])


def test_recovers_known_attitude():
    """Залегание восстанавливается точно на изогнутом следе."""
    t = np.linspace(0, 200, 40)
    xs = t
    ys = 40.0 * np.sin(t / 60.0)          # изгиб в плане обязателен
    for dip, az in ((25.0, 120.0), (5.0, 300.0), (60.0, 15.0)):
        pts = _plane_points(dip, az, xs, ys)
        r = at.attitude_of_trace(pts)
        assert "reason" not in r, (dip, az, r)
        assert abs(r["dip"] - dip) < 1e-6, (dip, r["dip"])
        assert abs((r["dip_az"] - az + 180.0) % 360.0 - 180.0) < 1e-6
        assert r["rms"] < 1e-9


def test_steep_plane_is_not_special():
    """Крутое залегание считается так же, как пологое.

    Подгонка z = a*x + b*y + c здесь развалилась бы: у вертикальной
    плоскости коэффициенты уходят в бесконечность. Через собственные
    векторы особого случая нет.
    """
    t = np.linspace(0, 100, 30)
    xs = t
    ys = 25.0 * np.sin(t / 30.0)
    r = at.attitude_of_trace(_plane_points(85.0, 90.0, xs, ys))
    assert "reason" not in r
    assert abs(r["dip"] - 85.0) < 1e-5


def test_straight_trace_is_refused():
    """Прямой в плане след залегания не определяет - честный отказ.

    Через одну прямую в пространстве проходит бесконечно много
    плоскостей. Наивная реализация выдала бы уверенное число из шума
    округления.
    """
    xs = np.linspace(0, 200, 40)
    ys = np.zeros_like(xs)
    r = at.attitude_of_trace(_plane_points(25.0, 120.0, xs, ys))
    assert "reason" in r
    assert "прям" in r["reason"]


def test_flat_trace_is_refused():
    """Все отметки равны: плоскость горизонтальна, азимут бессмыслен."""
    xs = np.linspace(0, 100, 20)
    ys = 30.0 * np.sin(xs / 25.0)
    pts = np.column_stack([xs, ys, np.full_like(xs, 110.0)])
    r = at.attitude_of_trace(pts)
    assert "reason" in r and "одинаков" in r["reason"]


def test_planarity_separates_line_from_plane():
    """Мера обусловленности различает прямую и настоящую плоскость."""
    t = np.linspace(0, 200, 40)
    curved = _plane_points(25.0, 120.0, t, 40.0 * np.sin(t / 60.0))
    straight = _plane_points(25.0, 120.0, t, np.zeros_like(t))
    _n1, v1, _c1 = at.fit_plane(curved)
    _n2, v2, _c2 = at.fit_plane(straight)
    assert at.planarity(v1) > 0.5
    assert at.planarity(v2) < 1e-6


def test_noise_shows_up_in_rms():
    """Отклонение от плоскости попадает в невязку, а не прячется."""
    rng = np.random.default_rng(4)
    t = np.linspace(0, 200, 60)
    pts = _plane_points(20.0, 200.0, t, 45.0 * np.sin(t / 55.0))
    pts[:, 2] += rng.normal(0.0, 0.5, pts.shape[0])
    r = at.attitude_of_trace(pts)
    assert "reason" not in r
    assert 0.2 < r["rms"] < 1.0
    assert abs(r["dip"] - 20.0) < 3.0


def test_windows_follow_a_fold():
    """Складка: скользящее окно показывает изменение залегания.

    Одно число по всему следу дало бы среднее, физически не существующее.
    """
    t = np.linspace(0, 300, 90)
    xs = t
    ys = 50.0 * np.sin(t / 70.0)
    # два крыла: первое падает на восток, второе на запад
    z = np.where(t < 150, 100.0 - 0.30 * t, 100.0 - 0.30 * 150 + 0.30 * (t - 150))
    pts = np.column_stack([xs, ys, z])
    wins = at.attitude_windows(pts, window=15)
    ok = [(i, r) for i, r in wins if "reason" not in r]
    assert len(ok) > 10
    left = [r["dip_az"] for i, r in ok if i < 30]
    right = [r["dip_az"] for i, r in ok if i > 60]
    assert left and right
    # азимуты крыльев расходятся заметно
    diff = abs((np.mean(left) - np.mean(right) + 180.0) % 360.0 - 180.0)
    assert diff > 60.0


def test_three_points_is_the_minimal_case():
    """Правило трёх точек работает как частный случай общего метода."""
    pts = np.array([[0.0, 0.0, 100.0],
                    [100.0, 0.0, 100.0],
                    [0.0, 100.0, 50.0]])
    r = at.attitude_of_trace(pts)
    assert "reason" not in r
    assert abs(r["dip"] - math.degrees(math.atan(0.5))) < 1e-6
    assert abs(r["dip_az"] - 0.0) < 1e-6      # спуск строго на север


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
