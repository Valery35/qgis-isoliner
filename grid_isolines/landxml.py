# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Чтение и запись LandXML 1.2 без QGIS.

Формат принят обменным у программ обработки тахеометрической съёмки:
Credo, Trimble Business Center, Topcon Magnet, Leica Infinity, Civil 3D,
Bentley. Схема держится с 2008 года и почти не менялась.

Читается общая часть, полезная любому пользователю, и предметная, под
которую у модуля уже есть приёмники:

    CgPoints        точки съёмки с именем, кодом и описанием
    PlanFeatures    именованные линии с кодами
    Surfaces        триангуляция, гранями (её режет разрезом 4.06)
    Alignments      трасса с пикетажем, продольный профиль, поперечники

Кадастровые участки, трубопроводные сети, дорожные объекты, полевые
измерения и межевые знаки не читаются намеренно: это чужие предметные
области, и поддержать их наполовину хуже, чем не поддерживать. Такие
разделы считаются и называются в сводке, а не пропускаются молча.

Три места, где формат наказывает невнимательного:

Порядок координат. В схеме точка записана как «север, восток», то есть
Y раньше X. Часть программ пишет наоборот, поэтому порядок вынесен
параметром, а читатель докладывает получившийся охват: зеркальный
поворот виден по нему сразу.

Единицы. Файл в футах читается без единой ошибки и даёт отметки втрое
меньше. Единицы читаются из заголовка и пересчитываются в метры, а их
отсутствие попадает в предупреждения.

