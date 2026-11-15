# -*- coding: utf-8 -*-
"""1.10 Пример скважин: прежний класс против нового.

python tools/qgis_check/run_110.py <папка с grid_isolines_old> [отчёт.json]

Растр дрейфа сверяется через поле dz скважин: dz считается выборкой из
той же сетки дрейфа.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    ext = "500000,501000,6400000,6400800 [EPSG:32640]"
    base = {"EXTENT": ext, "N_POINTS": 120, "SEED": 7}
    tmp = tempfile.mkdtemp(prefix="run110_")
    cases = [
        ("default seed 7", dict(base)),
        ("all fields", dict(base, MINTYPE=True, HEAD=True, HYDRO=True)),
        ("head only", dict(base, HEAD=True, SEED=11)),
        ("fault throw 8", dict(base, THROW=8.0, N_POINTS=300)),
        ("custom ranges", dict(base, VMIN=10.0, VMAX=20.0, SMOOTH=0.3,
                               NUGGET_FRAC=0.1, ROOF_MIN=-400.0,
                               ROOF_MAX=-300.0, THICK_MIN=2.0,
                               THICK_MAX=3.0)),
        ("drift raster", dict(base, OUTPUT_DRIFT=os.path.join(
            tmp, "drift_%s.tif"))),
        ("error: vmax <= vmin", dict(base, VMIN=5.0, VMAX=5.0)),
        ("error: empty extent", dict(base, EXTENT="500000,500000,6400000,"
                                     "6400000 [EPSG:32640]")),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        tempfile.gettempdir(), "run_110.json")
    sinks = ["OUTPUT", "OUTPUT_FAULT"]
    # путь растра у прежнего и нового класса свой, иначе второй затрёт первый
    bad = 0
    report = []
    for title, params in cases:
        pa, pb = dict(params), dict(params)
        if "OUTPUT_DRIFT" in params:
            pa["OUTPUT_DRIFT"] = params["OUTPUT_DRIFT"] % "old"
            pb["OUTPUT_DRIFT"] = params["OUTPUT_DRIFT"] % "new"
        a = common.run(old, "ExampleWellsAlgorithm", pa, sinks)
        b = common.run(new, "ExampleWellsAlgorithm", pb, sinks)
        same = a == b
        if "OUTPUT_DRIFT" in params:
            ra = open(pa["OUTPUT_DRIFT"], "rb").read()
            rb = open(pb["OUTPUT_DRIFT"], "rb").read()
            same = same and len(ra) == len(rb)
        rows = sum(len(o["rows"]) for o in b["outputs"].values()
                   if isinstance(o, dict))
        print("%-40s %s  ok=%s rows=%d journal=%d" % (
            title, "SAME" if same else "DIFF", b["ok"], rows,
            len(b["journal"])))
        bad += 0 if same else 1
        report.append({"case": title, "same": same,
                       "old": None if same else a,
                       "new": None if same else b})
    import json
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1, default=str)
    print("cases %d, differences %d" % (len(cases), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
