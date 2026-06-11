# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), - либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии - в файле LICENSE (на английском, юридически значим).
"""
Конвейер построения изолиний и контурных полигонов из растра.

Линии: сглаживание ПОЛЯ (растра) -> gdal:contour -> фильтр коротких ->
лёгкое скругление линий (Chaikin) -> fixgeometries. Сглаживание поля (а не
отдельных линий) гарантирует, что изолинии не пересекаются даже в густых
местах; скругление линий поверх гладкого поля убирает «октагоны» от грубого
грида, не создавая пересечений.

Полигоны (контурные пояса): строятся НЕ полигонизацией классов растра (это
давало «ступеньки» по краю ячеек, не совпадающие с линиями), а полигонизацией
самих СГЛАЖЕННЫХ изолиний + контура валидной области:

    1) берём те же сглаженные изолинии, что и в линейном выходе;
    2) строим контур валидной области растра (footprint) - внешнюю и
       внутренние (вокруг дыр данных) границы;
    3) притягиваем концы изолиний к контуру (snap), объединяем и нодируем всю
       сеть (union), полигонизуем;
    4) каждый получившийся пояс относим к диапазону уровней ВЫБОРКОЙ растра в
       репрезентативной точке полигона (point-on-surface) - ELEV_MIN/ELEV_MAX.

Так границы полигонов совпадают с изолиниями, покрытие сплошное (без дыр),
а «ступеньки» исчезают.
"""
import math

from qgis.core import QgsProcessingException

DEFAULT_FIELD = "ELEV"
INDEX_FIELD = "is_index"


def _parse_levels(text):
    """Уровни через пробел; десятичный разделитель - запятая или точка."""
    out = []
    for tok in str(text).replace(";", " ").split():
        tok = tok.strip().replace(",", ".")   # «-400,5» -> -400.5
        if not tok:
            continue
        try:
            out.append(float(tok))
        except ValueError:
            raise QgsProcessingException("Не удалось разобрать уровень: %r" % tok)
    return sorted(set(out))


def compute_levels(raster, band, interval, base):
    """Уровни СТРОГО внутри диапазона растра (чтобы крайние пояса были
    непустыми и рисовались). Возвращает [] если диапазон меньше шага."""
    from osgeo import gdal
    ds = gdal.Open(raster)
    if ds is None:
        return []
    b = ds.GetRasterBand(int(band))
    try:
        mn, mx = b.ComputeRasterMinMax(False)
    except Exception:
        try:
            st = b.GetStatistics(True, True)
            mn, mx = st[0], st[1]
        except Exception:
            ds = None
            return []
    ds = None
    if not (mx > mn) or interval <= 0:
        return []
    k0 = int(math.floor((mn - base) / interval)) + 1
    k1 = int(math.ceil((mx - base) / interval)) - 1
    return [base + k * interval for k in range(k0, k1 + 1)]


def _extra_levels(levels):
    return "-fl " + " ".join(repr(float(v)) for v in levels)


def _gaussian_nodata(arr, valid, sigma):
    """Гаусово сглаживание поля с учётом nodata (значения за маской не
    «протекают» внутрь). Сепарабельная свёртка, край - повтор крайних ячеек.
    Чистый NumPy (без SciPy)."""
    import numpy as np
    r = max(1, int(math.ceil(3.0 * sigma)))
    xs = np.arange(-r, r + 1, dtype=float)
    k = np.exp(-(xs * xs) / (2.0 * sigma * sigma))
    k /= k.sum()
    v = np.where(valid, arr, 0.0).astype(float)
    m = valid.astype(float)

    def conv(a, axis):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (r, r)
        ap = np.pad(a, pad, mode="edge")
        out = np.zeros_like(a)
        for j, kk in enumerate(k):
            sl = [slice(None), slice(None)]
            sl[axis] = slice(j, j + a.shape[axis])
            out += kk * ap[tuple(sl)]
        return out

    vb = conv(conv(v, 1), 0)
    mb = conv(conv(m, 1), 0)
    return np.where(mb > 1e-6, vb / np.where(mb > 1e-6, mb, 1.0), arr)


