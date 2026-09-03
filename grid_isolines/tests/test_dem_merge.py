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


# --- охват и разрешение результата ---------------------------------------

def test_grid_covers_the_extent_whole_cells():
    """Сетка накрывает охват целым числом ячеек, начало в левом верхнем."""
    gt, nx, ny = dm.grid_from_extent(100.0, 200.0, 350.0, 400.0, 10.0)
    assert (nx, ny) == (25, 20)
    assert gt[0] == 100.0 and gt[1] == 10.0 and gt[5] == -10.0
    assert gt[3] == 400.0                       # верх = ymin + ny*cell
    # неровный охват округляется вверх, иначе полоса справа осталась бы вне
    _gt, nx2, ny2 = dm.grid_from_extent(0.0, 0.0, 95.0, 91.0, 10.0)
    assert (nx2, ny2) == (10, 10)


def test_grid_refuses_nonsense():
    for args in ((0, 0, 10, 10, 0.0), (0, 0, 0, 10, 1.0), (0, 0, 10, 0, 1.0)):
        try:
            dm.grid_from_extent(*args)
        except ValueError:
            pass
        else:
            raise AssertionError("ожидался отказ на %r" % (args,))


def test_cell_count_catches_the_impossible_raster():
    """Сантиметровая съёмка на региональном охвате даёт триллион ячеек.

    Это и есть случай, ради которого ячейка результата вынесена в
    параметры: подробная съёмка бывает сантиметровой, региональная -
    тридцатиметровой на десятки километров.
    """
    huge = dm.cells_for(0, 0, 50000, 50000, 0.05)
    assert huge > 1e11
    assert dm.cells_for(0, 0, 50000, 50000, 5.0) == 100_000_000
    assert dm.cells_for(0, 0, 50000, 50000, 30.0) < 3e6


def test_coarsening_averages_rather_than_samples():
    """При укрупнении ячейки поверхность осредняется, а не выбирается.

    Билинейная выборка берёт значение в узле и теряет всё между узлами -
    на подробной съёмке это и есть сама подробность.
    """
    assert dm.resample_rule(0.05, 5.0) == "average"
    assert dm.resample_rule(30.0, 5.0) == "bilinear"
    assert dm.resample_rule(5.0, 5.0) == "bilinear"
    assert dm.resample_rule(5.0, 7.0) == "bilinear"    # без запаса не грубим
    assert dm.resample_rule(5.0, 8.0) == "average"


def test_service_value_instead_of_a_hole_ruins_the_graft():
    """Пустота, попавшая в расчёт числом, губит поправку целиком.

    У ЦМР пустоты помечены служебным значением: -32768, -9999, ноль. Если
    не передать его при переносе на сетку результата, оно идёт в расчёт
    как настоящая отметка. Кольцо перекрытия «находит» данные там, где их
    нет, расхождение считается по мусору, и поправка уезжает в
    бессмыслицу: со стороны выглядит, будто растр просто вставлен без
    перехода.

    Проверка держит числа, ради которых обёртка обязана передавать
    пустоту явно.
    """
    rng = np.random.default_rng(5)
    ny = nx = 200
    y, x = np.mgrid[0:ny, 0:nx]
    truth = 0.05 * x + 20 * np.sin(x / 60.0) * np.cos(y / 50.0)
    survey = np.zeros((ny, nx), dtype=bool)
    survey[60:150, 60:150] = True
    mask = np.zeros((ny, nx), dtype=bool)
    mask[65:145, 65:145] = True
    coarse = truth + DATUM + rng.normal(0, 0.3, (ny, nx))

    clean = np.where(survey, truth, np.nan)
    dirty = np.where(survey, truth, -32768.0)

    _z1, r_clean = dm.merge(clean, coarse, mask, width_px=10.0,
                            shift_mode="plane")
    _z2, r_dirty = dm.merge(dirty, coarse, mask, width_px=10.0,
                            shift_mode="plane")
    assert abs(r_clean["median"] + DATUM) < 1.0
    assert abs(r_dirty["median"]) > 1000.0, \
        "проверка потеряла смысл: мусор не влияет"


