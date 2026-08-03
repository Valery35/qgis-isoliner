# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Демонстрационный разрез Верхнекамского типа: ядро.

Задача демо - дать материал, на котором видно, где гридовое
представление работает, а где перестаёт. Поэтому колонка нарочно
содержит случаи, которые модель z(x, y) описать не может.

Колонка взята из справочника пластов целиком, сверху вниз: покровные
отложения, пестроцветная и терригенно-карбонатная толщи,
соляно-мергельная толща, дальше соль - переходная пачка, покровная
каменная соль, карналлитовая зона с междупластьями, сильвинитовые
пласты, подстилающая соль, маркирующая глина и нижняя соль. Междупластья
здесь такие же тела, как пласты, а не промежуток в паре: так они и
записаны в справочнике.

Устройство соляной части. Слоистая толща задаётся стратиграфической
координатой s: чем она больше, тем ниже лежит слой. В спокойной части s
совпадает с глубиной. В зоне складки координаты деформируются поворотом
вокруг оси, и слои заворачиваются: вертикальная скважина у замка
пересекает одно и то же тело трижды - в горизонтальном крыле, в верхней
дуге и в нижней. Никакая плотность сети этого не исправит, потому что
над точкой в плане оказывается несколько кровель.

Устройство надсолевой части. Покровные, пестроцветная и
терригенно-карбонатная толщи откладываются от рельефа вниз постоянными
мощностями. Соляно-мергельная толща заполняет остаток до соли, поэтому
своей мощности у неё нет: она легла на растворённую соль и повторяет её
форму. Над куполом она тонкая, в стороне полная.

