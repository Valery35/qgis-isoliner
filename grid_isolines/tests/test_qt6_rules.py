# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Регрессия на проверку Qt6 в каталоге plugins.qgis.org (июль 2026).
#
# Каталог прогоняет загруженную версию через Qt6 Check и блокирует находки.
# Первой поймалась категория-ловушка раскраски: `QgsRendererCategory(
# QVariant(), ...)`. В Qt6 пустой QVariant в значение категории не
# конвертируется, вместо него нужен `NULL` из `qgis.core`.
#
# Тест читает исходники пакета и запрещает конструкции, которые проверка
# каталога считает ошибкой. Дешевле поймать здесь, чем узнать из блокировки
# после заливки.
#     python grid_isolines/tests/test_qt6_rules.py
import os
import re
import sys

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# (регулярное выражение, чем заменять) - только то, что каталог уже ловил
# или ловит по документированным правилам Qt6
BANNED = (
    (r"QVariant\s*\(\s*\)", "NULL из qgis.core"),
    (r"QVariant\.Null", "NULL из qgis.core"),
    (r"QVariant\.Invalid", "NULL из qgis.core"),
)


def _sources():
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py"):
            path = os.path.join(PKG, name)
            with open(path, encoding="utf-8") as f:
                yield name, f.read()


def _strip_comments(code):
    """Убирает строки-комментарии: в них конструкции упоминаются нарочно,
    чтобы объяснить запрет."""
    out = []
    for line in code.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        out.append(line.split("  #")[0])
    return "\n".join(out)


def test_no_empty_qvariant():
    bad = []
    for name, code in _sources():
        body = _strip_comments(code)
        for pattern, hint in BANNED:
            for m in re.finditer(pattern, body):
                line = body[:m.start()].count("\n") + 1
                bad.append("%s:%d %s -> %s" % (name, line, m.group(0), hint))
    assert not bad, "запрещено проверкой Qt6:\n  " + "\n  ".join(bad)


def test_qvariant_types_still_allowed():
    """Типы полей (QVariant.String и подобные) проверкой не запрещены:
    тест не должен ловить их и мешать работе."""
    sample = 'QgsField("a", QVariant.String)\nQgsField("b", QVariant.Double)'
    for pattern, _hint in BANNED:
        assert not re.search(pattern, sample), pattern


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
