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
"""Класс плагина: регистрирует провайдер Processing «Isoliner».

hasProcessingProvider=yes -> QGIS сам вызывает initProcessing(). Регистрация
идемпотентна (без двойного добавления). Совместимо с QGIS 3.40 и 4.x.
"""
import os

from qgis.core import QgsApplication, QgsMessageLog

PROVIDER_ID = "isoliner"


def _log(msg):
    try:
        QgsMessageLog.logMessage(msg, "Isoliner")
    except Exception:  # nosec
        pass


class GridIsolinesPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.actions = []
        self.toolbar = None

    # --- журнал ------------------------------------------------------------
    def _plugin_version(self):
        """Версия из metadata.txt (не дублируем в коде: разъедется)."""
        import configparser
        path = os.path.join(os.path.dirname(__file__), "metadata.txt")
        parser = configparser.ConfigParser(strict=False)
        try:
            parser.read(path, encoding="utf-8")
            return parser.get("general", "version").strip()
        except Exception:
            return "?"

    def _start_log(self):
        """Заголовок сеанса: версия плагина, QGIS, ОС, NumPy, GDAL."""
        try:
            from . import trace
            base = ""
            try:
                base = QgsApplication.qgisSettingsDirPath()
            except Exception:
                base = ""
            if not base or not os.path.isdir(base):
                base = os.path.expanduser("~")
            trace.set_path(os.path.join(base, "isoliner.log"), "Isoliner")
            extra = []
            try:
                from qgis.core import Qgis
                extra.append("QGIS: %s" % Qgis.QGIS_VERSION)
            except Exception:  # nosec
                pass
            try:
                import numpy
                extra.append("NumPy: %s" % numpy.__version__)
            except Exception:
                extra.append("NumPy: нет")
            try:
                from osgeo import gdal
                extra.append("GDAL: %s" % gdal.__version__)
            except Exception:
                extra.append("GDAL: нет")
            trace.session(self._plugin_version(), extra)
            trace.step("Модуль загружен")
        except Exception as e:
            _log("Журнал не заведён: %s" % e)

    def _open_log(self):
        """Открыть файл журнала в системе."""
        try:
            from . import trace
            from qgis.PyQt.QtGui import QDesktopServices
            from qgis.PyQt.QtCore import QUrl
            p = trace.path()
            if p and os.path.exists(p):
                QDesktopServices.openUrl(QUrl.fromLocalFile(p))
            else:
                _log("Журнал ещё не создан.")
        except Exception as e:
            _log("Не удалось открыть журнал: %s" % e)

    # --- регистрация -------------------------------------------------------
    def initProcessing(self):
        reg = QgsApplication.processingRegistry()
        if reg.providerById(PROVIDER_ID) is not None:
            return  # уже зарегистрирован
        try:
            from .provider import GridIsolinesProvider
            self.provider = GridIsolinesProvider()
            reg.addProvider(self.provider)
            _log("Провайдер '%s' зарегистрирован." % PROVIDER_ID)
        except Exception as e:
            _log("Ошибка регистрации провайдера: %s" % e)
            raise

    def initGui(self):
        self.initProcessing()
        self._start_log()
        try:
            from qgis.PyQt.QtGui import QIcon
            try:
                from qgis.PyQt.QtGui import QAction   # Qt6 (QGIS 4)
            except ImportError:
                from qgis.PyQt.QtWidgets import QAction   # Qt5 (QGIS 3)
            from .i18n import tr, init_from_qgis
            from . import about, densityview, basemapview
            init_from_qgis()
            here = os.path.dirname(__file__)
            icon_main = QIcon(os.path.join(here, "icon.svg"))
            icon_log = QIcon(os.path.join(here, "icon_log.svg"))
            icon_den = QIcon(os.path.join(here, "icon_density.svg"))
            icon_map = QIcon(os.path.join(here, "icon_basemap.svg"))

            win = self.iface.mainWindow()
            self.toolbar = self.iface.addToolBar(tr("Isoliner"))
            self.toolbar.setObjectName("IsolinerToolbar")

            # Карта плотности (живой предпросмотр 3.07)
            if densityview.is_available():
                a_den = QAction(icon_den, tr("Карта плотности…"), win)
                a_den.setToolTip(tr(
                    "Живой предпросмотр плотности с переменной опорой"))
                a_den.triggered.connect(
                    lambda: densityview.show_view(self.iface))
                self._add(a_den, toolbar=True)

            # Подоснова: карта или космоснимок под данные
            if basemapview.is_available():
                a_map = QAction(icon_map, tr("Подоснова…"), win)
                a_map.setToolTip(tr(
                    "Подложить карту или космоснимок под данные"))
                a_map.triggered.connect(
                    lambda: basemapview.show_view(self.iface))
                self._add(a_map, toolbar=True)

            # О плагине
            a_about = QAction(icon_main, tr("О плагине…"), win)
            a_about.triggered.connect(lambda: about.show_about(win))
            self._add(a_about, toolbar=True)

            # Журнал
            a_log = QAction(icon_log, tr("Журнал…"), win)
            a_log.setToolTip(tr("Открыть файл журнала Isoliner"))
            a_log.triggered.connect(self._open_log)
            self._add(a_log, toolbar=False)     # только меню, панель не грузим
        except Exception as e:
            _log("Интерфейс плагина не создан: %s" % e)

    def _add(self, action, toolbar=False):
        self.iface.addPluginToMenu("Isoliner", action)
        if toolbar and self.toolbar is not None:
            self.toolbar.addAction(action)
        self.actions.append(action)

    def unload(self):
        for a in getattr(self, "actions", []):
            try:
                self.iface.removePluginMenu("Isoliner", a)
            except Exception:  # nosec
                pass
        self.actions = []
        if self.toolbar is not None:
            try:
                self.toolbar.deleteLater()
            except Exception:  # nosec
                pass
            self.toolbar = None
        reg = QgsApplication.processingRegistry()
        prov = self.provider or reg.providerById(PROVIDER_ID)
        if prov is not None:
            reg.removeProvider(prov)
            self.provider = None
