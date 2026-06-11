# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), - либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии - в файле LICENSE (на английском, юридически значим).
"""
Кастомный виджет для «Размер ячейки»: рядом показывает рассчитанный
размер грида (nx × ny) по охвату слоя/заданному охвату и текущему шагу.

Полностью защищён try/except: при любой несовместимости API виджет
откатывается к обычному числовому полю, диалог не ломается.
"""
import math

from qgis.PyQt.QtWidgets import QWidget, QHBoxLayout, QLabel

try:
    from qgis.gui import QgsDoubleSpinBox as _Spin
except Exception:  # на всякий случай
    from qgis.PyQt.QtWidgets import QDoubleSpinBox as _Spin

try:
    from processing.gui.wrappers import WidgetWrapper as _BASE
    WRAPPER_AVAILABLE = True
except Exception:               # QGIS 4: старый API виджетов удалён
    _BASE = object
    WRAPPER_AVAILABLE = False


class CellSizeWrapper(_BASE):
    """Старый-стиль WidgetWrapper (совместим с QGIS 3.x script-алгоритмами)."""

    # --- построение виджета ---------------------------------------------
    def createWidget(self):
        try:
            self._container = QWidget()
            lay = QHBoxLayout(self._container)
            lay.setContentsMargins(0, 0, 0, 0)
            self._spin = _Spin()
            try:
                self._spin.setMinimum(0.0)
                self._spin.setMaximum(1e9)
                self._spin.setDecimals(5)
            except Exception:
                pass
            self._spin.setToolTip("0 = авто: min(охват)/50")
            self._label = QLabel("грид: -")
            self._label.setStyleSheet("color:#888;")
            lay.addWidget(self._spin, 1)
            lay.addWidget(self._label, 0)
            try:
                self._spin.valueChanged.connect(self._recompute)
            except Exception:
                pass
            return self._container
        except Exception:
            self._spin = _Spin()
            return self._spin

    # --- значение параметра ---------------------------------------------
    def setValue(self, value):
        try:
            self._spin.setValue(float(value) if value not in (None, "") else 0.0)
        except Exception:
            pass

    def value(self):
        try:
            return self._spin.value()
        except Exception:
            return 0.0

    # --- связь с другими параметрами ------------------------------------
    def postInitialize(self, wrappers):
        self._wrappers = {}
        for w in wrappers:
            name = None
            try:
                name = w.parameterDefinition().name()
            except Exception:
                try:
                    name = w.param.name()
                except Exception:
                    name = None
            if name:
                self._wrappers[name] = w
        ext = self._wrappers.get("EXTENT")
        if ext is not None:
            try:
                ext.widgetValueHasChanged.connect(lambda *a: self._recompute())
            except Exception:
                pass
        inp = self._wrappers.get("INPUT")
        if inp is not None:
            try:
                inp.widgetValueHasChanged.connect(lambda *a: self._recompute())
            except Exception:
                pass
        self._recompute()

    # --- расчёт размера грида -------------------------------------------
    def _wrapper_value(self, w):
        if w is None:
            return None
        for getter in ("parameterValue", "value"):
            try:
                return getattr(w, getter)()
            except Exception:
                continue
        return None

    def _to_rect(self, v):
        from qgis.core import QgsRectangle
        try:
            if v is None:
                return None
            if hasattr(v, "xMinimum"):
                return v
            if isinstance(v, str) and v:
                head = v.split("[")[0].strip()
                parts = [p for p in head.replace(";", ",").split(",") if p.strip()]
                if len(parts) >= 4:
                    xmin, xmax, ymin, ymax = (float(parts[0]), float(parts[1]),
                                              float(parts[2]), float(parts[3]))
                    return QgsRectangle(xmin, ymin, xmax, ymax)
        except Exception:
            pass
        return None

    def _to_layer(self, v):
        from qgis.core import QgsProject
        try:
            if v is None:
                return None
            if hasattr(v, "extent") and hasattr(v, "isValid"):
                return v
            if isinstance(v, str) and v:
                lyr = QgsProject.instance().mapLayer(v)
                if lyr is not None:
                    return lyr
                found = QgsProject.instance().mapLayersByName(v)
                if found:
                    return found[0]
        except Exception:
            pass
        return None

    def _get_extent(self):
        if not hasattr(self, "_wrappers"):
            return None
        rect = self._to_rect(self._wrapper_value(self._wrappers.get("EXTENT")))
        if rect is not None and not rect.isEmpty():
            return rect
        lyr = self._to_layer(self._wrapper_value(self._wrappers.get("INPUT")))
        if lyr is not None:
            try:
                ext = lyr.extent()
                if ext is not None and not ext.isEmpty():
                    return ext
            except Exception:
                return None
        return None

    def _recompute(self, *args):
        if not hasattr(self, "_label"):
            return
        try:
            cell = self._spin.value()
            ext = self._get_extent()
            if ext is None or ext.isEmpty():
                self._label.setText("грид: -")
                return
            w, h = ext.width(), ext.height()
            c = cell if cell > 0 else (round((min(w, h) or 1.0) / 50.0, 5) or 1.0)
            nx = max(int(math.ceil(w / c)), 1)
            ny = max(int(math.ceil(h / c)), 1)
            suffix = " (авто)" if cell <= 0 else ""
            self._label.setText("грид: %d × %d%s" % (nx, ny, suffix))
        except Exception:
            try:
                self._label.setText("грид: -")
            except Exception:
                pass
