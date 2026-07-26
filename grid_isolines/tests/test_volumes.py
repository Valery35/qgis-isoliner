# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Тесты ядра объёмов земляных работ.

Проверяется существенное: объём известной фигуры считается точно, знак не
путается, мёртвая зона режет то, что должна, приведение к сетке не врёт на
линейной поверхности и не выдумывает данные за краем.

    python grid_isolines/tests/test_volumes.py
"""
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
sys.path.insert(0, os.path.dirname(MODULE))

from grid_isolines import volumes as V  # noqa: E402


def test_prism_volume_exact():
    """Призма известного объёма считается точно."""
    diff = np.zeros((10, 10))
    diff[2:6, 3:8] = 2.0          # 4 на 5 ячеек, высота 2
    st = V.cutfill_stats(diff, area=25.0)   # ячейка 5 на 5
    assert abs(st["fill_volume"] - 4 * 5 * 2.0 * 25.0) < 1e-9
    assert st["cut_volume"] == 0.0
    assert abs(st["fill_area"] - 20 * 25.0) < 1e-9


def test_sign_convention():
    """Стало выше это насыпь, стало ниже это выемка."""
    before = np.full((4, 4), 100.0)
    after = np.full((4, 4), 103.0)
    st = V.cutfill_stats(V.difference(after, before), area=1.0)
    assert st["fill_volume"] > 0 and st["cut_volume"] == 0.0
    st2 = V.cutfill_stats(V.difference(before, after), area=1.0)
    assert st2["cut_volume"] > 0 and st2["fill_volume"] == 0.0
    assert abs(st2["net_volume"] + st["net_volume"]) < 1e-9


def test_dead_zone_cuts_noise():
    """Мёртвая зона убирает фоновый шум и не трогает настоящую фигуру."""
    rng = np.random.default_rng(4)
    diff = rng.normal(0.0, 0.02, size=(60, 60))
    diff[20:30, 20:30] += 5.0
    naked = V.cutfill_stats(diff, area=1.0, dead=0.0)
    clean = V.cutfill_stats(diff, area=1.0, dead=0.1)
    assert clean["cut_volume"] < naked["cut_volume"] * 0.01
    assert abs(clean["fill_volume"] - 100 * 5.0) < 1.0


def test_nodata_not_counted():
    """Ячейки без данных в объём не идут и считаются отдельно."""
    diff = np.full((5, 5), 1.0)
    diff[0, :] = np.nan
    st = V.cutfill_stats(diff, area=1.0)
    assert st["cells_nodata"] == 5
    assert abs(st["fill_volume"] - 20.0) < 1e-9


def test_resample_linear_surface_exact():
    """На линейной поверхности билинейная передискретизация точна."""
    gt_src = (0.0, 10.0, 0.0, 100.0, 0.0, -10.0)
    ys, xs = np.mgrid[0:10, 0:10]
    # z = 3x + 2y в координатах СК от центров ячеек источника
    X = gt_src[0] + (xs + 0.5) * gt_src[1]
    Y = gt_src[3] + (ys + 0.5) * gt_src[5]
    src = 3.0 * X + 2.0 * Y

    gt_dst = (5.0, 5.0, 0.0, 95.0, 0.0, -5.0)
    out = V.resample_bilinear(src, gt_src, gt_dst, (12, 12))
    ysd, xsd = np.mgrid[0:12, 0:12]
    Xd = gt_dst[0] + (xsd + 0.5) * gt_dst[1]
    Yd = gt_dst[3] + (ysd + 0.5) * gt_dst[5]
    want = 3.0 * Xd + 2.0 * Yd
    ok = np.isfinite(out)
    assert ok.sum() > 50
    assert np.nanmax(np.abs(out[ok] - want[ok])) < 1e-8


def test_resample_does_not_extrapolate():
    """За краем источника возвращается NaN, а не выдуманная высота."""
    gt_src = (0.0, 10.0, 0.0, 100.0, 0.0, -10.0)
    src = np.ones((5, 5))
    gt_dst = (-100.0, 10.0, 0.0, 200.0, 0.0, -10.0)
    out = V.resample_bilinear(src, gt_src, gt_dst, (5, 5))
    assert np.all(np.isnan(out))


def test_same_grid_tolerance():
    """Сетки, различающиеся в последних разрядах, считаются одной."""
    gt = (0.0, 30.0, 0.0, 1000.0, 0.0, -30.0)
    gt2 = (1e-9, 30.0, 0.0, 1000.0 - 1e-9, 0.0, -30.0)
    assert V.same_grid(gt, (10, 10), gt2, (10, 10))
    gt3 = (15.0, 30.0, 0.0, 1000.0, 0.0, -30.0)
    assert not V.same_grid(gt, (10, 10), gt3, (10, 10))


def test_resample_conserves_volume_inside():
    """Билинейная передискретизация сохраняет объём, если фигура не у края.

    Это важное свойство, и его стоит держать под тестом: сдвиг сетки сам по
    себе объём не меняет, веса билинейки в сумме дают единицу. Значит
    расхождения с чужими программами берутся не отсюда, а из того, какие
    ячейки вообще попали в счёт.
    """
    gt_src = (0.0, 1.0, 0.0, 20.0, 0.0, -1.0)
    src = np.zeros((20, 20))
    src[8:12, 8:12] = 4.0
    gt_dst = (0.5, 1.0, 0.0, 19.5, 0.0, -1.0)
    out = V.resample_bilinear(src, gt_src, gt_dst, (20, 20))
    v_src = V.cutfill_stats(src, area=1.0)["fill_volume"]
    v_out = V.cutfill_stats(out, area=1.0)["fill_volume"]
    assert abs(v_src - v_out) < 1e-6


def test_edge_clipping_loses_volume():
    """А вот у края объём теряется: часть фигуры уходит за сетку.

    Ровно этот механизм и разводит цифры между программами. Не формула и
    не интерполяция, а рамка и маска.
    """
    gt_src = (0.0, 1.0, 0.0, 10.0, 0.0, -1.0)
    src = np.zeros((10, 10))
    src[0:3, 0:3] = 4.0            # фигура прижата к верхнему левому углу
    gt_dst = (2.0, 1.0, 0.0, 8.0, 0.0, -1.0)   # рамка сдвинута внутрь
    out = V.resample_bilinear(src, gt_src, gt_dst, (10, 10))
    v_src = V.cutfill_stats(src, area=1.0)["fill_volume"]
    v_out = V.cutfill_stats(out, area=1.0)["fill_volume"]
    assert v_out < v_src * 0.6


def test_zone_stats_split():
    """Участки считаются раздельно, нулевая метка не возвращается."""
    diff = np.zeros((4, 4))
    diff[:2, :] = 1.0
    diff[2:, :] = -2.0
    labels = np.zeros((4, 4), dtype=int)
    labels[:2, :] = 1
    labels[2:, :] = 2
    zs = V.zone_stats(diff, labels, area=1.0)
    assert set(zs) == {1, 2}
    assert abs(zs[1]["fill_volume"] - 8.0) < 1e-9
    assert abs(zs[2]["cut_volume"] - 16.0) < 1e-9


def test_balance_verdict():
    """Вердикт по балансу смотрит на долю нетто, а не на его величину."""
    big = {"fill_volume": 100000.0, "cut_volume": 99900.0, "net_volume": 100.0}
    small = {"fill_volume": 150.0, "cut_volume": 50.0, "net_volume": 100.0}
    assert V.balance_verdict(big) == "balanced"
    assert V.balance_verdict(small) == "import"
    out = {"fill_volume": 50.0, "cut_volume": 150.0, "net_volume": -100.0}
    assert V.balance_verdict(out) == "export"
    assert V.balance_verdict({"fill_volume": 0.0, "cut_volume": 0.0,
                              "net_volume": 0.0}) == "empty"


def test_format_number_groups_digits():
    """Разряды разделяются, экспоненты в ведомости быть не должно."""
    assert V.format_volume(701224863.4) == "701\u00a0224\u00a0863"
    assert V.format_volume(999) == "999"
    assert V.format_volume(0) == "0"
    assert "e+" not in V.format_volume(7.01225e8)


def test_format_number_keeps_sign():
    """Знак нетто не теряется и не уезжает внутрь числа."""
    out = V.format_volume(-70662800)
    assert out.startswith("-")
    assert out == "-70\u00a0662\u00a0800"


def test_format_number_survives_nan():
    """Пустое значение не роняет ведомость и не печатается как nan."""
    assert V.format_number(float("nan")) == "-"
    assert V.format_number(None) == "-"


def test_format_area_in_hectares():
    """Площадь переводится в гектары с двумя знаками."""
    assert V.format_area_ha(59509800) == "5\u00a0950.98"


def test_clip_to_zones_keeps_inside_only():
    """Обрезка гасит всё вне участков и не трогает то, что внутри."""
    diff = np.arange(16, dtype=float).reshape(4, 4)
    labels = np.zeros((4, 4), dtype=int)
    labels[1:3, 1:3] = 1
    out = V.clip_to_zones(diff, labels)
    assert np.isnan(out[0, 0]) and np.isnan(out[3, 3])
    assert out[1, 1] == diff[1, 1] and out[2, 2] == diff[2, 2]
    assert int(np.count_nonzero(np.isfinite(out))) == 4


def test_clip_does_not_change_zone_volumes():
    """Обрезка не меняет объёмы по участкам: она про картинку, не про счёт."""
    rng = np.random.default_rng(11)
    diff = rng.normal(0.0, 1.0, size=(30, 30))
    labels = np.zeros((30, 30), dtype=int)
    labels[5:15, 5:15] = 1
    before = V.zone_stats(diff, labels, area=1.0)[1]
    after = V.zone_stats(V.clip_to_zones(diff, labels), labels, area=1.0)[1]
    assert abs(before["fill_volume"] - after["fill_volume"]) < 1e-9
    assert abs(before["cut_volume"] - after["cut_volume"]) < 1e-9


TESTS = [v for k, v in sorted(globals().items()) if k.startswith("test_")]

if __name__ == "__main__":
    bad = 0
    for t in TESTS:
        try:
            t()
            print("[ok]", t.__name__)
        except AssertionError as e:
            bad += 1
            print("FAIL", t.__name__, e)
    print("%d тестов, ошибок %d" % (len(TESTS), bad))
    sys.exit(1 if bad else 0)