Эрозия. Уровень растворения это отметка, до которой дошла вода. Соль,
поднятая куполом выше этого уровня, растворена и вынесена, а срез
переносится проходом сверху вниз: поверхность, оказавшаяся выше уровня,
опускается на него. Зеркало это не кровля назначенного тела, а кровля
того, которое уцелело: в стороне от купола переходная пачка, над сводом
уже покровная соль. Какое именно тело выходит под зеркало, считает
subcrop_map.
"""

import math

import numpy as np

# Колонка сверху вниз: код, вид тела, мощность в метрах, цвет.
# Порядок, коды, вид и цвет - из справочника пластов. Мощности взяты
# правдоподобные для Верхнекамского и на устройство не влияют: меняются
# числом.
#
# Мощность соляно-мергельной толщи не задана (None): она заполняет всё от
# подошвы терригенно-карбонатной до кровли уцелевшей соли.
COLUMN = (
    ("Q",          "пласт",        12.0, "#ffffff"),
    ("ПЦТ",        "пласт",        35.0, "#8b6914"),
    ("ТКТ",        "пласт",        45.0, "#53e753"),
    ("СМТ",        "пласт",        None, "#499c49"),
    ("ПП",         "пласт",        12.0, "#add8e6"),
    ("ПКС",        "пласт",        20.0, "#1e3bff"),
    ("К",          "пласт",         3.5, "#ffff00"),
    ("И-К",        "междупластье",  2.5, "#add8e6"),
    ("И",          "пласт",         3.0, "#ffff00"),
    ("З-И",        "междупластье",  3.0, "#add8e6"),
    ("З",          "пласт",         2.5, "#ffff00"),
    ("Ж-З",        "междупластье",  3.5, "#add8e6"),
    ("Ж",          "пласт",         3.0, "#ffff00"),
    ("Е-Ж",        "междупластье",  4.0, "#add8e6"),
    ("Е",          "пласт",         5.0, "#ffff00"),
    ("Д-Е",        "междупластье",  3.0, "#add8e6"),
    ("Д",          "пласт",         3.0, "#ffff00"),
    ("Г-Д",        "междупластье",  3.0, "#add8e6"),
    ("Г",          "пласт",         3.5, "#ffff00"),
    ("В-Г",        "междупластье",  4.0, "#add8e6"),
    ("В",          "пласт",         6.0, "#ffa500"),
    ("Б-В",        "междупластье",  3.0, "#add8e6"),
    ("АБ",         "пласт",         7.0, "#008000"),
    ("А'-КрI",     "междупластье",  3.0, "#add8e6"),
    ("КрI",        "пласт",         1.5, "#ffc0cb"),
    ("КрI-КрII",   "междупластье",  3.0, "#add8e6"),
    ("КрII",       "пласт",         5.0, "#ff0000"),
    ("КрII-КрIII", "междупластье",  2.5, "#add8e6"),
    ("КрIIIа",     "пласт",         1.5, "#ffc0cb"),
    ("КрIIIа-б",   "междупластье",  1.5, "#add8e6"),
    ("КрIIIб",     "пласт",         1.5, "#ffc0cb"),
    ("КрIIIб-в",   "междупластье",  1.5, "#add8e6"),
    ("КрIIIв",     "пласт",         2.0, "#ffc0cb"),
    ("ПДКС",       "пласт",        40.0, "#1e90ff"),
    ("МГ",         "пласт",         0.6, "#4d4d4d"),
    ("НКС",        "пласт",        60.0, "#4859ff"),
)

FILL_CODE = "СМТ"            # тело переменной мощности между рельефом и солью
SALT_TOP_CODE = "ПП"         # первое соляное тело: с него начинается соль
WATERPROOF_TOP = "В"         # кровля, от которой считается водозащитная толща

_ISALT = [r[0] for r in COLUMN].index(SALT_TOP_CODE)
COVER = COLUMN[:_ISALT]
SALT = COLUMN[_ISALT:]

DEFAULT_RELIEF = 240.0       # дневная поверхность в спокойной части
DEFAULT_TOP = 60.0           # кровля соли в спокойной части
DEFAULT_LEVEL = 100.0        # уровень растворения: докуда дошла вода

CUT_CODE = "__cut__"         # ключ зеркала в выдаче surfaces()


def relief_at(x, y=None, amp=8.0, wl=900.0):
    """Дневная поверхность: пологие волны вокруг средней отметки."""
    z = DEFAULT_RELIEF + amp * math.sin(2.0 * math.pi * x / wl)
    if y is not None:
        z += 0.6 * amp * math.cos(2.0 * math.pi * y / (0.8 * wl))
    return z


def level_at(x, y=None, amp=3.0, wl=700.0):
    """Уровень растворения: отметка, до которой дошла вода.

    Он положе рельефа и с ним не связан: воду держит не форма земли, а
    гидрогеология. Слабая волна нужна, чтобы зеркало не выглядело
    чертёжной плоскостью.
    """
    z = DEFAULT_LEVEL + amp * math.sin(2.0 * math.pi * x / wl)
    if y is not None:
        z += 0.5 * amp * math.cos(2.0 * math.pi * y / (1.3 * wl))
    return z


def _uplift(x, y, dome):
    """Подъём соли над куполом: колокол с полуосями r и ry.

    Купол не деформирует слои внутри толщи, он поднимает её целиком.
    Этого довольно: интерес не в самом поднятии, а в том, что поднятое
    попадает выше уровня растворения и срезается.
    """
    if dome is None:
        return 0.0
    r = float(dome["r"]) or 1.0
    dx = (x - dome["xc"]) / r
    dy = 0.0 if y is None else (y - dome.get("yc", 0.0)) / (
        float(dome.get("ry", r)) or 1.0)
    return float(dome["up"]) * math.exp(-2.0 * (dx * dx + dy * dy))


def _strat_vec(x, zs, fold, y=None, dome=None):
    """Стратиграфическая координата массива отметок: метры ниже кровли соли.

    Вектором, а не поточечно: колонка из тридцати шести тел, и шаг по
    стволу мельче полуметра, иначе маркирующая глина в шестьдесят
    сантиметров проваливается между пробами. Поточечный марш на такой
    сетке считался бы минутами.

    fold - dict(xc, zc, r, turn) плюс необязательные yc и ry, или None.
    В сечении координаты поворачиваются вокруг (xc, zc) на угол turn,
    плавно нарастающий к ядру. При turn больше прямого угла слои
    заворачиваются, и вертикаль пересекает их несколько раз.

    Складка ограничена и по простиранию: yc и ry задают её протяжённость
    вдоль оси, а к торцам поворот сходит на нет. Без этого получается не
    структура, а полоса через всю площадь.
    """
    z = np.asarray(zs, float) - _uplift(x, y, dome)
    out = DEFAULT_TOP - z
    if fold is None:
        return out
    damp = 1.0
    if y is not None and fold.get("ry"):
        dy = abs(y - fold.get("yc", 0.0)) / float(fold["ry"])
        if dy >= 1.0:
            return out
        t = 1.0 - dy
        damp = t * t * (3.0 - 2.0 * t)
    dx = x - fold["xc"]
    dz = z - fold["zc"]
    r = np.hypot(dx, dz)
    inside = r < fold["r"]
    if not np.any(inside):
        return out
    t = 1.0 - r[inside] / fold["r"]
    ang = math.radians(fold["turn"]) * (t * t * (3.0 - 2.0 * t)) * damp
    ca, sa = np.cos(ang), np.sin(ang)
    zr = fold["zc"] + (-dx * sa + dz[inside] * ca)
    out[inside] = DEFAULT_TOP - zr
    return out


def salt_edges(beds=SALT):
    """Границы соляных тел по стратиграфической координате, сверху вниз."""
    edges = [0.0]
    for row in beds:
        edges.append(edges[-1] + row[2])
    return np.asarray(edges, float)


def column_at(x, zs, fold=None, y=None, dome=None, beds=SALT, level=True):
    """Индексы соляных тел для массива отметок: -1, где соли нет.

    Надсолевые тела сюда не входят: они не следуют за стратиграфической
    координатой соли, а лежат на ней сверху.

    level=True означает, что выше уровня растворения соли нет.
    """
    zs = np.asarray(zs, float)
    s = _strat_vec(x, zs, fold, y=y, dome=dome)
    edges = salt_edges(beds)
    idx = np.searchsorted(edges, s, side="right") - 1
    idx[(s < 0.0) | (s >= edges[-1])] = -1
    if level:
        idx[zs > level_at(x, y)] = -1
    return idx


def cover_at(x, z, y=None, salt_top=None):
    """Код надсолевого тела на отметке z или None, если её там нет.

    Отсчёт идёт от рельефа вниз постоянными мощностями. Заполняющее тело
    занимает всё от подошвы вышележащего до кровли соли: его подошва и
    есть зеркало.
    """
    zr = relief_at(x, y)
    if z > zr:
        return None
    d = zr - z
    acc = 0.0
    for code, _body, thk, _col in COVER:
        if thk is None:
            if salt_top is not None and z <= salt_top:
                return None
            return code
        if d < acc + thk:
            return code
        acc += thk
    return None


def hole_intervals(x, z_top, depth, step=0.2, beds=SALT, fold=None, y=None,
                   dome=None, cover=True):
    """Интервалы по стволу вертикальной скважины, сверху вниз.

    Возвращает список (от, до, код) от устья. Тело, вскрытое несколько
    раз, даст несколько интервалов с одним кодом - это и есть признак
    опрокинутого залегания.
    """
    n = int(math.ceil(depth / step)) + 1
    d = np.arange(n, dtype=float) * step
    zs = z_top - d
    idx = column_at(x, zs, fold=fold, y=y, dome=dome, beds=beds)
    codes = [beds[i][0] if i >= 0 else None for i in idx]
    if cover:
        hit = [i for i, c in enumerate(codes) if c is not None]
        top_salt = float(zs[hit[0]]) if hit else None
        for i, c in enumerate(codes):
            if c is None and (top_salt is None or zs[i] > top_salt):
                codes[i] = cover_at(x, float(zs[i]), y=y, salt_top=top_salt)
    out = []
    cur, start = None, 0.0
    for i, code in enumerate(codes):
        if code != cur:
            if cur is not None:
                out.append((start, float(d[i]), cur))
            cur, start = code, float(d[i])
    if cur is not None:
        out.append((start, float(d[-1]), cur))
    return out


def count_entries(intervals):
    """Сколько раз каждое тело вскрыто по стволу: dict код -> число."""
    n = {}
    for _f, _t, code in intervals:
        n[code] = n.get(code, 0) + 1
    return n


def _field(fn, xs, ys):
    """Поверхность по функции точки: обычная сетка значений."""
    out = np.empty((len(ys), len(xs)), float)
    for i, y in enumerate(ys):
        for j, x in enumerate(xs):
            out[i, j] = fn(float(x), float(y))
    return out


def surfaces(nx, ny, cell, beds=SALT, fold=None, wedge=None, seed=7,
             dome=None, step=0.2):
    """Гриды кровель и подошв всей колонки - как их построил бы человек.

    Существенная оговорка. В зоне складки этих поверхностей не
    существует: над точкой несколько кровель. Гриды строятся по ПЕРВОМУ
    сверху вскрытию, то есть ровно так, как их получил бы человек,
    собравший замеры из скважин и не заметивший опрокидывания. Именно
    поэтому демо и полезно: диагностика на таких гридах обязана
    показать, что с ними что-то не так.

    wedge - dict(bed, x0, x1): тело, выклинивающееся от x0 к x1.
    dome - dict(xc, yc, r, ry, up): купол, поднимающий соль.

    Возвращает dict код -> [кровля, подошва] по всей колонке и зеркало
    под ключом CUT_CODE.
    """
    rng = np.random.default_rng(seed)
    xs = (np.arange(nx) + 0.5) * cell
    ys = (np.arange(ny) + 0.5) * cell
    shape = (ny, nx)
    out = {row[0]: [np.full(shape, np.nan), np.full(shape, np.nan)]
           for row in COLUMN}

    up = float(dome["up"]) if dome else 0.0
    z0 = DEFAULT_LEVEL + 40.0 + up
    depth = sum(r[2] for r in beds) * 2.2 + 60.0 + up
    n = int(math.ceil(depth / step)) + 1
    zs_col = z0 - np.arange(n, dtype=float) * step
    for i, y in enumerate(ys):
        for j, x in enumerate(xs):
            # level=False: гриды строятся по неразмытой толще, а срез
            # переносится на поверхности ниже, проходом сверху вниз.
            # Иначе тело, целиком ушедшее выше уровня, не нашлось бы
            # вовсе и осталось бы nodata - той самой дырой, из-за
            # которой чертёж шёл ступенями.
            idx = column_at(float(x), zs_col, fold=fold, y=float(y),
                            dome=dome, beds=beds, level=False)
            k = 0
            seen = set()
            while k < n:
                b = idx[k]
                if b < 0:
                    k += 1
                    continue
                m = k
                while m + 1 < n and idx[m + 1] == b:
                    m += 1
                if b not in seen:            # только первое сверху вскрытие
                    seen.add(b)
                    code = beds[b][0]
                    out[code][0][i, j] = zs_col[k]
                    out[code][1][i, j] = zs_col[m]
                k = m + 1

    # выклинивание: мощность сводится к нулю на отрезке от x0 к x1
    if wedge:
        code = wedge["bed"]
        top, bot = out[code]
        # Граница выклинивания изогнута: прямая по меридиану выглядит
        # чертёжной условностью, а не геологией.
        bend = 0.18 * (wedge["x1"] - wedge["x0"]) * np.sin(
            2.0 * np.pi * ys / max(ys[-1], 1.0))
        x0 = wedge["x0"] + bend[:, None]
        x1 = wedge["x1"] + bend[:, None]
        t = np.clip((xs[None, :] - x0) / (x1 - x0), 0.0, 1.0)
        k = 1.0 - t * t * (3.0 - 2.0 * t)      # сглаженная ступень
        out[code][1] = top - (top - bot) * k

    # Шум построения. Он пространственно связан: соседние ячейки
    # интерполируются по одним и тем же скважинам и ошибаются похоже.
    # Кровля и подошва одного тела ошибаются согласованно, поэтому шум у
    # пары общий и в мощности сокращается. У всей толщи есть ещё общая
    # составляющая: там, где сеть редкая, колонка смещается целиком.
    common = _smooth_noise(rng, shape, sigma=0.5, passes=8)
    for row in beds:
        own = _smooth_noise(rng, shape, sigma=0.12, passes=8)
        for k in (0, 1):
            out[row[0]][k] = out[row[0]][k] + common + own

    # Зеркало: уровень растворения. Всё, что оказалось выше, опускается
    # на него проходом сверху вниз. Тело, ушедшее выше целиком, получает
    # нулевую мощность и ложится на зеркало, а не выбрасывается в
    # nodata: выброшенное обрывалось бы на разрезе отвесной стенкой по
    # границе ячейки, и чертёж шёл бы ступенями.
    lvl = _field(level_at, xs, ys) + common \
        + _smooth_noise(rng, shape, sigma=0.12, passes=8)
    for row in beds:
        for k in (0, 1):
            out[row[0]][k] = np.minimum(out[row[0]][k], lvl)
    salt_top = out[SALT[0][0]][0]
    out[CUT_CODE] = [salt_top.copy(), salt_top.copy()]

    # Надсолевая часть: постоянные мощности от рельефа вниз, заполняющее
    # тело занимает остаток до кровли уцелевшей соли.
    z = _field(relief_at, xs, ys) + _smooth_noise(rng, shape, sigma=0.3,
                                                  passes=8)
    for code, _body, thk, _col in COVER:
        out[code][0] = z.copy()
        z = z - thk if thk is not None else salt_top.copy()
        out[code][1] = z.copy()
    return out


def subcrop_map(surf, beds=SALT, tol=0.05):
    """Какое тело выходит под зеркало: индекс в beds, -1 если соли нет.

    Зеркало это не кровля назначенного тела, а кровля того, которое
    уцелело. В стороне от купола это первое соляное тело, над сводом -
    следующее за ним. Карта отвечает на вопрос прямо, вместо того чтобы
    вычитать поверхности глазами.
    """
    shape = surf[beds[0][0]][0].shape
    out = np.full(shape, -1, int)
    for k in range(len(beds) - 1, -1, -1):
        top, bot = surf[beds[k][0]]
        alive = np.isfinite(top) & np.isfinite(bot) & ((top - bot) > tol)
        out[alive] = k
    return out


def waterproof_thickness(surf, top_code=WATERPROOF_TOP):
    """Мощность водозащитной толщи: от кровли заданного пласта до зеркала.

    Над сводом купола она тонкая, и это главная карта такой модели: не
    структура сама по себе, а сколько породы отделяет выработку от воды.
    """
    return surf[CUT_CODE][0] - surf[top_code][0]


def _smooth_noise(rng, shape, sigma=0.4, passes=4):
    """Пространственно связанный шум: белый, сглаженный скользящим средним.

    Простое усреднение по соседям несколько раз даёт поле, у которого
    соседние ячейки похожи, а размах остаётся заданным. Этого довольно,
    чтобы поверхности не были идеально гладкими, и при этом структура не
    тонула.
    """
    e = rng.normal(0.0, 1.0, shape)
    for _ in range(passes):
        p = np.pad(e, 1, mode="edge")
        e = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]
             + 4.0 * e) / 8.0
    sd = float(e.std())
    return e * (sigma / sd) if sd > 1e-12 else e
