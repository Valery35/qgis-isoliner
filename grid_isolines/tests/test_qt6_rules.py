# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Регрессия на проверку Qt6 в каталоге plugins.qgis.org (июль 2026).
#
# Каталог прогоняет загруженную версию через Qt6 Check и блокирует находки.
# Первой поймалась категория-ловушка раскраски: `QgsRendererCategory(
# QVariant(), ...)`. В Qt6 пустой QVariant в значение категории не
# конвертируется, вместо него нужен `NULL` из `qgis.core`.
#
# Тест читает исходники пакета и запрещает конструкции, которые проверка
# каталога считает ошибкой. Дешевле поймать здесь, чем узнать из блокировки
# после заливки.
#     python grid_isolines/tests/test_qt6_rules.py
import os
import re
import sys

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# (регулярное выражение, чем заменять) - только то, что каталог уже ловил
# или ловит по документированным правилам Qt6
BANNED = (
    (r"QVariant\s*\(\s*\)", "NULL из qgis.core"),
    (r"QVariant\.Null", "NULL из qgis.core"),
    (r"QVariant\.Invalid", "NULL из qgis.core"),
)


def _sources():
    for name in sorted(os.listdir(PKG)):
        if name.endswith(".py"):
            path = os.path.join(PKG, name)
            with open(path, encoding="utf-8") as f:
                yield name, f.read()


def _strip_comments(code):
    """Гасит комментарии: в них конструкции упоминаются нарочно, чтобы
    объяснить запрет.

    Строка не выбрасывается, а становится пустой. Прежде она выбрасывалась,
    и номер в сообщении считался по укороченному тексту: он не совпадал с
    номером в файле и уводил читателя не туда.
    """
    out = []
    for line in code.splitlines():
        if line.lstrip().startswith("#"):
            out.append("")
        else:
            out.append(line.split("  #")[0])
    return "\n".join(out)


def test_no_empty_qvariant():
    bad = []
    for name, code in _sources():
        body = _strip_comments(code)
        for pattern, hint in BANNED:
            for m in re.finditer(pattern, body):
                line = body[:m.start()].count("\n") + 1
                bad.append("%s:%d %s -> %s" % (name, line, m.group(0), hint))
    assert not bad, "запрещено проверкой Qt6:\n  " + "\n  ".join(bad)


# Короткие формы enum. В Qt6 значение живёт во вложенном классе, и обращение
# через сам класс проверка каталога считает ошибкой: «add 'Type' before
# 'Double'». В коде такие формы держались годами и работали, поэтому глазами
# они не видны, а находит их только каталог - уже после заливки.
#
# Пара: что запрещено и какой класс надо вставить.
SHORT_ENUMS = (
    (r"QgsProcessingParameterNumber\.(?!Type\.)(Double|Integer)\b", "Type"),
    (r"QgsProcessingParameterDefinition\.(?!Flag\.)(Flag\w+)\b", "Flag"),
    (r"QgsProcessing\.(?!SourceType\.)(Type\w+)\b", "SourceType"),
    (r"QgsProcessingParameterField\.(?!DataType\.)"
     r"(Numeric|String|DateTime|Any)\b", "DataType"),
    (r"QgsProcessingParameterFile\.(?!Behavior\.)(File|Folder)\b", "Behavior"),
)


def test_no_short_enum_forms():
    """Каталог блокирует версию за каждую такую строку.

    Проверка идёт по всему пакету, а не по одному файлу: короткая форма
    заводится там, где пишется новый инструмент, и в прошлый раз это были
    сразу три места в разных группах.
    """
    bad = []
    for name, code in _sources():
        body = _strip_comments(code)
        for pattern, mid in SHORT_ENUMS:
            for m in re.finditer(pattern, body):
                line = body[:m.start()].count("\n") + 1
                bad.append("%s:%d %s - вставить %s перед %s"
                           % (name, line, m.group(0), mid, m.group(1)))
    assert not bad, (
        "короткая форма enum, каталог такую версию не примет:\n  "
        + "\n  ".join(bad))


