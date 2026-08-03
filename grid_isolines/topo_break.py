# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Кандидаты бровок и подошв из ЦМР на чистом NumPy.

Бровка - место, где уклон меняется быстрее всего. Признаком служит
величина градиента уклона (градусы на метр), гребни признака выделяются
подавлением немаксимумов поперёк, соединяются гистерезисом двумя порогами
и трассируются в ломаные. Знак профильной кривизны делит кандидатов на
бровки (выпуклый излом) и подошвы (вогнутый).

Схема - Canny по полю уклона, адаптированный к рельефу. Пороги гистерезиса
подбираются из распределения признака и наружу не выносятся: содержательный
отбор кандидатов делается после, по перепаду поперёк линии, и он
принадлежит человеку.
"""

import numpy as np

try:
    from .topo_surface import slope_aspect, _window_max, _window_min
except ImportError:  # безголовый запуск тестов из папки модуля
    from topo_surface import slope_aspect, _window_max, _window_min


def _grad(z, cell):
    """Центральные разности, на краях односторонние. Оси (восток, север)."""
    gy, gx = np.gradient(np.asarray(z, dtype=np.float64), float(cell))
    # строка 0 северная: рост индекса строки идёт на юг
    return gx, -gy


def _smooth3(a, passes=2):
    """Биномиальное сглаживание 3x3, сепарабельное, NaN не расползается."""
    out = a.copy()
    for _ in range(int(passes)):
        v = np.where(np.isfinite(out), out, 0.0)
        wt = np.isfinite(out).astype(np.float64)
        for axis in (0, 1):
            v = (np.roll(v, 1, axis) + 2.0 * v + np.roll(v, -1, axis)) / 4.0
            wt = (np.roll(wt, 1, axis) + 2.0 * wt + np.roll(wt, -1, axis)) / 4.0
        with np.errstate(invalid="ignore", divide="ignore"):
            out = np.where(wt > 1e-9, v / wt, np.nan)
    return out


def edge_strength(z, cell, nodata_mask=None):
    """Признак излома и его знак.

    Возвращает (E, sign, slope):
    E - величина градиента уклона, градусы на метр. Велика на бровке и
    на подошве, мала на плоскости и на ровном откосе.
    sign - знак профильной кривизны: +1 выпуклый излом (бровка),
    -1 вогнутый (подошва), 0 не определился.
    slope - уклон в градусах, он нужен вызывающему для атрибутов.

    Уклон перед градиентом сглаживается биномиальным ядром: это первый
    шаг Canny, без него шум плотной съёмки (сантиметры на ячейке в метр)
    даёт пики признака той же величины, что настоящие бровки, и паутина
    шума дробит их линии узлами.

    Ячейки nodata и их соседи получают NaN: ядра через дыры не считаем.
    """
    z = np.asarray(z, dtype=np.float64)
    if nodata_mask is None:
        nodata_mask = ~np.isfinite(z)
    slope, _aspect = slope_aspect(z, cell, nodata_mask=nodata_mask)
    slope_s = _smooth3(np.where(np.isfinite(slope), slope, np.nan))

    sx, sy = _grad(slope_s, cell)
    e = np.hypot(sx, sy)

    # профильная кривизна через вторые производные, знак решает выпуклость
    zx, zy = _grad(np.where(nodata_mask, np.nan, z), cell)
    zxx, _ = _grad(zx, cell)
    _, zyy = _grad(zy, cell)
    zxy, _ = _grad(zy, cell)
    g2 = zx * zx + zy * zy
    with np.errstate(invalid="ignore", divide="ignore"):
        kp = -(zx * zx * zxx + 2.0 * zx * zy * zxy + zy * zy * zyy)
        kp = np.where(g2 > 1e-12, kp / g2, 0.0)
    kp_s = _smooth3(np.where(np.isfinite(kp), kp, 0.0), passes=1)
    sign = np.zeros_like(kp, dtype=np.int8)
    sign[kp_s > 1e-9] = 1
    sign[kp_s < -1e-9] = -1

    bad = ~np.isfinite(e)
    e = np.where(bad, 0.0, e)
    # Кромка растра: уклон там считан по продлённому краю, и градиент
    # уклона рождает ложный гребень вдоль всей рамки. Признак в полосе
    # двух ячеек не определён и гасится.
    e[:2, :] = 0.0
    e[-2:, :] = 0.0
    e[:, :2] = 0.0
    e[:, -2:] = 0.0
    return e, sign, slope


def _nms(e, cell):
    """Подавление немаксимумов поперёк гребня признака.

    Направление берётся из градиента самого признака, квантуется в четыре
    оси. Ячейка выживает, когда она не слабее обоих соседей вдоль оси.
    """
    ex, ey = _grad(e, cell)
    ang = np.degrees(np.arctan2(ey, ex)) % 180.0
    p = np.pad(e, 1, mode="constant", constant_values=0.0)

    c = p[1:-1, 1:-1]
    east = p[1:-1, 2:]
    west = p[1:-1, :-2]
    north = p[:-2, 1:-1]
    south = p[2:, 1:-1]
    ne = p[:-2, 2:]
    sw = p[2:, :-2]
    nw = p[:-2, :-2]
    se = p[2:, 2:]

    out = np.zeros_like(e, dtype=bool)
    a0 = (ang < 22.5) | (ang >= 157.5)          # градиент восток-запад
    a45 = (ang >= 22.5) & (ang < 67.5)          # северо-восток
    a90 = (ang >= 67.5) & (ang < 112.5)         # север-юг
    a135 = (ang >= 112.5) & (ang < 157.5)       # северо-запад
    out |= a0 & (c >= east) & (c >= west)
    out |= a45 & (c >= ne) & (c >= sw)
    out |= a90 & (c >= north) & (c >= south)
    out |= a135 & (c >= nw) & (c >= se)
    return out & (e > 0.0)


def _hysteresis(e, ridge, t_low, t_high):
    """Гистерезис: сильные ячейки тянут за собой слабые по 8-связности."""
    weak = ridge & (e >= t_low)
    strong = ridge & (e >= t_high)
    if not strong.any():
        return strong
    keep = strong.copy()
    frontier = strong
    grow = np.zeros_like(keep)
    while True:
        p = np.pad(frontier, 1, mode="constant", constant_values=False)
        grow[:] = False
        grow |= p[1:-1, 2:] | p[1:-1, :-2] | p[:-2, 1:-1] | p[2:, 1:-1]
        grow |= p[:-2, 2:] | p[2:, :-2] | p[:-2, :-2] | p[2:, 2:]
        frontier = grow & weak & ~keep
        if not frontier.any():
            break
        keep |= frontier
    return keep


MIN_TURN_DEG = 5.0   # наименьший поворот склона на ячейку, считаемый изломом


def auto_thresholds(e, ridge, cell):
    """Пороги гистерезиса: квантиль по гребням, но не ниже физического пола.

    Квантиль нужен, чтобы порог подстраивался под данные. Привязываться к
    максимуму нельзя: порог становится заложником одного самого резкого
    объекта на растре, и стоит добавить в сцену насыпь с острым гребнем,
    как уступы карьера уходят под порог. Проверено на демо.

    Но и одного квантиля мало: у него нет понятия о том, что излома нет
    вовсе. На ровной наклонной плоскости признак почти нулевой, а квантиль
    всё равно найдёт «четверть самых сильных» и выдаст ложные линии.
    Поэтому снизу стоит физический пол: излом обязан поворачивать склон
    хотя бы на MIN_TURN_DEG градусов на ячейку. Признак меряется в
    градусах на метр, отсюда деление на размер ячейки.
    """
    vals = e[ridge & (e > 0.0)]
    if vals.size == 0:
        return 0.0, 0.0
    floor = MIN_TURN_DEG / float(cell)
    t_high = max(float(np.percentile(vals, 25.0)), floor)
    return 0.5 * t_high, t_high


def _thin(mask):
    """Утоньшение Чжана-Суэня до линий в одну ячейку.

    Гребень после подавления немаксимумов местами толще одной ячейки, на
    таких местах каждая ячейка становится узлом и трассировка крошит линию
    на двухячеечные обрывки. Утоньшение снимает лишние ячейки, сохраняя
    связность и концы.
    """
    m = mask.astype(np.uint8).copy()

    def neighbors(p):
        # порядок P2..P9 по часовой от севера
        return (p[:-2, 1:-1], p[:-2, 2:], p[1:-1, 2:], p[2:, 2:],
                p[2:, 1:-1], p[2:, :-2], p[1:-1, :-2], p[:-2, :-2])

    while True:
        changed = False
        for phase in (0, 1):
            p = np.pad(m, 1, mode="constant")
            n2, n3, n4, n5, n6, n7, n8, n9 = neighbors(p)
            ring = [n2, n3, n4, n5, n6, n7, n8, n9]
            b = sum(ring)
            a = sum(((ring[i] == 0) & (ring[(i + 1) % 8] == 1)).astype(np.uint8)
                    for i in range(8))
            if phase == 0:
                c1 = (n2 * n4 * n6) == 0
                c2 = (n4 * n6 * n8) == 0
            else:
                c1 = (n2 * n4 * n8) == 0
                c2 = (n2 * n6 * n8) == 0
            kill = (m == 1) & (b >= 2) & (b <= 6) & (a == 1) & c1 & c2
            if kill.any():
                m[kill] = 0
                changed = True
        if not changed:
            break
    return m.astype(bool)


def trace_lines(mask):
    """Трассировка ячеек-гребней в ломаные по 8-связности.

    На узлах линия не рвётся, а продолжается в соседа с наименьшим
    поворотом: на диагональных дугах подавление немаксимумов оставляет
    чересстрочные пары ячеек, узлов там много, и трассировка по принципу
    «узел равно конец» крошила дугу на двухячеечные обрывки. Поворот
    больше прямого угла - конец линии. Возвращает список списков
    (row, col).
    """
    rows, cols = np.nonzero(mask)
    cells = set(zip(rows.tolist(), cols.tolist()))
    if not cells:
        return []

    def nbrs(rc):
        r, c = rc
        out = []
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                p = (r + dr, c + dc)
                if p in cells:
                    out.append(p)
        return out

    deg = {rc: len(nbrs(rc)) for rc in cells}
    visited_edges = set()

    def walk(start, first):
        line = [start, first]
        visited_edges.add((start, first))
        visited_edges.add((first, start))
        prev, cur = start, first
        while True:
            dr0 = cur[0] - prev[0]
            dc0 = cur[1] - prev[1]
            n0 = np.hypot(dr0, dc0)
            best, best_cos = None, 0.0
            for nx in nbrs(cur):
                if nx == prev or (cur, nx) in visited_edges:
                    continue
                dr1 = nx[0] - cur[0]
                dc1 = nx[1] - cur[1]
                cos = (dr0 * dr1 + dc0 * dc1) / (n0 * np.hypot(dr1, dc1))
                if cos > best_cos:            # поворот строго меньше прямого
                    best, best_cos = nx, cos
            if best is None:
                break
            visited_edges.add((cur, best))
            visited_edges.add((best, cur))
            line.append(best)
            prev, cur = cur, best
        return line

    # старты: сначала настоящие концы, потом всё непройденное
    order = sorted(cells, key=lambda rc: (deg[rc] != 1, rc))
    lines = []
    for s in order:
        for nb in nbrs(s):
            if (s, nb) in visited_edges:
                continue
            lines.append(walk(s, nb))
    return [ln for ln in lines if len(ln) >= 2]


def line_attributes(z, cell, line, sign, slope, probe=3):
    """Атрибуты кандидата: перепад поперёк, длина, уклон сторон, вид.

    Перепад меряется в каждой вершине по нормали к линии: разность отметок
    в probe ячейках по обе стороны, итог - медиана по вершинам. Медиана
    же решает знак: бровка или подошва. Уклон сторон - средний уклон в тех
    же пробных ячейках.
    """
    z = np.asarray(z, dtype=np.float64)
    h, w = z.shape
    drops, signs, slopes = [], [], []
    n = len(line)
    for i, (r, c) in enumerate(line):
        j0, j1 = max(0, i - 1), min(n - 1, i + 1)
        dr = line[j1][0] - line[j0][0]
        dc = line[j1][1] - line[j0][1]
        norm = np.hypot(dr, dc)
        if norm < 1e-9:
            continue
        # нормаль к направлению линии
        nr, nc = -dc / norm, dr / norm
        ra = int(round(r + nr * probe))
        ca = int(round(c + nc * probe))
        rb = int(round(r - nr * probe))
        cb = int(round(c - nc * probe))
        if not (0 <= ra < h and 0 <= ca < w and 0 <= rb < h and 0 <= cb < w):
            continue
        za, zb = z[ra, ca], z[rb, cb]
        if np.isfinite(za) and np.isfinite(zb):
            drops.append(abs(za - zb))
        if 0 <= r < h and 0 <= c < w:
            signs.append(int(sign[r, c]))
            sa, sb = slope[ra, ca], slope[rb, cb]
            if np.isfinite(sa) and np.isfinite(sb):
                slopes.append(0.5 * (sa + sb))
    length = 0.0
    for i in range(1, n):
        length += np.hypot(line[i][0] - line[i - 1][0],
                           line[i][1] - line[i - 1][1])
    length *= float(cell)
    drop = float(np.median(drops)) if drops else 0.0
    slope_mean = float(np.median(slopes)) if slopes else 0.0
    s = int(np.sign(np.median(signs))) if signs else 0
    kind = "brow" if s > 0 else ("toe" if s < 0 else "flat")
    return {"drop": drop, "length_m": length,
            "slope_deg": slope_mean, "kind": kind}


def breakline_candidates(z, cell, min_drop=0.2, min_len_cells=5,
                         nodata_mask=None, probe=3):
    """Полный проход: от ЦМР до списка кандидатов с атрибутами.

    Возвращает список dict(cells=[(row, col)...], drop, length_m,
    slope_deg, kind), отсортированный по убыванию перепада. Пороги
    гистерезиса внутренние (auto_thresholds), наружу выведены только
    отсечка шума min_drop (метры) и минимальная длина в ячейках.
    """
    e, sign, slope = edge_strength(z, cell, nodata_mask=nodata_mask)
    ridge = _nms(e, cell)
    # Излом без перепада не излом. Перепад рельефа в окне пробы гасит
    # шумовые гребни до порогов и трассировки: шум в сантиметры не
    # набирает min_drop на плоскости, а окрестность бровки набирает
    # всегда. Это главный фильтр, пороги гистерезиса лишь дочищают.
    zz = np.asarray(z, dtype=np.float64)
    zf_hi = np.where(np.isfinite(zz), zz, -np.inf)
    zf_lo = np.where(np.isfinite(zz), zz, np.inf)
    relief = (_window_max(zf_hi, int(probe))
              - _window_min(zf_lo, int(probe)))
    ridge &= np.isfinite(relief) & (relief >= float(min_drop))
    t_low, t_high = auto_thresholds(e, ridge, cell)
    keep = _hysteresis(e, ridge, t_low, t_high)
    keep = _thin(keep)
    out = []
    for line in trace_lines(keep):
        if len(line) < int(min_len_cells):
            continue
        attrs = line_attributes(z, cell, line, sign, slope, probe=probe)
        if attrs["drop"] < float(min_drop):
            continue
        rec = {"cells": line}
        rec.update(attrs)
        out.append(rec)
    out.sort(key=lambda r: -r["drop"])
    return out


def sample_z(z, line, nodata_mask=None):
    """Отметки вдоль линии по ячейкам растра.

    Возвращает список z по вершинам. Ячейки nodata дают NaN, вызывающий
    решает сам, что с такой линией делать.
    """
    z = np.asarray(z, dtype=np.float64)
    h, w = z.shape
    out = []
    for r, c in line:
        if 0 <= r < h and 0 <= c < w:
            v = float(z[r, c])
            if nodata_mask is not None and nodata_mask[r, c]:
                v = float("nan")
        else:
            v = float("nan")
        out.append(v)
    return out


def pair_breaklines(brows, toes, z, cell, max_dist=200.0, min_share=0.4,
                    nodata_mask=None, downstream=None):
    """Собирает пары бровка-подошва спуском по склону.

    От каждой пробной вершины бровки идёт спуск по D8, пока не встретится
    ячейка подошвы или не кончится предел пути. Подошвы голосуют, побеждает
    та, куда пришло больше проб. Спуск выбран потому, что он повторяет
    физику уступа: вода с бровки скатывается по откосу ровно к его подошве,
    и это работает на кривых бортах, где ближайшая по расстоянию подошва
    может принадлежать соседнему уступу.

    Одна подошва может собрать несколько бровок, и это норма, а не сбой:
    трассировка режет длинную бровку на куски (съезд, развилки, разрывы
    признака), и все куски спускаются к одной и той же подошве.
    Поэтому результат группируется по подошве: форма это одна подошва и
    множество бровок при ней, ровно как в постановке про поверхность
    между структурными линиями. Общее поле связи одно на группу, и
    подошва в выход попадает один раз, а не по разу на каждую бровку.

    brows, toes - списки линий, каждая список (row, col).
    Возвращает (groups, unpaired):
    groups - список dict(toe=j, brows=[i...], link=str, share=наименьшая
    доля голосов в группе);
    unpaired - список dict(kind, idx, reason).
    """
    z = np.asarray(z, dtype=np.float64)
    ny, nx = z.shape
    if downstream is None:
        try:
            from .topo_flow import d8_directions
        except ImportError:  # безголовый запуск
            from topo_flow import d8_directions
        _dir, downstream = d8_directions(z, nodata_mask=nodata_mask)

    toe_id = np.full(ny * nx, -1, dtype=np.int64)
    for j, ln in enumerate(toes):
        for r, c in ln:
            if 0 <= r < ny and 0 <= c < nx:
                toe_id[r * nx + c] = j

    max_steps = max(1, int(round(float(max_dist) / float(cell))))
    by_toe = {}
    unpaired = []

    for i, ln in enumerate(brows):
        step = max(1, len(ln) // 12)
        votes = {}
        probes = 0
        for r, c in ln[::step]:
            probes += 1
            cur = r * nx + c
            for _ in range(max_steps):
                nxt = downstream[cur]
                if nxt < 0:
                    break
                cur = int(nxt)
                j = int(toe_id[cur])
                if j >= 0:
                    votes[j] = votes.get(j, 0) + 1
                    break
        if not votes or probes == 0:
            unpaired.append({"kind": "brow", "idx": i,
                             "reason": "спуск не дошёл до подошвы"})
            continue
        j, v = max(votes.items(), key=lambda kv: kv[1])
        share = v / float(probes)
        if share < float(min_share):
            unpaired.append({"kind": "brow", "idx": i,
                             "reason": "спуск разошёлся по разным подошвам"})
            continue
        by_toe.setdefault(j, []).append((i, share))

    groups = []
    for j in sorted(by_toe):
        items = by_toe[j]
        groups.append({"toe": j, "brows": [i for i, _s in items],
                       "link": "form-%d" % (len(groups) + 1),
                       "share": min(s for _i, s in items)})
    for j in range(len(toes)):
        if j not in by_toe:
            unpaired.append({"kind": "toe", "idx": j,
                             "reason": "к подошве не спустилась ни одна бровка"})
    return groups, unpaired


# --- толерантный читатель поля вида ------------------------------------

# Коды бровок из классификаторов: Рекомендации Росреестра и технические
# требования к цифровым планам. Отдельного кода подошвы нет ни в одном -
# она проходит нижней горизонталью или линией основания, поэтому список
# односторонний, и это не упущение.
CREST_CODES = ("22170000", "22213000", "22263000", "22413000",
               "22632000", "22633000", "22650000", "32183300",
               "62350400", "62350500")

_CREST_WORDS = ("бровк", "brow", "crest", "верх", "top", "гребен", "ridge")
_TOE_WORDS = ("подош", "toe", "низ", "bottom", "основани", "поднож")


def classify_kind(value):
    """Вид линии из значения поля: 'brow', 'toe' или None.

    В выходе 2.19 стоят brow и toe, а в сдаточном комплекте будет
    «Бровка откоса, насыпи, выемки укреплённая» или код 62350400.
    Читатель терпит и то, и другое: сперва точное совпадение, потом
    известные коды, потом вхождение слова. Неузнанное возвращает
    None - угадывать вслепую хуже, чем сказать «не понял».
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s in ("brow", "toe"):
        return s
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits:
        for code in CREST_CODES:
            if digits.startswith(code) or code.startswith(digits[:8]):
                if len(digits) >= 8:
                    return "brow"
    for w in _TOE_WORDS:          # подошва раньше бровки: «подошва откоса»
        if w in s:                # содержит и «откос», и слово подошвы
            return "toe"
    for w in _CREST_WORDS:
        if w in s:
            return "brow"
    return None


