# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Отметки линиям форм с примыкающих горизонталей: ядро.

Постановка (пятая редакция, раздел «Что подаётся», приоритет 3). У линии
откоса в топографическом чертеже своей отметки нет, но горизонтали по
нормативу доводятся до линии описания объекта с формированием узловых
точек. Значит на бровке сидит набор точек, у каждой отметка своей
горизонтали, и по ним восстанавливается профиль линии - переменная
отметка из самих данных, а не одна на всю линию.

Механика: для каждой линии формы собираются места встречи с горизонталями
(пересечения сегментов и концы горизонталей в допуске), каждая даёт пару
(дуговая координата, отметка), профиль интерполируется вдоль линии по
дуге. Работает ровно настолько, насколько комплект топологически
согласован: нет доведённых горизонталей - нет точек - линия остаётся
немой и уходит в отчёт с причиной.
"""

import numpy as np


def _cum_length(pts):
    """Дуговые координаты вершин ломаной."""
    d = np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))
    return np.concatenate([[0.0], np.cumsum(d)])


def _seg_intersect(p1, p2, p3, p4):
    """Пересечение отрезков p1-p2 и p3-p4. Возвращает (t, u) или None:
    t - доля на первом отрезке, u - на втором."""
    d1x, d1y = p2[0] - p1[0], p2[1] - p1[1]
    d2x, d2y = p4[0] - p3[0], p4[1] - p3[1]
    den = d1x * d2y - d1y * d2x
    if abs(den) < 1e-12:
        return None
    t = ((p3[0] - p1[0]) * d2y - (p3[1] - p1[1]) * d2x) / den
    u = ((p3[0] - p1[0]) * d1y - (p3[1] - p1[1]) * d1x) / den
    if -1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9:
        return (min(max(t, 0.0), 1.0), min(max(u, 0.0), 1.0))
    return None


def _point_to_polyline(pt, pts, cum):
    """Расстояние от точки до ломаной и дуговая координата проекции."""
    best_d, best_s = np.inf, 0.0
    for i in range(len(pts) - 1):
        ax, ay = pts[i]
        bx, by = pts[i + 1]
        vx, vy = bx - ax, by - ay
        L2 = vx * vx + vy * vy
        if L2 < 1e-18:
            continue
        t = ((pt[0] - ax) * vx + (pt[1] - ay) * vy) / L2
        t = min(max(t, 0.0), 1.0)
        px, py = ax + t * vx, ay + t * vy
        d = np.hypot(pt[0] - px, pt[1] - py)
        if d < best_d:
            best_d = d
            best_s = cum[i] + t * np.hypot(vx, vy)
    return best_d, best_s


def gather_samples(line_pts, contours, tol):
    """Точки встречи линии формы с горизонталями.

    line_pts - (N, 2) вершины линии формы;
    contours - список dict(pts=(M, 2), z=отметка горизонтали);
    tol - допуск примыкания, в единицах координат.

    Возвращает список (s, z): дуговая координата на линии формы и отметка.
    Берутся два вида встреч. Пересечение сегментов - когда горизонталь
    проведена через линию насквозь. Конец горизонтали в допуске - когда
    она доведена до линии узловой точкой, как требует норматив; допуск
    прощает несовпадение оцифровки.
    """
    line_pts = np.asarray(line_pts, dtype=np.float64)
    cum = _cum_length(line_pts)
    out = []
    for ct in contours:
        z = ct.get("z")
        if z is None:
            continue
        cpts = np.asarray(ct["pts"], dtype=np.float64)
        if len(cpts) < 1:
            continue
        # грубая отбраковка по прямоугольникам с запасом допуска
        if (cpts[:, 0].max() < line_pts[:, 0].min() - tol or
                cpts[:, 0].min() > line_pts[:, 0].max() + tol or
                cpts[:, 1].max() < line_pts[:, 1].min() - tol or
                cpts[:, 1].min() > line_pts[:, 1].max() + tol):
            continue
        hit = False
        for j in range(len(cpts) - 1):
            for i in range(len(line_pts) - 1):
                r = _seg_intersect(line_pts[i], line_pts[i + 1],
                                   cpts[j], cpts[j + 1])
                if r is not None:
                    t, _u = r
                    seg = np.hypot(*(line_pts[i + 1] - line_pts[i]))
                    out.append((cum[i] + t * seg, float(z)))
                    hit = True
        if not hit:
            # концы горизонтали: узловая точка примыкания
            for end in (cpts[0], cpts[-1]):
                d, s = _point_to_polyline(end, line_pts, cum)
                if d <= tol:
                    out.append((s, float(z)))
    return out


def profile_from_samples(line_pts, samples):
    """Отметки вершин линии по точкам встречи.

    samples - список (s, z). Интерполяция линейна по дуге, за крайними
    точками отметка держится постоянной (экстраполировать уклон по двум
    соседним горизонталям опасно: у бровки уклон вдоль часто ломается).
    Возвращает (zs, n_used): отметки вершин и число опорных точек. Если
    точек нет - (None, 0). Одна точка даёт постоянную отметку.
    """
    if not samples:
        return None, 0
    line_pts = np.asarray(line_pts, dtype=np.float64)
    cum = _cum_length(line_pts)
    arr = np.array(sorted(samples), dtype=np.float64)
    s, z = arr[:, 0], arr[:, 1]
    # одинаковые дуговые координаты (двойное пересечение) - среднее
    su, idx = np.unique(np.round(s, 9), return_inverse=True)
    zu = np.zeros_like(su)
    cnt = np.zeros_like(su)
    for k, zi in zip(idx, z):
        zu[k] += zi
        cnt[k] += 1
    zu /= np.maximum(cnt, 1)
    if len(su) == 1:
        return np.full(len(line_pts), zu[0]), 1
    zs = np.interp(cum, su, zu)
    return zs, len(su)


def snap_elevations(lines, contours, tol):
    """Полный проход: линии форм получают профиль с горизонталей.

    lines - список dict(pts=(N, 2), ...произвольные поля сохраняются);
    contours - список dict(pts, z);
    tol - допуск примыкания.

    Возвращает (done, skipped):
    done - список dict исходной линии плюс zs=(N,) и n_samples;
    skipped - список dict(line=исходный dict, reason=строка).
    """
    done, skipped = [], []
    for ln in lines:
        pts = np.asarray(ln["pts"], dtype=np.float64)
        if len(pts) < 2:
            skipped.append({"line": ln, "reason": "меньше двух вершин"})
            continue
        samples = gather_samples(pts, contours, tol)
        zs, n = profile_from_samples(pts, samples)
        if zs is None:
            skipped.append({"line": ln,
                            "reason": "ни одна горизонталь не примыкает"})
            continue
        item = dict(ln)
        item["zs"] = zs
        item["n_samples"] = n
        done.append(item)
    return done, skipped
