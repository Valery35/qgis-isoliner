# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Гигиена поставки. Каталог plugins.qgis.org прогоняет архив через
# сканер секретов, и служебный мусор ловится как находка: в июле 2026
# `.pytest_cache/CACHEDIR.TAG` был помечен как «Potential Hex High
# Entropy String» и заблокировал версию. Никакого секрета там нет, но
# разбираться с блокировкой дороже, чем не класть мусор в архив.
#     python grid_isolines/tests/test_package_hygiene.py
import os
import sys

PKG = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   ".."))

# Папки и файлы, которых в рабочем дереве быть не должно вовсе.
# __pycache__ и .pyc сюда не входят: их создаёт сам Python при любом
# запуске тестов, ловить их тестом бессмысленно. Из архива они убираются
# при упаковке явными исключениями (см. правила релиза в AGENTS.md).
BANNED_DIRS = (".pytest_cache", ".ipynb_checkpoints", ".mypy_cache",
               ".ruff_cache", ".tox", ".idea", ".vscode")
BANNED_SUFFIX = (".orig", ".rej", ".bak", ".swp")
BANNED_NAMES = (".DS_Store", "Thumbs.db")


def _walk():
    for root, dirs, files in os.walk(PKG):
        rel = os.path.relpath(root, PKG)
        yield rel, dirs, files


def test_no_junk_directories():
    bad = []
    for rel, dirs, _files in _walk():
        for d in dirs:
            if d in BANNED_DIRS:
                bad.append(os.path.join(rel, d))
    assert not bad, "служебные папки в поставке: %s" % ", ".join(sorted(bad))


def test_no_junk_files():
    bad = []
    for rel, _dirs, files in _walk():
        for f in files:
            if f in BANNED_NAMES or f.endswith(BANNED_SUFFIX):
                bad.append(os.path.join(rel, f))
    assert not bad, "мусор в поставке: %s" % ", ".join(sorted(bad))


def test_expected_layout():
    """Костяк поставки на месте: без него архив собран неправильно."""
    for name in ("metadata.txt", "__init__.py", "algorithms.py",
                 "provider.py", "i18n.py"):
        assert os.path.exists(os.path.join(PKG, name)), name
    assert os.path.isdir(os.path.join(PKG, "styles"))
    assert os.path.isdir(os.path.join(PKG, "doc"))


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


def test_every_referenced_style_file_exists():
    """Каждый _style_path указывает на существующий .qml.

    Ссылка на несуществующий файл не роняет запуск: стиль просто не
    применяется, а слой выходит с вырожденной серой шкалой и выглядит
    чёрным. Ошибку видит пользователь, а не разработчик, поэтому сторож
    здесь.
    """
    import re
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(here, "algorithms.py"), encoding="utf-8") as f:
        src = f.read()
    names = set(re.findall(r'_style_path\("([^"]+)"\)', src))
    assert names, "ссылки на стили пропали - проверка выродилась"
    missing = [n for n in sorted(names)
               if not os.path.exists(os.path.join(here, "styles",
                                                  "%s.qml" % n))]
    assert not missing, "нет файлов стилей: %s" % ", ".join(missing)
