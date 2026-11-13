# -*- coding: utf-8 -*-
"""1.06 Вариограмма: прежний класс против нового.

python tools/qgis_check/run_106.py <папка с grid_isolines_old> [отчёт.json]

Случай «без подбора» прежний класс не проходил: переменная dest_model
заводилась только внутри ветки подбора, и при снятой галочке расчёт
падал на UnboundLocalError. Новый класс считает. Этот случай ждёт
расхождения и в итог сверки не входит.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def grouped_layer():
    """Сгущённая сеть с полем вида разведки и полем весов."""
    from qgis.PyQt.QtCore import QVariant
    rows = []
    for i, r in enumerate(datasets.clustered()):
        if r["x"] is None:
            continue
        hole = r["attrs"]["hole"]
        kind = "сеть" if hole.startswith("G") else (
            "куст" if hole.startswith("C") else None)
        wt = 0.5 + (i % 3) * 0.25
        rows.append(("POINT(%r %r)" % (r["x"], r["y"]),
                     [r["attrs"]["au"], kind, wt, 0.0 if i == 3 else wt]))
    # ещё 30 точек сети, чтобы группа «сеть» прошла порог в 30 точек
    for j in range(30):
        x = datasets.X0 + 35.0 + 17.0 * (j % 6)
        y = datasets.Y0 + 140.0 + 23.0 * (j // 6)
        rows.append(("POINT(%r %r)" % (x, y),
                     [1.5 + 0.07 * j, "сеть", 1.0, 1.0]))
    return common.memory_layer(
        "Point", "EPSG:32640",
        [("au", QVariant.Double), ("kind", QVariant.String),
         ("wt", QVariant.Double), ("wt0", QVariant.Double)], rows, "grouped")


def main(argv):
    old, new = common.start(argv[1])
    demo = datasets.demo_profiles_layer()
    grp = grouped_layer()
    base = {"N_LAGS": 15, "MAXLAG": 0.0, "FIT": True, "FIT_MODEL": 0,
            "ROBUST": False, "MIN_GROUP_PCT": 2.0}

    def c(layer, z, **kw):
        d = dict(base, INPUT=layer, ZFIELD=z)
        d.update(kw)
        return d

    cases = [
        ("demo rho_k auto fit", c(demo, "rho_k")),
        ("demo sp groups by profile", c(demo, "sp", GROUP_FIELD="profile")),
        ("demo vp robust 8 lags", c(demo, "vp", ROBUST=True, N_LAGS=8)),
        ("demo rho_k spherical", c(demo, "rho_k", FIT_MODEL=1)),
        ("demo rho_k exponential", c(demo, "rho_k", FIT_MODEL=2)),
        ("demo rho_k gaussian", c(demo, "rho_k", FIT_MODEL=3)),
        ("demo z maxlag 5 (warning)", c(demo, "z", MAXLAG=5.0)),
        ("demo rho_k outliers 5% removed", c(demo, "rho_k", VAL_PCT=5.0)),
        ("demo rho_k outliers capped", c(demo, "rho_k", VAL_PCT=5.0,
                                         VAL_CAP=True)),
        ("demo rho_k bounds", c(demo, "rho_k", VAL_MIN=8.0, VAL_MAX=70.0)),
        ("demo cloud and model overlay", c(demo, "rho_k", SHOW_CLOUD=True,
                                           SHOW_MODEL=True)),
        ("demo profile name", c(demo, "rho_k", SAVE_PROFILE="КрII кровля")),
        ("grouped: groups with skipped and empty",
         c(grp, "au", GROUP_FIELD="kind")),
        ("grouped: weights", c(grp, "au", WEIGHT_FIELD="wt")),
        ("grouped: weights with zero", c(grp, "au", WEIGHT_FIELD="wt0")),
        ("grouped: weights and groups", c(grp, "au", WEIGHT_FIELD="wt",
                                          GROUP_FIELD="kind")),
        ("error: no Z, no field", c(grp, "")),
    ]
    expected_diff = [("no fit (old class crashed)", c(demo, "rho_k",
                                                       FIT=False))]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_106.json")
    sinks = ["OUTPUT", "OUT_MODEL"]
    bad = common.compare(cases, old, new, "ExperimentalVariogramAlgorithm",
                         sinks, out, files=["OUTPUT_HTML"])
    print("-- expected difference:")
    common.compare(expected_diff, old, new, "ExperimentalVariogramAlgorithm",
                   sinks, out + ".nofit.json", files=["OUTPUT_HTML"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
