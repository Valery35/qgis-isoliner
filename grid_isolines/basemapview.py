# -*- coding: utf-8 -*-
"""Окно «Подоснова»: подложить карту или космоснимок под данные.

Слои грузятся из файлов .qlr через QgsLayerDefinition. Строка подключения и
стиль руками не собираются: в .qlr уже лежит и то, и другое, включая систему
координат и прозрачность. Собранный вручную URI это теряет, а недействительный
слой QGIS добавляет молча.

Про систему координат. Подосновы отдаются в EPSG:3857, а проект обычно
работает в местной системе. Если у системы координат проекта задан датум,
переход считается и подоснова совмещается. Если датум не задан, снимок
сместится без предупреждения, поэтому окно проверяет это заранее.

Окно немодальное: подосновы подбирают, глядя на карту.
"""

import os

try:
    from qgis.PyQt.QtWidgets import (
        QDialog, QVBoxLayout, QGroupBox, QListWidget, QListWidgetItem,
        QLabel, QCheckBox, QMessageBox, QApplication, QDialogButtonBox,
    )
    from qgis.core import QgsProject, QgsLayerDefinition
    from . import qt_compat as qtc
    _QGIS = True
except Exception:                                   # nosec
    _QGIS = False

from . import basemap_registry as basemaps
from .i18n import tr


def is_available():
    """Есть ли из чего строить окно: QGIS под рукой и хотя бы один .qlr."""
    return _QGIS and bool(basemaps.load())


def show_view(iface):
    """Открывает окно подосновы. Повторный вызов поднимает уже открытое."""
    win = iface.mainWindow() if iface is not None else None
    existing = getattr(show_view, "_dlg", None)
    if existing is None:
        existing = BasemapDialog(iface, win)
        show_view._dlg = existing

        def _forget():
            show_view._dlg = None

        existing.destroyed.connect(_forget)
    else:
        existing.recheck()
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


class BasemapDialog(QDialog):
    """Выбор и добавление подоснов."""

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self.setWindowTitle(tr("Isoliner - Подоснова"))
        self.setMinimumWidth(520)
        self.setModal(False)
        self.setWindowModality(qtc.NonModal)

        self._maps = basemaps.load()
        self._build()
        self._check_crs()

    def _build(self):
        root = QVBoxLayout(self)

        box = QGroupBox(tr("Источники"))
        lay = QVBoxLayout(box)
        self.list = QListWidget()
        self.list.setMinimumHeight(170)
        for bm in self._maps:
            text = bm.name
            if bm.has_transparent_style:
                text += "  " + tr("(есть полупрозрачный стиль)")
            item = QListWidgetItem(text)
            item.setData(qtc.UserRole, bm.key)
            item.setFlags(item.flags() | qtc.ItemIsUserCheckable)
            item.setCheckState(qtc.Unchecked)
            self.list.addItem(item)
        lay.addWidget(self.list)
        root.addWidget(box)

        self.under = QCheckBox(tr("Положить под остальные слои"))
        self.under.setChecked(True)
        root.addWidget(self.under)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #a04000;")
        root.addWidget(self.warning)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        root.addWidget(self.status)

        buttons = QDialogButtonBox()
        # ActionRole, а не AcceptRole: окно остаётся открытым, чтобы можно
        # было добавить ещё одну подоснову, посмотрев на карту.
        self.btn_add = buttons.addButton(tr("Добавить"),
                                         qtc.dbb('ActionRole'))
        buttons.addButton(tr("Закрыть"), qtc.dbb('RejectRole'))
        buttons.rejected.connect(self.close)
        self.btn_add.clicked.connect(self.add_selected)
        root.addWidget(buttons)

        if not self._maps:
            self.status.setText(tr("В папке basemaps нет файлов .qlr."))
            self.btn_add.setEnabled(False)

    # ---- система координат ----

    def recheck(self):
        """Перепроверка при повторном открытии: проект мог сменить систему."""
        self._check_crs()
        self.status.setText("")

    def _check_crs(self):
        crs = QgsProject.instance().crs()
        if not crs.isValid():
            self.warning.setText(tr(
                "Система координат проекта не задана. Подоснова ляжет неверно."))
            return
        try:
            wkt = crs.toWkt()
        except Exception:                           # nosec
            wkt = ""
        if wkt and "DATUM" not in wkt.upper():
            self.warning.setText(tr(
                "В системе координат проекта не задан датум. Подоснова ляжет "
                "со сдвигом."))
            return
        self.warning.setText("")

    # ---- добавление ----

    def _selected(self):
        keys = []
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item.checkState() == qtc.Checked:
                keys.append(item.data(qtc.UserRole))
        return [m for m in self._maps if m.key in keys]

    def add_selected(self):
        chosen = self._selected()
        if not chosen:
            QMessageBox.information(self, tr("Подоснова"),
                                    tr("Отметьте хотя бы один источник."))
            return

        QApplication.setOverrideCursor(qtc.WaitCursor)
        added, failed = [], []
        try:
            project = QgsProject.instance()
            root = project.layerTreeRoot()
            for bm in chosen:
                if not os.path.exists(bm.path):
                    failed.append(bm.name)
                    continue
                ok, message = QgsLayerDefinition.loadLayerDefinition(
                    bm.path, project, root)
                if ok:
                    added.append(bm.name)
                else:
                    failed.append("%s (%s)" % (bm.name, message or "?"))
        except Exception as exc:                    # nosec
            failed.append(str(exc))
        finally:
            QApplication.restoreOverrideCursor()

        if added and self.under.isChecked():
            self._move_down(added)

        parts = []
        if added:
            parts.append(tr("Добавлено: ") + ", ".join(added))
        if failed:
            parts.append(tr("Не удалось подключить: ") + ", ".join(failed))
        self.status.setText(". ".join(parts))

        if added:
            self._uncheck_all()
            if self.iface is not None:
                self.iface.messageBar().pushInfo(tr("Подоснова"),
                                                 ", ".join(added))
                try:
                    self.iface.mapCanvas().refresh()
                except Exception:                   # nosec
                    pass

    def _uncheck_all(self):
        """Отметки снимаются, иначе повторное нажатие добавит слой дважды."""
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(qtc.Unchecked)

    def _move_down(self, names):
        """Опускает добавленные слои в самый низ дерева."""
        try:
            project = QgsProject.instance()
            root = project.layerTreeRoot()
            for name in names:
                for node in root.findLayers():
                    layer = node.layer()
                    if layer is None or layer.name() != name:
                        continue
                    clone = node.clone()
                    root.addChildNode(clone)
                    node.parent().removeChildNode(node)
                    break
        except Exception:                           # nosec
            pass
