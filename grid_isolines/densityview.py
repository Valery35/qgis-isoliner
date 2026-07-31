# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Окно «Карта плотности» - живой предпросмотр инструмента 3.07.

Немодальное окно (как 3D-просмотр). Слева слой и поля, справа предпросмотр на
грубой сетке: он считается за миллисекунды, поэтому сигма и размер ячейки
меняют картинку сразу. Внизу постоянно горит инвариант массы.

Ядро расчёта общее с инструментом 3.07 (density.py), полный расчёт идёт через
тот же алгоритм Processing (isoliner:vardensity), поэтому логика не двоится.
Кнопка «Демо» создаёт учебный набор инструментом 3.08.
"""
from .i18n import tr

_DIALOG = None
PREVIEW_N = 120          # сторона грубой сетки предпросмотра
PREVIEW_FEATURES = 50000  # предел объектов в предпросмотре


def is_available():
    """Окну нужен только NumPy, который и так есть в QGIS."""
    try:
        import numpy  # noqa: F401
        return True
    except Exception:
        return False


def show_view(iface):
    """Открыть (или поднять) окно «Карта плотности»."""
    global _DIALOG
    parent = iface.mainWindow() if iface is not None else None
    if _DIALOG is None:
        _DIALOG = _build_dialog(parent, iface)
    _DIALOG.refresh_layers()
    _DIALOG.show()
    _DIALOG.raise_()


def _build_dialog(parent, iface):
    import numpy as np
    from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
    try:                                   # QGIS 3.30+/4
        from qgis.core import Qgis
        _POINT_GT = Qgis.GeometryType.Point
        _LINE_GT = Qgis.GeometryType.Line
        _POLY_GT = Qgis.GeometryType.Polygon
    except Exception:                      # старые QGIS 3
        from qgis.core import QgsWkbTypes
        _POINT_GT = QgsWkbTypes.GeometryType.PointGeometry
        _LINE_GT = QgsWkbTypes.GeometryType.LineGeometry
        _POLY_GT = QgsWkbTypes.GeometryType.PolygonGeometry
    from qgis.PyQt.QtCore import Qt, QTimer
    from qgis.PyQt.QtGui import QImage, QPixmap
    from qgis.PyQt.QtWidgets import (
        QDialog, QHBoxLayout, QVBoxLayout, QFormLayout, QComboBox, QLabel,
        QDoubleSpinBox, QPushButton, QCheckBox, QGroupBox, QWidget, QSplitter,
        QMessageBox)
    from . import density as D

    _KEEP = getattr(getattr(Qt, "AspectRatioMode", Qt), "KeepAspectRatio")
    _SMOOTH = getattr(getattr(Qt, "TransformationMode", Qt),
                      "SmoothTransformation")
    _RGBA = getattr(getattr(QImage, "Format", QImage), "Format_RGBA8888")

    class DensityDialog(QDialog):
        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(tr("Карта плотности (переменная опора)"))
            self.resize(980, 620)
            self._img = None          # держим ссылку: QImage не копирует буфер
            self._busy = False

            # --- левая колонка: параметры ---
            form = QFormLayout()
            self.cb_layer = QComboBox()
            self.cb_mass = QComboBox()
            self.cb_prec = QComboBox()
            self.cb_from = QComboBox()
            self.cb_to = QComboBox()
            self.cb_aux = QComboBox()
            form.addRow(tr("Замеры"), self.cb_layer)
            form.addRow(tr("Поле массы"), self.cb_mass)
            form.addRow(tr("Поле точности"), self.cb_prec)

            self.sp_sigma = QDoubleSpinBox()
            self.sp_sigma.setRange(0.0, 1e7)
            self.sp_sigma.setDecimals(1)
            self.sp_sigma.setSingleStep(10.0)
            self.sp_sigma.setToolTip(tr("0 - полуячейка"))
            form.addRow(tr("Сигма по умолчанию, м"), self.sp_sigma)

            self.sp_cell = QDoubleSpinBox()
            self.sp_cell.setRange(0.01, 1e7)
            self.sp_cell.setDecimals(2)
            self.sp_cell.setValue(50.0)
            self.sp_cell.setToolTip(tr("Размер ячейки итогового растра"))
            form.addRow(tr("Ячейка растра, м"), self.sp_cell)

            self.cb_edge = QComboBox()
            self.cb_edge.addItems([tr("Донормировать внутри"),
                                   tr("Потерять массу")])
            form.addRow(tr("Носитель за краем"), self.cb_edge)

            adv = QGroupBox(tr("Дополнительно"))
            fadv = QFormLayout(adv)
            fadv.addRow(tr("Поле from_m (линии)"), self.cb_from)
            fadv.addRow(tr("Поле to_m (линии)"), self.cb_to)
            fadv.addRow(tr("Вспом. растр (дазиметрия)"), self.cb_aux)

            self.ch_iso = QCheckBox(tr("Построить изолинии плотности"))
            self.ch_iso.setChecked(True)
            self.ch_sigma = QCheckBox(tr("Слой эффективной сигмы (доверие)"))
            self.ch_sigma.setChecked(True)

            self.btn_demo = QPushButton(tr("Демо"))
            self.btn_write = QPushButton(tr("Записать растр"))
            self.btn_close = QPushButton(tr("Закрыть"))
            rowb = QHBoxLayout()
            rowb.addWidget(self.btn_demo)
            rowb.addStretch(1)
            rowb.addWidget(self.btn_write)
            rowb.addWidget(self.btn_close)

            left = QWidget()
            lv = QVBoxLayout(left)
            lv.addLayout(form)
            lv.addWidget(adv)
            lv.addWidget(self.ch_iso)
            lv.addWidget(self.ch_sigma)
            lv.addStretch(1)

            # --- правая колонка: предпросмотр ---
            self.lbl_img = QLabel()
            self.lbl_img.setMinimumSize(420, 420)
            self.lbl_img.setAlignment(
                getattr(getattr(Qt, "AlignmentFlag", Qt), "AlignCenter"))
            self.lbl_img.setStyleSheet(
                "background:#202020; border:1px solid #444;")
            self.lbl_scale = QLabel("")
            right = QWidget()
            rv = QVBoxLayout(right)
            rv.addWidget(self.lbl_img, 1)
            rv.addWidget(self.lbl_scale)

            split = QSplitter()
            split.addWidget(left)
            split.addWidget(right)
            split.setStretchFactor(1, 1)

            self.lbl_inv = QLabel("")
            self.lbl_inv.setStyleSheet("font-weight:bold;")

            root = QVBoxLayout(self)
            root.addWidget(split, 1)
            root.addWidget(self.lbl_inv)
            root.addLayout(rowb)

            # --- реакции: перерисовка с задержкой ---
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(180)
            self._timer.timeout.connect(self._preview)
            self.cb_layer.currentIndexChanged.connect(self._layer_changed)
            for w in (self.cb_mass, self.cb_prec, self.cb_from, self.cb_to,
                      self.cb_aux, self.cb_edge):
                w.currentIndexChanged.connect(self._schedule)
            for w in (self.sp_sigma, self.sp_cell):
                w.valueChanged.connect(self._schedule)
            self.btn_write.clicked.connect(self._write)
            self.btn_demo.clicked.connect(self._demo)
            self.btn_close.clicked.connect(self.hide)

        # --- наполнение списков -------------------------------------------
        def refresh_layers(self):
            cur = self.cb_layer.currentData()
            self.cb_layer.blockSignals(True)
            self.cb_layer.clear()
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsVectorLayer) and lyr.isValid():
                    self.cb_layer.addItem(lyr.name(), lyr.id())
            i = self.cb_layer.findData(cur)
            if i >= 0:
                self.cb_layer.setCurrentIndex(i)
            self.cb_layer.blockSignals(False)
            self.cb_aux.blockSignals(True)
            self.cb_aux.clear()
            self.cb_aux.addItem(tr("(нет)"), None)
            for lyr in QgsProject.instance().mapLayers().values():
                if isinstance(lyr, QgsRasterLayer) and lyr.isValid():
                    self.cb_aux.addItem(lyr.name(), lyr.id())
            self.cb_aux.blockSignals(False)
            self._layer_changed()

        def _layer(self):
            lid = self.cb_layer.currentData()
            return QgsProject.instance().mapLayer(lid) if lid else None

        def _layer_changed(self):
            lyr = self._layer()
            fields = []
            if lyr is not None:
                for f in lyr.fields():
                    if f.isNumeric():
                        fields.append(f.name())
            for cb in (self.cb_mass, self.cb_prec, self.cb_from, self.cb_to):
                cur = cb.currentText()
                cb.blockSignals(True)
                cb.clear()
                cb.addItem(tr("(нет)"))
                cb.addItems(fields)
                j = cb.findText(cur)
                if j >= 0:
                    cb.setCurrentIndex(j)
                cb.blockSignals(False)
            # разумные догадки по именам полей демо
            self._guess(self.cb_mass, ("mass",))
            self._guess(self.cb_prec, ("prec", "sigma"))
            self._guess(self.cb_from, ("from_m",))
            self._guess(self.cb_to, ("to_m",))
            if lyr is not None:
                ext = lyr.extent()
                span = max(ext.width(), ext.height())
                if span > 0 and self.sp_cell.value() <= 0.01:
                    self.sp_cell.setValue(round(span / 200.0, 2))
            self._schedule()

        @staticmethod
        def _guess(cb, names):
            for n in names:
                i = cb.findText(n)
                if i >= 0:
                    cb.setCurrentIndex(i)
                    return

        def _fld(self, cb):
            t = cb.currentText()
            return None if (not t or t == tr("(нет)")) else t

        def _schedule(self):
            self._timer.start()

        # --- предпросмотр --------------------------------------------------
        def _preview(self):
            if self._busy:
                return
            lyr = self._layer()
            if lyr is None:
                self.lbl_img.setPixmap(QPixmap())
                self.lbl_inv.setText(tr("Слой не выбран."))
                return
            self._busy = True
            try:
                dens, total, in_mass, n = self._compute_preview(lyr)
                if dens is None:
                    self.lbl_img.setPixmap(QPixmap())
                    self.lbl_inv.setText(tr("Нет данных для предпросмотра."))
                    return
                rgba = D.colorize(dens)
                rgba = np.ascontiguousarray(np.flipud(rgba))   # строка 0 - север
                h, w = rgba.shape[0], rgba.shape[1]
                self._img = QImage(rgba.tobytes(), w, h, 4 * w, _RGBA)
                pm = QPixmap.fromImage(self._img).scaled(
                    self.lbl_img.width() - 4, self.lbl_img.height() - 4,
                    _KEEP, _SMOOTH)
                self.lbl_img.setPixmap(pm)
                lost = 0.0 if in_mass <= 0 else max(
                    0.0, (in_mass - total) / in_mass * 100.0)
                self.lbl_inv.setText(tr(
                    "Объектов: %d.  Масса входа: %.6g.  На сетке: %.6g.  "
                    "Потеряно на краю: %.2f%%") % (n, in_mass, total, lost))
                vmax = float(dens.max()) if dens.size else 0.0
                self.lbl_scale.setText(tr(
                    "Предпросмотр %d×%d, максимум %.4g масса/км². Полный расчёт "
                    "- по кнопке «Записать растр».") % (
                        dens.shape[1], dens.shape[0], vmax))
            except Exception as e:                                   # noqa: BLE001
                self.lbl_inv.setText(tr("Предпросмотр не построен: %s") % e)
            finally:
                self._busy = False

        def _compute_preview(self, lyr):
            ext = lyr.extent()
            if ext.isEmpty():
                return None, 0.0, 0.0, 0
            span = max(ext.width(), ext.height())
            if span <= 0:
                return None, 0.0, 0.0, 0
            cell = span / float(PREVIEW_N)
            gs = D.GridSpec.from_extent(ext.xMinimum(), ext.yMinimum(),
                                        ext.xMaximum(), ext.yMaximum(), cell)
            acc, snum, wsum = gs.new_acc()
            renorm = self.cb_edge.currentIndex() == 0
            f_mass = self._fld(self.cb_mass)
            f_prec = self._fld(self.cb_prec)
            f_from = self._fld(self.cb_from)
            f_to = self._fld(self.cb_to)
            dsig = float(self.sp_sigma.value()) or None
            gt = lyr.geometryType()
            aux = self._aux_grid(gs) if gt == _POLY_GT else None

            in_mass = 0.0
            n = 0
            for ft in lyr.getFeatures():
                if n >= PREVIEW_FEATURES:
                    break
                g = ft.geometry()
                if g is None or g.isEmpty():
                    continue
                mass = _num(ft, f_mass, 1.0)
                prec = _num(ft, f_prec, None)
                if mass is None or mass == 0:
                    continue
                in_mass += float(mass)
                n += 1
                if gt == _POINT_GT:
                    for x, y in _points(g):
                        D.add_point(acc, snum, wsum, gs, x, y, mass,
                                    prec if prec else dsig,
                                    renorm_inside=renorm)
                elif gt == _LINE_GT:
                    fr = _num(ft, f_from, None)
                    to = _num(ft, f_to, None)
                    for verts in _lines(g):
                        v = verts
                        if f_from or f_to:
                            v = D.cut_polyline(verts, fr, to)
                            if v is None:
                                continue
                        D.add_line(acc, snum, wsum, gs, v, mass,
                                   prec if prec else dsig,
                                   renorm_inside=renorm)
                else:
                    for rings in _polys(g):
                        mask = D.rasterize_polygon(gs, rings)
                        D.add_polygon(acc, snum, wsum, gs, mask, mass, aux=aux)
            dens, _eff, total = D.finalize(acc, snum, wsum, gs)
            return dens, total, in_mass, n

        def _aux_grid(self, gs):
            lid = self.cb_aux.currentData()
            if not lid:
                return None
            lyr = QgsProject.instance().mapLayer(lid)
            if lyr is None:
                return None
            try:
                from osgeo import gdal
                ds = gdal.Open(lyr.source())
                if ds is None:
                    return None
                g = ds.GetGeoTransform()
                arr = ds.GetRasterBand(1).ReadAsArray().astype(float)
                nd = ds.GetRasterBand(1).GetNoDataValue()
                ds = None
                if nd is not None:
                    arr = np.where(arr == nd, 0.0, arr)
                col = ((gs.col_centers() - g[0]) / g[1]).astype(int)
                row = ((gs.row_centers() - g[3]) / g[5]).astype(int)
                col = np.clip(col, 0, arr.shape[1] - 1)
                row = np.clip(row, 0, arr.shape[0] - 1)
                return arr[np.ix_(row, col)]
            except Exception:
                return None

        # --- действия -------------------------------------------------------
        def _demo(self):
            """Создать учебный набор инструментом 3.08."""
            try:
                import processing
                from qgis.core import QgsProject  # noqa: F401
                canvas = iface.mapCanvas() if iface is not None else None
                ext = canvas.extent() if canvas is not None else None
                crs = (canvas.mapSettings().destinationCrs().authid()
                       if canvas is not None else "")
                if ext is None or ext.isEmpty():
                    QMessageBox.information(
                        self, tr("Демо"),
                        tr("Приблизьте карту к нужной области."))
                    return
                extent = "%f,%f,%f,%f" % (ext.xMinimum(), ext.xMaximum(),
                                          ext.yMinimum(), ext.yMaximum())
                if crs:
                    extent += " [%s]" % crs
                processing.runAndLoadResults("isoliner:densitydemo", {
                    "EXTENT": extent,
                    "CELL_AUX": max(ext.width(), ext.height()) / 100.0,
                    "SEED": 1,
                    "OUT_POINTS": "TEMPORARY_OUTPUT",
                    "OUT_LINES": "TEMPORARY_OUTPUT",
                    "OUT_POLYGONS": "TEMPORARY_OUTPUT",
                    "OUT_AUX": "TEMPORARY_OUTPUT"})
                self.refresh_layers()
                QMessageBox.information(
                    self, tr("Демо"),
                    tr("Демо создано: масса точек 500, линий 200, полигонов "
                       "300. Выберите слой и смотрите предпросмотр."))
            except Exception as e:                                   # noqa: BLE001
                QMessageBox.warning(self, tr("Демо"), str(e))

        def _toast(self, text):
            """Ненавязчивое сообщение: строка состояния QGIS или подпись внизу."""
            try:
                if iface is not None:
                    iface.messageBar().pushInfo("Isoliner", text)
                    return
            except Exception:
                pass                                                 # nosec
            self.lbl_inv.setText(text)

        def _write(self):
            """Полный расчёт через инструмент 3.07 и оформление слоёв."""
            lyr = self._layer()
            if lyr is None:
                return
            try:
                import processing
                params = {
                    "INPUT": lyr,
                    "CELL": float(self.sp_cell.value()),
                    "EDGE": int(self.cb_edge.currentIndex()),
                    "DEFAULT_SIGMA": float(self.sp_sigma.value()),
                    "OUTPUT": "TEMPORARY_OUTPUT",
                }
                for key, cb in (("MASS_FIELD", self.cb_mass),
                                ("PREC_FIELD", self.cb_prec),
                                ("FROM_FIELD", self.cb_from),
                                ("TO_FIELD", self.cb_to)):
                    v = self._fld(cb)
                    if v:
                        params[key] = v
                aid = self.cb_aux.currentData()
                if aid:
                    params["DASY"] = QgsProject.instance().mapLayer(aid)
                if self.ch_sigma.isChecked():
                    params["OUTPUT_SIGMA"] = "TEMPORARY_OUTPUT"
                res = processing.run("isoliner:vardensity", params)
                dens_path = res.get("OUTPUT")
                dl = QgsRasterLayer(dens_path, tr("Плотность (переменная опора)"))
                dmin = dmax = None
                if dl.isValid():
                    style_density(dl)
                    QgsProject.instance().addMapLayer(dl)
                    try:
                        st = dl.dataProvider().bandStatistics(1)
                        dmin, dmax = float(st.minimumValue), float(st.maximumValue)
                    except Exception:
                        pass                                         # nosec
                    if self.ch_iso.isChecked():
                        try:
                            iv = D.nice_interval(max(dmin, 0.0), dmax) \
                                if dmin is not None else 0.0
                            iso = processing.run("isoliner:raster_to_isolines", {
                                "INPUT": dl, "BAND": 1, "INTERVAL": iv,
                                "OUTPUT": "TEMPORARY_OUTPUT"})
                            lv = iso.get("OUTPUT")
                            if isinstance(lv, str):
                                from qgis.core import QgsVectorLayer as _VL
                                lvl = _VL(lv, tr("Изолинии плотности"), "ogr")
                                if lvl.isValid():
                                    QgsProject.instance().addMapLayer(lvl)
                        except Exception:
                            pass                                     # nosec
                sp = res.get("OUTPUT_SIGMA")
                if sp:
                    sl = QgsRasterLayer(sp, tr("Эффективная сигма (доверие)"))
                    if sl.isValid():
                        try:
                            ss = sl.dataProvider().bandStatistics(1)
                            if float(ss.maximumValue) > float(ss.minimumValue):
                                style_density(sl)          # псевдоцвет
                            else:
                                self._toast(tr(
                                    "Эффективная сигма постоянна (%.4g): у слоя "
                                    "одинаковая точность, карта доверия "
                                    "вырождена.") % float(ss.maximumValue))
                        except Exception:
                            pass                                     # nosec
                        QgsProject.instance().addMapLayer(sl)
            except Exception as e:                                   # noqa: BLE001
                QMessageBox.warning(self, tr("Карта плотности"), str(e))

    def _num(ft, field, default):
        if not field:
            return default
        try:
            v = ft.attribute(field)
            if v is None:
                return default
            fv = float(v)
            return fv if np.isfinite(fv) else default
        except (TypeError, ValueError):
            return default

    def _points(g):
        from qgis.core import QgsWkbTypes
        if QgsWkbTypes.isMultiType(g.wkbType()):
            for p in g.asMultiPoint():
                yield (p.x(), p.y())
        else:
            p = g.asPoint()
            yield (p.x(), p.y())

    def _lines(g):
        from qgis.core import QgsWkbTypes
        parts = (g.asMultiPolyline() if QgsWkbTypes.isMultiType(g.wkbType())
                 else [g.asPolyline()])
        for line in parts:
            if len(line) >= 2:
                yield np.array([(p.x(), p.y()) for p in line], float)

    def _polys(g):
        from qgis.core import QgsWkbTypes
        polys = (g.asMultiPolygon() if QgsWkbTypes.isMultiType(g.wkbType())
                 else [g.asPolygon()])
        for poly in polys:
            rings = [np.array([(p.x(), p.y()) for p in ring], float)
                     for ring in poly if len(ring) >= 3]
            if rings:
                yield rings

    return DensityDialog(parent)


def style_density(layer):
    """Псевдоцвет с прозрачными нулями: растр ложится на карту уже одетым."""
    try:
        from qgis.core import (QgsColorRampShader, QgsRasterShader,
                               QgsSingleBandPseudoColorRenderer,
                               QgsRasterTransparency)
        from qgis.PyQt.QtGui import QColor
        prov = layer.dataProvider()
        st = prov.bandStatistics(1)
        lo, hi = float(st.minimumValue), float(st.maximumValue)
        if not (hi > lo):
            return
        lo = max(lo, 0.0)
        stops = [(0.00, QColor(68, 1, 84)), (0.25, QColor(49, 104, 142)),
                 (0.50, QColor(31, 158, 137)), (0.75, QColor(109, 205, 89)),
                 (1.00, QColor(253, 231, 37))]
        ramp = QgsColorRampShader(lo, hi)
        ramp.setColorRampType(getattr(
            getattr(QgsColorRampShader, "Type", QgsColorRampShader),
            "Interpolated"))
        items = [QgsColorRampShader.ColorRampItem(
            lo + t * (hi - lo), c, "%.4g" % (lo + t * (hi - lo)))
            for t, c in stops]
        ramp.setColorRampItemList(items)
        shader = QgsRasterShader()
        shader.setRasterShaderFunction(ramp)
        layer.setRenderer(QgsSingleBandPseudoColorRenderer(prov, 1, shader))
        tr_ = QgsRasterTransparency()
        px = QgsRasterTransparency.TransparentSingleValuePixel()
        px.min, px.max, px.percentTransparent = 0.0, 0.0, 100.0
        tr_.setTransparentSingleValuePixelList([px])
        layer.renderer().setRasterTransparency(tr_)
        layer.triggerRepaint()
    except Exception:
        pass                                                          # nosec