def _smooth_raster(raster, band, sigma, nodata, feedback):
    """Возвращает путь к временному растру со сглаженным полем (та же маска
    валидных ячеек, та же геопривязка). Контуры сглаженного поля не
    пересекаются и плавны - это надёжнее, чем сглаживать каждую линию отдельно
    (последнее давало пересечения в густых местах и угловатость)."""
    from qgis.core import QgsProcessingUtils
    from osgeo import gdal
    import numpy as np
    import os
    import uuid

    ds = gdal.Open(raster)
    if ds is None:
        raise QgsProcessingException("Не удалось открыть растр для сглаживания.")
    b = ds.GetRasterBand(int(band))
    arr = b.ReadAsArray().astype(float)
    nd = b.GetNoDataValue()
    if nd is None:
        nd = nodata
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ny, nx = arr.shape

    valid = np.isfinite(arr)
    if nd is not None:
        valid &= (arr != nd)

    feedback.pushInfo("Сглаживание поля (σ=%g яч.)…" % sigma)
    sm = _gaussian_nodata(arr, valid, sigma)
    out_nd = float(nd) if nd is not None else -9999.0
    res = np.where(valid, sm, out_nd).astype(np.float32)

    tmp = os.path.join(QgsProcessingUtils.tempFolder(),
                       "smooth_%s.tif" % uuid.uuid4().hex)
    drv = gdal.GetDriverByName("GTiff")
    ods = drv.Create(tmp, nx, ny, 1, gdal.GDT_Float32)
    ods.SetGeoTransform(gt)
    if proj:
        ods.SetProjection(proj)
    ob = ods.GetRasterBand(1)
    ob.SetNoDataValue(out_nd)
    ob.WriteArray(res)
    ob.FlushCache()
    ods = None
    ds = None
    return tmp


def _prep_raster(raster, band, smooth, smooth_radius, nodata, feedback):
    """Готовит растр под контуринг: сглаженная копия (band 1) или оригинал.
    Один и тот же растр используется и для контура, и для контура области,
    и для выборки поясов - поэтому линии, полигоны и диапазоны согласованы."""
    if smooth and smooth_radius and smooth_radius > 0:
        return _smooth_raster(raster, band, float(smooth_radius), nodata,
                              feedback), 1
    return raster, band


