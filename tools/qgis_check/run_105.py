# -*- coding: utf-8 -*-
"""1.05 Декластеризация: прежний класс против нового в QGIS без окна.

python tools/qgis_check/run_105.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    from qgis.PyQt.QtCore import QVariant
    demo = datasets.demo_profiles_layer()
    sample = datasets.clustered_layer()
    zlayer = datasets.clustered_layer(with_z=True)
    single = common.memory_layer("Point", "EPSG:32640", [("v", QVariant.Double)],
                                 [("POINT(0 0)", [1.0]), ("POINT(5 5)", [None])],
                                 "single")
    base = {"MODE": 0, "CELL_SIZE": 0.0, "OBJECTIVE": 0, "NCELL": 24,
            "ASPECT": 1.0, "NOFF": 4}

    def c(layer, z, **kw):
        d = dict(base, INPUT=layer, ZFIELD=z)
        d.update(kw)
        return d

    cases = [
        ("demo 1.11 auto min", c(demo, "rho_k")),
        ("demo 1.11 auto max", c(demo, "rho_k", OBJECTIVE=1)),
        ("demo 1.11 auto ncell 10 noff 1", c(demo, "sp", NCELL=10, NOFF=1)),
        ("demo 1.11 manual cell 0", c(demo, "rho_k", MODE=1)),
        ("demo 1.11 manual 60 aspect 2 noff 8",
         c(demo, "vp", MODE=1, CELL_SIZE=60.0, ASPECT=2.0, NOFF=8)),
        ("sample auto min", c(sample, "au")),
        ("sample auto max aspect 0.5", c(sample, "au", OBJECTIVE=1,
                                         ASPECT=0.5)),
        ("sample manual 120", c(sample, "au", MODE=1, CELL_SIZE=120.0)),
        ("sample Z of geometry", c(zlayer, "")),
        ("error: no Z, no field", c(sample, "")),
        ("error: one valid point", c(single, "v")),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_105.json")
    bad = common.compare(cases, old, new, "DeclusteringAlgorithm",
                         ["OUTPUT"], out, files=["OUTPUT_HTML"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
