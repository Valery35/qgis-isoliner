# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Складчатость: дисперсия со снятым наклоном и рост её с масштабом."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fold  # noqa: E402


def _plane(ny=60, nx=80):
    i, j = np.mgrid[0:ny, 0:nx]
    return 100.0 + 0.3 * j - 0.2 * i


def test_plane_has_no_folding():
    """На наклонной плоскости складчатости нет вовсе.

    Прямая дисперсия отметок на таком склоне велика, и в этом вся суть
    подгонки плоскости: наклон уходит, извилистость остаётся. Тест
    сторожит именно это, потому что на первой редакции формула считала
    суммы по идеальному окну и у края врала.
    """
    v = fold.detrended_variance(_plane(), 9)
    assert float(np.nanmax(v)) < 1e-6


def test_fold_shows_up():
    """Смятая поверхность даёт заметный разброс остатков."""
    j = np.mgrid[0:60, 0:80][1]
    z = _plane() + 3.0 * np.sin(j / 4.0)
    v = fold.detrended_variance(z, 9)
    assert float(np.nanmedian(v)) > 1e-3


def test_slope_by_scale_separates_the_cases():
    """Наклон по масштабу различает спокойное и смятое.

    Одно окно о складчатости не говорит: дисперсия зависит от масштаба, и
    мерой служит скорость её роста.
    """
    j = np.mgrid[0:60, 0:80][1]
    z = _plane() + 3.0 * np.sin(j / 4.0)
    quiet = fold.scale_slope(_plane(), (5, 9, 15, 25))
    rough = fold.scale_slope(z, (5, 9, 15, 25))
    assert float(np.nanmedian(rough)) > float(np.nanmedian(quiet)) + 1.0


def test_detrend_removes_the_level_inside_the_field():
    """Внутри поля остаток наклонной плоскости обращается в ноль.

    У края окно добирается отражением, и там остаток ненулевой по
    построению: проверять надо внутреннюю часть, а не всё поле.
    """
    j = np.mgrid[0:40, 0:40][1]
    z = 100.0 + 0.5 * j
    r = fold.detrend(z, 9)
    inner = r[8:-8, 8:-8]
    assert float(np.nanmax(np.abs(inner))) < 1e-9
    assert np.isfinite(r).all()


def test_boxsum_matches_direct_sum():
    """Сумма по окну совпадает с прямым подсчётом внутри поля."""
    rng = np.random.default_rng(3)
    a = rng.normal(size=(20, 25))
    s = fold.boxsum(a, 5)
    i, j = 10, 12
    assert abs(float(s[i, j]) - float(a[i - 2:i + 3, j - 2:j + 3].sum())) < 1e-9


def _run():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in fns:
        try:
            fn()
            print("ok: %s" % name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("FAIL: %s - %s" % (name, exc))
    print("\n%d тестов, ошибок %d" % (len(fns), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_run())
