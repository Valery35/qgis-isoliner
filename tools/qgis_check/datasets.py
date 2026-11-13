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


def _layer(rows, fields, name, with_z=False):
    import common
    geom = "PointZ" if with_z else "Point"
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
