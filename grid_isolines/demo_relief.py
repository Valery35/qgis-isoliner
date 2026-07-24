# -*- coding: utf-8 -*-
"""Генератор демо-рельефа. Служебная роль: headless-тесты, скриншоты
руководства, работа без сети. Не подменяет живые данные из 2.01.

Рельеф детерминированный по seed: наклонная равнина, холмы Гауссова
профиля, извилистая долина с постоянным падением вдоль оси. Долина
гарантирует, что извлечение речной сети из результата даёт внятный
водоток, а горизонтали выглядят естественно.

Режим ravine добавляет овражно-балочную сеть: врезанные тальвеги с
крутыми бортами, ветвящиеся под острым углом к главному стволу. Такой
рельеф - самая тяжёлая проверка для интерполяции по горизонталям: узкие
врезы между соседними горизонталями срезаются, а на профиле поперёк
оврага это видно сразу. Для проверочных наборов и картинок к валидации.
"""

import numpy as np

DEFAULT_NX = 300
DEFAULT_NY = 300
DEFAULT_CELL = 30.0
DEFAULT_SEED = 42
DEFAULT_BASE_Z = 200.0


def _ravine_network(xx, yy, nx, ny, cell, rng, n_main=2):
    """Овражно-балочная сеть: главные тальвеги плюс отвершки.

    Каждый тальвег - ломаная сверху вниз по склону с плавным изгибом. Врез
    считается по расстоянию до оси в виде узкого гауссова профиля, глубина
    нарастает вниз по течению, как у реального оврага: вершина мелкая,
    устье глубокое. Отвершки короче и мельче, входят под острым углом.
    """
    cut = np.zeros_like(xx, dtype=np.float64)

    def carve(x0, y0, x1, y1, depth, half_w, bend):
        """Врез вдоль оси от (x0,y0) к (x1,y1) с изгибом bend."""
        n = 160
        t = np.linspace(0.0, 1.0, n)
        ax = x0 + (x1 - x0) * t + bend * np.sin(np.pi * t)
        ay = y0 + (y1 - y0) * t
        # расстояние до ломаной считаем по ближайшему узлу: для узкого
        # профиля этого достаточно, а стоит копейки
        d2 = np.full(xx.shape, np.inf)
        deep = np.zeros(xx.shape)
        for k in range(n):
            dk = (xx - ax[k]) ** 2 + (yy - ay[k]) ** 2
            closer = dk < d2
            d2 = np.where(closer, dk, d2)
            deep = np.where(closer, depth * (0.25 + 0.75 * t[k]), deep)
        return deep * np.exp(-d2 / (2.0 * half_w ** 2))

    for m in range(int(n_main)):
        x0 = rng.uniform(0.25, 0.75) * nx
        x1 = x0 + rng.uniform(-0.12, 0.12) * nx
        depth = rng.uniform(18.0, 30.0)
        half_w = rng.uniform(0.010, 0.018) * nx
        bend = rng.uniform(-0.08, 0.08) * nx
        cut = np.maximum(cut, carve(x0, 0.10 * ny, x1, 0.92 * ny,
                                    depth, half_w, bend))
        # отвершки: короткие, входят сбоку под острым углом
        for _ in range(int(rng.integers(2, 4))):
            ty = rng.uniform(0.35, 0.80) * ny
            side = 1.0 if rng.random() < 0.5 else -1.0
            length = rng.uniform(0.12, 0.22) * ny
            sx = x0 + side * rng.uniform(0.10, 0.20) * nx
            cut = np.maximum(cut, carve(
                sx, ty - length, x0 + side * 0.01 * nx, ty,
                depth * rng.uniform(0.45, 0.7), half_w * rng.uniform(0.6, 0.9),
                side * rng.uniform(0.0, 0.03) * nx))
    return cut


def generate(nx=DEFAULT_NX, ny=DEFAULT_NY, cell=DEFAULT_CELL,
             seed=DEFAULT_SEED, base_z=DEFAULT_BASE_Z, ravine=False):
    """Возвращает 2D float32 грид (ny, nx), строка 0 - северный край.

    ravine=True добавляет овражно-балочную сеть поверх обычного рельефа.
    """
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

    if ravine:
        # Овраги режем последними, поверх готовой формы: так борта выходят
        # крутыми, а не размазанными холмами.
        z -= _ravine_network(xx, yy, nx, ny, cell, rng)

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


# --- проектная площадка для примерки объёмов (2.18) ----------------------

def design_pad(z, frac=0.4, zones=3, dz=0.0):
    """Проектная поверхность: горизонтальная площадка в середине рельефа.

    Нужна для примерки инструмента 2.18: чтобы считать объёмы, нужна пара
    поверхностей, а демо до сих пор выдавало одну.

    Отметка площадки берётся средним рельефа внутри области, а не круглым
    числом и не медианой. Это не вкусовщина, а точный ответ: объём равен
    сумме разностей на площадь ячейки, значит нетто обращается в ноль
    ровно тогда, когда отметка равна среднему. Медиана делит пополам
    ячейки, а не кубометры, и баланс при ней не сходится. Круглое число
    вообще легко даёт вырожденный случай, где вся площадка выше рельефа.

    Сдвиг dz поднимает или опускает площадку от этой отметки, чтобы на
    демо можно было получить и привозной грунт, и вывозной.

    Возвращает (design, bounds, pad_z, zone_bounds), где bounds это
    (r0, r1, c0, c1) полуинтервалами, а zone_bounds список таких же
    четвёрок, режущих область по столбцам.
    """
    z = np.asarray(z, dtype=float)
    ny, nx = z.shape
    frac = float(frac)
    if not (0.05 <= frac <= 0.9):
        raise ValueError("Доля области работ должна быть от 0.05 до 0.9.")
    zones = max(1, int(zones))

    hw = max(2, int(round(nx * frac / 2.0)))
    hh = max(2, int(round(ny * frac / 2.0)))
    cx, cy = nx // 2, ny // 2
    c0, c1 = max(0, cx - hw), min(nx, cx + hw)
    r0, r1 = max(0, cy - hh), min(ny, cy + hh)
    if c1 - c0 < zones:
        zones = max(1, c1 - c0)

    inside = z[r0:r1, c0:c1]
    pad_z = float(np.nanmean(inside)) + float(dz)

    design = z.astype(float).copy()
    design[r0:r1, c0:c1] = pad_z

    edges = np.linspace(c0, c1, zones + 1)
    zone_bounds = []
    for k in range(zones):
        a, b = int(round(edges[k])), int(round(edges[k + 1]))
        if b > a:
            zone_bounds.append((r0, r1, a, b))
    return design, (r0, r1, c0, c1), pad_z, zone_bounds
