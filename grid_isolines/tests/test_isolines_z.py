# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Флажок «Записать высоту в Z геометрии» в 1.04. Пришёл из вопроса в QGIS
# Курилке про экспорт изолиний в DXF с высотой: без Z в геометрии АвтоКАД
# и Кредо кладут горизонтали на нулевую отметку. QGIS в контейнере нет,
# поэтому проверяем то, что проверяется статикой по исходникам.
#     python grid_isolines/tests/test_isolines_z.py
import os
import re
import sys

PKG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


def _read(name):
    with open(os.path.join(PKG, name), encoding="utf-8") as f:
        return f.read()


def test_function_exists_and_uses_linestringz():
    code = _read("isolines.py")
    assert "def add_z_from_field(" in code
    # поднимаем именно в Z, не оставляем плоским
    body = code[code.index("def add_z_from_field("):]
    assert "LineStringZ" in body
    assert "QgsPoint(" in body and ".z(" not in body  # Z из поля, не из точки
    # исходный плоский слой не портим: пишем рядом _z
    assert "_z" in body


def test_z_is_constant_per_line():
    """Изолиния это линия равного уровня: Z у всех вершин один, из поля."""
    body = _read("isolines.py")
    body = body[body.index("def add_z_from_field("):]
    # одно чтение z на объект, затем оно ставится всем точкам
    assert re.search(r"z = float\(feat\[idx\]\)", body)
    assert re.search(r"QgsPoint\(p\.x\(\), p\.y\(\), z\)", body)


def test_flag_wired_into_both_branches():
    code = _read("algorithms.py")
    # флажок объявлен и прочитан
    assert 'ADD_Z = "ADD_Z"' in code
    assert "add_z = self.parameterAsBoolean(parameters, self.ADD_Z" in code
    # вызван в обеих ветках 1.04 (с полигонами и без)
    assert code.count("add_z_from_field(") == 2
    # по умолчанию выключен: плоский слой остаётся поведением по умолчанию
    assert "alg.ADD_Z, False" in code or "self.ADD_Z, False" in code


def test_default_field_used_as_fallback():
    code = _read("algorithms.py")
    assert "field_name or DEFAULT_FIELD" in code


def _load_helper():
    """Исполнить исходник _move_load_on_completion отдельно от QGIS.

    Функция работает только со словарём отложенной загрузки и своих
    импортов из qgis не имеет, поэтому её можно проверить по-настоящему,
    а не статикой.
    """
    code = _read("algorithms.py")
    start = code.index("def _move_load_on_completion(")
    tail = code[start:]
    end = tail.index("\ndef ", 1)
    ns = {}
    exec(compile(tail[:end], "helper", "exec"), ns)  # nosec
    return ns["_move_load_on_completion"]


class _StubContext(object):
    """Минимальный контекст Processing: словарь путь -> детали загрузки."""

    def __init__(self, pending):
        self._pending = dict(pending)

    def layersToLoadOnCompletion(self):
        return dict(self._pending)

    def setLayersToLoadOnCompletion(self, pending):
        self._pending = dict(pending)


def test_move_load_on_completion_rewires_path():
    """Детали загрузки переезжают на новый путь, старый снимается.

    Без этого Processing грузит в проект плоский исходный файл, а
    Z-версия остаётся лежать на диске незамеченной.
    """
    move = _load_helper()
    details = object()
    ctx = _StubContext({"/tmp/iso.gpkg": details, "/tmp/poly.gpkg": "poly"})
    assert move(ctx, "/tmp/iso.gpkg", "/tmp/iso_z.gpkg") is True
    got = ctx.layersToLoadOnCompletion()
    assert "/tmp/iso.gpkg" not in got      # оба слоя в дерево не приедут
    assert got["/tmp/iso_z.gpkg"] is details
    assert got["/tmp/poly.gpkg"] == "poly"  # чужой выход не тронут


def test_move_load_on_completion_noop_cases():
    """Молчаливый отказ там, где переносить нечего."""
    move = _load_helper()
    ctx = _StubContext({"/tmp/iso.gpkg": "d"})
    assert move(ctx, "/tmp/iso.gpkg", "/tmp/iso.gpkg") is False   # путь тот же
    assert move(ctx, "/tmp/iso.gpkg", "") is False                # нет нового
    assert move(ctx, "/tmp/other.gpkg", "/tmp/x.gpkg") is False   # не грузится
    assert ctx.layersToLoadOnCompletion() == {"/tmp/iso.gpkg": "d"}


def test_z_layer_is_the_one_loaded():
    """Обе ветки 1.04 перевешивают загрузку сразу после записи Z."""
    code = _read("algorithms.py")
    assert code.count("_move_load_on_completion(context, out, out_z)") == 2
    # перевешиваем до того, как ставим имя и стиль: и то и другое стоит
    # под проверкой willLoadLayerOnCompletion и иначе не сработает
    i = code.index("_move_load_on_completion(context, out, out_z)")
    tail = code[i:]
    assert tail.index("out = out_z") < tail.index("_set_output_name")


def test_multipart_preserved():
    """Тип выхода берётся от входа: мультилинии не рассыпаются на части."""
    body = _read("isolines.py")
    body = body[body.index("def add_z_from_field("):]
    assert "isMultiType" in body
    assert "MultiLineStringZ" in body
    assert "QgsMultiLineString()" in body
    # одиночный тип по-прежнему пишется одиночным
    assert "QgsWkbTypes.Type.LineStringZ" in body


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


def test_belt_polygon_is_flat():
    """Кольцо контурного полигона лежит на одной отметке.

    Отметки вдоль кольца пробовали дважды: порогом по середине пояса и
    выборкой самой поверхности. Оба раза на сцене выходили ленты,
    уезжающие по высоте. Неплоское кольцо движок триангулирует в плане,
    и между вершинами разной высоты натягиваются длинные тонкие
    треугольники через весь пояс. У тел поясов крышка плоская, высоту
    держат стенки, и с ними этого не происходит.
    """
    code = _read("isolines.py")
    body = code[code.index("def _polygon_with_z("):]
    body = body[:body.index("\ndef ")]
    # одна отметка на весь полигон, а не выборка по вершинам
    assert "z = float(z_level)" in body
    assert "sample_grid_points" not in body and "vertex_levels" not in body
    assert body.count("QgsPoint(") == 1


def test_belt_polygon_sits_on_the_upper_bound():
    """Полигон кладётся на ту же отметку, что верхняя крышка тела.

    Тело собирается из mins[idx] и maxs[idx], верхняя крышка на maxs.
    Полигон на том же уровне ложится на тело без зазора.
    """
    code = _read("isolines.py")
    assert "_polygon_with_z(gg, float(maxs[idx]))" in code
    assert "z_lo, z_hi = float(mins[idx]), float(maxs[idx])" in code