def test_long_enum_forms_are_not_flagged():
    """Контроль: правильная запись не должна ловиться."""
    sample = ("QgsProcessingParameterNumber.Type.Double\n"
              "QgsProcessingParameterDefinition.Flag.FlagAdvanced\n"
              "QgsProcessing.SourceType.TypeVectorPoint\n"
              "QgsProcessingParameterField.DataType.Numeric\n")
    for pattern, _mid in SHORT_ENUMS:
        assert not re.search(pattern, sample), pattern


# Фабрики QgsGeometry, снятые в QGIS 3. Код с ними разбирается только в
# момент выполнения, поэтому строка может пролежать в редко вызываемой ветке
# годами. Так и вышло с поверхностью LandXML: `fromPolygon` уехал в 5.13.5, а
# упал на первом же настоящем файле в 5.13.15.
#
# Пара: что запрещено и чем заменять. Для плоской геометрии это XY-форма, для
# геометрии с отметками кольцо через QgsPolygon, иначе Z срежется.
GONE_FACTORIES = (
    (r"QgsGeometry\.fromPolygon\s*\(", "QgsPolygon с setExteriorRing "
     "(или fromPolygonXY, если отметки не нужны)"),
    (r"QgsGeometry\.fromMultiPolygon\s*\(", "QgsMultiPolygon или "
     "fromMultiPolygonXY"),
    (r"QgsGeometry\.fromMultiPolyline\s*\(", "QgsMultiLineString или "
     "fromMultiPolylineXY"),
    (r"QgsGeometry\.fromMultiPoint\s*\(", "fromMultiPointXY"),
    (r"QgsGeometry\.fromPoint\s*\(", "fromPointXY (QgsPoint принимает "
     "конструктор QgsGeometry)"),
)


def test_no_removed_geometry_factories():
    """Имени нет у класса, и выясняется это только на прогоне.

    `fromPolyline` не запрещён: он существует и берёт QgsPoint с отметкой.
    Запрещены именно те имена, которых у QgsGeometry нет вовсе.
    """
    bad = []
    for name, code in _sources():
        body = _strip_comments(code)
        for pattern, hint in GONE_FACTORIES:
            for m in re.finditer(pattern, body):
                line = body[:m.start()].count("\n") + 1
                bad.append("%s:%d %s -> %s"
                           % (name, line, m.group(0).strip("("), hint))
    assert not bad, ("такого метода у QgsGeometry нет, упадёт на прогоне:\n  "
                     + "\n  ".join(bad))


def test_live_factories_are_not_flagged():
    """Контроль: существующие фабрики ловиться не должны."""
    sample = ("QgsGeometry.fromPolylineXY(pts)\n"
              "QgsGeometry.fromPolyline(pts)\n"
              "QgsGeometry.fromPolygonXY(rings)\n"
              "QgsGeometry.fromPointXY(p)\n"
              "QgsGeometry.fromWkt(s)\n")
    for pattern, _hint in GONE_FACTORIES:
        assert not re.search(pattern, sample), pattern


def test_the_landxml_case_is_caught():
    """Строка, на которой упал 8.01, обязана ловиться."""
    sample = "f.setGeometry(QgsGeometry.fromPolygon([QgsLineString(ring)]))"
    assert any(re.search(p, sample) for p, _h in GONE_FACTORIES)


def test_qvariant_types_still_allowed():
    """Типы полей (QVariant.String и подобные) проверкой не запрещены:
    тест не должен ловить их и мешать работе."""
    sample = 'QgsField("a", QVariant.String)\nQgsField("b", QVariant.Double)'
    for pattern, _hint in BANNED:
        assert not re.search(pattern, sample), pattern


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL %s: %s" % (name, exc))
    print("%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_run())
