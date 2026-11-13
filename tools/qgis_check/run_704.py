# -*- coding: utf-8 -*-
"""7.04 Размерность Минковского: прежний класс против нового.

python tools/qgis_check/run_704.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    lines, polys = datasets.dimension_layers()
    base = {"N_SIZES": 8, "OFFSETS": 3, "DENSIFY": 0.5}
    cases = [
        ("lines default", dict(base, FEATURES=lines)),
        ("polygons with a hole", dict(base, FEATURES=polys)),
        ("lines K 4 offsets 1 vertices only",
         dict(base, FEATURES=lines, N_SIZES=4, OFFSETS=1, DENSIFY=0.0)),
        ("lines K 12 offsets 5 densify 2",
         dict(base, FEATURES=lines, N_SIZES=12, OFFSETS=5, DENSIFY=2.0)),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_704.json")
    bad = common.compare(cases, old, new, "MinkowskiDimensionAlgorithm",
                         ["OUTPUT"], out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
