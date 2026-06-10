# -*- coding: utf-8 -*-
#
# Isoliner — грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), — либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии — в файле LICENSE (на английском, юридически значим).
"""Класс плагина: регистрирует провайдер Processing «Isoliner».

hasProcessingProvider=yes -> QGIS сам вызывает initProcessing(). Регистрация
идемпотентна (без двойного добавления). Совместимо с QGIS 3.40 и 4.x.
"""
from qgis.core import QgsApplication, QgsMessageLog

PROVIDER_ID = "isoliner"


def _log(msg):
    try:
        QgsMessageLog.logMessage(msg, "Isoliner")
    except Exception:
        pass


class GridIsolinesPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None

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

    def unload(self):
        reg = QgsApplication.processingRegistry()
        prov = self.provider or reg.providerById(PROVIDER_ID)
        if prov is not None:
            reg.removeProvider(prov)
            self.provider = None
