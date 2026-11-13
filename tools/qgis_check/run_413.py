# -*- coding: utf-8 -*-
"""4.13 Содержания по пласту в устьях: прежний класс против нового.

python tools/qgis_check/run_413.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    lc, li, lh = datasets.drill_layers()
    base = {"COLLAR": lc, "INTERVAL": li, "CHEM": lh,
            "COMPONENTS": ["kcl", "nacl"], "BED": "КрII", "MIN_COVER": 0.5}
    cases = [
        ("КрII kcl nacl cover 0.5", dict(base)),
        ("КрII cover 0.9", dict(base, MIN_COVER=0.9)),
        ("КрII nacl only", dict(base, COMPONENTS=["nacl"])),
        ("Сл kcl nacl", dict(base, BED="Сл")),
        ("explicit field names", dict(base, CID="hole_id", IID="hole_id",
                                      IFROM="from", ITO="to", ICODE="code",
                                      HID="hole_id", HFROM="from",
                                      HTO="to")),
        ("error: no bed", dict(base, BED="  ")),
        ("error: bed not found", dict(base, BED="XX")),
        ("error: no collar for bed", dict(base, BED="Карн")),
        ("error: no components", dict(base, COMPONENTS=[])),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_413.json")
    bad = common.compare(cases, old, new, "BedGradesAtCollarsAlgorithm",
                         ["OUTPUT"], out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
