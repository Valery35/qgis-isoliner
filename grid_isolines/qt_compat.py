# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
"""Совместимость Qt5 и Qt6.

QGIS 3 собран на Qt5, QGIS 4 - на Qt6. В Qt6 перечисления строгие: вместо
`Qt.WaitCursor` нужно `Qt.CursorShape.WaitCursor`, вместо `Qt.UserRole` -
`Qt.ItemDataRole.UserRole`. Плоское имя там просто отсутствует, и обращение
к нему роняет окно с AttributeError.

Константы разрешаются здесь один раз, чтобы в остальном коде не было
развилок по версии.
"""

from qgis.PyQt.QtCore import Qt


def _c(scope, name):
    """Строгое имя (Qt6), иначе плоское (Qt5)."""
    holder = getattr(Qt, scope, None)
    if holder is not None and hasattr(holder, name):
        return getattr(holder, name)
    return getattr(Qt, name)


WaitCursor          = _c("CursorShape",    "WaitCursor")
UserRole            = _c("ItemDataRole",   "UserRole")
Checked             = _c("CheckState",     "Checked")
Unchecked           = _c("CheckState",     "Unchecked")
ItemIsUserCheckable = _c("ItemFlag",       "ItemIsUserCheckable")
NonModal            = _c("WindowModality", "NonModal")


def dbb(name):
    """Кнопки QDialogButtonBox: в Qt6 разнесены по StandardButton и ButtonRole.

    dbb('Ok'), dbb('Cancel'), dbb('ActionRole'), dbb('RejectRole').
    """
    from qgis.PyQt.QtWidgets import QDialogButtonBox
    for scope in ("StandardButton", "ButtonRole"):
        holder = getattr(QDialogButtonBox, scope, None)
        if holder is not None and hasattr(holder, name):
            return getattr(holder, name)
    return getattr(QDialogButtonBox, name)
