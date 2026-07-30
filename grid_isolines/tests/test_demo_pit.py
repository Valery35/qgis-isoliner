# -*- coding: utf-8 -*-
"""Тесты демо-карьера (demo_pit) и его связка с детектором бровок.

Запуск: python test_demo_pit.py.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import demo_pit  # noqa: E402
import topo_break as tb  # noqa: E402


def test_deterministic_by_seed():
    """Одно зерно - один рельеф, другое зерно - другой."""
    z1, t1 = demo_pit.generate(nx=120, ny=100, seed=7)
    z2, t2 = demo_pit.generate(nx=120, ny=100, seed=7)
    z3, _t3 = demo_pit.generate(nx=120, ny=100, seed=8)
    assert np.array_equal(z1, z2)
    assert len(t1) == len(t2)
    assert not np.array_equal(z1, z3)


def test_pit_depth_matches_benches():
    """Дно карьера ниже основания примерно на сумму уступов."""
    z, _t = demo_pit.generate(nx=300, ny=240, seed=7,
                              benches=3, bench_h=10.0, noise=0.0)
    zb, _t2 = demo_pit.generate(nx=300, ny=240, seed=7,
                                benches=0, bench_h=10.0, noise=0.0)
    drop = float((zb - z).max())
    assert 25.0 < drop < 35.0


def test_truth_lines_present_and_typed():
    """Истинные линии: уступы парами, отвал парой, канава тройкой."""
    _z, truth = demo_pit.generate(nx=300, ny=240, seed=7, benches=3)
    links = {}
    for t in truth:
        links.setdefault(t["link"], []).append(t["kind"])
    for i in (1, 2, 3):
        kinds = links.get("bench-%d" % i, [])
        assert "brow" in kinds and "toe" in kinds
    assert sorted(links["dump"]) == ["brow", "toe"]
    assert sorted(links["ditch"]) == ["brow", "brow", "thalweg"]


def test_truth_z_on_lines_matches_raster():
    """Отметки вершин истинных линий совпадают с растром в их ячейках."""
    z, truth = demo_pit.generate(nx=300, ny=240, seed=7, noise=0.0)
    worst = 0.0
    for t in truth:
        for x, y, zv in t["pts"][::7]:
            r = int(min(max(y, 0), z.shape[0] - 1))
            c = int(min(max(x, 0), z.shape[1] - 1))
            worst = max(worst, abs(z[r, c] - zv))
    assert worst < 1e-9


def test_ditch_converges():
    """Канава сходится: глубина у точки схождения нулевая, дальше растёт."""
    z, truth = demo_pit.generate(nx=300, ny=240, seed=7, noise=0.0)
    zb, _t = demo_pit.generate(nx=300, ny=240, seed=7, benches=0, noise=0.0,
                               ditch=False)
    th = [t for t in truth if t["kind"] == "thalweg"][0]["pts"]
    x0, y0, _ = th[0]
    xe, ye, _ = th[-1]
    r0, c0 = int(y0), int(x0)
    re_, ce = int(min(ye, z.shape[0] - 1)), int(min(xe, z.shape[1] - 1))
    cut0 = zb[r0, c0] - z[r0, c0]
    cut1 = zb[re_, ce] - z[re_, ce]
    assert cut0 < 0.3
    assert cut1 > 1.2


def test_detector_finds_truth():
    """Эталонная проверка 2.19: кандидаты ложатся на истинные линии.

    Меряются две величины, и обе числом: полнота (доля вершин истинных
    линий уступов, к которым подошёл кандидат ближе трёх ячеек) и точность
    (доля ячеек кандидатов, легших ближе трёх ячеек к какой-нибудь
    истинной линии). Пороги нарочно мягкие: тест ловит поломку метода, а
    точные цифры - дело живой приёмки.
    """
    z, truth = demo_pit.generate(nx=300, ny=240, seed=7, benches=2,
                                 bench_h=10.0, noise=0.02)
    cands = tb.breakline_candidates(z, cell=1.0, min_drop=1.0,
                                    min_len_cells=12, probe=4)
    assert cands, "детектор не нашёл ничего на демо-карьере"
    cand_cells = set()
    for cd in cands:
        cand_cells.update(cd["cells"])
    cand_arr = np.array(sorted(cand_cells), dtype=float)

    def near(r, c, tol=3.0):
        d = np.hypot(cand_arr[:, 0] - r, cand_arr[:, 1] - c)
        return bool((d <= tol).any())

    bench_pts = []
    for t in truth:
        if not t["link"].startswith("bench-"):
            continue
        for x, y, _zv in t["pts"][::4]:
            bench_pts.append((y, x))          # row, col
    hits = sum(1 for r, c in bench_pts if near(r, c))
    completeness = hits / float(len(bench_pts))
    assert completeness > 0.6, "полнота %.2f" % completeness

    truth_arr = []
    for t in truth:
        for x, y, _zv in t["pts"]:
            truth_arr.append((y, x))
    truth_arr = np.array(truth_arr, dtype=float)
    ok = 0
    cand_list = sorted(cand_cells)
    for r, c in cand_list[::5]:
        d = np.hypot(truth_arr[:, 0] - r, truth_arr[:, 1] - c)
        if d.min() <= 3.0:
            ok += 1
    precision = ok / float(len(cand_list[::5]))
    assert precision > 0.5, "точность %.2f" % precision


def test_shapes_do_not_scale_with_grid():
    """Формы карьера физичны: высота уступа не зависит от размера грида.

    Охват в 2.21 кладёт демо на место, но не растягивает его. Тест держит
    это со стороны ядра: на гриде вдвое шире уступ обязан остаться
    десятиметровым, а не превратиться в километровый блин.
    """
    z1, t1 = demo_pit.generate(nx=200, ny=200, seed=7, benches=2,
                               bench_h=10.0, noise=0.0, ditch=False)
    z2, t2 = demo_pit.generate(nx=400, ny=400, seed=7, benches=2,
                               bench_h=10.0, noise=0.0, ditch=False)
    base1, _ = demo_pit.generate(nx=200, ny=200, seed=7, benches=0,
                                 noise=0.0, dump=False, ditch=False)
    base2, _ = demo_pit.generate(nx=400, ny=400, seed=7, benches=0,
                                 noise=0.0, dump=False, ditch=False)
    d1 = float((base1 - z1).max())
    d2 = float((base2 - z2).max())
    assert abs(d1 - d2) < 1.0        # глубина одна и та же, 2 уступа по 10 м
    assert 18.0 < d1 < 22.0


def test_corner_bench_has_varying_crest():
    """Уступ с поворотом: отметка гребня меняется вдоль него.

    Форма добавлена ради двух чисел цены метода расстояний. Оба проявляются
    только при переменной отметке: на постоянной ни залом на медиальной
    оси, ни веер у поворота ничего не искажают.
    """
    z, truth = demo_pit.generate(seed=7, noise=0.0)
    cor = [t for t in truth if t["link"] == "corner"]
    kinds = sorted(t["kind"] for t in cor)
    assert kinds == ["brow", "toe", "toe"]      # гребень и два откоса
    crest = [t for t in cor if t["kind"] == "brow"][0]
    zs = [p[2] for p in crest["pts"]]
    assert max(zs) - min(zs) > 3.0
    xs = [p[0] for p in crest["pts"]]
    ys = [p[1] for p in crest["pts"]]
    # поворот на прямой угол: одно плечо вдоль x, другое вдоль y
    assert max(xs) - min(xs) > 20.0 and max(ys) - min(ys) > 20.0


def test_corner_does_not_disturb_the_pit():
    """Насыпь не трогает карьер: её отключение не меняет его глубину.

    На мелких гридах она дотягивалась до бровки верхнего уступа и меняла
    отметку уже снятой истинной линии.
    """
    z1, _t = demo_pit.generate(nx=300, ny=240, seed=7, noise=0.0)
    z0, _t0 = demo_pit.generate(nx=300, ny=240, seed=7, noise=0.0,
                                corner=False)
    base, _b = demo_pit.generate(nx=300, ny=240, seed=7, benches=0,
                                 noise=0.0, dump=False, ditch=False,
                                 corner=False)
    assert abs(float((base - z1).max()) - float((base - z0).max())) < 1e-9


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print("ok:", n)
        except AssertionError as ex:
            failed += 1
            print("FAIL:", n, "-", ex)
    print("\n%d тестов, ошибок %d" % (len(fns), failed))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    _run()
