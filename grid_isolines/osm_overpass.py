# -*- coding: utf-8 -*-
"""Загрузка топоосновы из OpenStreetMap через Overpass API.

Слои: водотоки (готовые тальвеги, в OSM рисуются вниз по течению),
водоёмы (плоскости постоянной высоты), вершины с отметками,
обрывы и насыпи (линии разрыва), береговая линия (нулевая горизонталь).

Данные © участники OpenStreetMap, лицензия ODbL. Атрибуция обязательна
и проставляется в метаданные выходных слоёв.

Ограничение первой версии: у водоёмов берём только замкнутые way,
мультиполигоны-relation (крупные озёра из нескольких контуров)
пропускаем, это отмечено в руководстве.
"""

import json
import urllib.error
import urllib.parse
import urllib.request

ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "Isoliner-QGIS-plugin (topo loader; https://www.informpp.ru/)"
ATTRIBUTION = "Данные: © участники OpenStreetMap, ODbL"
DEFAULT_TIMEOUT = 90
MAX_BBOX_DEG2 = 0.5

LAYER_WATERCOURSES = "watercourses"
LAYER_WATERBODIES = "waterbodies"
LAYER_PEAKS = "peaks"
LAYER_BREAKS = "breaks"
LAYER_COASTLINE = "coastline"
ALL_LAYERS = (LAYER_WATERCOURSES, LAYER_WATERBODIES, LAYER_PEAKS,
              LAYER_BREAKS, LAYER_COASTLINE)


class OsmSourceError(Exception):
    """Ошибка источника OSM с внятным текстом для пользователя."""


def check_bbox(lon_min, lat_min, lon_max, lat_max, max_deg2=MAX_BBOX_DEG2):
    if lon_max <= lon_min or lat_max <= lat_min:
        raise OsmSourceError("Пустая рамка: проверьте координаты.")
    area = (lon_max - lon_min) * (lat_max - lat_min)
    if area > max_deg2:
        raise OsmSourceError(
            "Рамка {:.2f} кв. градуса при пределе {:.2f}. "
            "Публичные серверы Overpass не любят большие запросы. "
            "Уменьшите рамку или поднимите предел в параметрах.".format(
                area, max_deg2
            )
        )


def build_query(lon_min, lat_min, lon_max, lat_max, layers,
                timeout=DEFAULT_TIMEOUT):
    """Собирает запрос Overpass QL по рамке и набору слоёв."""
    bbox = "({:.7f},{:.7f},{:.7f},{:.7f})".format(
        lat_min, lon_min, lat_max, lon_max)
    parts = []
    if LAYER_WATERCOURSES in layers:
        parts.append('way["waterway"~"^(river|stream|canal)$"]' + bbox + ";")
    if LAYER_WATERBODIES in layers:
        parts.append('way["natural"="water"]' + bbox + ";")
    if LAYER_PEAKS in layers:
        parts.append('node["natural"="peak"]' + bbox + ";")
    if LAYER_BREAKS in layers:
        parts.append('way["natural"="cliff"]' + bbox + ";")
        parts.append('way["man_made"="embankment"]' + bbox + ";")
    if LAYER_COASTLINE in layers:
        parts.append('way["natural"="coastline"]' + bbox + ";")
    if not parts:
        raise OsmSourceError("Не выбран ни один слой топоосновы.")
    return ("[out:json][timeout:{}];(\n".format(int(timeout))
            + "\n".join(parts)
            + "\n);\nout geom qt;")


def run_query(query, timeout=DEFAULT_TIMEOUT, endpoints=ENDPOINTS,
              feedback=None):
    """Выполняет запрос, при отказе основного сервера пробует зеркало."""
    last_err = None
    for url in endpoints:
        if feedback:
            feedback.pushInfo("Запрос к {}".format(url))
        req = urllib.request.Request(
            url,
            data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
            headers={"User-Agent": USER_AGENT},
        )
        try:
            # Адреса фиксированы константой ENDPOINTS, только https.
            with urllib.request.urlopen(req, timeout=timeout + 10) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, ValueError) as exc:
            last_err = exc
            if feedback:
                feedback.pushInfo("Сервер не ответил: {}".format(exc))
    raise OsmSourceError(
        "Серверы Overpass недоступны или отклонили запрос ({}). "
        "Проверьте сеть, уменьшите рамку или повторите позже: "
        "публичные серверы имеют лимиты нагрузки.".format(last_err)
    )


def _parse_ele(tags):
    raw = tags.get("ele")
    if raw is None:
        return None
    try:
        return float(str(raw).replace(",", ".").split()[0])
    except (ValueError, IndexError):
        return None


def _way_coords(element):
    geom = element.get("geometry") or []
    return [(p["lon"], p["lat"]) for p in geom]


def _is_closed(coords):
    return len(coords) >= 4 and coords[0] == coords[-1]


def parse_elements(data):
    """Раскладывает ответ Overpass по слоям.

    Возвращает словарь слой -> список объектов
    {"geom": "point"|"line"|"polygon", "coords": [...], "attrs": {...}}.
    """
    result = {name: [] for name in ALL_LAYERS}
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        etype = el.get("type")
        if etype == "node" and tags.get("natural") == "peak":
            result[LAYER_PEAKS].append({
                "geom": "point",
                "coords": [(el["lon"], el["lat"])],
                "attrs": {
                    "name": tags.get("name"),
                    "ele": _parse_ele(tags),
                    "osm_id": el.get("id"),
                },
            })
            continue
        if etype != "way":
            continue
        coords = _way_coords(el)
        if len(coords) < 2:
            continue
        attrs = {"name": tags.get("name"), "osm_id": el.get("id")}
        if tags.get("waterway") in ("river", "stream", "canal"):
            attrs["waterway"] = tags["waterway"]
            result[LAYER_WATERCOURSES].append(
                {"geom": "line", "coords": coords, "attrs": attrs})
        elif tags.get("natural") == "water":
            if _is_closed(coords):
                attrs["water"] = tags.get("water")
                result[LAYER_WATERBODIES].append(
                    {"geom": "polygon", "coords": coords, "attrs": attrs})
        elif tags.get("natural") == "cliff":
            attrs["kind"] = "cliff"
            result[LAYER_BREAKS].append(
                {"geom": "line", "coords": coords, "attrs": attrs})
        elif tags.get("man_made") == "embankment":
            attrs["kind"] = "embankment"
            result[LAYER_BREAKS].append(
                {"geom": "line", "coords": coords, "attrs": attrs})
        elif tags.get("natural") == "coastline":
            result[LAYER_COASTLINE].append(
                {"geom": "line", "coords": coords, "attrs": attrs})
    return result


def clip_line_to_bbox(coords, lon_min, lat_min, lon_max, lat_max):
    """Грубый клип линии по рамке: режем на куски по вхождению вершин.

    Overpass отдаёт объекты целиком, длинную реку надо укорачивать.
    Сегмент оставляем, если хотя бы один его конец внутри рамки,
    точного пересечения с границей не считаем, для целей тальвегов
    и отрисовки этого достаточно.
    """
    def inside(p):
        return lon_min <= p[0] <= lon_max and lat_min <= p[1] <= lat_max

    pieces = []
    current = []
    for i, p in enumerate(coords):
        keep = inside(p) or (i > 0 and inside(coords[i - 1])) or (
            i + 1 < len(coords) and inside(coords[i + 1]))
        if keep:
            current.append(p)
        elif current:
            if len(current) >= 2:
                pieces.append(current)
            current = []
    if len(current) >= 2:
        pieces.append(current)
    return pieces
