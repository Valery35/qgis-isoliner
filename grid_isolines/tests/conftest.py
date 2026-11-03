# -*- coding: utf-8 -*-
"""Общая настройка тестов.

Язык интерфейса - состояние модуля i18n, и тест, переключивший его на
английский, оставлял его таким для всех следующих. Пока чистые модули не
переводили своих сообщений, это ничего не значило. С 5.13.46 они
переводят, и проверки русских сообщений стали зависеть от порядка
запуска. Перед каждым тестом язык возвращается к русскому, исходному.
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
if PKG not in sys.path:
    sys.path.insert(0, PKG)


@pytest.fixture(autouse=True)
def _russian_by_default():
    mods = [m for m in (sys.modules.get("i18n"),
                        sys.modules.get("grid_isolines.i18n")) if m]
    if not mods:
        import i18n  # noqa: F401
        mods = [sys.modules["i18n"]]
    for m in mods:
        m.set_language("ru", help="ru")
    yield
    for m in mods:
        m.set_language("ru", help="ru")
