# -*- coding: utf-8 -*-
"""4.11 Образец справочника пластов: прежний класс против нового.

python tools/qgis_check/run_411.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    cases = [("template", {})]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_411.json")
    bad = common.compare(cases, old, new, "PlastReferenceTemplateAlgorithm",
                         ["OUTPUT"], out)
    # тот же прогон в английском окне: вид тела переводится
    from grid_isolines import i18n as i_new
    from grid_isolines_old import i18n as i_old
    i_new.set_language("en", "en")
    i_old.set_language("en", "en")
    bad += common.compare([("template, English UI", {})], old, new,
                          "PlastReferenceTemplateAlgorithm", ["OUTPUT"],
                          out + ".en.json")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
