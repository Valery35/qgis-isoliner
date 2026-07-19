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
from contextlib import suppress

from qgis.core import QgsProcessingException

from .i18n import tr as _tr  # двуязычие RU/EN

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
            raise QgsProcessingException(_tr("Не удалось разобрать уровень: %r") % tok)
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
        raise QgsProcessingException(_tr("Не удалось открыть растр для сглаживания."))
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

    feedback.pushInfo(_tr("Сглаживание поля (σ=%g яч.)…") % sigma)
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


def _cubic_resample_matrix(n, p):
    """Матрица (n*p x n) кубической интерполяции (свёртка Кейса, a=-0.5), край -
    повтор крайних ячеек. Узел выхода o -> позиция входа (o+0.5)/p - 0.5."""
    import numpy as np
    o = np.arange(n * p)
    src = (o + 0.5) / p - 0.5
    i0 = np.floor(src).astype(int)
    frac = src - i0
    a = -0.5
    W = np.zeros((n * p, n), float)
    for m in (-1, 0, 1, 2):
        t = np.abs(frac - m)
        w = np.where(t <= 1, (a + 2) * t**3 - (a + 3) * t**2 + 1,
                     np.where(t < 2, a * t**3 - 5 * a * t**2 + 8 * a * t - 4 * a,
                              0.0))
        idx = np.clip(i0 + m, 0, n - 1)
        W[o, idx] += w
    return W


def _fill_invalid(arr, valid, iters):
    """Растит валидные значения в nodata (среднее 4-соседей, iters итераций).
    Запас под кубику, чтобы у границы данных не было «звона» (Гиббса)."""
    import numpy as np
    a = np.where(valid, arr, 0.0).astype(float)
    v = valid.copy()
    for _ in range(int(iters)):
        if v.all():
            break
        s = np.zeros_like(a)
        c = np.zeros_like(a)
        for sh in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            s += np.roll(np.where(v, a, 0.0), sh, axis=(0, 1))
            c += np.roll(v.astype(float), sh, axis=(0, 1))
        nv = (~v) & (c > 0)
        a[nv] = s[nv] / c[nv]
        v |= nv
    return a


def _densify_raster(raster, band, factor, nodata, feedback):
    """Путь к временному растру, сгущённому в `factor` раз бикубической
    интерполяцией. Чистый NumPy - без SciPy и без Processing-алгоритмов, чтобы
    одинаково работать в QGIS 3 и 4 (ресемплинг через gdalwarp/native менялся
    между версиями). Контуры по сгущённому полю гладки топологически чисто;
    маска nodata ресемплится ближайшим соседом - футпринт данных не расползается
    и звона у границы нет."""
    from qgis.core import QgsProcessingUtils
    from osgeo import gdal
    import numpy as np
    import os
    import uuid

    p = int(factor)
    ds = gdal.Open(raster)
    if ds is None:
        raise QgsProcessingException(_tr("Не удалось открыть растр для сгущения."))
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
        ds = None
        return raster

    feedback.pushInfo(_tr("Сгущение грида ×%d (бикубика)…") % p)
    filled = _fill_invalid(arr, valid, iters=3)
    filled = np.where(np.isfinite(filled), filled, float(np.mean(arr[valid])))
    dense = _cubic_resample_matrix(ny, p) @ filled @ _cubic_resample_matrix(nx, p).T

    def nearest(n):
        o = np.arange(n * p)
        return np.clip(np.round((o + 0.5) / p - 0.5).astype(int), 0, n - 1)
    vmask = valid[np.ix_(nearest(ny), nearest(nx))]

    out_nd = float(nd) if nd is not None else -9999.0
    res = np.where(vmask, dense, out_nd).astype(np.float32)
    gt2 = (gt[0], gt[1] / p, gt[2] / p, gt[3], gt[4] / p, gt[5] / p)

    tmp = os.path.join(QgsProcessingUtils.tempFolder(),
                       "densify_%s.tif" % uuid.uuid4().hex)
    drv = gdal.GetDriverByName("GTiff")
    ods = drv.Create(tmp, nx * p, ny * p, 1, gdal.GDT_Float32)
    ods.SetGeoTransform(gt2)
    if proj:
        ods.SetProjection(proj)
    ob = ods.GetRasterBand(1)
    ob.SetNoDataValue(out_nd)
    ob.WriteArray(res)
    ob.FlushCache()
    ods = None
    ds = None
    return tmp


