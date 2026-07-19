# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Сглаживание с ограничением (лечение террасинга):
#     python grid_isolines/tests/test_smooth_clamped.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", ".."))

from grid_isolines import validate_core as vc  # noqa: E402
from grid_isolines.topo_smooth import smooth_clamped  # noqa: E402

CELL = 10.0
IV = 5.0


def _true_surface(n=180):
    y, x = np.mgrid[0:n, 0:n]
    r = np.hypot(x - n / 2.0, y - n / 2.0) * CELL
    return 300.0 - 0.06 * r + 6.0 * np.sin(x / 25.0) * np.cos(y / 30.0)


def _terraced(n=180):
    return np.round(_true_surface(n) / IV) * IV


# --- главное свойство ----------------------------------------------------

def test_terracing_is_cured():
    """Диагноз должен сниматься нашим же прибором."""
    terr = _terraced()
    before = vc.terracing_stats(terr, CELL, IV)["attract_ratio"]
    after = vc.terracing_stats(smooth_clamped(terr, IV, iters=50),
                               CELL, IV)["attract_ratio"]
    assert before > 3.0, before
    assert after < 1.5, after


def test_surface_gets_closer_to_truth():
    """Лечение должно приближать к истине, а не просто размазывать."""
    true_z = _true_surface()
    terr = _terraced()
    e0 = float(np.mean(np.abs(terr - true_z)))
    e1 = float(np.mean(np.abs(smooth_clamped(terr, IV, iters=50) - true_z)))
    assert e1 < 0.5 * e0, (e0, e1)


# --- ограничение ---------------------------------------------------------

def test_shift_never_exceeds_half_interval():
    """Ключевая гарантия: точка не уходит дальше половины сечения.

    Отсюда следует, что горизонталь не может перескочить на соседний уровень.
    """
    terr = _terraced()
    for it in (10, 50, 200):
        z = smooth_clamped(terr, IV, iters=it)
        assert np.max(np.abs(z - terr)) <= 0.5 * IV + 1e-9, it


def test_band_parameter_tightens_the_clamp():
    terr = _terraced()
    z = smooth_clamped(terr, IV, iters=100, band=0.1)
    assert np.max(np.abs(z - terr)) <= 0.1 * IV + 1e-9


def test_zero_band_changes_nothing():
    terr = _terraced()
    assert np.allclose(smooth_clamped(terr, IV, iters=50, band=0.0), terr)


def test_zero_iterations_is_identity():
    terr = _terraced()
    z = smooth_clamped(terr, IV, iters=0)
    assert np.array_equal(z, terr)
    assert z is not terr, "вход менять нельзя"


def test_input_is_not_modified():
    terr = _terraced()
    copy = terr.copy()
    smooth_clamped(terr, IV, iters=20)
    assert np.array_equal(terr, copy)


# --- поведение на краях и без данных ------------------------------------

def test_flat_surface_stays_flat():
    z = np.full((40, 40), 123.0)
    assert np.allclose(smooth_clamped(z, IV, iters=30), 123.0)


def test_plane_stays_plane():
    """На линейном склоне сглаживание не должно ничего менять по существу."""
    n = 60
    _j, i = np.mgrid[0:n, 0:n]
    z = 100.0 + 0.02 * i * CELL
    out = smooth_clamped(z, IV, iters=30)
    # Порог под float32: расчёт ведётся в одинарной точности ради памяти.
    # 1e-4 м это одна десятая миллиметра, для рельефа абсолютный ноль. Смысл
    # теста в другом: у линейного склона не должно быть систематического
    # загиба у края растра, а он давал бы миллиметры и рос бы с итерациями.
    assert np.max(np.abs(out - z)) < 1e-4


def test_nodata_is_preserved_and_does_not_leak():
    terr = _terraced(80)
    mask = np.zeros(terr.shape, dtype=bool)
    mask[:10, :] = True
    z = terr.copy()
    z[mask] = np.nan
    out = smooth_clamped(z, IV, iters=30, nodata_mask=mask)
    assert np.all(np.isnan(out[mask])), "область без данных должна остаться пустой"
    assert np.all(np.isfinite(out[~mask])), "данные не должны испортиться"
    assert np.max(np.abs(out[~mask] - terr[~mask])) <= 0.5 * IV + 1e-9


def test_all_nodata_returns_input():
    z = np.full((20, 20), np.nan)
    out = smooth_clamped(z, IV, iters=10)
    assert np.all(np.isnan(out))


def test_is_deterministic():
    terr = _terraced(100)
    a = smooth_clamped(terr, IV, iters=25)
    b = smooth_clamped(terr, IV, iters=25)
    assert np.array_equal(a, b)


def test_bad_interval_raises():
    try:
        smooth_clamped(_terraced(40), 0.0)
    except ValueError:
        return
    raise AssertionError("нулевое сечение должно приводить к ошибке")


# --- честная оговорка ----------------------------------------------------

def test_does_not_invent_lost_detail():
    """Сглаживание не возвращает того, чего в данных нет.

    Срезанный узкий врез не восстанавливается: правка ограничена половиной
    сечения, а врез глубже. Проверяем прямо, чтобы обещание в документации
    подтверждалось кодом.
    """
    n = 120
    y, x = np.mgrid[0:n, 0:n]
    base = 200.0 - 0.02 * y * CELL
    gully = base - 20.0 * np.exp(-((x - n / 2.0) ** 2) / (2.0 * 2.0 ** 2))
    lost = base.copy()                      # врез потерян при построении
    out = smooth_clamped(lost, IV, iters=60)
    depth = float(np.max(base - out))
    assert depth < 0.5 * IV + 1e-9, depth
    assert float(np.max(base - gully)) > 15.0


# --- обвязка -------------------------------------------------------------

def _alg_source():
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "algorithms.py")
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def test_tool_reports_before_and_after():
    """Лечение обязано быть самопроверяемым: индекс считается дважды."""
    src = _alg_source()
    i = src.index("class TerraceSmoothAlgorithm(")
    seg = src[i:src.index("ALGORITHMS = [", i)]
    assert "before = _vc.terracing_stats(" in seg
    assert "after = _vc.terracing_stats(" in seg
    assert "_write_smooth_report(" in seg


def test_report_function_exists():
    src = _alg_source()
    assert "def _write_smooth_report(" in src
    i = src.index("def _write_smooth_report(")
    seg = src[i:i + 4000]
    # в отчёт должны попасть обе цифры и предел сдвига
    assert "attract_ratio" in seg
    assert "наибольший сдвиг" in seg


def test_diagnosis_points_to_the_cure():
    """Вердикт 2.13 должен называть средство, а не только болезнь."""
    src = _alg_source()
    assert src.count("Лечится инструментом 2.14") >= 1


def test_diagnosis_and_cure_stay_separate_tools():
    """Слияние инструментов недопустимо: диагноз должен быть независим.

    Проверять результат лечения тем же запуском, который его и сделал,
    нельзя, поэтому 2.13 обязан оставаться отдельным алгоритмом.
    """
    src = _alg_source()
    assert "class TerracingCheckAlgorithm(" in src
    assert "class TerraceSmoothAlgorithm(" in src
    i = src.index("class TerracingCheckAlgorithm(")
    seg = src[i:src.index("class ", i + 10)]
    assert "smooth_clamped" not in seg, "диагностика не должна править данные"


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
