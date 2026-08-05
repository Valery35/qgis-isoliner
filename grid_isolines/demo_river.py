# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Демо-река: детерминированная долина со створами и эталонной кривой.

Назначение то же, что у остальных демо: материал, на котором ответ
известен заранее. Первый створ построен из простых фигур - у русла
прямоугольное сечение, у пойм наклонные плоскости, - и для него кривая
расходов считается вручную по формулам площадей. Расхождение расчёта с
этой эталонной кривой есть ошибка ядра, а не данных.
"""

import math

import numpy as np

try:
    from . import hydro_section as hs
except ImportError:                      # headless-тесты без пакета
    import hydro_section as hs

# Опорные размеры демо-долины.
CH_WIDTH = 20.0        # ширина русла по дну, м
CH_DEPTH = 2.5         # глубина русла от бровок, м
FP_WIDTH = 60.0        # ширина каждой поймы, м
FP_RISE = 1.5          # подъём поймы от бровки к краю, м
Z_BANK = 100.0         # отметка бровок
N_CHANNEL = 0.030
N_FLOOD = 0.070
SLOPE = 0.0004
KM0 = 10.0             # километраж первого створа


def demo_profile():
    """Профиль первого створа: поймы плоскостями, русло коробом.

    Слева направо: край левой поймы, бровка, дно двумя точками, бровка,
    край правой поймы. Все площади считаются вручную, это и есть основа
    эталона.
    """
    d = [0.0, FP_WIDTH,
         FP_WIDTH, FP_WIDTH + CH_WIDTH,
         FP_WIDTH + CH_WIDTH, 2 * FP_WIDTH + CH_WIDTH]
    z = [Z_BANK + FP_RISE, Z_BANK,
         Z_BANK - CH_DEPTH, Z_BANK - CH_DEPTH,
         Z_BANK, Z_BANK + FP_RISE]
    return np.asarray(d), np.asarray(z)


def demo_fragments():
    """Три фрагмента первого створа с шероховатостями демо."""
    d, z = demo_profile()
    return hs.split_by_divides(d, z, FP_WIDTH, FP_WIDTH + CH_WIDTH,
                               N_FLOOD, N_CHANNEL, N_FLOOD)


def reference_curve(levels):
    """Эталонная кривая первого створа, посчитанная по фигурам.

    Русло - прямоугольник: A = b·h, P = b + 2·h (стенки вертикальны).
    Пойма - треугольник на наклонной плоскости до выхода на край, дальше
    трапеция. Всё по формулам, без ядра: эталон обязан быть независимым.
    """
    out = {"level": np.asarray(levels, float)}
    a_ch, p_ch = [], []
    a_fp, p_fp = [], []
    for lv in levels:
        h = lv - (Z_BANK - CH_DEPTH)
        if h <= 0:
            a_ch.append(0.0)
            p_ch.append(0.0)
        else:
            a_ch.append(CH_WIDTH * h)
            p_ch.append(CH_WIDTH + 2.0 * min(h, CH_DEPTH))
        hf = lv - Z_BANK
        if hf <= 0:
            a_fp.append(0.0)
            p_fp.append(0.0)
        else:
            m = FP_WIDTH / FP_RISE          # заложение откоса поймы
            hh = min(hf, FP_RISE)
            w = m * hh
            a = 0.5 * w * hh + (hf - hh) * FP_WIDTH
            a_fp.append(a)
            p_fp.append(math.hypot(w, hh))
    q_ch = [hs.manning_q(a, p, N_CHANNEL, SLOPE)
            for a, p in zip(a_ch, p_ch)]
    q_fp = [hs.manning_q(a, p, N_FLOOD, SLOPE)
            for a, p in zip(a_fp, p_fp)]
    out["q_channel"] = np.asarray(q_ch)
    out["q_left"] = np.asarray(q_fp)
    out["q_right"] = np.asarray(q_fp)
    out["q_total"] = out["q_channel"] + 2.0 * out["q_left"]
    return out


def demo_sections(n=4, km_step=1.0):
    """Цепочка створов вниз по течению для уклона по километражу.

    Отметки дна убывают согласно SLOPE, километраж растёт. Возвращает
    список dict: km, z_bed, профиль (d, z).
    """
    out = []
    for j in range(n):
        drop = SLOPE * km_step * 1000.0 * j
        d, z = demo_profile()
        out.append({"km": KM0 + km_step * j,
                    "z_bed": float(Z_BANK - CH_DEPTH - drop),
                    "d": d, "z": z - drop})
    return out


def valley_surface(secs, cell=5.0, margin=40.0):
    """Растр долины по цепочке створов: продольно линейно между створами.

    Нужен затоплению: резать отметкой нечего, пока долина существует
    только линиями. Поверхность строится ровно из тех же створов, что и
    кривые, поэтому полигон затопления и кривая расходов говорят об одной
    и той же долине, а не о двух похожих.

    Продольно между створами идёт линейная интерполяция, поперёк берётся
    профиль створа. Модель бедная, но для демо верная: уклон в ней ровно
    тот, что задан, и площадь затопления на заданной отметке считается по
    тем же отметкам, что попали в кривую.

    Возвращает (arr, gt_like), где gt_like это (x0, cell, y0, cell) в
    локальных координатах: начало в левом нижнем углу площади.
    """
    if len(secs) < 2:
        raise ValueError("нужны минимум два створа")
    d0 = np.asarray(secs[0]["d"], float)
    width = float(d0[-1] - d0[0])
    length = float((secs[-1]["km"] - secs[0]["km"]) * 1000.0)
    nx = int(math.ceil((width + 2.0 * margin) / cell)) + 1
    ny = int(math.ceil((length + 2.0 * margin) / cell)) + 1
    xs = np.arange(nx, dtype=float) * cell - margin
    ys = np.arange(ny, dtype=float) * cell - margin
    # продольная координата створов от первого, метры вниз по течению
    pos = np.asarray([(s["km"] - secs[0]["km"]) * 1000.0 for s in secs],
                     float)
    prof = [np.interp(xs, np.asarray(s["d"], float),
                      np.asarray(s["z"], float)) for s in secs]
    arr = np.empty((ny, nx), float)
    for i, y in enumerate(ys):
        yy = min(max(y, pos[0]), pos[-1])
        k = int(np.searchsorted(pos, yy, side="right") - 1)
        k = min(max(k, 0), len(pos) - 2)
        span = pos[k + 1] - pos[k]
        t = 0.0 if span <= 0 else (yy - pos[k]) / span
        arr[i] = prof[k] * (1.0 - t) + prof[k + 1] * t
    return arr, (float(xs[0]), cell, float(ys[0]), cell)


def survey_table(secs, step=None):
    """Таблица промеров: пары расстояние-отметка по каждому створу.

    Тот самый вид, в котором профиль хранят существующие программы, и
    вход для импорта. Отдаётся списком dict с полями sec, dist, elev, km
    - именами из контракта, чтобы импорт подхватил их сам.
    """
    out = []
    for j, s in enumerate(secs):
        d = np.asarray(s["d"], float)
        z = np.asarray(s["z"], float)
        if step:
            dd = np.arange(d[0], d[-1] + 0.5 * step, step)
            zz = np.interp(dd, d, z)
        else:
            dd, zz = d, z
        name = "%s %d" % ("Створ", j + 1)
        # Рядом с профилем существующие программы держат и коэффициент
        # шероховатости, и уклон: без них импорт вернул бы геометрию, но
        # не расчётные свойства, и кривая по восстановленным створам
        # разошлась бы с исходной.
        for a, b in zip(dd, zz):
            out.append({"sec": name, "dist": float(a), "elev": float(b),
                        "km": float(s["km"]),
                        "div_l": FP_WIDTH,
                        "div_r": FP_WIDTH + CH_WIDTH,
                        "n_left": N_FLOOD, "n_channel": N_CHANNEL,
                        "n_right": N_FLOOD, "slope": SLOPE})
    return out


def probability_table(curve, probs=(1.0, 5.0, 10.0), fracs=(0.9, 0.6, 0.35)):
    """Учебные расходы обеспеченности по эталонной кривой.

    Настоящие расходы обеспеченности выходят из статистики рядов
    наблюдений, и считать их не дело плагина. Демо нужно другое: чтобы
    уровни и подвал чертежа было на чём показать, а значения гарантированно
    лежали внутри кривой и не требовали экстраполяции. Поэтому расходы
    берутся долями от наибольшего на кривой, а обеспеченности им
    приписываются условно.
    """
    q = np.asarray(curve["q_total"], float)
    qmax = float(np.max(q))
    return [{"prob": float(p), "q": round(qmax * float(f), 2)}
            for p, f in zip(probs, fracs)]


def observed_levels(secs):
    """Учебные наблюдённые уровни: отметка и подпись с датой.

    На чертеже гидроствора рядом с расчётными уровнями наносят замеренный,
    вида УВ 472.90 X/2021. Это не расчёт, и в демо он такой же условный,
    как расходы обеспеченности: отметка берётся чуть выше бровок русла,
    чтобы линия легла в видимой части профиля.
    """
    z0 = float(secs[0]["z_bed"])
    return [{"level": round(z0 + CH_DEPTH + 0.4, 2), "label": "X/2021"},
            {"level": round(z0 + CH_DEPTH - 0.6, 2), "label": "VII/2024"}]
