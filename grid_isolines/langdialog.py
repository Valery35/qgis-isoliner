# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Окно выбора языка интерфейса и языка справки.

Два списка, в каждом «как в QGIS», «Русский», «English». Выбор записывается в
общие ключи informpp/, поэтому действует во всех двуязычных модулях Информ++
сразу, а не только в том, из меню которого окно открыто.

Названия языков в списке не переводятся. Человек, который не понимает
текущего языка окна, должен найти свой язык по его собственному имени.
"""
from .i18n import tr, stored_choices, CHOICES

# Имя языка на нём самом. «Как в QGIS» переводится, имена языков - нет.
NATIVE = {"ru": "Русский", "en": "English"}


def _fill(combo, current):
    combo.addItem(tr("Как в QGIS"), "auto")
    for code in ("ru", "en"):
        combo.addItem(NATIVE[code], code)
    idx = combo.findData(current if current in CHOICES else "auto")
    combo.setCurrentIndex(max(idx, 0))


def ask(parent=None):
    """Показать окно. Вернуть пару (интерфейс, справка) или None при отмене."""
    from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QFormLayout,
                                     QComboBox, QLabel, QDialogButtonBox)
    ui, hl = stored_choices()
    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("Язык модулей Информ++"))
    lay = QVBoxLayout(dlg)

    form = QFormLayout()
    cb_ui = QComboBox(dlg)
    _fill(cb_ui, ui)
    form.addRow(tr("Язык интерфейса"), cb_ui)
    cb_help = QComboBox(dlg)
    _fill(cb_help, hl)
    form.addRow(tr("Язык справки"), cb_help)
    lay.addLayout(form)

    note = QLabel(tr(
        "Интерфейс - это меню, названия инструментов и подписи полей. Справка - "
        "это боковая справка инструмента, подсказки полей и руководство PDF. Выбор "
        "хранится в общих настройках модулей Информ++."), dlg)
    note.setWordWrap(True)
    lay.addWidget(note)

    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                           | QDialogButtonBox.StandardButton.Cancel, dlg)
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)

    if not dlg.exec():
        return None
    return cb_ui.currentData(), cb_help.currentData()
