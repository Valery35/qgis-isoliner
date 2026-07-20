# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Регрессия на падение QGIS при загрузке выходных слоёв (июль 2026).
#
# Причина была такая: QgsProcessingContext.setPostProcessor передаёт
# владение объектом в C++, и QGIS удаляет постпроцессор после обработки
# слоя. Пока общий на модуль синглтон назначался одному слою за прогон,
# это не проявлялось. Как только инструмент отдал два слоя сразу (демо-
# рельеф с точками створов), один и тот же объект уничтожался дважды -
# двойное освобождение и падение процесса.
#
# Правило, которое проверяет этот тест: у постпроцессоров не должно быть
# общих экземпляров, каждому слою свой объект.
#     python grid_isolines/tests/test_postprocessors.py
import os
import re
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                   "algorithms.py")
with open(SRC, encoding="utf-8") as f:
    CODE = f.read()


def test_no_shared_postprocessor_singletons():
    """Ни один постпроцессор не раздаётся из общего экземпляра."""
    assert ".instance()" not in CODE.replace("QgsProject.instance()", ""), \
        "постпроцессор раздаётся синглтоном: QGIS удалит объект дважды"
    # классовый кэш экземпляра тоже под запретом
    for m in re.finditer(r"class (\w*PostProcessor\w*)\(.*?\n(.*?)(?=\nclass |\ndef )",
                         CODE, re.S):
        body = m.group(2)
        assert "_instance" not in body, \
            "%s хранит общий экземпляр" % m.group(1)


def test_each_setpostprocessor_gets_fresh_object():
    """Объект, отданный в setPostProcessor, создан рядом конструктором.

    Проверка грубая, но ловит именно ту ошибку: имя переменной должно
    присваиваться вызовом конструктора постпроцессора в той же функции,
    а не браться из общего места.
    """
    for m in re.finditer(r"setPostProcessor\((\w+)\)", CODE):
        var = m.group(1)
        head = CODE[:m.start()]
        # ближайшее присваивание этой переменной выше по тексту
        assigns = re.findall(r"\n\s*%s = (\w+)\(" % re.escape(var), head)
        assert assigns, "не найдено присваивание %s" % var
        ctor = assigns[-1]
        assert "PostProcessor" in ctor, \
            "%s получен не конструктором постпроцессора (%s)" % (var, ctor)


def test_keep_alive_holds_reference():
    """Ссылка на постпроцессор остаётся у нас: без неё сборщик мусора
    Python может убить объект раньше, чем QGIS его вызовет."""
    n_set = len(re.findall(r"setPostProcessor\(", CODE))
    n_keep = len(re.findall(r"_KEEP_ALIVE\.(append|extend)\(", CODE))
    assert n_keep >= 1
    assert n_set >= n_keep, "лишние _KEEP_ALIVE без назначения"


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