def _save(processing, cur, final_output, context, feedback):
    if final_output and final_output != "TEMPORARY_OUTPUT":
        cur = processing.run("native:savefeatures", {
            "INPUT": cur,
            "OUTPUT": final_output,
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    return cur


def _contour_lines(processing, raster, band, interval, base, levels,
                   min_length, line_iter, field_name, ignore_nodata, nodata,
                   context, feedback):
    """Изолинии-линии (без флага is_index). Сглаживание поля (растра) делается
    до контуринга (см. _prep_raster) - это убирает пересечения. Дополнительно
    линии можно слегка СКРУГЛИТЬ (Chaikin, line_iter итераций): поле уже
    гладкое, контуры разнесены, поэтому скругление не создаёт пересечений, но
    убирает «октагоны» от грубого грида. Общее ядро для линий и для границ
    полигонов: геометрия гарантированно совпадает."""
    params = {
        "INPUT": raster, "BAND": band, "FIELD_NAME": field_name,
        "CREATE_3D": False, "IGNORE_NODATA": bool(ignore_nodata),
        "NODATA": nodata, "OFFSET": base, "OUTPUT": "TEMPORARY_OUTPUT",
    }
    if levels:
        params["INTERVAL"] = 0.0
        params["EXTRA"] = _extra_levels(levels)
    elif interval > 0:
        params["INTERVAL"] = float(interval)
    else:
        raise QgsProcessingException("Задайте шаг изолиний или уровни.")

    feedback.pushInfo("gdal:contour…")
    cur = processing.run("gdal:contour", params, context=context,
                         feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    if min_length and min_length > 0:
        feedback.pushInfo("Фильтр коротких линий (< %g)…" % min_length)
        cur = processing.run("native:extractbyexpression", {
            "INPUT": cur, "EXPRESSION": "$length >= %g" % float(min_length),
            "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    if line_iter and line_iter > 0:
        feedback.pushInfo("Скругление линий (Chaikin, %d итер.)…" % line_iter)
        cur = processing.run("native:smoothgeometry", {
            "INPUT": cur, "ITERATIONS": int(line_iter), "OFFSET": 0.25,
            "MAX_ANGLE": 180.0, "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    return processing.run("native:fixgeometries", {
        "INPUT": cur, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]


def _finalize_lines(processing, cur, interval, base, index_every, field_name,
                    final_output, context, feedback):
    """Доводит линии до выходного слоя: флаг главных изолиний (is_index) и
    сохранение. Общий хвост для линейного выхода (с полигонами и без)."""
    if index_every and index_every > 1 and interval > 0:
        feedback.pushInfo("Главные изолинии: каждая %d-я…" % index_every)
        step = float(interval) * int(index_every)
        expr = ('CASE WHEN abs(("{f}" - {b}) - round(("{f}" - {b})/{s})*{s})'
                ' < {tol} THEN 1 ELSE 0 END').format(
            f=field_name, b=base, s=step, tol=interval * 1e-4)
        return processing.run("native:fieldcalculator", {
            "INPUT": cur, "FIELD_NAME": INDEX_FIELD, "FIELD_TYPE": 1,
            "FIELD_LENGTH": 1, "FIELD_PRECISION": 0, "FORMULA": expr,
            "OUTPUT": final_output,
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    return _save(processing, cur, final_output, context, feedback)


def isolines_from_raster(raster, band, interval, base, levels_text,
                         index_every, min_length, smooth, smooth_radius,
                         line_iter, field_name, ignore_nodata, nodata,
                         final_output, context, feedback):
    """Изолинии-линии. levels_text (если задан) имеет приоритет над шагом.
    Сглаживание - на уровне поля; line_iter - лёгкое скругление линий."""
    from qgis import processing
    field_name = field_name or DEFAULT_FIELD
    levels = _parse_levels(levels_text) if levels_text else []
    rp, rb = _prep_raster(raster, band, smooth, smooth_radius, nodata, feedback)
    li = int(line_iter) if smooth else 0
    cur = _contour_lines(processing, rp, rb, interval, base, levels,
                         min_length, li, field_name, ignore_nodata, nodata,
                         context, feedback)
    return _finalize_lines(processing, cur, interval, base, index_every,
                           field_name, final_output, context, feedback)


# ---------------------------------------------------------------------------
#  Полигоны = пояса между изолиниями (границы совпадают с линиями, без дыр)
# ---------------------------------------------------------------------------
def _layer_from_string(s, context):
    """Загружает слой по строке-результату дочернего алгоритма (id/путь)."""
    if s is None:
        return None
    try:
        from qgis.core import QgsProcessingUtils
        lyr = QgsProcessingUtils.mapLayerFromString(s, context)
        return lyr if (lyr is not None and lyr.isValid()) else None
    except Exception:
        return None


def _footprint_lines(processing, raster, band, nodata, context, feedback):
    """Контур валидной области растра: полигонизуем маску валидных ячеек,
    объединяем (dissolve) и переводим в линии (внешняя + внутренние границы).
    Возвращает (lines_layer_string, geotransform, projection, array, valid)."""
    from qgis.core import QgsProcessingUtils
    from osgeo import gdal
    import os
    import uuid
    import numpy as np

    ds = gdal.Open(raster)
    if ds is None:
        raise QgsProcessingException("Не удалось открыть растр.")
    b = ds.GetRasterBand(int(band))
    arr = b.ReadAsArray().astype(float)
    nd = b.GetNoDataValue()
    if nd is None:
        nd = nodata
    gt = ds.GetGeoTransform()
    proj = ds.GetProjection()
    ny, nx = arr.shape

    valid = np.isfinite(arr)
    if nd is not None:
        valid &= (arr != nd)
    if not valid.any():
        raise QgsProcessingException("В растре нет валидных значений.")

    MASK_ND = 0
    mask = np.where(valid, 1, MASK_ND).astype(np.int32)
    tmp = os.path.join(QgsProcessingUtils.tempFolder(),
                       "mask_%s.tif" % uuid.uuid4().hex)
    drv = gdal.GetDriverByName("GTiff")
    ods = drv.Create(tmp, nx, ny, 1, gdal.GDT_Int32)
    ods.SetGeoTransform(gt)
    if proj:
        ods.SetProjection(proj)
    ob = ods.GetRasterBand(1)
    ob.SetNoDataValue(MASK_ND)
    ob.WriteArray(mask)
    ob.FlushCache()
    ods = None
    ds = None

    feedback.pushInfo("Контур валидной области…")
    fp = processing.run("gdal:polygonize", {
        "INPUT": tmp, "BAND": 1, "FIELD": "DN",
        "EIGHT_CONNECTEDNESS": False, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    fp = processing.run("native:dissolve", {
        "INPUT": fp, "FIELD": [], "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    lines = processing.run("native:polygonstolines", {
        "INPUT": fp, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    return lines, gt, proj, arr, valid


def _sample_value(arr, valid, gt, x, y, max_r=4):
    """Значение растра в точке (x, y) с поиском ближайшей валидной ячейки."""
    ny, nx = arr.shape
    px, py = gt[1], gt[5]
    if px == 0 or py == 0:
        return None
    col = int(math.floor((x - gt[0]) / px))
    row = int(math.floor((y - gt[3]) / py))
    if 0 <= row < ny and 0 <= col < nx and valid[row, col]:
        return float(arr[row, col])
    # поиск ближайшей валидной ячейки в небольшом окне
    for r in range(1, max_r + 1):
        r0, r1 = max(0, row - r), min(ny, row + r + 1)
        c0, c1 = max(0, col - r), min(nx, col + r + 1)
        sub_v = valid[r0:r1, c0:c1]
        if sub_v.any():
            sub_a = arr[r0:r1, c0:c1]
            return float(sub_a[sub_v][0])
    return None


def _polygonize_belts(processing, lines_layer, area_lines, crs, context,
                      feedback):
    """Строит замкнутые пояса из набора линий + контура области.

    Узлует всю сеть через native:splitwithlines (надёжно режет и T-стыки, где
    конец изолинии упирается в контур - этого native:union на линиях не делал),
    затем полигонизует.
    """
    merged = processing.run("native:mergevectorlayers", {
        "LAYERS": [lines_layer, area_lines],
        "CRS": crs, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    feedback.pushInfo("Нодирование сети линий…")
    noded = processing.run("native:splitwithlines", {
        "INPUT": merged, "LINES": merged, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    feedback.pushInfo("Полигонизация поясов…")
    return processing.run("native:polygonize", {
        "INPUT": noded, "KEEP_FIELDS": False, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]


def _belts_to_layer(processing, polys_src, arr, valid, gt, levels, crs,
                    final_output, context, feedback):
    """Каждому поясу присваивает диапазон уровней выборкой растра в
    репрезентативной точке (point-on-surface) и сохраняет слой."""
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsFields, QgsField, QgsGeometry)
    from qgis.PyQt.QtCore import QVariant
    import numpy as np

    poly_layer = _layer_from_string(polys_src, context)
    if poly_layer is None:
        raise QgsProcessingException(
            "Полигонизация не дала результата (проверьте изолинии/контур).")

    vals = arr[valid]
    # rmin/rmax расширяем до крайних уровней, чтобы mins/maxs были
    # монотонны (поле могло слегка сузиться при сглаживании)
    rmin = min(float(vals.min()), float(levels[0]))
    rmax = max(float(vals.max()), float(levels[-1]))
    mins = np.asarray([rmin] + list(levels), float)
    maxs = np.asarray(list(levels) + [rmax], float)
    lv = np.asarray(levels, float)

    fields = QgsFields()
    fields.append(QgsField("ELEV_MIN", QVariant.Double, len=20, prec=6))
    fields.append(QgsField("ELEV_MAX", QVariant.Double, len=20, prec=6))
    mem = QgsVectorLayer(
        "MultiPolygon?crs=%s" % (crs.authid() or crs.toWkt()),
        "пояса", "memory")
    dp = mem.dataProvider()
    dp.addAttributes(fields.toList())
    mem.updateFields()

    feedback.pushInfo("Назначение диапазонов поясам…")
    out_feats = []
    n_total = max(poly_layer.featureCount(), 1)
    for i, feat in enumerate(poly_layer.getFeatures()):
        g = feat.geometry()
        if g is None or g.isEmpty():
            continue
        rep = g.pointOnSurface()
        if rep is None or rep.isEmpty():
            rep = g.centroid()
        if rep is None or rep.isEmpty():
            continue
        p = rep.asPoint()
        val = _sample_value(arr, valid, gt, p.x(), p.y())
        if val is None:
            continue                       # точка вне валидной области - пропуск
        idx = int(np.digitize([val], lv)[0])     # 0..len(levels)
        nf = QgsFeature(mem.fields())
        gg = QgsGeometry(g)
        gg.convertToMultiType()
        nf.setGeometry(gg)
        nf.setAttributes([float(mins[idx]), float(maxs[idx])])
        out_feats.append(nf)
        if i % 200 == 0:
            feedback.setProgress(int(100.0 * i / n_total))
    if not out_feats:
        raise QgsProcessingException("Ни один пояс не получил значения.")
    dp.addFeatures(out_feats)
    mem.updateExtents()
    return _save(processing, mem, final_output, context, feedback)


def isolines_and_polygons(raster, band, interval, base, levels_text,
                          index_every, min_length, smooth, smooth_radius,
                          line_iter, field_name, ignore_nodata, nodata,
                          lines_output, polygons_output, context, feedback):
    """Изолинии И контурные пояса из ОДНОГО набора линий.

    Сглаживание выполняется один раз на уровне поля (растра); этот же
    сглаженный растр используется для контура, контура области и выборки
    поясов - поэтому линии, границы полигонов и диапазоны согласованы.

    Против «расхождения по краям»: и линейный выход, и границы поясов строятся
    из одного набора линий, у которого концы заранее притянуты к контуру
    области (snap, ТОЛЬКО концевые точки - форма изолиний не меняется).
    """
    from qgis import processing
    from qgis.core import QgsCoordinateReferenceSystem

    field_name = field_name or DEFAULT_FIELD

    # сглаживаем поле один раз; дальше всё работает по prepared-растру
    rp, rb = _prep_raster(raster, band, smooth, smooth_radius, nodata, feedback)

    levels = _parse_levels(levels_text) if levels_text else []
    if not levels:
        levels = compute_levels(rp, rb, interval, base)
    if not levels:
        raise QgsProcessingException("Недостаточно уровней для полигонов.")
    levels = sorted(levels)

    # 1) контур валидной области + массив растра (для выборки поясов)
    area_lines, gt, proj, arr, valid = _footprint_lines(
        processing, rp, rb, nodata, context, feedback)
    px = abs(gt[1]) or 1.0
    crs = QgsCoordinateReferenceSystem()
    if proj:
        crs.createFromWkt(proj)

    # 2) изолинии (ядро) -> притягиваем ТОЛЬКО концы к контуру
    li = int(line_iter) if smooth else 0
    iso = _contour_lines(processing, rp, rb, interval, base, levels,
                         min_length, li, field_name, ignore_nodata, nodata,
                         context, feedback)
    feedback.pushInfo("Согласование концов изолиний с контуром…")
    iso = processing.run("native:snapgeometries", {
        "INPUT": iso, "REFERENCE_LAYER": area_lines,
        "TOLERANCE": float(3.0 * px), "BEHAVIOR": 5,
        "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    # 3) линейный выход - из ЭТИХ же согласованных линий
    lines_out = _finalize_lines(processing, iso, interval, base, index_every,
                                field_name, lines_output, context, feedback)

    # 4) пояса - из тех же линий + контур; границы совпадают с линиями
    polys_src = _polygonize_belts(processing, iso, area_lines, crs, context,
                                  feedback)
    polys_out = _belts_to_layer(processing, polys_src, arr, valid, gt, levels,
                                crs, polygons_output, context, feedback)

    return {"lines": lines_out, "polygons": polys_out}
