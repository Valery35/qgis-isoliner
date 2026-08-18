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


def _as_layer(src, context):
    """Слой из чего угодно: строки Processing, пути или самого слоя.

    В цепочке изолиний соседствуют два вида звеньев. Алгоритмы Processing
    возвращают СТРОКУ-идентификатор, а свои шаги (чистка обрывков,
    притяжка, овершут) отдают готовый memory-слой: они обходят объекты
    сами, мимо проверки геометрии Processing. Дальше эти звенья
    перемешаны, и любое место, разрешающее слой, обязано принимать оба.

    Прямой вызов mapLayerFromString на объекте слоя бросает TypeError.
    Так и упала полигонизация, когда овершут перестал быть шагом
    Processing: сама правка была верной, а звено ниже по цепочке о ней не
    знало. Поэтому разрешение слоя живёт в одном месте.
    """
    from qgis.core import QgsProcessingUtils
    if src is None or isinstance(src, str):
        return QgsProcessingUtils.mapLayerFromString(src, context) \
            if src else None
    return src


def _geom_parts(geom):
    """Геометрия линии -> список частей [[(x, y), ...], ...]."""
    try:
        raw = (geom.asMultiPolyline() if geom.isMultipart()
               else [geom.asPolyline()])
    except TypeError:                       # QGIS 4 на одиночной геометрии
        raw = [geom.asPolyline()]
    return [[(float(p.x()), float(p.y())) for p in part] for part in raw]


def _parts_geom(parts):
    """Список частей -> геометрия линии."""
    from qgis.core import QgsGeometry, QgsPointXY
    rings = [[QgsPointXY(x, y) for x, y in part] for part in parts]
    return (QgsGeometry.fromMultiPolylineXY(rings) if len(rings) > 1
            else QgsGeometry.fromPolylineXY(rings[0]))


def _rewrite_lines(src, transform, context, name="lines"):
    """Общий каркас шага цепочки: слой линий на входе, слой линий на выходе.

    Три шага - чистка обрывков, притяжка концов к разлому и продление
    открытых концов - делают одно и то же обрамление: разрешают слой,
    заводят память с теми же полями и системой координат, обходят
    объекты, разбирают геометрию на части и собирают обратно. Отличаются
    они только тем, что делают с частями.

    Каркас был скопирован трижды, и каждая копия жила своей жизнью. Ровно
    на этом сломались три правки подряд: переход на свой обход уронил
    полигонизацию, потом овершут дважды сломался сам - сперва отменой у
    разлома, потом сдвигом индексов при дописывании вершин. Ошибка в
    копии не видна из соседней копии.

    Теперь обрамление одно, а `transform` получает список частей в
    простых координатах и возвращает `(новые части, счётчик)`. Пустой
    список частей означает, что объект выбрасывается. Функции-обработчики
    от этого становятся чистой математикой без QGIS и проверяются
    безголовыми тестами.

    Обход идёт по объектам, а не через processing.run: алгоритм
    проверяет геометрию на входе и срывается на том, что призван
    вычистить.
    """
    from qgis.core import QgsVectorLayer, QgsFeature, QgsWkbTypes
    lay = _as_layer(src, context)
    if lay is None:
        return src, 0
    crs = lay.crs()
    uri = "%s?crs=%s" % (QgsWkbTypes.displayString(lay.wkbType()),
                         crs.authid() or crs.toWkt())
    mem = QgsVectorLayer(uri, name, "memory")
    if not mem.isValid():
        return src, 0
    mem.dataProvider().addAttributes(lay.fields())
    mem.updateFields()
    feats = []
    total = 0
    for f in lay.getFeatures():
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        parts, n = transform(_geom_parts(g))
        total += n
        if not parts:
            continue
        nf = QgsFeature(mem.fields())
        nf.setGeometry(_parts_geom(parts))
        nf.setAttributes(f.attributes())
        feats.append(nf)
    mem.dataProvider().addFeatures(feats)
    mem.updateExtents()
    return mem, total


def _fault_polylines(faults, context):
    """Разломы списком ломаных [(x, y), ...]. Пусто, если слой не открылся."""
    from qgis.core import QgsVectorLayer
    lay = _as_layer(faults, context)
    if lay is None:
        lay = QgsVectorLayer(faults, "faults", "ogr")
    if lay is None or not lay.isValid():
        return []
    out = []
    for f in lay.getFeatures():
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        try:
            parts = (g.asMultiPolyline() if g.isMultipart()
                     else [g.asPolyline()])
        except TypeError:                      # QGIS 4 на одиночной геометрии
            parts = [g.asPolyline()]
        for part in parts:
            if len(part) >= 2:
                out.append([(float(p.x()), float(p.y())) for p in part])
    return out


def _nearest_on_fault(x, y, lines):
    """Ближайшая точка на разломах: (расстояние, x, y, ломаная) или None."""
    best = None
    for pts in lines:
        for k in range(len(pts) - 1):
            ax, ay = pts[k]
            bx, by = pts[k + 1]
            dx, dy = bx - ax, by - ay
            den = dx * dx + dy * dy
            if den <= 0.0:
                continue
            s = ((x - ax) * dx + (y - ay) * dy) / den
            s = 0.0 if s < 0.0 else (1.0 if s > 1.0 else s)
            cx, cy = ax + s * dx, ay + s * dy
            d = math.hypot(x - cx, y - cy)
            if best is None or d < best[0]:
                best = (d, cx, cy, pts)
    return best


def _on_fault(x, y, lines, eps):
    """Лежит ли точка на линии разлома с точностью eps."""
    near = _nearest_on_fault(x, y, lines)
    return near is not None and near[0] <= eps


