# -*- coding: utf-8 -*-
"""3.08 Пример для плотности: прежний класс против нового.

python tools/qgis_check/run_308.py <папка с grid_isolines_old> [отчёт.json]

Вспомогательный растр у прежнего и нового класса пишется в свой файл:
один путь на оба прогона держал файл открытым и вешал второй прогон.
"""

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    tmp = tempfile.mkdtemp(prefix="run308_")
    ext = "500000,501000,6400000,6400800 [EPSG:32640]"
    cases = [("seed 1", {"EXTENT": ext, "SEED": 1, "CELL_AUX": 50.0}),
             ("seed 9 cell 20", {"EXTENT": ext, "SEED": 9,
                                 "CELL_AUX": 20.0}),
             # зерно 0 у density.demo_dataset значит «случайно»: такой
             # прогон каждый раз новый, и сверять его не с чем
             ("seed 4 wide extent", {"EXTENT": "0,2000,0,500 [EPSG:32640]",
                                     "SEED": 4, "CELL_AUX": 50.0})]
    sinks = ["OUT_POINTS", "OUT_LINES", "OUT_POLYGONS"]
    out = argv[2] if len(argv) > 2 else os.path.join(
        tempfile.gettempdir(), "run_308.json")
    bad, report = 0, []
    for k, (title, params) in enumerate(cases):
        pa = dict(params, OUT_AUX=os.path.join(tmp, "old_%d.tif" % k))
        pb = dict(params, OUT_AUX=os.path.join(tmp, "new_%d.tif" % k))
        a = common.run(old, "DensityDemoAlgorithm", pa, sinks)
        b = common.run(new, "DensityDemoAlgorithm", pb, sinks)
        same = a == b and os.path.getsize(pa["OUT_AUX"]) == \
            os.path.getsize(pb["OUT_AUX"])
        rows = sum(len(o["rows"]) for o in b["outputs"].values()
                   if isinstance(o, dict))
        print("%-40s %s  ok=%s rows=%d journal=%d" % (
            title, "SAME" if same else "DIFF", b["ok"], rows,
            len(b["journal"])))
        bad += 0 if same else 1
        report.append({"case": title, "same": same,
                       "old": None if same else a,
                       "new": None if same else b})
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    print("cases %d, differences %d" % (len(cases), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