def classify_kinds(values):
    """Разбор набора значений: (решения, неузнанные).

    решения - dict значение -> вид, только для узнанных;
    неузнанные - список значений в порядке появления.
    Нужен инструменту, чтобы напечатать в журнал таблицу решений: на
    первом живом слое видно, как прочитан каждый код, а не «доверьтесь».
    """
    decided, unknown, seen = {}, [], set()
    for v in values:
        key = None if v is None else str(v).strip()
        if key in seen:
            continue
        seen.add(key)
        k = classify_kind(v)
        if k is None:
            unknown.append(key)
        else:
            decided[key] = k
    return decided, unknown


def pair_by_elevation(brows, toes, z_brows, z_toes, cell, max_dist=50.0):
    """Формы без ЦМР: пара определяется отметками, а не спуском.

    Спуск по склону в pair_breaklines отвечает на один вопрос - какая
    подошва принадлежит этой бровке, - и нужен он потому, что у линий нет
    отметок. Когда отметки есть (например, после 2.22), ответ уже в
    данных: подошва обязана лежать ниже бровки, а из лежащих ниже берётся
    ближайшая. ЦМР при этом не требуется вовсе, и топографический
    сценарий перестаёт ходить по кругу «нужен рельеф, чтобы построить
    рельеф».

    brows, toes - списки линий, каждая список (row, col) либо (x, y) в
    одних единицах с cell; z_brows, z_toes - средние отметки линий.
    Возвращает (groups, unpaired) в том же виде, что pair_breaklines.
    """
    import numpy as _np
    max_cells = float(max_dist) / float(cell)
    by_toe = {}
    unpaired = []
    arrs = [_np.asarray(t, dtype=float) for t in toes]
    for i, br in enumerate(brows):
        b = _np.asarray(br, dtype=float)
        best_j, best_d = -1, _np.inf
        for j, t in enumerate(arrs):
            if z_toes[j] >= z_brows[i]:
                continue                      # подошва не может быть выше
            d = _np.min(_np.hypot(
                b[:, 0][:, None] - t[:, 0][None, :],
                b[:, 1][:, None] - t[:, 1][None, :]))
            if d < best_d:
                best_d, best_j = d, j
        if best_j < 0:
            unpaired.append({"kind": "brow", "idx": i,
                             "reason": "ниже этой бровки подошв нет"})
        elif best_d > max_cells:
            unpaired.append({"kind": "brow", "idx": i,
                             "reason": "ближайшая подошва дальше предела"})
        else:
            by_toe.setdefault(best_j, []).append(i)
    groups = []
    for j in sorted(by_toe):
        groups.append({"toe": j, "brows": by_toe[j],
                       "link": "form-%d" % (len(groups) + 1),
                       "share": 1.0})
    for j in range(len(toes)):
        if j not in by_toe:
            unpaired.append({"kind": "toe", "idx": j,
                             "reason": "к подошве не отнесена ни одна бровка"})
    return groups, unpaired
