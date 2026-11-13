# -*- coding: utf-8 -*-
"""Наборы данных для сверок и образцов: одни и те же в QGIS и без него.

Здесь только данные (списки и числа). Слои QGIS из них строит common.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

X0, Y0 = 500000.0, 6400000.0


def demo_profiles(seed=7):
    """Выход демо 1.11 (электроразведка) с заданным зерном: точки и поля."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from grid_isolines import geodemo
    d = geodemo.gen_profiles(X0, Y0, X0 + 1000.0, Y0 + 800.0, n_profiles=4,
                             picket_step=20.0, seed=seed)
    rows = []
    for i in range(len(d["x"])):
        rows.append({"x": float(d["x"][i]), "y": float(d["y"][i]),
                     "attrs": {"profile": int(d["profile"][i]),
                               "rho_k": float(d["rho_k"][i]),
                               "sp": float(d["sp"][i]),
                               "vp": float(d["vp"][i]),
                               "z": float(d["z"][i])}})
    return rows


def clustered(with_z=False):
    """Сеть 6 на 6 через 100 м и сгущение 15 точек в богатом углу,
    одна точка-двойник, одна без значения и одна без геометрии."""
    rows = []
    for j in range(6):
        for i in range(6):
            x, y = X0 + 100.0 * i, Y0 + 100.0 * j
            rows.append({"x": x, "y": y,
                         "attrs": {"au": 1.0 + 0.1 * i + 0.05 * j,
                                   "hole": "G%d%d" % (i, j)}})
    k = 0
    for j in range(3):
        for i in range(5):
            x, y = X0 + 20.0 + 15.0 * i, Y0 + 20.0 + 25.0 * j
            rows.append({"x": x, "y": y,
                         "attrs": {"au": 4.0 + 0.3 * i - 0.2 * j,
                                   "hole": "C%d" % k}})
            k += 1
    rows.append({"x": X0 + 20.0, "y": Y0 + 20.0,
                 "attrs": {"au": 6.0, "hole": "DUP"}})
    rows.append({"x": X0 + 250.0, "y": Y0 + 250.0,
                 "attrs": {"au": None, "hole": "NULL"}})
    rows.append({"x": None, "y": None, "attrs": {"au": 2.0, "hole": "NOGEOM"}})
    if with_z:
        for r in rows:
            r["z"] = r["attrs"]["au"]
    return rows


def drill_model():
    """Модель бурения для 4.13: устья, интервалы, пробы.

    H1 - пласт одним интервалом, пробы с границей посередине пласта;
    H2 - пласт двумя интервалами, у NaCl пропуск;
    H3 - пласт без проб; H4 - пробы с перекрытием и текстовым числом;
    H5 - устье без интервалов пласта; H6 - интервал с перепутанными
    from и to; H7 - интервалы без устья."""
    collars = []
    for i, h in enumerate(("H1", "H2", "H3", "H4", "H5", "H6")):
        collars.append({"x": X0 + 100.0 * i, "y": Y0 + 50.0 * (i % 2),
                        "attrs": {"hole_id": h, "z": 150.0 - i}})
    iv = [("H1", 0.0, 100.0, "Сл"), ("H1", 100.0, 104.0, "КрII"),
          ("H1", 104.0, 110.0, "Сл"),
          ("H2", 90.0, 92.0, "КрII"), ("H2", 92.0, 93.0, "Сл"),
          ("H2", 93.0, 95.5, "КрII"),
          ("H3", 80.0, 83.0, "КрII"),
          ("H4", 70.0, 76.0, "КрII"),
          ("H5", 0.0, 50.0, "Сл"),
          ("H6", 64.0, 60.0, " КрII "),
          ("H7", 10.0, 12.0, "КрII"), ("H7", 12.0, 14.0, "Карн")]
    intervals = [{"x": None, "y": None,
                  "attrs": {"hole_id": h, "from": a, "to": b, "code": c}}
                 for h, a, b, c in iv]
    ch = [("H1", 99.0, 102.0, 30.0, 60.0), ("H1", 102.0, 104.5, 20.0, 70.0),
          ("H2", 90.0, 92.0, 25.0, None), ("H2", 93.0, 95.5, 35.0, 55.0),
          ("H4", 70.0, 74.0, 10.0, 80.0), ("H4", 73.0, 76.0, "12,5", 75.0),
          ("H6", 60.0, 64.0, 18.0, 66.0), ("H7", 10.0, 12.0, 40.0, 50.0),
          ("H1", 200.0, 201.0, 99.0, 1.0)]
    chem = [{"x": None, "y": None,
             "attrs": {"hole_id": h, "from": a, "to": b,
                       "kcl": k if not isinstance(k, str) else None,
                       "kcl_txt": k if isinstance(k, str) else None,
                       "nacl": n}}
            for h, a, b, k, n in ch]
    return collars, intervals, chem


def drill_layers():
    from qgis.PyQt.QtCore import QVariant
    c, i, h = drill_model()
    lc = _layer(c, [("hole_id", QVariant.String), ("z", QVariant.Double)],
                "collar")
    li = _layer(i, [("hole_id", QVariant.String), ("from", QVariant.Double),
                    ("to", QVariant.Double), ("code", QVariant.String)],
                "interval", geom="None")
    lh = _layer(h, [("hole_id", QVariant.String), ("from", QVariant.Double),
                    ("to", QVariant.Double), ("kcl", QVariant.Double),
                    ("kcl_txt", QVariant.String), ("nacl", QVariant.Double)],
                "chem", geom="None")
    return lc, li, lh


def _layer(rows, fields, name, with_z=False, geom=None):
    import common
    geom = geom or ("PointZ" if with_z else "Point")
    out = []
    for r in rows:
        wkt = None
        if r["x"] is not None:
            if with_z:
                z = r.get("z")
                wkt = "POINT Z(%r %r %r)" % (r["x"], r["y"],
                                             float("nan") if z is None else z)
            else:
                wkt = "POINT(%r %r)" % (r["x"], r["y"])
        out.append((wkt, [r["attrs"].get(f) for f, _t in fields]))
    return common.memory_layer(geom, "EPSG:32640", fields, out, name)


def demo_profiles_layer(seed=7):
    from qgis.PyQt.QtCore import QVariant
    f = [("profile", QVariant.Int), ("rho_k", QVariant.Double),
         ("sp", QVariant.Double), ("vp", QVariant.Double),
         ("z", QVariant.Double)]
    return _layer(demo_profiles(seed), f, "demo_1_11")


def clustered_layer(with_z=False):
    from qgis.PyQt.QtCore import QVariant
    f = [("au", QVariant.Double), ("hole", QVariant.String)]
    return _layer(clustered(with_z), f, "clustered_z" if with_z
                  else "clustered", with_z)
