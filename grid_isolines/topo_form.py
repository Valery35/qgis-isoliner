# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Поверхность между структурными линиями: ядро.

Метод расстояний по постановке пятой редакции. Верх и низ - множества
линий и точек с отметками, растеризованные в сетку. Для каждой ячейки
считаются точные евклидовы расстояния до обоих множеств вместе с отметкой
ближайшего источника, вес alpha = d2/(d1+d2), отметка - линейная (или иная
монотонная) комбинация. Соответствие точек между сторонами не ищется,
перехлёст невозможен по построению.

Схема Фельзенсвальба-Хуттенлохера: точное преобразование расстояний
двумя проходами одномерных параболических огибающих, с переносом индекса
источника. Чистый NumPy, циклы только по строкам и столбцам.

Известная цена метода (меряется тестами, не обсуждается словами):
отклонение от линейчатой поверхности при переменной отметке на изгибе и
залом на медиальной оси у вогнутых углов.

Скорость: чистый Python в одномерных проходах, порядка 15 секунд на
полтора миллиона ячеек на сторону. Формы локальны, поэтому вызывающий
инструмент обязан резать растр по охвату формы с запасом её ширины, а не
считать преобразование на всём гриде.
"""

import numpy as np

_BIG = 1e18


def _edt_1d(f, idx):
    """Одномерное преобразование квадратов расстояний с переносом источника.

    f - массив стоимостей (0 в источниках, _BIG вне), idx - индексы для
    переноса (какой источник ближайший). Возвращает (d2, idx_out).
    Каноническая параболическая огибающая Фельзенсвальба-Хуттенлохера:
    бесконечные стоимости обрабатываются самой схемой, параболы с _BIG
    просто проигрывают всем, никаких особых случаев не нужно.
    """
    n = f.shape[0]
    d = np.empty(n)
    src = np.empty(n, dtype=np.int64)
    v = np.zeros(n, dtype=np.int64)
    z = np.empty(n + 1)
    k = 0
    z[0] = -np.inf
    z[1] = np.inf
    for q in range(1, n):
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * (q - v[k]))
        while s <= z[k]:
            k -= 1
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * (q - v[k]))
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        p = v[k]
        d[q] = (q - p) * (q - p) + f[p]
        src[q] = idx[p]
    return d, src


def distance_with_source(mask, values):
    """Точное евклидово расстояние до маски и отметка ближайшего источника.

    mask - булев растр источников, values - их отметки (NaN вне).
    Возвращает (dist, z_near): расстояние в ячейках (float) и отметку
    ближайшей ячейки-источника. Где источников нет вовсе - inf и NaN.
    """
    ny, nx = mask.shape
    f = np.where(mask, 0.0, _BIG)
    # проход по столбцам: расстояние до ближайшего источника в столбце,
    # с запоминанием строки источника
    d2 = np.empty((ny, nx))
    row_src = np.empty((ny, nx), dtype=np.int64)
    rows = np.arange(ny, dtype=np.int64)
    for c in range(nx):
        d2[:, c], row_src[:, c] = _edt_1d(f[:, c], rows)
    # проход по строкам: параболы поверх столбцовых расстояний, с переносом
    # колонки источника; строка источника берётся из первого прохода
    dist2 = np.empty((ny, nx))
    z_near = np.full((ny, nx), np.nan)
    cols = np.arange(nx, dtype=np.int64)
    for r in range(ny):
        drow, csrc = _edt_1d(d2[r, :], cols)
        dist2[r, :] = drow
        good = drow < _BIG
        if good.any():
            cc = csrc[good]
            rr = row_src[r, cc]
            z_near[r, good] = values[rr, cc]
    dist = np.sqrt(np.where(dist2 >= _BIG, np.inf, dist2))
    return dist, z_near


def rasterize_side(shape, features, cell=1.0):
    """Растеризация стороны формы: линии и точки с отметками.

    features - список dict(pts=[(x, y)... или (x, y, z)...], z=None|число).
    Координаты в единицах растра (метры при origin в нуле): колонка x/cell,
    строка y/cell. Приоритеты отметки: вершины с z, иначе поле z объекта.
    Объект без отметок пропускается (его дело - барьер, не форма).

    Возвращает (mask, values, skipped): булев растр, отметки, число
    пропущенных объектов.
    """
    ny, nx = shape
    mask = np.zeros(shape, dtype=bool)
    values = np.full(shape, np.nan)
    skipped = 0

    def put(r, c, z):
        if 0 <= r < ny and 0 <= c < nx:
            mask[r, c] = True
            values[r, c] = z

    for ft in features:
        pts = ft.get("pts") or []
        if not pts:
            skipped += 1
            continue
        has_z = len(pts[0]) >= 3
        const_z = ft.get("z")
        if not has_z and const_z is None:
            skipped += 1
            continue
        if len(pts) == 1:
            x, y = pts[0][0], pts[0][1]
            z = pts[0][2] if has_z else const_z
            put(int(round(y / cell)), int(round(x / cell)), float(z))
            continue
        for i in range(len(pts) - 1):
            x0, y0 = pts[i][0], pts[i][1]
            x1, y1 = pts[i + 1][0], pts[i + 1][1]
            z0 = pts[i][2] if has_z else const_z
            z1 = pts[i + 1][2] if has_z else const_z
            n_step = max(1, int(np.hypot(x1 - x0, y1 - y0) / cell * 2))
            for k in range(n_step + 1):
                t = k / float(n_step)
                put(int(round((y0 + t * (y1 - y0)) / cell)),
                    int(round((x0 + t * (x1 - x0)) / cell)),
                    float(z0 + t * (z1 - z0)))
    return mask, values, skipped


SHAPE_LINEAR = "linear"
SHAPE_SMOOTH = "smooth"


def shape_function(t, kind=SHAPE_LINEAR):
    """Функция формы поперёк тела. Жёсткое требование: f(0)=0 и f(1)=1,
    иначе поплывут отметки на самих линиях, а они - договор с
    пользователем."""
    t = np.clip(t, 0.0, 1.0)
    if kind == SHAPE_SMOOTH:
        return t * t * (3.0 - 2.0 * t)
    return t


def form_surface(top_mask, top_values, bot_mask, bot_values,
                 shape_kind=SHAPE_LINEAR):
    """Поверхность формы по двум растеризованным сторонам.

    Возвращает dict:
    z - отметки всюду (обрезать телом - дело вызывающего);
    alpha - вес верха (1 на верхе, 0 на низе);
    d_top, d_bot - расстояния в ячейках;
    seam - |z_top - z_bot| в ячейках, где обе стороны ближе ячейки:
        проверка согласованности отметок в местах схождения.
    Защита от деления: где d_top + d_bot = 0 (ячейка на обеих сторонах,
    сходящаяся промоина), отметка берётся средним, веса не считаются.
    """
    d1, z1 = distance_with_source(top_mask, top_values)
    d2, z2 = distance_with_source(bot_mask, bot_values)
    denom = d1 + d2
    with np.errstate(invalid="ignore", divide="ignore"):
        alpha_raw = np.where(denom > 1e-12, d2 / denom, 0.5)
    alpha = shape_function(alpha_raw, shape_kind)
    z = alpha * z1 + (1.0 - alpha) * z2
    both_near = (d1 < 1.0) & (d2 < 1.0)
    seam = np.where(both_near, np.abs(z1 - z2), 0.0)
    return {"z": z, "alpha": alpha_raw, "d_top": d1, "d_bot": d2,
            "seam": seam}


def body_mask(top_mask, bot_mask, d_top, d_bot, width_factor=1.05):
    """Тело формы: где сумма расстояний не превышает местной ширины.

    Ширина формы меняется вдоль неё (сходящаяся промоина, неровный борт),
    поэтому сравнивать сумму расстояний с одним числом нельзя. Местная
    ширина берётся тем же механизмом переноса от ближайшего источника,
    которым идут отметки: на ячейках верха записывается их расстояние до
    низа, и перенос раздаёт каждой ячейке ширину её участка формы. Тело -
    ячейки, где d_top + d_bot <= местная ширина с допуском.

    Одна формула закрывает все формы разбора: у конуса ширина это радиус,
    у кольца - расстояние между кольцами, у канавы каждая бровка несёт
    свою ширину, у сходящейся промоины ширина идёт к нулю, и тело
    сходится вместе с ней. Маска нужна только затем, чтобы знать, где
    писать отметки: геометрию поверхности она не задаёт.
    """
    w_on_top = np.where(top_mask, d_bot, np.nan)
    _d, w_local = distance_with_source(top_mask, w_on_top)
    total = d_top + d_bot
    ok = np.isfinite(total) & np.isfinite(w_local)
    # допуск: полторы ячейки на дискретизацию линий
    return ok & (total <= w_local * width_factor + 1.5)


def border_cells(body):
    """Граница тела: ячейки тела, у которых есть сосед вне тела.

    Граница уходит в 2.03 барьером, чтобы внешние данные не перетягивали
    поверхность через уступ. Возвращает булев растр.
    """
    p = np.pad(body, 1, mode="constant", constant_values=False)
    inner = (p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
             & p[:-2, :-2] & p[:-2, 2:] & p[2:, :-2] & p[2:, 2:])
    return body & ~inner


def build_form(top_features, bot_features, shape, cell=1.0,
               shape_kind=SHAPE_LINEAR, width_factor=1.05):
    """Одна форма целиком: от объектов сторон до тела с отметками.

    Возвращает dict: z (отметки в теле, NaN вне), body, border, seam_max,
    n_body, skipped_top, skipped_bot, width_med (медианная ширина в
    ячейках - для журнала и проверки разрешения).
    """
    tm, tv, sk_t = rasterize_side(shape, top_features, cell=cell)
    bm, bv, sk_b = rasterize_side(shape, bot_features, cell=cell)
    if not tm.any() or not bm.any():
        return {"z": np.full(shape, np.nan), "body": np.zeros(shape, bool),
                "border": np.zeros(shape, bool), "seam_max": 0.0,
                "n_body": 0, "skipped_top": sk_t, "skipped_bot": sk_b,
                "width_med": 0.0}
    r = form_surface(tm, tv, bm, bv, shape_kind=shape_kind)
    body = body_mask(tm, bm, r["d_top"], r["d_bot"],
                     width_factor=width_factor)
    z = np.where(body, r["z"], np.nan)
    widths = r["d_bot"][tm]
    widths = widths[np.isfinite(widths)]
    return {"z": z, "body": body, "border": border_cells(body),
            "seam_max": float(np.nanmax(np.where(body, r["seam"], 0.0)))
            if body.any() else 0.0,
            "n_body": int(body.sum()),
            "skipped_top": sk_t, "skipped_bot": sk_b,
            "width_med": float(np.median(widths)) if widths.size else 0.0}


def collect_forms(top_features, bot_features):
    """Разбор форм по полю связи.

    Каждый объект - dict(pts=..., z=..., link=...). Объекты без link не
    собираются в формы (их дело барьер). Возвращает (forms, orphans):
    forms - список (link, top_list, bot_list) в порядке появления;
    orphans - список dict(side, link, reason) для одиноких сторон.
    """
    tops, bots, order = {}, {}, []
    for side, feats, store in (("top", top_features, tops),
                               ("bot", bot_features, bots)):
        for ft in feats:
            link = ft.get("link")
            if link is None or link == "":
                continue
            if link not in store:
                store[link] = []
            if link not in order:
                order.append(link)
            store[link].append(ft)
    forms, orphans = [], []
    for link in order:
        t, b = tops.get(link, []), bots.get(link, [])
        if t and b:
            forms.append((link, t, b))
        elif t:
            orphans.append({"side": "top", "link": link,
                            "reason": "нет низа с этим значением связи"})
        else:
            orphans.append({"side": "bot", "link": link,
                            "reason": "нет верха с этим значением связи"})
    return forms, orphans


def forms_to_constraints(top_features, bot_features, extent, cell,
                         shape_kind=SHAPE_LINEAR, margin_factor=2.0):
    """Формы -> жёсткие узлы и барьеры для 2.03 Topo2Raster.

    Считает каждую форму в вырезке по её охвату с запасом (формы локальны,
    преобразование расстояний на всём гриде - пустая трата), затем
    переводит ячейки тел в точки (x, y, z), а границы тел в ломаные-барьеры
    по ячейкам.

    Возвращает dict: points (N, 3), barriers (список (K, 2) отрезков по
    паре точек - барьерная маска в 2.03 растеризует их заново), report -
    список словарей по формам для журнала (link, n_body, width_med,
    seam_max, skipped).
    """
    xmin, ymin, xmax, ymax = (float(v) for v in extent)
    forms, orphans = collect_forms(top_features, bot_features)
    all_pts = []
    barriers = []
    report = []
    for link, tfe, bfe in forms:
        xs, ys = [], []
        for ft in tfe + bfe:
            for p in ft["pts"]:
                xs.append(p[0])
                ys.append(p[1])
        if not xs:
            continue
        span = max(max(xs) - min(xs), max(ys) - min(ys), 10.0 * cell)
        pad = span * (margin_factor - 1.0) * 0.5 + 5.0 * cell
        fx0 = max(xmin, min(xs) - pad)
        fy0 = max(ymin, min(ys) - pad)
        fx1 = min(xmax, max(xs) + pad)
        fy1 = min(ymax, max(ys) + pad)
        nx = max(8, int(np.ceil((fx1 - fx0) / cell)))
        ny = max(8, int(np.ceil((fy1 - fy0) / cell)))

        def shift(feats):
            out = []
            for ft in feats:
                pts = [(p[0] - fx0, p[1] - fy0) + ((p[2],) if len(p) > 2
                                                   else ())
                       for p in ft["pts"]]
                out.append({"pts": pts, "z": ft.get("z")})
            return out

        fr = build_form(shift(tfe), shift(bfe), (ny, nx), cell=cell,
                        shape_kind=shape_kind)
        rr, cc = np.nonzero(fr["body"])
        if rr.size:
            all_pts.append(np.stack([
                fx0 + (cc + 0.5) * cell,
                fy0 + (rr + 0.5) * cell,
                fr["z"][rr, cc]], axis=1))
        br, bc = np.nonzero(fr["border"])
        for r0, c0 in zip(br.tolist(), bc.tolist()):
            x = fx0 + (c0 + 0.5) * cell
            y = fy0 + (r0 + 0.5) * cell
            barriers.append(np.array([[x - 0.5 * cell, y],
                                      [x + 0.5 * cell, y]]))
            barriers.append(np.array([[x, y - 0.5 * cell],
                                      [x, y + 0.5 * cell]]))
        report.append({"link": link, "n_body": fr["n_body"],
                       "width_med": fr["width_med"],
                       "seam_max": fr["seam_max"],
                       "skipped": fr["skipped_top"] + fr["skipped_bot"]})
    points = np.concatenate(all_pts, axis=0) if all_pts else \
        np.zeros((0, 3))
    return {"points": points, "barriers": barriers, "report": report,
            "orphans": orphans}
