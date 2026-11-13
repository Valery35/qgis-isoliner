# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Инструмент как объект: описание и расчёт без QGIS.

Контракт перенесён из Isoliner3D (isoliner3d/toolspec.py) без
отступлений: tools.json и tool_runs.json обоих модулей читает один
крейт на Rust. Правки контракта идут через его владельца, а не в
каждом модуле отдельно.

Инструмент состоит из двух частей. Описание: код («1.05»),
имя для Processing, название, справка, поля с подписями и подсказками.
Расчёт: функция run(значения, отчёт) на простых данных - массивах
NumPy, словарях и списках. Ни то ни другое не знает QGIS.

В Isoliner класс QGIS остаётся прежним (поля, память слоёв, подписи
полей, округление), его _process переводит слои в данные для run и
записывает результат в слои. Описание для tools.json строится из
того же класса разбором initAlgorithm (tools/export_tools.py в корне
репозитория), а расчёт переносится на Rust с теми же кодами и полями.

Данные полей на входе run:

- mesh   - весь слой полигонов с Z одним телом: {"verts", "faces"};
- lines  - линии: список {"id", "parts": [массивы (n, 3)], "attrs"},
           у линий без Z третий столбец NaN;
- points - точки: список {"id", "xyz": (3,), "attrs"};
- features - объекты любой геометрии: список {"id", "parts":
           [массивы (n, 3)], "attrs"}. Часть - вершины точки, линии
           или внешнего кольца полигона, без Z третий столбец NaN;
- meshes - слой тел, тело на объект: список {"id", "attrs", "verts",
           "faces"}, у объекта без граней verts и faces - None;
- table  - таблица или слой любой геометрии: список {"id", "attrs",
           "xyz"}, xyz - первая вершина (3,) или None без геометрии;
- field  - имя поля или пустая строка;
- string - строка; bool - True или False;
- number, integer - число; enum - номер выбранного пункта, при
           multiple=True - список номеров;
- raster - растр: Grid (ниже), каналы читаются по запросу;
- band   - номер канала растра parent, с единицы;
- sink   - выход. Обязательного выхода на входе run нет,
           необязательный приходит True, если его заказали.
           Выход с geometry="raster" - растр, run отдаёт RasterResult.

К каждому слою на входе обёртка добавляет values["@КЛЮЧ"] =
{"name": имя слоя, "geometry": "point" | "line" | "polygon" | "none",
"hasz": есть ли Z, "fields": [имена полей], "numeric": [числовые
поля]}. У растра там {"name", "geometry": "raster", "bands": число
каналов, "names": [подписи каналов]}. Без QGIS этот словарь можно
не давать. Поле field с multiple=True приходит списком имён.

Результат run - словарь {ключ выхода: Result}. Result несёт поля
(имя, тип), объекты (геометрия, значения) и признак keep_source:
выход начинается полями объекта источника. При geometry="source"
выход берёт и геометрию источника как есть: так отбор возвращает
те же объекты, что получил, а геометрия объекта - это {"source": id}.

Поле с advanced=True обёртка прячет в дополнительные параметры.