def _closest_on_fault(x, y, lines, tol):
    """Ближайшая точка на разломе или None, если притягивать не надо.

    None возвращается в двух случаях. Первый простой: разлом дальше
    допуска. Второй и есть лекарство от веера: ближайшая точка совпала с
    КОНЦЕВОЙ вершиной ломаной, то есть исходная точка лежит за концом
    разлома. Для всех таких точек ближайшая точка одна и та же, и притяжка
    сводила их в один узел пучком расходящихся отрезков.

    За концом разлома разрыва нет, поверхность там смыкается, и тянуть
    туда изолинию не к чему.
    """
    best = _nearest_on_fault(x, y, lines)
    if best is None or best[0] > tol:
        return None
    d, cx, cy, pts = best
    eps = max(tol * 1e-3, 1e-9)
    for tx, ty in (pts[0], pts[-1]):
        if math.hypot(cx - tx, cy - ty) <= eps:
            return None
    return cx, cy


def _junction_report(lines, tol):
    """Сомкнутые и недоведённые концы разломов. См. kb2d.junction_report."""
    eps = float(tol) * 1e-3
    joined = 0
    gaps = []
    for i, pts in enumerate(lines):
        if len(pts) < 2:
            continue
        for at, (x, y) in ((0, pts[0]), (1, pts[-1])):
            near = None
            for j, other in enumerate(lines):
                if j == i or len(other) < 2:
                    continue
                hit = _nearest_on_fault(x, y, [other])
                if hit is not None and (near is None or hit[0] < near):
                    near = hit[0]
            if near is None:
                continue
            if near <= eps:
                joined += 1
            elif near <= tol:
                gaps.append((i, at, near))
    return joined, gaps


def _abuts_other(pts, at, others, eps):
    """Упирается ли конец ломаной в другую линию разлома.

    Конец, лежащий на соседнем разломе, НЕ затухающий. Смещение там не
    сходит на нет, оно передаётся соседнему нарушению, и разрыв
    продолжается. Обходиться с таким концом как с выклинивающимся
    неправильно: у стыка появится просвет и пучок изолиний, которых там
    быть не должно.
    """
    x, y = pts[0] if at == 0 else pts[-1]
    for other in others:
        if other is pts or len(other) < 2:
            continue
        near = _nearest_on_fault(x, y, [other])
        if near is not None and near[0] <= eps:
            return True
    return False


def _axis_for_corridor(pts, others, width, eps):
    """Ось коридора: свободный конец укоротить, примыкающий продлить.

    Свободный конец укорачивается на ширину полосы, иначе торец режет
    изолинии за концевой вершиной и обрезанные концы повисают.

    Примыкающий конец, наоборот, продлевается на ту же ширину: коридор
    должен дойти до соседнего разлома и перекрыться с его коридором,
    иначе у стыка останется непрорезанный участок.
    """
    head_join = _abuts_other(pts, 0, others, eps)
    tail_join = _abuts_other(pts, 1, others, eps)
    out = list(pts)
    if head_join or tail_join:
        out = _extend_ends_xy(out,
                              width if head_join else 0.0,
                              width if tail_join else 0.0)
    if not head_join or not tail_join:
        # укорачиваем только свободные концы: режем с одной стороны за раз
        if not head_join:
            out = list(reversed(_trim_one_end(list(reversed(out)), width)))
        if not tail_join:
            out = _trim_one_end(out, width)
    return out


def _trim_one_end(pts, cut):
    """Укоротить ломаную на cut с КОНЦА. Чистая математика."""
    if len(pts) < 2 or cut <= 0.0:
        return list(pts)
    rev = list(reversed(pts))
    seg = [math.hypot(rev[k + 1][0] - rev[k][0], rev[k + 1][1] - rev[k][1])
           for k in range(len(rev) - 1)]
    total = sum(seg)
    if total <= 0.0:
        return list(pts)
    cut = min(float(cut), total / 3.0)
    acc = 0.0
    for k, ln in enumerate(seg):
        if acc + ln >= cut:
            s = (cut - acc) / ln if ln > 0 else 0.0
            x = rev[k][0] + s * (rev[k + 1][0] - rev[k][0])
            y = rev[k][1] + s * (rev[k + 1][1] - rev[k][1])
            return list(reversed([(x, y)] + list(rev[k + 1:])))
        acc += ln
    return list(pts)


def _trim_polyline(pts, cut):
    """Укоротить ломаную на cut с обоих концов. Чистая математика.

    Нужна коридору. Полоса, доходящая до самого конца разлома, режет
    изолинии своим торцом, и обрезанный конец оказывается ЗА концевой
    вершиной линии. Притянуть его нельзя (там разрыв уже сошёл на нет),
    и он остаётся висеть в стороне от разлома. Полигонизация выбрасывает
    висячее ребро вместе со всем куском изолинии до ближайшего узла, а
    узлов у изолиний почти нет - выбрасывается кусок целиком. На карте
    такая изолиния рисуется без границы пояса под ней, а соседние пояса
    сливаются.

    Укороченная линия ставит торец коридора внутрь разлома. Тогда любой
    обрезанный конец лежит сбоку от линии, проекция попадает строго
    внутрь, и притяжка его берёт.

    Короткая линия не укорачивается до исчезновения: срез не больше трети
    длины с каждой стороны.
    """
    if len(pts) < 2 or cut <= 0.0:
        return list(pts)
    seg = [math.hypot(pts[k + 1][0] - pts[k][0], pts[k + 1][1] - pts[k][1])
           for k in range(len(pts) - 1)]
    total = sum(seg)
    if total <= 0.0:
        return list(pts)
    cut = min(float(cut), total / 3.0)

    def walk(points, lengths, dist):
        """Точка на расстоянии dist от начала и хвост ломаной после неё."""
        acc = 0.0
        for k, ln in enumerate(lengths):
            if acc + ln >= dist:
                s = (dist - acc) / ln if ln > 0 else 0.0
                x = points[k][0] + s * (points[k + 1][0] - points[k][0])
                y = points[k][1] + s * (points[k + 1][1] - points[k][1])
                return [(x, y)] + list(points[k + 1:])
            acc += ln
        return [points[-1]]

    head = walk(pts, seg, cut)
    rev = list(reversed(head))
    seg2 = [math.hypot(rev[k + 1][0] - rev[k][0], rev[k + 1][1] - rev[k][1])
            for k in range(len(rev) - 1)]
    return list(reversed(walk(rev, seg2, cut)))


