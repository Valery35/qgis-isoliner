# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Пункты меню плагина: «О плагине» и «Руководство (PDF)».

Диалог «О плагине» читает версию и историю изменений из metadata.txt -
отдельный файл истории не нужен, канонический changelog уже там.
Qt импортируется лениво (внутри функций), поэтому модуль читается и
тестируется без QGIS (см. tests/test_about.py).
"""
import os

_HERE = os.path.dirname(__file__)


def read_metadata():
    """Словарь секции [general] из metadata.txt (version, changelog и пр.)."""
    import configparser
    cp = configparser.ConfigParser(interpolation=None)
    with open(os.path.join(_HERE, "metadata.txt"), encoding="utf-8") as f:
        cp.read_file(f)
    return dict(cp["general"])


def manual_path():
    """Путь к PDF руководства по языку интерфейса (как кнопка «Справка»)."""
    from .i18n import language
    candidates = []
    try:
        if language() == "en":
            candidates.append("Isoliner_en.pdf")
    except Exception:
        pass
    candidates.append("Isoliner.pdf")
    for fname in candidates:
        p = os.path.join(_HERE, "doc", fname)
        if os.path.exists(p):
            return p
    return ""


def open_manual(parent=None):
    """Открыть руководство системным просмотрщиком PDF."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices
    from qgis.PyQt.QtWidgets import QMessageBox
    from .i18n import tr
    p = manual_path()
    if p:
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))
    else:
        QMessageBox.warning(parent, "Isoliner", tr("Руководство не найдено."))


def open_log(parent=None):
    """Открыть файл журнала isoliner.log системным приложением."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices
    from qgis.PyQt.QtWidgets import QMessageBox
    from .i18n import tr
    try:
        from . import trace
        p = trace.path()
    except Exception:
        p = ""
    if p and os.path.exists(p):
        QDesktopServices.openUrl(QUrl.fromLocalFile(p))
    else:
        QMessageBox.information(parent, "Isoliner",
                                tr("Журнал ещё не создан."))


def show_changelog(parent=None):
    """Окно с историей изменений из metadata.txt (прокручиваемый текст)."""
    from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QPlainTextEdit,
                                     QDialogButtonBox)
    from .i18n import tr
    meta = read_metadata()
    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("История изменений"))
    dlg.resize(640, 480)
    lay = QVBoxLayout(dlg)
    txt = QPlainTextEdit(dlg)
    txt.setReadOnly(True)
    txt.setPlainText(meta.get("changelog", ""))
    lay.addWidget(txt)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)
    dlg.exec()


def show_about(parent=None):
    """Диалог «О плагине»: иконка, версия, ссылки, история изменений."""
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtGui import QIcon
    from qgis.PyQt.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout,
                                     QLabel, QPushButton, QDialogButtonBox)
    from .i18n import tr
    meta = read_metadata()
    ver = meta.get("version", "?")
    home = meta.get("homepage", "")
    tracker = meta.get("tracker", "")

    dlg = QDialog(parent)
    dlg.setWindowTitle(tr("О плагине"))
    lay = QVBoxLayout(dlg)

    head = QHBoxLayout()
    icon = QLabel(dlg)
    ipath = os.path.join(_HERE, "icon.svg")
    if os.path.exists(ipath):
        icon.setPixmap(QIcon(ipath).pixmap(56, 56))
    head.addWidget(icon)
    title = QLabel(
        "<b>Isoliner</b><br>%s<br>© ООО «Информ++»" % (tr("Версия %s") % ver),
        dlg)
    head.addWidget(title, 1)
    lay.addLayout(head)

    links = QLabel(
        '<a href="https://www.informpp.ru/">www.informpp.ru</a> · '
        '<a href="%s">%s</a> · '
        '<a href="%s">%s</a>' % (home, tr("Исходный код"),
                                 tracker, tr("Сообщить об ошибке")), dlg)
    links.setOpenExternalLinks(True)
    links.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    lay.addWidget(links)

    row = QHBoxLayout()
    btn_log = QPushButton(tr("История изменений"), dlg)
    btn_log.clicked.connect(lambda: show_changelog(dlg))
    row.addWidget(btn_log)
    btn_man = QPushButton(tr("Руководство (PDF)"), dlg)
    btn_man.clicked.connect(lambda: open_manual(dlg))
    row.addWidget(btn_man)
    btn_journal = QPushButton(tr("Журнал"), dlg)
    btn_journal.clicked.connect(lambda: open_log(dlg))
    row.addWidget(btn_journal)
    row.addStretch(1)
    lay.addLayout(row)

    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)
    dlg.exec()