Строки, которые видит пользователь, помечены N_(): по этой метке
их находит проверка переводов, а переводит обёртка при показе.
Сообщения отчёта - шаблон с аргументами, тоже переводятся при показе.
"""

import numpy as np


def N_(text):
    """Метка строки для перевода: перевод делается при показе."""
    return text


KINDS = ("mesh", "meshes", "lines", "points", "features", "table",
         "raster", "band",
         "field", "string", "bool", "number", "integer", "enum", "sink")
SOURCE_KINDS = ("mesh", "meshes", "lines", "points", "features", "table")


class Grid(object):
    """Растр без GDAL: каналы, привязка, подписи.

    transform - шесть чисел привязки в порядке GDAL: x0, шаг по x,
    поворот, y0, поворот, шаг по y (обычно отрицательный). Канал -
    массив (строки, столбцы) float, пусто - NaN. Каналы задаются
    списком массивов или функцией read(номер) с числом count и формой
    shape: так обёртка QGIS читает только те каналы, что спросил
    расчёт. Номера каналов с единицы, как в QGIS и GDAL.

    meta - метаданные растра строками: у куба там отметка первого
    уровня и шаг по вертикали. crs - система координат строкой WKT,
    расчёт её не трогает, а выход берёт как есть.
    """

    def __init__(self, bands=None, transform=(0.0, 1.0, 0.0, 0.0, 0.0,
                                               -1.0),
                 names=None, meta=None, read=None, count=None, shape=None,
                 crs=""):
        self.transform = tuple(float(x) for x in transform)
        if len(self.transform) != 6:
            raise ValueError("transform needs six numbers")
        if bands is not None:
            self._bands = [np.asarray(b, dtype=float) for b in bands]
            if not self._bands:
                raise ValueError("grid without bands")
            shapes = set(b.shape for b in self._bands)
            if len(shapes) != 1 or len(self._bands[0].shape) != 2:
                raise ValueError("bands of different shapes")
            self.count = len(self._bands)
            self.shape = self._bands[0].shape
        else:
            if read is None or not count or shape is None:
                raise ValueError("grid needs bands or read, count, shape")
            self._bands = [None] * int(count)
            self.count = int(count)
            self.shape = tuple(int(x) for x in shape)
        self._read = read
        self.names = list(names) if names else [""] * self.count
        if len(self.names) != self.count:
            raise ValueError("names do not match bands")
        self.meta = dict(meta or {})
        self.crs = crs or ""

    @property
    def height(self):
        return self.shape[0]

    @property
    def width(self):
        return self.shape[1]

    def band(self, number):
        """Канал по номеру с единицы: массив float, пусто - NaN."""
        if not 1 <= int(number) <= self.count:
            raise IndexError("band %s of %d" % (number, self.count))
        i = int(number) - 1
        if self._bands[i] is None:
            self._bands[i] = np.asarray(self._read(i + 1), dtype=float)
        return self._bands[i]

    def bands(self):
        return [self.band(k) for k in range(1, self.count + 1)]


class RasterResult(object):
    """Выход растром: Grid и имя слоя.

    Обёртка пишет его в GeoTIFF Float32, пусто - NaN, с подписями
    каналов и метаданными. Система координат - у grid.crs, а без неё
    берётся от первого входа.
    """

    geometry = "raster"

    def __init__(self, grid, name=None):
        if not isinstance(grid, Grid):
            raise ValueError("RasterResult needs a Grid")
        self.grid = grid
        self.name = name


class Param(object):
    """Поле инструмента."""

    def __init__(self, key, kind, label, hint, default=None, options=(),
                 optional=False, parent=None, minimum=None, maximum=None,
                 numeric=False, geometry=None, advanced=False,
                 multiple=False):
        if kind not in KINDS:
            raise ValueError("unknown kind %r" % (kind,))
        self.key = key
        self.kind = kind
        self.label = label
        self.hint = hint
        self.default = default
        self.options = tuple(options)
        self.optional = bool(optional)
        self.parent = parent
        self.minimum = minimum
        self.maximum = maximum
        self.numeric = bool(numeric)
        self.geometry = geometry
        self.advanced = bool(advanced)
        self.multiple = bool(multiple)

    def to_dict(self):
        out = {"key": self.key, "kind": self.kind, "label": self.label,
               "hint": self.hint}
        for name in ("default", "parent", "minimum", "maximum",
                     "geometry"):
            val = getattr(self, name)
            if val is not None:
                out[name] = val
        if self.options:
            out["options"] = list(self.options)
        if self.optional:
            out["optional"] = True
        if self.numeric:
            out["numeric"] = True
        if self.advanced:
            out["advanced"] = True
        if self.multiple:
            out["multiple"] = True
        return out


class Result(object):
    """Выход инструмента: поля и объекты.

    fields - [(имя, тип)], тип "double", "int", "string" или
    "copy:КЛЮЧ" - поле с тем же именем и типом, что у слоя КЛЮЧ.
    features - [(геометрия, значения)]; геометрия тела -
    {"verts", "faces"}. keep_source - объекты несут сначала значения
    объекта источника (значения в начале списка), потом свои.
    geometry - "mesh" (тела), "line" (линия с Z, геометрия -
    {"points": (n, 3)}), "point" (точка с Z, геометрия - {"xyz": (3,)})
    или "source": геометрия объекта источника
    keep_source как есть, тогда геометрия - {"source": id}, или
    "none" - таблица без геометрии, геометрия объекта - None.
    replace - свои поля с именем поля источника пишутся в него,
    а не рядом. Значения объекта тогда - словарь {поле: значение}:
    чего в словаре нет, остаётся от источника, у новых полей пусто.
    """

    GEOMETRIES = ("mesh", "line", "point", "source", "none")

    def __init__(self, fields, features=None, keep_source=None,
                 name=None, geometry="mesh", replace=False):
        if geometry not in self.GEOMETRIES:
            raise ValueError("unknown geometry %r" % (geometry,))
        if geometry == "source" and not keep_source:
            raise ValueError("geometry='source' needs keep_source")
        if replace and not keep_source:
            raise ValueError("replace needs keep_source")
        self.fields = list(fields)
        self.features = list(features or [])
        self.keep_source = keep_source
        self.name = name
        self.geometry = geometry
        self.replace = bool(replace)

    def add(self, geometry, values):
        self.features.append((geometry, dict(values) if isinstance(
            values, dict) else list(values)))


class Report(object):
    """Отчёт расчёта: сведения, предупреждения, прогресс, отмена.

    Обёртка QGIS переопределяет методы и передаёт строки в журнал
    Processing. Без QGIS отчёт копит строки в list, так их видят
    тесты.

    Переводится не только шаблон, но и строковые аргументы, у которых
    есть перевод: слово вроде N_("точка") приходит аргументом, и его
    пара лежит в messages файла tools.json. Перенос на другой язык
    обязан делать так же, иначе в английском отчёте останется
    русское слово.
    """

    def __init__(self):
        self.lines = []

    def info(self, template, *args):
        self.lines.append(("info", template % args if args else template))

    def warn(self, template, *args):
        self.lines.append(("warn", template % args if args else template))

    def progress(self, share):
        pass

    def canceled(self):
        return False


class ToolError(Exception):
    """Расчёт невозможен: шаблон сообщения и аргументы для перевода."""

    def __init__(self, template, *args):
        Exception.__init__(self, template % args if args else template)
        self.template = template
        self.args_ = args


class Tool(object):
    """Описание инструмента и его расчёт."""

    def __init__(self, code, name, title, group, help, params, run):
        self.code = code
        self.name = name
        self.title = title
        self.group = group
        self.help = help
        self.params = list(params)
        self.run = run
        keys = [p.key for p in self.params]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate field in %s" % name)
        kinds = dict((p.key, p.kind) for p in self.params)
        for p in self.params:
            if p.kind == "band" and kinds.get(p.parent) != "raster":
                raise ValueError("band %s needs a raster parent" % p.key)

    def display_name(self):
        """Название с кодом, как в панели Processing."""
        return "%s %s" % (self.code, self.title)

    def param(self, key):
        for p in self.params:
            if p.key == key:
                return p
        raise KeyError(key)

    def defaults(self):
        """Значения полей по умолчанию: для тестов и для run без QGIS.

        Необязательный выход по умолчанию не заказан."""
        out = {}
        for p in self.params:
            if p.kind != "sink":
                out[p.key] = list(p.default or []) if p.multiple \
                    else p.default
            elif p.optional:
                out[p.key] = False
        return out

    def to_dict(self):
        return {"code": self.code, "name": self.name, "title": self.title,
                "group": self.group, "help": self.help,
                "params": [p.to_dict() for p in self.params]}
