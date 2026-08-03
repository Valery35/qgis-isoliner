# -*- coding: utf-8 -*-
"""Тесты диагностики согласованности пачки (stack_check).

Запуск: python test_stack_check.py.
"""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stack_check as sc  # noqa: E402


def _wedge(n=40, z_top=100.0, dip=0.0):
    """Пласт, выклинивающийся к правому краю: мощность падает до нуля."""
    x = np.linspace(0.0, 1.0, n)
    top = np.tile(z_top - dip * x, (n, 1))
    bot = np.tile(z_top - 10.0 * (1.0 - x) - dip * x, (n, 1))
    return top, bot


def test_uniform_bed_is_clean():
    """Выдержанный пласт: ни выклинивания, ни отрицательной мощности."""
    top = np.full((20, 20), 100.0)
    bot = np.full((20, 20), 90.0)
    codes, m, st = sc.check_bed(top, bot)
    assert (codes == sc.CODE_OK).all()
    assert st["n_zero"] == 0 and st["n_neg"] == 0
    assert abs(st["min_thk"] - 10.0) < 1e-9


def test_wedge_gives_zero_not_error():
    """Выклинивание - это нулевая мощность, а не ошибка.

    Пласт, честно сходящий на нет, обязан отличаться от пласта, у которого
    кровля провалилась ниже подошвы: первое геология, второе дефект.
    """
    top, bot = _wedge()
    codes, m, st = sc.check_bed(top, bot, zero_tol=0.05)
    assert st["n_zero"] > 0
    assert st["n_neg"] == 0
    assert sc.CODE_NEG not in np.unique(codes)


def test_crossing_surfaces_are_caught():
    """Кровля ниже подошвы: интерполяторы разошлись в зоне выклинивания."""
    n = 30
    x = np.linspace(-1.0, 1.0, n)
    top = np.tile(100.0 + x, (n, 1))
    bot = np.tile(100.0 - x, (n, 1))     # пересекаются в середине
    codes, m, st = sc.check_bed(top, bot)
    assert st["n_neg"] > 0
    assert (codes == sc.CODE_NEG).any()
    assert st["min_thk"] < 0


def test_known_overturn_is_not_an_error():
    """Отмеченное опрокидывание уходит в свой счёт, а не в ошибки.

    Отрицательная мощность бывает двух происхождений, и различить их
    арифметикой нельзя. Индикатор из скважин снимает вопрос там, где он
    посчитан.
    """
    top = np.full((10, 10), 90.0)
    bot = np.full((10, 10), 100.0)      # всюду перевёрнуто
    mask = np.zeros((10, 10), dtype=bool)
    mask[:5] = True                     # половина известна как опрокидывание
    codes, m, st = sc.check_bed(top, bot, overturned=mask)
    assert st["n_known"] == 50
    assert st["n_neg"] == 50
    assert (codes[:5] == sc.CODE_KNOWN).all()
    assert (codes[5:] == sc.CODE_NEG).all()


def test_stack_catches_contact_overlap():
    """Пласты по отдельности целы, а вместе перехлёстываются.

    Подошва верхнего опускается ниже кровли нижнего. Проверка каждого
    пласта в одиночку такого не увидит - нужен взгляд на пару соседей.
    """
    n = 20
    x = np.linspace(0.0, 1.0, n)
    top1 = np.tile(np.full(n, 100.0), (n, 1))
    bot1 = np.tile(95.0 - 6.0 * x, (n, 1))     # уходит вниз
    top2 = np.tile(np.full(n, 92.0), (n, 1))   # стоит на месте
    bot2 = np.tile(np.full(n, 80.0), (n, 1))
    codes, rep = sc.check_stack([("верхний", top1, bot1),
                                 ("нижний", top2, bot2)])
    beds = [r for r in rep if r["kind"] == "bed"]
    contacts = [r for r in rep if r["kind"] == "contact"]
    assert all(r["n_neg"] == 0 for r in beds)   # каждый пласт цел
    assert contacts[0]["n_cross"] > 0           # а вместе перехлёст
    assert (codes == sc.CODE_CROSS).any()


