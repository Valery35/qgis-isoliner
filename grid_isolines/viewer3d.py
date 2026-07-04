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
from .mesh3d import grid_to_mesh_arrays, sample_bilinear

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


def _read_raster(source):
    """Читает первый канал растра как массив с NaN и geotransform."""
    import numpy as np
    from osgeo import gdal
    ds = gdal.Open(source)
    if ds is None:
        return None, None
    b = ds.GetRasterBand(1)
    arr = b.ReadAsArray().astype(float)
    nd = b.GetNoDataValue()
    if nd is not None:
        arr = np.where(arr == nd, np.nan, arr)
    gt = ds.GetGeoTransform()
    ds = None
    return arr, gt


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
    try:  # QGIS 3.30+/4: Qgis.GeometryType.Point
        from qgis.core import Qgis
        _POINT_GT = Qgis.GeometryType.Point
    except Exception:  # старые QGIS 3
        from qgis.core import QgsWkbTypes
        _POINT_GT = QgsWkbTypes.PointGeometry
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import (
        QDialog, QHBoxLayout, QVBoxLayout, QListWidget, QListWidgetItem,
        QDoubleSpinBox, QPushButton, QLabel, QFormLayout, QSplitter, QWidget,
        QComboBox)

    # Qt5/Qt6: enum'ы либо плоские, либо в scoped-подклассах
    _CHECKED = getattr(getattr(Qt, "CheckState", Qt), "Checked")
    _UNCHECKED = getattr(getattr(Qt, "CheckState", Qt), "Unchecked")
    _USER_ROLE = getattr(getattr(Qt, "ItemDataRole", Qt), "UserRole")
    _CHECKABLE = getattr(getattr(Qt, "ItemFlag", Qt), "ItemIsUserCheckable")

    class ViewerDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(tr("Isoliner - 3D-просмотр поверхностей (бета)"))
            self.resize(1000, 640)
            try:  # Qt6: scoped enum, Qt5: плоский; без кнопок тоже не беда
                flag = getattr(getattr(Qt, "WindowType", Qt),
                               "WindowMinMaxButtonsHint")
                self.setWindowFlags(self.windowFlags() | flag)
            except Exception:
                pass

            self.layer_list = QListWidget()
            self.vex = QDoubleSpinBox()
            self.vex.setRange(0.01, 10000.0)
            self.vex.setValue(5.0)
            self.vex.setDecimals(2)
            self.spacing = QDoubleSpinBox()
            self.spacing.setRange(0.0, 1e9)
            self.spacing.setValue(0.0)
            self.spacing.setDecimals(1)
            self.btn = QPushButton(tr("Обновить сцену"))
            self.btn.clicked.connect(self.rebuild)
            self.info = QLabel("")
            self.info.setWordWrap(True)

            self.wells_combo = QComboBox()
            self.wells_combo.currentIndexChanged.connect(self._wells_changed)
            self.wells_fields = QListWidget()
            self.wells_fields.setMaximumHeight(110)
            self.attr_combo = QComboBox()
            self.attr_target = QComboBox()

            form = QFormLayout()
            form.addRow(tr("Вертикальное преувеличение"), self.vex)
            form.addRow(tr("Разнос по Z (шаг вниз)"), self.spacing)
            form.addRow(tr("Окраска атрибутом (растр)"), self.attr_combo)
            form.addRow(tr("Применить окраску к"), self.attr_target)
            form.addRow(tr("Скважины (точки)"), self.wells_combo)
            form.addRow(tr("Поля отметок"), self.wells_fields)

            left = QWidget()
            lv = QVBoxLayout(left)
            lv.addWidget(QLabel(tr("Поверхности (растры проекта):")))
            lv.addWidget(self.layer_list, 1)
            lv.addLayout(form)
            lv.addWidget(self.btn)
            lv.addWidget(self.info)

            self.view = gl.GLViewWidget()
            self.view.setBackgroundColor((250, 250, 248))

            split = QSplitter()
            split.addWidget(left)
            split.addWidget(self.view)
            split.setStretchFactor(1, 1)
            root = QHBoxLayout(self)
            root.addWidget(split)
            self._items = []

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
            prev_attr = self.attr_combo.currentData()
            prev_tgt = self.attr_target.currentData()
            self.attr_combo.blockSignals(True)
            self.attr_target.blockSignals(True)
            self.attr_combo.clear()
            self.attr_target.clear()
            self.attr_combo.addItem(tr("(нет)"), None)
            self.attr_target.addItem(tr("(все поверхности)"), None)
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsRasterLayer):
                    self.attr_combo.addItem(lyr.name(), lyr.id())
                    self.attr_target.addItem(lyr.name(), lyr.id())
            ia = self.attr_combo.findData(prev_attr)
            self.attr_combo.setCurrentIndex(max(ia, 0))
            it_ = self.attr_target.findData(prev_tgt)
            self.attr_target.setCurrentIndex(max(it_, 0))
            self.attr_combo.blockSignals(False)
            self.attr_target.blockSignals(False)
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

        def _wells_changed(self):
            """Заполняет список числовых полей отметок, h* отмечены сразу."""
            import re
            self.wells_fields.clear()
            lyr = QgsProject.instance().mapLayer(
                self.wells_combo.currentData() or "")
            if lyr is None:
                return
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
                    out.append((p.x(), p.y(), zs))
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

        def rebuild(self):
            for m in self._items:
                self.view.removeItem(m)
            self._items = []
            layers = self._checked_layers()
            if not layers:
                self.info.setText(tr("Отметьте хотя бы один растр."))
                return
            vex = float(self.vex.value())
            spacing = float(self.spacing.value())
            meshes, skipped = [], []
            for k, lyr in enumerate(layers):
                arr, gt = _read_raster(lyr.source())
                if arr is None:
                    skipped.append(lyr.name())
                    continue
                try:
                    verts, faces = grid_to_mesh_arrays(
                        arr, gt, zscale=1.0, zoffset=-spacing * k,
                        step=_auto_step(arr))
                except ValueError:
                    skipped.append(lyr.name())
                    continue
                meshes.append((verts, faces, PALETTE[k % len(PALETTE)],
                               lyr.id()))
            if not meshes:
                self.info.setText(tr("Гриды не открылись."))
                return
            wells = self._well_points()
            allv = np.vstack([m[0] for m in meshes])
            xs = [allv[:, 0].min(), allv[:, 0].max()]
            ys = [allv[:, 1].min(), allv[:, 1].max()]
            zs_ = [allv[:, 2].min(), allv[:, 2].max()]
            for x, y, zw in wells:
                xs += [x]; ys += [y]; zs_ += [min(zw), max(zw)]
            cx = 0.5 * (min(xs) + max(xs))
            cy = 0.5 * (min(ys) + max(ys))
            cz = 0.5 * (min(zs_) + max(zs_))
            attr = None
            target_id = self.attr_target.currentData()
            alayer = QgsProject.instance().mapLayer(
                self.attr_combo.currentData() or "")
            if alayer is not None:
                aarr, agt = _read_raster(alayer.source())
                if aarr is not None:
                    vals = {}
                    for m in meshes:
                        if target_id is not None and m[3] != target_id:
                            continue
                        vals[m[3]] = sample_bilinear(
                            aarr, agt, m[0][:, 0], m[0][:, 1])
                    fins = [v[np.isfinite(v)] for v in vals.values()
                            if np.isfinite(v).any()]
                    if fins:
                        fin = np.concatenate(fins)
                        vmin, vmax = float(fin.min()), float(fin.max())
                        rng = (vmax - vmin) or 1.0
                        attr = (vals, vmin, vmax, rng)

            for k, (verts, faces, color, lid) in enumerate(meshes):
                v = verts.copy()
                v[:, 0] -= cx
                v[:, 1] -= cy
                v[:, 2] = (v[:, 2] - cz) * vex
                md = gl.MeshData(vertexes=v.astype('float32'), faces=faces)
                if attr is not None and lid in attr[0]:
                    vals, vmin, vmax, rng = attr
                    md.setVertexColors(
                        colormap((vals[lid] - vmin) / rng).astype('float32'))
                    item = gl.GLMeshItem(meshdata=md, smooth=True,
                                         glOptions='opaque')
                else:
                    item = gl.GLMeshItem(meshdata=md, smooth=True,
                                         shader='shaded', color=color,
                                         glOptions='opaque')
                self.view.addItem(item)
                self._items.append(item)
            span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)

            if wells:
                segs, tops = [], []
                for x, y, zs in wells:
                    zlo = (min(zs) - cz) * vex
                    zhi = (max(zs) - cz) * vex
                    segs.append([x - cx, y - cy, zlo])
                    segs.append([x - cx, y - cy, zhi])
                    tops.append([x - cx, y - cy, zhi])
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

            self.view.opts['distance'] = span * 1.5
            self.view.opts['center'].setX(0)
            self.view.opts['center'].setY(0)
            self.view.opts['center'].setZ(0)
            self.view.update()
            msg = tr("Показано поверхностей: %d.") % len(meshes)
            if attr is not None:
                msg += " " + tr("Окраска: %s [%.4g … %.4g].") % (
                    alayer.name(), attr[1], attr[2])
            if wells:
                msg += " " + tr("Скважин: %d.") % len(wells)
            if skipped:
                msg += " " + tr("Пропущено: %s") % ", ".join(skipped)
            self.info.setText(msg)

    return ViewerDialog(parent)
