# -*- coding: utf-8 -*-
"""Загрузка Copernicus DEM GLO-30 из открытого AWS-бакета.

Плитки 1 на 1 градус, формат COG, доступ без регистрации и ключей
через GDAL /vsicurl/. Плитки океана в бакете отсутствуют, это норма.
Севернее 50 широты у GLO-30 шаг по долготе крупнее (1.5 сек и более),
поэтому сырую градусную плитку в анализ не пускаем никогда:
выход всегда перепроецирован в метрическую СК с кубической интерполяцией.
"""

import math

BUCKET_URL = "https://copernicus-dem-30m.s3.amazonaws.com"
DEFAULT_CELL = 30.0
DEFAULT_MAX_TILES = 25

# Источники ЦМР
SOURCE_GLO30 = "glo30"
SOURCE_GEDTM30 = "gedtm30"

# GEDTM30: единый глобальный COG, bare-earth DTM, CC BY 4.0.
# Слой edtm - предсказанная высота рельефа (не uncertainty и не маска).
# Имя по конвенции Open-Earth-Monitor, версия v20250611, EGM2008 geoid.
GEDTM30_COG = (
    "https://s3.opengeohub.org/global/edtm/"
    "gedtm_rf_m_30m_s_20060101_20151231_go_epsg.4326.3855_v20250611.tif"
)
GEDTM30_NODATA = -2147483647   # Int32 no-data сырого COG (Zenodo)
# COG несёт scale=0.1 в метаданных банда, GDAL применяет его при чтении
# с флагом -unscale, отдавая настоящие метры. Вручную не делим.


class DemSourceError(Exception):
    """Ошибка источника ЦМР с внятным текстом для пользователя."""


def tile_name(lat, lon):
    """Имя плитки GLO-30 по юго-западному углу градусной ячейки."""
    f_lat = int(math.floor(lat))
    f_lon = int(math.floor(lon))
    ns = "N{:02d}".format(f_lat) if f_lat >= 0 else "S{:02d}".format(-f_lat)
    ew = "E{:03d}".format(f_lon) if f_lon >= 0 else "W{:03d}".format(-f_lon)
    return "Copernicus_DSM_COG_10_{}_00_{}_00_DEM".format(ns, ew)


def tile_url(name):
    return "{}/{}/{}.tif".format(BUCKET_URL, name, name)


def vsicurl_path(url):
    return "/vsicurl/" + url


def tiles_for_bbox(lon_min, lat_min, lon_max, lat_max, max_tiles=DEFAULT_MAX_TILES):
    """Список имён плиток, покрывающих рамку в EPSG:4326.

    Рамку через антимеридиан не поддерживаем, площадь ограничена
    max_tiles, чтобы случайный клик по глобусу не тянул полмира.
    """
    if lon_max <= lon_min or lat_max <= lat_min:
        raise DemSourceError("Пустая рамка: проверьте координаты.")
    if lon_min < -180.0 or lon_max > 180.0 or lat_min < -90.0 or lat_max > 90.0:
        raise DemSourceError(
            "Рамка выходит за пределы EPSG:4326. "
            "Рамка через антимеридиан не поддерживается."
        )
    eps = 1e-9
    lon_a = int(math.floor(lon_min))
    lon_b = int(math.floor(lon_max - eps))
    lat_a = int(math.floor(lat_min))
    lat_b = int(math.floor(lat_max - eps))
    n_tiles = (lon_b - lon_a + 1) * (lat_b - lat_a + 1)
    if n_tiles > max_tiles:
        raise DemSourceError(
            "Рамка требует {} плиток при пределе {}. "
            "Уменьшите рамку или поднимите предел в параметрах.".format(
                n_tiles, max_tiles
            )
        )
    names = []
    for la in range(lat_a, lat_b + 1):
        for lo in range(lon_a, lon_b + 1):
            names.append(tile_name(la, lo))
    return names


def utm_epsg_for(lon, lat):
    """EPSG зоны UTM по точке (обычно центр рамки)."""
    zone = int(math.floor((lon + 180.0) / 6.0)) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


def open_existing_tiles(names, gdal_module):
    """Открывает плитки через /vsicurl/, отсутствующие (океан) пропускает.

    Возвращает список vsicurl-путей существующих плиток.
    Если не открылась ни одна, различаем две ситуации:
    сеть недоступна вовсе или рамка целиком в океане.
    """
    gdal_module.UseExceptions()
    found = []
    for name in names:
        path = vsicurl_path(tile_url(name))
        try:
            ds = gdal_module.Open(path)
        except RuntimeError:
            ds = None
        if ds is not None:
            found.append(path)
            ds = None
    if not found:
        raise DemSourceError(
            "Не удалось открыть ни одной плитки GLO-30. "
            "Либо нет доступа к сети (проверьте соединение и прокси), "
            "либо рамка целиком лежит в океане."
        )
    return found


def _resolve_dst_srs(osr_module, dst_epsg, dst_wkt, center_lonlat):
    """Целевая метрическая СК: WKT, EPSG или UTM по центру рамки."""
    dst_srs = osr_module.SpatialReference()
    if dst_wkt is not None:
        dst_srs.ImportFromWkt(dst_wkt)
    elif dst_epsg is not None:
        dst_srs.ImportFromEPSG(int(dst_epsg))
    else:
        dst_srs.ImportFromEPSG(utm_epsg_for(*center_lonlat))
    return dst_srs