def _prep_raster(raster, band, smooth, smooth_radius, densify, nodata, feedback):
    """Готовит растр под контуринг: при необходимости сглаженная копия (Гаусс),
    затем при необходимости сгущённая бикубикой. Один и тот же растр идёт и на
    контур, и на контур области, и на выборку поясов - поэтому линии, полигоны и
    диапазоны согласованы."""
    cur, cb = raster, band
    if smooth and smooth_radius and smooth_radius > 0:
        cur, cb = _smooth_raster(cur, cb, float(smooth_radius), nodata,
                                 feedback), 1
    if densify and int(densify) > 1:
        cur, cb = _densify_raster(cur, cb, int(densify), nodata, feedback), 1
    return cur, cb


def _save(processing, cur, final_output, context, feedback):
    if final_output and final_output != "TEMPORARY_OUTPUT":
        cur = processing.run("native:savefeatures", {
            "INPUT": cur,
            "OUTPUT": final_output,
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    return cur


def _mark_confidence(cur, raster, band, interval, frac, cut, context,
                     feedback, min_run=3):
    """Отметить участки, где положение горизонтали задано шумом, а не рельефом.

    Мерится не уклон, а перепад высот на ячейку: величина той же размерности,
    что и сечение, поэтому порог задаётся долей сечения и одинаково работает
    на пологом склоне и на водной глади. Пологий склон при сечении 0.5 м даёт
    на ячейке сантиметры, гладь миллиметры.

    Два режима. Без cut линия остаётся целой и получает поля drop_min и
    drop_mean, решение за человеком. С cut линия дополнительно рвётся на
    границе подозрительного участка, и куски помечаются полем lowconf.
    Ничего не удаляется: показывать или прятать, решает фильтр слоя.

    Рвут только серии от min_run подряд идущих вершин, иначе одна случайная
    ячейка крошила бы горизонталь на нормальном склоне.
    """
    from qgis.core import (QgsVectorLayer, QgsFeature, QgsFields, QgsField,
                           QgsGeometry)
    from qgis.PyQt.QtCore import QVariant
    import numpy as np
    from osgeo import gdal
    from . import validate_core as _vc
    from . import section_core as _sc

    lyr = _layer_from_string(cur, context)
    if lyr is None:
        return cur
    ds = gdal.Open(raster)
    if ds is None:
        return cur
    b = ds.GetRasterBand(int(band))
    arr = b.ReadAsArray().astype("float32")
    nd = b.GetNoDataValue()
    gt = ds.GetGeoTransform()
    ds = None
    if nd is not None:
        arr = np.where(arr == nd, np.nan, arr)
    cell = abs(gt[1]) or 1.0
    drop = _vc.drop_per_cell(arr, cell)
    thr = float(frac) * float(interval)

    fields = QgsFields(lyr.fields())
    fields.append(QgsField("drop_min", QVariant.Double))
    fields.append(QgsField("drop_mean", QVariant.Double))
    if cut:
        fields.append(QgsField("lowconf", QVariant.Int))
    crs = lyr.crs()
    mem = QgsVectorLayer("LineString?crs=%s" % (crs.authid() or crs.toWkt()),
                         _tr("изолинии"), "memory")
    dp = mem.dataProvider()
    dp.addAttributes(fields.toList())
    mem.updateFields()

    n_low = n_parts = n_src = 0
    total_len = low_len = 0.0
    out = []
    for ft in lyr.getFeatures():
        g = ft.geometry()
        if g is None or g.isEmpty():
            continue
        n_src += 1
        for part in (g.asMultiPolyline() if g.isMultipart()
                     else [g.asPolyline()]):
            if len(part) < 2:
                continue
            xs = np.array([p.x() for p in part], dtype=float)
            ys = np.array([p.y() for p in part], dtype=float)
            d = _sc.sample_grid_points(drop, gt, xs, ys, True)
            info = _vc.line_confidence(d, thr)
            if info is None:
                continue
            flags = ~(np.isfinite(d) & (d >= thr))
            if not cut:
                fa = QgsFeature(fields)
                fa.setGeometry(QgsGeometry.fromPolylineXY(part))
                fa.setAttributes(list(ft.attributes())
                                 + [info["drop_min"], info["drop_mean"]])
                out.append(fa)
                total_len += fa.geometry().length()
                if info["n_low"]:
                    low_len += fa.geometry().length() * info["n_low"] / len(d)
                continue
            keep, cutr = _vc.confident_runs(flags, min_run)
            for (rng, low) in ([(r, 0) for r in keep] + [(r, 1) for r in cutr]):
                i0, i1 = rng
                sub = part[i0:i1 + 1]
                if len(sub) < 2:
                    continue
                dd = d[i0:i1 + 1]
                si = _vc.line_confidence(dd, thr) or info
                fa = QgsFeature(fields)
                fa.setGeometry(QgsGeometry.fromPolylineXY(sub))
                fa.setAttributes(list(ft.attributes())
                                 + [si["drop_min"], si["drop_mean"], int(low)])
                out.append(fa)
                n_parts += 1
                ln = fa.geometry().length()
                total_len += ln
                if low:
                    n_low += 1
                    low_len += ln
    if not out:
        return cur
    dp.addFeatures(out)
    mem.updateExtents()

    feedback.pushInfo(_tr("Уверенность горизонталей: порог перепада %.4g м на "
                          "ячейку (%.3g сечения).") % (thr, frac))
    if cut:
        feedback.pushInfo(_tr(
            "Линий на входе %d, кусков на выходе %d, из них ниже шума %d. "
            "Доля длины ниже шума %.3g процента.")
            % (n_src, n_parts, n_low,
               100.0 * low_len / total_len if total_len > 0 else 0.0))
        if n_low:
            feedback.pushInfo(_tr(
                "Куски помечены полем lowconf = 1. Ничего не удалено, "
                "спрячьте их фильтром слоя, если мешают."))
    else:
        feedback.pushInfo(_tr(
            "Записаны поля drop_min и drop_mean. Подозрительные участки "
            "видно выражением drop_min < %.4g.") % thr)
    context.temporaryLayerStore().addMapLayer(mem)
    return mem.id()


def _warn_flat_levels(raster, band, interval, base, levels, feedback,
                      max_cells=60_000_000):
    """Предупредить, если уровень сечения попал в плоскую площадку.

    Изолиния на площадке с околонулевым уклоном задаётся не рельефом, а шумом:
    она рассыпается на множество мелких колец и выглядит как одна утолщённая
    линия. Классический источник - водная гладь. Диагностика идёт за один
    проход по массиву и никогда не роняет построение: не получилось - молча
    строим дальше.

    Числа выводятся на ЭКРАН: на удалённых машинах заказчика это единственный
    доступный канал, файл-лог оттуда не забрать.
    """
    try:
        import numpy as np
        from osgeo import gdal
        from . import validate_core as _vc

        ds = gdal.Open(raster)
        if ds is None:
            return
        if ds.RasterXSize * ds.RasterYSize > max_cells:
            ds = None
            return
        gt = ds.GetGeoTransform()
        b = ds.GetRasterBand(int(band))
        arr = b.ReadAsArray().astype("float32")
        nd = b.GetNoDataValue()
        ds = None
        if nd is not None:
            arr = np.where(arr == nd, np.nan, arr)
        cell = abs(gt[1]) or 1.0

        lv = list(levels) if levels else compute_levels(raster, band,
                                                        interval, base)
        if not lv:
            return
        step = float(interval) if interval and interval > 0 else (
            min(np.diff(sorted(lv))) if len(lv) > 1 else 0.0)
        if step <= 0:
            return
        hits = _vc.flat_level_hits(arr, cell, lv, step)
        if not hits:
            return
        worst = hits[0]
        if worst["share_valid"] < 0.005:
            return
        feedback.pushWarning(_tr(
            "Уровень %.4g проходит по площадке с околонулевым уклоном: "
            "задето %d ячеек, это %.3g процента данных. Изолиния там "
            "рассыпется на мелкие кольца и будет выглядеть утолщённой.")
            % (worst["level"], worst["n_flat"], 100.0 * worst["share_valid"]))
        for h in hits[1:3]:
            if h["share_valid"] >= 0.005:
                feedback.pushInfo(_tr(
                    "То же на уровне %.4g: ячеек %d, %.3g процента.")
                    % (h["level"], h["n_flat"], 100.0 * h["share_valid"]))
        feedback.pushInfo(_tr(
            "Обычно это водная гладь или залитая площадка. Замаскируйте её до "
            "построения либо сместите уровни: урез это отдельный объект, а не "
            "горизонталь."))
    except Exception as exc:
        # Диагностика не имеет права ронять построение: она справочная.
        # Сообщить о пропуске пытаемся, но если и журнал уже недоступен,
        # то писать всё равно некуда, и это не повод прерывать расчёт.
        with suppress(Exception):
            feedback.pushInfo(_tr("Проверка плоских площадок пропущена: %s")
                              % exc)


def _level_step(levels):
    """Шаг по списку уровней: нужен, когда шаг не задан явно."""
    import numpy as np
    lv = sorted(float(v) for v in (levels or []))
    if len(lv) < 2:
        return 0.0
    d = np.diff(lv)
    d = d[d > 0]
    return float(np.min(d)) if d.size else 0.0


def _contour_lines(processing, raster, band, interval, base, levels,
                   min_length, line_iter, field_name, ignore_nodata, nodata,
                   context, feedback, confidence=0, conf_frac=0.01):
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
        raise QgsProcessingException(_tr("Задайте шаг изолиний или уровни."))

    _warn_flat_levels(raster, band, interval, base, levels, feedback)

    feedback.pushInfo("gdal:contour…")
    cur = processing.run("gdal:contour", params, context=context,
                         feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    # Уверенность считаем ДО фильтра коротких линий: иначе обрывки, оставшиеся
    # от разрезания, попали бы в статистику длин и часть выкинулась бы дважды
    # по разным причинам.
    if confidence:
        cur = _mark_confidence(cur, raster, band,
                               interval if interval > 0 else _level_step(levels),
                               conf_frac, confidence >= 2, context, feedback)

    if min_length and min_length > 0:
        feedback.pushInfo(_tr("Фильтр коротких линий (< %g)…") % min_length)
        cur = processing.run("native:extractbyexpression", {
            "INPUT": cur, "EXPRESSION": "$length >= %g" % float(min_length),
            "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    if line_iter and line_iter > 0:
        feedback.pushInfo(_tr("Скругление линий (Chaikin, %d итер.)…") % line_iter)
        cur = processing.run("native:smoothgeometry", {
            "INPUT": cur, "ITERATIONS": int(line_iter), "OFFSET": 0.25,
            "MAX_ANGLE": 180.0, "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    return processing.run("native:fixgeometries", {
        "INPUT": cur, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]


def _add_slope_side(processing, cur, slope_ref, context, feedback, flip=0):
    """Добавляет поле dn_sign для бергштрихов. Знак выбран так, чтобы в стиле
    смещение offset = @dn_sign * ширина клало штрих на сторону склона ВНИЗ
    (с учётом того, что в QGIS положительное смещение уходит влево от
    направления линии). 0 у края, где значение не прочиталось. Считается
    сэмплированием исходного растра по обе стороны линии в её середине."""
    rid, band, eps = slope_ref
    # Про знак смещения. Была гипотеза, что в QGIS 4 сторона смещения
    # перевернулась относительно QGIS 3, и код переворачивал dn_sign по номеру
    # версии. Проверка на живой машине с QGIS 4.0.3 показала обратное: с
    # переворотом штрихи легли вверх по склону, без переворота верно. Значит
    # четвёрка ведёт себя как тройка, и угадывание по версии убрано.
    # Переключатель оставлен: сборки бывают разные, а увидеть неверную сторону
    # на карте можно за секунду, тогда как вычислить её из API нельзя.
    ver = 0
    try:
        from qgis.core import Qgis
        ver = int(Qgis.versionInt())
    except Exception:
        ver = 0
    if flip > 0:
        _flip, how = 1, _tr("как есть (задано вручную)")
    elif flip < 0:
        _flip, how = -1, _tr("зеркально (задано вручную)")
    else:
        _flip = 1
        how = _tr("автоматически")
    feedback.pushInfo(_tr(
        "Бергштрихи: сторона выбрана %s, версия QGIS %d, знак %+d. Если "
        "штрихи смотрят вверх по склону, переключите параметр вручную.")
        % (how, ver, _flip))
    neg, pos = -1 * _flip, 1 * _flip
    expr = (
        "with_variable('p', line_interpolate_point($geometry, $length/2.0),"
        " with_variable('a', line_interpolate_angle($geometry, $length/2.0),"
        "  with_variable('vr', raster_value('{rid}', {b}, "
        "project(@p, {e}, radians(@a + 90))),"
        "   with_variable('vl', raster_value('{rid}', {b}, "
        "project(@p, {e}, radians(@a - 90))),"
        "    CASE WHEN @vr IS NULL OR @vl IS NULL THEN 0"
        "     WHEN @vr < @vl THEN {neg} ELSE {pos} END))))"
    ).format(rid=rid, b=int(band), e=float(eps), neg=neg, pos=pos)
    feedback.pushInfo(_tr("Сторона склона (dn_sign) для бергштрихов…"))
    return processing.run("native:fieldcalculator", {
        "INPUT": cur, "FIELD_NAME": "dn_sign", "FIELD_TYPE": 1,
        "FIELD_LENGTH": 2, "FIELD_PRECISION": 0, "FORMULA": expr,
        "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]


def _orient_uphill(processing, cur, slope_ref, context, feedback):
    """Разворачивает горизонтали так, чтобы верх подписи смотрел вверх по склону.

    Это и есть топографическая подпись. QGIS ставит подпись вдоль линии и
    отсчитывает верх текста от направления линии, поэтому достаточно задать
    линиям одно направление относительно склона.

    ПРОВЕРЕНО НА ЖИВОЙ МАШИНЕ (QGIS 4.0.3): верх подписи смотрит в сторону
    ВЫСОКОЙ стороны, когда высокая сторона находится СПРАВА от направления
    линии. Первоначальное предположение было обратным, и подписи вставали
    вверх ногами, то есть в сторону убывания.

    Растр опрашивается по обе стороны линии в её середине, как и для
    бергштрихов. Поле up_side остаётся в слое: 1 означает, что линия оставлена
    как была, 0 что развёрнута. По нему видно, что инструмент сделал.

    Бергштрихи от этого не страдают: сторона склона считается ПОСЛЕ разворота
    и подстраивается сама.
    """
    rid, band, eps = slope_ref
    expr = (
        "with_variable('p', line_interpolate_point($geometry, $length/2.0),"
        " with_variable('a', line_interpolate_angle($geometry, $length/2.0),"
        "  with_variable('vr', raster_value('{rid}', {b}, "
        "project(@p, {e}, radians(@a + 90))),"
        "   with_variable('vl', raster_value('{rid}', {b}, "
        "project(@p, {e}, radians(@a - 90))),"
        "    CASE WHEN @vr IS NULL OR @vl IS NULL THEN 1"
        "     WHEN @vr >= @vl THEN 1 ELSE 0 END))))"
    ).format(rid=rid, b=int(band), e=float(eps))
    feedback.pushInfo(_tr("Ориентация горизонталей вверх по склону…"))
    cur = processing.run("native:fieldcalculator", {
        "INPUT": cur, "FIELD_NAME": "up_side", "FIELD_TYPE": 1,
        "FIELD_LENGTH": 1, "FIELD_PRECISION": 0, "FORMULA": expr,
        "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    # разворачиваем те линии, у которых высокая сторона оказалась слева
    return processing.run("native:geometrybyexpression", {
        "INPUT": cur, "OUTPUT_GEOMETRY": 1, "WITH_Z": False, "WITH_M": False,
        "EXPRESSION": 'if("up_side" = 1, $geometry, reverse($geometry))',
        "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]


def _finalize_lines(processing, cur, interval, base, index_every, field_name,
                    final_output, context, feedback, slope_ref=None,
                    uphill_ref=None, hatch_flip=0):
    """Доводит линии до выходного слоя: сторона склона (dn_sign, опционально),
    флаг главных изолиний (is_index) и сохранение. Общий хвост для линейного
    выхода (с полигонами и без)."""
    # ВАЖЕН ПОРЯДОК: сначала разворот, потом сторона склона. Разворот меняет
    # направление линии, а значит меняет местами лево и право, и посчитанный
    # заранее dn_sign у развёрнутых линий указывал бы на противоположную
    # сторону - бергштрихи легли бы вверх по склону. Проявляется только при
    # одновременно включённых депрессионном стиле и топографических подписях,
    # поэтому легко не заметить.
    if uphill_ref:
        # Разворот геометрии не должен ронять построение: не получилось -
        # выходим с обычными линиями и говорим об этом.
        try:
            cur = _orient_uphill(processing, cur, uphill_ref, context, feedback)
        except Exception as e:
            feedback.pushWarning(
                _tr("Не удалось развернуть линии вверх по склону: %s") % e)
    if slope_ref:
        try:
            cur = _add_slope_side(processing, cur, slope_ref, context, feedback,
                                  flip=hatch_flip)
        except Exception as e:
            feedback.pushWarning(
                _tr("Не удалось вычислить сторону склона (dn_sign): %s") % e)
    if index_every and index_every > 1 and interval > 0:
        feedback.pushInfo(_tr("Главные изолинии: каждая %d-я…") % index_every)
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
                         densify, line_iter, field_name, ignore_nodata, nodata,
                         final_output, context, feedback, slope_ref=None,
                         uphill_ref=None, confidence=0, conf_frac=0.01,
                         hatch_flip=0):
    """Изолинии-линии. levels_text (если задан) имеет приоритет над шагом.
    Сглаживание - на уровне поля; line_iter - лёгкое скругление линий."""
    from qgis import processing
    field_name = field_name or DEFAULT_FIELD
    levels = _parse_levels(levels_text) if levels_text else []
    rp, rb = _prep_raster(raster, band, smooth, smooth_radius, densify, nodata,
                          feedback)
    li = int(line_iter)
    cur = _contour_lines(processing, rp, rb, interval, base, levels,
                         min_length, li, field_name, ignore_nodata, nodata,
                         context, feedback, confidence, conf_frac)
    return _finalize_lines(processing, cur, interval, base, index_every,
                           field_name, final_output, context, feedback,
                           slope_ref=slope_ref, uphill_ref=uphill_ref,
                           hatch_flip=hatch_flip)


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
        raise QgsProcessingException(_tr("Не удалось открыть растр."))
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
        raise QgsProcessingException(_tr("В растре нет валидных значений."))

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

    feedback.pushInfo(_tr("Контур валидной области…"))
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

    Нодирование и полигонизация - напрямую через GEOS (QgsGeometry.unaryUnion +
    QgsGeometry.polygonize), а не через native:splitwithlines: последний на
    густой сети (сгущённые изолинии) терял часть граней, и покрытие поясов
    падало вдвое. unaryUnion узлует всю сеть (включая T-стыки концов изолиний с
    контуром), polygonize собирает грани. Возвращает memory-слой полигонов.
    """
    from qgis.core import (QgsProcessingUtils, QgsGeometry, QgsVectorLayer,
                            QgsFeature)
    geoms = []
    for lid in (lines_layer, area_lines):
        lay = QgsProcessingUtils.mapLayerFromString(lid, context)
        if lay is None:
            continue
        for f in lay.getFeatures():
            g = f.geometry()
            if g is not None and not g.isEmpty():
                geoms.append(g)

    feedback.pushInfo(_tr("Нодирование сети линий (GEOS)…"))
    merged = QgsGeometry.unaryUnion(geoms)
    feedback.pushInfo(_tr("Полигонизация поясов (GEOS)…"))
    poly = QgsGeometry.polygonize([merged])

    mem = QgsVectorLayer("Polygon?crs=%s" % (crs.authid() or crs.toWkt()),
                         "belts_src", "memory")
    feats = []
    if poly is not None and not poly.isEmpty():
        for part in poly.asGeometryCollection():
            if part is not None and not part.isEmpty():
                nf = QgsFeature()
                nf.setGeometry(part)
                feats.append(nf)
    mem.dataProvider().addFeatures(feats)
    mem.updateExtents()
    feedback.pushInfo(_tr("Поясов получено (GEOS): %d") % len(feats))
    return mem


def _belts_to_layer(processing, polys_src, arr, valid, gt, levels, crs,
                    final_output, context, feedback):
    """Каждому поясу присваивает диапазон уровней выборкой растра в
    репрезентативной точке (point-on-surface) и сохраняет слой."""
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsFields, QgsField, QgsGeometry)
    from qgis.PyQt.QtCore import QVariant
    import numpy as np

    poly_layer = (polys_src if hasattr(polys_src, "getFeatures")
                  else _layer_from_string(polys_src, context))
    if poly_layer is None:
        raise QgsProcessingException(
            _tr("Полигонизация не дала результата (проверьте изолинии/контур)."))

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
        _tr("пояса"), "memory")
    dp = mem.dataProvider()
    dp.addAttributes(fields.toList())
    mem.updateFields()

    feedback.pushInfo(_tr("Назначение диапазонов поясам…"))
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
        raise QgsProcessingException(_tr("Ни один пояс не получил значения."))
    dp.addFeatures(out_feats)
    mem.updateExtents()
    return _save(processing, mem, final_output, context, feedback)



def isolines_and_polygons(raster, band, interval, base, levels_text,
                          index_every, min_length, smooth, smooth_radius,
                          densify, line_iter, field_name, ignore_nodata, nodata,
                          lines_output, polygons_output, context, feedback,
                          slope_ref=None, uphill_ref=None,
                          hatch_flip=0):
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

    # Сгущение применяется и к полигонам: пояса полигонизуются по сгущённому
    # полю напрямую через GEOS (см. _polygonize_belts), границы совпадают с
    # изолиниями.
    rp, rb = _prep_raster(raster, band, smooth, smooth_radius, densify, nodata,
                          feedback)

    levels = _parse_levels(levels_text) if levels_text else []
    if not levels:
        levels = compute_levels(rp, rb, interval, base)
    if not levels:
        raise QgsProcessingException(_tr("Недостаточно уровней для полигонов."))
    levels = sorted(levels)

    # 1) контур валидной области + массив растра (для выборки поясов)
    area_lines, gt, proj, arr, valid = _footprint_lines(
        processing, rp, rb, nodata, context, feedback)
    px = abs(gt[1]) or 1.0
    crs = QgsCoordinateReferenceSystem()
    if proj:
        crs.createFromWkt(proj)

    # 2) изолинии (ядро) -> притягиваем ТОЛЬКО концы к контуру
    li = int(line_iter)
    iso = _contour_lines(processing, rp, rb, interval, base, levels,
                         min_length, li, field_name, ignore_nodata, nodata,
                         context, feedback)
    snap_tol = float(3.0 * px)
    feedback.pushInfo(_tr("Согласование концов изолиний с контуром…"))
    iso = processing.run("native:snapgeometries", {
        "INPUT": iso, "REFERENCE_LAYER": area_lines,
        "TOLERANCE": snap_tol, "BEHAVIOR": 5,
        "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]

    # 3) линейный выход - из ЭТИХ же согласованных линий
    lines_out = _finalize_lines(processing, iso, interval, base, index_every,
                                field_name, lines_output, context, feedback,
                                slope_ref=slope_ref, uphill_ref=uphill_ref,
                           hatch_flip=hatch_flip)

    # Овершут ТОЛЬКО для открытых линий (упираются концами в контур): их
    # продлеваем за контур на ~1 ячейку, чтобы пересечь его и чисто
    # занодировать стык (в QGIS 4 / GEOS 3.14 касание T-стыков часто не
    # нодируется, и грань не замыкается). Замкнутые петли (start==end) НЕ
    # трогаем - овершут на точке замыкания дал бы спур внутрь поля и плодил
    # лишние полигоны-слайверы. Хвостик-овершут полигонизация отбрасывает как
    # dangle, поэтому границы поясов совпадают с линиями.
    over = float(px)
    iso_single = processing.run("native:multiparttosingleparts", {
        "INPUT": iso, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    feedback.pushInfo(_tr("Продление открытых концов за контур…"))
    split = processing.run("native:extractbyexpression", {
        "INPUT": iso_single,
        "EXPRESSION":
            "distance(start_point($geometry), end_point($geometry)) > 0",
        "OUTPUT": "TEMPORARY_OUTPUT", "FAIL_OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)
    open_ext = processing.run("native:extendlines", {
        "INPUT": split["OUTPUT"], "START_DISTANCE": over,
        "END_DISTANCE": over, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    iso_for_poly = processing.run("native:mergevectorlayers", {
        "LAYERS": [open_ext, split["FAIL_OUTPUT"]],
        "CRS": crs, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    polys_src = _polygonize_belts(processing, iso_for_poly, area_lines, crs,
                                  context, feedback)
    polys_out = _belts_to_layer(processing, polys_src, arr, valid, gt, levels,
                                crs, polygons_output, context, feedback)

    return {"lines": lines_out, "polygons": polys_out}
