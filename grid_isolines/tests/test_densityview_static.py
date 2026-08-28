# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Статические сторожа окна «Карта плотности». Окно живёт в QGIS, и в
# контейнере его не запустить, поэтому проверяется устройство кода: обе
# правки отзывчивости держатся на паре строк, которые легко потерять при
# переработке окна.
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, "..", "densityview.py"),
              encoding="utf-8").read()


def test_busy_tick_is_not_lost():
    """Тик, пришедший во время пересчёта, перезапускает предпросмотр.

    Иначе последний поворот ручки теряется и картинка отстаёт: пересчёт
    занят, return, а нового тика не будет.
    """
    assert "self._again = True" in SRC
    body = SRC[SRC.index("def _preview"):]
    body = body[:body.index("def _compute_preview")]
    assert "_again" in body and "_timer.start()" in body


def test_aux_raster_is_cached_per_grid():
    """Вспомогательный растр не перечитывается целиком на каждый тик.

    ReadAsArray всего файла под ползунком - это диск каждые 180 мс; ключ
    кэша включает слой и сетку, поэтому смена того или другого честно
    перечитывает.
    """
    body = SRC[SRC.index("def _aux_grid"):]
    body = body[:body.index("def ", 10)]
    assert "_aux_cache" in body
    for part in ("lid", "gs.xmin", "gs.ymin", "gs.cell", "gs.nx", "gs.ny"):
        assert part in body, part


def test_preview_walks_the_cache_not_the_provider():
    """Предпросмотр идёт по сырью из кэша, а не по провайдеру слоя.

    Каждый тик читал слой заново: на больших shapefile диск съедал
    больше, чем счёт. Прямой обход getFeatures остаётся только в двух
    местах - при построении кэша и в хвосте сверх предохранителя.
    """
    body = SRC[SRC.index("def _compute_preview"):]
    body = body[:body.index("def _aux_grid")]
    assert "getFeatures" not in body
    assert "_layer_raw" in body
    assert SRC.count("getFeatures") == 2


def test_cache_is_dropped_on_layer_edits():
    """Правка слоя сбрасывает кэш: плотность не должна отставать от данных."""
    for sig in ("dataChanged", "layerModified", "editingStopped"):
        assert sig in SRC, sig
    body = SRC[SRC.index("def _layer_changed"):]
    body = body[:body.index("def ", 10)]
    assert "_drop_cache" in body


def test_cache_has_a_memory_guard():
    """Предохранитель по вершинам: огромный слой работает как раньше."""
    assert "_CACHE_VERTS_MAX" in SRC
    body = SRC[SRC.index("def _layer_raw"):]
    body = body[:body.index("def _raw_rest")]
    assert "_CACHE_VERTS_MAX" in body and "self._feat_cache = None" in body


def test_field_reading_twins_stay_together():
    """У _num есть двойник _num_a по сохранённым значениям.

    Оба обязаны одинаково обходиться с пустыми значениями: вернуть
    умолчание на None и на нечисловом значении.
    """
    for name in ("def _num(", "def _num_a("):
        assert name in SRC, name
    for fn in ("_num(", "_num_a("):
        body = SRC[SRC.index("def " + fn):]
        body = body[:body.index("\n\n")]
        assert "return default" in body and "np.isfinite" in body
