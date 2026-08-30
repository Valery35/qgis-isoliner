# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Сверка страницы быстрого старта с фактическим списком инструментов:
#     python grid_isolines/tests/test_quickstart.py
#
# Зачем это нужно. Такая страница гниёт молча: инструмент переименовали или
# перенумеровали, а маршрут продолжает уверенно вести читателя в пустоту.
# Тест превращает обещание в проверяемое утверждение.
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
ALG = os.path.join(PKG, "algorithms.py")

# Страница живёт в репозитории рядом с руководством, а не внутри пакета.
CANDIDATES = [
    os.path.join(PKG, "..", "manual", "quickstart_ru.md"),
    os.path.join(PKG, "..", "..", "manual", "quickstart_ru.md"),
    os.path.join(PKG, "doc", "quickstart_ru.md"),
]

# В тексте инструмент пишется жирным и начинается с номера:
#     **2.13 Диагностика террасинга ЦМР**
MENTION = re.compile(r"\*\*(\d\.\d\d) ([^*]+?)\*\*")


def _page():
    for p in CANDIDATES:
        if os.path.exists(p):
            return open(p, encoding="utf-8").read()
    return None


def _tools():
    """Номер инструмента -> название, прямо из исходника."""
    src = open(ALG, encoding="utf-8").read()
    out = {}
    for m in re.finditer(r'self\.tr\("(\d\.\d\d) ([^"]+)"\)', src):
        out[m.group(1)] = m.group(2)
    return out


def test_source_exposes_numbered_tools():
    tools = _tools()
    assert len(tools) > 30, len(tools)


def test_every_mentioned_tool_exists():
    page = _page()
    if page is None:
        print("   (страницы быстрого старта нет, проверка пропущена)")
        return
    tools = _tools()
    bad = []
    for num, name in MENTION.findall(page):
        real = tools.get(num)
        if real is None:
            bad.append("%s: такого номера нет" % num)
        elif real.strip() != name.strip():
            bad.append("%s: в тексте «%s», в модуле «%s»" % (num, name, real))
    assert not bad, "; ".join(bad)


def test_route_tools_are_mentioned():
    """Пять инструментов маршрута должны быть на странице."""
    page = _page()
    if page is None:
        print("   (страницы быстрого старта нет, проверка пропущена)")
        return
    for num in ("1.02", "1.04", "2.03", "2.13", "4.01"):
        assert num in page, num


def test_page_starts_from_data_that_the_reader_does_not_have():
    """Обещание «своих данных не нужно» должно подтверждаться шагами."""
    page = _page()
    if page is None:
        print("   (страницы быстрого старта нет, проверка пропущена)")
        return
    assert "1.09" in page, "сценарий с точками должен начинаться с генератора"
    assert "2.01" in page, "сценарий с рельефом должен начинаться с загрузки"
    assert "4.10" in page, "сценарий с разрезом должен начинаться с примера"


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL %s: %s" % (name, exc))
    print("%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())


def test_tool_count_matches_the_module():
    """Число инструментов на странице совпадает с модулем.

    Строчка «инструментов в модуле N» устаревает молча: инструменты
    добавляются, а страница остаётся с прежним числом, и первое, что
    видит новый пользователь, - неправда.
    """
    page = _page()
    if page is None:
        print("   (страницы быстрого старта нет, проверка пропущена)")
        return
    n = len(_tools())
    m = re.search(r"Инструментов в модуле (\d+)", page)
    assert m, "на странице нет строки с числом инструментов"
    assert int(m.group(1)) == n, \
        "на странице %s, в модуле %d" % (m.group(1), n)
