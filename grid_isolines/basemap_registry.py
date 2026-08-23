# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
"""Реестр подоснов: файлы .qlr в папке basemaps.

Модуль называется иначе, чем папка с источниками, намеренно: файл
basemaps.py рядом с папкой basemaps сбивает и человека, и часть
установщиков, а на импорт пакета такое соседство влияет молча.

Источники описаны определениями слоёв QGIS (.qlr), а не собираются в коде.
В .qlr уже лежит и правильная строка подключения, и система координат, и
стиль с прозрачностью. Собранный вручную URI это теряет, а недействительный
слой QGIS добавляет молча: ни ошибки, ни подосновы.

Файл делается в самом QGIS: настроить слой, затем правой кнопкой
«Экспорт - Сохранить как файл определения слоя». Порядок в списке задаётся
числовым префиксом имени файла.

Разбор свой, без модулей xml. Стандартный разборщик уязвим к раздутым
сущностям и внешним ссылкам, сканер каталога plugins.qgis.org его
блокирует, а тянуть в плагин defusedxml нельзя: его нет в поставке QGIS.
Здесь нужны всего три вещи - имя слоя, строка подключения и список
стилей, - и они берутся поиском по тексту. Сущности при этом не
раскрываются вовсе, то есть путь атаки закрыт вместе с ними.

Модуль не зависит от QGIS: разбор проверяется тестами.
"""

import os
import re

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
BASEMAPS_DIR = os.path.join(PLUGIN_DIR, "basemaps")

_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"))


def _unescape(text):
    """Обратная замена служебных последовательностей.

    Амперсанд идёт последним: иначе `&amp;lt;` превратился бы в `<`.
    """
    for src, dst in _ENTITIES:
        text = text.replace(src, dst)
    return text.replace("&amp;", "&")


def _tag_text(text, tag):
    """Содержимое первого элемента `tag`. Пусто, если элемента нет."""
    m = re.search(r"<%s(?:\s[^>]*)?>(.*?)</%s>" % (tag, tag), text, re.S)
    return _unescape(m.group(1)).strip() if m else ""


def _attr(text, tag, name):
    """Значение атрибута `name` у первого элемента `tag`."""
    m = re.search(r"<%s\b[^>]*?\b%s=\"(.*?)\"" % (tag, name), text, re.S)
    return _unescape(m.group(1)) if m else ""


class BaseMap:
    """Подоснова, описанная файлом .qlr."""

    def __init__(self, path, name, source="", styles=None, provider="wms"):
        self.path = path
        self.name = name
        self.source = source
        self.styles = styles or []
        self.provider = provider

    @property
    def key(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def has_transparent_style(self):
        """Есть ли сохранённый полупрозрачный вариант."""
        return len(self.styles) > 1


def read(path):
    """Читает .qlr. Возвращает BaseMap или None, если файл некорректен."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except Exception:                               # nosec
        return None
    if "<qlr" not in text:
        return None

    name = _tag_text(text, "layername")
    if not name:
        name = _attr(text, "layer-tree-layer", "name")
    if not name:
        return None

    source = _tag_text(text, "datasource")
    if not source:
        source = _attr(text, "layer-tree-layer", "source")

    styles = [_unescape(s) for s in
              re.findall(r"<map-layer-style\b[^>]*?\bname=\"(.*?)\"", text)]

    provider = _tag_text(text, "provider") or "wms"
    return BaseMap(path=path, name=name, source=source, styles=styles,
                   provider=provider)


def load(folder=None):
    """Все подосновы папки, по порядку имён файлов."""
    folder = folder or BASEMAPS_DIR
    if not os.path.isdir(folder):
        return []
    maps = []
    for fname in sorted(os.listdir(folder)):
        if not fname.lower().endswith(".qlr"):
            continue
        bm = read(os.path.join(folder, fname))
        if bm is not None:
            maps.append(bm)
    return maps


def check_source(source):
    """Беглая проверка строки подключения.

    Ловится то, из-за чего слой выходит недействительным: без crs QGIS
    угадывает систему координат. У плиточных источников (xyz) дополнительно
    нужен шаблон {z}/{x}/{y}, а у ArcGIS REST его нет и быть не должно.
    Возвращает список замечаний, пустой список - всё в порядке.
    """
    problems = []
    if "crs=" not in source:
        problems.append("не задана система координат (crs)")
    if "type=xyz" in source:
        if not re.search(r"%7Bz%7D|\{z\}", source):
            problems.append("в адресе нет шаблона z")
        if not re.search(r"%7Bx%7D|\{x\}", source):
            problems.append("в адресе нет шаблона x")
        if not re.search(r"%7By%7D|\{y\}", source):
            problems.append("в адресе нет шаблона y")
    return problems