def _trimmed_fault_layer(faults, cut, context):
    """Ось коридора: свободные концы укорочены, примыкающие продлены."""
    from qgis.core import (QgsVectorLayer, QgsFeature, QgsGeometry,
                           QgsPointXY)
    lines = _fault_polylines(faults, context)
    if not lines:
        return None
    src = _as_layer(faults, context)
    crs = src.crs() if src is not None else None
    uri = "LineString?crs=%s" % ((crs.authid() or crs.toWkt()) if crs else "")
    mem = QgsVectorLayer(uri, "faults_trimmed", "memory")
    if not mem.isValid():
        return None
    feats = []
    eps = float(cut) * 0.05
    for pts in lines:
        short = _axis_for_corridor(pts, lines, float(cut), eps)
        if len(short) < 2:
            continue
        f = QgsFeature()
        f.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(x, y) for x, y in short]))
        feats.append(f)
    if not feats:
        return None
    mem.dataProvider().addFeatures(feats)
    mem.updateExtents()
    return mem


def _snap_ends_to_faults(iso, faults, tol, context, feedback):
    """Притянуть концы изолиний к линии разлома по нормали.

    Своя притяжка вместо native:snapgeometries. Штатная тянет конец к
    ближайшей точке опорного слоя без разбора, и за концом разлома этой
    точкой оказывается концевая вершина - одна на все окрестные концы.
    Управлять этим у алгоритма нечем, поэтому проверка стоит здесь.
    """
    lines = _fault_polylines(faults, context)
    if not lines:
        return iso

    def transform(parts):
        out = []
        moved = 0
        for part in parts:
            pts = list(part)
            if len(pts) >= 2:
                for at in (0, len(pts) - 1):
                    hit = _closest_on_fault(pts[at][0], pts[at][1], lines, tol)
                    if hit is not None:
                        pts[at] = hit
                        moved += 1
            out.append(pts)
        return out, moved

    lay, moved = _rewrite_lines(iso, transform, context, "snapped_lines")
    feedback.pushInfo(_tr("Концов притянуто к разлому: %d.") % moved)
    return lay


def _extend_ends_xy(pts, over_head, over_tail):
    """Продлить ломаную [(x, y), ...] с концов. Чистая математика.

    Длина задаётся по концам отдельно, ноль означает не продлевать. У
    контура области нужен хвостик в ячейку, у разлома - короткий: там
    достаточно пересечь линию, чтобы стык занодировался.

    Обе вершины-хвостика считаются ДО того, как список изменится, и только
    потом дописываются. Иначе вставка в начало сдвигает индексы, и второй
    конец берётся не от той вершины: хвостик уезжает от предпоследней
    точки, а приписывается к последней. На карте это чужой отрезок поперёк
    изолиний, а в поясах - незамкнутые грани и потеря части полигонов.

    Замкнутая ломаная не трогается: хвостик на точке замыкания дал бы
    шпору внутрь поля и лишние полигоны-слайверы.
    """
    if len(pts) < 2:
        return list(pts)
    (x0, y0), (x1, y1) = pts[0], pts[-1]
    if abs(x0 - x1) < 1e-12 and abs(y0 - y1) < 1e-12:
        return list(pts)

    def tip(p, q, over):
        if over <= 0.0:
            return None
        dx, dy = p[0] - q[0], p[1] - q[1]
        d = math.hypot(dx, dy)
        if d <= 0.0:
            return None
        return (p[0] + dx / d * over, p[1] + dy / d * over)

    head = tip(pts[0], pts[1], float(over_head))
    tail = tip(pts[-1], pts[-2], float(over_tail))
    out = list(pts)
    if tail is not None:
        out.append(tail)
    if head is not None:
        out.insert(0, head)
    return out


def _extend_free_ends(iso, faults, over, context, feedback, hold_r=0.0):
    """Продлить открытые концы за контур, НЕ перепрыгивая разлом.

    Овершут нужен на контуре области: касание T-стыком в GEOS часто не
    нодируется, и грань пояса не замыкается. Хвостик полигонизация
    отбрасывает как висячее ребро, поэтому границы поясов совпадают с
    изолиниями.

    У разлома хвостик нужен тоже, но КОРОТКИЙ. Отменять его было ошибкой:
    конец, притянутый на линию, образует с ней T-стык, а такой стык GEOS
    часто не нодирует - ровно то, ради чего овершут и заведён. Грань не
    замыкалась, соседние пояса сливались, и под частью изолиний границы
    полигонов не было вовсе.

    Длинный хвостик у разлома тоже вреден: он выносится на другое крыло и
    достаёт до соседней изолинии, замыкая лишнюю грань. Отсюда доля от
    ячейки: пересечь линию хватает, дотянуться до чужого крыла нет. Запас
    надёжен, потому что коридор уже вычистил полосу своей ширины по обе
    стороны, и ближайшая чужая изолиния не ближе неё.

    Замкнутые петли не трогаются: овершут на точке замыкания дал бы шпору
    внутрь поля и лишние полигоны-слайверы.
    """
    lines = _fault_polylines(faults, context) if faults else []
    eps = max(float(hold_r), float(over) * 0.05, 1e-9)
    short = float(over) * 0.1

    def transform(parts):
        out = []
        grown = 0
        for part in parts:
            at_head = bool(lines) and len(part) >= 2 and _on_fault(
                part[0][0], part[0][1], lines, eps)
            at_tail = bool(lines) and len(part) >= 2 and _on_fault(
                part[-1][0], part[-1][1], lines, eps)
            new_part = _extend_ends_xy(part,
                                       short if at_head else over,
                                       short if at_tail else over)
            grown += max(len(new_part) - len(part), 0)
            out.append(new_part)
        return out, grown

    lay, grown = _rewrite_lines(iso, transform, context, "extended_lines")
    feedback.pushInfo(_tr("Концов продлено: %d.") % grown)
    return lay


