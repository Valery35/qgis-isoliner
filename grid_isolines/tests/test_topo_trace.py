# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Линии стока: куда стечёт вода из заданной ячейки. Ход идёт по тем же
# указателям D8, что и водотоки с водосборами, поэтому трасса и река
# по одному руслу совпадают вершина в вершину.
#
# Обрыв трассы естественный и бывает четырёх видов: сток (ниже некуда),
# край листа, приход в приёмник (водоём или водоток) и слияние с уже
# пройденной трассой. Последнее нужно на площадных источниках: отвал в
# тысячу ячеек иначе даст тысячу копий одного русла.
#
#     python grid_isolines/tests/test_topo_trace.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
import topo_flow as tf                    # noqa: E402


def _slope(ny=20, nx=20, step=1.0):
    r, _c = np.mgrid[0:ny, 0:nx]
    return 200.0 - r * step


def test_trace_runs_downhill_to_the_edge():
    z = _slope()
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 20 + 10], z.shape)[0]
    assert len(path) == 20, len(path)
    rows = [i // 20 for i in path]
    assert rows == list(range(20)), "трасса обязана идти строго вниз"
    assert tf.path_reason(path, ds, z.shape) == "край листа"


def test_trace_stops_in_a_pit():
    """Незаполненная впадина обрывает трассу: ниже некуда."""
    z = _slope()
    z[10, 10] = 150.0
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [5 * 20 + 10], z.shape)[0]
    assert path[-1] == 10 * 20 + 10
    assert tf.path_reason(path, ds, z.shape) == "сток"


def test_trace_stops_at_a_receiver():
    """Водоток или водоём принимает трассу и дальше её не ведёт."""
    z = _slope()
    _idx, ds = tf.d8_directions(z)
    stop = set(int(12 * 20 + c) for c in range(20))
    path = tf.trace_downhill(ds, [0 * 20 + 10], z.shape, stop=stop)[0]
    assert path[-1] == 12 * 20 + 10
    assert tf.path_reason(path, ds, z.shape, stop=stop) == "приёмник"


def test_merging_traces_walk_each_cell_once():
    """С обрывом при слиянии выходит дерево, а не пачка копий русла.

    Площадной источник даёт столько стартов, сколько в нём ячеек. Ниже
    по склону они сходятся в одно русло, и без обрыва по нему пройдёт
    каждая трасса целиком.
    """
    z = _slope(ny=30, nx=30)
    z += np.abs(np.mgrid[0:30, 0:30][1] - 15) * 0.2      # ложбина по центру
    _idx, ds = tf.d8_directions(z)
    starts = [2 * 30 + c for c in range(10, 21)]
    loose = tf.trace_downhill(ds, starts, z.shape)
    seen = set()
    tree = tf.trace_downhill(ds, starts, z.shape, seen=seen)
    assert sum(len(p) for p in loose) > sum(len(p) for p in tree)
    # трасса доводится ДО места слияния и там обрывается: последняя
    # ячейка общая, иначе дерево распалось бы на несвязанные куски
    inner = [i for p in tree for i in p[:-1]]
    assert len(inner) == len(set(inner)), "ячейка пройдена дважды"
    ends = [p[-1] for p in tree if p]
    assert len(set(inner) & set(ends)) <= len(ends)
    assert all(len(p) >= 1 for p in tree)


def test_metrics_measure_the_path():
    z = _slope(ny=11, nx=11, step=2.0)
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 11 + 5], z.shape)[0]
    m = tf.path_metrics(path, z, z.shape, 10.0)
    assert m["cells"] == 11
    assert abs(m["length"] - 100.0) < 1e-9      # десять шагов по 10 м
    assert abs(m["drop"] - 20.0) < 1e-9
    assert abs(m["slope"] - 0.2) < 1e-9
    assert m["z_start"] > m["z_end"]


def test_metrics_take_a_rectangular_cell():
    z = _slope(ny=5, nx=5)
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 5 + 2], z.shape)[0]
    m = tf.path_metrics(path, z, z.shape, 10.0, cell_y=25.0)
    assert abs(m["length"] - 100.0) < 1e-9, m["length"]


def test_single_cell_path_is_not_an_error():
    z = _slope()
    _idx, ds = tf.d8_directions(z)
    m = tf.path_metrics([19 * 20 + 3], z, z.shape, 5.0)
    assert m["cells"] == 1 and m["length"] == 0.0 and m["slope"] == 0.0


def test_start_outside_the_grid_gives_an_empty_path():
    z = _slope()
    _idx, ds = tf.d8_directions(z)
    assert tf.trace_downhill(ds, [-1, 10 ** 6], z.shape) == [[], []]


def test_loop_in_a_foreign_grid_does_not_hang():
    """Указатели из чужого инструмента могут зациклиться, ход всё равно
    обязан закончиться."""
    ds = np.array([1, 0], dtype=np.int64)          # две ячейки друг на друга
    paths = tf.trace_downhill(ds, [0], (1, 2))
    assert paths[0] == [0, 1]


def _run():
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok:", name)
            except AssertionError as e:
                fails += 1
                print("СБОЙ:", name, e)
    if fails:
        sys.exit(1)
    print("all trace tests passed")


if __name__ == "__main__":
    _run()


