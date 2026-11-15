# -*- coding: utf-8 -*-
"""Живая загрузка GEM-15 по рамке без QGIS: проверка источника 2.01.

python tools/gem15_live.py <выход.tif> lon_min lat_min lon_max lat_max
                           [dtm|dsm] [ячейка]
Печатает журнал, размер растра и долю заполненных ячеек.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "grid_isolines"))

from osgeo import gdal, osr  # noqa: E402

import dem_glo30 as dem  # noqa: E402


class FB(object):
    def pushInfo(self, s):
        print("info:", s)

    def pushWarning(self, s):
        print("warn:", s)


def main(argv):
    out = argv[1]
    ext = tuple(float(v) for v in argv[2:6])
    band = argv[6] if len(argv) > 6 else "dtm"
    cell = float(argv[7]) if len(argv) > 7 else 15.0
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    try:
        path, srcs = dem.fetch_dem(ext, out, gdal, osr, cell=cell,
                                   source=dem.SOURCE_GEM15, feedback=FB(),
                                   gem15_band=band)
    except dem.DemSourceError as exc:
        print("REFUSED:", exc)
        return 2
    ds = gdal.Open(path)
    b = ds.GetRasterBand(1)
    print("raster:", path, ds.RasterXSize, "x", ds.RasterYSize,
          "cell", ds.GetGeoTransform()[1], "bytes", os.path.getsize(path))
    st = b.ComputeStatistics(False)
    print("min %.2f max %.2f mean %.2f" % (st[0], st[1], st[2]))
    print("filled share %.4f" % dem.valid_share(path, gdal, -9999.0))
    print("sources:", len(srcs))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
