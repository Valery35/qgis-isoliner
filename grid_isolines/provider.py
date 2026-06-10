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
"""Провайдер «Isoliner» (Processing): группа «Грид и изолинии»."""
import os

from qgis.core import QgsProcessingProvider, QgsMessageLog
from qgis.PyQt.QtGui import QIcon

from .algorithms import ALGORITHMS


def _log(msg):
    try:
        QgsMessageLog.logMessage(msg, "Isoliner")
    except Exception:
        pass


class GridIsolinesProvider(QgsProcessingProvider):
    def loadAlgorithms(self):
        loaded = 0
        for cls in ALGORITHMS:
            try:
                self.addAlgorithm(cls())
                loaded += 1
            except Exception as e:   # один сбойный алгоритм не валит группу
                _log("Не удалось добавить %s: %s" % (cls.__name__, e))
        _log("Загружено алгоритмов: %d" % loaded)

    def id(self):
        return "isoliner"

    def name(self):
        return "Isoliner"

    def longName(self):
        return self.name()

    def icon(self):
        path = os.path.join(os.path.dirname(__file__), "icon.svg")
        return QIcon(path) if os.path.exists(path) else QIcon()
