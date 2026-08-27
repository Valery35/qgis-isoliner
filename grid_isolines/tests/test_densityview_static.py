# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Статические сторожа окна «Карта плотности». Окно живёт в QGIS, и в
# контейнере его не запустить, поэтому проверяется устройство кода: обе
# правки отзывчивости держатся на паре строк, которые легко потерять при
# переработке окна.
import io
import os

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = io.open(os.path.join(HERE, "..", "densityview.py"),
              encoding="utf-8").read()


def test_busy_tick_is_not_lost():
    """Тик, пришедший во время пересчёта, перезапускает предпросмотр.

    Иначе последний поворот ручки теряется и картинка отстаёт: пересчёт
    занят, return, а нового тика не будет.
    """
    assert "self._again = True" in SRC
    body = SRC[SRC.index("def _preview"):]
    body = body[:body.index("def _compute_preview")]
    assert "_again" in body and "_timer.start()" in body


def test_aux_raster_is_cached_per_grid():
    """Вспомогательный растр не перечитывается целиком на каждый тик.

    ReadAsArray всего файла под ползунком - это диск каждые 180 мс; ключ
    кэша включает слой и сетку, поэтому смена того или другого честно
    перечитывает.
    """
    body = SRC[SRC.index("def _aux_grid"):]
    body = body[:body.index("def ", 10)]
    assert "_aux_cache" in body
    for part in ("lid", "gs.xmin", "gs.ymin", "gs.cell", "gs.nx", "gs.ny"):
        assert part in body, part
