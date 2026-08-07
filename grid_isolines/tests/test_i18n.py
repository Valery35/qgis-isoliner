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
        except (ValueError, SyntaxError):  # literal_eval на не-литерале
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

    # Повторяющиеся ключи словаря. Литерал молча оставляет последнее
    # значение, поэтому омоним («подошва» пласта и подошва уступа) тихо
    # ломает перевод там, где его добавили позже. Ловим разбором исходника:
    # в готовом словаре дубля уже не видно.
    seen, clash, same = {}, [], 0
    for node in ast.walk(ast.parse(open(
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "i18n.py"),
            encoding="utf-8").read())):
        if not isinstance(node, ast.Dict) or len(node.keys) < 50:
            continue
        for k, v in zip(node.keys, node.values):
            if not (isinstance(k, ast.Constant) and isinstance(v, ast.Constant)):
                continue
            if k.value in seen:
                if seen[k.value] != v.value:
                    clash.append(k.value)
                else:
                    same += 1
                continue
            seen[k.value] = v.value
    # Повтор с РАЗНЫМ переводом - настоящий дефект: литерал словаря молча
    # оставляет последнее значение, и омоним («подошва» пласта и подошва
    # уступа) тихо ломает перевод там, где его добавили позже. Повтор с
    # тем же значением безвреден, он только считается.
    if clash:
        print("[FAIL] один ключ с разными переводами:", clash[:5])
        sys.exit(1)
    print("[ok] no conflicting keys (%d harmless repeats)" % same)
    # Затенённые тесты: две функции с одним именем в файле - вторая молча
    # съедает первую, и та не запускается никогда. Один раз уже случилось в
    # test_fractal, поэтому сторож на весь набор.
    import collections as _c
    shadowed = []
    tdir = os.path.dirname(os.path.abspath(__file__))
    for fn in sorted(os.listdir(tdir)):
        if not (fn.startswith("test_") and fn.endswith(".py")):
            continue
        try:
            tree = ast.parse(open(os.path.join(tdir, fn),
                                  encoding="utf-8").read())
        except SyntaxError:
            continue
        names = [n.name for n in tree.body
                 if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
        shadowed += ["%s:%s" % (fn, k)
                     for k, v in _c.Counter(names).items() if v > 1]
    if shadowed:
        print("[FAIL] затенённые тесты:", shadowed)
        sys.exit(1)
    print("[ok] no shadowed test functions")
    # Стоп-слова стиля. «Честный» и «врёт» пролезают в тексты как затычка
    # там, где надо сказать по существу, и вычищать их вручную каждый раз
    # бесполезно - проверка дешевле. Правило принято для всех публикуемых
    # текстов, поэтому смотрим и код со справками, и руководства.
    import glob as _g
    import re as _re
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    bad = []
    for path in _g.glob(os.path.join(root, "*.py")):
        txt = open(path, encoding="utf-8").read()
        for m in _re.finditer(r"честн[а-яё]*|врёт|врут|врал[а-яё]*", txt):
            bad.append("%s: %s" % (os.path.basename(path),
                                   txt[max(0, m.start() - 30):m.end() + 20]
                                   .replace("\n", " ").strip()))
    if bad:
        print("[FAIL] стоп-слова стиля:", len(bad))
        for b in bad[:5]:
            print("   •", b)
        sys.exit(1)
    print("[ok] no style stop-words")
    print("[ok] TRANSLATIONS total entries: %d" % len(i18n.TRANSLATIONS))
    print("ALL TESTS PASSED")


def test_i18n_checks_run_under_pytest():
    """Обёртка для pytest.

    Файл написан скриптом и весь полезный разбор лежит в main() под
    ``if __name__ == "__main__"``. Pytest такие файлы собирает пустыми:
    ни одной функции с именем test_ в нём не было, и проверки переводов,
    затенённых тестов и стоп-слов молча не выполнялись ни в одном общем
    прогоне. При провале main() уходит в sys.exit(1), это SystemExit,
    и тест падает как положено.
    """
    main()


if __name__ == "__main__":
    main()