def test_clean_stack_reports_nothing():
    """Согласованная пачка: все счётчики нулевые."""
    beds = []
    z = 100.0
    for i in range(3):
        beds.append(("пласт %d" % (i + 1),
                     np.full((15, 15), z), np.full((15, 15), z - 8.0)))
        z -= 10.0
    codes, rep = sc.check_stack(beds)
    assert (codes == sc.CODE_OK).all()
    assert all(r.get("n_neg", 0) == 0 and r.get("n_cross", 0) == 0
               for r in rep)


def test_nodata_does_not_leak():
    """Пустые ячейки не попадают ни в один счёт."""
    top = np.full((10, 10), 100.0)
    bot = np.full((10, 10), 90.0)
    top[0, 0] = np.nan
    codes, m, st = sc.check_bed(top, bot)
    assert st["n_valid"] == 99
    assert st["n_zero"] == 0 and st["n_neg"] == 0
    assert codes[0, 0] == sc.CODE_OK


def test_area_not_cell_count():
    """В журнал идёт площадь: число ячеек само по себе ничего не значит."""
    codes = np.zeros((10, 10), dtype=np.uint8)
    codes[:2] = sc.CODE_NEG
    assert abs(sc.zone_extent_m(codes, sc.CODE_NEG, 1.0) - 20.0) < 1e-9
    assert abs(sc.zone_extent_m(codes, sc.CODE_NEG, 30.0) - 18000.0) < 1e-6


def test_summary_lines_are_readable():
    """Строки журнала складываются и содержат площади."""
    top, bot = _wedge()
    codes, rep = sc.check_stack([("КрII", top, bot)], zero_tol=0.05)
    lines = sc.summarize(rep, cell=5.0)
    assert lines and "КрII" in lines[0]
    assert "м2" in lines[0]


def test_overturned_holes_from_intervals():
    """Опрокидывание видно в самой скважине: порядок кодов по стволу.

    Считать это по скважинам надёжнее, чем гадать по гридам: скважина
    видит настоящую последовательность, грид - только интерполяцию.
    """
    order = ["A", "B", "C"]
    iv = [
        # нормальная скважина: A, B, C сверху вниз
        {"hole_id": "n1", "frm": 0.0, "to": 5.0, "code": "A"},
        {"hole_id": "n1", "frm": 5.0, "to": 9.0, "code": "B"},
        {"hole_id": "n1", "frm": 9.0, "to": 14.0, "code": "C"},
        # перевёрнутая: B встретился ниже C
        {"hole_id": "x1", "frm": 0.0, "to": 4.0, "code": "A"},
        {"hole_id": "x1", "frm": 4.0, "to": 8.0, "code": "C"},
        {"hole_id": "x1", "frm": 8.0, "to": 12.0, "code": "B"},
    ]
    bad = sc.overturned_holes(iv, order)
    assert bad == {"x1"}


def test_unknown_codes_do_not_break_order():
    """Коды вне справочника пропускаются, а не считаются нарушением."""
    order = ["A", "B"]
    iv = [{"hole_id": "h", "frm": 0.0, "to": 3.0, "code": "A"},
          {"hole_id": "h", "frm": 3.0, "to": 6.0, "code": "ЩЕБЕНЬ"},
          {"hole_id": "h", "frm": 6.0, "to": 9.0, "code": "B"}]
    assert sc.overturned_holes(iv, order) == set()


def test_witness_has_three_states():
    """Свидетельство скважин: подтверждено, опровергнуто, не проверено.

    Между скважинами правды нет, и размазывать её интерполяцией значило бы
    выдавать догадку за данные. Непроверенное - честное состояние.
    """
    gt = (0.0, 10.0, 0.0, 100.0, 0.0, -10.0)     # ячейка 10 м, 10x10
    holes = [("bad", 15.0, 95.0), ("good", 85.0, 15.0)]
    w = sc.witness_map((10, 10), gt, holes, {"bad"}, radius_m=25.0)
    assert w[0, 1] == sc.W_CONFIRMS              # рядом с перевёрнутой
    assert w[8, 8] == sc.W_CONTRADICTS           # рядом с нормальной
    assert w[5, 5] == sc.W_UNCHECKED             # далеко от обеих


