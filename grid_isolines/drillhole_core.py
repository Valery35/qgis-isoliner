# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Ядро чтения и развёртки данных бурения. Чистая математика, без QGIS и Qt.

Модель данных - согласованный формат обмена (см. AGENTS.md, раздел «Формат обмена:
модель данных бурения»). Две таблицы:

* collar - устья: hole_id (строка), z (отметка устья), eoh (глубина забоя по
  стволу), координаты в геометрии точки;
* interval - интервалы по стволу: hole_id, from, to, code. Глубины по стволу
  от устья, вниз положительные, НЕ отметками.

Читатель терпимый, по правилам чтения: пустые и нечисловые глубины пропускаются,
перепутанные from и to меняются местами, интервалы за забоем рисуются как
есть, перехлёсты не разрешаются и не прячутся, интервалы без устья
пропускаются. Всё пропущенное считается и отдаётся одной сводкой - инструмент
выводит её на экран через processing feedback (принцип машины УК: файл-лог
недоступен, экранная сводка попадает на скриншот).

Ядро зовут инструмент Processing (выноска скважин на разрез) и, без
изменений, модуль Isoliner 3D: там глубина превращается в Z напрямую.
Тесты работают с ядром напрямую, без QGIS.

Соглашение о координатах чертежа то же, что в section_core: ось X -
расстояние вдоль линии, ось Y - отметка, умноженная на vex. Раскладка
нескольких разрезов сдвигает готовые координаты на (ox, oy) снаружи.
"""
import hashlib
import math


# --- ожидаемые имена полей ----------------------------------------------

COLLAR_ID = "hole_id"
COLLAR_Z = "z"
COLLAR_EOH = "eoh"
COLLAR_LABEL = "number"
INTERVAL_ID = "hole_id"
INTERVAL_FROM = "from"
INTERVAL_TO = "to"
INTERVAL_CODE = "code"

# Терпимость к именам: ожидаемые имена первыми, дальше частые варианты
# выгрузок горных пакетов. Сравнение без учёта регистра.
FIELD_SYNONYMS = {
    COLLAR_ID: ("hole_id", "holeid", "hole", "bhid", "dhid", "well"),
    COLLAR_Z: ("z", "elev", "elevation", "rl", "collar_z"),
    COLLAR_EOH: ("eoh", "depth", "max_depth", "td", "total_depth"),
    # подпись устья: короткий номер, а не составной hole_id. Выгрузка
    # Геоконструктора везёт number, горные пакеты - name или label
    COLLAR_LABEL: ("number", "name", "label"),
    INTERVAL_FROM: ("from", "from_", "depth_from", "from_m"),
    INTERVAL_TO: ("to", "to_", "depth_to", "to_m"),
    INTERVAL_CODE: ("code", "litho", "lith", "geol", "class", "seam"),
}


def find_field(names, wanted):
    """Имя поля из списка names под ожидаемое имя wanted или None.

    Сначала точное совпадение без учёта регистра, затем синонимы в порядке
    словаря. Возвращается имя в исходном написании слоя.
    """
    lower = {str(n).lower(): n for n in names}
    for cand in FIELD_SYNONYMS.get(wanted, (wanted,)):
        if cand in lower:
            return lower[cand]
    return None


def resolve_field(names, chosen, wanted):
    """Итоговое имя поля: выбор пользователя, если такое поле есть в слое
    (без учёта регистра), иначе автопоиск по ожидаемому имени и синонимам.
    None - не нашлось ничего. Так диалог инструмента живёт без обязательных
    выпадашек: данные с ожидаемыми именами находят поля сами.
    """
    if chosen:
        lower = {str(n).lower(): n for n in names}
        nm = lower.get(str(chosen).strip().lower())
        if nm is not None:
            return nm
    return find_field(names, wanted)


# --- терпимый разбор чисел ----------------------------------------------

def parse_num(v):
    """Число из значения атрибута или None. Терпимо: строки с запятой вместо
    точки и с пробелами принимаются, пустое, None, нечисловое и не конечное
    (nan, inf) дают None."""
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else None
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def parse_id(v):
    """Идентификатор скважины: непустая строка без крайних пробелов или None.
    Числовые идентификаторы приводятся к строке без хвоста «.0»."""
    if v is None:
        return None
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    s = str(v).strip()
    return s or None


# --- обрезка линиями чертежа ---------------------------------------------

def profile_y(parts, x, pick_max=True):
    """Высота y ломаных профиля в позиции x или None, если x вне охвата.

    parts - список ломаных [(x, y), ...] в координатах чертежа. Линейная
    интерполяция по сегментам, накрывающим x. Если x накрыт несколькими
    сегментами, берётся верхний (pick_max) или нижний: для кровли чертежа
    нужен верх, для подошвы низ.
    """
    best = None
    for pts in parts or ():
        for i in range(1, len(pts)):
            x1, y1 = pts[i - 1]
            x2, y2 = pts[i]
            if x1 == x2:
                continue
            lo, hi = (x1, x2) if x1 < x2 else (x2, x1)
            if lo - 1e-9 <= x <= hi + 1e-9:
                y = y1 + (x - x1) * (y2 - y1) / (x2 - x1)
                if best is None:
                    best = y
                else:
                    best = max(best, y) if pick_max else min(best, y)
    return best


def clip_columns_profile(cols, top_parts=None, bot_parts=None,
                         counters=None):
    """Обрезка готовых колонок линиями кровли и подошвы чертежа.

    Работает в локальных координатах чертежа (d по оси расстояния, y уже с
    vex, до раскладки ox и oy). В каждой позиции колонки берётся высота
    линии, верх колонки зажимается кровлей, низ подошвой. Колонка вне
    охвата линии по x (торцы чертежа) этой линией не трогается. Интервал
    целиком за линией выпадает, потерявшая всё колонка выпадает из чертежа.

    counters - те же ключи, что у рамки: n_clip_cut, n_clip_out,
    n_holes_out. Возвращает новый список колонок.
    """
    out = []
    n_cut = n_clip_out = n_drop = 0
    for col in cols:
        ytop = profile_y(top_parts, col.d, True) if top_parts else None
        ybot = profile_y(bot_parts, col.d, False) if bot_parts else None
        if ytop is None and ybot is None:
            out.append(col)
            continue
        segs = []
        for (t, b, it) in col.segments:
            t2 = min(t, ytop) if ytop is not None else t
            b2 = max(b, ybot) if ybot is not None else b
            if t2 <= b2:
                n_clip_out += 1
                continue
            if t2 != t or b2 != b:
                n_cut += 1
            segs.append((t2, b2, it))
        stop, sbot = col.stick
        if ytop is not None:
            stop = min(stop, ytop)
        if ybot is not None:
            sbot = max(sbot, ybot)
        if stop <= sbot and not segs:
            n_drop += 1
            continue
        sbot = min(sbot, stop)
        out.append(Column(col.hole_id, col.d, col.offset, segs,
                          (stop, sbot), stop))
    if counters is not None:
        counters["n_clip_cut"] = counters.get("n_clip_cut", 0) + n_cut
        counters["n_clip_out"] = counters.get("n_clip_out", 0) + n_clip_out
        counters["n_holes_out"] = counters.get("n_holes_out", 0) + n_drop
        if len(out) != len(cols):
            counters["n_wells"] = (counters.get("n_wells", 0)
                                   - (len(cols) - len(out)))
    return out


# Кириллические буквы, неотличимые на экране от латинских. Идентификатор,
# набранный в разных раскладках, выглядит одинаково, а ключом не является.
LOOKALIKE = {
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
}


def _delatin(s):
    return "".join(LOOKALIKE.get(ch, ch) for ch in s)


def explain_id_mismatch(orphan_ids, collar_ids):
    """Назвать причину расхождения ключей, если она распознаётся.

    Счётчик осиротевших интервалов говорит, что ключи не сошлись, но не
    говорит почему. Между тем причина почти всегда одна из трёх, и все
    три проверяются сравнением строк.
    """
    for a in orphan_ids:
        for b in collar_ids:
            if a == b:
                continue
            if a.casefold() == b.casefold():
                return "различается только регистр: «%s» и «%s»" % (a, b)
            if _delatin(a) == _delatin(b):
                return ("совпадают на вид, но набраны в разных раскладках: "
                        "«%s» и «%s»" % (a, b))
            if a.replace(" ", "") == b.replace(" ", ""):
                return "различаются только пробелами: «%s» и «%s»" % (a, b)
    return None


# --- сводка чтения -------------------------------------------------------

class ReadSummary:
    """Счётчики терпимого читателя. Одна сводка на прогон, на экран."""

    def __init__(self):
        self.collar_total = 0        # строк устий прочитано
        self.collar_kept = 0         # устий принято
        self.collar_no_id = 0        # пропущено: пустой hole_id
        self.collar_bad_z = 0        # пропущено: нет отметки устья
        self.collar_no_xy = 0        # пропущено: нет координат
        self.collar_dup = 0          # пропущено: повтор hole_id (взято первое)
        self.collar_no_eoh = 0       # принято, но забой не задан
        self.int_total = 0           # строк интервалов прочитано
        self.int_kept = 0            # интервалов принято
        self.int_no_id = 0           # пропущено: пустой hole_id
        self.int_bad_depth = 0       # пропущено: глубины пустые или нечисловые
        self.int_zero = 0            # пропущено: нулевая длина (from == to)
        self.int_swapped = 0         # принято: from и to поменяны местами
        self.int_no_code = 0         # принято: пустой code
        self.int_orphan = 0          # пропущено: устья с таким hole_id нет
        self.int_beyond_eoh = 0      # принято: конец глубже забоя (как есть)
        self.int_overlap = 0         # принято: перехлёст с соседом (как есть)
        self.int_gap = 0             # принято: разрыв до соседа (как есть)
        # Образцы несошедшихся ключей. Счётчик говорит, что интервалы
        # осиротели, но не говорит почему, а причина почти всегда в самих
        # значениях: хвостовой пробел, другой регистр, ноль в номере,
        # похожие буквы кириллицы и латиницы. Без образцов это ищут
        # глазами по таблице.
        self.orphan_ids = []
        self.collar_ids = []

    def lines(self, tr=None):
        """Строки сводки для экрана. Итог всегда, остальное если не ноль.
        tr - необязательный переводчик шаблонов (инструмент передаёт свой)."""
        t = tr if tr is not None else (lambda s: s)
        out = [t("Устьев принято %d из %d, интервалов %d из %d.")
               % (self.collar_kept, self.collar_total,
                  self.int_kept, self.int_total)]
        parts = [
            (self.collar_no_id, "устьев без hole_id: %d"),
            (self.collar_bad_z, "устьев без отметки z: %d"),
            (self.collar_no_xy, "устьев без координат: %d"),
            (self.collar_dup, "повторов hole_id (взято первое): %d"),
            (self.collar_no_eoh, "устьев без забоя eoh: %d"),
            (self.int_no_id, "интервалов без hole_id: %d"),
            (self.int_bad_depth, "интервалов с пустыми глубинами: %d"),
            (self.int_zero, "интервалов нулевой длины: %d"),
            (self.int_orphan, "интервалов без устья: %d"),
        ]
        skipped = [t(tpl) % n for n, tpl in parts if n]
        if skipped:
            out.append(t("Пропущено: %s.") % ", ".join(skipped))
        parts = [
            (self.int_swapped, "переставлено from и to: %d"),
            (self.int_no_code, "интервалов без кода: %d"),
            (self.int_beyond_eoh, "интервалов за забоем (как есть): %d"),
            (self.int_overlap, "перехлёстов (как есть): %d"),
            (self.int_gap, "разрывов между интервалами (как есть): %d"),
        ]
        noted = [t(tpl) % n for n, tpl in parts if n]
        if noted:
            out.append(t("Принято с оговоркой: %s.") % ", ".join(noted))
        if self.int_orphan and self.orphan_ids:
            out.append(t(
                "Не сошлись hole_id. В интервалах: %s. В устьях: %s. "
                "Сравните написание: хвостовой пробел, регистр, ноль в "
                "номере, похожие буквы кириллицы и латиницы.")
                % (", ".join("«%s»" % s for s in self.orphan_ids),
                   ", ".join("«%s»" % s for s in self.collar_ids)))
            why = explain_id_mismatch(self.orphan_ids, self.collar_ids)
            if why:
                out.append(t("Похоже, %s.") % why)
        return out


# --- модель --------------------------------------------------------------

class Collar:
    """Устье: идентификатор, план, отметка, забой (nan если не задан)."""

    __slots__ = ("hole_id", "x", "y", "z", "eoh")

    def __init__(self, hole_id, x, y, z, eoh):
        self.hole_id = hole_id
        self.x = x
        self.y = y
        self.z = z
        self.eoh = eoh


class Interval:
    """Интервал по стволу: глубины от устья, вниз положительные."""

    __slots__ = ("hole_id", "frm", "to", "code", "extra")

    def __init__(self, hole_id, frm, to, code, extra=None):
        self.hole_id = hole_id
        self.frm = frm
        self.to = to
        self.code = code
        self.extra = extra if extra is not None else {}


# --- чтение --------------------------------------------------------------

def read_collars(rows, summary):
    """Устья из строк (hole_id, x, y, z, eoh) в словарь hole_id -> Collar.

    Правила: пустой hole_id, отсутствие отметки z или координат - пропуск.
    Повтор hole_id - берётся первое, повтор считается. Забой eoh может быть
    не задан (nan), тогда глубину ствола даст самый глубокий интервал.
    """
    out = {}
    for raw in rows:
        summary.collar_total += 1
        hid = parse_id(raw[0])
        if hid is None:
            summary.collar_no_id += 1
            continue
        x, y = parse_num(raw[1]), parse_num(raw[2])
        if x is None or y is None:
            summary.collar_no_xy += 1
            continue
        z = parse_num(raw[3])
        if z is None:
            summary.collar_bad_z += 1
            continue
        eoh = parse_num(raw[4])
        if eoh is None or eoh < 0:
            summary.collar_no_eoh += 1
            eoh = float("nan")
        if hid in out:
            summary.collar_dup += 1
            continue
        out[hid] = Collar(hid, x, y, z, eoh)
        summary.collar_kept += 1
    return out


def read_intervals(rows, summary):
    """Интервалы из строк (hole_id, from, to, code[, extra]) в список Interval.

    Правила чтения: пустые и нечисловые глубины - пропуск, перепутанные
    from и to меняются местами (считается), нулевая длина - пропуск, пустой
    code принимается пустой строкой (считается). Прочие колонки едут в extra
    как есть, ядро их не трактует.
    """
    out = []
    for raw in rows:
        summary.int_total += 1
        hid = parse_id(raw[0])
        if hid is None:
            summary.int_no_id += 1
            continue
        frm, to = parse_num(raw[1]), parse_num(raw[2])
        if frm is None or to is None:
            summary.int_bad_depth += 1
            continue
        if frm > to:
            frm, to = to, frm
            summary.int_swapped += 1
        if frm == to:
            summary.int_zero += 1
            continue
        code = raw[3]
        code = "" if code is None else str(code).strip()
        if not code:
            summary.int_no_code += 1
        extra = raw[4] if len(raw) > 4 and raw[4] else {}
        out.append(Interval(hid, frm, to, code, extra))
        summary.int_kept += 1
    return out


def assemble(collars, intervals, summary, eps=1e-9):
    """Интервалы по скважинам: словарь hole_id -> список Interval по frm.

    Интервалы без устья пропускаются (считаются, int_kept уменьшается,
    чтобы итог сводки означал «дошло до чертежа»). Интервалы за забоем,
    перехлёсты и разрывы между соседями остаются как есть, только
    считаются: колонка рисуется по данным, дыры не заполняются.
    """
    holes = {}
    summary.collar_ids = sorted(collars)[:5]
    for it in intervals:
        if it.hole_id not in collars:
            summary.int_orphan += 1
            summary.int_kept -= 1
            if len(summary.orphan_ids) < 5 \
                    and it.hole_id not in summary.orphan_ids:
                summary.orphan_ids.append(it.hole_id)
            continue
        holes.setdefault(it.hole_id, []).append(it)
    for hid, its in holes.items():
        its.sort(key=lambda it: (it.frm, it.to))
        eoh = collars[hid].eoh
        prev_to = None
        for it in its:
            if math.isfinite(eoh) and it.to > eoh + eps:
                summary.int_beyond_eoh += 1
            if prev_to is not None and it.frm < prev_to - eps:
                summary.int_overlap += 1
            elif prev_to is not None and it.frm > prev_to + eps:
                # дыра в колонке: между интервалами нет описания. Ничего не
                # выдумываем, только считаем - на чертеже это выглядит как
                # разрыв колонки, и вопрос всегда к данным, а не к рисованию
                summary.int_gap += 1
            prev_to = max(prev_to, it.to) if prev_to is not None else it.to
    return holes


def hole_depth(collar, its):
    """Глубина ствола для отрисовки: забой, а без забоя - низ интервалов."""
    if math.isfinite(collar.eoh) and collar.eoh > 0:
        return collar.eoh
    return max((it.to for it in its), default=0.0)


# --- развёртка -----------------------------------------------------------

def unfold(z, frm, to):
    """Глубины по стволу в отметки: (кровля, подошва) = (z - frm, z - to).
    Скважина вертикальная (инклинометрии в первом заходе нет)."""
    return z - frm, z - to


def read_surveys(rows, summary):
    """Таблица инклинометрии: hole_id, глубина по стволу, азимут, зенит.

    Азимут отсчитывается от севера по часовой стрелке, зенитный угол от
    вертикали вниз: ноль это вертикальная скважина, девяносто -
    горизонтальная. Такое соглашение принято в буровой документации, и
    отступать от него незачем.

    Строки без имени скважины, с нечисловой глубиной или углом
    пропускаются и считаются в сводку. Замеры сортируются по глубине, и
    повторная глубина у одной скважины отбрасывается: две ориентации в
    одной точке ствола противоречивы, и выбирать между ними наугад
    неправильно.
    """
    out = {}
    seen = {}
    for row in rows:
        # Строка кортежем, как и у остальных таблиц модуля:
        # (hole_id, глубина, азимут, зенит).
        hid = parse_id(row[0]) if len(row) > 0 else ""
        md = parse_num(row[1]) if len(row) > 1 else None
        azi = parse_num(row[2]) if len(row) > 2 else None
        inc = parse_num(row[3]) if len(row) > 3 else None
        if not hid:
            summary["survey_no_id"] = summary.get("survey_no_id", 0) + 1
            continue
        if md is None or azi is None or inc is None:
            summary["survey_bad_num"] = summary.get("survey_bad_num", 0) + 1
            continue
        key = (hid, round(float(md), 6))
        if key in seen:
            summary["survey_dup_md"] = summary.get("survey_dup_md", 0) + 1
            continue
        seen[key] = True
        out.setdefault(hid, []).append((float(md), float(azi), float(inc)))
    for hid in out:
        out[hid].sort(key=lambda r: r[0])
    return out


def axis_from_survey(x0, y0, z0, stations, eoh=None):
    """Ось скважины по инклинометрии, метод минимальной кривизны.

    Между соседними замерами ствол считается дугой окружности, а не
    ломаной: касательный метод при редких замерах уводит забой на
    десятки метров, и это давно известная ошибка.

    stations - список (глубина, азимут, зенит), отсортированный по
    глубине. Если первый замер лежит ниже устья, участок до него
    считается по его же ориентации. Если задан eoh и он ниже последнего
    замера, ствол продолжается по последней ориентации.

    Возвращает список (глубина, x, y, z) от устья вниз.
    """
    if not stations:
        return [(0.0, float(x0), float(y0), float(z0))]
    st = list(stations)
    if st[0][0] > 0.0:
        st.insert(0, (0.0, st[0][1], st[0][2]))
    if eoh is not None and float(eoh) > st[-1][0]:
        st.append((float(eoh), st[-1][1], st[-1][2]))

    axis = [(st[0][0], float(x0), float(y0), float(z0))]
    for k in range(1, len(st)):
        md1, a1, i1 = st[k - 1]
        md2, a2, i2 = st[k]
        dmd = md2 - md1
        if dmd <= 0.0:
            continue
        ra1, ri1 = math.radians(a1), math.radians(i1)
        ra2, ri2 = math.radians(a2), math.radians(i2)
        cosb = (math.cos(ri2 - ri1)
                - math.sin(ri1) * math.sin(ri2) * (1.0 - math.cos(ra2 - ra1)))
        cosb = max(-1.0, min(1.0, cosb))
        beta = math.acos(cosb)
        rf = 1.0 if beta < 1e-9 else (2.0 / beta) * math.tan(beta / 2.0)
        half = dmd / 2.0 * rf
        dn = half * (math.sin(ri1) * math.cos(ra1)
                     + math.sin(ri2) * math.cos(ra2))
        de = half * (math.sin(ri1) * math.sin(ra1)
                     + math.sin(ri2) * math.sin(ra2))
        dv = half * (math.cos(ri1) + math.cos(ri2))
        _md, px, py, pz = axis[-1]
        axis.append((md2, px + de, py + dn, pz - dv))
    return axis


def point_at_depth(axis, md):
    """Точка оси на заданной глубине по стволу: (x, y, z).

    Между узлами оси идёт линейная развёртка. Глубже последнего узла
    ствол продолжается по направлению последнего звена: интервал,
    выходящий за забой, рисуется как есть, а не обрезается молча.
    """
    if not axis:
        return None
    md = float(md)
    if md <= axis[0][0]:
        return axis[0][1], axis[0][2], axis[0][3]
    for k in range(1, len(axis)):
        m0, x0, y0, z0 = axis[k - 1]
        m1, x1, y1, z1 = axis[k]
        if md <= m1:
            fr = 0.0 if m1 == m0 else (md - m0) / (m1 - m0)
            return (x0 + fr * (x1 - x0), y0 + fr * (y1 - y0),
                    z0 + fr * (z1 - z0))
    if len(axis) == 1:
        return axis[0][1], axis[0][2], axis[0][3]
    m0, x0, y0, z0 = axis[-2]
    m1, x1, y1, z1 = axis[-1]
    seg = m1 - m0
    if seg <= 0:
        return x1, y1, z1
    fr = (md - m1) / seg
    return x1 + fr * (x1 - x0), y1 + fr * (y1 - y0), z1 + fr * (z1 - z0)


def axis_offset_to_line(axis, vertices):
    """Наименьшее удаление оси скважины от линии разреза.

    Отбирать по устью неправильно: наклонная скважина, устье которой
    далеко, а забой у самой линии, при отборе по устью потерялась бы.
    """
    best = float("inf")
    for _md, x, y, _z in axis:
        _d, off = project_to_polyline(vertices, x, y)
        best = min(best, off)
    return best


def intervals_from_levels(z, levels, codes, min_len=0.01):
    """Стопка отметок границ в интервалы модели бурения: список (from, to, code).

    Обратный ход к unfold и мост от широкого формата (h1...h6) к модели
    collar/interval. levels - отметки границ сверху вниз, codes - коды
    пластов между соседними границами (len(codes) == len(levels) - 1),
    z - отметка устья. Интервалы тоньше min_len и интервалы с нечисловой
    границей пропускаются, глубины считаются от устья вниз положительными.
    """
    out = []
    for k in range(len(levels) - 1):
        top, bot = levels[k], levels[k + 1]
        if top is None or bot is None:
            continue
        top, bot = float(top), float(bot)
        if not (math.isfinite(top) and math.isfinite(bot)):
            continue
        frm, to = z - top, z - bot
        if frm > to:
            frm, to = to, frm
        if to - frm < min_len:
            continue
        out.append((frm, to, codes[k]))
    return out


# --- порядок и цвет кодов ------------------------------------------------

def code_order(holes):
    """Коды в порядке первого появления сверху вниз, без справочника.

    Скважины обходятся по hole_id, интервалы каждой - сверху вниз, поэтому
    порядок детерминирован и не пляшет между прогонами.
    """
    seen = []
    for hid in sorted(holes):
        for it in sorted(holes[hid], key=lambda i: (i.frm, i.to)):
            if it.code not in seen:
                seen.append(it.code)
    return seen


def code_color(code):
    """Детерминированный цвет кода «#rrggbb». Один и тот же код всегда даёт
    один и тот же цвет, в любом прогоне и на любой машине."""
    h = hashlib.md5(str(code).encode("utf-8")).digest()  # nosec - не криптография
    hue = h[0] / 255.0
    sat = 0.45 + 0.35 * (h[1] / 255.0)
    val = 0.65 + 0.25 * (h[2] / 255.0)
    i = int(hue * 6.0) % 6
    f = hue * 6.0 - int(hue * 6.0)
    p, q, t = val * (1 - sat), val * (1 - f * sat), val * (1 - (1 - f) * sat)
    r, g, b = [(val, t, p), (q, val, p), (p, val, t),
               (p, q, val), (t, p, val), (val, p, q)][i]
    return "#%02x%02x%02x" % (round(r * 255), round(g * 255), round(b * 255))


# --- проекция на линию разреза -------------------------------------------

def project_to_polyline(vertices, px, py):
    """Проекция точки на ломаную: (d, offset).

    d - расстояние вдоль ломаной до ближайшей точки проекции от начала,
    offset - расстояние от точки до этой проекции. За концами ломаной d
    зажимается в [0, длина], offset считается до ближайшего конца, поэтому
    коридор отсекает и торцевые скважины по настоящему удалению.
    """
    best_d = 0.0
    best_off = float("inf")
    acc = 0.0
    for i in range(1, len(vertices)):
        ax, ay = vertices[i - 1]
        bx, by = vertices[i]
        vx, vy = bx - ax, by - ay
        seg = math.hypot(vx, vy)
        if seg == 0.0:
            continue
        t = ((px - ax) * vx + (py - ay) * vy) / (seg * seg)
        t = min(max(t, 0.0), 1.0)
        cx, cy = ax + t * vx, ay + t * vy
        off = math.hypot(px - cx, py - cy)
        if off < best_off:
            best_off = off
            best_d = acc + t * seg
        acc += seg
    return best_d, best_off


# --- колонки на чертёж ----------------------------------------------------

class Column:
    """Колонка одной скважины в координатах чертежа (до раскладки).

    d - позиция по оси расстояния, offset - удаление от линии, segments -
    список (ytop, ybot, Interval) уже с vex, stick - (ytop, ybot) ствола от
    устья до забоя, ytop_label - высота точки подписи (устье).
    """

    __slots__ = ("hole_id", "d", "offset", "segments", "stick", "ytop_label",
                 "seg_xy", "path")

    def __init__(self, hole_id, d, offset, segments, stick, ytop_label,
                 seg_xy=None, path=None):
        self.hole_id = hole_id
        self.d = d
        self.offset = offset
        self.segments = segments
        self.stick = stick
        self.ytop_label = ytop_label
        # Наклонная скважина: у каждого интервала своя позиция по оси
        # расстояния сверху и снизу, а ствол это ломаная. У вертикальной
        # обе координаты совпадают с d, и прежнее поведение сохраняется.
        self.seg_xy = seg_xy
        self.path = path


def columns_for_section(collars, holes, vertices, corridor, vex,
                        counters=None, zclip=None, surveys=None):
    """Колонки скважин для одной линии разреза.

    Устье проецируется на линию, дальние скважины отсекаются коридором
    (corridor <= 0 - берутся все). Глубины переводятся в отметки вычитанием
    из z и умножаются на vex, по соглашению чертежа. Возвращает список
    Column по возрастанию d.

    zclip - необязательная пара отметок (zmin, zmax): рамка чертежа, уже
    расширенная допуском. Интервал, пересекающий кромку, подрезается до
    кромки, интервал целиком за рамкой пропускается, ствол и точка подписи
    зажимаются той же рамкой. Скважина, у которой за рамкой оказалось всё,
    из чертежа выпадает. None - без обрезки, поведение прежних версий.

    counters - необязательный словарь, в него добавляются ключи n_wells и
    n_outside по этой линии, а также min_off - наименьшее удаление устья от
    линии. Число диагностическое: когда коридор пуст, оно с экрана говорит,
    узок ли коридор или устья живут в другом координатном пространстве.
    При обрезке добавляются n_clip_cut (интервалов подрезано), n_clip_out
    (интервалов за рамкой) и n_holes_out (скважин целиком за рамкой).
    """
    zmn = zmx = None
    if zclip is not None:
        zmn, zmx = float(zclip[0]), float(zclip[1])
        if not (math.isfinite(zmn) and math.isfinite(zmx) and zmx > zmn):
            zmn = zmx = None
    cols = []
    n_out = 0
    n_cut = n_clip_out = n_holes_out = 0
    min_off = float("inf")
    for hid in sorted(holes):
        c = collars[hid]
        st = (surveys or {}).get(hid)
        axis = None
        if st:
            axis = axis_from_survey(c.x, c.y, c.z, st,
                                    eoh=(c.eoh if math.isfinite(c.eoh)
                                         else None))
        if axis and len(axis) > 1:
            # Отбор по всей оси: наклонная скважина с далёким устьем, но
            # забоем у самой линии, при отборе по устью потерялась бы.
            off = axis_offset_to_line(axis, vertices)
            d, _ = project_to_polyline(vertices, c.x, c.y)
        else:
            axis = None
            d, off = project_to_polyline(vertices, c.x, c.y)
        if off < min_off:
            min_off = off
        if corridor > 0 and off > corridor:
            n_out += 1
            continue

        def _at(md):
            """Позиция по оси расстояния и отметка на глубине md."""
            if axis is None:
                return d, c.z - float(md)
            px, py, pz = point_at_depth(axis, md)
            dd, _o = project_to_polyline(vertices, px, py)
            return dd, pz
        its = sorted(holes[hid], key=lambda i: (i.frm, i.to))
        segs = []
        seg_xy = [] if axis is not None else None
        for it in its:
            dtop, ztop = _at(it.frm)
            dbot, zbot = _at(it.to)
            if zmn is not None:
                if zbot >= zmx or ztop <= zmn:
                    n_clip_out += 1
                    continue
                t2, b2 = min(ztop, zmx), max(zbot, zmn)
                if t2 != ztop or b2 != zbot:
                    n_cut += 1
                ztop, zbot = t2, b2
            segs.append((ztop * vex, zbot * vex, it))
            if seg_xy is not None:
                seg_xy.append(((dtop, ztop * vex), (dbot, zbot * vex), it))
        depth = hole_depth(c, its)
        stop, sbot = c.z, c.z - depth
        if zmn is not None:
            stop, sbot = min(stop, zmx), max(sbot, zmn)
            if stop <= sbot and not segs:
                n_holes_out += 1
                continue
        stick = (stop * vex, min(sbot, stop) * vex)
        path = None
        if axis is not None:
            path = []
            for md, px, py, pz in axis:
                dd, _o = project_to_polyline(vertices, px, py)
                if zmn is not None:
                    pz = min(max(pz, zmn), zmx)
                path.append((dd, pz * vex))
        if axis is not None and counters is not None and len(axis) > 1:
            # Смещение забоя по горизонтали: без него наклон на чертеже с
            # большим преувеличением не разглядеть, и человеку не на что
            # опереться, кроме глаза.
            d_top, _ = project_to_polyline(vertices, axis[0][1], axis[0][2])
            d_bot, _ = project_to_polyline(vertices, axis[-1][1], axis[-1][2])
            shift = abs(d_bot - d_top)
            counters["n_incl"] = counters.get("n_incl", 0) + (
                1 if shift > 1e-6 else 0)
            counters["max_shift"] = max(counters.get("max_shift", 0.0), shift)
        cols.append(Column(hid, d, off, segs, stick, stick[0],
                           seg_xy=seg_xy, path=path))
    cols.sort(key=lambda col: (col.d, col.hole_id))
    if counters is not None:
        counters["n_wells"] = counters.get("n_wells", 0) + len(cols)
        counters["n_outside"] = counters.get("n_outside", 0) + n_out
        if math.isfinite(min_off):
            counters["min_off"] = min(
                counters.get("min_off", float("inf")), min_off)
        if zmn is not None:
            counters["n_clip_cut"] = counters.get("n_clip_cut", 0) + n_cut
            counters["n_clip_out"] = (counters.get("n_clip_out", 0)
                                      + n_clip_out)
            counters["n_holes_out"] = (counters.get("n_holes_out", 0)
                                       + n_holes_out)
    return cols
