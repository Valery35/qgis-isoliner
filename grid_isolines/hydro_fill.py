# -*- coding: utf-8 -*-
"""Заполнение понижений по Planchon-Darboux (2001) с epsilon.

Чистый NumPy. Вместо параллельной итерации Якоби используем четыре
направленных прохода (сверху вниз, снизу вверх, слева направо,
справа налево). Каждый проход идёт последовательно вдоль своей оси
и векторизован по перпендикулярной, при этом ячейка видит три
соседа из уже обработанной линии, включая диагонали. Вода стекает
через весь грид за один проход, поэтому сходимость занимает
единицы полных циклов, а не тысячи итераций.

Epsilon задаёт минимальный уклон на плоских участках, чтобы поток
не останавливался. Ячейки на границе грида и ячейки, соседние
с nodata, считаются стоками: через них вода уходит с грида.
"""

import numpy as np

DEFAULT_EPSILON = 0.001
DEFAULT_MAX_PASSES = 100
_BIG = np.float64(1e30)


def _sweep(w, z, interior, eps, axis, reverse):
    """Один направленный проход. Меняет w на месте, возвращает w.

    axis=0: проход по строкам (вертикальное направление),
    axis=1: по столбцам (горизонтальное). reverse задаёт направление.
    """
    if axis == 1:
        w = w.T
        z = z.T
        interior = interior.T
    n = w.shape[0]
    lines = range(n - 2, -1, -1) if reverse else range(1, n)
    step = 1 if reverse else -1
    for i in lines:
        prev = w[i + step]
        nmin = prev.copy()
        nmin[1:] = np.minimum(nmin[1:], prev[:-1])
        nmin[:-1] = np.minimum(nmin[:-1], prev[1:])
        cand = nmin + eps
        line_new = np.where(z[i] >= cand, z[i], np.minimum(w[i], cand))
        w[i] = np.where(interior[i], line_new, w[i])
    return w.T if axis == 1 else w


def fill_depressions(z, nodata_mask=None, epsilon=DEFAULT_EPSILON,
                     max_passes=DEFAULT_MAX_PASSES, feedback=None):
    """Возвращает (заполненный_грид, число_поднятых_ячеек, макс_подъём).

    z: 2D float ndarray. nodata_mask: True там, где данных нет.
    Значения в nodata-ячейках не меняются и на соседей не влияют.
    """
    z_in = np.asarray(z, dtype=np.float64)
    if z_in.ndim != 2 or z_in.shape[0] < 2 or z_in.shape[1] < 2:
        raise ValueError("Ожидается двумерный грид не меньше 2x2.")
    eps = np.float64(epsilon)
    if eps < 0:
        raise ValueError("Epsilon не может быть отрицательным.")

    if nodata_mask is None:
        nodata_mask = ~np.isfinite(z_in)
    else:
        nodata_mask = np.asarray(nodata_mask, dtype=bool) | ~np.isfinite(z_in)
    valid = ~nodata_mask

    # Стоки: граница грида и валидные ячейки, соседние с nodata.
    outlets = np.zeros(z_in.shape, dtype=bool)
    outlets[0, :] = outlets[-1, :] = True
    outlets[:, 0] = outlets[:, -1] = True
    pad = np.pad(nodata_mask, 1, constant_values=False)
    near_nodata = np.zeros(z_in.shape, dtype=bool)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di == 0 and dj == 0:
                continue
            near_nodata |= pad[1 + di:1 + di + z_in.shape[0],
                               1 + dj:1 + dj + z_in.shape[1]]
    outlets = (outlets | near_nodata) & valid
    interior = valid & ~outlets

    z_work = np.where(nodata_mask, -_BIG, z_in)
    w = np.where(outlets | nodata_mask, z_work, _BIG)

    converged = False
    for n_pass in range(1, int(max_passes) + 1):
        before = w.copy()
        w = _sweep(w, z_work, interior, eps, axis=0, reverse=False)
        w = _sweep(w, z_work, interior, eps, axis=0, reverse=True)
        w = _sweep(w, z_work, interior, eps, axis=1, reverse=False)
        w = _sweep(w, z_work, interior, eps, axis=1, reverse=True)
        if np.array_equal(before, w):
            converged = True
            if feedback:
                feedback.pushInfo(
                    "Заполнение сошлось за {} проходов.".format(n_pass))
            break
    if not converged and feedback:
        feedback.pushInfo(
            "Достигнут предел {} проходов, результат может быть "
            "неполным на очень сложном рельефе.".format(max_passes))

    filled = np.where(valid, w, z_in)
    diff = filled - np.where(valid, z_in, filled)
    raised = valid & (diff > 1e-9)
    n_raised = int(np.count_nonzero(raised))
    max_raise = float(diff[raised].max()) if n_raised else 0.0
    return filled, n_raised, max_raise
