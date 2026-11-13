# -*- coding: utf-8 -*-
"""7.03 Размерность линий и границ: прежний класс против нового.

python tools/qgis_check/run_703.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    lines, polys = datasets.dimension_layers()
    cases = [("lines", {"LINES": lines}), ("polygons", {"LINES": polys})]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_703.json")
    bad = common.compare(cases, old, new, "LineDimensionAlgorithm",
                         ["OUTPUT"], out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
