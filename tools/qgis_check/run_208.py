# -*- coding: utf-8 -*-
"""2.08 Разделить горизонтали: прежний класс против нового.

python tools/qgis_check/run_208.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    lyr = datasets.contour_layer()
    cases = [
        ("every 4", {"INPUT": lyr, "FIELD": "elev", "EVERY": 4}),
        ("every 2 offset 1", {"INPUT": lyr, "FIELD": "elev", "EVERY": 2,
                              "OFFSET": 1}),
        ("every 5 offset 2", {"INPUT": lyr, "FIELD": "elev", "EVERY": 5,
                              "OFFSET": 2}),
        ("error: step too large", {"INPUT": lyr, "FIELD": "elev",
                                   "EVERY": 50}),
        ("error: text field, no numbers", {"INPUT": lyr, "FIELD": "src",
                                           "EVERY": 4}),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_208.json")
    bad = common.compare(cases, old, new, "ContourSplitAlgorithm",
                         ["OUTPUT_BUILD", "OUTPUT_CHECK"], out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