Кривые. Дуга и переходная кривая в ломаную спрямляются, и спрямление
укорачивает трассу. Дуга разбивается по заданной стрелке прогиба,
переходная кривая заменяется хордой, и число таких замен докладывается.
"""
import math

# Стрелка прогиба по умолчанию: на дуге радиусом 100 метров даёт шаг около
# шести метров. Мельче незачем, крупнее уже видно на чертеже.
DEFAULT_SAG = 0.05

# Множители перевода в метры. Названия единиц взяты из схемы, регистр в
# файлах разный, поэтому сравнение идёт по нижнему регистру.
_TO_METRE = {
    "meter": 1.0, "metre": 1.0, "meters": 1.0,
    "kilometer": 1000.0, "kilometre": 1000.0,
    "centimeter": 0.01, "centimetre": 0.01,
    "millimeter": 0.001, "millimetre": 0.001,
    "foot": 0.3048, "feet": 0.3048, "ift": 0.3048, "internationalfoot": 0.3048,
    "ussurveyfoot": 1200.0 / 3937.0, "surveyfoot": 1200.0 / 3937.0,
    "inch": 0.0254, "yard": 0.9144, "mile": 1609.344,
}

# Разделы схемы, которые читаются. Всё остальное на верхнем уровне
# попадает в сводку непрочитанного.
_KNOWN_TOP = ("Units", "CoordinateSystem", "Project", "Application",
              "CgPoints", "PlanFeatures", "Surfaces", "Alignments",
              "FeatureDictionary", "Feature", "Amendment")


class LandXmlError(Exception):
    """Файл не разобрался: испорченный XML либо не LandXML вовсе."""


# --- разбор XML своими силами -------------------------------------------
#
# Модули xml из стандартной библиотеки в плагин не берутся: сканер каталога
# plugins.qgis.org блокирует их, потому что штатный разборщик поддаётся
# раздутым сущностям и внешним ссылкам, а defusedxml в поставке QGIS нет.
# Тот же запрет уже сработал на чтении палитры Leapfrog.
#
# Разборщик ниже намеренно ограничен: он раскрывает только пять встроенных
# сущностей и числовые ссылки, а объявление своих сущностей отвергает
# отказом. Раздуть такой файл нечем.

_ENT = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_MAX_DEPTH = 100


class _El(object):
    """Элемент дерева: имя, атрибуты, текст, дети."""

    __slots__ = ("tag", "attrib", "text", "children")

    def __init__(self, tag, attrib):
        self.tag = tag
        self.attrib = attrib
        self.text = ""
        self.children = []

    def __iter__(self):
        return iter(self.children)

    def get(self, key, default=None):
        return self.attrib.get(key, default)


def _unescape(s):
    if "&" not in s:
        return s
    out, i = [], 0
    while True:
        j = s.find("&", i)
        if j < 0:
            out.append(s[i:])
            break
        out.append(s[i:j])
        k = s.find(";", j + 1, j + 12)
        if k < 0:
            out.append("&")
            i = j + 1
            continue
        name = s[j + 1:k]
        if name in _ENT:
            out.append(_ENT[name])
        elif name.startswith("#"):
            try:
                code = (int(name[2:], 16) if name[1:2].lower() == "x"
                        else int(name[1:]))
                out.append(chr(code))
            except (ValueError, OverflowError):
                out.append(s[j:k + 1])
        else:
            # чужая сущность не раскрывается: раскрывать нечем и незачем
            out.append(s[j:k + 1])
        i = k + 1
    return "".join(out)


def _attrs(chunk):
    """Атрибуты из хвоста открывающего тега."""
    out, i, n = {}, 0, len(chunk)
    while i < n:
        while i < n and chunk[i] in " \t\r\n":
            i += 1
        j = i
        while j < n and chunk[j] not in " \t\r\n=/>":
            j += 1
        if j == i:
            break
        name = chunk[i:j]
        i = j
        while i < n and chunk[i] in " \t\r\n":
            i += 1
        if i >= n or chunk[i] != "=":
            out[name] = ""
            continue
        i += 1
        while i < n and chunk[i] in " \t\r\n":
            i += 1
        if i < n and chunk[i] in "\"'":
            q = chunk[i]
            k = chunk.find(q, i + 1)
            if k < 0:
                break
            out[name] = _unescape(chunk[i + 1:k])
            i = k + 1
        else:
            k = i
            while k < n and chunk[k] not in " \t\r\n/>":
                k += 1
            out[name] = _unescape(chunk[i:k])
            i = k
    return out


def _parse_xml(text):
    """Дерево из текста. Отказ при объявлении сущностей и при обрыве."""
    root, stack, i, n = None, [], 0, len(text)
    while True:
        lt = text.find("<", i)
        if lt < 0:
            break
        if stack and lt > i:
            stack[-1].text += _unescape(text[i:lt])
        if text.startswith("<!--", lt):
            i = text.find("-->", lt)
            if i < 0:
                raise LandXmlError("Файл оборван внутри комментария XML")
            i += 3
            continue
        if text.startswith("<?", lt):
            i = text.find("?>", lt)
            if i < 0:
                raise LandXmlError("Файл оборван внутри объявления XML")
            i += 2
            continue
        if text.startswith("<![CDATA[", lt):
            end = text.find("]]>", lt)
            if end < 0:
                raise LandXmlError("Файл оборван внутри CDATA")
            if stack:
                stack[-1].text += text[lt + 9:end]
            i = end + 3
            continue
        if text.startswith("<!", lt):
            head = text[lt:lt + 200].upper()
            if "ENTITY" in head or "DOCTYPE" in head:
                raise LandXmlError(
                    "В файле объявлены сущности XML. Такие файлы не "
                    "читаются: раскрытие сущностей это известный способ "
                    "раздуть разбор до отказа машины")
            i = text.find(">", lt)
            if i < 0:
                raise LandXmlError("Файл оборван внутри объявления XML")
            i += 1
            continue
        gt = text.find(">", lt)
        if gt < 0:
            raise LandXmlError("Файл оборван внутри тега XML")
        body = text[lt + 1:gt]
        if body.startswith("/"):
            name = body[1:].strip().split(":")[-1]
            if not stack:
                raise LandXmlError("Лишний закрывающий тег XML: %s" % name)
            el = stack.pop()
            if el.tag != name:
                raise LandXmlError(
                    "Тег XML закрыт не тем именем: открыт %s, закрыт %s"
                    % (el.tag, name))
            i = gt + 1
            continue
        selfclose = body.endswith("/")
        if selfclose:
            body = body[:-1]
        sp = 0
        while sp < len(body) and body[sp] not in " \t\r\n":
            sp += 1
        name = body[:sp].strip().split(":")[-1]
        if not name:
            raise LandXmlError("Пустое имя тега XML")
        el = _El(name, _attrs(body[sp:]))
        if stack:
            stack[-1].children.append(el)
        elif root is None:
            root = el
        else:
            raise LandXmlError("В файле больше одного корневого элемента")
        if not selfclose:
            stack.append(el)
            if len(stack) > _MAX_DEPTH:
                raise LandXmlError("Слишком глубокая вложенность XML")
        i = gt + 1
    if stack:
        raise LandXmlError(
            "Файл XML оборван: тег %s не закрыт" % stack[-1].tag)
    if root is None:
        raise LandXmlError("Файл не разобран как XML: корневого элемента нет")
    return root



class Document(object):
    """Содержимое файла в простых типах, без QGIS.

    Поля заполняются чтением и читаются записью, поэтому запись это в
    точности обратная операция и проверяется прогоном туда и обратно.
    """

    def __init__(self):
        self.units = {"linear": None, "to_metre": 1.0}
        self.crs = {"name": None, "epsg": None}
        self.points = []        # {name, code, desc, x, y, z}
        self.lines = []         # {name, desc, coords[(x, y, z)]}
        self.surfaces = []      # {name, desc, points, faces, skipped_faces}
        self.alignments = []    # {name, desc, sta_start, length, coords,
                                #  profile[(sta, z)], cross_sects[...]}
        self.unsupported = {}   # имя раздела -> сколько раз встретился
        self.warnings = []
        self.spirals = 0        # сколько переходных кривых заменено хордой

    def is_empty(self):
        return not (self.points or self.lines or self.surfaces
                    or self.alignments)

    def counts(self):
        """Сводка для журнала: что и сколько прочитано."""
        xs = sum(len(a["cross_sects"]) for a in self.alignments)
        return {"points": len(self.points), "lines": len(self.lines),
                "surfaces": len(self.surfaces),
                "alignments": len(self.alignments), "cross_sects": xs,
                "faces": sum(len(s["faces"]) for s in self.surfaces)}


# --- разбор --------------------------------------------------------------

def _tag(el):
    """Имя элемента. Приставку пространства имён разборщик уже снял."""
    return el.tag


def _find(parent, name):
    for ch in parent:
        if _tag(ch) == name:
            return ch
    return None


def _iter(parent, name):
    for ch in parent:
        if _tag(ch) == name:
            yield ch


def _floats(text):
    if not text:
        return []
    out = []
    for piece in text.replace(",", " ").split():
        try:
            out.append(float(piece))
        except ValueError:
            return []
    return out


class _Reader(object):
    def __init__(self, north_first=True, sagitta=DEFAULT_SAG):
        self.north_first = bool(north_first)
        self.sag = float(sagitta) if sagitta and sagitta > 0 else DEFAULT_SAG
        self.doc = Document()
        self.k = 1.0

    # координаты ---------------------------------------------------------

    def _xyz(self, text):
        """Тройка или пара из текста элемента, уже в метрах и в порядке XY."""
        v = _floats(text)
        if len(v) < 2:
            return None
        a, b = v[0] * self.k, v[1] * self.k
        x, y = (b, a) if self.north_first else (a, b)
        z = v[2] * self.k if len(v) > 2 else None
        return (x, y, z)

    def _pt(self, parent, name):
        el = _find(parent, name)
        return self._xyz(el.text) if el is not None else None

    # заголовок ----------------------------------------------------------

    def _units(self, root):
        u = _find(root, "Units")
        name = None
        if u is not None:
            for ch in u:
                name = ch.get("linearUnit") or name
        if not name:
            self.doc.warnings.append(
                "В файле не указаны единицы измерения, числа взяты как есть. "
                "Если файл в футах, отметки и длины окажутся втрое меньше")
            return
        self.doc.units["linear"] = name
        k = _TO_METRE.get(name.strip().lower().replace(" ", ""))
        if k is None:
            self.doc.warnings.append(
                "Единица измерения «%s» неизвестна, пересчёт не выполнен"
                % name)
            return
        self.doc.units["to_metre"] = k
        self.k = k

    def _crs(self, root):
        c = _find(root, "CoordinateSystem")
        if c is None:
            self.doc.warnings.append(
                "В файле не указана система координат, слои получат систему "
                "проекта. Проверьте её до работы с результатом")
            return
        self.doc.crs["name"] = c.get("name") or c.get("desc")
        code = c.get("epsgCode")
        if code:
            try:
                self.doc.crs["epsg"] = int(str(code).strip())
            except ValueError:
                pass

    # общие элементы -----------------------------------------------------

    def _points(self, root):
        for group in _iter(root, "CgPoints"):
            for el in _iter(group, "CgPoint"):
                p = self._xyz(el.text)
                if p is None:
                    continue
                self.doc.points.append({
                    "name": el.get("name") or el.get("oID") or "",
                    "code": el.get("code") or "",
                    "desc": el.get("desc") or "",
                    "x": p[0], "y": p[1], "z": p[2]})

    def _plan_features(self, root):
        for group in _iter(root, "PlanFeatures"):
            gname = group.get("name") or ""
            for el in _iter(group, "PlanFeature"):
                geom = _find(el, "CoordGeom")
                coords = self._coordgeom(geom) if geom is not None else []
                if len(coords) < 2:
                    continue
                self.doc.lines.append({
                    "name": el.get("name") or "",
                    "desc": el.get("desc") or gname,
                    "coords": coords})

    def _surfaces(self, root):
        for group in _iter(root, "Surfaces"):
            for el in _iter(group, "Surface"):
                d = _find(el, "Definition")
                if d is None:
                    continue
                ids, pts = {}, []
                pnts = _find(d, "Pnts")
                if pnts is not None:
                    for p in _iter(pnts, "P"):
                        xyz = self._xyz(p.text)
                        if xyz is None:
                            continue
                        ids[str(p.get("id") or len(pts) + 1)] = len(pts)
                        pts.append((xyz[0], xyz[1],
                                    0.0 if xyz[2] is None else xyz[2]))
                faces, skipped, lost = [], 0, 0
                fs = _find(d, "Faces")
                if fs is not None:
                    for f in _iter(fs, "F"):
                        if str(f.get("i") or "0").strip() in ("1", "true"):
                            skipped += 1
                            continue
                        ref = (f.text or "").split()
                        idx = [ids.get(r) for r in ref[:3]]
                        if len(idx) < 3 or any(i is None for i in idx):
                            lost += 1
                            continue
                        faces.append(tuple(idx))
                if lost:
                    self.doc.warnings.append(
                        "Поверхность «%s»: отброшено граней со ссылкой на "
                        "неизвестную точку: %d" % (el.get("name") or "", lost))
                self.doc.surfaces.append({
                    "name": el.get("name") or "", "desc": el.get("desc") or "",
                    "points": pts, "faces": faces, "skipped_faces": skipped})

    # трассы -------------------------------------------------------------

    def _arc(self, start, centre, end, rot):
        """Дуга ломаной по стрелке прогиба. Концы ставятся точно."""
        cx, cy = centre[0], centre[1]
        r0 = math.hypot(start[0] - cx, start[1] - cy)
        r1 = math.hypot(end[0] - cx, end[1] - cy)
        r = (r0 + r1) / 2.0
        if r <= 0:
            return [start, end]
        a0 = math.atan2(start[1] - cy, start[0] - cx)
        a1 = math.atan2(end[1] - cy, end[0] - cx)
        ccw = str(rot or "").strip().lower() in ("ccw", "counterclockwise")
        sweep = (a1 - a0) % (2 * math.pi) if ccw else -((a0 - a1)
                                                        % (2 * math.pi))
        if abs(sweep) < 1e-12:
            sweep = 2 * math.pi if ccw else -2 * math.pi
        # шаг по стрелке прогиба: sag = r * (1 - cos(step / 2))
        ratio = max(min(1.0 - self.sag / r, 1.0), -1.0)
        step = 2.0 * math.acos(ratio)
        n = max(int(math.ceil(abs(sweep) / step)), 1)
        out = []
        for i in range(n + 1):
            a = a0 + sweep * i / float(n)
            out.append((cx + r * math.cos(a), cy + r * math.sin(a), None))
        out[0], out[-1] = start, end
        return out

    def _coordgeom(self, geom):
        coords = []

        def add(seq):
            for p in seq:
                if p is None:
                    continue
                if coords and _same(coords[-1], p):
                    continue
                coords.append(p)

        for el in geom:
            name = _tag(el)
            start = self._pt(el, "Start")
            end = self._pt(el, "End")
            if name == "Line":
                add([start, end])
            elif name == "Curve":
                centre = self._pt(el, "Center")
                if start is None or end is None:
                    continue
                if centre is None:
                    self.doc.warnings.append(
                        "Кривая без центра заменена хордой: по координатам "
                        "начала и конца дуга не восстанавливается")
                    add([start, end])
                else:
                    add(self._arc(start, centre, end, el.get("rot")))
            elif name == "Spiral":
                self.doc.spirals += 1
                add([start, end])
            elif name == "IrregularLine":
                pts = [start]
                for lst in ("PntList3D", "PntList2D"):
                    node = _find(el, lst)
                    if node is None:
                        continue
                    v = _floats(node.text)
                    step = 3 if lst == "PntList3D" else 2
                    for i in range(0, len(v) - step + 1, step):
                        pts.append(self._xyz(" ".join(
                            repr(x) for x in v[i:i + step])))
                pts.append(end)
                add(pts)
        if self.doc.spirals:
            msg = ("Переходных кривых заменено хордой: %d. Длина трассы на "
                   "них занижена" % self.doc.spirals)
            if msg not in self.doc.warnings:
                self.doc.warnings = [w for w in self.doc.warnings
                                     if not w.startswith("Переходных")]
                self.doc.warnings.append(msg)
        return coords

    def _profile(self, al):
        prof = _find(al, "Profile")
        if prof is None:
            return []
        out = []
        pa = _find(prof, "ProfAlign")
        if pa is not None:
            for pvi in _iter(pa, "PVI"):
                v = _floats(pvi.text)
                if len(v) >= 2:
                    out.append((v[0] * self.k, v[1] * self.k))
        if out:
            return out
        ps = _find(prof, "ProfSurf")
        if ps is not None:
            node = _find(ps, "PntList2D")
            v = _floats(node.text) if node is not None else []
            for i in range(0, len(v) - 1, 2):
                out.append((v[i] * self.k, v[i + 1] * self.k))
        return out

    def _cross_sects(self, al):
        node = _find(al, "CrossSects")
        if node is None:
            return []
        out = []
        for cs in _iter(node, "CrossSect"):
            try:
                sta = float(cs.get("sta"))
            except (TypeError, ValueError):
                continue
            surfs = []
            for srf in _iter(cs, "CrossSectSurf"):
                pts = []
                for p in _iter(srf, "CrossSectPnt"):
                    # Пара это смещение от оси и отметка. Правило «сначала
                    # север» сюда не относится: применить его значит
                    # поменять смещение с отметкой и положить чертёж набок.
                    v = _floats(p.text)
                    if len(v) >= 2:
                        pts.append((v[0] * self.k, v[1] * self.k))
                if pts:
                    surfs.append({"name": srf.get("name") or "",
                                  "desc": srf.get("desc") or "",
                                  "points": pts})
            if surfs:
                out.append({"sta": sta * self.k, "name": cs.get("name") or "",
                            "desc": cs.get("desc") or "", "surfaces": surfs})
        return out

    def _alignments(self, root):
        for group in _iter(root, "Alignments"):
            for al in _iter(group, "Alignment"):
                geom = _find(al, "CoordGeom")
                coords = self._coordgeom(geom) if geom is not None else []
                self.doc.alignments.append({
                    "name": al.get("name") or "",
                    "desc": al.get("desc") or "",
                    "sta_start": _opt_float(al.get("staStart"), self.k),
                    "length": _opt_float(al.get("length"), self.k),
                    "coords": coords,
                    "profile": self._profile(al),
                    "cross_sects": self._cross_sects(al)})

    # непрочитанное ------------------------------------------------------

    def _unsupported(self, root):
        for el in root:
            name = _tag(el)
            if name in _KNOWN_TOP:
                continue
            self.doc.unsupported[name] = self.doc.unsupported.get(name, 0) + 1

    def read(self, text):
        root = _parse_xml(text)
        if _tag(root) != "LandXML":
            raise LandXmlError(
                "Корневой элемент «%s», а ожидался LandXML" % _tag(root))
        self._units(root)
        self._crs(root)
        self._points(root)
        self._plan_features(root)
        self._surfaces(root)
        self._alignments(root)
        self._unsupported(root)
        return self.doc


def _same(a, b, eps=1e-9):
    return abs(a[0] - b[0]) < eps and abs(a[1] - b[1]) < eps


def _opt_float(v, k=1.0):
    try:
        return float(v) * k
    except (TypeError, ValueError):
        return None


def loads(text, north_first=True, sagitta=DEFAULT_SAG):
    """Разбор текста файла. Возвращает Document."""
    return _Reader(north_first, sagitta).read(text)


def load(path, north_first=True, sagitta=DEFAULT_SAG):
    with open(path, "rb") as fh:
        raw = fh.read()
    for enc in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            return loads(raw.decode(enc), north_first, sagitta)
        except UnicodeDecodeError:
            continue
    raise LandXmlError("Кодировка файла не распознана")


# --- запись --------------------------------------------------------------

def _num(v):
    """Число для файла: без экспоненты и без хвоста нулей, но с точкой."""
    if v is None:
        return "0.0"
    s = "%.6f" % float(v)
    s = s.rstrip("0")
    if s.endswith("."):
        s += "0"
    return s


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _ne(x, y, z=None):
    """Текст точки по схеме: сначала север, потом восток."""
    out = "%s %s" % (_num(y), _num(x))
    if z is not None:
        out += " %s" % _num(z)
    return out


def dumps(doc, application="Isoliner"):
    """Файл LandXML 1.2 из Document. Всегда в метрах.

    Пишется то же подмножество, что читается, поэтому запись и чтение
    сверяются прогоном туда и обратно.
    """
    o = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<LandXML xmlns="http://www.landxml.org/schema/LandXML-1.2" '
         'version="1.2" language="English">',
         '<Units><Metric linearUnit="meter" areaUnit="squareMeter" '
         'volumeUnit="cubicMeter" temperatureUnit="celsius" '
         'pressureUnit="milliBars" angularUnit="decimal degrees" '
         'directionUnit="decimal degrees"/></Units>',
         '<Application name="%s"/>' % _esc(application)]
    crs = doc.crs or {}
    if crs.get("name") or crs.get("epsg"):
        bits = []
        if crs.get("name"):
            bits.append('name="%s"' % _esc(crs["name"]))
        if crs.get("epsg"):
            bits.append('epsgCode="%d"' % int(crs["epsg"]))
        o.append("<CoordinateSystem %s/>" % " ".join(bits))

    if doc.points:
        o.append("<CgPoints>")
        for p in doc.points:
            at = ['name="%s"' % _esc(p.get("name") or "")]
            if p.get("code"):
                at.append('code="%s"' % _esc(p["code"]))
            if p.get("desc"):
                at.append('desc="%s"' % _esc(p["desc"]))
            o.append("<CgPoint %s>%s</CgPoint>"
                     % (" ".join(at), _ne(p["x"], p["y"], p.get("z"))))
        o.append("</CgPoints>")

    if doc.lines:
        o.append('<PlanFeatures name="Isoliner">')
        for ln in doc.lines:
            c = ln["coords"]
            at = ['name="%s"' % _esc(ln.get("name") or "")]
            if ln.get("desc"):
                at.append('desc="%s"' % _esc(ln["desc"]))
            o.append("<PlanFeature %s><CoordGeom><IrregularLine>"
                     % " ".join(at))
            o.append("<Start>%s</Start>" % _ne(*c[0]))
            if len(c) > 2:
                mid = " ".join(_ne(*p) for p in c[1:-1])
                o.append("<PntList3D>%s</PntList3D>" % mid)
            o.append("<End>%s</End>" % _ne(*c[-1]))
            o.append("</IrregularLine></CoordGeom></PlanFeature>")
        o.append("</PlanFeatures>")

    if doc.surfaces:
        o.append("<Surfaces>")
        for s in doc.surfaces:
            o.append('<Surface name="%s"><Definition surfType="TIN">'
                     % _esc(s.get("name") or "surface"))
            o.append("<Pnts>")
            for i, p in enumerate(s["points"], start=1):
                o.append('<P id="%d">%s</P>' % (i, _ne(p[0], p[1], p[2])))
            o.append("</Pnts><Faces>")
            for f in s["faces"]:
                o.append("<F>%d %d %d</F>" % (f[0] + 1, f[1] + 1, f[2] + 1))
            o.append("</Faces></Definition></Surface>")
        o.append("</Surfaces>")

    if doc.alignments:
        o.append("<Alignments>")
        for a in doc.alignments:
            at = ['name="%s"' % _esc(a.get("name") or "")]
            if a.get("length") is not None:
                at.append('length="%s"' % _num(a["length"]))
            at.append('staStart="%s"' % _num(a.get("sta_start") or 0.0))
            o.append("<Alignment %s>" % " ".join(at))
            c = a.get("coords") or []
            if len(c) >= 2:
                o.append("<CoordGeom><IrregularLine>")
                o.append("<Start>%s</Start>" % _ne(c[0][0], c[0][1]))
                if len(c) > 2:
                    o.append("<PntList2D>%s</PntList2D>"
                             % " ".join(_ne(p[0], p[1]) for p in c[1:-1]))
                o.append("<End>%s</End>" % _ne(c[-1][0], c[-1][1]))
                o.append("</IrregularLine></CoordGeom>")
            if a.get("profile"):
                o.append('<Profile name="%s"><ProfAlign name="%s">'
                         % (_esc(a.get("name") or "profile"),
                            _esc(a.get("name") or "profile")))
                for sta, z in a["profile"]:
                    o.append("<PVI>%s %s</PVI>" % (_num(sta), _num(z)))
                o.append("</ProfAlign></Profile>")
            if a.get("cross_sects"):
                o.append("<CrossSects>")
                for cs in a["cross_sects"]:
                    at = ['sta="%s"' % _num(cs["sta"])]
                    if cs.get("name"):
                        at.append('name="%s"' % _esc(cs["name"]))
                    o.append("<CrossSect %s>" % " ".join(at))
                    for srf in cs["surfaces"]:
                        o.append('<CrossSectSurf name="%s">'
                                 % _esc(srf.get("name") or "surface"))
                        for off, z in srf["points"]:
                            o.append("<CrossSectPnt>%s %s</CrossSectPnt>"
                                     % (_num(off), _num(z)))
                        o.append("</CrossSectSurf>")
                    o.append("</CrossSect>")
                o.append("</CrossSects>")
            o.append("</Alignment>")
        o.append("</Alignments>")

    o.append("</LandXML>")
    return "\n".join(o)


# --- пикетаж: положение по трассе и смещение от неё ----------------------
#
# Поперечник в файле задан пикетом и смещениями от оси, а на карте он лежит
# поперёк трассы. Перевод между этими двумя видами нужен в обе стороны, и
# он чистая геометрия, поэтому живёт здесь и проверяется без QGIS.
#
# Знак смещения принят как в дорожной практике: положительное смещение
# справа по ходу трассы, отрицательное слева.

def cum_lengths(coords):
    """Нарастающие длины по вершинам ломаной."""
    out = [0.0]
    for (x0, y0), (x1, y1) in zip([(p[0], p[1]) for p in coords],
                                  [(p[0], p[1]) for p in coords[1:]]):
        out.append(out[-1] + math.hypot(x1 - x0, y1 - y0))
    return out


def point_at_station(coords, sta, sta_start=0.0):
    """Точка трассы на пикете и единичный вектор хода в ней.

    За концами трассы положение продолжается по крайнему направлению: у
    съёмки пикет поперечника нередко выходит за последнюю вершину оси на
    метры, и отбрасывать такой поперечник было бы хуже, чем продолжить.
    Возвращает (x, y, tx, ty) либо None, если трасса вырождена.
    """
    if len(coords) < 2:
        return None
    acc = cum_lengths(coords)
    if acc[-1] <= 0:
        return None
    s = float(sta) - float(sta_start or 0.0)
    for i in range(len(acc) - 1):
        if acc[i] <= s <= acc[i + 1] or (i == 0 and s < acc[0]) or \
                (i == len(acc) - 2 and s > acc[-1]):
            seg = acc[i + 1] - acc[i]
            if seg <= 0:
                continue
            t = (s - acc[i]) / seg
            x0, y0 = coords[i][0], coords[i][1]
            x1, y1 = coords[i + 1][0], coords[i + 1][1]
            tx, ty = (x1 - x0) / seg, (y1 - y0) / seg
            return (x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, tx, ty)
    return None


def offset_point(coords, sta, offset, sta_start=0.0):
    """Точка на смещении от оси: положительное смещение справа по ходу."""
    at = point_at_station(coords, sta, sta_start)
    if at is None:
        return None
    x, y, tx, ty = at
    # нормаль справа от направления хода
    return (x + ty * float(offset), y - tx * float(offset))


def station_offset(coords, x, y, sta_start=0.0):
    """Пикет и смещение точки относительно трассы, обратная задача."""
    if len(coords) < 2:
        return None
    acc = cum_lengths(coords)
    best = None
    for i in range(len(coords) - 1):
        x0, y0 = coords[i][0], coords[i][1]
        x1, y1 = coords[i + 1][0], coords[i + 1][1]
        dx, dy = x1 - x0, y1 - y0
        seg2 = dx * dx + dy * dy
        if seg2 <= 0:
            continue
        t = ((x - x0) * dx + (y - y0) * dy) / seg2
        t = max(0.0, min(1.0, t))
        px, py = x0 + dx * t, y0 + dy * t
        d = math.hypot(x - px, y - py)
        if best is None or d < best[0]:
            seg = math.sqrt(seg2)
            side = (x - px) * (dy / seg) - (y - py) * (dx / seg)
            best = (d, acc[i] + seg * t + float(sta_start or 0.0),
                    d if side >= 0 else -d)
    return None if best is None else (best[1], best[2])


def dump(doc, path, application="Isoliner"):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(doc, application))
    return path
