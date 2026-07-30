# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Регрессия против находок сканера безопасности каталога plugins.qgis.org.
#
# Каталог прогоняет загруженную версию через сканер и блокирует находки.
# Пойманное в июле 2026:
#   - `.pytest_cache/CACHEDIR.TAG` принят за строку с высокой энтропией
#     (закрыто в tests/test_package_hygiene.py);
#   - `xml.etree.ElementTree` при чтении палитры Leapfrog: стандартный
#     разборщик уязвим к раздутым сущностям и внешним ссылкам, а тянуть в
#     плагин defusedxml нельзя, его нет в поставке QGIS.
#
# Этот тест держит исходники в стороне от конструкций, которые сканер
# считает опасными. Дешевле поймать здесь, чем узнать из блокировки после
# заливки.
#     python grid_isolines/tests/test_scanner_rules.py
import os
import re
import sys

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# (шаблон, чем заменять) - только то, что каталог уже ловил или ловит по
# документированным правилам сканера
BANNED = (
    (r"\bimport\s+xml\b", "свой разбор без модулей xml"),
    (r"\bfrom\s+xml[\s.]", "свой разбор без модулей xml"),
    (r"\bElementTree\b", "свой разбор без модулей xml"),
    (r"\bminidom\b", "свой разбор без модулей xml"),
    (r"\bpyexpat\b", "свой разбор без модулей xml"),
    # точка перед именем означает метод объекта: dlg.exec() это диалог Qt,
    # а не встроенный exec, и запрещать его нечего
    (r"(?<![\w.])eval\s*\(", "разбор без eval"),
    (r"(?<![\w.])exec\s*\(", "выполнение без exec"),
    (r"\bpickle\.loads?\s*\(", "формат без pickle"),
    (r"shell\s*=\s*True", "запуск без оболочки"),
)


def _sources():
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py"):
            with open(os.path.join(PKG, name), encoding="utf-8") as f:
                yield name, f.read()


def _code_only(text):
    """Без строк-комментариев: в них конструкции упоминаются нарочно, чтобы
    объяснить запрет."""
    return "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith("#"))


def test_no_unsafe_constructs():
    bad = []
    for name, text in _sources():
        code = _code_only(text)
        for pattern, hint in BANNED:
            for m in re.finditer(pattern, code):
                line = code[:m.start()].count("\n") + 1
                bad.append("%s:%d %s -> %s"
                           % (name, line, m.group(0).strip(), hint))
    assert not bad, "сканер каталога это заблокирует:\n  " + "\n  ".join(bad)


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