def test_apply_witness_translates_codes():
    """Подтверждённое опрокидывание перестаёт быть дефектом, спорное - нет."""
    codes = np.full((3, 3), sc.CODE_NEG, dtype=np.uint8)
    w = np.array([[sc.W_CONFIRMS, sc.W_CONTRADICTS, sc.W_UNCHECKED]] * 3,
                 dtype=np.uint8)
    out = sc.apply_witness(codes, w)
    assert (out[:, 0] == sc.CODE_KNOWN).all()    # скважина подтвердила
    assert (out[:, 1] == sc.CODE_NEG).all()      # скважина против - дефект
    assert (out[:, 2] == sc.CODE_NEG).all()      # нет данных - остаётся


def test_pair_by_name_on_real_names():
    """Имена комплекта разбираются в пары без настройки.

    Имена в проектах несут структуру: B_top и B_bottom это пласт B.
    Разбирать их дешевле, чем заставлять человека выбирать десяток слоёв
    по одному в нужном порядке.
    """
    names = ["B_top", "B_bottom", "А'Б_top", "А'Б_bottom",
             "KpII_top", "KpII_bottom"]
    pairs, un = sc.pair_by_name(names)
    assert [p[0] for p in pairs] == ["B", "А'Б", "KpII"]
    assert not un


def test_pair_by_name_reports_leftovers():
    """Неразобранное перечисляется, а не проглатывается."""
    names = ["B_top", "B_bottom", "рельеф", "KpII_top"]
    pairs, un = sc.pair_by_name(names)
    assert [p[0] for p in pairs] == ["B"]
    reasons = {nm: why for nm, why in un}
    assert "рельеф" in reasons and "признака" in reasons["рельеф"]
    assert "KpII_top" in reasons and "подошвы" in reasons["KpII_top"]


def test_bottom_parsed_before_top():
    """_bottom не должен разбираться как _bot плюс хвост.

    Порядок проверки суффиксов не случаен: _bottom содержит _bot, и при
    обратном порядке пласт назвался бы «B_tom».
    """
    bed, role = sc.split_name("B_bottom")
    assert bed == "B" and role == "bot"
    bed2, role2 = sc.split_name("B_bot")
    assert bed2 == "B" and role2 == "bot"


def test_order_from_reference():
    """Справочник пластов задаёт порядок пар, а не порядок слоёв."""
    names = ["KpII_top", "KpII_bottom", "B_top", "B_bottom"]
    pairs, _un = sc.pair_by_name(names, order=["B", "KpII"])
    assert [p[0] for p in pairs] == ["B", "KpII"]


def test_detects_inverted_order():
    """Перехлёст на всю площадь при целых пластах - признак обратного порядка.

    На живом прогоне пласты подались снизу вверх, и инструмент показал
    перехлёст 94.9 км2 при полном порядке внутри каждого пласта. Геология
    так не выглядит: настоящий перехлёст местный и соседствует с
    выклиниванием.
    """
    n = 30
    upper = ("B", np.full((n, n), 100.0), np.full((n, n), 90.0))
    lower = ("КрII", np.full((n, n), 88.0), np.full((n, n), 70.0))
    codes, rep = sc.check_stack([upper, lower])
    assert not sc.looks_inverted(rep, codes)
    codes2, rep2 = sc.check_stack([lower, upper])
    assert sc.looks_inverted(rep2, codes2)


def test_min_gap_map_shows_where():
    """Карта зазора отвечает на вопрос «где», а не только «сколько».

    Наименьший зазор в метр при мощностях в десятки означает, что пачка
    держится на удаче, и знать надо место: там перехлёст и возникнет.
    """
    n = 20
    x = np.linspace(0.0, 1.0, n)
    top1 = np.tile(np.full(n, 100.0), (n, 1))
    bot1 = np.tile(95.0 - 4.0 * x, (n, 1))      # подошва опускается
    top2 = np.tile(np.full(n, 90.0), (n, 1))
    bot2 = np.tile(np.full(n, 80.0), (n, 1))
    gap = sc.min_gap_map([("верх", top1, bot1), ("низ", top2, bot2)])
    assert gap[0, 0] > gap[0, -1]               # сужается вправо
    assert abs(float(gap.min()) - 1.0) < 1e-6


def test_gap_map_needs_two_beds():
    """Один пласт: зазора нет, и это не ошибка."""
    n = 10
    assert sc.min_gap_map([("A", np.full((n, n), 10.0),
                            np.full((n, n), 0.0))]) is None


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
