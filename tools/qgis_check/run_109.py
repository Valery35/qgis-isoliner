# -*- coding: utf-8 -*-
"""1.09 Кросс-валидация метода: прежний класс против нового.

python tools/qgis_check/run_109.py <папка с grid_isolines_old> [отчёт.json]

Выборка проверяемых точек случайная, когда их больше N. Оба класса
зовут один и тот же генератор NumPy, поэтому с заданным зерном выбор
совпадает.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    from run_106 import grouped_layer
    from run_108 import model_table
    demo = datasets.demo_profiles_layer()
    sample = datasets.clustered_layer()
    grp = grouped_layer()
    vgt = model_table()
    base = {"METHOD": 0, "N_VALIDATE": 0, "SEED": 5, "EXCL_X": 0.0,
            "EXCL_Y": 0.0, "KTYPE": 0, "RADIUS": 0.0, "MIN_POINTS": 1,
            "MAX_POINTS": 24, "S1_SILL": 300.0, "S1_RANGE": 250.0}
    ext = "500100,500800,6400100,6400700 [EPSG:32640]"

    def c(layer, z, **kw):
        d = dict(base, INPUT=layer, ZFIELD=z)
        d.update(kw)
        return d

    cases = [
        ("kriging, random 100 of 184, seed 5", c(demo, "rho_k")),
        ("kriging, all points", c(demo, "rho_k", N_VALIDATE=200)),
        ("kriging, exclusion buffer", c(demo, "rho_k", N_VALIDATE=200,
                                        EXCL_X=30.0, EXCL_Y=30.0)),
        ("kriging, filter extent and z", c(demo, "rho_k", N_VALIDATE=200,
                                           FILTER_EXTENT=ext, ZMIN=8.0,
                                           ZMAX=60.0)),
        ("kriging, table profile", c(demo, "rho_k", N_VALIDATE=200,
                                     VG_TABLE=vgt, VG_NAME="rho_aniso")),
        ("kriging, id field and weights", c(grp, "au", IDFIELD="kind",
                                            WEIGHT_FIELD="wt")),
        ("kriging, gaussian", c(demo, "z", N_VALIDATE=60, S1_MODEL=2,
                                S1_SILL=50.0, S1_RANGE=400.0)),
        ("mincurv, default", c(demo, "rho_k", METHOD=1, N_VALIDATE=40)),
        ("mincurv, extent cell tension", c(demo, "z", METHOD=1,
                                           N_VALIDATE=40, EXTENT=ext,
                                           CELL_SIZE=20.0, TENSION=0.3)),
        ("mincurv, exclusion", c(sample, "au", METHOD=1, EXCL_X=20.0,
                                 EXCL_Y=20.0)),
        ("error: filters leave fewer than two",
         c(demo, "rho_k", ZMIN=1e9)),
        ("error: no Z, no field", c(sample, "")),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_109.json")
    bad = common.compare(cases, old, new, "MethodCrossValidationAlgorithm",
                         ["OUTPUT"], out, files=["OUTPUT_HTML"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
