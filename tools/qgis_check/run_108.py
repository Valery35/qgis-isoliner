# -*- coding: utf-8 -*-
"""1.08 Кросс-валидация вариограммы: прежний класс против нового.

python tools/qgis_check/run_108.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402
import datasets  # noqa: E402


def model_table():
    """Таблица моделей вариограмм с двумя профилями, как пишет 1.06."""
    from qgis.PyQt.QtCore import QVariant
    spec = (("profile", QVariant.String), ("field", QVariant.String),
            ("struct", QVariant.Int), ("model", QVariant.Int),
            ("sill", QVariant.Double), ("range", QVariant.Double),
            ("azimuth", QVariant.Double), ("anis", QVariant.Double),
            ("nugget", QVariant.Double), ("val_pct", QVariant.Double),
            ("val_cap", QVariant.Bool), ("fitted", QVariant.String),
            ("author", QVariant.String), ("note", QVariant.String))
    rows = [(None, ["rho", "rho_k", 1, 1, 300.0, 250.0, 0.0, 1.0, 20.0,
                    0.0, False, "2026-11-13", "", ""]),
            (None, ["rho_aniso", "rho_k", 1, 2, 280.0, 300.0, 45.0, 0.6,
                    30.0, 0.0, False, "2026-11-13", "", ""])]
    return common.memory_layer("None", "EPSG:32640", list(spec), rows,
                               "vg_models")


def main(argv):
    old, new = common.start(argv[1])
    demo = datasets.demo_profiles_layer()
    sample = datasets.clustered_layer()
    vgt = model_table()
    from run_106 import grouped_layer
    grp = grouped_layer()
    base = {"KTYPE": 0, "RADIUS": 0.0, "MIN_POINTS": 1, "MAX_POINTS": 24,
            "SKMEAN": 0.0, "NUGGET": 0.0, "S1_MODEL": 0, "S1_SILL": 1.0,
            "S1_RANGE": 0.0, "S1_AZIMUTH": 0.0, "S1_ANIS": 1.0,
            "DETREND": False, "DETREND_DEG": 0}

    def c(layer, z, **kw):
        d = dict(base, INPUT=layer, ZFIELD=z)
        d.update(kw)
        return d

    cases = [
        ("demo rho_k default", c(demo, "rho_k")),
        ("demo rho_k spherical nugget sill range",
         c(demo, "rho_k", NUGGET=20.0, S1_SILL=300.0, S1_RANGE=250.0)),
        ("demo simple kriging", c(demo, "rho_k", KTYPE=1, SKMEAN=40.0,
                                  S1_SILL=300.0, S1_RANGE=250.0)),
        ("demo search radius and points", c(demo, "sp", RADIUS=150.0,
                                            MIN_POINTS=3, MAX_POINTS=8)),
        ("demo gaussian (nugget raised)", c(demo, "z", S1_MODEL=2,
                                            S1_SILL=50.0, S1_RANGE=400.0)),
        ("demo power range 0", c(demo, "z", S1_MODEL=3)),
        ("demo power range 5", c(demo, "z", S1_MODEL=3, S1_RANGE=5.0)),
        ("demo only nugget", c(demo, "z", NUGGET=5.0, S1_SILL=0.0)),
        ("demo anisotropy", c(demo, "rho_k", S1_SILL=300.0, S1_RANGE=300.0,
                              S1_AZIMUTH=30.0, S1_ANIS=0.5)),
        ("demo detrend 1", c(demo, "z", DETREND=True)),
        ("demo detrend 2", c(demo, "z", DETREND=True, DETREND_DEG=1)),
        ("demo id field", c(demo, "rho_k", IDFIELD="profile")),
        ("demo outliers 5%", c(demo, "rho_k", VAL_PCT=5.0)),
        ("demo table profile", c(demo, "rho_k", VG_TABLE=vgt,
                                 VG_NAME="rho")),
        ("demo table anisotropic profile", c(demo, "rho_k", VG_TABLE=vgt,
                                             VG_NAME="rho_aniso")),
        ("error: table, no profile name", c(demo, "rho_k", VG_TABLE=vgt)),
        ("error: table, unknown profile", c(demo, "rho_k", VG_TABLE=vgt,
                                            VG_NAME="нет")),
        ("sample au hole id", c(sample, "au", IDFIELD="hole")),
        ("grouped au weights", c(grp, "au", WEIGHT_FIELD="wt")),
        ("grouped au weights with zero", c(grp, "au", WEIGHT_FIELD="wt0")),
        ("error: no Z, no field", c(sample, "")),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_108.json")
    bad = common.compare(cases, old, new, "CrossValidationAlgorithm",
                         ["OUTPUT"], out, files=["OUTPUT_HTML"])
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
