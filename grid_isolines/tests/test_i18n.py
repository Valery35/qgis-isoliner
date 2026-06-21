# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Проверка двуязычия без QGIS.

1) Поведение tr(): EN подменяет, RU возвращает исходник, неизвестное - как есть.
2) Покрытие: каждая русская строка, реально обёрнутая в _tr()/self.tr() в коде
   (плюс элементы списков-меток и константы, обёрнутые через _tr(x) for x in ...),
   имеет английский перевод в TRANSLATIONS. Иначе - регрессия (новая строка
   без перевода) - тест падает.

Запуск:  python grid_isolines/tests/test_i18n.py
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)              # .../grid_isolines
ROOT = os.path.dirname(PKG)              # родитель пакета

# импортировать i18n как самостоятельный модуль (без пакета/QGIS)
sys.path.insert(0, PKG)
import i18n  # noqa: E402

SCAN_FILES = ["algorithms.py", "widgets.py", "isolines.py", "kb2d.py", "provider.py"]
# Константы-списки и одиночные строки, обёрнутые как _tr(x) for x in LIST / _tr(NAME):
# AST не видит их литералы в точке tr(), поэтому читаем сами присваивания.
CONST_NAMES = ["MODEL_LABELS", "KTYPE_LABELS", "FIT_LABELS",
               "ACTION_LABELS", "PROFILE_NONE", "CREDIT", "GROUP"]


def _wrapped_constants(tree):
    """Литералы из присваиваний MODEL_LABELS=[...] / PROFILE_NONE='...' и т.п."""
    out = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign) and node.target:
            targets = [node.target]
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if not (names & set(CONST_NAMES)) or node.value is None:
            continue
        try:
            val = ast.literal_eval(node.value)
        except Exception:
            continue
        if isinstance(val, str):
            out.append(val)
        elif isinstance(val, (list, tuple)):
            out.extend(x for x in val if isinstance(x, str))
    return out


def collect_keys():
    keys = set()
    for fn in SCAN_FILES:
        path = os.path.join(PKG, fn)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read(), fn)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                is_tr = (isinstance(f, ast.Name) and f.id == "_tr") or \
                        (isinstance(f, ast.Attribute) and f.attr == "tr")
                if is_tr and node.args:
                    a0 = node.args[0]
                    if isinstance(a0, ast.Constant) and isinstance(a0.value, str):
                        keys.add(a0.value)
                # _profile_enum(key, "label", ...) translates its 2nd arg via _tr
                if isinstance(f, ast.Name) and f.id == "_profile_enum" and len(node.args) >= 2:
                    a1 = node.args[1]
                    if isinstance(a1, ast.Constant) and isinstance(a1.value, str):
                        keys.add(a1.value)
        keys.update(_wrapped_constants(tree))
    return keys


def main():
    # --- поведение ---
    i18n.set_language("en")
    assert i18n.tr("Точечный слой") == "Point layer"
    assert i18n.tr("Тип кригинга") == "Kriging type"
    assert i18n.tr("неизвестная строка zzz") == "неизвестная строка zzz"
    i18n.set_language("ru")
    assert i18n.tr("Точечный слой") == "Точечный слой"
    i18n.set_language("en_US")  # нормализация локали
    assert i18n.language() == "en"
    print("[ok] behaviour: en/ru switch, fallback, locale normalization")

    # --- покрытие ---
    keys = collect_keys()
    missing = i18n.missing_keys(keys)
    if missing:
        print("[FAIL] %d wrapped UI strings without EN translation:" % len(missing))
        for m in sorted(missing):
            print("   •", repr(m[:80]))
        sys.exit(1)

    # все переводы непустые
    empty = [k for k in keys if i18n.TRANSLATIONS.get(k, "x") == ""]
    assert not empty, empty

    print("[ok] coverage: %d wrapped UI strings, all translated" % len(keys))
    print("[ok] TRANSLATIONS total entries: %d" % len(i18n.TRANSLATIONS))
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    main()