def _warp_to_metric(src_ds_or_path, out_path, extent_4326, gdal_module,
                    osr_module, dst_srs, cell, nodata, dst_nodata=None):
    """Общий варп источника в метрическую СК с обрезкой по рамке."""
    lon_min, lat_min, lon_max, lat_max = extent_4326
    src_srs = osr_module.SpatialReference()
    src_srs.ImportFromEPSG(4326)
    src_srs.SetAxisMappingStrategy(osr_module.OAMS_TRADITIONAL_GIS_ORDER)
    tr = osr_module.CoordinateTransformation(src_srs, dst_srs)
    xs, ys = [], []
    for lon, lat in ((lon_min, lat_min), (lon_min, lat_max),
                     (lon_max, lat_min), (lon_max, lat_max)):
        x, y, _ = tr.TransformPoint(lon, lat)
        xs.append(x)
        ys.append(y)
    bounds = (min(xs), min(ys), max(xs), max(ys))
    warp_opts = gdal_module.WarpOptions(
        dstSRS=dst_srs.ExportToWkt(),
        xRes=float(cell), yRes=float(cell),
        resampleAlg="cubic",
        outputBounds=bounds,
        srcNodata=nodata,
        dstNodata=(dst_nodata if dst_nodata is not None else nodata),
        format="GTiff",
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "PREDICTOR=2"],
        multithread=True,
    )
    out_ds = gdal_module.Warp(out_path, src_ds_or_path, options=warp_opts)
    if out_ds is None:
        raise DemSourceError(
            "Перепроецирование не удалось. Проверьте доступ к сети: "
            "варп читает данные источника по HTTP."
        )
    out_ds = None
    return out_path


def fetch_dem(extent_4326, out_path, gdal_module, osr_module,
              dst_epsg=None, dst_wkt=None, cell=DEFAULT_CELL,
              max_tiles=DEFAULT_MAX_TILES, source=SOURCE_GLO30,
              feedback=None):
    """Полный цикл загрузки ЦМР по рамке в метрическую СК.

    extent_4326: (lon_min, lat_min, lon_max, lat_max).
    source: SOURCE_GLO30 (Copernicus GLO-30, плиточная мозаика) или
    SOURCE_GEDTM30 (единый глобальный COG bare-earth DTM, CC BY 4.0).
    Целевая СК задаётся кодом dst_epsg либо строкой dst_wkt (для
    пользовательских СК без кода EPSG). Без обоих берётся UTM по центру.
    Возвращает (out_path, список_источников).
    """
    lon_min, lat_min, lon_max, lat_max = extent_4326
    center = ((lon_min + lon_max) / 2.0, (lat_min + lat_max) / 2.0)
    dst_srs = _resolve_dst_srs(osr_module, dst_epsg, dst_wkt, center)

    if source == SOURCE_GEDTM30:
        cog = vsicurl_path(GEDTM30_COG)
        if feedback:
            feedback.pushInfo("Источник: GEDTM30 (единый COG, bare-earth).")
        try:
            probe = gdal_module.Open(cog)
        except RuntimeError:
            probe = None
        if probe is None:
            raise DemSourceError(
                "Не удалось открыть COG GEDTM30. Проверьте соединение "
                "и прокси, либо источник временно недоступен."
            )
        probe = None
        # COG хранит высоту как Int32 со scale-метаданными. Warp scale
        # не применяет, поэтому сперва translate -unscale переводит в
        # настоящие метры (Float32), затем warp в метрическую СК.
        unscaled = "/vsimem/gedtm_unscaled.tif"
        tr = gdal_module.Translate(
            unscaled, cog,
            options=gdal_module.TranslateOptions(
                unscale=True, outputType=gdal_module.GDT_Float32,
                noData=-9999.0))
        if tr is None:
            raise DemSourceError(
                "Не удалось прочитать COG GEDTM30 (unscale).")
        tr = None
        _warp_to_metric(unscaled, out_path, extent_4326, gdal_module,
                        osr_module, dst_srs, cell, -9999.0)
        gdal_module.Unlink(unscaled)
        if feedback:
            feedback.pushInfo("GEDTM30: высоты приведены к метрам.")
        return out_path, [cog]

    # SOURCE_GLO30: плиточная мозаика
    names = tiles_for_bbox(lon_min, lat_min, lon_max, lat_max, max_tiles)
    if feedback:
        feedback.pushInfo("Плиток по рамке: {}".format(len(names)))
    paths = open_existing_tiles(names, gdal_module)
    if feedback:
        feedback.pushInfo("Найдено в бакете: {} из {}".format(
            len(paths), len(names)))
    vrt_path = "/vsimem/glo30_mosaic.vrt"
    vrt = gdal_module.BuildVRT(vrt_path, paths)
    if vrt is None:
        raise DemSourceError("Не удалось собрать VRT-мозаику из плиток.")
    _warp_to_metric(vrt, out_path, extent_4326, gdal_module, osr_module,
                    dst_srs, cell, -32768.0)
    vrt = None
    gdal_module.Unlink(vrt_path)
    return out_path, paths
