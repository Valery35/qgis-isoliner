# -*- coding: utf-8 -*-
"""7.05 Пример для фракталов: прежний класс против нового.

python tools/qgis_check/run_705.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    ext = "500000,501000,6400000,6400800 [EPSG:32640]"
    cases = [("seed 1", {"EXTENT": ext, "SEED": 1}),
             ("seed 0", {"EXTENT": ext, "SEED": 0}),
             ("seed 42 tall extent", {"EXTENT": "0,300,0,2000 [EPSG:32640]",
                                      "SEED": 42})]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_705.json")
    bad = common.compare(cases, old, new, "FractalDemoAlgorithm",
                         ["OUT_RIVERS", "OUT_BASIN", "OUT_COAST"], out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
