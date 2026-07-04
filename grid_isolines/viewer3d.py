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
from .mesh3d import grid_to_mesh_arrays, bed_to_mesh_arrays, sample_bilinear

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
        QComboBox, QCheckBox)

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

            self.plane_combo = QComboBox()
            self.wells_combo = QComboBox()
            self.wells_combo.currentIndexChanged.connect(self._wells_changed)
            self.wells_fields = QListWidget()
            self.wells_fields.setMaximumHeight(110)
            self.attr_combo = QComboBox()
            self.pband = QDoubleSpinBox()
            self.pband.setRange(0, 99)
            self.pband.setValue(3)
            self.pband.setDecimals(0)
            self.opacity = QDoubleSpinBox()
            self.opacity.setRange(0.0, 95.0)
            self.opacity.setValue(0.0)
            self.opacity.setDecimals(0)
            self.zband = QDoubleSpinBox()
            self.zband.setRange(1, 99)
            self.zband.setValue(1)
            self.zband.setDecimals(0)
            self.aband = QDoubleSpinBox()
            self.aband.setRange(1, 99)
            self.aband.setValue(1)
            self.aband.setDecimals(0)

            form = QFormLayout()
            form.addRow(tr("Вертикальное преувеличение"), self.vex)
            form.addRow(tr("Разнос по Z (шаг вниз)"), self.spacing)
            form.addRow(tr("Прозрачность поверхностей (процентов)"),
                        self.opacity)
            self.beds_chk = QCheckBox(
                tr("Тела пластов (канал 1 кровля, канал 2 подошва)"))
            form.addRow(self.beds_chk)
            form.addRow(tr("Канал параметра пласта (0 - палитра)"), self.pband)
            form.addRow(tr("Канал высот (Z)"), self.zband)
            form.addRow(tr("Окраска поверхностей атрибутом (растр)"),
                        self.attr_combo)
            form.addRow(tr("Канал атрибута"), self.aband)
            form.addRow(tr("Плоскость разреза (линия)"), self.plane_combo)
            form.addRow(tr("Скважины (точки)"), self.wells_combo)
            form.addRow(tr("Поля отметок"), self.wells_fields)

            left = QWidget()
            lv = QVBoxLayout(left)
            lv.addWidget(QLabel(tr("Поверхности (растры проекта):")))
            lv.addWidget(self.layer_list, 1)
            lv.addLayout(form)
            lv.addLayout(views)
            lv.addWidget(self.btn)
            lv.addWidget(self.legend_pix)
            lv.addWidget(self.legend_txt)
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
            prev_attr = self.attr_combo.currentData()
            self.attr_combo.blockSignals(True)
            self.attr_combo.clear()
            self.attr_combo.addItem(tr("(нет)"), None)
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsRasterLayer):
                    self.attr_combo.addItem(lyr.name(), lyr.id())
            ia = self.attr_combo.findData(prev_attr)
            self.attr_combo.setCurrentIndex(max(ia, 0))
            self.attr_combo.blockSignals(False)
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
            zb = int(self.zband.value())
            beds_mode = self.beds_chk.isChecked()
            nbeds = 0
            for k, lyr in enumerate(layers):
                as_bed = beds_mode and _band_count(lyr.source()) >= 2
                try:
                    if as_bed:
                        top, gt = _read_raster(lyr.source(), 1)
                        bot, _g = _read_raster(lyr.source(), 2)
                        if top is None or bot is None:
                            raise ValueError
                        verts, faces = bed_to_mesh_arrays(
                            top, bot, gt, zscale=1.0,
                            zoffset=-spacing * k, step=_auto_step(top))
                        nbeds += 1
                    else:
                        arr, gt = _read_raster(lyr.source(), zb)
                        if arr is None:
                            raise ValueError
                        verts, faces = grid_to_mesh_arrays(
                            arr, gt, zscale=1.0, zoffset=-spacing * k,
                            step=_auto_step(arr))
                except ValueError:
                    skipped.append(lyr.name())
                    continue
                if not len(faces):
                    skipped.append(lyr.name())
                    continue
                meshes.append((verts, faces, PALETTE[k % len(PALETTE)],
                               lyr.id(), as_bed, lyr.source()))
            if not meshes:
                self.info.setText(tr("Гриды не открылись."))
                return
            wells = self._well_points()
            planes = self._plane_lines()
            allv = np.vstack([m[0] for m in meshes])
            xs = [allv[:, 0].min(), allv[:, 0].max()]
            ys = [allv[:, 1].min(), allv[:, 1].max()]
            zs_ = [allv[:, 2].min(), allv[:, 2].max()]
            for x, y, zw in wells:
                xs += [x]; ys += [y]; zs_ += [min(zw), max(zw)]
            for pts, zlo, zhi in planes:
                xs += [p[0] for p in pts]; ys += [p[1] for p in pts]
                if zlo is not None:
                    zs_ += [zlo, zhi]
            cx = 0.5 * (min(xs) + max(xs))
            cy = 0.5 * (min(ys) + max(ys))
            cz = 0.5 * (min(zs_) + max(zs_))
            # окраска: тело пласта - собственным каналом параметра;
            # одноканальная поверхность - внешним атрибутным растром
            vals = {}
            pband = int(self.pband.value())
            alayer = QgsProject.instance().mapLayer(
                self.attr_combo.currentData() or "")
            aarr = agt = None
            if alayer is not None:
                aarr, agt = _read_raster(alayer.source(),
                                         int(self.aband.value()))
            for m in meshes:
                verts_m, lid, as_bed, src = m[0], m[3], m[4], m[5]
                if as_bed and pband > 0:
                    parr, pgt = _read_raster(src, pband)
                    if parr is not None:
                        vals[lid] = sample_bilinear(
                            parr, pgt, verts_m[:, 0], verts_m[:, 1])
                elif not as_bed and aarr is not None:
                    vals[lid] = sample_bilinear(
                        aarr, agt, verts_m[:, 0], verts_m[:, 1])
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
            for k, (verts, faces, color, lid, as_bed, src) in enumerate(meshes):
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

            if wells:
                mast = span * 0.02  # мачта над устьем: скважина видна всегда,
                # даже когда штанга целиком внутри непрозрачного тела
                segs, tops = [], []
                for x, y, zs in wells:
                    zlo = (min(zs) - cz) * vex
                    zhi = (max(zs) - cz) * vex + mast
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
            if nbeds:
                msg += " " + tr("Тел пластов: %d.") % nbeds
            if planes:
                msg += " " + tr("Плоскостей разреза: %d.") % len(planes)
            if attr is not None:
                parts = []
                if nbeds and pband > 0:
                    parts.append(tr("канал %d пласта") % pband)
                if alayer is not None and aarr is not None:
                    parts.append(alayer.name())
                msg += " " + tr("Окраска: %s [%.4g … %.4g].") % (
                    ", ".join(parts), attr[1], attr[2])
            if wells:
                msg += " " + tr("Скважин: %d.") % len(wells)
            if skipped:
                msg += " " + tr("Пропущено: %s") % ", ".join(skipped)
            self.info.setText(msg)

    return ViewerDialog(parent)
