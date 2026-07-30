# -*- coding: utf-8 -*-
"""Тесты ядра кандидатов бровок (topo_break) на синтетике.

Запуск: python test_topo_break.py из папки tests или корня модуля.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import topo_break as tb  # noqa: E402


def _bench(h=60, w=80, ztop=110.0, zbot=100.0, x0=30, x1=40):
    """Синтетический уступ: плато, откос, нижняя площадка.

    Колонки до x0 - верхнее плато, x0..x1 - равномерный откос,
    дальше нижняя площадка. Бровка стоит на колонке x0, подошва на x1.
    """
    z = np.empty((h, w), dtype=float)
    for c in range(w):
        if c <= x0:
            z[:, c] = ztop
        elif c >= x1:
            z[:, c] = zbot
        else:
            t = (c - x0) / float(x1 - x0)
            z[:, c] = ztop + t * (zbot - ztop)
    return z


def test_edge_strength_peaks_at_breaks():
    """Признак велик на бровке и подошве, мал на плато и середине откоса."""
    z = _bench()
    e, sign, slope = tb.edge_strength(z, cell=1.0)
    row = 30
    at_brow = e[row, 29:32].max()
    at_toe = e[row, 39:42].max()
    at_flat = e[row, 5:15].max()
    at_mid = e[row, 34:37].max()
    assert at_brow > 5.0 * max(at_flat, 1e-9)
    assert at_toe > 5.0 * max(at_flat, 1e-9)
    assert at_brow > 2.0 * at_mid


def test_sign_separates_brow_and_toe():
    """Знак кривизны: у бровки выпуклый, у подошвы вогнутый."""
    z = _bench()
    _e, sign, _s = tb.edge_strength(z, cell=1.0)
    row = 30
    assert sign[row, 30] == 1 or sign[row, 29] == 1     # бровка
    assert sign[row, 40] == -1 or sign[row, 41] == -1   # подошва


def test_candidates_found_and_positioned():
    """Полный проход: два кандидата, стоят на своих колонках, знаки верны."""
    z = _bench()
    cands = tb.breakline_candidates(z, cell=1.0, min_drop=1.0,
                                    min_len_cells=10)
    assert len(cands) >= 2
    brows = [c for c in cands if c["kind"] == "brow"]
    toes = [c for c in cands if c["kind"] == "toe"]
    assert brows and toes
    bc = np.median([c for _r, c in brows[0]["cells"]])
    tc = np.median([c for _r, c in toes[0]["cells"]])
    assert abs(bc - 30) <= 1.5
    assert abs(tc - 40) <= 1.5


def test_drop_measures_bench_height():
    """Перепад поперёк линии близок к высоте уступа."""
    z = _bench(ztop=110.0, zbot=100.0)
    cands = tb.breakline_candidates(z, cell=1.0, min_drop=1.0,
                                    min_len_cells=10, probe=8)
    top = cands[0]
    assert top["drop"] > 4.0          # заметная часть десятиметрового уступа
    assert top["length_m"] > 30.0     # линия прошла вдоль большей части борта


def test_min_drop_filters_shallow():
    """Мелкий уступ ниже отсечки не проходит: колея не засоряет выход."""
    z = _bench(ztop=100.3, zbot=100.0)
    cands = tb.breakline_candidates(z, cell=1.0, min_drop=0.5,
                                    min_len_cells=10, probe=8)
    assert cands == []


def test_flat_surface_yields_nothing():
    """Плоскость и ровный наклон кандидатов не дают."""
    flat = np.full((40, 40), 100.0)
    assert tb.breakline_candidates(flat, cell=1.0) == []
    xx = np.tile(np.arange(40, dtype=float), (40, 1))
    tilted = 100.0 + 0.3 * xx
    assert tb.breakline_candidates(tilted, cell=1.0, min_drop=0.2) == []


def test_nodata_does_not_leak():
    """Дыра nodata не рождает ложную бровку по своему краю."""
    z = _bench()
    z[10:20, 55:65] = np.nan
    cands = tb.breakline_candidates(z, cell=1.0, min_drop=1.0,
                                    min_len_cells=10)
    for c in cands:
        cols = [cc for _r, cc in c["cells"]]
        assert np.median(cols) < 50    # все кандидаты на уступе, не у дыры


def test_trace_lines_chains_and_ring():
    """Трассировка: цепочка остаётся одной линией, кольцо замыкается."""
    m = np.zeros((10, 10), dtype=bool)
    m[5, 1:8] = True
    lines = tb.trace_lines(m)
    assert len(lines) == 1 and len(lines[0]) == 7

    ring = np.zeros((10, 10), dtype=bool)
    ring[2, 2:7] = True
    ring[6, 2:7] = True
    ring[3:6, 2] = True
    ring[3:6, 6] = True
    lines = tb.trace_lines(ring)
    total = sum(len(ln) for ln in lines)
    assert total >= 15                 # кольцо обойдено, ячейки не потеряны


def test_bent_bench_is_followed():
    """Г-образный борт: линия кандидата следует за поворотом бровки.

    Уступ идёт по колонке в нижней половине и по строке в правой верхней,
    с сопряжением. Детектор должен пройти оба плеча, а не оборваться на
    повороте.
    """
    h = w = 70
    z = np.full((h, w), 110.0)
    for r in range(h):
        for c in range(w):
            # плато Г-образное: спуск только в юго-восточном квадранте,
            # расстояние за бровку - меньшая из двух координатных
            d = min(c - 30, r - 30) if (c > 30 and r > 30) else 0
            if d <= 0:
                z[r, c] = 110.0
            elif d >= 10:
                z[r, c] = 100.0
            else:
                z[r, c] = 110.0 - d
    cands = tb.breakline_candidates(z, cell=1.0, min_drop=1.0,
                                    min_len_cells=10, probe=8)
    brows = [c for c in cands if c["kind"] == "brow"]
    assert brows
    total = sum(len(c["cells"]) for c in brows)
    assert total > 45          # оба плеча по ~27 ячеек, одно плечо не пройдёт


def test_pairing_on_two_benches():
    """Два уступа: каждая бровка находит свою подошву, а не чужую.

    Рельеф из двух уступов с бермой. Ближайшая по расстоянию подошва для
    верхней бровки могла бы оказаться нижней (берма узкая), спуск по склону
    приводит к правильной.
    """
    h, w = 40, 90
    z = np.empty((h, w))
    for c in range(w):
        if c < 20:
            v = 120.0
        elif c < 30:
            v = 120.0 - (c - 20)          # первый откос
        elif c < 40:
            v = 110.0                     # берма
        elif c < 50:
            v = 110.0 - (c - 40)          # второй откос
        else:
            v = 100.0
        z[:, c] = v
    brows = [[(r, 20) for r in range(5, 35)], [(r, 40) for r in range(5, 35)]]
    toes = [[(r, 30) for r in range(5, 35)], [(r, 50) for r in range(5, 35)]]
    groups, unpaired = tb.pair_breaklines(brows, toes, z, cell=1.0,
                                          max_dist=50.0)
    assert len(groups) == 2
    got = {g["toe"]: g["brows"] for g in groups}
    assert got == {0: [0], 1: [1]}
    assert all(g["share"] > 0.9 for g in groups)
    assert unpaired == []


def test_pairing_reports_orphans():
    """Бровка без подошвы в пределах пути остаётся непарной с причиной."""
    h, w = 30, 60
    z = np.empty((h, w))
    for c in range(w):
        z[:, c] = 100.0 if c < 20 else 100.0 - (c - 20) * 0.02
    brows = [[(r, 20) for r in range(5, 25)]]
    groups, unpaired = tb.pair_breaklines(brows, [], z, cell=1.0, max_dist=5.0)
    assert groups == []
    assert unpaired and unpaired[0]["kind"] == "brow"


def test_sample_z_reads_raster():
    """Отметки снимаются с растра по ячейкам линии."""
    z = np.arange(100, dtype=float).reshape(10, 10)
    vals = tb.sample_z(z, [(0, 0), (1, 1), (9, 9)])
    assert vals == [0.0, 11.0, 99.0]


def test_toe_gathers_several_crest_fragments():
    """Куски одной бровки собираются в одну форму, а не в разные пары.

    Трассировка режет длинную бровку разрывами признака, и каждый кусок
    спускается к той же подошве. Раньше это давало N пар с одной и той же
    подошвой, и подошва попадала в выход N раз.
    """
    h, w = 40, 60
    z = np.empty((h, w))
    for c in range(w):
        z[:, c] = 100.0 if c < 20 else (100.0 - (c - 20) if c < 30 else 90.0)
    # три куска одной бровки на колонке 20 и одна подошва на колонке 30
    brows = [[(r, 20) for r in range(2, 12)],
             [(r, 20) for r in range(14, 24)],
             [(r, 20) for r in range(26, 36)]]
    toes = [[(r, 30) for r in range(2, 36)]]
    groups, unpaired = tb.pair_breaklines(brows, toes, z, cell=1.0,
                                          max_dist=30.0)
    assert len(groups) == 1
    assert sorted(groups[0]["brows"]) == [0, 1, 2]
    assert unpaired == []


def test_kind_reader_tolerates_real_codes():
    """Поле вида в сдаточном комплекте: слова и коды классификаторов.

    2.19 пишет brow и toe, а в реальном слое будет «Бровка откоса,
    насыпи, выемки укреплённая» или код 62350400. Читатель терпит и то, и
    другое, а неузнанное честно возвращает None: угадывать вслепую хуже,
    чем сказать «не понял».
    """
    assert tb.classify_kind("brow") == "brow"
    assert tb.classify_kind("toe") == "toe"
    assert tb.classify_kind("Бровка откоса, насыпи, выемки укреплённая") \
        == "brow"
    assert tb.classify_kind("Подошва откоса") == "toe"
    assert tb.classify_kind("62350400") == "brow"
    assert tb.classify_kind("22213000") == "brow"
    assert tb.classify_kind("crest") == "brow"
    assert tb.classify_kind("TOE") == "toe"
    # неузнанное и пустое
    for v in ("трубопровод", "", None, "12345678", "  "):
        assert tb.classify_kind(v) is None, v


def test_toe_wins_over_crest_words():
    """«Подошва откоса» содержит и подошву, и откос - должна быть подошвой.

    Порядок проверки слов не случаен: сначала подошва, потом бровка.
    Обратный порядок ловил бы «подошву» на слове из названия объекта.
    """
    assert tb.classify_kind("подошва откоса насыпи") == "toe"
    assert tb.classify_kind("низ уступа") == "toe"
    assert tb.classify_kind("верх уступа") == "brow"


def test_classify_kinds_reports_unknown():
    """Разбор набора: решения по значению и список неузнанных."""
    vals = ["brow", "brow", "Подошва откоса", "трубопровод", None]
    decided, unknown = tb.classify_kinds(vals)
    assert decided == {"brow": "brow", "Подошва откоса": "toe"}
    assert unknown == ["трубопровод", None]


def test_pair_by_elevation_without_dem():
    """Пары без ЦМР: подошва - ближайшая линия ниже бровки.

    Спуск по склону отвечал на вопрос «где низ». Когда у линий есть
    отметки, ответ уже в данных, и рельеф для спаривания не нужен - это
    разрывает круг топографического сценария, где рельеф как раз и
    строится.
    """
    brows = [[(r, 10) for r in range(5, 25)],
             [(r, 30) for r in range(5, 25)]]
    toes = [[(r, 17) for r in range(5, 25)],
            [(r, 37) for r in range(5, 25)]]
    groups, unpaired = tb.pair_by_elevation(
        brows, toes, [120.0, 110.0], [110.0, 100.0], 1.0, max_dist=50.0)
    assert len(groups) == 2 and not unpaired
    assert {g["toe"]: g["brows"] for g in groups} == {0: [0], 1: [1]}


def test_pair_by_elevation_refuses_higher_toe():
    """Линия выше бровки подошвой быть не может, даже если она ближе."""
    brows = [[(r, 10) for r in range(5, 25)]]
    toes = [[(r, 12) for r in range(5, 25)],    # ближе, но выше
            [(r, 25) for r in range(5, 25)]]    # дальше, но ниже
    groups, unpaired = tb.pair_by_elevation(
        brows, toes, [100.0], [110.0, 90.0], 1.0, max_dist=50.0)
    assert len(groups) == 1
    assert groups[0]["toe"] == 1
    assert [u["kind"] for u in unpaired] == ["toe"]


def test_pair_by_elevation_respects_limit():
    """Дальше предела пара не собирается, бровка уходит в непарные."""
    brows = [[(r, 10) for r in range(5, 25)]]
    toes = [[(r, 200) for r in range(5, 25)]]
    groups, unpaired = tb.pair_by_elevation(
        brows, toes, [120.0], [100.0], 1.0, max_dist=50.0)
    assert not groups
    reasons = sorted(u["kind"] for u in unpaired)
    assert reasons == ["brow", "toe"]


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
