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


def fetch_dem(extent_4326, out_path, gdal_module, osr_module,
              dst_epsg=None, dst_wkt=None, cell=DEFAULT_CELL,
              max_tiles=DEFAULT_MAX_TILES, feedback=None):
    """Полный цикл: плитки, VRT-мозаика, варп в метрическую СК.

    extent_4326: (lon_min, lat_min, lon_max, lat_max).
    Целевая СК задаётся кодом dst_epsg либо строкой dst_wkt: WKT
    покрывает пользовательские СК без кода EPSG (локальные шахтные
    сетки). Без обоих берётся UTM по центру рамки.
    Возвращает (out_path, использованные_плитки).
    """
    lon_min, lat_min, lon_max, lat_max = extent_4326
    names = tiles_for_bbox(lon_min, lat_min, lon_max, lat_max, max_tiles)
    if feedback:
        feedback.pushInfo("Плиток по рамке: {}".format(len(names)))
    paths = open_existing_tiles(names, gdal_module)
    if feedback:
        feedback.pushInfo("Найдено в бакете: {} из {}".format(len(paths), len(names)))

    if dst_epsg is None and dst_wkt is None:
        dst_epsg = utm_epsg_for((lon_min + lon_max) / 2.0,
                                (lat_min + lat_max) / 2.0)

    vrt_path = "/vsimem/glo30_mosaic.vrt"
    vrt = gdal_module.BuildVRT(vrt_path, paths)
    if vrt is None:
        raise DemSourceError("Не удалось собрать VRT-мозаику из плиток.")

    src_srs = osr_module.SpatialReference()
    src_srs.ImportFromEPSG(4326)
    src_srs.SetAxisMappingStrategy(osr_module.OAMS_TRADITIONAL_GIS_ORDER)
    dst_srs = osr_module.SpatialReference()
    if dst_wkt is not None:
        dst_srs.ImportFromWkt(dst_wkt)
    else:
        dst_srs.ImportFromEPSG(int(dst_epsg))
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
        dstNodata=-32768.0,
        format="GTiff",
        creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "PREDICTOR=2"],
        multithread=True,
    )
    out_ds = gdal_module.Warp(out_path, vrt, options=warp_opts)
    vrt = None
    gdal_module.Unlink(vrt_path)
    if out_ds is None:
        raise DemSourceError(
            "Перепроецирование не удалось. Проверьте доступ к сети: "
            "варп читает данные плиток по HTTP."
        )
    out_ds = None
    return out_path, paths
