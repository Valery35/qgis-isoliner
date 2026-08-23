# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты реестра подоснов. Реестр не тянет QGIS: источники описаны файлами
# .qlr, разбор идёт средствами стандартной библиотеки.
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import basemap_registry as basemaps  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, "basemaps")


def test_folder_ships_with_the_plugin():
    """Папка источников должна попадать в поставку, а не остаться локальной."""
    assert os.path.isdir(FOLDER), FOLDER


def test_sources_are_loaded():
    assert len(basemaps.load(FOLDER)) >= 7


def test_osm_is_present():
    names = [b.name for b in basemaps.load(FOLDER)]
    assert "OpenStreetMap" in names


def test_satellite_imagery_is_present():
    """Геологу космоснимок нужен чаще всего."""
    names = [b.name for b in basemaps.load(FOLDER)]
    assert "Космоснимки ESRI" in names


def test_order_follows_file_names():
    """Порядок в списке задаётся числовым префиксом имени файла."""
    keys = [b.key for b in basemaps.load(FOLDER)]
    assert keys == sorted(keys)


def test_every_source_passes_the_check():
    """Недействительный слой QGIS добавляет молча, поэтому сторож здесь.

    Без crs система координат угадывается, у плиточного источника нужен
    шаблон z, x, y.
    """
    for bm in basemaps.load(FOLDER):
        assert basemaps.check_source(bm.source) == [], bm.name


def test_broken_file_is_skipped(tmp_path):
    """Битый .qlr не роняет реестр: источник просто пропускается."""
    p = tmp_path / "01_broken.qlr"
    p.write_text("не xml", encoding="utf-8")
    assert basemaps.load(str(tmp_path)) == []


def test_missing_folder_gives_empty_list():
    assert basemaps.load(os.path.join(ROOT, "нет_такой_папки")) == []


def test_check_source_names_the_missing_crs():
    problems = basemaps.check_source("type=xyz&url=http://x/{z}/{x}/{y}.png")
    assert any("координат" in p for p in problems)


def test_check_source_names_the_missing_tile_template():
    problems = basemaps.check_source("crs=EPSG:3857&type=xyz&url=http://x/y.png")
    assert len(problems) == 3


def test_arcgis_rest_needs_no_tile_template():
    """У ArcGIS REST шаблона плиток нет и быть не должно."""
    src = "crs=EPSG:3857&url=https://server/MapServer&format=png"
    assert basemaps.check_source(src) == []


def test_transparent_style_is_reported():
    """Космоснимок идёт с полупрозрачным вариантом, он и подписан в списке."""
    by_key = {b.key: b for b in basemaps.load(FOLDER)}
    assert by_key["02_esri_imagery"].has_transparent_style


# --- совместимость Qt5 и Qt6 ---------------------------------------------
#
# QGIS 4 собран на Qt6, где перечисления строгие: Qt.UserRole там нет,
# нужно Qt.ItemDataRole.UserRole. Окно подосновы падало на этом с
# AttributeError, и падало уже у пользователя - в QGIS 3 тот же код
# работает. Проверка идёт на подставном Qt обеих раскладок, поэтому не
# требует ни QGIS, ни Qt.

import importlib.util  # noqa: E402
import sys  # noqa: E402
import types  # noqa: E402

QT_COMPAT = os.path.join(ROOT, "qt_compat.py")

_NAMES = (("UserRole", "ItemDataRole", 32),
          ("WaitCursor", "CursorShape", 3),
          ("Checked", "CheckState", 2),
          ("Unchecked", "CheckState", 0),
          ("ItemIsUserCheckable", "ItemFlag", 16),
          ("NonModal", "WindowModality", 0))


def _fake_qt(strict):
    """Подставной Qt: строгие перечисления Qt6 или плоские Qt5."""
    class Qt:
        pass

    for name, scope, value in _NAMES:
        if strict:
            holder = getattr(Qt, scope, None)
            if holder is None:
                holder = type(scope, (), {})
                setattr(Qt, scope, holder)
            setattr(holder, name, value)
        else:
            setattr(Qt, name, value)
    return Qt


def _load_compat(strict):
    """Загружает qt_compat поверх подставного Qt и убирает подмену за собой.

    Записи sys.modules восстанавливаются в исходное состояние: иначе
    заглушка остаётся в общем прогоне и роняет соседей, которым нужен
    настоящий модуль qgis.
    """
    core = types.ModuleType("qgis.PyQt.QtCore")
    core.Qt = _fake_qt(strict)
    fake = {"qgis": types.ModuleType("qgis"),
            "qgis.PyQt": types.ModuleType("qgis.PyQt"),
            "qgis.PyQt.QtCore": core}
    saved = {k: sys.modules.get(k) for k in fake}
    sys.modules.update(fake)
    try:
        spec = importlib.util.spec_from_file_location("qt_compat_probe",
                                                      QT_COMPAT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        for key, prev in saved.items():
            if prev is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = prev
    return mod


def test_strict_enums_resolve():
    """Qt6: константа берётся из вложенного перечисления."""
    mod = _load_compat(strict=True)
    for name, _scope, value in _NAMES:
        assert getattr(mod, name) == value, name


def test_flat_enums_resolve():
    """Qt5: та же константа лежит прямо на Qt."""
    mod = _load_compat(strict=False)
    for name, _scope, value in _NAMES:
        assert getattr(mod, name) == value, name


def test_dialog_window_uses_the_compat_layer():
    """Окно подосновы не должно обращаться к Qt напрямую."""
    with open(os.path.join(ROOT, "basemapview.py"), encoding="utf-8") as f:
        code = "\n".join(ln for ln in f.read().splitlines()
                         if not ln.lstrip().startswith("#"))
    assert "Qt.UserRole" not in code
    assert "Qt.Checked" not in code and "Qt.Unchecked" not in code
    assert "Qt.WaitCursor" not in code
    assert "QDialogButtonBox.ActionRole" not in code
