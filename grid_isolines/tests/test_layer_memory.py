# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Память входных слоёв: мёртвые ссылки не подставляются.

Зачем этот тест. Память слоёв хранит id, а id живёт внутри проекта. Если
запомненный слой удалён или проект другой, id остаётся мёртвым. Подставить
его значением по умолчанию нельзя: комбо в диалоге выглядит пустым, а
значение внутри непустое и нерабочее, и запуск падает с сообщением
«некорректное значение» без видимой причины. Так и случилось в 4.18.2 на
параметре POINTS инструмента 2.03.

Живой QGIS в безголовом прогоне недоступен, поэтому проверяем текстом
исходника, что страж на месте: _restore_layer_defaults пропускает значение
через _alive_layer_ref прежде чем звать setDefaultValue, а сам страж
проверяет и id в проекте, и путь на диске.

    python grid_isolines/tests/test_layer_memory.py
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULE = os.path.dirname(HERE)
SRC = os.path.join(MODULE, "algorithms.py")


def _func_body(text, name):
    """Тело функции верхнего уровня по имени, до следующего def/класса."""
    m = re.search(r"^def %s\(.*?(?=^def |^class )" % re.escape(name),
                  text, re.S | re.M)
    assert m, "не найдена функция %s" % name
    return m.group(0)


def test_guard_called_before_default():
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    body = _func_body(text, "_restore_layer_defaults")
    pos_guard = body.find("_alive_layer_ref(")
    pos_set = body.find("setDefaultValue(")
    assert pos_guard != -1, "страж _alive_layer_ref не зовётся"
    assert pos_set != -1, "setDefaultValue пропал из восстановления"
    assert pos_guard < pos_set, "страж должен стоять до setDefaultValue"


def test_guard_checks_project_and_path():
    with open(SRC, encoding="utf-8") as f:
        text = f.read()
    body = _func_body(text, "_alive_layer_ref")
    assert "mapLayer(" in body, "страж не проверяет id в текущем проекте"
    assert "os.path.exists(" in body, "страж не проверяет путь на диске"
    assert "isinstance(v, list)" in body, "страж не чистит списки слоёв"


if __name__ == "__main__":
    test_guard_called_before_default()
    test_guard_checks_project_and_path()
    print("test_layer_memory: OK")
