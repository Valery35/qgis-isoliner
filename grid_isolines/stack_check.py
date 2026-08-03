# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Диагностика согласованности пачки пластов: ядро.

Кровли и подошвы пластов обычно строятся кригингом независимо друг от
друга: каждая поверхность знает только свои замеры и ничего не знает о
существовании соседей. Пока пласты выдержаны, это сходит с рук. В зоне
выклинивания, где мощность идёт к нулю, две независимо построенные
поверхности почти неизбежно пересекаются - кровля местами уходит ниже
подошвы. Арифметика по такой пачке даёт отрицательные мощности и объёмы,
а на разрезе видны вывернутые полосы.

Инструмент считает, а не решает. Отрицательная мощность бывает двух
происхождений: ошибка интерполяции и настоящее опрокидывание пласта на
локальной складке. Различить их арифметикой нельзя - выглядят одинаково.
Поэтому места отмечаются, числа печатаются, а приговор выносит геолог,
глядя на разрез через найденную зону. Если в данных есть свой индикатор
опрокидывания, его можно подать маской, и такие ячейки уходят в отдельный
счёт вместо списка ошибок.

Работа идёт по гридам z(x, y): пачка это набор поверхностей, а не тел, и
объёмного представления для проверок не требуется.
"""

import numpy as np

# коды зон в выходном растре
CODE_OK = 0        # всё согласовано
CODE_ZERO = 1      # нулевая мощность: пласт выклинился
CODE_NEG = 2       # отрицательная мощность: кровля ниже подошвы
CODE_CROSS = 3     # пересечение с соседним пластом по вертикали
CODE_KNOWN = 4     # отмечено маской как известное опрокидывание


def bed_thickness(top, bot, nodata_mask=None):
    """Мощность пласта: кровля минус подошва. NaN там, где нет данных."""
    t = np.asarray(top, dtype=float)
    b = np.asarray(bot, dtype=float)
    m = t - b
    if nodata_mask is not None:
        m = np.where(nodata_mask, np.nan, m)
    return m


def check_bed(top, bot, zero_tol=0.0, overturned=None):
    """Проверка одного пласта: (коды, мощность, счётчики).

    zero_tol - мощность, ниже которой пласт считается выклинившимся, а не
    ошибкой. Ноль означает строгое сравнение; на практике имеет смысл
    задавать величину порядка точности построения поверхностей, иначе
    численный шум в зоне выклинивания попадёт в ошибки.

    overturned - булева маска мест, где опрокидывание известно из данных
    (например, посчитанный по скважинам индикатор). Отрицательная мощность
    там не считается дефектом и уходит в свой счёт.
    """
    m = bed_thickness(top, bot)
    codes = np.full(m.shape, CODE_OK, dtype=np.uint8)
    valid = np.isfinite(m)
    zero = valid & (np.abs(m) <= zero_tol)
    neg = valid & (m < -abs(zero_tol))
    codes[zero] = CODE_ZERO
    codes[neg] = CODE_NEG
    if overturned is not None:
        known = neg & np.asarray(overturned, dtype=bool)
        codes[known] = CODE_KNOWN
        neg = neg & ~known
    stats = {"n_valid": int(valid.sum()),
             "n_zero": int(zero.sum()),
             "n_neg": int(neg.sum()),
             "n_known": int((codes == CODE_KNOWN).sum()),
             "min_thk": float(np.nanmin(m)) if valid.any() else float("nan"),
             "max_thk": float(np.nanmax(m)) if valid.any() else float("nan")}
    return codes, m, stats


def check_stack(beds, zero_tol=0.0, overturned=None):
    """Проверка всей пачки сверху вниз.

    beds - список (name, top, bot) в стратиграфическом порядке, сверху
    вниз. Кроме проверки каждого пласта сверяются соседи: подошва
    вышележащего не должна опускаться ниже кровли нижележащего. Это
    отдельный дефект: каждый пласт по отдельности может быть в порядке, а
    вместе они перехлёстываются.

    Возвращает (codes, report):
    codes - растр кодов, худший случай на ячейку (порядок важности
    возрастает от OK к CROSS);
    report - список словарей по пластам и по контактам.
    """
    if not beds:
        return None, []
    shape = np.asarray(beds[0][1]).shape
    codes = np.full(shape, CODE_OK, dtype=np.uint8)
    report = []
    for name, top, bot in beds:
        c, m, st = check_bed(top, bot, zero_tol=zero_tol,
                             overturned=overturned)
        st["bed"] = name
        st["kind"] = "bed"
        report.append(st)
        codes = np.maximum(codes, c)
    for i in range(len(beds) - 1):
        upper_name, _ut, ub = beds[i]
        lower_name, lt, _lb = beds[i + 1]
        gap = np.asarray(ub, dtype=float) - np.asarray(lt, dtype=float)
        valid = np.isfinite(gap)
        cross = valid & (gap < -abs(zero_tol))
        if overturned is not None:
            cross = cross & ~np.asarray(overturned, dtype=bool)
        codes = np.where(cross, np.maximum(codes, CODE_CROSS), codes)
        report.append({"kind": "contact",
                       "bed": "%s / %s" % (upper_name, lower_name),
                       "n_valid": int(valid.sum()),
                       "n_cross": int(cross.sum()),
                       "min_gap": float(np.nanmin(gap)) if valid.any()
                       else float("nan")})
    return codes.astype(np.uint8), report


def min_gap_map(beds, zero_tol=0.0):
    """Наименьший зазор до соседнего пласта в каждой ячейке.

    Число в журнале говорит, насколько близко пачка подошла к перехлёсту,
    но не говорит где. А знать надо именно место: там перехлёст и
    возникнет при следующем пересчёте поверхностей. На реальной пачке
    наименьший зазор оказался около метра при мощностях в десятки - то
    есть междупластье тоньше погрешности построения, и держится такая
    пачка на удаче.

    Возвращает растр: минимум по всем контактам зазора между подошвой
    вышележащего и кровлей нижележащего. Отрицательные значения - уже
    перехлёст.
    """
    if len(beds) < 2:
        return None
    out = None
    for i in range(len(beds) - 1):
        _un, _ut, ub = beds[i]
        _ln, lt, _lb = beds[i + 1]
        gap = np.asarray(ub, dtype=float) - np.asarray(lt, dtype=float)
        out = gap if out is None else np.fmin(out, gap)
    return out


def looks_inverted(report, codes):
    """Похоже ли, что пласты поданы в обратном порядке.

    Признак прямой: внутри пластов всё чисто, а перехлёст занимает почти
    всю площадь. Геология так не выглядит - при настоящем перехлёсте он
    местный и соседствует с зонами выклинивания. Зато именно так выглядит
    пачка, поданная снизу вверх: подошва «верхнего» лежит ниже кровли
    «нижнего» всюду, потому что они поменяны местами.

    Возвращает True либо False; решение остаётся за человеком, инструмент
    только предупреждает.
    """
    beds = [r for r in report if r.get("kind") == "bed"]
    contacts = [r for r in report if r.get("kind") == "contact"]
    if not beds or not contacts:
        return False
    if any(r["n_neg"] for r in beds):
        return False                    # внутри пластов есть дефекты
    total = float(np.asarray(codes).size)
    crossed = sum(r["n_cross"] for r in contacts) / max(len(contacts), 1)
    return crossed > 0.9 * total


def zone_extent_m(codes, code, cell):
    """Площадь зоны с данным кодом в квадратных метрах.

    Число ячеек само по себе ничего не говорит: на метровой сетке тысяча
    ячеек это тысяча квадратных метров, на тридцатиметровой - почти
    гектар. Поэтому в журнал идёт площадь, а не счёт.
    """
    return float((np.asarray(codes) == code).sum()) * float(cell) ** 2


def summarize(report, cell):
    """Строки для журнала: по пласту и по контакту, с площадями."""
    out = []
    for r in report:
        if r.get("kind") == "bed":
            out.append(
                "%s: мощность от %.2f до %.2f м, выклинивание %.0f м2, "
                "отрицательная мощность %.0f м2%s"
                % (r["bed"], r["min_thk"], r["max_thk"],
                   r["n_zero"] * cell ** 2, r["n_neg"] * cell ** 2,
                   (", известное опрокидывание %.0f м2"
                    % (r["n_known"] * cell ** 2)) if r["n_known"] else ""))
        else:
            out.append(
                "%s: наименьший зазор %.2f м, перехлёст %.0f м2"
                % (r["bed"], r["min_gap"], r["n_cross"] * cell ** 2))
    return out


# --- свидетельство скважин ------------------------------------------------

# состояния ячейки после сверки со скважинами
W_UNCHECKED = 0    # рядом нет скважин, сказать нечего
W_CONFIRMS = 1     # скважина рядом показывает тот же перевёрнутый порядок
W_CONTRADICTS = 2  # скважина рядом показывает нормальный порядок


def overturned_holes(intervals, order):
    """Скважины, в которых порядок пластов по стволу нарушен.

    intervals - список dict(hole_id, frm, to, code): интервалы опробования
    по глубине от устья вниз. order - список кодов пластов сверху вниз,
    стратиграфический порядок (справочник пластов).

    В нормальном разрезе коды встречаются по стволу в том же порядке, что
    и в справочнике. Если пласт, который должен лежать выше, встретился
    ниже соседа - разрез в этой скважине перевёрнут. Возвращает множество
    hole_id.

    Считать это по скважинам надёжнее, чем гадать по гридам: скважина
    видит настоящую последовательность, а грид - только результат
    интерполяции.
    """
    rank = {c: i for i, c in enumerate(order)}
    by_hole = {}
    for it in intervals:
        code = it.get("code")
        if code not in rank:
            continue
        by_hole.setdefault(it["hole_id"], []).append(
            (float(it["frm"]), rank[code]))
    bad = set()
    for hid, rows in by_hole.items():
        rows.sort()
        ranks = [r for _d, r in rows]
        # нарушение: где-то дальше по стволу встретился пласт выше по
        # стратиграфии, чем уже пройденный
        for i in range(1, len(ranks)):
            if ranks[i] < ranks[i - 1]:
                bad.add(hid)
                break
    return bad


def witness_map(shape, gt, holes, bad_ids, radius_m):
    """Свидетельство скважин по площади: три состояния на ячейку.

    holes - список (hole_id, x, y), gt - геотрансформ GDAL,
    radius_m - расстояние, на котором скважина ещё считается свидетелем.

    Маску опрокидывания не строим интерполяцией: скважина знает правду в
    своей точке, а между скважинами правды нет, и размазывать её по
    площади значило бы выдавать догадку за данные. Поэтому ячейка либо
    подтверждена ближайшей скважиной, либо ею же опровергнута, либо
    остаётся непроверенной - и последнее не порок, а состояние.
    """
    ny, nx = shape
    out = np.full(shape, W_UNCHECKED, dtype=np.uint8)
    if not holes:
        return out
    cell_x, cell_y = abs(gt[1]), abs(gt[5])
    yy, xx = np.mgrid[0:ny, 0:nx]
    wx = gt[0] + (xx + 0.5) * gt[1]
    wy = gt[3] + (yy + 0.5) * gt[5]
    best_d = np.full(shape, np.inf)
    best_bad = np.zeros(shape, dtype=bool)
    for hid, hx, hy in holes:
        d = np.hypot(wx - float(hx), wy - float(hy))
        closer = d < best_d
        best_d = np.where(closer, d, best_d)
        best_bad = np.where(closer, hid in bad_ids, best_bad)
    near = best_d <= float(radius_m)
    out[near & best_bad] = W_CONFIRMS
    out[near & ~best_bad] = W_CONTRADICTS
    _ = cell_x, cell_y
    return out


def apply_witness(codes, witness):
    """Перевод кода отрицательной мощности по свидетельству скважин.

    Подтверждённое опрокидывание перестаёт быть дефектом: оно уходит в код
    известного. Опровергнутое остаётся дефектом - там гриды спорят со
    скважиной, и права скважина. Непроверенное остаётся как есть: инструмент
    не знает и не притворяется.
    """
    out = np.array(codes, copy=True)
    conf = (out == CODE_NEG) & (np.asarray(witness) == W_CONFIRMS)
    out[conf] = CODE_KNOWN
    return out


# --- разбор пар по именам слоёв -------------------------------------------

TOP_MARKS = ("_top", "_кровля", "_krovlya", "-top", " top", "_верх")
BOT_MARKS = ("_bottom", "_bot", "_подошва", "_podoshva", "-bottom",
             " bottom", "_низ")


def split_name(name):
    """Имя слоя -> (пласт, роль) либо (None, None).

    Роль - "top" или "bot". Разбор по суффиксу: B_top и B_bottom дают
    пласт B. Имена в комплектах несут структуру, и разбирать их дешевле,
    чем заставлять человека выбирать десяток слоёв по одному в нужном
    порядке. Неразобранное возвращается пустым и печатается в журнал: как
    и с полем вида, угадывать вслепую хуже, чем сказать «не понял».
    """
    low = str(name).strip().lower()
    for mark in BOT_MARKS:          # подошва раньше: _bottom содержит _bot
        if low.endswith(mark):
            return str(name)[:len(low) - len(mark)], "bot"
    for mark in TOP_MARKS:
        if low.endswith(mark):
            return str(name)[:len(low) - len(mark)], "top"
    return None, None


def pair_by_name(names, order=None):
    """Пары кровля-подошва из списка имён слоёв.

    order - стратиграфический порядок кодов пластов сверху вниз. Если
    задан, пары выстраиваются по нему; иначе сохраняется порядок
    появления, который в дереве слоёв обычно и есть стратиграфический.

    Возвращает (pairs, unmatched):
    pairs - список (пласт, имя кровли, имя подошвы);
    unmatched - список (имя, причина) для всего, что не сложилось в пару.
    """
    beds, seen, unmatched = {}, [], []
    for nm in names:
        bed, role = split_name(nm)
        if bed is None:
            unmatched.append((nm, "имя не содержит признака кровли или "
                                  "подошвы"))
            continue
        if bed not in beds:
            beds[bed] = {}
            seen.append(bed)
        if role in beds[bed]:
            unmatched.append((nm, "для пласта уже есть слой этой роли"))
            continue
        beds[bed][role] = nm
    pairs = []
    keys = seen
    if order:
        rank = {str(c).strip().lower(): i for i, c in enumerate(order)}
        keys = sorted(seen, key=lambda b: rank.get(str(b).strip().lower(),
                                                   len(rank) + seen.index(b)))
    for bed in keys:
        r = beds[bed]
        if "top" in r and "bot" in r:
            pairs.append((bed, r["top"], r["bot"]))
        else:
            missing = "подошвы" if "top" in r else "кровли"
            unmatched.append((r.get("top") or r.get("bot"),
                              "для пласта нет %s" % missing))
    return pairs, unmatched
