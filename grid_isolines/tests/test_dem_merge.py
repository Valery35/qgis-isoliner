# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты сшивки детальной ЦМР с открытой. Ядро не тянет QGIS.
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import dem_merge as dm  # noqa: E402
from grid_isolines import topo_form as tf  # noqa: E402


DATUM = 12.4


def _scene(seed=11, ny=300, nx=300, tilt=0.0):
    """Истинный рельеф, съёмка шире маски врезки и открытая ЦМР со сдвигом."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:ny, 0:nx]
    truth = 0.05 * x + 30 * np.sin(x / 70.0) * np.cos(y / 55.0)
    survey = np.zeros((ny, nx), dtype=bool)
    survey[90:210, 100:220] = True
    mask = np.zeros((ny, nx), dtype=bool)
    mask[110:190, 120:200] = True
    fine = np.where(survey, truth + rng.normal(0, 0.05, (ny, nx)), np.nan)
    coarse = (truth + DATUM + tilt * x + 1.2 * np.sin(x / 9.0)
              + rng.normal(0, 0.35, (ny, nx)))
    return truth, fine, coarse, mask


def test_distance_matches_the_exact_transform():
    """Быстрое расстояние обязано совпасть с точным до последнего знака.

    Развёртка по всем строкам сразу считает то же, что построчный вариант
    в topo_form, но на подробной ЦМР идёт в полтора десятка раз быстрее.
    """
    rng = np.random.default_rng(3)
    for shape in ((60, 80), (37, 41)):
        m = rng.random(shape) < 0.03
        m[0, 0] = True
        got = dm.distance_to_mask(m)
        ref, _z = tf.distance_with_source(m, np.where(m, 1.0, np.nan))
        assert np.allclose(got, ref, atol=1e-9)


def test_empty_mask_gives_infinite_distance():
    d = dm.distance_to_mask(np.zeros((5, 5), dtype=bool))
    assert np.isinf(d).all()


def test_weights_run_from_one_to_zero():
    """Вес: единица внутри участка, ноль за буфером, между ними спуск."""
    mask = np.zeros((40, 40), dtype=bool)
    mask[15:25, 15:25] = True
    w = dm.blend_weights(mask, 6.0)
    assert np.allclose(w[mask], 1.0)
    assert abs(float(w[20, 32])) < 1e-12          # дальше буфера
    line = w[20, 25:32]
    assert np.all(np.diff(line) <= 1e-12), "вес обязан убывать вдоль кольца"
    assert 0.0 < float(w[20, 28]) < 1.0


def test_datum_shift_is_removed():
    """Систематический сдвиг снимается по кольцу перекрытия."""
    _truth, fine, coarse, mask = _scene()
    _z, rep = dm.merge(fine, coarse, mask, width_px=12.0)
    assert rep["overlap"] > 0
    assert abs(rep["median"] + DATUM) < 0.5, "невязка должна быть около датума"
    assert abs(rep["after"]["median"]) < 0.05, "после поправки остатка нет"


def test_seam_is_not_worse_than_the_terrain_itself():
    """Мера приёмки: шов не выделяется на фоне обычного рельефа.

    Ручная сшивка (внутри маски съёмка, снаружи открытая) даёт на шве
    уступ во весь датум - это и есть та ступень, на которой у гидрологов
    встаёт поток.
    """
    _truth, fine, coarse, mask = _scene()
    merged, _rep = dm.merge(fine, coarse, mask, width_px=12.0)
    seam, background = dm.seam_step(merged, mask, 12.0)
    assert seam <= background * 1.5

    naive = np.where(mask, fine, coarse)
    naive_seam, _bg = dm.seam_step(naive, mask, 12.0)
    assert naive_seam > DATUM, "проверка потеряла смысл: ступени нет"


def test_detail_is_kept_inside_the_area():
    """Внутри участка остаётся подробная съёмка, а не смесь."""
    truth, fine, coarse, mask = _scene()
    merged, _rep = dm.merge(fine, coarse, mask, width_px=12.0)
    err = np.abs(merged - truth)[mask]
    assert float(err.mean()) < 0.1


def test_holes_in_the_survey_are_closed_by_the_open_dem():
    """Дырка в подробной съёмке закрывается открытой ЦМР, а не остаётся."""
    _truth, fine, coarse, mask = _scene()
    fine = fine.copy()
    fine[140:150, 140:150] = np.nan               # прогал внутри участка
    merged, rep = dm.merge(fine, coarse, mask, width_px=12.0)
    assert np.isfinite(merged[140:150, 140:150]).all()
    assert rep["holes"] == 0


def test_no_overlap_is_reported_out_loud():
    """Съёмка обрывается по маске: поправку снять не по чему, и это сказано.

    Молчаливая сшивка в этом случае даёт ступень во весь датум, а человек
    узнаёт о ней, когда поток встанет на шве.
    """
    _truth, fine, coarse, mask = _scene()
    fine = np.where(mask, fine, np.nan)           # съёмка ровно по маске
    _z, rep = dm.merge(fine, coarse, mask, width_px=12.0)
    assert rep["overlap"] == 0
    assert "warning" in rep and "перекрытия" in rep["warning"]


def test_tilted_residual_is_taken_by_the_plane():
    """Наклонная невязка снимается плоскостью лучше, чем одним числом."""
    _truth, fine, coarse, mask = _scene(tilt=0.03)
    _z1, r_med = dm.merge(fine, coarse, mask, width_px=12.0,
                          shift_mode="median")
    _z2, r_pl = dm.merge(fine, coarse, mask, width_px=12.0,
                         shift_mode="plane")
    assert r_pl["mode"] == "plane" and r_med["mode"] == "median"
    assert abs(r_pl["after"]["p95"] - r_pl["after"]["p05"]) <= \
        abs(r_med["after"]["p95"] - r_med["after"]["p05"])


def test_shapes_must_match():
    fine = np.zeros((10, 10))
    coarse = np.zeros((10, 11))
    mask = np.zeros((10, 10), dtype=bool)
    try:
        dm.merge(fine, coarse, mask)
    except ValueError as exc:
        assert "сетке" in str(exc)
    else:
        raise AssertionError("несовпадение сеток должно быть отказом")
