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


def _koch(p0, p1, depth):
    import math
    if depth == 0:
        return [p0, p1]
    (x0, y0), (x1, y1) = p0, p1
    dx, dy = (x1 - x0) / 3.0, (y1 - y0) / 3.0
    a = (x0 + dx, y0 + dy)
    b = (x0 + 2 * dx, y0 + 2 * dy)
    c = (a[0] + dx * 0.5 - dy * math.sqrt(3) / 2,
         a[1] + dy * 0.5 + dx * math.sqrt(3) / 2)
    out = []
    for q0, q1 in ((p0, a), (a, c), (c, b), (b, p1)):
        seg = _koch(q0, q1, depth - 1)
        out.extend(seg if not out else seg[1:])
    return out


def wiggle(n, amp, x0=0.0, y0=0.0, length=1000.0):
    """Изрезанная линия без случайности: сумма синусов."""
    import math
    pts = []
    for i in range(n):
        t = i / (n - 1.0)
        y = amp * (math.sin(37.0 * t) + 0.5 * math.sin(113.0 * t)
                   + 0.25 * math.sin(331.0 * t))
        pts.append((x0 + length * t, y0 + y))
    return pts


def dimension_lines():
    """Линии для 7.03 и 7.04: снежинка Коха, изрезанная, прямая,
    короткая (меньше трёх вершин) и двухчастная."""
    X, Y = X0, Y0
    koch = [(X + x, Y + y) for x, y in _koch((0.0, 0.0), (900.0, 0.0), 5)]
    rows = [
        ("koch", [koch]),
        ("wiggle", [wiggle(400, 30.0, X, Y + 500.0)]),
        ("straight", [[(X, Y + 900.0), (X + 500.0, Y + 900.0),
                       (X + 1000.0, Y + 900.0)]]),
        ("short", [[(X, Y + 1200.0), (X + 10.0, Y + 1200.0)]]),
        ("multi", [wiggle(120, 10.0, X, Y + 1500.0, 300.0),
                   wiggle(260, 20.0, X + 400.0, Y + 1500.0, 500.0)]),
    ]
    return rows


def dimension_polygons():
    """Полигоны для 7.03 и 7.04: снежинка Коха и квадрат с дыркой."""
    X, Y = X0, Y0
    tri = [(0.0, 0.0), (600.0, 0.0), (300.0, 519.6152422706632)]
    ring = []
    for a, b in ((tri[0], tri[2]), (tri[2], tri[1]), (tri[1], tri[0])):
        seg = _koch(a, b, 4)
        ring.extend(seg if not ring else seg[1:])
    ring = [(X + x, Y + y) for x, y in ring]
    sq = [(X + 1000.0, Y), (X + 1300.0, Y), (X + 1300.0, Y + 300.0),
          (X + 1000.0, Y + 300.0), (X + 1000.0, Y)]
    hole = [(X + 1100.0, Y + 100.0), (X + 1200.0, Y + 100.0),
            (X + 1200.0, Y + 200.0), (X + 1100.0, Y + 100.0)]
    return [("koch_flake", [ring]), ("square_hole", [sq, hole])]


def _wkt_line(parts):
    if len(parts) == 1:
        return "LINESTRING(%s)" % ", ".join("%r %r" % p for p in parts[0])
    return "MULTILINESTRING(%s)" % ", ".join(
        "(%s)" % ", ".join("%r %r" % p for p in part) for part in parts)


def _wkt_poly(rings):
    return "POLYGON(%s)" % ", ".join(
        "(%s)" % ", ".join("%r %r" % p for p in ring) for ring in rings)


def dimension_layers():
    import common
    from qgis.PyQt.QtCore import QVariant
    f = [("name", QVariant.String)]
    lines = common.memory_layer(
        "MultiLineString", "EPSG:32640", f,
        [(_wkt_line(p), [n]) for n, p in dimension_lines()], "lines")
    polys = common.memory_layer(
        "Polygon", "EPSG:32640", f,
        [(_wkt_poly(p), [n]) for n, p in dimension_polygons()], "polys")
    return lines, polys


def contours():
    """Горизонтали для 2.08: уровни 100..160 через 5 м, у части уровней
    по два звена, одна без отметки, одна с текстовой отметкой."""
    rows = []
    for k, z in enumerate(range(100, 165, 5)):
        y = Y0 + 40.0 * k
        rows.append({"parts": [[(X0, y), (X0 + 300.0, y + 10.0),
                                (X0 + 600.0, y)]],
                     "attrs": {"elev": float(z), "src": "A"}})
        if k % 3 == 0:
            rows.append({"parts": [[(X0 + 700.0, y), (X0 + 900.0, y + 5.0)]],
                         "attrs": {"elev": float(z), "src": "B"}})
    rows.append({"parts": [[(X0, Y0 - 50.0), (X0 + 100.0, Y0 - 50.0)]],
                 "attrs": {"elev": None, "src": "NULL"}})
    return rows


def contour_layer():
    import common
    from qgis.PyQt.QtCore import QVariant
    rows = [(_wkt_line(r["parts"]), [r["attrs"]["elev"], r["attrs"]["src"]])
            for r in contours()]
    return common.memory_layer(
        "LineString", "EPSG:32640",
        [("elev", QVariant.Double), ("src", QVariant.String)], rows,
        "contours")


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
