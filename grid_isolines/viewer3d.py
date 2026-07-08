# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Собственный 3D-просмотр поверхностей (бета).

Окно на pyqtgraph.opengl: выбранные растры проекта рисуются треугольными
мешами, каждый горизонт своим цветом, с вертикальным преувеличением и
разносом по Z. Не зависит от штатного 3D-вида QGIS (Qt3D).

Qt и pyqtgraph импортируются лениво: модуль импортируется headless без
ошибок (tests/test_viewer3d.py). Если pyqtgraph/PyOpenGL не установлены,
пользователь получает окно с инструкцией по установке.
"""
import os

from .i18n import tr
from .mesh3d import grid_to_mesh_arrays, bed_to_mesh_arrays, sample_bilinear, thin_labels_xy, fraction_inside_bbox

# опорные цвета шкалы (тёмно-синий -> бирюза -> жёлтый, а-ля viridis)
_CMAP = [(0.267, 0.005, 0.329), (0.229, 0.322, 0.546),
         (0.128, 0.567, 0.551), (0.369, 0.789, 0.383),
         (0.993, 0.906, 0.144)]


def colormap(t):
    """t в [0..1] (массив) -> RGBA (N, 4). NaN -> серый."""
    import numpy as np
    t = np.asarray(t, dtype=float)
    out = np.empty(t.shape + (4,))
    bad = ~np.isfinite(t)
    tt = np.clip(np.where(bad, 0.0, t), 0.0, 1.0)
    n = len(_CMAP) - 1
    pos = tt * n
    i = np.minimum(pos.astype(int), n - 1)
    f = (pos - i)[..., None]
    a = np.array(_CMAP)
    out[..., :3] = a[i] * (1 - f) + a[i + 1] * f
    out[..., 3] = 1.0
    out[bad] = (0.6, 0.6, 0.6, 1.0)
    return out

_DIALOG = None  # держим окно живым

LIBS_DIR = os.path.join(os.path.dirname(__file__), "libs")


def is_available():
    """Быстрая проверка без импорта: есть ли pyqtgraph и PyOpenGL
    (системные или в libs/ плагина). По ней решается, показывать ли
    пункт меню - без пакетов пункта просто нет."""
    import importlib.util
    have = (importlib.util.find_spec("pyqtgraph") is not None and
            importlib.util.find_spec("OpenGL") is not None)
    if have:
        return True
    return (os.path.isdir(os.path.join(LIBS_DIR, "pyqtgraph")) and
            os.path.isdir(os.path.join(LIBS_DIR, "OpenGL")))


def _import_gl():
    """Импорт pyqtgraph.opengl: сначала системный, затем из libs/ плагина."""
    try:
        import pyqtgraph.opengl as gl
        return gl
    except Exception:
        pass
    import sys
    if os.path.isdir(LIBS_DIR) and LIBS_DIR not in sys.path:
        sys.path.insert(0, LIBS_DIR)
    import pyqtgraph.opengl as gl
    return gl


MAX_VERTS = 60000  # автопрореживание крупных гридов

PALETTE = [
    (0.85, 0.55, 0.10, 1.0),
    (0.20, 0.55, 0.85, 1.0),
    (0.30, 0.70, 0.35, 1.0),
    (0.80, 0.30, 0.30, 1.0),
    (0.60, 0.40, 0.80, 1.0),
    (0.50, 0.50, 0.30, 1.0),
    (0.20, 0.70, 0.70, 1.0),
    (0.85, 0.45, 0.65, 1.0),
]


def _auto_step(arr):
    """Шаг прореживания, чтобы вершин было не больше MAX_VERTS."""
    ny, nx = arr.shape
    total = ny * nx
    if total <= MAX_VERTS:
        return 1
    import math
    return int(math.ceil(math.sqrt(total / float(MAX_VERTS))))


def _read_raster(source, band=1):
    """Читает канал растра как массив с NaN и geotransform."""
    import numpy as np
    from osgeo import gdal
    ds = gdal.Open(source)
    if ds is None or band > ds.RasterCount:
        return None, None
    b = ds.GetRasterBand(band)
    arr = b.ReadAsArray().astype(float)
    nd = b.GetNoDataValue()
    if nd is not None:
        arr = np.where(arr == nd, np.nan, arr)
    gt = ds.GetGeoTransform()
    ds = None
    return arr, gt


def _band_count(source):
    from osgeo import gdal
    ds = gdal.Open(source)
    if ds is None:
        return 0
    n = ds.RasterCount
    ds = None
    return n


def _band_items(source):
    """[(номер, подпись)] по описаниям каналов растра."""
    from osgeo import gdal
    ds = gdal.Open(source)
    if ds is None:
        return []
    out = []
    for i in range(1, ds.RasterCount + 1):
        d = ds.GetRasterBand(i).GetDescription()
        out.append((i, "%d - %s" % (i, d) if d else str(i)))
    ds = None
    return out


def show_viewer(iface):
    """Открывает (или поднимает) окно 3D-просмотра."""
    global _DIALOG
    parent = iface.mainWindow() if iface is not None else None
    try:
        _import_gl()
    except Exception:
        from qgis.PyQt.QtWidgets import QMessageBox
        QMessageBox.information(
            parent, tr("3D-просмотр поверхностей"),
            tr("3D-просмотр недоступен в этой установке плагина."))
        return
    if _DIALOG is None:
        _DIALOG = _build_dialog(parent)
    _DIALOG.refresh_layers()
    _DIALOG.show()
    _DIALOG.raise_()


def _build_dialog(parent):
    import numpy as np
    gl = _import_gl()
    from qgis.core import QgsProject, QgsRasterLayer, QgsVectorLayer
    try:  # QGIS 3.30+/4: Qgis.GeometryType.*
        from qgis.core import Qgis
        _POINT_GT = Qgis.GeometryType.Point
        _LINE_GT = Qgis.GeometryType.Line
    except Exception:  # старые QGIS 3
        from qgis.core import QgsWkbTypes
        _POINT_GT = QgsWkbTypes.PointGeometry
        _LINE_GT = QgsWkbTypes.LineGeometry
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import (
        QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
        QDoubleSpinBox, QPushButton, QLabel, QFormLayout, QSplitter, QWidget,
        QComboBox, QCheckBox, QLineEdit, QTabWidget, QGroupBox)

    # Qt5/Qt6: enum'ы либо плоские, либо в scoped-подклассах
    _CHECKED = getattr(getattr(Qt, "CheckState", Qt), "Checked")
    _UNCHECKED = getattr(getattr(Qt, "CheckState", Qt), "Unchecked")
    _USER_ROLE = getattr(getattr(Qt, "ItemDataRole", Qt), "UserRole")
    _CHECKABLE = getattr(getattr(Qt, "ItemFlag", Qt), "ItemIsUserCheckable")

    class _PickView(gl.GLViewWidget):
        """GLViewWidget с колбэком на клик без перетаскивания."""
        pick_cb = None

        def mousePressEvent(self, ev):
            self._press = self._evpos(ev)
            super().mousePressEvent(ev)

        def mouseReleaseEvent(self, ev):
            pos = self._evpos(ev)
            pr = getattr(self, "_press", None)
            super().mouseReleaseEvent(ev)
            if (self.pick_cb is not None and pr is not None and
                    abs(pos[0] - pr[0]) < 3 and abs(pos[1] - pr[1]) < 3):
                self.pick_cb(pos[0], pos[1])

        @staticmethod
        def _evpos(ev):
            p = ev.position() if hasattr(ev, "position") else ev.pos()
            return (float(p.x()), float(p.y()))

    class ViewerDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(tr("Isoliner - 3D-просмотр поверхностей (бета)"))
            self.resize(1060, 660)
            try:  # Qt6: scoped enum, Qt5: плоский; без кнопок тоже не беда
                flag = getattr(getattr(Qt, "WindowType", Qt),
                               "WindowMinMaxButtonsHint")
                self.setWindowFlags(self.windowFlags() | flag)
            except Exception:
                pass

            self._opts = {}          # id слоя -> персональные настройки
            self._loading_opts = False

            # --- общие настройки сцены
            self.vex = QDoubleSpinBox()
            self.vex.setRange(0.01, 10000.0)
            self.vex.setValue(5.0)
            self.vex.setDecimals(2)
            self.spacing = QDoubleSpinBox()
            self.spacing.setRange(0.0, 1e9)
            self.spacing.setValue(0.0)
            self.spacing.setDecimals(1)
            self.opacity = QDoubleSpinBox()
            self.opacity.setRange(0.0, 95.0)
            self.opacity.setValue(0.0)
            self.opacity.setDecimals(0)
            self.btn = QPushButton(tr("Обновить сцену"))
            self.btn.clicked.connect(self.rebuild)
            btn_top = QPushButton(tr("Сверху"))
            btn_top.clicked.connect(lambda: self._set_view(90, -90))
            btn_side = QPushButton(tr("Сбоку"))
            btn_side.clicked.connect(lambda: self._set_view(8, -90))
            btn_png = QPushButton(tr("Снимок PNG…"))
            btn_png.clicked.connect(self._save_png)
            views = QHBoxLayout()
            views.addWidget(btn_top)
            views.addWidget(btn_side)
            views.addWidget(btn_png)
            self.legend_pix = QLabel()
            self.legend_txt = QLabel("")
            self.legend_pix.hide()
            self.legend_txt.hide()
            self.info = QLabel("")
            self.info.setWordWrap(True)

            # --- вкладка «Слои»: список + редактор параметров слоя
            self.layer_list = QListWidget()
            self.filter_edit = QLineEdit()
            self.filter_edit.setPlaceholderText(tr("Фильтр слоёв…"))
            self.filter_edit.textChanged.connect(self._apply_filter)
            fl = QHBoxLayout()
            fl.addWidget(self.filter_edit, 1)
            b_all = QPushButton(tr("Все"))
            b_none = QPushButton(tr("Ничего"))
            b_all.clicked.connect(lambda: self._check_visible(True))
            b_none.clicked.connect(lambda: self._check_visible(False))
            fl.addWidget(b_all)
            fl.addWidget(b_none)

            self.opt_box = QGroupBox(tr("Параметры слоя"))
            self.mode_combo = QComboBox()
            for label, key in ((tr("Авто"), "auto"),
                               (tr("Поверхность"), "surface"),
                               (tr("Тело пласта"), "body")):
                self.mode_combo.addItem(label, key)
            self.zband = QComboBox()
            self.color_combo = QComboBox()
            self.color_btn = QPushButton()
            self.color_btn.setFixedSize(46, 22)
            self.color_btn.setToolTip(tr("Задать свой цвет"))
            self.color_btn.clicked.connect(self._pick_solid_color)
            self.aband = QComboBox()
            of = QFormLayout(self.opt_box)
            of.addRow(tr("Режим"), self.mode_combo)
            of.addRow(tr("Канал высот (Z)"), self.zband)
            crow = QHBoxLayout()
            crow.addWidget(self.color_combo, 1)
            crow.addWidget(self.color_btn, 0)
            of.addRow(tr("Окраска"), crow)
            of.addRow(tr("Канал атрибута"), self.aband)
            for w in (self.mode_combo, self.zband, self.aband):
                w.currentIndexChanged.connect(self._save_opts)
            self.color_combo.currentIndexChanged.connect(self._color_changed)

            tab_layers = QWidget()
            tl = QVBoxLayout(tab_layers)
            tl.addLayout(fl)
            tl.addWidget(self.layer_list, 1)
            tl.addWidget(self.opt_box)
            self.layer_list.itemChanged.connect(self._item_toggled)
            self.layer_list.currentItemChanged.connect(self._load_opts)

            # --- вкладка «Векторы»
            self.plane_combo = QComboBox()
            self.wells_combo = QComboBox()
            self.wells_combo.currentIndexChanged.connect(self._wells_changed)
            self.wells_fields = QListWidget()
            self.wells_label = QComboBox()
            vform = QFormLayout()
            vform.addRow(tr("Плоскость разреза (линия)"), self.plane_combo)
            vform.addRow(tr("Скважины (точки)"), self.wells_combo)
            vform.addRow(tr("Поле подписи скважин"), self.wells_label)
            vform.addRow(tr("Поля отметок"), self.wells_fields)
            tab_vec = QWidget()
            tv = QVBoxLayout(tab_vec)
            tv.addLayout(vform)
            tv.addStretch(1)

            tabs = QTabWidget()
            tabs.addTab(tab_layers, tr("Слои"))
            tabs.addTab(tab_vec, tr("Векторы"))

            gform = QFormLayout()
            gform.addRow(tr("Вертикальное преувеличение"), self.vex)
            gform.addRow(tr("Разнос по Z (шаг вниз)"), self.spacing)
            gform.addRow(tr("Прозрачность поверхностей (процентов)"),
                         self.opacity)

            left = QWidget()
            lv = QVBoxLayout(left)
            lv.addWidget(tabs, 1)
            lv.addLayout(gform)
            lv.addLayout(views)
            lv.addWidget(self.btn)
            lv.addWidget(self.legend_pix)
            lv.addWidget(self.legend_txt)
            lv.addWidget(self.info)

            self.view = _PickView()
            self.view.setBackgroundColor((250, 250, 248))
            self.view.pick_cb = self._pick_at
            self._pick = None
            self._pick_marker = None

            split = QSplitter()
            split.addWidget(left)
            split.addWidget(self.view)
            split.setStretchFactor(1, 1)
            root = QHBoxLayout(self)
            root.addWidget(split)
            self._items = []

        def _set_view(self, elevation, azimuth):
            self.view.opts['elevation'] = elevation
            self.view.opts['azimuth'] = azimuth
            self.view.update()

        def _save_png(self):
            from qgis.PyQt.QtWidgets import QFileDialog
            fn, _ = QFileDialog.getSaveFileName(
                self, tr("Сохранить снимок"), "isoliner_3d.png",
                "PNG (*.png)")
            if not fn:
                return
            img = self.view.grabFramebuffer()
            img.save(fn, "PNG")
            self.info.setText(tr("Снимок сохранён: %s") % os.path.basename(fn))

        def _show_legend(self, vmin, vmax):
            import numpy as np
            from qgis.PyQt.QtGui import QImage, QPixmap
            w, h = 220, 14
            rgba = (colormap(np.tile(np.linspace(0, 1, w), (h, 1)))
                    * 255).astype(np.uint8)
            self._legend_bytes = rgba.tobytes()  # держим буфер живым
            img = QImage(self._legend_bytes, w, h, w * 4,
                         QImage.Format.Format_RGBA8888
                         if hasattr(QImage, "Format")
                         and hasattr(QImage.Format, "Format_RGBA8888")
                         else QImage.Format_RGBA8888)
            self.legend_pix.setPixmap(QPixmap.fromImage(img))
            self.legend_txt.setText("%.4g … %.4g" % (vmin, vmax))
            self.legend_pix.show()
            self.legend_txt.show()

        def _hide_legend(self):
            self.legend_pix.hide()
            self.legend_txt.hide()

        def refresh_layers(self):
            """Пересобирает списки слоёв, сохраняя отметки и выбор."""
            checked = {self.layer_list.item(i).data(_USER_ROLE)
                       for i in range(self.layer_list.count())
                       if self.layer_list.item(i).checkState() == _CHECKED}
            self.layer_list.clear()
            for lyr in QgsProject.instance().mapLayers().values():
                if not isinstance(lyr, QgsRasterLayer):
                    continue
                it = QListWidgetItem(lyr.name())
                it.setData(_USER_ROLE, lyr.id())
                it.setFlags(it.flags() | _CHECKABLE)
                it.setCheckState(_CHECKED if lyr.id() in checked
                                 else _UNCHECKED)
                self.layer_list.addItem(it)
            prev_pl = self.plane_combo.currentData()
            self.plane_combo.blockSignals(True)
            self.plane_combo.clear()
            self.plane_combo.addItem(tr("(нет)"), None)
            for lyr in QgsProject.instance().mapLayers().values():
                if not isinstance(lyr, QgsVectorLayer):
                    continue
                gt_ = lyr.geometryType()
                if gt_ == _LINE_GT or getattr(gt_, "name", "") == "Line":
                    self.plane_combo.addItem(lyr.name(), lyr.id())
            ip = self.plane_combo.findData(prev_pl)
            self.plane_combo.setCurrentIndex(max(ip, 0))
            self.plane_combo.blockSignals(False)
            prev = self.wells_combo.currentData()
            self.wells_combo.blockSignals(True)
            self.wells_combo.clear()
            self.wells_combo.addItem(tr("(нет)"), None)
            for lyr in QgsProject.instance().mapLayers().values():
                if not isinstance(lyr, QgsVectorLayer):
                    continue
                gt = lyr.geometryType()
                if gt == _POINT_GT or getattr(gt, "name", "") == "Point":
                    self.wells_combo.addItem(lyr.name(), lyr.id())
            i = self.wells_combo.findData(prev)
            self.wells_combo.setCurrentIndex(max(i, 0))
            self.wells_combo.blockSignals(False)
            self._wells_changed()
            self._load_opts(self.layer_list.currentItem())

        def _wells_changed(self):
            """Заполняет список числовых полей отметок, h* отмечены сразу."""
            import re
            self.wells_fields.clear()
            self.wells_label.blockSignals(True)
            self.wells_label.clear()
            self.wells_label.addItem(tr("(нет)"), None)
            lyr = QgsProject.instance().mapLayer(
                self.wells_combo.currentData() or "")
            if lyr is None:
                self.wells_label.blockSignals(False)
                return
            guess = -1
            for f in lyr.fields():
                self.wells_label.addItem(f.name(), f.name())
                if guess < 0 and f.name().lower() in ("name", "well",
                                                      "скв", "имя"):
                    guess = self.wells_label.count() - 1
            self.wells_label.setCurrentIndex(max(guess, 0))
            self.wells_label.blockSignals(False)
            for f in lyr.fields():
                if not f.isNumeric():
                    continue
                it = QListWidgetItem(f.name())
                it.setFlags(it.flags() | _CHECKABLE)
                auto = bool(re.match(r"^[hz]\d*$", f.name(), re.I))
                it.setCheckState(_CHECKED if auto else _UNCHECKED)
                self.wells_fields.addItem(it)

        def _well_points(self):
            """Собирает (x, y, [отметки]) по отмеченным полям."""
            lyr = QgsProject.instance().mapLayer(
                self.wells_combo.currentData() or "")
            if lyr is None:
                return []
            names = [self.wells_fields.item(i).text()
                     for i in range(self.wells_fields.count())
                     if self.wells_fields.item(i).checkState() == _CHECKED]
            if not names:
                return []
            lab = self.wells_label.currentData()
            out = []
            for ft in lyr.getFeatures():
                g = ft.geometry()
                if g is None or g.isEmpty():
                    continue
                p = g.asPoint()
                zs = []
                for nm in names:
                    try:
                        v = float(ft[nm])
                    except (TypeError, ValueError):
                        continue
                    if v == v:  # не NaN
                        zs.append(v)
                if zs:
                    txt = ""
                    if lab:
                        val = ft[lab]
                        txt = "" if val is None else str(val)
                    out.append((p.x(), p.y(), zs, txt))
            return out

        def _checked_layers(self):
            proj = QgsProject.instance()
            out = []
            for i in range(self.layer_list.count()):
                it = self.layer_list.item(i)
                if it.checkState() != _CHECKED:
                    continue
                lyr = proj.mapLayer(it.data(_USER_ROLE))
                if lyr is not None:
                    out.append(lyr)
            return out

        def _apply_filter(self, text):
            low = (text or "").lower()
            for i in range(self.layer_list.count()):
                it = self.layer_list.item(i)
                it.setHidden(bool(low) and low not in it.text().lower())

        def _check_visible(self, state):
            self.layer_list.blockSignals(True)
            for i in range(self.layer_list.count()):
                it = self.layer_list.item(i)
                if not it.isHidden():
                    it.setCheckState(_CHECKED if state else _UNCHECKED)
            self.layer_list.blockSignals(False)

        @staticmethod
        def _fill_band_combo(combo, items, keep, zero_label=None):
            combo.blockSignals(True)
            combo.clear()
            if zero_label:
                combo.addItem(zero_label, 0)
            for num, label in items:
                combo.addItem(label, num)
            i = combo.findData(keep)
            combo.setCurrentIndex(i if i >= 0 else 0)
            combo.blockSignals(False)

        def _combo_band(self, combo, default):
            v = combo.currentData()
            return int(v) if v is not None else default

        def _default_opts(self, source):
            n = _band_count(source)
            return dict(solid=None, mode="auto", zband=1,
                        cband=3 if n >= 3 else 0,
                        attr_id=None, aband=1)

        def _item_toggled(self, *_a):
            pass  # отметки читаются при перестройке сцены

        def _load_opts(self, item, *_a):
            """Показывает настройки выбранного в списке слоя."""
            self._loading_opts = True
            try:
                if item is None:
                    self.opt_box.setEnabled(False)
                    return
                lyr = QgsProject.instance().mapLayer(item.data(_USER_ROLE))
                if lyr is None:
                    self.opt_box.setEnabled(False)
                    return
                self.opt_box.setEnabled(True)
                self.opt_box.setTitle(tr("Параметры слоя") + ": " + lyr.name())
                o = self._opts.setdefault(lyr.id(),
                                          self._default_opts(lyr.source()))
                i = self.mode_combo.findData(o["mode"])
                self.mode_combo.setCurrentIndex(max(i, 0))
                items = _band_items(lyr.source()) or [(1, "1")]
                self._fill_band_combo(self.zband, items, o["zband"])
                cc = self.color_combo
                cc.blockSignals(True)
                cc.clear()
                cc.addItem(tr("Палитра"), ("palette", None))
                cc.addItem(tr("Свой цвет"), ("solid", None))
                for num, label in items:
                    cc.addItem(label, ("band", num))
                proj = QgsProject.instance()
                first_r = True
                for rl in proj.mapLayers().values():
                    if not isinstance(rl, QgsRasterLayer) or \
                            rl.id() == lyr.id():
                        continue
                    if first_r:
                        cc.insertSeparator(cc.count())
                        first_r = False
                    cc.addItem(rl.name(), ("raster", rl.id()))
                if o.get("attr_id"):
                    want = ("raster", o["attr_id"])
                elif o.get("cband"):
                    want = ("band", o["cband"])
                elif o.get("solid"):
                    want = ("solid", None)
                else:
                    want = ("palette", None)
                i = cc.findData(want)
                cc.setCurrentIndex(i if i >= 0 else 0)
                cc.blockSignals(False)
                self._sync_aband(o.get("aband", 1))
                self._sync_swatch()
            finally:
                self._loading_opts = False

        def _sync_swatch(self):
            """Плашка показывает свой цвет слоя и активна в режиме
            «Свой цвет»."""
            item = self.layer_list.currentItem()
            o = self._opts.get(item.data(_USER_ROLE), {}) if item else {}
            d = self.color_combo.currentData() or ("palette", None)
            solid = d[0] == "solid"
            self.color_btn.setEnabled(solid)
            col = o.get("solid") or "#e08214"
            self.color_btn.setStyleSheet(
                "background-color: %s; border: 1px solid #888;" %
                (col if solid else "#d0d0d0"))

        def _pick_solid_color(self):
            from qgis.PyQt.QtWidgets import QColorDialog
            from qgis.PyQt.QtGui import QColor
            item = self.layer_list.currentItem()
            if item is None:
                return
            lid = item.data(_USER_ROLE)
            o = self._opts.get(lid)
            if o is None:
                lyr = QgsProject.instance().mapLayer(lid)
                if lyr is None:
                    return
                o = self._default_opts(lyr.source())
                self._opts[lid] = o
            c = QColorDialog.getColor(QColor(o.get("solid") or "#e08214"),
                                      self, tr("Свой цвет слоя"))
            if c.isValid():
                o["solid"] = c.name()
                self._sync_swatch()

        def _sync_aband(self, keep):
            """Канал атрибута активен только при окраске внешним растром."""
            d = self.color_combo.currentData()
            if d and d[0] == "raster":
                lyr = QgsProject.instance().mapLayer(d[1] or "")
                items = _band_items(lyr.source()) if lyr is not None \
                    else [(1, "1")]
                self._fill_band_combo(self.aband, items or [(1, "1")], keep)
                self.aband.setEnabled(True)
            else:
                self.aband.blockSignals(True)
                self.aband.clear()
                self.aband.blockSignals(False)
                self.aband.setEnabled(False)

        def _color_changed(self, *_a):
            if self._loading_opts:
                return
            self._sync_aband(1)
            self._save_opts()

        def _save_opts(self, *_a):
            if self._loading_opts:
                return
            item = self.layer_list.currentItem()
            if item is None:
                return
            lid = item.data(_USER_ROLE)
            d = self.color_combo.currentData() or ("palette", None)
            prev = self._opts.get(lid, {})
            self._opts[lid] = dict(
                mode=self.mode_combo.currentData() or "auto",
                zband=self._combo_band(self.zband, 1),
                cband=d[1] if d[0] == "band" else 0,
                attr_id=d[1] if d[0] == "raster" else None,
                solid=(prev.get("solid") or "#e08214")
                    if d[0] == "solid" else None,
                aband=self._combo_band(self.aband, 1))
            self._sync_swatch()

        def _plane_lines(self):
            """Полилинии выбранного определения разреза + zmin/zmax из полей
            (None, если полей нет)."""
            lyr = QgsProject.instance().mapLayer(
                self.plane_combo.currentData() or "")
            if lyr is None:
                return []
            names = {f.name().lower() for f in lyr.fields()}
            has_z = "zmin" in names and "zmax" in names
            out = []
            for ft in lyr.getFeatures():
                g = ft.geometry()
                if g is None or g.isEmpty():
                    continue
                try:  # QGIS 4: на одиночной LineString бросает TypeError
                    polys = g.asMultiPolyline()
                except Exception:
                    polys = []
                if not polys:
                    try:
                        pl = g.asPolyline()
                    except Exception:
                        pl = []
                    polys = [pl] if pl else []
                zlo = zhi = None
                if has_z:
                    try:
                        zlo, zhi = float(ft["zmin"]), float(ft["zmax"])
                    except (TypeError, ValueError, KeyError):
                        zlo = zhi = None
                for pl in polys:
                    if len(pl) >= 2:
                        out.append(([(p.x(), p.y()) for p in pl], zlo, zhi))
            return out

        def _pick_at(self, px, py):
            """Клик по сцене: луч, пересечение с рельефом, каналы в точке."""
            import numpy as np
            pk = self._pick
            if not pk or not pk["layers"]:
                return
            from qgis.PyQt.QtGui import QVector3D
            w = max(self.view.width(), 1)
            h = max(self.view.height(), 1)
            m = self.view.projectionMatrix() * self.view.viewMatrix()
            inv, ok = m.inverted()
            if not ok:
                return
            xn = 2.0 * px / w - 1.0
            yn = 1.0 - 2.0 * py / h
            p0 = inv.map(QVector3D(xn, yn, -1.0))
            p1 = inv.map(QVector3D(xn, yn, 1.0))
            a = np.array([p0.x(), p0.y(), p0.z()], float)
            d = np.array([p1.x(), p1.y(), p1.z()], float) - a
            ts = np.linspace(0.0, 1.0, 512)
            pts = a[None, :] + ts[:, None] * d[None, :]
            cx, cy, cz, vex = pk["cx"], pk["cy"], pk["cz"], pk["vex"]
            X = pts[:, 0] + cx
            Y = pts[:, 1] + cy
            best = None  # (t, layer, X, Y, z_scene)
            for L in pk["layers"]:
                arr, gt = _read_raster(L["source"], L["zband"])
                if arr is None:
                    continue
                zs = sample_bilinear(arr, gt, X, Y)
                surf = (zs + L["zoff"] - cz) * vex
                diff = pts[:, 2] - surf
                okm = np.isfinite(diff)
                sgn = np.where(okm, np.sign(diff), 0.0)
                cross = np.where((sgn[:-1] * sgn[1:] < 0) &
                                 okm[:-1] & okm[1:])[0]
                if not len(cross):
                    continue
                i = int(cross[0])
                f = abs(diff[i]) / (abs(diff[i]) + abs(diff[i + 1]) + 1e-12)
                tt = ts[i] + (ts[i + 1] - ts[i]) * f
                if best is None or tt < best[0]:
                    xh = X[i] + (X[i + 1] - X[i]) * f
                    yh = Y[i] + (Y[i + 1] - Y[i]) * f
                    zh = pts[i, 2] + (pts[i + 1, 2] - pts[i, 2]) * f
                    best = (tt, L, xh, yh, zh)
            if best is None:
                return
            _t, L, xh, yh, zh = best
            from osgeo import gdal
            ds = gdal.Open(L["source"])
            vals = []
            if ds is not None:
                j = int((xh - ds.GetGeoTransform()[0]) /
                        ds.GetGeoTransform()[1])
                i = int((yh - ds.GetGeoTransform()[3]) /
                        ds.GetGeoTransform()[5])
                if 0 <= i < ds.RasterYSize and 0 <= j < ds.RasterXSize:
                    for b in range(1, ds.RasterCount + 1):
                        bd = ds.GetRasterBand(b)
                        v = bd.ReadAsArray(j, i, 1, 1)
                        v = float(v[0, 0]) if v is not None else float("nan")
                        nd = bd.GetNoDataValue()
                        if nd is not None and v == nd:
                            v = float("nan")
                        nm = bd.GetDescription() or str(b)
                        vals.append((nm, v))
                ds = None
            parts = ["%s=%.4g" % (nm, v) for nm, v in vals
                     if v == v]
            if len(vals) >= 2 and vals[0][1] == vals[0][1] and \
                    vals[1][1] == vals[1][1]:
                parts.append(tr("мощность") + "=%.4g" %
                             (vals[0][1] - vals[1][1]))
            self.info.setText("%s @ (%.1f, %.1f): %s" %
                              (L["name"], xh, yh, "; ".join(parts)))
            # маркер попадания
            if self._pick_marker is not None:
                try:
                    self.view.removeItem(self._pick_marker)
                except Exception:
                    pass
            r = pk["span"] * 0.006
            sph = gl.MeshData.sphere(rows=8, cols=8, radius=r)
            mk = gl.GLMeshItem(meshdata=sph, smooth=True, shader='shaded',
                               color=(0.85, 0.15, 0.15, 1.0),
                               glOptions='opaque')
            mk.translate(xh - cx, yh - cy, zh)
            self.view.addItem(mk)
            self._pick_marker = mk

        def rebuild(self):
            for m in self._items:
                self.view.removeItem(m)
            self._items = []
            if self._pick_marker is not None:
                try:
                    self.view.removeItem(self._pick_marker)
                except Exception:
                    pass
                self._pick_marker = None
            layers = self._checked_layers()
            if not layers:
                self.info.setText(tr("Отметьте хотя бы один растр."))
                return
            vex = float(self.vex.value())
            spacing = float(self.spacing.value())
            meshes, skipped = [], []
            nbeds = 0
            for k, lyr in enumerate(layers):
                o = self._opts.get(lyr.id()) or \
                    self._default_opts(lyr.source())
                mode = o.get("mode", "auto")
                as_bed = (mode == "body" or
                          (mode == "auto" and
                           _band_count(lyr.source()) >= 2))
                try:
                    if as_bed:
                        top, gt = _read_raster(lyr.source(), 1)
                        bot, _g = _read_raster(lyr.source(), 2)
                        if top is None or bot is None:
                            raise ValueError
                        verts, faces = bed_to_mesh_arrays(
                            top, bot, gt, zscale=1.0,
                            zoffset=-spacing * k, step=_auto_step(top))
                        surf_arr = top
                        nbeds += 1
                    else:
                        arr, gt = _read_raster(lyr.source(),
                                               o.get("zband", 1))
                        if arr is None:
                            raise ValueError
                        verts, faces = grid_to_mesh_arrays(
                            arr, gt, zscale=1.0, zoffset=-spacing * k,
                            step=_auto_step(arr))
                        surf_arr = arr
                except ValueError:
                    skipped.append(lyr.name())
                    continue
                if not len(faces):
                    skipped.append(lyr.name())
                    continue
                base = PALETTE[k % len(PALETTE)]
                if o.get("solid"):
                    qc = o["solid"].lstrip("#")
                    base = tuple(int(qc[i:i + 2], 16) / 255.0
                                 for i in (0, 2, 4)) + (1.0,)
                meshes.append((verts, faces, base, lyr.id(), as_bed,
                               lyr.source(), o, surf_arr, gt,
                               -spacing * k))
            if not meshes:
                self.info.setText(tr("Гриды не открылись."))
                return
            wells = self._well_points()
            planes = self._plane_lines()
            allv = np.vstack([m[0] for m in meshes])
            xs = [allv[:, 0].min(), allv[:, 0].max()]
            ys = [allv[:, 1].min(), allv[:, 1].max()]
            zs_ = [allv[:, 2].min(), allv[:, 2].max()]
            for x, y, zw, _txt in wells:
                xs += [x]; ys += [y]; zs_ += [min(zw), max(zw)]
            for pts, zlo, zhi in planes:
                xs += [p[0] for p in pts]; ys += [p[1] for p in pts]
                if zlo is not None:
                    zs_ += [zlo, zhi]
            cx = 0.5 * (min(xs) + max(xs))
            cy = 0.5 * (min(ys) + max(ys))
            cz = 0.5 * (min(zs_) + max(zs_))
            # окраска пер-слойно: свой канал cband; если 0 - внешний
            # атрибутный растр слоя; иначе палитра
            vals = {}
            src_names = []
            for m in meshes:
                verts_m, lid, as_bed, src, o = (m[0], m[3], m[4],
                                                m[5], m[6])
                cband = int(o.get("cband", 0) or 0)
                if cband > 0:
                    parr, pgt = _read_raster(src, cband)
                    if parr is not None:
                        vals[lid] = sample_bilinear(
                            parr, pgt, verts_m[:, 0], verts_m[:, 1])
                        src_names.append(tr("канал %d") % cband)
                    continue
                alayer = QgsProject.instance().mapLayer(
                    o.get("attr_id") or "")
                if alayer is not None:
                    aarr, agt = _read_raster(alayer.source(),
                                             int(o.get("aband", 1)))
                    if aarr is not None:
                        vals[lid] = sample_bilinear(
                            aarr, agt, verts_m[:, 0], verts_m[:, 1])
                        src_names.append(alayer.name())
            attr = None
            fins = [v[np.isfinite(v)] for v in vals.values()
                    if np.isfinite(v).any()]
            if fins:
                fin = np.concatenate(fins)
                vmin, vmax = float(fin.min()), float(fin.max())
                rng = (vmax - vmin) or 1.0
                attr = (vals, vmin, vmax, rng)

            alpha = 1.0 - float(self.opacity.value()) / 100.0
            gopt = 'opaque' if alpha >= 0.999 else 'translucent'
            for k, (verts, faces, color, lid, as_bed, src, o,
                    _sa, _gt, _zo) in enumerate(meshes):
                v = verts.copy()
                v[:, 0] -= cx
                v[:, 1] -= cy
                v[:, 2] = (v[:, 2] - cz) * vex
                md = gl.MeshData(vertexes=v.astype('float32'), faces=faces)
                if attr is not None and lid in attr[0]:
                    vals, vmin, vmax, rng = attr
                    vc = colormap((vals[lid] - vmin) / rng)
                    vc[:, 3] = alpha
                    md.setVertexColors(vc.astype('float32'))
                    item = gl.GLMeshItem(meshdata=md, smooth=True,
                                         glOptions=gopt)
                else:
                    item = gl.GLMeshItem(meshdata=md, smooth=True,
                                         shader='shaded',
                                         color=color[:3] + (alpha,),
                                         glOptions=gopt)
                self.view.addItem(item)
                self._items.append(item)
            if attr is not None:
                self._show_legend(attr[1], attr[2])
            else:
                self._hide_legend()
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)

            self._pick_marker = None
            self._pick = dict(cx=cx, cy=cy, cz=cz, vex=vex, span=span,
                              layers=[])
            for k, (verts, faces, color, lid, as_bed, src, o,
                    _sa, _gt, _zo) in enumerate(meshes):
                zb = 1 if as_bed else int(o.get("zband", 1))
                lyr = QgsProject.instance().mapLayer(lid)
                self._pick["layers"].append(dict(
                    name=lyr.name() if lyr else "?", source=src,
                    zband=zb, zoff=-spacing * k))

            if planes:
                pad = 0.05 * (max(zs_) - min(zs_) or 1.0)
                dlo, dhi = min(zs_) - pad, max(zs_) + pad
                for pts, zlo, zhi in planes:
                    lo = zlo if zlo is not None else dlo
                    hi = zhi if zhi is not None else dhi
                    zl = (lo - cz) * vex
                    zh = (hi - cz) * vex
                    npt = len(pts)
                    pv = np.empty((2 * npt, 3), dtype='float32')
                    for i, (px, py) in enumerate(pts):
                        pv[2 * i] = (px - cx, py - cy, zl)
                        pv[2 * i + 1] = (px - cx, py - cy, zh)
                    fidx = []
                    for i in range(npt - 1):
                        a, b, c_, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
                        fidx += [[a, c_, d], [a, d, b]]
                    md = gl.MeshData(vertexes=pv,
                                     faces=np.array(fidx, dtype=np.int64))
                    itm = gl.GLMeshItem(meshdata=md, smooth=False,
                                        color=(0.30, 0.35, 0.50, 0.30),
                                        glOptions='translucent')
                    self.view.addItem(itm)
                    self._items.append(itm)
                    # контур: низ -> верх в обратном порядке -> замыкание
                    frame = np.vstack([pv[0::2], pv[1::2][::-1], pv[0:1]])
                    ln = gl.GLLinePlotItem(pos=frame, mode='line_strip',
                                           width=1.5, antialias=True,
                                           color=(0.20, 0.24, 0.38, 0.9),
                                           glOptions='translucent')
                    self.view.addItem(ln)
                    self._items.append(ln)

            # след разреза: линия пересечения секущих плоскостей с каждой
            # поверхностью - яркая нить по кровле/подошве вдоль линии
            if planes and meshes:
                for pts, _zlo, _zhi in planes:
                    P = np.asarray(pts, dtype=float)
                    if len(P) < 2:
                        continue
                    # плотная передискретизация линии по длине
                    seg = np.diff(P, axis=0)
                    seglen = np.hypot(seg[:, 0], seg[:, 1])
                    total = float(seglen.sum())
                    if total <= 0:
                        continue
                    ns = max(int(total / (span * 0.004)), 32)
                    tt = np.linspace(0.0, total, ns)
                    cum = np.concatenate([[0.0], np.cumsum(seglen)])
                    sx = np.interp(tt, cum, P[:, 0])
                    sy = np.interp(tt, cum, P[:, 1])
                    for mm in meshes:
                        sa, gt, zo = mm[7], mm[8], mm[10]
                        zc = sample_bilinear(sa, gt, sx, sy)
                        good = np.isfinite(zc)
                        if good.sum() < 2:
                            continue
                        tr = np.column_stack([
                            sx - cx, sy - cy,
                            (zc - cz) * vex + zo]).astype('float32')
                        # разрыв нитей в местах NaN: рисуем связными кусками
                        idx = np.where(good)[0]
                        splits = np.split(idx, np.where(np.diff(idx) > 1)[0]
                                          + 1)
                        for run in splits:
                            if len(run) < 2:
                                continue
                            tl = gl.GLLinePlotItem(
                                pos=tr[run], mode='line_strip', width=3.0,
                                antialias=True, color=(0.95, 0.20, 0.15, 1.0),
                                glOptions='opaque')
                            self.view.addItem(tl)
                            self._items.append(tl)

            if wells:
                mast = span * 0.02  # мачта над устьем: скважина видна всегда,
                # даже когда штанга целиком внутри непрозрачного тела
                segs, tops, labels = [], [], []
                for x, y, zs, txt in wells:
                    zlo = (min(zs) - cz) * vex
                    zhi = (max(zs) - cz) * vex + mast
                    segs.append([x - cx, y - cy, zlo])
                    segs.append([x - cx, y - cy, zhi])
                    tops.append([x - cx, y - cy, zhi])
                    labels.append((x - cx, y - cy, zhi + mast * 0.3, txt))
                seg = np.array(segs, dtype='float32')
                line = gl.GLLinePlotItem(pos=seg, mode='lines', width=2.0,
                                         color=(0.15, 0.15, 0.15, 1.0),
                                         antialias=True, glOptions='opaque')
                self.view.addItem(line)
                self._items.append(line)
                r = span * 0.004
                if len(tops) <= 500:  # шарики на устьях
                    sph = gl.MeshData.sphere(rows=8, cols=8, radius=r)
                    for t_ in tops:
                        ball = gl.GLMeshItem(meshdata=sph, smooth=True,
                                             shader='shaded',
                                             color=(0.12, 0.12, 0.12, 1.0),
                                             glOptions='opaque')
                        ball.translate(t_[0], t_[1], t_[2])
                        self.view.addItem(ball)
                        self._items.append(ball)
                else:  # много скважин - круглые спрайты
                    dots = gl.GLScatterPlotItem(
                        pos=np.array(tops, dtype='float32'),
                        size=r * 2, pxMode=False,
                        color=(0.12, 0.12, 0.12, 0.9),
                        glOptions='translucent')
                    self.view.addItem(dots)
                    self._items.append(dots)
                TextItem = getattr(gl, "GLTextItem", None)
                if TextItem is not None and len(labels) <= 500:
                    from qgis.PyQt.QtGui import QFont
                    fnt = QFont()
                    fnt.setPointSize(8)
                    # прореживание: не подписывать скважину, если рядом
                    # уже есть подписанная - тексты не налезают
                    shown = thin_labels_xy(
                        [(lx, ly) for lx, ly, _z, _t in labels],
                        min_dist=span * 0.045)
                    for keep, (lx, ly, lz, txt) in zip(shown, labels):
                        if not keep or not txt:
                            continue
                        ti = TextItem(pos=(lx, ly, lz), text=txt,
                                      color=(30, 30, 30, 255), font=fnt)
                        self.view.addItem(ti)
                        self._items.append(ti)

            self.view.opts['distance'] = span * 1.5
            self.view.opts['center'].setX(0)
            self.view.opts['center'].setY(0)
            self.view.opts['center'].setZ(0)
            self.view.update()
            msg = tr("Показано поверхностей: %d.") % len(meshes)
            if nbeds:
                msg += " " + tr("Тел пластов: %d.") % nbeds
            if planes:
                msg += " " + tr("Плоскостей разреза: %d.") % len(planes)
            if attr is not None:
                uniq = list(dict.fromkeys(src_names))
                msg += " " + tr("Окраска: %s [%.4g … %.4g].") % (
                    ", ".join(uniq), attr[1], attr[2])
            if wells:
                msg += " " + tr("Скважин: %d.") % len(wells)
            if skipped:
                msg += " " + tr("Пропущено: %s") % ", ".join(skipped)
            self.info.setText(msg)

    return ViewerDialog(parent)
