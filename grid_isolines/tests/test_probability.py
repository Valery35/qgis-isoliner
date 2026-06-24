# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Тесты карты вероятности превышения. Движок не зависит от QGIS:
#     python grid_isolines/tests/test_probability.py
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kb2d  # noqa: E402


def test_norm_cdf_known_values():
    """Φ(z) совпадает с табличными значениями (точность аппроксимации)."""
    z = np.array([-3.0, -1.96, -1.0, 0.0, 1.0, 1.96, 3.0])
    exp = np.array([0.0013499, 0.0249979, 0.1586553, 0.5,
                    0.8413447, 0.9750021, 0.9986501])
    assert np.allclose(kb2d.norm_cdf(z), exp, atol=2e-6)


def test_norm_cdf_symmetry_and_range():
    z = np.linspace(-5, 5, 101)
    c = kb2d.norm_cdf(z)
    assert np.all(c >= 0) and np.all(c <= 1)
    assert np.allclose(c + kb2d.norm_cdf(-z), 1.0, atol=2e-6)   # Φ(z)+Φ(−z)=1
    assert np.all(np.diff(c) >= -1e-12)                         # монотонна


def test_exceedance_at_threshold_is_half():
    est = np.array([10.0, 10.0, 10.0])
    se = np.array([1.0, 5.0, 0.3])
    p = kb2d.exceedance_prob(est, se, 10.0, above=True)
    assert np.allclose(p, 0.5, atol=2e-6)        # оценка = порог -> 0.5


def test_exceedance_monotone_in_estimate():
    est = np.array([0.0, 5.0, 10.0, 15.0, 20.0])
    se = np.full(5, 3.0)
    p = kb2d.exceedance_prob(est, se, 10.0, above=True)
    assert np.all(np.diff(p) > 0)                # выше оценка -> выше P(Z>t)


def test_exceedance_sides_complement():
    rng = np.random.default_rng(0)
    est = rng.uniform(-5, 25, 200)
    se = rng.uniform(0.5, 6.0, 200)
    a = kb2d.exceedance_prob(est, se, 10.0, above=True)
    b = kb2d.exceedance_prob(est, se, 10.0, above=False)
    assert np.allclose(a + b, 1.0, atol=1e-9)


def test_exceedance_zero_error_is_step():
    """Нулевая ошибка - вырожденное распределение, ступенька 0/1."""
    est = np.array([12.0, 8.0, 10.0])
    se = np.array([0.0, 0.0, 0.0])
    p = kb2d.exceedance_prob(est, se, 10.0, above=True)
    assert p[0] == 1.0 and p[1] == 0.0          # выше/ниже порога
    assert p[2] == 0.0                          # ровно порог: est>t ложно


def test_exceedance_range():
    rng = np.random.default_rng(1)
    est = rng.normal(0, 50, 1000)
    se = rng.uniform(0.1, 30, 1000)
    p = kb2d.exceedance_prob(est, se, 0.0, above=True)
    assert float(p.min()) >= 0.0 and float(p.max()) <= 1.0


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok:", fn.__name__)
    print("\nALL %d TESTS PASSED" % len(fns))


if __name__ == "__main__":
    _run_all()
