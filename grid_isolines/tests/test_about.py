# -*- coding: utf-8 -*-
"""Headless-тесты about.py: чтение metadata.txt и путь к руководству.

Без QGIS: about.py импортирует Qt только внутри функций диалогов.
Запуск: python grid_isolines/tests/test_about.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from grid_isolines import about  # noqa: E402
from grid_isolines import i18n  # noqa: E402


def test_read_metadata():
    meta = about.read_metadata()
    assert re.match(r"^\d+\.\d+\.\d+$", meta["version"]), meta["version"]
    assert meta["changelog"].strip().startswith(meta["version"])
    assert "%" not in meta["changelog"] or "%%" in meta["changelog"] or \
        "процент" in meta["changelog"]
    assert meta["homepage"].startswith("https://")


def test_manual_path_by_locale():
    i18n.set_language("ru")
    p_ru = about.manual_path()
    assert p_ru.endswith("Isoliner.pdf"), p_ru
    i18n.set_language("en")
    p_en = about.manual_path()
    assert p_en.endswith("Isoliner_en.pdf"), p_en
    i18n.set_language(None)


def test_menu_strings_translated():
    for s in ("О плагине…", "Руководство (PDF)", "История изменений",
              "Версия %s", "Руководство не найдено."):
        i18n.set_language("en")
        assert i18n.tr(s) != s, s
        i18n.set_language(None)


if __name__ == "__main__":
    test_read_metadata()
    test_manual_path_by_locale()
    test_menu_strings_translated()
    print("test_about: OK")