def _cut_fault_corridor(processing, iso, faults, width, context, feedback):
    """Вырезать из изолиний полосу вдоль разлома и притянуть концы к линии.

    Зачем. Грид сплошной, а значения по разные стороны разлома отличаются
    на всю амплитуду, и скачок приходится на пару соседних ячеек. Контурер
    рисует в этом промежутке все промежуточные уровни разом: при
    амплитуде в двадцать метров и сечении в два метра это десяток изолиний
    в ширине одной ячейки. Геологического смысла у них нет, это
    интерполяция поперёк разрыва, которого пласт не знает.

    Торцы коридора плоские: полоса обрывается ровно на конце разлома. За
    концом разрыва уже нет, поверхность смыкается, и резать там нечего.
    Круглый торец пробовали, и он оставлял в поясах клин: изолинии,
    обрезанные за концом линии, получали свободные концы посреди
    просвета, овершут продлевал их навстречу друг другу, и они замыкали
    лишнюю грань.

    Веер сходящихся линий, который был раньше, формой торца не лечился
    вовсе: дело было в притяжке. Штатная тянула конец к ближайшей точке
    разлома, а за концом линии такая точка одна на всех - концевая
    вершина. Поэтому притяжка своя (_snap_ends_to_faults): конец,
    ближайшая точка которого совпала с концевой вершиной, не двигается.
    С плоским торцом такие концы вообще перестали появляться.

    Ширина задаётся в единицах карты. Меньше ячейки брать незачем: скачок
    занимает ровно ячейку. Заметно больше - начнут пропадать изолинии,
    идущие вдоль разлома по делу, и просвет у конца станет шире.
    """
    if not faults or width <= 0:
        return iso
    feedback.pushInfo(_tr("Коридор у разлома: полоса %.4g ед. карты…") % width)
    # Коридор строится по УКОРОЧЕННОЙ линии, а притягиваются концы к
    # настоящей. Так торец полосы стоит внутри разлома, и всякий
    # обрезанный конец лежит сбоку от линии, а не за её концом.
    axis = _trimmed_fault_layer(faults, float(width), context) or faults
    buf = processing.run("native:buffer", {
        "INPUT": axis, "DISTANCE": float(width), "SEGMENTS": 12,
        "END_CAP_STYLE": 1, "JOIN_STYLE": 0, "MITER_LIMIT": 2.0,
        "DISSOLVE": True, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback,
        is_child_algorithm=True)["OUTPUT"]
    iso = processing.run("native:difference", {
        "INPUT": iso, "OVERLAY": buf, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback,
        is_child_algorithm=True)["OUTPUT"]
    iso = _clean_lines(iso, context, feedback, _tr("вырезание коридора"))
    return _snap_ends_to_faults(iso, faults, float(width) * 1.25,
                                context, feedback)


def _split_by_faults(processing, iso, faults, corridor, context, feedback):
    """Разрез изолиний разломом и вырезание коридора. Общий шаг двух веток."""
    if not faults:
        return iso
    lines_dbg = _fault_polylines(faults, context)
    if lines_dbg and corridor > 0:
        joined, gaps = _junction_report(lines_dbg, 2.0 * float(corridor))
        if joined:
            feedback.pushInfo(_tr("Сомкнутых стыков разломов: %d.") % joined)
        if gaps:
            feedback.pushWarning(_tr(
                "Недоведённых концов разломов: %d, наибольший зазор %.4g ед. "
                "карты. У такого конца коридор считает разлом затухающим и "
                "оставляет просвет, хотя разрыв там продолжается в соседнее "
                "нарушение. Доведите концы до соседней линии.")
                % (len(gaps), max(d for _, _, d in gaps)))
    feedback.pushInfo(_tr("Разрез изолиний линиями разломов…"))
    iso = processing.run("native:splitwithlines", {
        "INPUT": iso, "LINES": faults, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    iso = _clean_lines(iso, context, feedback, _tr("разрез разломами"))
    return _cut_fault_corridor(processing, iso, faults, corridor,
                               context, feedback)


def _clean_parts(parts):
    """Выбросить вырожденные части и снять совпадающие соседние узлы.

    Разрез линией разлома оставляет обрывки: если линия проходит точно
    через вершину изолинии, кусок вырождается в точку или в отрезок
    нулевой длины. Processing считает такую геометрию некорректной и по
    умолчанию прерывает расчёт целиком, а не пропускает объект. Падало на
    продлении открытых концов, и виноват был не тот шаг, на котором
    вылезло: обрывок родился раньше, при разрезе.

    Чистая математика, проверяется без QGIS.
    """
    out = []
    dropped = 0
    for part in parts:
        pts = []
        for xy in part:
            if not pts or abs(xy[0] - pts[-1][0]) > 1e-12 \
                    or abs(xy[1] - pts[-1][1]) > 1e-12:
                pts.append(tuple(xy))
        length = sum(math.hypot(pts[k + 1][0] - pts[k][0],
                                pts[k + 1][1] - pts[k][1])
                     for k in range(len(pts) - 1))
        if len(pts) < 2 or length <= 0.0:
            dropped += 1
            continue
        out.append(pts)
    return out, dropped


def _clean_lines(src, context, feedback, where=""):
    """Убрать вырожденные обрывки, минуя проверку геометрии Processing."""
    lay, dropped = _rewrite_lines(src, _clean_parts, context, "clean_lines")
    if dropped:
        feedback.pushInfo(_tr("Вырожденных обрывков отброшено: %d%s.")
                          % (dropped, (" (%s)" % where) if where else ""))
    return lay


def _drop_fid(processing, layer_id, context, feedback):
    """Убрать поле fid, пришедшее из GeoPackage.

    gdal:contour пишет во временный GeoPackage, а QGIS показывает у слоя
    GPKG служебный ключ fid обычным полем. Дальше оно едет по всей цепочке
    как атрибут. Пока изолинию никто не разрезал, значения оставались
    уникальными и всё работало. Разрез линией разлома делает из одной
    изолинии несколько кусков с ОДНИМ И ТЕМ ЖЕ fid, и запись результата в
    GeoPackage падает на UNIQUE constraint failed: OUTPUT.fid.

    Поле снимается сразу после контуринга, один раз для всех потребителей:
    любой шаг, размножающий объекты (разрез, разбор мультичастей), даёт ту
    же беду, и чинить её у каждого по отдельности значит ждать следующего.
    """
    from qgis.core import QgsProcessingUtils
    try:
        lay = _as_layer(layer_id, context)
        names = [f.name() for f in lay.fields()] if lay is not None else []
    except (AttributeError, RuntimeError):
        return layer_id
    if "fid" not in names:
        return layer_id
    return processing.run("native:deletecolumn", {
        "INPUT": layer_id, "COLUMN": ["fid"], "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]


def _pixel_size(raster):
    """Размер ячейки растра в единицах карты, ноль при неудаче."""
    try:
        from osgeo import gdal
        ds = gdal.Open(raster)
        if ds is None:
            return 0.0
        gt = ds.GetGeoTransform()
        return abs(float(gt[1])) or 0.0
    except Exception:
        return 0.0


def _contour_lines(processing, raster, band, interval, base, levels,
                   min_length, line_iter, field_name, ignore_nodata, nodata,
                   context, feedback, confidence=0, conf_frac=0.01,
                   thin=0.0):
    """Изолинии-линии (без флага is_index). Сглаживание поля (растра) делается
    до контуринга (см. _prep_raster) - это убирает пересечения. Дополнительно
    линии можно слегка СКРУГЛИТЬ (Chaikin, line_iter итераций): поле уже
    гладкое, контуры разнесены, поэтому скругление не создаёт пересечений, но
    убирает «октагоны» от грубого грида. Общее ядро для линий и для границ
    полигонов: геометрия гарантированно совпадает.

    thin - прореживание контура, доля ячейки. Контур из грида несёт вершину
    почти на каждом пересечении ячейки, и кольцо в двадцать тысяч вершин это
    не геология, а неупрощённый растр. Прореживание стоит здесь и только
    здесь: линии и границы поясов выходят из одного набора, поэтому общая
    граница соседних поясов остаётся общей сама собой. Прореживать готовые
    полигоны по отдельности нельзя, у соседей разошлись бы стыки."""
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
    cur = _drop_fid(processing, cur, context, feedback)

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

    if thin and thin > 0:
        tol = float(thin) * (_pixel_size(raster) or 0.0)
        if tol > 0:
            feedback.pushInfo(
                _tr("Прореживание контуров (допуск %.4g)…") % tol)
            cur = processing.run("native:simplifygeometries", {
                "INPUT": cur, "METHOD": 0, "TOLERANCE": tol,
                "OUTPUT": "TEMPORARY_OUTPUT",
            }, context=context, feedback=feedback,
                is_child_algorithm=True)["OUTPUT"]

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


def _orient_uphill(processing, cur, slope_ref, context, feedback, flip=0):
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

    Переключатель стороны действует и здесь: при значении «перевернуть»
    условие сохранения линии инвертируется, и подписи встают вверх ногами
    относительно автоматического выбора. Это нужно, чтобы одна ручная
    команда разворачивала всю картину разом, а не половину её.

    Бергштрихи от этого не страдают: сторона склона считается ПОСЛЕ разворота
    и подстраивается сама. Важно только не применить переворот второй раз в
    _add_slope_side, иначе штрихи вернутся на прежнюю сторону - за этим
    следит _finalize_lines.
    """
    rid, band, eps = slope_ref
    keep, turn = (0, 1) if flip < 0 else (1, 0)
    expr = (
        "with_variable('p', line_interpolate_point($geometry, $length/2.0),"
        " with_variable('a', line_interpolate_angle($geometry, $length/2.0),"
        "  with_variable('vr', raster_value('{rid}', {b}, "
        "project(@p, {e}, radians(@a + 90))),"
        "   with_variable('vl', raster_value('{rid}', {b}, "
        "project(@p, {e}, radians(@a - 90))),"
        "    CASE WHEN @vr IS NULL OR @vl IS NULL THEN 1"
        "     WHEN @vr >= @vl THEN {keep} ELSE {turn} END))))"
    ).format(rid=rid, b=int(band), e=float(eps), keep=keep, turn=turn)
    feedback.pushInfo(_tr(
        "Ориентация горизонталей вверх по склону: %s.")
        % (_tr("зеркально (задано вручную)") if flip < 0
           else _tr("как есть")))
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
    #
    # ПЕРЕВОРОТ ПРИМЕНЯЕТСЯ РОВНО ОДИН РАЗ. Ручная команда «перевернуть» должна
    # разворачивать и штрихи, и подписи, но подписи разворачиваются сменой
    # направления линии, а это само по себе переставляет лево и право и меняет
    # знак dn_sign. Если после такого разворота применить переворот ещё и в
    # _add_slope_side, два переворота погасят друг друга и штрихи вернутся на
    # прежнюю сторону. Поэтому при удавшемся развороте сторона склона
    # считается без переворота: он уже учтён в направлении линий.
    uphill_done = False
    if uphill_ref:
        # Разворот геометрии не должен ронять построение: не получилось -
        # выходим с обычными линиями и говорим об этом.
        try:
            cur = _orient_uphill(processing, cur, uphill_ref, context, feedback,
                                 flip=hatch_flip)
            uphill_done = True
        except Exception as e:
            feedback.pushWarning(
                _tr("Не удалось развернуть линии вверх по склону: %s") % e)
    if slope_ref:
        try:
            cur = _add_slope_side(processing, cur, slope_ref, context, feedback,
                                  flip=0 if uphill_done else hatch_flip)
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
                         hatch_flip=0, faults=None, corridor=0.0, thin=0.0):
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
                         context, feedback, confidence, conf_frac, thin)
    # Разлом режет изолинии и тогда, когда пояса не строятся: разрыв
    # принадлежит линиям, а не полигонам.
    cur = _split_by_faults(processing, cur, faults, corridor, context, feedback)
    return _finalize_lines(processing, cur, interval, base, index_every,
                           field_name, final_output, context, feedback,
                           slope_ref=slope_ref, uphill_ref=uphill_ref,
                           hatch_flip=hatch_flip)


# ---------------------------------------------------------------------------
#  Полигоны = пояса между изолиниями (границы совпадают с линиями, без дыр)
# ---------------------------------------------------------------------------
def _layer_from_string(s, context):
    """Слой по результату дочернего алгоритма, с проверкой годности.

    Разрешение делает _as_layer, поэтому объект слоя принимается наравне
    со строкой. Отличие от _as_layer одно: здесь негодный слой это None,
    а не он сам.
    """
    if s is None:
        return None
    try:
        lyr = _as_layer(s, context)
        return lyr if (lyr is not None and lyr.isValid()) else None
    except (TypeError, AttributeError, RuntimeError):
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


def _polygon_with_z(geom, arr, gt, lo, hi, valid=None):
    """Полигон с отметками: каждое кольцо на своём уровне.

    Кольцо пояса целиком лежит на одной изолинии, поэтому отметка у него
    одна на всё кольцо. Внешнее на одном уровне, внутреннее на соседнем,
    и полигон между ними становится наклонной гранью.

    Выборка идёт БЛИЖАЙШЕЙ ячейкой, а не билинейно: билинейная даёт
    пусто на самом краю растра, а контур области идёт ровно по краю, и
    отметок не оставалось бы вовсе.
    """
    import numpy as np
    from qgis.core import (QgsGeometry, QgsPoint, QgsLineString, QgsPolygon,
                           QgsMultiPolygon)
    from . import section_core as _sc
    try:
        polys_src = geom.asMultiPolygon() or []
    except TypeError:
        polys_src = []
    if not polys_src:
        one = geom.asPolygon()
        polys_src = [one] if one else []
    if not polys_src or (lo is None and hi is None):
        return geom
    out = QgsMultiPolygon()
    for poly in polys_src:
        qp = QgsPolygon()
        for k, ring in enumerate(poly):
            if len(ring) < 4:
                continue
            xs = np.array([p.x() for p in ring], dtype=np.float64)
            ys = np.array([p.y() for p in ring], dtype=np.float64)
            # Отметка на КАЖДУЮ вершину, а не одна на кольцо: у пояса
            # чаще всего одно кольцо, и оно идёт частью по нижней
            # изолинии, частью по верхней. Один уровень на кольцо сделал
            # бы плоскими девять поясов из десяти.
            zs = _sc.vertex_levels(xs, ys, arr, gt, lo, hi)
            if zs is None:
                return geom
            ls = QgsLineString([QgsPoint(float(x), float(y), float(z))
                                for x, y, z in zip(xs, ys, zs)])
            if k == 0:
                qp.setExteriorRing(ls)
            else:
                qp.addInteriorRing(ls)
        if qp.exteriorRing() is None:
            continue
        out.addGeometry(qp)
    if out.numGeometries() == 0:
        return geom
    return QgsGeometry(out)


def _polygonize_belts(processing, lines_layer, area_lines, crs, context,
                      feedback, faults=None):
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
    # Линия разлома входит в сеть наравне с изолиниями и контуром: тогда
    # грани замыкаются точно по ней, и граница поясов идёт по разлому, а
    # не ступеньками по ячейкам.
    sources = [lines_layer, area_lines]
    if faults:
        sources.append(faults)
    for lid in sources:
        lay = _as_layer(lid, context)
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


def belt_thickness(geom):
    """Средняя толщина полигона: удвоенная площадь на периметр.

    У полосы, порождённой разрывом грида, ширина около ячейки при любой
    длине, поэтому по площади её не отличить от настоящего пояса: полоса
    вдоль разлома тянется на сотни ячеек и площадь имеет большую.
    Отношение площади к периметру от длины не зависит и даёт как раз
    ширину.

    Для вытянутой фигуры длиной L и шириной w площадь равна L·w, периметр
    примерно 2L, и удвоенное отношение даёт w. Для компактной фигуры
    величина завышена, но там она и не нужна: пояс шириной в десятки
    ячеек порог проходит с запасом.
    """
    if geom is None or geom.isEmpty():
        return 0.0
    per = float(geom.length())
    if per <= 0.0:
        return 0.0
    return 2.0 * float(geom.area()) / per


def _belts_to_layer(processing, polys_src, arr, valid, gt, levels, crs,
                    final_output, context, feedback, min_thick=0.0,
                    with_z=False, solids_output=None):
    """Каждому поясу присваивает диапазон уровней выборкой растра в
    репрезентативной точке (point-on-surface) и сохраняет слой.

    `solids_output` включает второй выход: тот же пояс замкнутой
    оболочкой, крышка снизу на ELEV_MIN, крышка сверху на ELEV_MAX,
    стенки по всем кольцам, включая дыры. Оболочка нужна объёму, обрезке
    сцены с закрытым срезом и обмену с программами, которые понимают
    только замкнутые тела. Считается из тех же колец и тех же диапазонов,
    поэтому второй проход по данным не нужен.
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsFields, QgsField, QgsGeometry,
        QgsPoint, QgsLineString, QgsPolygon, QgsMultiPolygon)
    from . import solids as _solids
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
    # Слой с отметками объявляется MultiPolygonZ: иначе провайдер молча
    # срежет третью координату, и юбки выйдут плоскими.
    mem = QgsVectorLayer(
        "%s?crs=%s" % ("MultiPolygonZ" if with_z else "MultiPolygon",
                       crs.authid() or crs.toWkt()),
        _tr("пояса"), "memory")
    dp = mem.dataProvider()
    dp.addAttributes(fields.toList())
    mem.updateFields()

    smem = sdp = None
    if solids_output is not None:
        sfields = QgsFields()
        sfields.append(QgsField("ELEV_MIN", QVariant.Double, len=20, prec=6))
        sfields.append(QgsField("ELEV_MAX", QVariant.Double, len=20, prec=6))
        # поле shell отличает тело от пояса без догадок по геометрии
        sfields.append(QgsField("shell", QVariant.Int))
        smem = QgsVectorLayer(
            "MultiPolygonZ?crs=%s" % (crs.authid() or crs.toWkt()),
            _tr("тела"), "memory")
        sdp = smem.dataProvider()
        sdp.addAttributes(sfields.toList())
        smem.updateFields()
    solid_feats = []
    n_open = 0

    def rings_of(geom):
        """Кольца объекта в плане: [[внешнее, дыра, ...], ...]."""
        try:
            polys = geom.asMultiPolygon() or []
        except TypeError:
            polys = []
        if not polys:
            one = geom.asPolygon()
            polys = [one] if one else []
        return [[[(p.x(), p.y()) for p in r] for r in poly if r]
                for poly in polys]

    feedback.pushInfo(_tr("Назначение диапазонов поясам…"))
    out_feats = []
    n_thin = 0
    # Грань может выпасть из покрытия молча: не встала репрезентативная
    # точка или она легла на nodata. Считается только отсев тонких полос,
    # поэтому настоящая дыра в покрытии не оставила бы в журнале ни следа,
    # и искали бы её глазами по карте.
    n_norep = n_nodata = 0
    a_norep = a_nodata = 0.0
    n_total = max(poly_layer.featureCount(), 1)
    for i, feat in enumerate(poly_layer.getFeatures()):
        g = feat.geometry()
        if g is None or g.isEmpty():
            continue
        rep = g.pointOnSurface()
        if rep is None or rep.isEmpty():
            rep = g.centroid()
        if rep is None or rep.isEmpty():
            n_norep += 1
            a_norep += float(g.area())
            continue
        if min_thick > 0.0 and belt_thickness(g) < min_thick:
            n_thin += 1
            continue                       # полоса с разрыва, а не пояс
        p = rep.asPoint()
        val = _sample_value(arr, valid, gt, p.x(), p.y())
        if val is None:
            n_nodata += 1
            a_nodata += float(g.area())
            continue                       # точка вне валидной области
        idx = int(np.digitize([val], lv)[0])     # 0..len(levels)
        nf = QgsFeature(mem.fields())
        gg = QgsGeometry(g)
        if with_z:
            # Пояс лежит между двумя уровнями, и его кольца идут по ним:
            # внешнее по одному, внутреннее по другому. Отметки вершин
            # делают из плоского пояса наклонную грань, а из набора
            # поясов - ступенчатую поверхность.
            lo = float(lv[idx - 1]) if idx > 0 else None
            hi = float(lv[idx]) if idx < len(lv) else None
            gg = _polygon_with_z(gg, arr, gt, lo, hi)
        gg.convertToMultiType()
        nf.setGeometry(gg)
        nf.setAttributes([float(mins[idx]), float(maxs[idx])])
        out_feats.append(nf)
        if sdp is not None:
            z_lo, z_hi = float(mins[idx]), float(maxs[idx])
            mp = QgsMultiPolygon()
            n_parts = 0
            for rings in rings_of(g):
                parts = _solids.shell_faces(rings, z_lo, z_hi)
                if not parts:
                    continue
                _nf2, _ne, loose, many = _solids.edge_report(parts)
                if loose or many:
                    n_open += 1
                for part in parts:
                    pg = QgsPolygon()
                    for k2, ring in enumerate(part):
                        ls = QgsLineString(
                            [QgsPoint(x, y, z) for x, y, z in ring])
                        if k2 == 0:
                            pg.setExteriorRing(ls)
                        else:
                            pg.addInteriorRing(ls)
                    mp.addGeometry(pg)
                    n_parts += 1
            if n_parts:
                sf = QgsFeature(smem.fields())
                sf.setGeometry(QgsGeometry(mp))
                sf.setAttributes([z_lo, z_hi, 1])
                solid_feats.append(sf)
        if i % 200 == 0:
            feedback.setProgress(int(100.0 * i / n_total))
    if n_norep or n_nodata:
        feedback.pushWarning(_tr(
            "Граней выпало из покрытия: %d без репрезентативной точки "
            "(площадь %.4g), %d с выборкой на nodata (площадь %.4g). В "
            "покрытии останутся дыры этой площади. Чаще всего это мелкая "
            "грань в тройном узле у края маски или у разлома.")
            % (n_norep, a_norep, n_nodata, a_nodata))
    if n_thin:
        feedback.pushInfo(_tr(
            "Отброшено полос тоньше %.3g: %d. Такой полигон не возникает из "
            "склона, он только из скачка между соседними ячейками - у "
            "разлома, обрыва или края области.") % (min_thick, n_thin))
        # Порог задуман против обрывков у разрыва, а не против нормальных
        # поясов. На крутой поверхности с частым сечением пояс между
        # соседними уровнями сам по себе уже ячейки, и порог в ячейку
        # выкашивает карту. Молчать об этом нельзя: на выходе останется
        # горстка полигонов, и причина будет неочевидна.
        if n_thin * 2 > n_total:
            feedback.pushWarning(_tr(
                "Отсеяно больше половины поясов (%d из %d). Порог толщины "
                "задуман против обрывков у разрыва. На крутой поверхности "
                "с частым сечением нормальный пояс сам по себе уже ячейки, "
                "и порог выкашивает карту. Уменьшите порог или поставьте "
                "ноль.") % (n_thin, n_total))
    if not out_feats:
        raise QgsProcessingException(_tr("Ни один пояс не получил значения."))
    dp.addFeatures(out_feats)
    mem.updateExtents()
    belts = _save(processing, mem, final_output, context, feedback)
    if sdp is None:
        return belts
    if n_open:
        feedback.pushWarning(_tr(
            "Незамкнутых оболочек: %d. Проверьте слой тел скриптом "
            "tools/check_solids.py.") % n_open)
    if not solid_feats:
        feedback.pushWarning(_tr(
            "Тел не построено: у поясов нулевая мощность или пустые "
            "кольца."))
        return {"belts": belts, "solids": None}
    sdp.addFeatures(solid_feats)
    smem.updateExtents()
    feedback.pushInfo(_tr(
        "Тел построено: %d. Каждое замкнуто: крышка снизу на ELEV_MIN, "
        "сверху на ELEV_MAX, стенки по всем кольцам, включая дыры.")
        % len(solid_feats))
    return {"belts": belts,
            "solids": _save(processing, smem, solids_output, context,
                            feedback)}



def isolines_and_polygons(raster, band, interval, base, levels_text,
                          index_every, min_length, smooth, smooth_radius,
                          densify, line_iter, field_name, ignore_nodata, nodata,
                          lines_output, polygons_output, context, feedback,
                          slope_ref=None, uphill_ref=None,
                          hatch_flip=0, min_thick=0.0, faults=None,
                          corridor=0.0, with_z=False, thin=0.0,
                          solids_output=None):
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
                         context, feedback, thin=thin)
    # Разлом режет изолинии по векторной части, а не по гриду: ячейка не
    # передаёт диагональ, и вырезанный из грида барьер шёл бы ступеньками.
    # Здесь линия точная, разрез идёт ровно по ней.
    iso = _split_by_faults(processing, iso, faults, corridor, context, feedback)

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
    iso_single = _clean_lines(iso_single, context, feedback,
                              _tr("разбор мультичастей"))
    feedback.pushInfo(_tr("Продление открытых концов за контур…"))
    iso_for_poly = _extend_free_ends(iso_single, faults, over,
                                     context, feedback,
                                     hold_r=float(corridor) * 1.1)
    polys_src = _polygonize_belts(processing, iso_for_poly, area_lines, crs,
                                  context, feedback, faults=faults)
    polys_out = _belts_to_layer(processing, polys_src, arr, valid, gt, levels,
                                crs, polygons_output, context, feedback,
                                min_thick=min_thick, with_z=with_z,
                                solids_output=solids_output)
    solids_out = None
    if isinstance(polys_out, dict):
        polys_out, solids_out = polys_out["belts"], polys_out["solids"]

    return {"lines": lines_out, "polygons": polys_out, "solids": solids_out}


def add_z_from_field(layer_path, field_name, context, feedback=None):
    """Поднять вершины линий на отметку из поля: LineString -> LineStringZ.

    Изолиния это линия равного уровня, поэтому Z у всех её вершин один и
    равен значению поля. Такой слой при экспорте в DXF доносит высоту в
    АвтоКАД и Кредо без ручного «задать Z». Работает над готовым слоем на
    диске: перечитывает, переписывает геометрию, кладёт рядом _z-версию и
    возвращает её путь. Плоский слой не портится.

    Возвращает путь к слою с Z или исходный путь, если что-то пошло не так
    (тогда высота остаётся в поле, как раньше).
    """
    from qgis.core import (QgsVectorLayer, QgsVectorFileWriter, QgsFeature,
                           QgsGeometry, QgsLineString, QgsMultiLineString,
                           QgsPoint, QgsWkbTypes)
    src = QgsVectorLayer(layer_path, "iso", "ogr")
    if not src.isValid():
        return layer_path
    idx = src.fields().indexOf(field_name)
    if idx < 0:
        if feedback is not None:
            feedback.pushWarning(
                "Поле высоты %s не найдено, Z не записан." % field_name)
        return layer_path

    fields = src.fields()
    out_path = layer_path
    lower = layer_path.lower()
    for ext in (".gpkg", ".shp", ".geojson", ".json"):
        if lower.endswith(ext):
            out_path = layer_path[:-len(ext)] + "_z" + ext
            break
    else:
        out_path = layer_path + "_z.gpkg"

    driver = ("GPKG" if out_path.lower().endswith(".gpkg")
              else "ESRI Shapefile" if out_path.lower().endswith(".shp")
              else "GeoJSON")
    # Многочастность источника сохраняем: изолиния одного уровня приходит
    # одним объектом со всеми своими ветвями, и разбор её на отдельные
    # объекты множит записи и дублирует подписи. Тип выхода берём от входа.
    try:
        multi = QgsWkbTypes.isMultiType(src.wkbType())
    except Exception:  # nosec
        multi = False
    out_wkb = (QgsWkbTypes.Type.MultiLineStringZ if multi
               else QgsWkbTypes.Type.LineStringZ)

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = driver
    ctx = context.transformContext() if context is not None else None
    try:
        writer = QgsVectorFileWriter.create(
            out_path, fields, out_wkb, src.crs(), ctx, opts)
    except Exception:  # старые сигнатуры QGIS
        writer = QgsVectorFileWriter(
            out_path, "UTF-8", fields, out_wkb, src.crs(), driver)
    if writer.hasError() != QgsVectorFileWriter.WriterError.NoError:
        return layer_path

    n = 0
    for feat in src.getFeatures():
        try:
            z = float(feat[idx])
        except (TypeError, ValueError):
            z = 0.0
        geom = feat.geometry()
        if geom is None or geom.isEmpty():
            continue
        try:
            parts = (geom.asMultiPolyline() if geom.isMultipart()
                     else [geom.asPolyline()])
        except TypeError:  # QGIS 4: asMultiPolyline() на одиночной геометрии
            parts = [geom.asPolyline()]
        rings = [QgsLineString([QgsPoint(p.x(), p.y(), z) for p in pts])
                 for pts in parts if len(pts) >= 2]
        if not rings:
            continue
        if multi:
            ml = QgsMultiLineString()
            for ls in rings:
                ml.addGeometry(ls)
            fo = QgsFeature(fields)
            fo.setAttributes(feat.attributes())
            fo.setGeometry(QgsGeometry(ml))
            writer.addFeature(fo)
            n += 1
        else:
            for ls in rings:
                fo = QgsFeature(fields)
                fo.setAttributes(feat.attributes())
                fo.setGeometry(QgsGeometry(ls))
                writer.addFeature(fo)
                n += 1
    del writer
    if feedback is not None:
        feedback.pushInfo(
            "Высота записана в Z геометрии: %d линий, поле %s."
            % (n, field_name))
    return out_path
