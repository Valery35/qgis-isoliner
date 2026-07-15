# -*- coding: utf-8 -*-
"""Генератор демо-рельефа. Служебная роль: headless-тесты, скриншоты
руководства, работа без сети. Не подменяет живые данные из 2.01.

Рельеф детерминированный по seed: наклонная равнина, холмы Гауссова
профиля, извилистая долина с постоянным падением вдоль оси. Долина
гарантирует, что извлечение речной сети из результата даёт внятный
водоток, а горизонтали выглядят естественно.
"""

import numpy as np

DEFAULT_NX = 300
DEFAULT_NY = 300
DEFAULT_CELL = 30.0
DEFAULT_SEED = 42
DEFAULT_BASE_Z = 200.0


def generate(nx=DEFAULT_NX, ny=DEFAULT_NY, cell=DEFAULT_CELL,
             seed=DEFAULT_SEED, base_z=DEFAULT_BASE_Z):
    """Возвращает 2D float32 грид (ny, nx), строка 0 - северный край."""
    nx = int(nx)
    ny = int(ny)
    if nx < 20 or ny < 20:
        raise ValueError("Минимальный размер демо-рельефа 20x20 ячеек.")
    rng = np.random.default_rng(int(seed))

    x = np.arange(nx, dtype=np.float64)
    y = np.arange(ny, dtype=np.float64)
    xx, yy = np.meshgrid(x, y)

    # Наклонная равнина: общее падение с севера на юг и с востока на запад.
    z = base_z + 0.15 * (ny - 1 - yy) * (cell / 30.0) \
        + 0.05 * xx * (cell / 30.0)

    # Холмы: 6-10 гауссиан со случайными позициями и размерами.
    n_hills = int(rng.integers(6, 11))
    for _ in range(n_hills):
        cx = rng.uniform(0.1 * nx, 0.9 * nx)
        cy = rng.uniform(0.1 * ny, 0.9 * ny)
        amp = rng.uniform(15.0, 60.0)
        sx = rng.uniform(0.04 * nx, 0.12 * nx)
        sy = rng.uniform(0.04 * ny, 0.12 * ny)
        z += amp * np.exp(-(((xx - cx) / sx) ** 2 + ((yy - cy) / sy) ** 2))

    # Долина: синусоидальная ось с юга на север, врез Гауссова профиля,
    # дно с монотонным падением к южному краю (строка ny-1).
    amp_m = rng.uniform(0.08, 0.18) * nx
    phase = rng.uniform(0.0, 2.0 * np.pi)
    axis_x = 0.5 * nx + amp_m * np.sin(
        2.0 * np.pi * yy / ny * rng.uniform(1.0, 2.0) + phase)
    half_width = rng.uniform(0.03, 0.06) * nx
    depth = rng.uniform(25.0, 45.0)
    cut = depth * np.exp(-((xx - axis_x) / half_width) ** 2)
    z -= cut

    # Общее падение равнины на юг уже даёт монотонный сток вдоль долины.
    return z.astype(np.float32)


def write_geotiff(z, out_path, gdal_module, osr_module, cell=DEFAULT_CELL,
                  epsg=32640, wkt=None, origin_x=500000.0,
                  origin_y=6500000.0,
                  as_int16=False):
    """Пишет грид в GeoTIFF. int16 вариант для компактного демо в поставке."""
    ny, nx = z.shape
    driver = gdal_module.GetDriverByName("GTiff")
    dtype = gdal_module.GDT_Int16 if as_int16 else gdal_module.GDT_Float32
    ds = driver.Create(out_path, nx, ny, 1, dtype,
                       options=["COMPRESS=DEFLATE", "PREDICTOR=2"])
    ds.SetGeoTransform((origin_x, float(cell), 0.0,
                        origin_y, 0.0, -float(cell)))
    srs = osr_module.SpatialReference()
    if wkt is not None:
        srs.ImportFromWkt(wkt)
    else:
        srs.ImportFromEPSG(int(epsg))
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    nodata = -32768
    band.SetNoDataValue(nodata)
    data = np.round(z).astype(np.int16) if as_int16 else z.astype(np.float32)
    band.WriteArray(data)
    band.FlushCache()
    ds = None
    return out_path
