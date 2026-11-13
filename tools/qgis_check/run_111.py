# -*- coding: utf-8 -*-
"""1.11 Пример геофизических профилей: прежний класс против нового.

python tools/qgis_check/run_111.py <папка с grid_isolines_old> [отчёт.json]
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common  # noqa: E402


def main(argv):
    old, new = common.start(argv[1])
    ext = "500000,501000,6400000,6400800 [EPSG:32640]"
    base = {"EXTENT": ext, "MODE": 0, "N_PROFILES": 4, "PICKET_STEP": 20.0,
            "SEED": 7}
    cases = [
        ("electro default seed 7", dict(base)),
        ("electro 6 profiles step 10 seed 3",
         dict(base, N_PROFILES=6, PICKET_STEP=10.0, SEED=3)),
        ("electro custom amplitudes", dict(base, RHO_BG=120.0, RHO_MIN=5.0,
                                           SP_AMP=-50.0, VP_AMP=30.0,
                                           NOISE=0.0, Z_BASE=50.0,
                                           Z_AMP=0.0)),
        ("electro one profile", dict(base, N_PROFILES=1)),
        ("subsidence 2 tours", dict(base, MODE=1)),
        ("subsidence 5 tours positive", dict(base, MODE=1, N_TOURS=5,
                                             SUBS_SIGN=1, SUBS_MAX=800.0)),
        ("error: empty extent", dict(base, EXTENT="500000,500000,6400000,"
                                     "6400000 [EPSG:32640]")),
    ]
    out = argv[2] if len(argv) > 2 else os.path.join(
        common.tempfile.gettempdir(), "run_111.json")
    bad = common.compare(cases, old, new, "GeophysProfilesDemoAlgorithm",
                         ["OUTPUT"], out)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