def test_fill_depressions_is_unpacked_everywhere():
    """Заполнение впадин отдаёт тройку, а не грид.

    Взять её целиком за грид можно только раз: numpy валится на
    неоднородной форме, и падение приходит уже из d8_directions, где
    причина не видна. Сторож ловит это статикой во всём пакете.
    """
    import re

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    for name in sorted(os.listdir(root)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(root, name), encoding="utf-8") as f:
            for n, line in enumerate(f, 1):
                t = line.strip()
                if "fill_depressions(" not in t or t.startswith("#"):
                    continue
                if re.match(r"(def |return |from |import |\w+\.)", t):
                    continue
                head = t.split("=")[0]
                if "=" in t and "," not in head:
                    bad.append("%s:%d %s" % (name, n, t[:60]))
    assert not bad, "заполнение впадин присвоено одним именем: %s" % bad


def _slope_then_flat(steep=2.0, flat=0.05, n_steep=30, n_flat=30, nx=20):
    r, _c = np.mgrid[0:(n_steep + n_flat), 0:nx]
    top = 200.0 - r * steep
    bottom = 200.0 - n_steep * steep - (r - n_steep) * flat
    return np.where(r < n_steep, top, bottom)


def test_flattening_cuts_at_the_foot_of_the_slope():
    """Трасса обрывается там, где поток теряет силу."""
    z = _slope_then_flat()
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 20 + 10], z.shape)[0]
    cut, was = tf.cut_on_flattening(path, z, z.shape, 10.0,
                                    min_slope=0.05, window=50.0)
    assert was and len(cut) < len(path)
    row = cut[-1] // 20
    assert 28 <= row <= 32, row      # у перелома, а не в середине полки


def test_short_shelf_is_skipped():
    """Ступенька в одну ячейку не считается выполаживанием.

    По одному шагу D8 уклон на грубой ЦМР скачет, поэтому меряется
    осреднённо по последним метрам пути.
    """
    z = _slope_then_flat()
    z[15, :] = z[14, :] - 0.05       # полка в одну ячейку на склоне
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 20 + 10], z.shape)[0]
    cut, was = tf.cut_on_flattening(path, z, z.shape, 10.0,
                                    min_slope=0.05, window=50.0)
    assert was
    assert cut[-1] // 20 > 20, "оборвались на короткой полке"


def test_flattening_off_by_zero():
    z = _slope_then_flat()
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 20 + 10], z.shape)[0]
    for slope, win in ((0.0, 50.0), (0.05, 0.0)):
        cut, was = tf.cut_on_flattening(path, z, z.shape, 10.0, slope, win)
        assert not was and len(cut) == len(path)


def test_steep_path_is_not_cut():
    z = _slope_then_flat(flat=2.0)      # склон без полки
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 20 + 10], z.shape)[0]
    cut, was = tf.cut_on_flattening(path, z, z.shape, 10.0, 0.05, 50.0)
    assert not was and len(cut) == len(path)


def test_short_path_survives_a_long_window():
    z = _slope_then_flat(n_steep=3, n_flat=2)
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * 20 + 10], z.shape)[0]
    cut, was = tf.cut_on_flattening(path, z, z.shape, 10.0, 0.05, 500.0)
    assert not was and cut == path


def test_degrees_convert_to_a_ratio():
    """Пороги задаются углом: лавинные критерии пишут в градусах."""
    assert abs(tf.slope_from_degrees(45) - 1.0) < 1e-12
    assert abs(tf.slope_from_degrees(25) - 0.46631) < 1e-4
    assert tf.slope_from_degrees(0) == 0.0


def test_steep_run_measures_the_start_zone():
    """Длина участка, который держит крутизну, а не одна ячейка."""
    ny, nx = 80, 20
    r, _c = np.mgrid[0:ny, 0:nx]
    thr30 = np.tan(np.radians(30.0))
    thr5 = np.tan(np.radians(5.0))
    z = np.where(r < 40, 500.0 - r * 10 * thr30,
                 500.0 - 40 * 10 * thr30 - (r - 40) * 10 * thr5)
    _idx, ds = tf.d8_directions(z)
    path = tf.trace_downhill(ds, [0 * nx + 10], z.shape)[0]
    length, i, j = tf.steep_run(path, z, z.shape, 10.0,
                                tf.slope_from_degrees(25))
    assert i == 0 and length >= 400.0
    assert tf.steep_run(path, z, z.shape, 10.0,
                        tf.slope_from_degrees(35))[0] == 0.0


def test_steep_run_matches_the_full_search():
    """Сверка с перебором всех пар: условие немонотонно, и обход
    по одному вперёд проскакивал бы участки."""
    rng = np.random.default_rng(5)
    for _case in range(60):
        n = int(rng.integers(2, 40))
        z = 100.0 + np.cumsum(-np.abs(rng.normal(1.0, 1.0, n)))
        s = np.arange(n) * 10.0
        thr = float(rng.uniform(0.02, 0.6))
        g = z + thr * s
        best = 0.0
        for a in range(n):
            for b in range(a + 1, n):
                if g[a] >= g[b]:
                    best = max(best, s[b] - s[a])
        got, _i, _j = tf.steep_run(list(range(n)), z.reshape(n, 1), (n, 1),
                                   10.0, thr, cell_y=10.0)
        assert abs(got - best) < 1e-9


def test_steep_run_on_a_flat_path_is_zero():
    z = np.zeros((10, 3))
    assert tf.steep_run(list(range(10)), z, (10, 3), 10.0, 0.1,
                        cell_y=10.0)[0] == 0.0