# --- обёртка QGIS: устройство переноса на сетку результата ---------------
#
# Сам перенос без QGIS не запустить, поэтому проверяется код класса:
# обе правки держатся на нескольких строках, которые легко потерять.

def _graft_source():
    import ast
    src = io_open(os.path.join(ROOT, "algorithms.py"))
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) \
                and node.name == "SurfaceGraftAlgorithm":
            return ast.get_source_segment(src, node)
    raise AssertionError("класс SurfaceGraftAlgorithm не найден")


def io_open(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_wrapper_passes_the_source_nodata():
    """Пустота источника передаётся при переносе на сетку результата."""
    body = _graft_source()
    assert "GetNoDataValue()" in body
    assert "srcNodata" in body


def test_wrapper_clips_to_the_target_frame():
    """Обе поверхности режутся по рамке результата, а не только сдвигаются.

    Раньше подробная ЦМР приходила целиком: рамку задали, а обрезки не
    было, и за её краем оставались данные.
    """
    body = _graft_source()
    assert "outputBounds" in body
    assert "xRes" in body and "yRes" in body


def test_three_modes_differ_on_a_tilted_offset():
    """Медиана, плоскость и «не снимать» дают три разных ответа.

    Проверка держит две вещи сразу. Первая: режимы вообще доходят до
    расчёта. Вторая: «не снимать» действительно не снимает - раньше он
    уходил в ветку медианы и молча правил высоту.
    """
    rng = np.random.default_rng(5)
    ny = nx = 200
    y, x = np.mgrid[0:ny, 0:nx]
    truth = 0.05 * x + 20 * np.sin(x / 60.0) * np.cos(y / 50.0)
    survey = np.zeros((ny, nx), dtype=bool)
    survey[60:150, 60:150] = True
    mask = np.zeros((ny, nx), dtype=bool)
    mask[70:140, 70:140] = True
    fine = np.where(survey, truth, np.nan)
    coarse = truth + 12.0 + 0.05 * x + rng.normal(0, 0.2, (ny, nx))

    out, rep = {}, {}
    for mode in ("median", "plane", "none"):
        out[mode], rep[mode] = dm.merge(fine, coarse, mask, width_px=10.0,
                                        shift_mode=mode)
        assert rep[mode]["mode"] == mode, mode

    assert np.nanmax(np.abs(out["median"] - out["plane"])) > 1.0
    assert np.nanmax(np.abs(out["median"] - out["none"])) > 5.0
    # «не снимать» оставляет расхождение как есть
    assert abs(rep["none"]["after"]["median"] - rep["none"]["median"]) < 1e-9
    assert rep["none"]["corr_min"] == 0.0 and rep["none"]["corr_max"] == 0.0


def test_constant_offset_makes_median_and_plane_agree():
    """На постоянной невязке плоскость вырождается в константу.

    Это не ошибка и не повод искать разницу: одинаковый ответ у двух
    режимов означает, что расхождение по площади не меняется.
    """
    rng = np.random.default_rng(8)
    ny = nx = 200
    y, x = np.mgrid[0:ny, 0:nx]
    truth = 15 * np.sin(x / 55.0) + 10 * np.cos(y / 45.0)
    survey = np.zeros((ny, nx), dtype=bool)
    survey[60:150, 60:150] = True
    mask = np.zeros((ny, nx), dtype=bool)
    mask[70:140, 70:140] = True
    fine = np.where(survey, truth, np.nan)
    coarse = truth + DATUM + rng.normal(0, 0.2, (ny, nx))
    a, _ra = dm.merge(fine, coarse, mask, width_px=10.0, shift_mode="median")
    b, _rb = dm.merge(fine, coarse, mask, width_px=10.0, shift_mode="plane")
    assert np.nanmax(np.abs(a - b)) < 0.1
