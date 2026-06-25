# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), - либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии - в файле LICENSE (на английском, юридически значим).
"""
Группа команд «Грид и изолинии».

Алгоритмы:
  Kriging2DAlgorithm        - точки → растр (ординарный/простой кригинг, KB2D)
  RasterToIsolinesAlgorithm - растр → изолинии (линии) и опционально полигоны

Вариограмма - нуггет + одна структура (модель, порог, радиус, азимут, анизотропия).
Структура с вкладом (порогом) <= 0 не учитывается (кроме первой).
"""
import math

import os
import json
import configparser
import uuid

import numpy as np
from osgeo import gdal, osr

from qgis.PyQt.QtCore import QUrl, QVariant

from .i18n import tr as _tr  # двуязычие RU/EN (нужен до module-level констант)
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsProcessingLayerPostProcessorInterface,
    QgsProject,
    QgsSettings,
    QgsFeatureRequest,
    QgsExpression,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterField,
    QgsProcessingParameterEnum,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterNumber,
    QgsProcessingParameterString,
    QgsProcessingParameterExtent,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterVectorDestination,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterDefinition,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsWkbTypes,
)

from .kb2d import (
    Variogram, build_grid, clip_outliers, cross_validate, EPS, PolyTrend,
    cross_validate_detrend, ExternalDrift, exceedance_prob,
    experimental_variogram, fit_variogram, model_curve, variogram_map,
    MODEL_SPHERICAL, MODEL_EXPONENTIAL, MODEL_GAUSSIAN, GAUSS_MIN_NUGGET_FRAC)
from .isolines import (
    isolines_from_raster, isolines_and_polygons, compute_levels, DEFAULT_FIELD,
    _gaussian_nodata)
from . import hydro

GROUP = _tr("1. Грид и изолинии")
GROUP_ID = "grid_isolines"
GROUP2 = _tr("2. Дополнительные инструменты")
GROUP2_ID = "extra_tools"

MODEL_LABELS = [_tr("Сферическая"), _tr("Экспоненциальная"), _tr("Гауссова"), _tr("Степенная")]
KTYPE_LABELS = [_tr("Ординарный (OK)"), _tr("Простой (SK)")]

NSTRUCT = 1  # число структур вариограммы (S2/S3 убраны как неиспользуемые)

CREDIT = ("\n\n- - -\nРазработано при поддержке ООО «Информ++» "
          "(www.informpp.ru).\nСтраница плагина: "
          "www.informpp.ru/главная-страница/qgis-isoliner")


def _credit():
    """Подпись «Разработано при поддержке…» на активном языке."""
    return _tr(CREDIT)


def _advanced(param):
    try:
        flag = QgsProcessingParameterDefinition.FlagAdvanced     # QGIS 3.x
    except AttributeError:
        from qgis.core import Qgis
        flag = Qgis.ProcessingParameterFlag.Advanced             # QGIS 4
    param.setFlags(param.flags() | flag)
    return param


def _short(s, n=32):
    s = (s or "data").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def _set_output_name(context, path, name):
    """Задаёт имя слоя в дереве, подмешивая имя источника."""
    try:
        if path and context.willLoadLayerOnCompletion(path):
            context.layerToLoadOnCompletionDetails(path).name = name
    except Exception:
        pass


# держим пост-процессоры живыми (иначе их соберёт сборщик мусора Python)
_KEEP_ALIVE = []


class _OrderState:
    """Помещает слой полигонов прямо ПОД слоем линий, когда оба загружены.
    Двигается только слой полигонов - узел линий не трогаем никогда, поэтому
    слой изолиний не может пропасть. Идемпотентно."""
    def __init__(self):
        self.lines_id = None
        self.polys_id = None

    def reorder(self):
        try:
            if not (self.lines_id and self.polys_id):
                return
            root = QgsProject.instance().layerTreeRoot()
            ln = root.findLayer(self.lines_id)
            pn = root.findLayer(self.polys_id)
            if ln is None or pn is None:
                return
            parent = ln.parent()
            if parent is None:
                return
            kids = parent.children()
            if ln not in kids:
                return
            li = kids.index(ln)
            if pn in kids and kids.index(pn) == li + 1:
                return                       # уже под линиями
            clone = pn.clone()
            parent.insertChildNode(li + 1, clone)   # копия полигонов под линиями
            pp = pn.parent()                         # затем убираем оригинал
            if pp is not None:
                pp.removeChildNode(pn)
        except Exception:
            pass


class _RolePostProcessor(QgsProcessingLayerPostProcessorInterface):
    def __init__(self, state, role):
        super().__init__()
        self.state = state
        self.role = role

    def postProcessLayer(self, layer, context, feedback):
        # только запоминаем id и откладываем перестановку на после загрузки
        # всех слоёв (через очередь событий) - так дерево стабильно
        try:
            if self.role == "lines":
                self.state.lines_id = layer.id()
            else:
                self.state.polys_id = layer.id()
            from qgis.PyQt.QtCore import QTimer
            QTimer.singleShot(0, self.state.reorder)
        except Exception:
            pass


def _order_lines_above(context, lines_path, polys_path):
    """Гарантирует, что слой изолиний окажется над слоем полигонов."""
    try:
        st = _OrderState()
        pl = _RolePostProcessor(st, "lines")
        pp = _RolePostProcessor(st, "polys")
        _KEEP_ALIVE.extend([st, pl, pp])
        if lines_path and context.willLoadLayerOnCompletion(lines_path):
            context.layerToLoadOnCompletionDetails(lines_path).setPostProcessor(pl)
        if polys_path and context.willLoadLayerOnCompletion(polys_path):
            context.layerToLoadOnCompletionDetails(polys_path).setPostProcessor(pp)
    except Exception:
        pass


def _style_path(name):
    """Путь к встроенному пресету стиля в папке styles модуля (без .qml)."""
    return os.path.join(os.path.dirname(__file__), "styles", name + ".qml")


class _StylePostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Накладывает .qml-стиль на загруженный слой и, опционально, регистрирует
    его id для перестановки порядка (линии над полигонами). У слоя может быть
    только один пост-процессор, поэтому стиль и порядок объединены здесь."""
    def __init__(self, style_path=None, order_state=None, role=None):
        super().__init__()
        self.style_path = style_path
        self.order_state = order_state
        self.role = role

    def postProcessLayer(self, layer, context, feedback):
        try:
            if self.style_path and os.path.exists(self.style_path):
                layer.loadNamedStyle(self.style_path)
                layer.triggerRepaint()
        except Exception:
            pass
        try:
            if self.order_state is not None and self.role:
                if self.role == "lines":
                    self.order_state.lines_id = layer.id()
                else:
                    self.order_state.polys_id = layer.id()
                from qgis.PyQt.QtCore import QTimer
                QTimer.singleShot(0, self.order_state.reorder)
        except Exception:
            pass


def _attach_style(context, path, style_path=None, order_state=None, role=None):
    """Вешает на выходной слой пост-процессор стиля (и порядка, если задан)."""
    try:
        if path and context.willLoadLayerOnCompletion(path):
            pp = _StylePostProcessor(style_path, order_state, role)
            _KEEP_ALIVE.append(pp)
            context.layerToLoadOnCompletionDetails(path).setPostProcessor(pp)
    except Exception:
        pass


class _AliasPostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Ставит псевдонимы полей на загруженный слой - но только если слой
    постоянный. Временные (memory) слои псевдонимы не хранят и QGIS на каждую
    попытку пишет предупреждение «не совместимы с временными слоями»; для них
    оставляем исходные имена полей (они и так читаемые)."""
    def __init__(self, aliases):
        super().__init__()
        self.aliases = aliases

    def postProcessLayer(self, layer, context, feedback):
        try:
            prov = layer.dataProvider()
            if prov is not None and prov.name() == "memory":
                return
            flds = layer.fields()
            for name, alias in self.aliases.items():
                i = flds.indexOf(name)
                if i >= 0:
                    layer.setFieldAlias(i, alias)
        except Exception:
            pass


def _set_field_aliases(context, path, aliases):
    """Назначить псевдонимы полей выходному слою после загрузки."""
    try:
        if path and context.willLoadLayerOnCompletion(path):
            pp = _AliasPostProcessor(aliases)
            _KEEP_ALIVE.append(pp)
            context.layerToLoadOnCompletionDetails(path).setPostProcessor(pp)
    except Exception:
        pass


def _help_url():
    """file:// ссылка на руководство в комплекте (для кнопки «Справка»).

    На английской локали открывается Isoliner_en.pdf, если он есть; иначе -
    русское Isoliner.pdf. Так одна кнопка даёт справку на языке интерфейса."""
    from .i18n import language as _lang  # текущий язык интерфейса
    doc = os.path.join(os.path.dirname(__file__), "doc")
    candidates = []
    try:
        if _lang() == "en":
            candidates.append("Isoliner_en.pdf")
    except Exception:
        pass
    candidates.append("Isoliner.pdf")
    for fname in candidates:
        p = os.path.join(doc, fname)
        if os.path.exists(p):
            return QUrl.fromLocalFile(p).toString()
    return ""


# --- запоминание введённых значений (в сессии и между запусками) ----------
# не запоминаем источники данных и выходы; ZFIELD (поле Z) запоминаем - удобно
# при повторных запусках по тому же слою (если поля нет в новом слое, QGIS его
# просто не подставит)
_PERSIST_DENY = {"INPUT", "OUTPUT", "OUTPUT_POLYGONS", "EXTENT", "MASK"}


def _settings_key(alg):
    return "isoliner/last/" + alg.name()


def _load_defaults(alg):
    try:
        raw = QgsSettings().value(_settings_key(alg), "")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def _save_values(alg, parameters):
    try:
        d = {k: v for k, v in parameters.items()
             if k not in _PERSIST_DENY and isinstance(v, (int, float, str, bool))}
        QgsSettings().setValue(_settings_key(alg), json.dumps(d))
    except Exception:
        pass


def _dv(alg, key, fallback):
    """Значение по умолчанию: ранее сохранённое или запасное."""
    return getattr(alg, "_defaults", {}).get(key, fallback)


def _last_profile_key(alg):
    return "isoliner/last_profile/" + alg.name()


def _remember_profile(alg, name):
    """Запомнить последний применённый профиль по имени (между сессиями)."""
    try:
        QgsSettings().setValue(_last_profile_key(alg), name or "")
    except Exception:
        pass


def _recalled_profile_index(alg):
    """Индекс последнего применённого профиля в списке [нет] + имена. По имени,
    а не по сохранённому индексу: добавление или удаление профиля не должно
    сдвигать выбор на чужой."""
    if alg is None:
        return 0
    try:
        name = QgsSettings().value(_last_profile_key(alg), "")
    except Exception:
        name = ""
    names = _profile_names()
    return (names.index(name) + 1) if name and name in names else 0


# Коды кондиционности из kb2d.data_warnings в текст для Журнала.
def _warn_data(feedback, xs, ys, vs):
    """Тихие предупреждения о плохой кондиционности входных точек. Не
    останавливают расчёт, только подсказывают в Журнал."""
    from .kb2d import data_warnings
    for code, val in data_warnings(xs, ys, vs):
        if code == "few_points":
            feedback.pushWarning(_tr(
                "Мало точек (%d): оценка кригинга и вариограммы неустойчива.")
                % val)
        elif code == "duplicates":
            feedback.pushWarning(_tr(
                "Точек с совпадающими координатами: %d. Частая причина "
                "вырожденной матрицы и артефактов. Уберите дубли или усредните "
                "пробы в одной точке.") % val)
        elif code == "constant":
            feedback.pushWarning(_tr(
                "Все значения одинаковы: кригинг вырождается, вариограмма "
                "нулевая. Проверьте выбранное поле."))


# Профили обработки: именованные наборы «вариограмма (Структура 1) + наггет +
# отсев ураганных проб». Хранятся глобально в QgsSettings (кросс-проектно),
# отдельно от per-algorithm настроек. «Вариограмма» и «Кросс-валидация» их
# сохраняют, «2D Kriging» подставляет, отдельный инструмент «Профили» правит.
PROFILE_NONE = _tr("(не выбран)")


def _profiles_key():
    return "isoliner/profiles"


_VERSION_CACHE = None


def _plugin_version():
    """Версия модуля из metadata.txt рядом с этим файлом (кэшируется)."""
    global _VERSION_CACHE
    if _VERSION_CACHE is None:
        ver = ""
        try:
            cp = configparser.ConfigParser(interpolation=None)
            cp.read(os.path.join(os.path.dirname(__file__), "metadata.txt"),
                    encoding="utf-8")
            ver = cp.get("general", "version", fallback="").strip()
        except Exception:
            ver = ""
        _VERSION_CACHE = ver
    return _VERSION_CACHE


def _version_line():
    """Строка для Журнала."""
    v = _plugin_version()
    return ("Isoliner " + v) if v else "Isoliner"


def _version_footer():
    """Подвал HTML-отчёта."""
    v = _plugin_version()
    name = ("Isoliner v" + v) if v else "Isoliner"
    return "<hr><p style='color:#888;font-size:smaller'>" + name + "</p>"


def _help_version(text):
    """Дописать версию в конец справки инструмента."""
    v = _plugin_version()
    text = "" if text is None else str(text)
    return (text + "\n\nIsoliner v" + v) if v else text


def _load_profiles():
    """Все профили: dict {имя: {...}}."""
    try:
        raw = QgsSettings().value(_profiles_key(), "")
        d = json.loads(raw) if raw else {}
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_profiles(d):
    try:
        QgsSettings().setValue(_profiles_key(), json.dumps(d))
        return True
    except Exception:
        return False


def _profile_names():
    """Имена профилей по алфавиту."""
    return sorted(_load_profiles().keys())


def _make_profile(nugget, model_code, sill, rng, azimuth=0.0, anis=1.0,
                  val_pct=0.0, val_min=None, val_max=None, val_cap=False):
    """Единый формат хранения профиля."""
    return {
        "nugget": float(nugget), "model": int(model_code),
        "sill": float(sill), "range": float(rng),
        "azimuth": float(azimuth), "anis": float(anis),
        "val_pct": float(val_pct),
        "val_min": (None if val_min is None else float(val_min)),
        "val_max": (None if val_max is None else float(val_max)),
        "val_cap": bool(val_cap)}


def _save_profile(name, profile):
    """Сохранить/перезаписать профиль под именем."""
    name = (name or "").strip()
    if not name:
        return False
    d = _load_profiles()
    d[name] = profile
    return _save_profiles(d)


def _merge_anisotropy(prof, azimuth, anis, rng=None):
    """Вписать анизотропию в существующий профиль, сохранив модель, наггет,
    силл и отсев. Радиус главной оси (rng) обновляется только если задан и
    положителен: при упёртом в окно радиусе его лучше не перетирать."""
    p = dict(prof)
    p["azimuth"] = float(azimuth)
    p["anis"] = float(anis)
    if rng is not None and rng > 0:
        p["range"] = float(rng)
    return p


def _get_profile(name):
    return _load_profiles().get((name or "").strip())


def _delete_profile(name):
    d = _load_profiles()
    name = (name or "").strip()
    if name in d:
        del d[name]
        return _save_profiles(d)
    return False


def _clear_profiles():
    return _save_profiles({})


def _profile_summary(p):
    """Короткое текстовое описание профиля для Журнала."""
    try:
        s = (_tr("наггет C0=%.4g, %s, порог C=%.4g, радиус a=%.4g") % (
            p["nugget"], MODEL_LABELS[int(p["model"])], p["sill"], p["range"]))
        if float(p.get("anis", 1.0)) != 1.0 or float(p.get("azimuth", 0.0)) != 0.0:
            s += (_tr(", анизотропия %.3g по азимуту %.4g°") % (
                p.get("anis", 1.0), p.get("azimuth", 0.0)))
        if float(p.get("val_pct", 0.0)) != 0.0:
            s += (_tr(", отсев %.4g%%") % p["val_pct"])
        return s
    except Exception:
        return _tr("профиль")


# ключи параметров структуры вариограммы
def _sk(i, suffix):
    return "S%d_%s" % (i, suffix)


def _san(name):
    """Имя поля из произвольного текста: буквы (вкл. кириллицу), цифры и
    подчёркивание; до 40 символов."""
    import re
    s = re.sub(r"\W+", "_", (name or "").strip(), flags=re.UNICODE).strip("_")
    return s[:40]


# ---------------------------------------------------------------------------
#  Параметры вариограммы/поиска - общий набор для кригинга
# ---------------------------------------------------------------------------
def _apply_profile(alg, parameters, context, feedback):
    """Если в параметре PROFILE выбран профиль - подставить его поверх полей
    диалога (наггет, Структура 1, отсев) и обнулить лишние структуры.
    Возвращает (возможно копию) parameters."""
    idx = alg.parameterAsEnum(parameters, alg.PROFILE, context)
    if idx <= 0:
        return parameters
    opts = [PROFILE_NONE] + _profile_names()
    name = opts[idx] if idx < len(opts) else None
    prof = _get_profile(name) if name else None
    if not prof:
        feedback.pushWarning(
            _tr("Профиль не найден - использую значения из диалога. "
            "Список профилей обновляется при открытии окна инструмента."))
        return parameters
    parameters = dict(parameters)
    parameters[alg.NUGGET] = prof["nugget"]
    parameters[_sk(1, "MODEL")] = int(prof["model"])
    parameters[_sk(1, "SILL")] = prof["sill"]
    parameters[_sk(1, "RANGE")] = prof["range"]
    parameters[_sk(1, "AZIMUTH")] = prof.get("azimuth", 0.0)
    parameters[_sk(1, "ANIS")] = prof.get("anis", 1.0)
    parameters[alg.VAL_PCT] = prof.get("val_pct", 0.0)
    parameters[alg.VAL_MIN] = prof.get("val_min")
    parameters[alg.VAL_MAX] = prof.get("val_max")
    parameters[alg.VAL_CAP] = bool(prof.get("val_cap", False))
    for i in range(2, NSTRUCT + 1):
        parameters[_sk(i, "SILL")] = 0.0
    feedback.pushInfo(
        _tr("Подставлен профиль «%s»: %s.") % (name, _profile_summary(prof)))
    _remember_profile(alg, name)
    return parameters


def _profile_enum(key, label, pick=False, alg=None):
    """Выпадающий список профилей с подписью-обёрткой (значения профиля
    строкой ниже). На QGIS без старого API виджетов - обычный список. Если
    передан alg и это не pick-список, по умолчанию подставляется последний
    применённый профиль (запоминается между сессиями)."""
    default = _recalled_profile_index(alg) if (alg is not None and not pick) else 0
    p = QgsProcessingParameterEnum(
        key, _tr(label), options=[_tr(PROFILE_NONE)] + _profile_names(),
        defaultValue=default)
    try:
        from .widgets import (ProfileWrapper, ProfilePickWrapper,
                              WRAPPER_AVAILABLE)
        if WRAPPER_AVAILABLE:
            cls = ProfilePickWrapper if pick else ProfileWrapper
            p.setMetadata({"widget_wrapper": {"class": cls}})
    except Exception:
        pass
    return p


def _add_kriging_params(alg):
    alg.addParameter(QgsProcessingParameterEnum(
        alg.KTYPE, _tr("Тип кригинга"), options=[_tr(x) for x in KTYPE_LABELS],
        defaultValue=_dv(alg, alg.KTYPE, 0)))

    # поиск и сетка - основные параметры
    alg.addParameter(QgsProcessingParameterNumber(
        alg.RADIUS, _tr("Радиус поиска (0 = вся выборка)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.RADIUS, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MIN_POINTS, _tr("Мин. число точек"),
        QgsProcessingParameterNumber.Integer,
        defaultValue=_dv(alg, alg.MIN_POINTS, 1), minValue=1))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MAX_POINTS, _tr("Макс. число точек"),
        QgsProcessingParameterNumber.Integer,
        defaultValue=_dv(alg, alg.MAX_POINTS, 24), minValue=1, maxValue=120))

    cs = QgsProcessingParameterNumber(
        alg.CELL_SIZE, _tr("Размер ячейки (0 = авто, min(охват)/50)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.CELL_SIZE, 0.0), minValue=0.0)
    try:  # живой показ размера грида; на QGIS 4 (без старого API) - обычное поле
        from .widgets import CellSizeWrapper, WRAPPER_AVAILABLE
        if WRAPPER_AVAILABLE:
            cs.setMetadata({"widget_wrapper": {"class": CellSizeWrapper}})
    except Exception:
        pass
    alg.addParameter(cs)

    alg.addParameter(QgsProcessingParameterExtent(
        alg.EXTENT, _tr("Охват растра (по умолчанию - по слою)"),
        optional=True))

    # обрезка экстраполяции - опция
    alg.addParameter(QgsProcessingParameterBoolean(
        alg.CLIP_HULL, _tr("Обрезать по контуру скважин (выпуклая оболочка)"),
        defaultValue=_dv(alg, alg.CLIP_HULL, False)))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.HULL_BUFFER, _tr("Буфер оболочки, ед. карты"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.HULL_BUFFER, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterVectorLayer(
        alg.MASK, _tr("Маска обрезки (полигон из проекта) - приоритетнее оболочки"),
        types=[QgsProcessing.TypeVectorPolygon], optional=True))

    # вариограмма - дополнительные параметры
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.SKMEAN, _tr("Среднее для простого кригинга"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.SKMEAN, 0.0))))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.NUGGET, _tr("Наггет C0"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.NUGGET, 0.0), minValue=0.0)))

    # структуры вариограммы (все - дополнительные)
    for i in range(1, NSTRUCT + 1):
        tag = _tr("Структура %d") % i
        default_sill = 1.0 if i == 1 else 0.0
        off = _tr("порог/вклад C") if i == 1 else _tr("порог/вклад C (0 = выкл.)")
        alg.addParameter(_advanced(QgsProcessingParameterEnum(
            _sk(i, "MODEL"), "%s · %s" % (tag, _tr("модель")),
            options=[_tr(x) for x in MODEL_LABELS], defaultValue=_dv(alg, _sk(i, "MODEL"), 0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "SILL"), "%s · %s" % (tag, off),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "SILL"), default_sill), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "RANGE"), "%s · %s" % (tag, _tr("радиус корреляции a (0=авто)")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "RANGE"), 0.0), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "AZIMUTH"), "%s · %s" % (tag, _tr("азимут, °")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "AZIMUTH"), 0.0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "ANIS"), "%s · %s" % (tag, _tr("анизотропия (малая/главная)")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "ANIS"), 1.0), minValue=EPS)))

    # отсев/срезка ураганных проб (по значению Z) - в самом конце
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_PCT, _tr("Ураганные пробы: перцентиль обрезки, % (0 = выкл.)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.VAL_PCT, 0.0), minValue=0.0, maxValue=49.0)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MIN, _tr("Нижняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MAX, _tr("Верхняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterBoolean(
        alg.VAL_CAP, _tr("Срезать к границе (capping) вместо удаления"),
        defaultValue=_dv(alg, alg.VAL_CAP, False))))


def _read_points(source, zfield, feedback=None,
                 vmin=None, vmax=None, pct=0.0, cap=False,
                 id_field=None, return_ids=False, request=None):
    idx = source.fields().lookupField(zfield)
    id_idx = source.fields().lookupField(id_field) if id_field else -1
    xs, ys, vs = [], [], []
    ids = [] if return_ids else None
    skipped_geom = 0
    skipped_value = 0
    feats = source.getFeatures(request) if request is not None \
        else source.getFeatures()
    for f in feats:
        g = f.geometry()
        if g is None or g.isEmpty():
            skipped_geom += 1
            continue
        v = f[idx]
        if v is None:
            skipped_value += 1
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            skipped_value += 1
            continue
        p = g.asPoint()
        xs.append(p.x()); ys.append(p.y()); vs.append(v)
        if return_ids:
            ids.append(f[id_idx] if id_idx >= 0 else f.id())
    if feedback is not None and (skipped_value or skipped_geom):
        feedback.pushInfo(
            _tr("Пропущено точек: %d без значения «%s»%s. Прочитано: %d.") %
            (skipped_value, zfield,
             (_tr(" и %d без геометрии") % skipped_geom) if skipped_geom else "",
             len(xs)))
    if len(xs) < 2:
        raise QgsProcessingException(
            _tr("Недостаточно валидных точек с числовым значением."))

    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)
    if return_ids:
        ids = np.asarray(ids, dtype=object)

    # отсев/срезка ураганных проб (до усреднения совпадающих точек)
    out, keep, lo, hi = clip_outliers(vs, vmin, vmax, pct, cap)
    if not (lo == float("-inf") and hi == float("inf")):
        if cap:
            nch = int(np.count_nonzero(out != vs))
            xs, ys, vs = xs, ys, out
            if feedback is not None:
                feedback.pushInfo(
                    _tr("Ураганные пробы: срезано %d значений к [%.4g; %.4g].") %
                    (nch, lo, hi))
        else:
            ncut = int(np.count_nonzero(~keep))
            xs, ys, vs = xs[keep], ys[keep], vs[keep]
            if return_ids:
                ids = ids[keep]
            if feedback is not None:
                feedback.pushInfo(
                    _tr("Ураганные пробы: удалено %d точек вне [%.4g; %.4g]; "
                    "осталось %d.") % (ncut, lo, hi, len(xs)))
        if len(xs) < 2:
            raise QgsProcessingException(
                _tr("После отсева ураганных проб осталось < 2 точек."))

    # схлопывание совпадающих точек (один XY = несколько проб) -> среднее Z.
    # без этого матрица кригинга вырождается: дыры NoData и «разлёт» ±1e15.
    # допуск задаём относительно охвата (а не фиксированными мм/градусами) -
    # так сливаются только практически совпадающие точки в любой системе
    # координат (метры/градусы/футы), без риска «склеить» разные скважины
    span = max(float(xs.max() - xs.min()), float(ys.max() - ys.min()), 1e-9)
    tol = span * 1e-7
    key = np.round(np.column_stack([xs, ys]) / tol) * tol
    uniq, first, inv = np.unique(key, axis=0, return_index=True,
                                 return_inverse=True)
    if len(uniq) < len(xs):
        sums = np.zeros(len(uniq)); cnts = np.zeros(len(uniq))
        np.add.at(sums, inv, vs)
        np.add.at(cnts, inv, 1.0)
        vs = sums / cnts
        xs = uniq[:, 0]
        ys = uniq[:, 1]
        if return_ids:
            ids = ids[first]      # за совпавшими точками - id первой
        if feedback is not None:
            feedback.pushInfo(
                _tr("Совпадающих точек усреднено: %d (осталось %d)") %
                (len(inv) - len(uniq), len(uniq)))
    if len(xs) < 2:
        raise QgsProcessingException(
            _tr("После усреднения совпадающих точек осталось < 2 узлов."))
    if return_ids:
        return xs, ys, vs, ids
    return xs, ys, vs


def _build_variogram(alg, parameters, context, nugget, auto_range, feedback=None):
    """Собирает вариограмму из нуггета и активных структур."""
    POWER = 4
    structures = []
    sill_total = 0.0
    for i in range(1, NSTRUCT + 1):
        sill = alg.parameterAsDouble(parameters, _sk(i, "SILL"), context)
        if i > 1 and sill <= 0:
            continue
        model = alg.parameterAsEnum(parameters, _sk(i, "MODEL"), context) + 1
        rng = alg.parameterAsDouble(parameters, _sk(i, "RANGE"), context)
        if model == POWER:
            # для степенной модели «радиус a» - это показатель степени ω
            # (0<ω<2), а НЕ радиус: авто = max(охват)/3 здесь даст переполнение
            if rng <= 0:
                rng = 1.0
                if feedback:
                    feedback.pushWarning(
                        _tr("Структура %d: степенная модель - поле «радиус a» это "
                        "показатель ω (0<ω<2), а не радиус; задан 0, взят ω=1.") % i)
            elif not (0.0 < rng < 2.0):
                if feedback:
                    feedback.pushWarning(
                        _tr("Структура %d: показатель степенной модели ω=%.3g вне "
                        "(0; 2) - приведён к диапазону.") % (i, rng))
                rng = min(max(rng, 0.05), 1.999)
        elif rng <= 0:
            rng = auto_range
        az = alg.parameterAsDouble(parameters, _sk(i, "AZIMUTH"), context)
        anis = alg.parameterAsDouble(parameters, _sk(i, "ANIS"), context)
        sill_total += max(sill, 0.0)
        structures.append({"it": model, "cc": max(sill, 0.0),
                           "aa": rng, "ang": az, "anis": anis})
    if not structures:  # первая структура с нулевым порогом - оставим как чистый эффект
        structures.append({"it": 1, "cc": 0.0, "aa": auto_range,
                           "ang": 0.0, "anis": 1.0})
    if feedback and nugget > 0 and sill_total <= 0:
        feedback.pushWarning(
            _tr("Задан только наггет (структурный вклад C = 0): кригинг выродится "
            "в локальное среднее, поверхность будет почти плоской."))
    vg = Variogram(nugget, structures)
    if feedback and vg.nugget_raised_from is not None:
        feedback.pushInfo(
            _tr("Гауссова модель: наггет повышен с %.4g до %.4g для устойчивости "
            "(минимум %g%% структурного силла).") % (
                vg.nugget_raised_from, vg.c0, GAUSS_MIN_NUGGET_FRAC * 100))
    return vg


def _sample_raster_bilinear(src, xd, yd, band=1):
    """Билинейная выборка значений растра в точках (xd, yd).

    Используется кригингом с внешним дрейфом: снимает значение s растра дрейфа
    в каждой скважине. Возвращает массив длины len(xd) с np.nan там, где точка
    вне растра либо все четыре соседних пикселя - nodata. Веса соседей с nodata
    обнуляются и нормируются, поэтому у края покрытия выборка остаётся честной.
    Координаты точек считаются в той же системе, что и растр (CRS совмещены
    вызывающей стороной). Растр предполагается осеориентированным (gt[2]=gt[4]=0).
    """
    ds = gdal.Open(src)
    if ds is None:
        return None
    b = ds.GetRasterBand(int(band))
    arr = b.ReadAsArray().astype(float)
    gt = ds.GetGeoTransform()
    nd = b.GetNoDataValue()
    ds = None
    ny, nx = arr.shape
    valid = np.isfinite(arr)
    if nd is not None:
        valid &= (arr != nd)
    px = float(gt[1]) or 1.0
    py = float(gt[5]) or 1.0
    fx = (np.asarray(xd, float) - gt[0]) / px - 0.5
    fy = (np.asarray(yd, float) - gt[3]) / py - 0.5
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    tx = fx - x0
    ty = fy - y0
    out = np.full(len(fx), np.nan, float)
    for k in range(len(fx)):
        i0, j0 = int(y0[k]), int(x0[k])
        acc = 0.0
        wsum = 0.0
        for dj, wx in ((0, 1.0 - tx[k]), (1, tx[k])):
            for di, wy in ((0, 1.0 - ty[k]), (1, ty[k])):
                ii, jj = i0 + di, j0 + dj
                if 0 <= ii < ny and 0 <= jj < nx and valid[ii, jj]:
                    w = wx * wy
                    acc += w * arr[ii, jj]
                    wsum += w
        if wsum > 0.0:
            out[k] = acc / wsum
    return out


def _resample_drift_to_grid(src, xmin, ymin, cell, nx, ny, band=1):
    """Передискретизация растра дрейфа на сетку кригинга (билинейно).

    Сетка совпадает с build_grid: геотрансформация (xmin, cell, 0,
    ymin+ny*cell, 0, -cell), центры ячеек = узлы оценки, строка 0 - север.
    Возвращает (ny, nx) float с np.nan в ячейках без покрытия. CRS не меняется:
    растр дрейфа и точки должны быть в одной системе координат.
    """
    out_bounds = (float(xmin), float(ymin),
                  float(xmin) + nx * cell, float(ymin) + ny * cell)
    ds = gdal.Warp("", src, format="MEM", outputBounds=out_bounds,
                   width=int(nx), height=int(ny),
                   resampleAlg=gdal.GRA_Bilinear, dstNodata=float("nan"))
    if ds is None:
        return None
    arr = ds.GetRasterBand(int(band)).ReadAsArray().astype(float)
    ds = None
    return arr


def _run_kriging_to_tiff(alg, parameters, context, feedback, source, zfield,
                         out_path, mask_layer=None, stderr_path=None):
    """Считывает параметры кригинга, строит грид, пишет GeoTIFF.
    -> (path, nodata, stderr_path|None).

    mask_layer (опц.) - полигональный слой/путь для обрезки экстраполяции.
    stderr_path (опц.) - если задан, дополнительно пишется растр стандартной
    ошибки кригинга (sqrt дисперсии) с той же обрезкой.
    """
    ktype = 1 if alg.parameterAsEnum(parameters, alg.KTYPE, context) == 0 else 0
    skmean = alg.parameterAsDouble(parameters, alg.SKMEAN, context)
    nugget = alg.parameterAsDouble(parameters, alg.NUGGET, context)
    radius = alg.parameterAsDouble(parameters, alg.RADIUS, context)
    ndmin = alg.parameterAsInt(parameters, alg.MIN_POINTS, context)
    ndmax = alg.parameterAsInt(parameters, alg.MAX_POINTS, context)
    cell = alg.parameterAsDouble(parameters, alg.CELL_SIZE, context)

    def _opt(name):
        v = parameters.get(name, None)
        if v is None or v == "":
            return None
        return alg.parameterAsDouble(parameters, name, context)
    pct = alg.parameterAsDouble(parameters, alg.VAL_PCT, context)
    vmin = _opt(alg.VAL_MIN)
    vmax = _opt(alg.VAL_MAX)
    cap = alg.parameterAsBool(parameters, alg.VAL_CAP, context)

    xd, yd, vrd = _read_points(source, zfield, feedback,
                               vmin=vmin, vmax=vmax, pct=pct, cap=cap)
    _warn_data(feedback, xd, yd, vrd)

    # --- логарифмирование: кригинг лог-нормальных величин (K, T, ...) -------
    # Кригуется ln(Z), оценка возвращается через exp (медианная оценка), а
    # стандартная ошибка пересчитывается в исходные единицы дельта-методом
    # SE_Z ≈ Z·SE_ln. Параметр есть только у «2D Kriging», читаем через getattr.
    logt = False
    if getattr(alg, "TRANSFORM", None) and \
            alg.parameterAsEnum(parameters, alg.TRANSFORM, context) == 1:
        pos = vrd > 0
        ndrop = int((~pos).sum())
        if ndrop:
            feedback.pushWarning(_tr(
                "Логарифм: отброшено %d точек со значением ≤ 0 "
                "(ln определён только для положительных).") % ndrop)
            xd, yd, vrd = xd[pos], yd[pos], vrd[pos]
        if len(vrd) < 2:
            raise QgsProcessingException(_tr(
                "Логарифм: положительных значений недостаточно для кригинга."))
        logt = True
        vrd = np.log(vrd)
        feedback.pushInfo(_tr(
            "Логарифмирование включено: кригуется ln(Z), оценка возвращается "
            "через exp (медиана). Вариограмму, наггет и среднее простого "
            "кригинга задавайте в единицах ln. Стандартная ошибка "
            "пересчитывается в исходные единицы дельта-методом."))

    # --- регрессия-кригинг: снятие полиномиального тренда -------------------
    # Тренд снимается МНК, дальше кригуются остатки, а после построения грида
    # тренд добавляется обратно к оценке. Параметры есть только у «2D Kriging»,
    # поэтому читаем через getattr - прочие вызовы остаются без изменения.
    trend = None
    if getattr(alg, "DETREND", None) and \
            alg.parameterAsBool(parameters, alg.DETREND, context):
        degree = alg.parameterAsEnum(parameters, alg.DETREND_DEG, context) + 1
        need = PolyTrend.n_terms(degree)
        if len(vrd) <= need:
            feedback.pushWarning(
                _tr("Снятие тренда отключено: точек %d, для степени %d нужно "
                "больше %d.") % (len(vrd), degree, need))
        else:
            var0 = float(np.var(vrd))
            trend = PolyTrend.fit(xd, yd, vrd, degree)
            vrd = trend.residuals(xd, yd, vrd)
            var1 = float(np.var(vrd))
            share = 100.0 * (1.0 - var1 / var0) if var0 > 0 else 0.0
            feedback.pushInfo(
                _tr("Снят тренд степени %d: убрано %.1f%% дисперсии "
                "(s данных %.4g, s остатка %.4g). Вариограмму задавайте по "
                "остаткам, стандартная ошибка - это ошибка кригинга остатков.")
                % (degree, share, math.sqrt(var0), math.sqrt(var1)))

    # --- кригинг с внешним дрейфом: снятие дрейфа по растру -----------------
    # Дрейф - линейная (или квадратичная) регрессия значения на стороннюю
    # величину s, известную всюду (растр). Снимается МНК, кригуются остатки, а
    # дрейф возвращается к оценке из того же растра, пересчитанного на сетку.
    # Та же схема регрессия-кригинг, что и у полиномиального тренда. Параметры
    # есть только у инструмента внешнего дрейфа, поэтому читаем через getattr.
    drift = None
    drift_src = None
    drift_band = 1
    if getattr(alg, "DRIFT_RASTER", None) and \
            parameters.get(alg.DRIFT_RASTER) not in (None, ""):
        rl = alg.parameterAsRasterLayer(parameters, alg.DRIFT_RASTER, context)
        if rl is None:
            raise QgsProcessingException(_tr("Не удалось открыть растр дрейфа."))
        drift_src = rl.source()
        if getattr(alg, "DRIFT_BAND", None):
            drift_band = max(int(alg.parameterAsInt(
                parameters, alg.DRIFT_BAND, context)), 1)
        if rl.crs() != source.sourceCrs():
            feedback.pushWarning(_tr(
                "Растр дрейфа и точки в разных системах координат. Совместите "
                "CRS, иначе выборка дрейфа в скважинах будет неверной."))
        ddeg = alg.parameterAsEnum(parameters, alg.DRIFT_DEG, context) + 1
        s_pts = _sample_raster_bilinear(drift_src, xd, yd, drift_band)
        if s_pts is None:
            raise QgsProcessingException(_tr("Не удалось прочитать растр дрейфа."))
        ok = np.isfinite(s_pts)
        need = ExternalDrift.n_terms(ddeg)
        if int(ok.sum()) <= need:
            feedback.pushWarning(_tr(
                "Внешний дрейф отключён: точек со значением дрейфа %d, для "
                "модели нужно больше %d.") % (int(ok.sum()), need))
        else:
            if not ok.all():
                feedback.pushWarning(_tr(
                    "Отброшено %d точек вне растра дрейфа (нет значения s).")
                    % int((~ok).sum()))
                xd, yd, vrd, s_pts = xd[ok], yd[ok], vrd[ok], s_pts[ok]
            var0 = float(np.var(vrd))
            drift = ExternalDrift.fit(s_pts, vrd, ddeg)
            vrd = drift.residuals(s_pts, vrd)
            var1 = float(np.var(vrd))
            share = 100.0 * (1.0 - var1 / var0) if var0 > 0 else 0.0
            feedback.pushInfo(_tr(
                "Снят внешний дрейф степени %d: убрано %.1f%% дисперсии "
                "(s данных %.4g, s остатка %.4g). Кригуются остатки, дрейф "
                "возвращается к оценке из растра. Вариограмму задавайте по "
                "остаткам.") % (ddeg, share, math.sqrt(var0), math.sqrt(var1)))

    ext = alg.parameterAsExtent(parameters, alg.EXTENT, context)
    if ext is None or ext.isEmpty():
        xmin, xmax = float(xd.min()), float(xd.max())
        ymin, ymax = float(yd.min()), float(yd.max())
    else:
        xmin, xmax = ext.xMinimum(), ext.xMaximum()
        ymin, ymax = ext.yMinimum(), ext.yMaximum()
    width, height = xmax - xmin, ymax - ymin
    if cell <= 0:
        cell = round((min(width, height) or 1.0) / 50.0, 5) or 1.0
    nx = max(int(math.ceil(width / cell)), 1)
    ny = max(int(math.ceil(height / cell)), 1)
    xmn, ymn = xmin + 0.5 * cell, ymin + 0.5 * cell
    if radius <= 0:
        radius = math.hypot(width, height) or 1e12
    rad2 = radius * radius
    auto_range = max(width, height) / 3.0 or 1.0

    vg = _build_variogram(alg, parameters, context, nugget, auto_range, feedback)
    nodata = -9999.0
    want_se = stderr_path is not None
    feedback.pushInfo(_tr("Сетка %d x %d, ячейка %.4g, точек %d, структур %d") %
                      (nx, ny, cell, len(xd), vg.nst))
    feedback.pushInfo(
        _tr("Дисперсия данных: %.4g. Ориентир: суммарный силл (C0 + вклады C) "
        "задавайте близким к ней. Наггет и силл - в абсолютных единицах "
        "дисперсии, не 0-1.") % float(np.var(vrd)))

    def prog(done, total):
        if feedback.isCanceled():
            raise QgsProcessingException(_tr("Прервано пользователем."))
        feedback.setProgress(int(80.0 * done / total))

    # блочный кригинг - параметры есть только у «2D Kriging», читаем через
    # getattr, прочие вызовы (CV, индикатор) остаются точечными.
    ndisc = 1
    if getattr(alg, "BLOCK", None) and \
            alg.parameterAsBool(parameters, alg.BLOCK, context):
        ndisc = max(int(alg.parameterAsInt(parameters, alg.BLOCK_DISC, context)), 2)
        feedback.pushInfo(
            _tr("Блочный кригинг: дискретизация %d×%d на ячейку. Оценка - среднее "
            "по блоку, стандартная ошибка блочная (ниже точечной). Значения в "
            "узлах-пробах точно не воспроизводятся.") % (ndisc, ndisc))

    res = build_grid(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
                     rad2, nodata, xmn, ymn, cell, nx, ny, progress=prog,
                     with_variance=want_se, ndisc=ndisc)
    grid, segrid = res if want_se else (res, None)

    # --- регрессия-кригинг: возврат тренда к оценке ------------------------
    # Координаты ячеек строго как в build_grid (строка 0 - север):
    # iy = ny - row, yloc = ymn + (iy-1)*cell, xloc = xmn + ix*cell.
    # Тренд добавляется только к валидным ячейкам, nodata остаётся nodata.
    # Стандартная ошибка не меняется: тренд детерминирован, кригуются остатки.
    if trend is not None:
        xs_cells = xmn + np.arange(nx) * cell
        valid = grid != nodata
        for row in range(ny):
            iy = ny - row
            yloc = ymn + (iy - 1) * cell
            m = valid[row]
            if m.any():
                tr_row = trend(xs_cells[m], np.full(int(m.sum()), yloc))
                grid[row, m] = grid[row, m] + tr_row.astype(grid.dtype)

    # --- внешний дрейф: возврат дрейфа к оценке из растра -------------------
    # Растр дрейфа пересчитывается на ту же сетку (центры ячеек = узлы), дрейф
    # m(s) добавляется к кригованным остаткам. Ячейки, не покрытые растром
    # дрейфа, достроить нельзя - их оценка и стандартная ошибка становятся
    # nodata. Дрейф детерминирован, своей погрешности к ошибке не добавляет.
    if drift is not None:
        sg = _resample_drift_to_grid(drift_src, xmin, ymin, cell, nx, ny,
                                     drift_band)
        if sg is None:
            raise QgsProcessingException(_tr(
                "Не удалось пересчитать растр дрейфа на сетку кригинга."))
        have_s = np.isfinite(sg)
        dvals = drift(sg.ravel()).reshape(sg.shape)
        add = (grid != nodata) & have_s
        grid[add] = grid[add] + dvals[add].astype(grid.dtype)
        lost = (grid != nodata) & (~have_s)
        n_lost = int(lost.sum())
        if n_lost:
            grid[lost] = nodata
            if segrid is not None:
                segrid[lost] = nodata
            feedback.pushInfo(_tr(
                "%d ячеек оставлены пустыми: растр дрейфа их не покрывает.")
                % n_lost)

    # --- логарифмирование: обратное преобразование оценки и ошибки ----------
    # Оценка ln -> exp (медиана). Стандартная ошибка ln -> исходные единицы
    # дельта-методом: SE_Z ≈ exp(оценка_ln)·SE_ln. nodata сохраняется.
    if logt:
        valid = grid != nodata
        lin = np.exp(np.where(valid, grid, 0.0))
        if segrid is not None:
            sev = valid & (segrid != nodata)
            segrid = np.where(sev, lin * segrid, nodata).astype(segrid.dtype)
        grid = np.where(valid, lin, nodata).astype(grid.dtype)

    crs = source.sourceCrs()
    geotr = (xmin, cell, 0.0, ymin + ny * cell, 0.0, -cell)

    def _write(dest, array):
        """Пишет один растр; при наличии маски - во временный и клипует в dest."""
        write_path = dest
        if mask_layer is not None:
            write_path = os.path.join(QgsProcessingUtils.tempFolder(),
                                      "krig_%s.tif" % uuid.uuid4().hex)
        driver = gdal.GetDriverByName("GTiff")
        ds = driver.Create(write_path, nx, ny, 1, gdal.GDT_Float32,
                           options=["COMPRESS=LZW", "TILED=YES"])
        ds.SetGeoTransform(geotr)
        if crs is not None and crs.isValid():
            srs = osr.SpatialReference()
            srs.ImportFromWkt(crs.toWkt())
            ds.SetProjection(srs.ExportToWkt())
        band = ds.GetRasterBand(1)
        band.SetNoDataValue(nodata)
        band.WriteArray(array)
        band.FlushCache()
        ds = None
        if mask_layer is not None:
            from qgis import processing
            processing.run("gdal:cliprasterbymasklayer", {
                "INPUT": write_path, "MASK": mask_layer, "NODATA": nodata,
                "CROP_TO_CUTLINE": False, "KEEP_RESOLUTION": True,
                "OUTPUT": dest,
            }, context=context, feedback=feedback, is_child_algorithm=True)

    smooth = alg.parameterAsBool(parameters, alg.SMOOTH, context)
    sm_rad = alg.parameterAsDouble(parameters, alg.SMOOTH_RADIUS, context)
    if smooth and sm_rad and sm_rad > 0:
        feedback.pushInfo(_tr("Сглаживание грида (σ=%g яч.)…") % sm_rad)
        gvalid = np.isfinite(grid) & (grid != nodata)
        gsm = _gaussian_nodata(grid, gvalid, float(sm_rad))
        grid = np.where(gvalid, gsm, nodata).astype(grid.dtype)
    if mask_layer is not None:
        feedback.pushInfo(_tr("Обрезка по маске…"))
    _write(out_path, grid)
    feedback.setProgress(85)
    if want_se:
        _write(stderr_path, segrid)
        feedback.setProgress(88)
    return out_path, nodata, (stderr_path if want_se else None)


def _build_mask(alg, parameters, context, feedback, layer):
    """Маска обрезки: явный полигон из проекта (приоритет) либо выпуклая
    оболочка ВСЕХ точек + буфер."""
    explicit = alg.parameterAsVectorLayer(parameters, alg.MASK, context)
    if explicit is not None:
        return explicit
    if not alg.parameterAsBool(parameters, alg.CLIP_HULL, context):
        return None
    from qgis import processing
    src = layer if layer is not None else parameters.get(alg.INPUT)
    feedback.pushInfo(_tr("Контур скважин: выпуклая оболочка…"))
    # Выпуклая оболочка ВСЕХ точек: сначала dissolve (все объекты -> один
    # мультиточечный объект), затем convexhull (одна оболочка по всем точкам).
    # Раньше использовался native:minimumboundinggeometry, но в QGIS 3.40 этого
    # алгоритма нет ("native:minimumboundinggeometry не найден"); связка
    # dissolve+convexhull даёт тот же результат и доступна во всех версиях.
    collected = processing.run("native:dissolve", {
        "INPUT": src, "FIELD": [], "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    hull = processing.run("native:convexhull", {
        "INPUT": collected, "OUTPUT": "TEMPORARY_OUTPUT",
    }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    buf = alg.parameterAsDouble(parameters, alg.HULL_BUFFER, context)
    if buf and buf > 0:
        hull = processing.run("native:buffer", {
            "INPUT": hull, "DISTANCE": float(buf), "SEGMENTS": 12,
            "DISSOLVE": True, "OUTPUT": "TEMPORARY_OUTPUT",
        }, context=context, feedback=feedback, is_child_algorithm=True)["OUTPUT"]
    return hull


# ---------------------------------------------------------------------------
#  Параметры построения изолиний - общий набор
# ---------------------------------------------------------------------------
def _add_isoline_params(alg):
    alg.addParameter(QgsProcessingParameterNumber(
        alg.INTERVAL, _tr("Шаг изолиний (0 = задать уровни ниже)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.INTERVAL, 1.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.BASE, _tr("Начальный уровень (offset)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.BASE, 0.0)))
    alg.addParameter(QgsProcessingParameterString(
        alg.LEVELS, _tr("Явные уровни (через пробел) - приоритетнее шага"),
        defaultValue=_dv(alg, alg.LEVELS, ""), optional=True))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.INDEX_EVERY, _tr("Главная изолиния каждая N-я (0 = выкл.)"),
        QgsProcessingParameterNumber.Integer,
        defaultValue=_dv(alg, alg.INDEX_EVERY, 5), minValue=0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MIN_LENGTH, _tr("Мин. длина линии, ед. карты (0 = без фильтра)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.MIN_LENGTH, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterEnum(
        alg.DENSIFY, _tr("Бикубическое сглаживание изолиний (сгущение грида)"),
        options=[_tr("выкл."), "×2", "×3", "×4"],
        defaultValue=_dv(alg, alg.DENSIFY, 0)))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.SMOOTH_LINE_ITER, _tr("Скругление линий (Chaikin), итераций "
                                  "(0 = выкл.)"),
        QgsProcessingParameterNumber.Integer,
        defaultValue=_dv(alg, alg.SMOOTH_LINE_ITER, 2), minValue=0, maxValue=5))
    alg.addParameter(QgsProcessingParameterString(
        alg.FIELD_NAME, _tr("Имя поля значения"),
        defaultValue=_dv(alg, alg.FIELD_NAME, DEFAULT_FIELD)))


# ===========================================================================
#  1. Точки → растр (кригинг)
# ===========================================================================
class Kriging2DAlgorithm(QgsProcessingAlgorithm):
    INPUT, ZFIELD = "INPUT", "ZFIELD"
    KTYPE, SKMEAN, NUGGET = "KTYPE", "SKMEAN", "NUGGET"
    RADIUS, MIN_POINTS, MAX_POINTS = "RADIUS", "MIN_POINTS", "MAX_POINTS"
    CELL_SIZE, EXTENT, OUTPUT = "CELL_SIZE", "EXTENT", "OUTPUT"
    CLIP_HULL, HULL_BUFFER, MASK = "CLIP_HULL", "HULL_BUFFER", "MASK"
    OUTPUT_STDERR = "OUTPUT_STDERR"
    PROFILE = "PROFILE"
    VAL_PCT, VAL_MIN, VAL_MAX, VAL_CAP = "VAL_PCT", "VAL_MIN", "VAL_MAX", "VAL_CAP"

    DETREND, DETREND_DEG = "DETREND", "DETREND_DEG"

    TRANSFORM = "TRANSFORM"

    BLOCK, BLOCK_DISC = "BLOCK", "BLOCK_DISC"

    SMOOTH, SMOOTH_RADIUS = "SMOOTH", "SMOOTH_RADIUS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return Kriging2DAlgorithm()
    def name(self): return "kriging2d"
    def displayName(self): return self.tr("1.1 2D Kriging (точки → растр)")

    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP)
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Ординарный/простой кригинг 2D по точечному слою (ядро GSLIB KB2D). "
            "Вариограмма: наггет + структура (сферическая, экспоненциальная, "
            "гауссова или степенная) с азимутом и анизотропией. "
            "Подходит для отметок пласта, мощностей, ФМС, химии и любых "
            "числовых атрибутов.\n\nРадиус поиска 0 = по всей выборке; "
            "размер ячейки 0 = min(охват)/50; радиус корреляции 0 = "
            "max(охват)/3. Опция обрезки убирает экстраполяцию вне контура "
            "скважин.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точечный слой"),
            [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения (Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric,
            defaultValue=_dv(self, self.ZFIELD, None)))
        tf = QgsProcessingParameterEnum(
            self.TRANSFORM, self.tr("Преобразование значения"),
            options=[self.tr("нет"),
                     self.tr("ln (для лог-нормальных, напр. K, T)")],
            defaultValue=_dv(self, self.TRANSFORM, 0))
        tf.setHelp(self.tr(
            "Логарифмирование перед кригингом для величин с разбросом на "
            "порядки (коэффициент фильтрации, водопроводимость, содержания с "
            "длинным правым хвостом). Кригуется ln(Z), оценка возвращается через "
            "exp - это медианная (геометрическая) оценка. Стандартная ошибка "
            "пересчитывается в исходные единицы дельта-методом. Значения должны "
            "быть положительными. Избавляет от ручного создания поля ln(Z). "
            "Вариограмму и наггет при этом задавайте в единицах ln."))
        self.addParameter(tf)
        _add_kriging_params(self)
        self.addParameter(QgsProcessingParameterBoolean(
            self.DETREND, self.tr("Снять полиномиальный тренд"),
            defaultValue=_dv(self, self.DETREND, False)))
        deg = QgsProcessingParameterEnum(
            self.DETREND_DEG, self.tr("Степень тренда"),
            options=[self.tr("1 (плоскость)"), self.tr("2 (квадратичная)")],
            defaultValue=_dv(self, self.DETREND_DEG, 0))
        deg.setHelp(self.tr(
            "Региональный тренд снимается МНК перед кригингом, кригуются остатки, "
            "тренд возвращается к оценке. Полезно для отметок пласта и мощностей "
            "с общим падением. Для химии без тренда эффекта почти нет. Степень 1 "
            "обычно достаточна, степень 2 может вобрать часть реальной структуры "
            "в тренд - следите за вариограммой остатков."))
        self.addParameter(deg)
        block = QgsProcessingParameterBoolean(
            self.BLOCK, self.tr("Блочный кригинг"),
            defaultValue=_dv(self, self.BLOCK, False))
        block.setHelp(self.tr(
            "Оценивает СРЕДНЕЕ по ячейке грида, а не значение в её центре: каждая "
            "ячейка разбивается на N×N точек дискретизации, ковариации "
            "усредняются по блоку. Поверхность глаже, стандартная ошибка ниже "
            "точечной - подходит для оценки запасов и содержаний по блоку. "
            "Пробы при этом не воспроизводятся точно (среднее блока ≠ значение "
            "в точке). Выключено - обычный точечный кригинг."))
        self.addParameter(block)
        disc = QgsProcessingParameterNumber(
            self.BLOCK_DISC, self.tr("Дискретизация блока, N×N на ячейку"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.BLOCK_DISC, 4), minValue=2, maxValue=10)
        disc.setHelp(self.tr(
            "Сколько точек на сторону ячейки берётся для усреднения по блоку "
            "(всего N×N). 4×4 достаточно почти всегда; больше - точнее, но "
            "медленнее. Действует только при включённом блочном кригинге."))
        self.addParameter(disc)
        self.addParameter(_profile_enum(
            self.PROFILE, _tr("Загрузить профиль обработки"), alg=self))
        self.addParameter(QgsProcessingParameterBoolean(
            self.SMOOTH, self.tr("Сгладить грид (Гаусс)"),
            defaultValue=_dv(self, self.SMOOTH, False)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH_RADIUS, self.tr("Радиус сглаживания, ячеек"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.SMOOTH_RADIUS, 1.0),
            minValue=0.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Растр кригинга")))
        se = QgsProcessingParameterRasterDestination(
            self.OUTPUT_STDERR, self.tr("Стандартная ошибка кригинга"),
            optional=True, createByDefault=False)
        se.setHelp(self.tr(
            "Необязательный растр стандартной ошибки кригинга (sqrt дисперсии "
            "ошибки): мера неопределённости оценки. Мала у скважин, растёт "
            "вдали от данных."))
        self.addParameter(se)

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        parameters = _apply_profile(self, parameters, context, feedback)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        se_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_STDERR, context) or None
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        src = layer.name() if layer is not None else "data"
        mask = _build_mask(self, parameters, context, feedback, layer)
        path, _, se = _run_kriging_to_tiff(self, parameters, context, feedback,
                                           source, zfield, out_path, mask,
                                           stderr_path=se_path)
        _set_output_name(context, path,
                         _tr("Кригинг %s · %s") % (zfield, _short(src)))
        results = {self.OUTPUT: path}
        if se:
            _set_output_name(context, se,
                             _tr("Стд. ошибка · %s · %s") % (zfield, _short(src)))
            results[self.OUTPUT_STDERR] = se
        _save_values(self, _saved)
        feedback.setProgress(100)
        return results


# ===========================================================================
#  Категориальный индикаторный кригинг
# ===========================================================================
class CategoricalIndicatorAlgorithm(QgsProcessingAlgorithm):
    """Категориальный индикаторный кригинг по текстовому/категориальному полю.
    На каждый класс строит индикатор 0/1, кригует ординарным кригингом (ядро
    KB2D), нормирует вероятности к сумме 1. Выход: многополосный растр
    вероятностей (полоса на класс), карта зон (самый вероятный класс) и
    уверенность. Кодом класса не кригует - у категорий нет порядка."""
    INPUT, CLASS_FIELD = "INPUT", "CLASS_FIELD"
    RADIUS, MIN_POINTS, MAX_POINTS = "RADIUS", "MIN_POINTS", "MAX_POINTS"
    CELL_SIZE, EXTENT = "CELL_SIZE", "EXTENT"
    OUTPUT_PROB, OUTPUT_ZONE, OUTPUT_CONF = \
        "OUTPUT_PROB", "OUTPUT_ZONE", "OUTPUT_CONF"

    def tr(self, s): return _tr(s)
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID
    def name(self): return "categorical_indicator"
    def displayName(self):
        return self.tr("2.1 Категориальный индикаторный кригинг")
    def createInstance(self): return CategoricalIndicatorAlgorithm()

    def shortHelpString(self):
        return _help_version(self.tr(
            "Индикаторный кригинг по категориальному полю (минтип, литотип, "
            "класс). На каждый класс строится индикатор 0/1, кригуется отдельно "
            "(ядро GSLIB KB2D), оценка обрезается в 0-1, затем вероятности по "
            "классам нормируются к сумме 1. Кодом класса не кригуем: у категорий "
            "нет порядка.\n\nВыход: многополосный растр вероятностей (полоса на "
            "класс, в описании полосы - имя класса), растр зон (код самого "
            "вероятного класса, соответствие кодов в Журнале) и растр "
            "уверенности (максимум вероятности). Пустые и NULL исключаются. "
            "Вариограмма каждого индикатора подбирается автоматически "
            "(сферическая).") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точечный слой"),
            [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.CLASS_FIELD, self.tr("Категориальное поле (класс)"),
            parentLayerParameterName=self.INPUT,
            defaultValue=_dv(self, self.CLASS_FIELD, None)))
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Радиус поиска (0 = вся выборка)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.RADIUS, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_POINTS, self.tr("Мин. число точек"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.MIN_POINTS, 4), minValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_POINTS, self.tr("Макс. число точек"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.MAX_POINTS, 24), minValue=1, maxValue=120))
        cs = QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Размер ячейки (0 = авто, min(охват)/50)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.CELL_SIZE, 0.0), minValue=0.0)
        try:
            from .widgets import CellSizeWrapper, WRAPPER_AVAILABLE
            if WRAPPER_AVAILABLE:
                cs.setMetadata({"widget_wrapper": {"class": CellSizeWrapper}})
        except Exception:
            pass
        self.addParameter(cs)
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Охват растра (по умолчанию - по слою)"),
            optional=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_PROB, self.tr("Вероятности по классам (многополосный)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_ZONE, self.tr("Карта зон (самый вероятный класс)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_CONF, self.tr("Уверенность (макс. вероятность)"),
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        field = self.parameterAsString(parameters, self.CLASS_FIELD, context)
        if source is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))

        xs, ys, labels = [], [], []
        for ft in source.getFeatures():
            if feedback.isCanceled():
                raise QgsProcessingException(_tr("Прервано пользователем."))
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            v = ft.attribute(field)
            if v is None:
                continue
            s = str(v).strip()
            if s == "" or s.upper() == "NULL":
                continue
            p = g.asPoint()
            xs.append(p.x()); ys.append(p.y()); labels.append(s)
        if len(xs) < 3:
            raise QgsProcessingException(
                _tr("Слишком мало точек с заданным классом."))
        xs = np.asarray(xs, float); ys = np.asarray(ys, float)
        labels = np.asarray(labels, dtype=object)
        classes = sorted(set(labels.tolist()))
        feedback.pushInfo(_tr("Классов: %d, точек: %d.") % (len(classes), len(xs)))
        for c in classes:
            ncls = int((labels == c).sum())
            feedback.pushInfo("  %s: %d" % (c, ncls))
            if ncls < 10:
                feedback.pushWarning(_tr(
                    "Класс «%s»: всего %d точек, индикаторная вариограмма будет "
                    "шумной, вероятность по нему ненадёжна.") % (c, ncls))

        crs = source.sourceCrs()
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if rect is None or rect.isEmpty():
            rect = source.sourceExtent()
        xmin, xmax = rect.xMinimum(), rect.xMaximum()
        ymin, ymax = rect.yMinimum(), rect.yMaximum()
        width, height = xmax - xmin, ymax - ymin
        cell = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
        if cell <= 0:
            cell = (min(width, height) / 50.0) or 1.0
        nx = max(int(math.ceil(width / cell)), 1)
        ny = max(int(math.ceil(height / cell)), 1)
        xmn, ymn = xmin + 0.5 * cell, ymin + 0.5 * cell
        radius = self.parameterAsDouble(parameters, self.RADIUS, context)
        if radius <= 0:
            radius = math.hypot(width, height) or 1e12
        ndmin = self.parameterAsInt(parameters, self.MIN_POINTS, context)
        ndmax = self.parameterAsInt(parameters, self.MAX_POINTS, context)
        nodata = -9999.0
        feedback.pushInfo(_tr("Сетка %d x %d, ячейка %.4g.") % (nx, ny, cell))

        def prog(k, K, done, total):
            if feedback.isCanceled():
                raise QgsProcessingException(_tr("Прервано пользователем."))
            feedback.setProgress(int(88.0 * (k + done / max(total, 1)) / max(K, 1)))

        from .kb2d import categorical_indicator_grids
        probs, zone, conf = categorical_indicator_grids(
            xs, ys, labels, classes, xmn, ymn, cell, nx, ny,
            ndmin=ndmin, ndmax=ndmax, radius=radius, nodata=nodata, progress=prog)

        geotr = (xmin, cell, 0.0, ymin + ny * cell, 0.0, -cell)
        wkt = None
        if crs is not None and crs.isValid():
            srs = osr.SpatialReference(); srs.ImportFromWkt(crs.toWkt())
            wkt = srs.ExportToWkt()
        drv = gdal.GetDriverByName("GTiff")
        opt = ["COMPRESS=LZW", "TILED=YES"]

        def _create(path, nbands):
            ds = drv.Create(path, nx, ny, nbands, gdal.GDT_Float32, options=opt)
            ds.SetGeoTransform(geotr)
            if wkt:
                ds.SetProjection(wkt)
            return ds

        prob_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_PROB, context)
        zone_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_ZONE, context)
        conf_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_CONF, context)

        K = len(classes)
        ds = _create(prob_path, K)
        for k in range(K):
            b = ds.GetRasterBand(k + 1)
            b.SetNoDataValue(nodata)
            b.SetDescription(classes[k])
            b.WriteArray(probs[:, :, k]); b.FlushCache()
        ds = None

        zg = zone.astype(np.float32)
        zg[zone < 0] = nodata
        ds = _create(zone_path, 1)
        bz = ds.GetRasterBand(1); bz.SetNoDataValue(nodata)
        bz.WriteArray(zg); bz.FlushCache(); ds = None

        results = {self.OUTPUT_PROB: prob_path, self.OUTPUT_ZONE: zone_path}
        if conf_path:
            ds = _create(conf_path, 1)
            bc = ds.GetRasterBand(1); bc.SetNoDataValue(nodata)
            bc.WriteArray(conf); bc.FlushCache(); ds = None
            results[self.OUTPUT_CONF] = conf_path

        _set_output_name(context, prob_path, _tr("Вероятности минтипа"))
        _set_output_name(context, zone_path, _tr("Зоны минтипа"))
        feedback.pushInfo(_tr("Коды зон: ") + "; ".join(
            "%d=%s" % (i, classes[i]) for i in range(K)))
        feedback.pushInfo(_tr(
            "Полосы растра вероятностей подписаны именами классов."))
        _save_values(self, _saved)
        feedback.setProgress(100)
        return results


# ===========================================================================
#  2. Растр → изолинии
# ===========================================================================
class RasterToIsolinesAlgorithm(QgsProcessingAlgorithm):
    INPUT, BAND = "INPUT", "BAND"
    INTERVAL, BASE, LEVELS = "INTERVAL", "BASE", "LEVELS"
    INDEX_EVERY, MIN_LENGTH = "INDEX_EVERY", "MIN_LENGTH"
    SMOOTH, SMOOTH_RADIUS = "SMOOTH", "SMOOTH_RADIUS"
    SMOOTH_LINE_ITER = "SMOOTH_LINE_ITER"
    DENSIFY = "DENSIFY"
    STYLE = "STYLE"
    FIELD_NAME, OUTPUT, OUTPUT_POLYGONS = "FIELD_NAME", "OUTPUT", "OUTPUT_POLYGONS"

    # выбор стиля линий -> имя пресета в папке styles (None = без стиля).
    # Депрессия сама включает расчёт стороны склона (dn_sign), отдельной галки нет.
    _STYLE_MAP = [None, "iso_structure", "iso_depression"]
    _STYLE_LABELS = ["Без стиля", "Структура / гипсометрия",
                     "Депрессия (штрихи вниз)"]

    def tr(self, s): return _tr(s)
    def createInstance(self): return RasterToIsolinesAlgorithm()
    def name(self): return "raster_to_isolines"
    def displayName(self): return self.tr("1.2 Изолинии из растра")

    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP)
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Строит изолинии из растра: равномерный шаг или явные уровни "
            "(через пробел), главные (утолщённые) изолинии флагом is_index, "
            "фильтр коротких линий.\n\nСкругление линий (Chaikin) слегка "
            "сглаживает контуры и убирает «октагоны» от грубого грида. "
            "Сглаживание самого поля выполняется в инструменте 2D Kriging.\n\n"
            "Бикубическое сглаживание (сгущение грида ×2…×4) даёт гладкие "
            "изолинии без «октагонов» от грубой сетки - это основной способ "
            "сглаживания, сильнее скругления линий (Chaikin). Работает и для "
            "линий, и для контурных полигонов: границы поясов совпадают с "
            "изолиниями.\n\n"
            "По умолчанию строит и "
            "контурные полигоны (пояса между изолиниями) во временный слой - их "
            "границы СОВПАДАЮТ с изолиниями, покрытие сплошное. Чтобы их не "
            "строить - очистите поле «Контурные полигоны».\n\nПоля: линии - "
            "значение уровня (по умолчанию ELEV) и is_index (1 у главных); "
            "полигоны - ELEV_MIN/ELEV_MAX (диапазон пояса).") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Растр")))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BAND, self.tr("Канал"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        _add_isoline_params(self)
        self.addParameter(QgsProcessingParameterEnum(
            self.STYLE, self.tr("Стиль изолиний"),
            options=[self.tr(x) for x in self._STYLE_LABELS],
            defaultValue=_dv(self, self.STYLE, 1)))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUTPUT, self.tr("Изолинии (линии)"),
            type=QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUTPUT_POLYGONS, self.tr("Контурные полигоны"),
            type=QgsProcessing.TypeVectorPolygon,
            optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        rl = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if rl is None:
            raise QgsProcessingException(self.tr("Не задан растр."))
        band = self.parameterAsInt(parameters, self.BAND, context)
        prov = rl.dataProvider()
        nodata = (prov.sourceNoDataValue(band)
                  if prov.sourceHasNoDataValue(band) else -9999.0)
        name = _short(rl.name())

        interval = self.parameterAsDouble(parameters, self.INTERVAL, context)
        base = self.parameterAsDouble(parameters, self.BASE, context)
        levels = self.parameterAsString(parameters, self.LEVELS, context)
        index_every = self.parameterAsInt(parameters, self.INDEX_EVERY, context)
        min_len = self.parameterAsDouble(parameters, self.MIN_LENGTH, context)
        sm_line = self.parameterAsInt(parameters, self.SMOOTH_LINE_ITER, context)
        densify = (1, 2, 3, 4)[self.parameterAsInt(
            parameters, self.DENSIFY, context)]
        field_name = self.parameterAsString(parameters, self.FIELD_NAME, context)

        # единые уровни для линий и полигонов: явные от пользователя, иначе
        # рассчитанные внутри диапазона растра (так совпадут и не «потеряются»
        # крайние пояса полигонов)
        if not levels.strip() and interval > 0:
            auto = compute_levels(rl.source(), band, interval, base)
            if auto:
                levels = " ".join(repr(float(v)) for v in auto)

        out_dest = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        poly_dest = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_POLYGONS, context)

        style_idx = self.parameterAsEnum(parameters, self.STYLE, context)
        style_name = self._STYLE_MAP[style_idx] if 0 <= style_idx < len(
            self._STYLE_MAP) else None
        line_style = _style_path(style_name) if style_name else None

        # сторона склона (dn_sign) нужна только депрессионному стилю, поэтому
        # считается автоматически, когда он выбран. Берётся по ИСХОДНОМУ растру
        # (стабильный слой с id), шаг сэмпла ~ ячейка. Сглаживание сторону не
        # переворачивает.
        slope_ref = None
        if style_name == "iso_depression":
            eps = rl.rasterUnitsPerPixelX() or 1.0
            slope_ref = (rl.id(), band, float(eps))

        if poly_dest:
            # линии и пояса строятся из ОДНОГО набора линий -> границы совпадают
            res = isolines_and_polygons(
                rl.source(), band, interval, base, levels, index_every,
                min_len, False, 0.0, densify, sm_line, field_name, True, nodata,
                out_dest, poly_dest, context, feedback, slope_ref=slope_ref)
            out, poly = res["lines"], res["polygons"]
            _set_output_name(context, out, _tr("Изолинии · %s") % name)
            _set_output_name(context, poly, _tr("Полигоны · %s") % name)
            st = _OrderState()
            _KEEP_ALIVE.append(st)
            _attach_style(context, out, line_style, st, "lines")
            _attach_style(context, poly, None, st, "polys")
            results = {self.OUTPUT: out, self.OUTPUT_POLYGONS: poly}
        else:
            out = isolines_from_raster(
                rl.source(), band, interval, base, levels, index_every,
                min_len, False, 0.0, densify, sm_line, field_name, True, nodata,
                out_dest, context, feedback, slope_ref=slope_ref)
            _set_output_name(context, out, _tr("Изолинии · %s") % name)
            _attach_style(context, out, line_style)
            results = {self.OUTPUT: out}

        _save_values(self, _saved)
        feedback.setProgress(100)
        return results


def _cv_advice(me, mae, rmse, msdr, r):
    """Авто-интерпретация метрик: итог + конкретные действия."""
    tips = []
    nan = float("nan")
    rr = rmse if rmse and rmse == rmse else 1.0
    good = True
    if msdr == msdr:
        if msdr > 1.3:
            tips.append(_tr("MSDR заметно больше 1 (%.3g): карта стандартной ошибки "
                        "занижена. Умножьте наггет C0 и вклады C на MSDR (радиус "
                        "и модель не трогайте) и пересчитайте - сами оценки не "
                        "изменятся, поправится только дисперсия кригинга.") % msdr)
            good = False
        elif msdr < 0.7:
            tips.append(_tr("MSDR меньше 1 (%.3g): неопределённость завышена. "
                        "Разделите наггет C0 и вклады C на MSDR (радиус и модель "
                        "не трогайте) и пересчитайте - оценки не изменятся.") % msdr)
            good = False
        else:
            tips.append(_tr("MSDR близок к 1: масштаб вариограммы подобран адекватно."))
    if abs(me) > 0.1 * rr:
        tips.append(_tr("ME заметно отличается от 0 (%+.3g): возможен "
                    "систематический сдвиг - проверьте данные и тип кригинга "
                    "(для простого - заданное среднее).") % me)
        good = False
    else:
        tips.append(_tr("ME близок к 0: систематического смещения нет."))
    if r == r:
        if r < 0.5:
            tips.append(_tr("Низкая корреляция (R=%.2f): модель слабо предсказывает - "
                        "попробуйте другой радиус, модель или анизотропию; либо "
                        "это предел данных (короткомасштабная изменчивость, "
                        "зоны замещения).") % r)
            good = False
        elif r >= 0.8:
            tips.append(_tr("Высокая корреляция (R=%.2f): оценки хорошо согласуются "
                        "с фактом.") % r)
    if good:
        tips.insert(0, _tr("Итог: параметры можно утверждать - перенесите ту же "
                       "вариограмму и настройки поиска в «2D Kriging»."))
    else:
        tips.insert(0, _tr("Итог: параметры стоит подправить (см. ниже) и повторить "
                       "кросс-валидацию перед финальным кригингом."))
    return tips


def _cv_used_params(alg, parameters, context):
    """Список (метка, значение) для параметров кригинга, отличных от
    стандартных. Нужен, чтобы отчёт был самодокументируемым."""
    P = parameters
    def pd(name): return alg.parameterAsDouble(P, name, context)
    def pi(name): return alg.parameterAsInt(P, name, context)
    def pe(name): return alg.parameterAsEnum(P, name, context)
    def pb(name): return alg.parameterAsBool(P, name, context)
    def popt(name):
        v = P.get(name, None)
        return None if (v is None or v == "") else pd(name)
    items = []
    if pe(alg.KTYPE) != 0:
        items.append((_tr("Тип кригинга"), _tr("простой (SK)")))
        items.append((_tr("Среднее (SK)"), "%.4g" % pd(alg.SKMEAN)))
    nug = pd(alg.NUGGET)
    if nug != 0:
        items.append((_tr("Наггет C0"), "%.4g" % nug))
    for i in range(1, NSTRUCT + 1):
        sill = pd(_sk(i, "SILL"))
        defsill = 1.0 if i == 1 else 0.0
        if sill <= 0 and i != 1:
            continue
        parts = []
        model = pe(_sk(i, "MODEL"))
        if model != 0:
            parts.append(MODEL_LABELS[model].lower())
        if sill != defsill:
            parts.append("C=%.4g" % sill)
        rng = pd(_sk(i, "RANGE"))
        if rng != 0:
            parts.append("a=%g" % rng)
        az = pd(_sk(i, "AZIMUTH"))
        if az != 0:
            parts.append(_tr("азимут=%g°") % az)
        anis = pd(_sk(i, "ANIS"))
        if anis != 1:
            parts.append(_tr("анис=%g") % anis)
        if parts:
            items.append((_tr("Структура %d") % i, ", ".join(parts)))
    if pd(alg.RADIUS) != 0:
        items.append((_tr("Радиус поиска"), "%g" % pd(alg.RADIUS)))
    if pi(alg.MIN_POINTS) != 1:
        items.append((_tr("Мин. точек"), "%d" % pi(alg.MIN_POINTS)))
    if pi(alg.MAX_POINTS) != 24:
        items.append((_tr("Макс. точек"), "%d" % pi(alg.MAX_POINTS)))
    if pd(alg.VAL_PCT) != 0:
        items.append((_tr("Отсев: перцентиль, %"), "%.4g" % pd(alg.VAL_PCT)))
    vmin = popt(alg.VAL_MIN)
    if vmin is not None:
        items.append((_tr("Нижняя граница"), "%.4g" % vmin))
    vmax = popt(alg.VAL_MAX)
    if vmax is not None:
        items.append((_tr("Верхняя граница"), "%.4g" % vmax))
    if pb(alg.VAL_CAP):
        items.append((_tr("Срезка (capping)"), _tr("да")))
    return items


def _write_cv_report(path, title, metrics, advice, fact, est, err,
                     ids=None, used_params=None, feedback=None):
    """Записать HTML-отчёт кросс-валидации: интерактивный график plotly
    (оценка vs факт с подписями скважин, гистограмма ошибок, QQ-график
    ошибок по форме) и таблица метрик. QQ строится по ошибкам, нормированным
    на их собственную дисперсию (z-оценка), поэтому показывает форму
    распределения (хвосты, скос, вторая популяция) независимо от калибровки
    масштаба - за масштаб отвечает MSDR. Если plotly недоступен - текстовый
    отчёт только с метриками (graceful fallback)."""
    rows = "".join(
        "<tr><td>%s</td><td style='text-align:right'>%s</td>"
        "<td style='color:#777'>%s</td></tr>" % m for m in metrics)
    table = (_tr("<table style='border-collapse:collapse' cellpadding='6'>"
             "<tr><th align='left'>Метрика</th><th>Значение</th>"
             "<th align='left'>Смысл</th></tr>%s</table>") % rows)
    if used_params:
        prows = "".join(
            "<tr><td style='color:#555'>%s</td>"
            "<td style='text-align:right'><b>%s</b></td></tr>" % kv
            for kv in used_params)
        params_inner = ("<table style='border-collapse:collapse' "
                        "cellpadding='4'>%s</table>" % prows)
    else:
        params_inner = (_tr("<span style='color:#777'>все параметры - "
                        "стандартные</span>"))
    params_box = (
        _tr("<div style='background:#f5f5f7;border:1px solid #ddd;"
        "padding:8px 14px;border-radius:6px'>"
        "<b>Параметры кригинга</b> "
        "<span style='color:#888;font-size:88%%'>(отличные от стандартных)</span>"
        "<div style='margin-top:6px'>%s</div></div>") % params_inner)
    table = ("<div style='display:flex;gap:24px;flex-wrap:wrap;"
             "align-items:flex-start'><div>%s</div><div>%s</div></div>"
             % (table, params_box))
    advice_html = ""
    if advice:
        items = "".join("<li>%s</li>" % a for a in advice)
        advice_html = (
            _tr("<div style='background:#f3f7f4;border:1px solid #cde0d6;"
            "padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'>"
            "<b>Рекомендации</b><ul style='margin:6px 0'>%s</ul></div>") % items)
    chart = ""
    try:
        import numpy as _np
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        from statistics import NormalDist
        n = len(fact)
        # подписи скважин (номер либо порядковый #)
        if ids is not None and len(ids) == n:
            labels = [(("" if v is None else str(v)) or ("#%d" % (i + 1)))
                      for i, v in enumerate(ids)]
        else:
            labels = ["#%d" % (i + 1) for i in range(n)]
        ae = _np.abs(err)
        nworst = min(8, n)
        worst = _np.argsort(ae)[-nworst:] if nworst > 0 else _np.array([], int)
        # подвыборка для отзывчивости, но худшие остатки всегда включены
        if n > 30000:
            sel = _np.random.default_rng(0).choice(n, 30000, replace=False)
            sel = _np.unique(_np.concatenate([sel, worst]))
            note = _tr(" (показаны ~30000 точек)")
        else:
            sel = _np.arange(n); note = ""
        fx = fact[sel]; ey = est[sel]
        lab_sel = [labels[i] for i in sel]
        lo = float(min(fx.min(), ey.min())); hi = float(max(fx.max(), ey.max()))
        fig = make_subplots(
            rows=2, cols=2, row_heights=[0.58, 0.42],
            specs=[[{"colspan": 2}, None], [{}, {}]],
            subplot_titles=(_tr("Оценка vs факт") + note,
                            _tr("Гистограмма ошибок"), _tr("QQ-график остатков")),
            vertical_spacing=0.12)
        fig.add_trace(go.Scattergl(
            x=fx, y=ey, mode="markers",
            marker=dict(size=4, color="#1f6f54", opacity=0.35),
            customdata=lab_sel,
            hovertemplate=_tr("скв. %{customdata}<br>факт %{x:.3g}"
                          "<br>оценка %{y:.3g}<extra></extra>")),
            row=1, col=1)
        fig.add_trace(go.Scatter(
            x=[lo, hi], y=[lo, hi], mode="lines",
            line=dict(color="#cc3333", width=2), hoverinfo="skip"),
            row=1, col=1)
        # подписи худших по модулю остатков прямо на графике
        if worst.size:
            fig.add_trace(go.Scatter(
                x=fact[worst], y=est[worst], mode="markers+text",
                marker=dict(size=8, color="#cc3333", symbol="circle-open",
                            line=dict(width=1.5)),
                text=[labels[i] for i in worst], textposition="top center",
                textfont=dict(size=9, color="#cc3333"),
                hovertemplate=_tr("скв. %{text}<br>факт %{x:.3g}"
                              "<br>оценка %{y:.3g}<extra>худшие</extra>")),
                row=1, col=1)
        fig.add_trace(go.Histogram(x=err, marker_color="#1f6f54"),
                      row=2, col=1)
        # QQ-график по форме: ошибки нормируем на их собственную дисперсию
        # (z-оценка) и сравниваем с нормальным распределением. Так график
        # показывает форму (хвосты/скос/вторая популяция) при любом MSDR.
        e_arr = _np.asarray(err, float)
        e_arr = e_arr[_np.isfinite(e_arr)]
        sde = float(e_arr.std()) or 1.0
        sr = (e_arr - e_arr.mean()) / sde
        if sr.size >= 5:
            srs = _np.sort(sr); m = srs.size
            nd = NormalDist()
            theo = _np.array([nd.inv_cdf((i + 0.5) / m) for i in range(m)])
            ql = float(min(theo.min(), srs.min()))
            qh = float(max(theo.max(), srs.max()))
            fig.add_trace(go.Scattergl(
                x=theo, y=srs, mode="markers",
                marker=dict(size=4, color="#1f6f54", opacity=0.5),
                hovertemplate=_tr("теор. %{x:.2f}<br>ошибка (z) %{y:.2f}<extra></extra>")),
                row=2, col=2)
            fig.add_trace(go.Scatter(
                x=[ql, qh], y=[ql, qh], mode="lines",
                line=dict(color="#cc3333", width=2), hoverinfo="skip"),
                row=2, col=2)
            fig.update_xaxes(title_text=_tr("теор. квантили (норм.)"), row=2, col=2)
            fig.update_yaxes(title_text=_tr("ошибка (z-оценка)"), row=2, col=2)
        else:
            fig.add_annotation(text=_tr("недостаточно точек для QQ"),
                               showarrow=False, row=2, col=2)
        fig.update_xaxes(title_text=_tr("факт"), row=1, col=1)
        fig.update_yaxes(title_text=_tr("оценка (LOO)"), row=1, col=1)
        fig.update_xaxes(title_text=_tr("оценка − факт"), row=2, col=1)
        fig.update_layout(showlegend=False, height=720,
                          margin=dict(l=50, r=20, t=50, b=50))
        chart = fig.to_html(full_html=False, include_plotlyjs=True)
    except Exception as e:
        if feedback is not None:
            feedback.pushInfo(_tr("plotly недоступен (%s) - отчёт без графика.") % e)
        chart = _tr("<p><i>Интерактивный график недоступен (нет plotly). ")
        chart += _tr("Диаграмму можно построить по слою остатков.</i></p>")
    html = (
        "<html><head><meta charset='utf-8'><title>%s</title></head><body>"
        "<h2>%s</h2>%s%s<br>%s%s</body></html>" % (
            title, title, table, advice_html, chart, _version_footer()))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def _add_cv_params(alg):
    """Параметры для кросс-валидации: вариограмма и поиск, без сетки/растра."""
    alg.addParameter(QgsProcessingParameterEnum(
        alg.KTYPE, _tr("Тип кригинга"), options=[_tr(x) for x in KTYPE_LABELS],
        defaultValue=_dv(alg, alg.KTYPE, 0)))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.RADIUS, _tr("Радиус поиска (0 = вся выборка)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.RADIUS, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MIN_POINTS, _tr("Мин. число точек"),
        QgsProcessingParameterNumber.Integer,
        defaultValue=_dv(alg, alg.MIN_POINTS, 1), minValue=1))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MAX_POINTS, _tr("Макс. число точек"),
        QgsProcessingParameterNumber.Integer,
        defaultValue=_dv(alg, alg.MAX_POINTS, 24), minValue=1, maxValue=120))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.SKMEAN, _tr("Среднее для простого кригинга"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.SKMEAN, 0.0))))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.NUGGET, _tr("Наггет C0"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.NUGGET, 0.0), minValue=0.0)))
    for i in range(1, NSTRUCT + 1):
        tag = _tr("Структура %d") % i
        default_sill = 1.0 if i == 1 else 0.0
        off = _tr("порог/вклад C") if i == 1 else _tr("порог/вклад C (0 = выкл.)")
        alg.addParameter(_advanced(QgsProcessingParameterEnum(
            _sk(i, "MODEL"), "%s · %s" % (tag, _tr("модель")),
            options=[_tr(x) for x in MODEL_LABELS], defaultValue=_dv(alg, _sk(i, "MODEL"), 0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "SILL"), "%s · %s" % (tag, off),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "SILL"), default_sill), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "RANGE"), "%s · %s" % (tag, _tr("радиус корреляции a (0=авто)")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "RANGE"), 0.0), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "AZIMUTH"), "%s · %s" % (tag, _tr("азимут, °")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "AZIMUTH"), 0.0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "ANIS"), "%s · %s" % (tag, _tr("анизотропия (малая/главная)")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "ANIS"), 1.0), minValue=EPS)))

    # отсев/срезка ураганных проб (по значению Z) - в самом конце
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_PCT, _tr("Ураганные пробы: перцентиль обрезки, % (0 = выкл.)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.VAL_PCT, 0.0), minValue=0.0, maxValue=49.0)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MIN, _tr("Нижняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MAX, _tr("Верхняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterBoolean(
        alg.VAL_CAP, _tr("Срезать к границе (capping) вместо удаления"),
        defaultValue=_dv(alg, alg.VAL_CAP, False))))


# ===========================================================================
#  3. Кросс-валидация вариограммы (leave-one-out)
# ===========================================================================
class CrossValidationAlgorithm(QgsProcessingAlgorithm):
    INPUT, ZFIELD = "INPUT", "ZFIELD"
    KTYPE, SKMEAN, NUGGET = "KTYPE", "SKMEAN", "NUGGET"
    RADIUS, MIN_POINTS, MAX_POINTS = "RADIUS", "MIN_POINTS", "MAX_POINTS"
    VAL_PCT, VAL_MIN, VAL_MAX, VAL_CAP = "VAL_PCT", "VAL_MIN", "VAL_MAX", "VAL_CAP"
    IDFIELD = "IDFIELD"
    DETREND, DETREND_DEG = "DETREND", "DETREND_DEG"
    PROFILE = "PROFILE"
    OUTPUT = "OUTPUT"
    OUTPUT_HTML = "OUTPUT_HTML"
    SAVE_PROFILE = "SAVE_PROFILE"

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def name(self):
        return "crossvalidation"

    def displayName(self):
        return self.tr("1.5 Кросс-валидация вариограммы")

    def group(self): return self.tr(GROUP)

    def groupId(self):
        return "grid_isolines"

    def shortHelpString(self):
        return _help_version(self.tr(
            "Скользящий контроль (leave-one-out): каждая скважина по очереди "
            "исключается, её значение предсказывается кригингом по остальным, "
            "и сравнивается с фактическим. Помогает подобрать вариограмму "
            "(наггет, радиус, модель) по ошибке, а не на глаз.\n\n"
            "В Журнал выводятся метрики: ME (смещение, к 0), RMSE (меньше - "
            "лучше), MSDR (к 1 - вариограмма адекватна по масштабу), R. "
            "Перебирайте параметры и сравнивайте RMSE и MSDR.\n\n"
            "Слой остатков (опц.) - точки со следующими полями:\n"
            "  • <номер скважины> - если задано «Поле номера скважины»;\n"
            "  • <имя проверяемого поля> - фактическое значение (факт);\n"
            "  • z_est - оценка кригинга по остальным точкам (LOO);\n"
            "  • error - оценка минус факт (минус: занижено, плюс: завышено);\n"
            "  • abs_error - модуль ошибки;\n"
            "  • std_resid - стандартизованный остаток: error / стандартную "
            "ошибку кригинга, со знаком (это не дисперсия).\n"
            "По нему видно, где модель промахивается.\n\n"
            "HTML-отчёт (по умолчанию) открывается в просмотрщике результатов: "
            "интерактивный график «оценка vs факт», гистограмма ошибок и "
            "таблица метрик."))

    def createInstance(self):
        return CrossValidationAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точки со значениями"),
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения Z"), parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric))
        self.addParameter(QgsProcessingParameterField(
            self.IDFIELD, self.tr("Поле номера скважины (необязательно)"),
            parentLayerParameterName=self.INPUT, optional=True))
        _add_cv_params(self)
        self.addParameter(QgsProcessingParameterBoolean(
            self.DETREND, self.tr("Снять полиномиальный тренд"),
            defaultValue=_dv(self, self.DETREND, False)))
        deg = QgsProcessingParameterEnum(
            self.DETREND_DEG, self.tr("Степень тренда"),
            options=[self.tr("1 (плоскость)"), self.tr("2 (квадратичная)")],
            defaultValue=_dv(self, self.DETREND_DEG, 0))
        deg.setHelp(self.tr(
            "Региональный тренд снимается МНК перед кригингом, кригуются остатки, "
            "тренд возвращается к оценке. Полезно для отметок пласта и мощностей "
            "с общим падением. Для химии без тренда эффекта почти нет. Степень 1 "
            "обычно достаточна, степень 2 может вобрать часть реальной структуры "
            "в тренд - следите за вариограммой остатков."))
        self.addParameter(deg)
        self.addParameter(_profile_enum(
            self.PROFILE, _tr("Загрузить профиль обработки"), alg=self))
        self.addParameter(QgsProcessingParameterString(
            self.SAVE_PROFILE,
            self.tr("Сохранить профиль под именем (пусто = не сохранять)"),
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Слой остатков (точки)"),
            type=QgsProcessing.TypeVectorPoint, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, self.tr("Отчёт о кросс-валидации (HTML)"),
            self.tr("HTML files (*.html)"), optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        parameters = _apply_profile(self, parameters, context, feedback)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        src = layer.name() if layer is not None else "data"
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        idfield = self.parameterAsString(parameters, self.IDFIELD, context) or None

        def _opt(name):
            v = parameters.get(name, None)
            if v is None or v == "":
                return None
            return self.parameterAsDouble(parameters, name, context)
        pct = self.parameterAsDouble(parameters, self.VAL_PCT, context)
        cap = self.parameterAsBool(parameters, self.VAL_CAP, context)
        xd, yd, vrd, ids = _read_points(
            source, zfield, feedback,
            vmin=_opt(self.VAL_MIN), vmax=_opt(self.VAL_MAX),
            pct=pct, cap=cap, id_field=idfield, return_ids=True)
        _warn_data(feedback, xd, yd, vrd)

        ktype = 1 if self.parameterAsEnum(parameters, self.KTYPE, context) == 0 else 0
        skmean = self.parameterAsDouble(parameters, self.SKMEAN, context)
        nugget = self.parameterAsDouble(parameters, self.NUGGET, context)
        radius = self.parameterAsDouble(parameters, self.RADIUS, context)
        ndmin = self.parameterAsInt(parameters, self.MIN_POINTS, context)
        ndmax = self.parameterAsInt(parameters, self.MAX_POINTS, context)
        width = float(xd.max() - xd.min()); height = float(yd.max() - yd.min())
        auto_range = max(width, height) / 3.0 or 1.0
        if radius <= 0:
            radius = math.hypot(width, height) or 1e12
        rad2 = radius * radius
        vg = _build_variogram(self, parameters, context, nugget, auto_range, feedback)
        # при заданном имени сохраняем проверенную модель как профиль
        pname = self.parameterAsString(parameters, self.SAVE_PROFILE, context)
        if pname and pname.strip():
            _s1_range = self.parameterAsDouble(parameters, _sk(1, "RANGE"), context)
            if _s1_range <= 0:
                _s1_range = auto_range
            _anis = self.parameterAsDouble(parameters, _sk(1, "ANIS"), context)
            prof = _make_profile(
                nugget,
                self.parameterAsEnum(parameters, _sk(1, "MODEL"), context),
                self.parameterAsDouble(parameters, _sk(1, "SILL"), context),
                _s1_range,
                azimuth=self.parameterAsDouble(parameters, _sk(1, "AZIMUTH"), context),
                anis=(_anis if _anis > 0 else 1.0),
                val_pct=pct, val_min=_opt(self.VAL_MIN),
                val_max=_opt(self.VAL_MAX), val_cap=cap)
            if _save_profile(pname, prof):
                feedback.pushInfo(
                    _tr("Профиль «%s» сохранён: проверенная модель Структуры 1 "
                    "(с анизотропией, если задана) + отсев.") % pname.strip())
        nodata = -9999.0

        dvar = float(np.var(vrd))
        feedback.pushInfo(
            _tr("Дисперсия данных: %.4g. Ориентир: суммарный силл (C0 + вклады C) "
            "задавайте близким к ней. Наггет и силл - в абсолютных единицах "
            "дисперсии, не 0-1.") % dvar)
        feedback.pushInfo(_tr("Кросс-валидация по %d точкам…") % len(xd))

        def prog(done, total):
            if feedback.isCanceled():
                raise QgsProcessingException(_tr("Прервано пользователем."))
            feedback.setProgress(int(95.0 * done / total))

        detrend = self.parameterAsBool(parameters, self.DETREND, context)
        if detrend:
            degree = self.parameterAsEnum(parameters, self.DETREND_DEG, context) + 1
            need = PolyTrend.n_terms(degree)
            if len(vrd) <= need:
                feedback.pushWarning(
                    _tr("Снятие тренда отключено: точек %d, для степени %d нужно "
                    "больше %d.") % (len(vrd), degree, need))
                detrend = False
            else:
                var0 = float(np.var(vrd))
                _r = PolyTrend.fit(xd, yd, vrd, degree).residuals(xd, yd, vrd)
                share = 100.0 * (1.0 - float(np.var(_r)) / var0) if var0 > 0 else 0.0
                feedback.pushInfo(
                    _tr("Снят тренд степени %d: убрано %.1f%% дисперсии. Тренд "
                    "переподбирается на каждом шаге LOO по остальным точкам, "
                    "вариограмму задавайте по остаткам.") % (degree, share))

        if detrend:
            est, var = cross_validate_detrend(
                xd, yd, vrd, degree, vg, ktype, skmean, ndmin, ndmax,
                rad2, nodata, progress=prog)
        else:
            est, var = cross_validate(xd, yd, vrd, vg, ktype, skmean, ndmin,
                                      ndmax, rad2, nodata, progress=prog)

        ok = est != nodata
        nvalid = int(ok.sum())
        if nvalid < 2:
            raise QgsProcessingException(_tr("Слишком мало оценённых точек."))
        err = est[ok] - vrd[ok]
        me = float(np.mean(err))
        mae = float(np.mean(np.abs(err)))
        rmse = float(np.sqrt(np.mean(err ** 2)))
        sd = np.sqrt(np.maximum(var[ok], 0.0))
        good = sd > 0
        msdr = float(np.mean((err[good] / sd[good]) ** 2)) if good.any() else float("nan")
        try:
            r = float(np.corrcoef(est[ok], vrd[ok])[0, 1])
        except Exception:
            r = float("nan")

        feedback.pushInfo(_tr("== Кросс-валидация (leave-one-out) =="))
        feedback.pushInfo(_tr("Точек оценено: %d из %d") % (nvalid, len(xd)))
        feedback.pushInfo(_tr("ME (смещение):   %+.4g   (ближе к 0 - лучше)") % me)
        feedback.pushInfo("MAE:             %.4g" % mae)
        feedback.pushInfo(_tr("RMSE:            %.4g   (меньше - лучше)") % rmse)
        feedback.pushInfo(_tr("MSDR:            %.3f   (ближе к 1 - лучше)") % msdr)
        feedback.pushInfo(_tr("R (оценка/факт): %.3f") % r)
        advice = _cv_advice(me, mae, rmse, msdr, r)
        for a in advice:
            feedback.pushInfo("• " + a)

        # слой остатков: колонка факта названа по проверяемому полю,
        # std_resid - стандартизованный остаток (оценка-факт)/ст.ошибка, со знаком.
        # псевдонимы полей ставим после загрузки (см. _set_field_aliases) -
        # alias/comment на уровне провайдера несовместимы с memory-слоями
        valname = _san(zfield) or "z"
        idname = (_san(idfield) or "well_id") if idfield else None
        fields = QgsFields()
        aliases = {}
        if idname:
            fields.append(QgsField(idname, QVariant.String))
            aliases[idname] = _tr("Номер скважины")
        fields.append(QgsField(valname, QVariant.Double))
        aliases[valname] = _tr("Факт (%s)") % zfield
        fields.append(QgsField("z_est", QVariant.Double))
        aliases["z_est"] = _tr("Оценка кригинга (LOO)")
        fields.append(QgsField("error", QVariant.Double))
        aliases["error"] = _tr("Ошибка (оценка − факт)")
        fields.append(QgsField("abs_error", QVariant.Double))
        aliases["abs_error"] = _tr("|Ошибка|")
        fields.append(QgsField("std_resid", QVariant.Double))
        aliases["std_resid"] = _tr("Станд. остаток (со знаком)")
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Point, source.sourceCrs())
        if sink is not None:
            for i in np.where(ok)[0]:
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(float(xd[i]), float(yd[i]))))
                e = float(est[i] - vrd[i])
                s = float(np.sqrt(max(var[i], 0.0)))
                attrs = []
                if idname:
                    attrs.append(None if ids[i] is None else str(ids[i]))
                attrs += [float(vrd[i]), float(est[i]), e, abs(e),
                          (e / s) if s > 0 else None]
                f.setAttributes(attrs)
                sink.addFeature(f)
        _set_output_name(context, dest,
                         _tr("Остатки CV %s · %s") % (zfield, _short(src)))
        _set_field_aliases(context, dest, aliases)

        # HTML-отчёт (интерактивный график + метрики)
        html_path = self.parameterAsFileOutput(
            parameters, self.OUTPUT_HTML, context)
        results = {self.OUTPUT: dest}
        if html_path:
            metrics = [
                (_tr("ME (смещение)"), "%+.4g" % me, _tr("ближе к 0 - лучше")),
                ("MAE", "%.4g" % mae, _tr("средняя |ошибка|")),
                ("RMSE", "%.4g" % rmse, _tr("меньше - лучше")),
                ("MSDR", "%.3f" % msdr, _tr("ближе к 1 - лучше")),
                (_tr("R (оценка/факт)"), "%.3f" % r, _tr("корреляция")),
                (_tr("Дисперсия данных"), "%.4g" % dvar,
                 _tr("суммарный силл (C0 + вклады C) ≈ дисперсии данных")),
                (_tr("Точек оценено"), "%d" % nvalid, _tr("из %d") % len(xd)),
            ]
            title = _tr("Кросс-валидация %s · %s") % (zfield, _short(src))
            ok_idx = np.where(ok)[0]
            ids_ok = ([ids[i] for i in ok_idx]
                      if (idfield and ids is not None) else None)
            used_params = _cv_used_params(self, parameters, context)
            try:
                _write_cv_report(html_path, title, metrics, advice,
                                 vrd[ok], est[ok], err, ids_ok,
                                 used_params, feedback)
                results[self.OUTPUT_HTML] = html_path
            except Exception as e:
                feedback.pushInfo(_tr("Не удалось записать HTML-отчёт: %s") % e)
        feedback.setProgress(100)
        return results


def _demo_field(rng, G, w):
    """Коррелированное поле GxG: белый шум, сглаженный скользящим средним
    (окно 2w+1, три прохода ≈ гауссово ядро). Края корректируются делением
    на число валидных отсчётов. Возвращает поле со средним 0 и ст.откл. 1."""
    f = rng.standard_normal((G, G))
    k = np.ones(2 * w + 1)
    d = np.convolve(np.ones(G), k, "same")
    for _ in range(3):
        f = np.apply_along_axis(lambda m: np.convolve(m, k, "same"), 0, f) / d[:, None]
        f = np.apply_along_axis(lambda m: np.convolve(m, k, "same"), 1, f) / d[None, :]
    s = f.std() or 1.0
    return (f - f.mean()) / s


def _demo_sample(f, xs, ys, xmin, xmax, ymin, ymax):
    """Билинейная выборка значений поля f в точках (xs, ys)."""
    G = f.shape[0]
    fx = (xs - xmin) / ((xmax - xmin) or 1.0) * (G - 1)
    fy = (ys - ymin) / ((ymax - ymin) or 1.0) * (G - 1)
    x0 = np.clip(np.floor(fx).astype(int), 0, G - 1); x1 = np.clip(x0 + 1, 0, G - 1)
    y0 = np.clip(np.floor(fy).astype(int), 0, G - 1); y1 = np.clip(y0 + 1, 0, G - 1)
    tx = fx - x0; ty = fy - y0
    v0 = f[y0, x0] * (1 - tx) + f[y0, x1] * tx
    v1 = f[y1, x0] * (1 - tx) + f[y1, x1] * tx
    return v0 * (1 - ty) + v1 * ty


def _demo_values(rng, G, w, xs, ys, ext, vmin, vmax, nf):
    """Сгенерировать значения в [vmin, vmax] с пространственной структурой:
    сглаженное поле (структура) + независимый шум (наггет долей nf дисперсии)."""
    field = _demo_field(rng, G, w)
    s = _demo_sample(field, xs, ys, ext[0], ext[1], ext[2], ext[3])
    mean = 0.5 * (vmin + vmax)
    tstd = 0.25 * (vmax - vmin)
    struct = s * (np.sqrt(max(1.0 - nf, 0.0)) * tstd)
    noise = rng.standard_normal(len(xs)) * (np.sqrt(max(nf, 0.0)) * tstd)
    return np.clip(mean + struct + noise, vmin, vmax)


class ExampleWellsAlgorithm(QgsProcessingAlgorithm):
    """Генерация демонстрационного набора скважин со значением абстрактного
    компонента, имеющим заданную пространственную структуру. Подходит для
    обучения и проверки кригинга/изолиний/кросс-валидации без реальных данных."""
    EXTENT = "EXTENT"
    N_POINTS = "N_POINTS"
    VMIN = "VMIN"
    VMAX = "VMAX"
    ROOF_MIN = "ROOF_MIN"
    ROOF_MAX = "ROOF_MAX"
    THICK_MIN = "THICK_MIN"
    THICK_MAX = "THICK_MAX"
    SMOOTH = "SMOOTH"
    NUGGET_FRAC = "NUGGET_FRAC"
    MINTYPE = "MINTYPE"
    HEAD = "HEAD"
    HYDRO = "HYDRO"
    SEED = "SEED"
    OUTPUT = "OUTPUT"
    OUTPUT_DRIFT = "OUTPUT_DRIFT"

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def name(self):
        return "examplewells"

    def displayName(self):
        return self.tr("1.6 Создать пример скважин (демо)")

    def group(self): return self.tr(GROUP)

    def groupId(self):
        return "grid_isolines"

    def shortHelpString(self):
        return _help_version(self.tr(
            "Создаёт точечный слой «скважин» со случайными координатами в "
            "пределах области и значением абстрактного компонента (X, %), "
            "имеющим пространственную структуру. Предназначен для обучения и "
            "проверки инструментов без реальных данных.\n\n"
            "Область задаётся экстентом (можно по слою, по холсту карты, "
            "вручную координатами или рисованием). «Гладкость» задаёт радиус "
            "корреляции как долю охвата (больше - крупнее «пятна»). «Доля "
            "наггета» задаёт долю дисперсии, приходящуюся на короткомасштабный "
            "шум (чем больше, тем меньше предсказуемость). В Журнал выводится "
            "стартовая вариограмма - её уточняют кросс-валидацией.\n\n"
            "Поля результата: номер скважины, абсолютная отметка кровли (roof), "
            "мощность (thick) и содержание X. Диапазоны кровли и мощности по "
            "умолчанию близки к реальным калийным данным; их можно изменить в "
            "разделе «Дополнительно».\n\nНеобязательные галки добавляют поля для "
            "смежных инструментов: напор (head) для градиента потока и "
            "категориальный минтип для индикаторного кригинга. Галка K и T "
            "добавляет напор и лог-нормальные поля K (коэф. фильтрации) и "
            "T = K·мощность для «Удельного расхода (Дарси)». Включённый вывод "
            "«Поверхность дрейфа» даёт растр сторонней поверхности и поле dz, "
            "линейно с ней связанное, для кригинга с внешним дрейфом."))

    def createInstance(self):
        return ExampleWellsAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Область (экстент)")))
        self.addParameter(QgsProcessingParameterNumber(
            self.N_POINTS, self.tr("Число скважин"),
            QgsProcessingParameterNumber.Integer, defaultValue=300,
            minValue=5, maxValue=200000))
        self.addParameter(QgsProcessingParameterNumber(
            self.VMIN, self.tr("Минимум значения X"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.VMAX, self.tr("Максимум значения X"),
            QgsProcessingParameterNumber.Double, defaultValue=50.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH, self.tr("Гладкость (доля охвата)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.15,
            minValue=0.02, maxValue=0.6))
        for key, label, dv in (
                (self.ROOF_MIN, _tr("Кровля: минимум, м (абс.)"), -250.0),
                (self.ROOF_MAX, _tr("Кровля: максимум, м (абс.)"), -50.0),
                (self.THICK_MIN, _tr("Мощность: минимум, м"), 1.0),
                (self.THICK_MAX, _tr("Мощность: максимум, м"), 8.0)):
            p = QgsProcessingParameterNumber(
                key, self.tr(label), QgsProcessingParameterNumber.Double,
                defaultValue=dv)
            _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterNumber(
            self.NUGGET_FRAC, self.tr("Доля наггета (от дисперсии)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.35,
            minValue=0.0, maxValue=0.7)
        _advanced(p); self.addParameter(p)
        self.addParameter(QgsProcessingParameterBoolean(
            self.MINTYPE,
            self.tr("Добавить категориальное поле минтипа (демо замещения)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.HEAD,
            self.tr("Добавить поле напора (для градиента потока)"),
            defaultValue=False))
        self.addParameter(QgsProcessingParameterBoolean(
            self.HYDRO,
            self.tr("Добавить поля K и T и напор (для удельного расхода)"),
            defaultValue=False))
        p = QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно ГСЧ (0 = случайно)"),
            QgsProcessingParameterNumber.Integer, defaultValue=0, minValue=0)
        _advanced(p); self.addParameter(p)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Скважины (демо)"),
            type=QgsProcessing.TypeVectorPoint))
        dr = QgsProcessingParameterRasterDestination(
            self.OUTPUT_DRIFT,
            self.tr("Поверхность дрейфа (растр) + поле dz, для внешнего дрейфа"),
            optional=True, createByDefault=False)
        dr.setHelp(self.tr(
            "Включите этот вывод, чтобы получить пару для кригинга с внешним "
            "дрейфом: растр гладкой сторонней поверхности s (известна всюду) и "
            "поле dz скважин, линейно с ней связанное. Запустите «Кригинг с "
            "внешним дрейфом» по полю dz с этим растром как дрейфом. Если вывод "
            "пропущен, поле dz не добавляется. По умолчанию выключено."))
        self.addParameter(dr)

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if rect.isEmpty() or rect.width() <= 0 or rect.height() <= 0:
            raise QgsProcessingException(_tr("Не задана корректная область (экстент)."))
        n = self.parameterAsInt(parameters, self.N_POINTS, context)
        vmin = self.parameterAsDouble(parameters, self.VMIN, context)
        vmax = self.parameterAsDouble(parameters, self.VMAX, context)
        if vmax <= vmin:
            raise QgsProcessingException(_tr("Максимум значения должен быть больше минимума."))
        smooth = self.parameterAsDouble(parameters, self.SMOOTH, context)
        nug = self.parameterAsDouble(parameters, self.NUGGET_FRAC, context)
        seed = self.parameterAsInt(parameters, self.SEED, context)

        rng = np.random.default_rng(seed if seed > 0 else None)
        xmin, xmax = rect.xMinimum(), rect.xMaximum()
        ymin, ymax = rect.yMinimum(), rect.yMaximum()
        ext = (xmin, xmax, ymin, ymax)

        roof_min = self.parameterAsDouble(parameters, self.ROOF_MIN, context)
        roof_max = self.parameterAsDouble(parameters, self.ROOF_MAX, context)
        thick_min = self.parameterAsDouble(parameters, self.THICK_MIN, context)
        thick_max = self.parameterAsDouble(parameters, self.THICK_MAX, context)

        G = 200
        w = max(1, int(round(smooth * G)))
        xs = rng.uniform(xmin, xmax, n)
        ys = rng.uniform(ymin, ymax, n)
        # три независимых поля: содержание X (наггет nug), кровля (гладкая
        # поверхность, малый наггет), мощность (умеренный наггет)
        valsX = _demo_values(rng, G, w, xs, ys, ext, vmin, vmax, nug)
        roof = _demo_values(rng, G, w, xs, ys, ext,
                            min(roof_min, roof_max), max(roof_min, roof_max), 0.05)
        thick = _demo_values(rng, G, w, xs, ys, ext,
                             min(thick_min, thick_max), max(thick_min, thick_max),
                             min(nug * 0.6, 0.5))

        want_mt = self.parameterAsBool(parameters, self.MINTYPE, context)
        mt = None
        if want_mt:
            # скрытое поле «замещения» 0-1 -> пороги в три класса минтипа,
            # сильвинит остаётся фоном (по образцу БКПРУ-4)
            repl = _demo_values(rng, G, w, xs, ys, ext, 0.0, 1.0, 0.12)
            mt = np.where(repl > 0.72, "Каменная соль замещения",
                 np.where(repl > 0.55, "Частичное замещение кс", "Сильвинит"))

        want_hydro = self.parameterAsBool(parameters, self.HYDRO, context)
        want_head = self.parameterAsBool(parameters, self.HEAD, context) \
            or want_hydro
        head = None
        if want_head:
            # напор: выраженный региональный уклон (поток вниз по нему) плюс
            # мягкая локальная вариация. Уклон в случайном направлении, перепад
            # ~20 м на охват - после кригинга поток идёт осмысленно.
            ang = rng.uniform(0.0, 2.0 * np.pi)
            ux, uy = math.cos(ang), math.sin(ang)
            proj = (xs - xmin) * ux + (ys - ymin) * uy
            lo, hi = float(proj.min()), float(proj.max())
            proj = (proj - lo) / ((hi - lo) or 1.0)
            head = 100.0 + 20.0 * (1.0 - proj) + \
                _demo_values(rng, G, w, xs, ys, ext, -2.0, 2.0, 0.05)

        kK = kT = None
        if want_hydro:
            # коэффициент фильтрации K лог-нормален (разброс на порядки, как в
            # реальных откачках). Поле ln(K) нормируется и подрезается до ±2.5σ,
            # иначе пики гладкого поля после exp дают неестественные выбросы.
            # Диапазон под ВКМКС: ln(K) ~ -1.8 ± 1.3·z -> K ~ 0.006…4 м/сут.
            # Водопроводимость физически согласована: T = K · мощность.
            kf = _demo_field(rng, G, max(1, int(round(min(max(smooth*1.2,0.06),0.5)*G))))
            z_at = _demo_sample(kf, xs, ys, xmin, xmax, ymin, ymax)
            z_at = (z_at - float(z_at.mean())) / (float(z_at.std()) or 1.0)
            z_at = np.clip(z_at, -2.5, 2.5)
            kK = np.exp(-1.8 + 1.3 * z_at)
            kT = kK * np.maximum(thick, 0.1)

        drift_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_DRIFT, context)
        want_drift = bool(drift_path)
        drift_grid = None
        dz = None
        if want_drift:
            # сторонняя структурная поверхность s(x,y) (соседний/подстилающий
            # пласт): гладкая, крупные пятна, без шума - известна всюду. Поле dz
            # скважин линейно связано с s (кровля целевого пласта повторяет
            # подстилающую поверхность) плюс мягкая локальная структура. На этой
            # паре «растр s + поле dz» показывают «Кригинг с внешним дрейфом».
            wd = max(1, int(round(min(max(smooth * 1.4, 0.08), 0.6) * G)))
            sfield = _demo_field(rng, G, wd)        # GxG, среднее 0, ст.откл. 1
            s_lo, s_hi = -320.0, -120.0
            drift_grid = 0.5 * (s_lo + s_hi) + sfield * (0.25 * (s_hi - s_lo))
            s_at_well = _demo_sample(drift_grid, xs, ys, xmin, xmax, ymin, ymax)
            local = _demo_values(rng, G, w, xs, ys, ext, -4.0, 4.0, 0.12)
            dz = 30.0 + 0.9 * s_at_well + local

        fields = QgsFields()
        fields.append(QgsField("well", QVariant.String))
        fields.append(QgsField("roof", QVariant.Double))
        fields.append(QgsField("thick", QVariant.Double))
        fields.append(QgsField("X", QVariant.Double))
        if want_head:
            fields.append(QgsField("head", QVariant.Double))
        if want_hydro:
            fields.append(QgsField("K", QVariant.Double))
            fields.append(QgsField("T", QVariant.Double))
        if want_mt:
            fields.append(QgsField("mintype", QVariant.String))
        if want_drift:
            fields.append(QgsField("dz", QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Point, crs)
        for i in range(n):
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(float(xs[i]), float(ys[i]))))
            attrs = ["SK-%04d" % (i + 1), float(roof[i]),
                     float(thick[i]), float(valsX[i])]
            if want_head:
                attrs.append(float(head[i]))
            if want_hydro:
                attrs.append(float(kK[i]))
                attrs.append(float(kT[i]))
            if want_mt:
                attrs.append(str(mt[i]))
            if want_drift:
                attrs.append(float(dz[i]))
            f.setAttributes(attrs)
            sink.addFeature(f)

        rng_m = 2.0 * smooth * max(xmax - xmin, ymax - ymin)
        var = float(np.var(valsX))
        feedback.pushInfo(
            _tr("Сгенерировано скважин: %d. Поля: кровля (roof), мощность (thick), "
            "содержание X. Дисперсия X ≈ %.4g.") % (n, var))
        feedback.pushInfo(
            _tr("Стартовая вариограмма для X (кригинг/кросс-валидация): суммарный "
            "силл ≈ %.4g, наггет C0 ≈ %.4g, радиус ≈ %.4g (в единицах "
            "координат). Уточните наггет по кросс-валидации до MSDR ≈ 1.") %
            (var, nug * var, rng_m))
        if want_mt:
            from collections import Counter
            cc = Counter(mt.tolist())
            feedback.pushInfo(_tr("Поле mintype (демо): ") + ", ".join(
                "%s=%d" % (k, v) for k, v in cc.items()))
        if want_head:
            feedback.pushInfo(_tr(
                "Поле напора (head): региональный уклон + локальная вариация. "
                "Кригуйте head, затем подайте растр в «Гидравлический градиент "
                "и направление потока»."))
        if want_hydro:
            feedback.pushInfo(_tr(
                "Поля K и T (демо): K лог-нормально (K ≈ %.4g…%.4g м/сут), "
                "T = K·мощность. Для удельного расхода создайте калькулятором "
                "поля ln(K) и ln(T), кригуйте их, а при подаче в «Удельный "
                "расход (Дарси)» включите галку «Растры заданы как ln». Напор "
                "(head) кригуйте как обычно.")
                % (float(kK.min()), float(kK.max())))
        results = {self.OUTPUT: dest}
        if want_drift:
            # растр пишется так, что центры пикселей совпадают с узлами
            # _demo_sample (G узлов от края до края), поэтому выборка дрейфа в
            # скважинах инструментом внешнего дрейфа воспроизводит s_at_well.
            cellx = (xmax - xmin) / (G - 1)
            celly = (ymax - ymin) / (G - 1)
            geotr = (xmin - 0.5 * cellx, cellx, 0.0,
                     ymax + 0.5 * celly, 0.0, -celly)
            crs_wkt = crs.toWkt() if (crs is not None and crs.isValid()) else None
            _write_grid_tiff(drift_path, np.flipud(drift_grid).astype(np.float32),
                             geotr, crs_wkt, -9999.0, G, G)
            _set_output_name(context, drift_path, _tr("Поверхность дрейфа (демо)"))
            results[self.OUTPUT_DRIFT] = drift_path
            feedback.pushInfo(_tr(
                "Поверхность дрейфа (растр) и поле dz: dz линейно связано с "
                "поверхностью. Запустите «Кригинг с внешним дрейфом» по полю dz "
                "с этим растром как дрейфом - сравните с обычным «2D Kriging» "
                "по dz без дрейфа."))
        _set_output_name(context, dest, _tr("Скважины (демо)"))
        # псевдонимы полей на демо-слое не ставим: этот слой создан, чтобы
        # подавать его в кригинг/кросс-валидацию, а псевдонимы на временном
        # слое вызывают предупреждения «не совместимы с временными слоями»
        # при дальнейшей обработке. Имена полей (well, roof, thick, X) понятны.
        feedback.setProgress(100)
        return results


def _add_model_params(alg):
    """Параметры заданной модели вариограммы (наггет + структуры) для
    наложения на экспериментальную. Имена ключей те же, что у кригинга и
    кросс-валидации (S1_MODEL и т.д.), но настройки хранятся по каждому
    алгоритму отдельно и между инструментами автоматически не переносятся.
    Перенос подобранной модели в «2D Kriging» делается осознанно - через
    именованный профиль обработки."""
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.NUGGET, _tr("Модель: наггет C0"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.NUGGET, 0.0), minValue=0.0)))
    for i in range(1, NSTRUCT + 1):
        tag = _tr("Структура %d") % i
        default_sill = 1.0 if i == 1 else 0.0
        off = _tr("порог/вклад C") if i == 1 else _tr("порог/вклад C (0 = выкл.)")
        alg.addParameter(_advanced(QgsProcessingParameterEnum(
            _sk(i, "MODEL"), "%s · %s" % (tag, _tr("модель")),
            options=[_tr(x) for x in MODEL_LABELS], defaultValue=_dv(alg, _sk(i, "MODEL"), 0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "SILL"), "%s · %s" % (tag, off),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "SILL"), default_sill), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "RANGE"), "%s · %s" % (tag, _tr("радиус корреляции a (0=авто)")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "RANGE"), 0.0), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "AZIMUTH"), "%s · %s" % (tag, _tr("азимут, °")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "AZIMUTH"), 0.0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "ANIS"), "%s · %s" % (tag, _tr("анизотропия (малая/главная)")),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(alg, _sk(i, "ANIS"), 1.0), minValue=EPS)))


# палитра для серий (группы): зелёный как основной цвет плагина + контрастные
_VG_COLORS = ["#1f6f54", "#c0552b", "#345b9c", "#9c7a1f", "#7d4a8c",
              "#2f8f8f", "#a23b5e", "#5c6b2f"]


def _fit_advice(fit, data_var, maxlag=None):
    """Короткие рекомендации по подобранной модели."""
    tips = []
    if not fit:
        return [_tr("Точек экспериментальной вариограммы мало для подбора. "
                "Увеличьте число лагов или максимальное расстояние.")]
    name = MODEL_LABELS[fit["model"]].lower()
    total = fit["nugget"] + fit["sill"]
    tips.append(_tr("Рекомендация: модель %s, наггет C0=%.4g, вклад C=%.4g "
                "(сумма %.4g), радиус a=%.4g. Качество подгонки R²=%.3f.")
                % (name, fit["nugget"], fit["sill"], total,
                   fit["range"], fit["r2"]))
    # радиус у края окна: модель не вышла на плато, порог экстраполирован
    edge = bool(maxlag and maxlag > 0 and fit["range"] >= 0.9 * maxlag)
    if data_var and data_var > 0:
        rel = total / data_var
        if rel < 0.6:
            msg = (_tr("Суммарный порог заметно ниже дисперсии данных (%.4g): "
                   "вариограмма не вышла на плато - увеличьте максимальное "
                   "расстояние, возможен тренд или вторая структура.") % data_var)
            if edge:
                msg += (_tr(" Радиус подбора (%.4g) достигает края окна (%.4g), "
                        "это подтверждает: кривая ещё растёт.")
                        % (fit["range"], maxlag))
            tips.append(msg)
        elif rel > 1.6:
            tips.append(_tr("Суммарный порог заметно выше дисперсии данных (%.4g) - "
                        "окно, вероятно, перешагивает тренд или безрудную зону. "
                        "Уменьшите максимальное расстояние до локального "
                        "масштаба и проверьте выбросы.") % data_var)
        else:
            msg = (_tr("Суммарный порог близок к дисперсии данных (%.4g) - "
                   "масштаб правдоподобен.") % data_var)
            if edge:
                msg += (_tr(" Радиус подбора (%.4g) у края окна (%.4g) - считайте "
                        "его нижней оценкой, при сомнении увеличьте окно и "
                        "проверьте, стабилизируется ли радиус.")
                        % (fit["range"], maxlag))
            tips.append(msg)
    elif edge:
        tips.append(_tr("Радиус подбора (%.4g) достигает края окна (%.4g) - "
                    "вариограмма не вышла на плато, радиус считайте нижней "
                    "оценкой.") % (fit["range"], maxlag))
    if fit["model"] == MODEL_GAUSSIAN and fit["nugget"] < 0.05 * (total or 1.0):
        tips.append(_tr("Гауссова модель с почти нулевым наггетом численно "
                    "неустойчива (кригинг даёт «бычьи глаза», MSDR "
                    "разваливается). Задайте небольшой наггет C0."))
    tips.append(_tr("Сохраните модель в профиль (поле «Сохранить профиль под "
                "именем»), проверьте «Кросс-валидацией» и подставьте профиль "
                "в «2D Kriging»."))
    return tips


def _write_variogram_report(path, title, series, data_var, fit, model_curves,
                            advice, meta, cloud=None, feedback=None):
    """HTML-отчёт по экспериментальной вариограмме: точки по лагам (по группам,
    если задано поле), наложенная модель и подобранная кривая, линия дисперсии
    данных, облако пар (опц.). Без plotly - таблица значений и рекомендация."""
    def _meta_table(rows):
        tr = "".join("<tr><td style='color:#555'>%s</td>"
                     "<td style='text-align:right'><b>%s</b></td></tr>" % kv
                     for kv in rows)
        return ("<table style='border-collapse:collapse' cellpadding='4'>%s"
                "</table>" % tr)

    meta_box = (
        _tr("<div style='background:#f5f5f7;border:1px solid #ddd;"
        "padding:8px 14px;border-radius:6px;display:inline-block'>"
        "<b>Сводка</b><div style='margin-top:6px'>%s</div></div>")
        % _meta_table(meta))
    advice_html = ""
    if advice:
        items = "".join("<li>%s</li>" % a for a in advice)
        advice_html = (
            _tr("<div style='background:#f3f7f4;border:1px solid #cde0d6;"
            "padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'>"
            "<b>Рекомендации</b><ul style='margin:6px 0'>%s</ul></div>") % items)

    chart = ""
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        if cloud is not None and len(cloud[0]):
            fig.add_trace(go.Scattergl(
                x=cloud[0], y=cloud[1], mode="markers",
                marker=dict(size=3, color="#bbbbbb", opacity=0.25),
                name=_tr("облако пар"), hoverinfo="skip"))
        for k, s in enumerate(series):
            col = s.get("color") or _VG_COLORS[k % len(_VG_COLORS)]
            npairs = s.get("npairs")
            sizes = None
            if npairs is not None and len(npairs):
                mx = float(max(npairs)) or 1.0
                sizes = [6.0 + 8.0 * (p / mx) ** 0.5 for p in npairs]
            fig.add_trace(go.Scatter(
                x=s["lag"], y=s["gamma"], mode="markers+lines",
                marker=dict(size=sizes or 8, color=col),
                line=dict(color=col, width=1, dash="dot"),
                name=s["label"],
                customdata=(npairs if npairs is not None else None),
                hovertemplate=("h %{x:.4g}<br>γ %{y:.4g}" +
                               (_tr("<br>пар %{customdata}") if npairs is not None
                                else "") + "<extra>" + s["label"] + "</extra>")))
        if model_curves:
            for mc in model_curves:
                fig.add_trace(go.Scatter(
                    x=mc["h"], y=mc["gamma"], mode="lines",
                    line=dict(color=mc.get("color", "#cc3333"), width=2,
                              dash=mc.get("dash", "solid")),
                    name=mc["label"]))
        if data_var and data_var > 0:
            fig.add_hline(y=data_var, line=dict(color="#999999", width=1,
                          dash="dash"),
                          annotation_text=_tr("дисперсия данных"),
                          annotation_position="right")
        fig.update_xaxes(title_text=_tr("расстояние h"), rangemode="tozero")
        fig.update_yaxes(title_text=_tr("полудисперсия γ(h)"), rangemode="tozero")
        fig.update_layout(height=560, legend=dict(orientation="h"),
                          margin=dict(l=60, r=20, t=30, b=50))
        chart = fig.to_html(full_html=False, include_plotlyjs=True)
    except Exception as e:
        if feedback is not None:
            feedback.pushInfo(_tr("plotly недоступен (%s) - отчёт без графика.") % e)
        head = (_tr("<tr><th align='left'>серия</th><th>h</th><th>γ(h)</th>"
                "<th>пар</th></tr>"))
        body = ""
        for s in series:
            np_ = s.get("npairs")
            for i in range(len(s["lag"])):
                body += ("<tr><td>%s</td><td style='text-align:right'>%.4g</td>"
                         "<td style='text-align:right'>%.4g</td>"
                         "<td style='text-align:right'>%s</td></tr>" % (
                             s["label"], s["lag"][i], s["gamma"][i],
                             (np_[i] if np_ is not None else "")))
        chart = (_tr("<p><i>Интерактивный график недоступен (нет plotly). "
                 "Значения экспериментальной вариограммы:</i></p>"
                 "<table border='1' cellpadding='4' "
                 "style='border-collapse:collapse'>%s%s</table>") % (head, body))

    html = (
        "<html><head><meta charset='utf-8'><title>%s</title></head><body>"
        "<h2>%s</h2>%s%s<br>%s%s</body></html>" % (
            title, title, meta_box, advice_html, chart, _version_footer()))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


# ===========================================================================
#  4. Экспериментальная вариограмма (изотропная) + подбор модели
# ===========================================================================
class ExperimentalVariogramAlgorithm(QgsProcessingAlgorithm):
    INPUT, ZFIELD, GROUP_FIELD = "INPUT", "ZFIELD", "GROUP_FIELD"
    MIN_GROUP_PCT = "MIN_GROUP_PCT"
    N_LAGS, MAXLAG, ROBUST, SHOW_CLOUD = "N_LAGS", "MAXLAG", "ROBUST", "SHOW_CLOUD"
    FIT, FIT_MODEL = "FIT", "FIT_MODEL"
    SHOW_MODEL = "SHOW_MODEL"
    NUGGET = "NUGGET"
    VAL_PCT, VAL_MIN, VAL_MAX, VAL_CAP = "VAL_PCT", "VAL_MIN", "VAL_MAX", "VAL_CAP"
    OUTPUT, OUTPUT_HTML = "OUTPUT", "OUTPUT_HTML"
    SAVE_PROFILE = "SAVE_PROFILE"

    FIT_LABELS = [_tr("Авто (лучшая по R²)"), _tr("Сферическая"),
                  _tr("Экспоненциальная"), _tr("Гауссова")]

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def name(self): return "experimental_variogram"

    def displayName(self):
        return self.tr("1.3 Вариограмма (экспериментальная)")

    def group(self): return self.tr(GROUP)

    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Строит изотропную экспериментальную полувариограмму по "
            "точкам: облако пар усредняется по интервалам расстояния (лагам). "
            "Помогает увидеть структуру данных и подобрать вариограмму глазом, "
            "а не угадывать наггет/радиус.\n\n"
            "Поле группировки (необязательно): для каждого значения поля "
            "строится своя кривая - удобно сравнить совокупности разной "
            "плотности (поверхностная и подземная разведка) и проверить, общая "
            "ли у них структура.\n\n"
            "Подбор модели (по умолчанию) даёт наггет C0, вклад C, радиус a и "
            "модель. Сохраните их в профиль (поле «Сохранить профиль под "
            "именем») и подставьте в «2D Kriging». Можно наложить уже заданную "
            "модель, чтобы сравнить её с облаком.\n\n"
            "HTML-отчёт открывается в просмотрщике результатов: точки по лагам, "
            "модель и подобранная кривая, линия дисперсии данных. Слой-таблица "
            "(опц.) содержит лаг, γ(h) и число пар для построения в QGIS."))

    def createInstance(self):
        return ExperimentalVariogramAlgorithm()

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точки со значениями"),
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения Z"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric))
        self.addParameter(QgsProcessingParameterField(
            self.GROUP_FIELD,
            self.tr("Поле группировки (необязательно, напр. вид разведки)"),
            parentLayerParameterName=self.INPUT, optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MIN_GROUP_PCT,
            self.tr("Минимум точек в группе, % от выборки (пол 30 точек)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.MIN_GROUP_PCT, 2.0),
            minValue=0.0, maxValue=100.0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.N_LAGS, self.tr("Число лагов"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.N_LAGS, 15), minValue=3, maxValue=100))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAXLAG, self.tr("Максимальное расстояние, в единицах слоя (0 = пол-диагонали)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.MAXLAG, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FIT, self.tr("Подобрать модель (рекомендация)"),
            defaultValue=_dv(self, self.FIT, True)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.FIT_MODEL, self.tr("Модель для подбора"),
            options=[_tr(x) for x in self.FIT_LABELS], defaultValue=_dv(self, self.FIT_MODEL, 0))))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.ROBUST, self.tr("Устойчивая оценка (Кресси-Хокинса)"),
            defaultValue=_dv(self, self.ROBUST, False))))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.SHOW_CLOUD, self.tr("Показать облако пар"),
            defaultValue=_dv(self, self.SHOW_CLOUD, False))))
        # наложение заданной модели вариограммы (наггет + структуры)
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.SHOW_MODEL, self.tr("Наложить заданную модель вариограммы"),
            defaultValue=_dv(self, self.SHOW_MODEL, False))))
        _add_model_params(self)
        # отсев/срезка ураганных проб - в самом конце
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_PCT, self.tr("Ураганные пробы: перцентиль обрезки, % (0 = выкл.)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.VAL_PCT, 0.0), minValue=0.0, maxValue=49.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MIN, self.tr("Нижняя граница значения (пусто = нет)"),
            QgsProcessingParameterNumber.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MAX, self.tr("Верхняя граница значения (пусто = нет)"),
            QgsProcessingParameterNumber.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.VAL_CAP, self.tr("Срезать к границе (capping) вместо удаления"),
            defaultValue=_dv(self, self.VAL_CAP, False))))
        self.addParameter(QgsProcessingParameterString(
            self.SAVE_PROFILE,
            self.tr("Сохранить профиль под именем (пусто = не сохранять)"),
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Таблица вариограммы (лаг, γ, число пар)"),
            type=QgsProcessing.TypeVector, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, self.tr("Отчёт (HTML)"),
            self.tr("HTML files (*.html)"), optional=True, createByDefault=True))

    def _opt(self, parameters, name, context):
        v = parameters.get(name, None)
        if v is None or v == "":
            return None
        return self.parameterAsDouble(parameters, name, context)

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        source = self.parameterAsSource(parameters, self.INPUT, context)
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        src = layer.name() if layer is not None else "data"
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        gfield = self.parameterAsString(parameters, self.GROUP_FIELD, context) or None
        min_group_pct = self.parameterAsDouble(parameters, self.MIN_GROUP_PCT, context)
        n_lags = self.parameterAsInt(parameters, self.N_LAGS, context)
        maxlag = self.parameterAsDouble(parameters, self.MAXLAG, context)
        robust = self.parameterAsBool(parameters, self.ROBUST, context)
        show_cloud = self.parameterAsBool(parameters, self.SHOW_CLOUD, context)
        do_fit = self.parameterAsBool(parameters, self.FIT, context)
        fit_choice = self.parameterAsEnum(parameters, self.FIT_MODEL, context)
        show_model = self.parameterAsBool(parameters, self.SHOW_MODEL, context)
        pct = self.parameterAsDouble(parameters, self.VAL_PCT, context)
        cap = self.parameterAsBool(parameters, self.VAL_CAP, context)
        vmin = self._opt(parameters, self.VAL_MIN, context)
        vmax = self._opt(parameters, self.VAL_MAX, context)

        # читаем все точки (для общей кривой, дисперсии и облака)
        xs, ys, vs = _read_points(source, zfield, feedback,
                                  vmin=vmin, vmax=vmax, pct=pct, cap=cap)
        _warn_data(feedback, xs, ys, vs)
        data_var = float(np.var(vs))
        feedback.pushInfo(_tr("Точек: %d. Дисперсия данных: %.4g (ориентир для "
                          "суммарного порога).") % (len(xs), data_var))
        # порог размера группы: % от выборки, но не меньше 30 точек
        group_min = max(int(round(min_group_pct / 100.0 * len(xs))), 30)

        ev = experimental_variogram(xs, ys, vs, n_lags=n_lags, maxlag=maxlag,
                                     robust=robust,
                                     cloud_max=(20000 if show_cloud else 0))
        if ev["subsampled"]:
            feedback.pushInfo(_tr("Точек много - для расчёта пар использована "
                              "случайная подвыборка %d точек.") % ev["n_used"])
        if maxlag and maxlag > 0:
            W = float(xs.max() - xs.min()); H = float(ys.max() - ys.min())
            spacing = (W * H / max(len(xs), 1)) ** 0.5 if W > 0 and H > 0 else 0.0
            if spacing > 0 and maxlag < spacing:
                feedback.pushWarning(
                    _tr("Максимальное расстояние (%.4g) меньше типичного шага между "
                    "точками (~%.4g) - пар почти нет. Значение задаётся в "
                    "единицах слоя (обычно метры).") % (maxlag, spacing))
        series = [{"label": _tr("все точки"), "lag": ev["lag"], "gamma": ev["gamma"],
                   "npairs": ev["npairs"], "color": _VG_COLORS[0]}]

        # группировка: отдельная кривая на каждое значение поля
        if gfield is not None:
            gidx = source.fields().lookupField(gfield)
            vals = []
            try:
                req = QgsFeatureRequest().setSubsetOfAttributes([gidx])
                req.setFlags(QgsFeatureRequest.NoGeometry)
                seen = set()
                for f in source.getFeatures(req):
                    g = f[gidx]
                    key = "" if g is None else str(g)
                    if key not in seen:
                        seen.add(key); vals.append(g)
                    if len(seen) > 12:
                        break
            except Exception as e:
                feedback.pushInfo(_tr("Не удалось перечислить группы: %s") % e)
                vals = []
            if len(vals) > 12:
                feedback.pushWarning(_tr("Групп больше 12 - группировка пропущена."))
            elif len(vals) >= 2:
                gname = QgsExpression.quotedColumnRef(gfield)
                skipped = []
                for k, g in enumerate(vals):
                    expr = ("%s IS NULL" % gname if g is None
                            else "%s = %s" % (gname, QgsExpression.quotedString(str(g))))
                    try:
                        req = QgsFeatureRequest().setFilterExpression(expr)
                        gx, gy, gv = _read_points(source, zfield, None,
                                                  vmin=vmin, vmax=vmax, pct=pct,
                                                  cap=cap, request=req)
                    except Exception:
                        continue
                    if len(gx) < group_min:
                        skipped.append((g, len(gx)))
                        continue
                    gev = experimental_variogram(gx, gy, gv, n_lags=n_lags,
                                                 maxlag=ev["maxlag"], robust=robust,
                                                 cloud_max=0)
                    label = "%s = %s" % (gfield, "—" if g is None else g)
                    col = _VG_COLORS[(k + 1) % len(_VG_COLORS)]
                    series.append({"label": label, "lag": gev["lag"],
                                   "gamma": gev["gamma"], "npairs": gev["npairs"],
                                   "color": col})
                if skipped:
                    txt = ", ".join("%s (%d)" % (("—" if g is None else g), n)
                                    for g, n in skipped)
                    feedback.pushInfo(_tr("Группы меньше %d точек пропущены: %s.")
                                      % (group_min, txt))
            else:
                feedback.pushInfo(_tr("В поле группировки меньше 2 значений - "
                                  "строю только общую кривую."))

        # подбор модели по общей кривой (рекомендация)
        fit = None
        if do_fit:
            model_arg = "auto" if fit_choice == 0 else (fit_choice - 1)
            fit = fit_variogram(ev["lag"], ev["gamma"], ev["npairs"],
                                model=model_arg)
            if fit:
                feedback.pushInfo(
                    _tr("Подбор: модель %s, C0=%.4g, C=%.4g, a=%.4g, R²=%.3f") % (
                        MODEL_LABELS[fit["model"]], fit["nugget"], fit["sill"],
                        fit["range"], fit["r2"]))
                pname = self.parameterAsString(
                    parameters, self.SAVE_PROFILE, context)
                if pname and pname.strip():
                    prof = _make_profile(
                        fit["nugget"], fit["model"], fit["sill"], fit["range"],
                        azimuth=0.0, anis=1.0,
                        val_pct=self.parameterAsDouble(
                            parameters, self.VAL_PCT, context),
                        val_min=self._opt(parameters, self.VAL_MIN, context),
                        val_max=self._opt(parameters, self.VAL_MAX, context),
                        val_cap=self.parameterAsBool(
                            parameters, self.VAL_CAP, context))
                    if _save_profile(pname, prof):
                        feedback.pushInfo(
                            _tr("Профиль «%s» сохранён: изотропная модель из "
                            "автоподбора + текущий отсев. Анизотропию можно "
                            "задать в кросс-валидации или инструменте "
                            "«Профили».") % pname.strip())

        # наложение заданной модели
        model_curves = None
        if show_model:
            w = float(xs.max() - xs.min()); h = float(ys.max() - ys.min())
            auto_range = max(w, h) / 3.0 or 1.0
            nugget = self.parameterAsDouble(parameters, self.NUGGET, context)
            vg = _build_variogram(self, parameters, context, nugget, auto_range,
                                  feedback)
            mc = model_curve(vg, ev["maxlag"])
            model_curves = [{"label": _tr("заданная модель"), "h": mc[0],
                             "gamma": mc[1], "color": "#cc3333"}]
            if len(mc) == 3:
                model_curves.append({"label": _tr("модель (малая ось)"), "h": mc[0],
                                     "gamma": mc[2], "color": "#cc3333",
                                     "dash": "dot"})
        # кривая подобранной модели
        if fit:
            vgf = Variogram(fit["nugget"], [{
                "it": fit["model"] + 1, "cc": fit["sill"], "aa": fit["range"],
                "ang": 0.0, "anis": 1.0}])
            hf, gf = model_curve(vgf, ev["maxlag"])
            mc = {"label": _tr("подобранная модель"), "h": hf, "gamma": gf,
                  "color": "#1f6f54", "dash": "solid"}
            model_curves = (model_curves or []) + [mc]

        advice = _fit_advice(fit, data_var, ev["maxlag"]) if do_fit else []

        # таблица-слой (без геометрии): лаг, γ, число пар, группа
        results = {}
        fields = QgsFields()
        fields.append(QgsField("series", QVariant.String))
        fields.append(QgsField("lag", QVariant.Double))
        fields.append(QgsField("gamma", QVariant.Double))
        fields.append(QgsField("npairs", QVariant.Int))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.NoGeometry)
        if sink is not None:
            def _emit(label, lag, gamma, npairs):
                for i in range(len(lag)):
                    f = QgsFeature(fields)
                    f.setAttributes([label, float(lag[i]), float(gamma[i]),
                                     int(npairs[i])])
                    sink.addFeature(f)
            _emit(_tr("все точки"), ev["lag"], ev["gamma"], ev["npairs"])
            for s in series[1:]:
                _emit(s["label"], s["lag"], s["gamma"], s["npairs"])
            _set_output_name(context, dest,
                             _tr("Вариограмма %s · %s") % (zfield, _short(src)))
            results[self.OUTPUT] = dest

        # HTML-отчёт
        html_path = self.parameterAsFileOutput(parameters, self.OUTPUT_HTML,
                                               context)
        if html_path:
            meta = [(_tr("Поле Z"), zfield), (_tr("Точек"), "%d" % len(xs)),
                    (_tr("Дисперсия данных"), "%.4g" % data_var),
                    (_tr("Число лагов"), "%d" % n_lags),
                    (_tr("Максимальное расстояние"), "%.4g" % ev["maxlag"]),
                    (_tr("Оценка"), _tr("Кресси-Хокинса") if robust else _tr("Матерона"))]
            if ev["subsampled"]:
                meta.append((_tr("Подвыборка точек"), "%d" % ev["n_used"]))
            title = _tr("Вариограмма %s · %s") % (zfield, _short(src))
            cloud = ((ev["cloud_h"], ev["cloud_g"]) if show_cloud and
                     ev["cloud_h"].size else None)
            try:
                _write_variogram_report(html_path, title, series, data_var, fit,
                                        model_curves, advice, meta, cloud, feedback)
                results[self.OUTPUT_HTML] = html_path
            except Exception as e:
                feedback.pushInfo(_tr("Не удалось записать HTML-отчёт: %s") % e)

        _save_values(self, parameters)
        feedback.setProgress(100)
        return results


# Порядок в этом списке на панель Processing не влияет: тулбокс сортирует
# алгоритмы внутри группы по алфавиту отображаемого имени. Список оставлен в
# логическом порядке только для чтения кода.
class ProfilesAlgorithm(QgsProcessingAlgorithm):
    """Управление профилями обработки: показать / сохранить вручную /
    удалить / очистить. Профиль = вариограмма (Структура 1) + наггет + отсев."""
    ACTION = "ACTION"
    PROFILE = "PROFILE"
    NAME = "NAME"
    NUGGET = "NUGGET"
    MODEL = "MODEL"
    SILL = "SILL"
    RANGE = "RANGE"
    AZIMUTH = "AZIMUTH"
    ANIS = "ANIS"
    VAL_PCT, VAL_MIN, VAL_MAX, VAL_CAP = "VAL_PCT", "VAL_MIN", "VAL_MAX", "VAL_CAP"
    ACTION_LABELS = [_tr("Показать список"), _tr("Сохранить вручную (по полям ниже)"),
                     _tr("Удалить выбранный"), _tr("Очистить все")]

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def createInstance(self): return ProfilesAlgorithm()

    def name(self): return "profiles"

    def displayName(self): return self.tr("1.7 Профили обработки")

    def group(self): return self.tr(GROUP)

    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Управление профилями обработки. Профиль - это именованный набор "
            "«вариограмма (Структура 1: наггет, тип, порог, радиус, азимут, "
            "оси) + отсев ураганных проб». Профили сохраняют «Вариограмма» и "
            "«Кросс-валидация», а подставляет «2D Kriging».\n\n"
            "Действие: Показать список (в Журнал), Сохранить вручную (по полям "
            "в «Дополнительно»), Удалить выбранный, Очистить все.\n\n"
            "Списки профилей в выпадающих полях обновляются при открытии окна: "
            "сохранили профиль - переоткройте инструмент, чтобы он появился."))

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            self.ACTION, self.tr("Действие"),
            options=[_tr(x) for x in self.ACTION_LABELS], defaultValue=0))
        self.addParameter(_profile_enum(
            self.PROFILE, _tr("Профиль (для удаления / просмотра)"), pick=True))
        self.addParameter(QgsProcessingParameterString(
            self.NAME, self.tr("Имя профиля (для «Сохранить вручную»)"),
            optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.NUGGET, self.tr("Модель: наггет C0"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.MODEL, self.tr("Модель: тип"),
            options=[_tr(x) for x in MODEL_LABELS], defaultValue=0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SILL, self.tr("Модель: порог/вклад C"),
            QgsProcessingParameterNumber.Double, defaultValue=1.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.RANGE, self.tr("Модель: радиус корреляции a"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.AZIMUTH, self.tr("Модель: азимут, °"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ANIS, self.tr("Модель: анизотропия (малая/главная)"),
            QgsProcessingParameterNumber.Double, defaultValue=1.0, minValue=EPS)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_PCT, self.tr("Отсев: перцентиль обрезки, % (0 = выкл.)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0,
            minValue=0.0, maxValue=49.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MIN, self.tr("Отсев: нижняя граница (пусто = нет)"),
            QgsProcessingParameterNumber.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MAX, self.tr("Отсев: верхняя граница (пусто = нет)"),
            QgsProcessingParameterNumber.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.VAL_CAP, self.tr("Отсев: срезать к границе вместо удаления"),
            defaultValue=False)))

    def _opt(self, parameters, name, context):
        v = parameters.get(name, None)
        if v is None or v == "":
            return None
        return self.parameterAsDouble(parameters, name, context)

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        action = self.parameterAsEnum(parameters, self.ACTION, context)
        if action == 0:
            profs = _load_profiles()
            if not profs:
                feedback.pushInfo(_tr("Сохранённых профилей нет."))
            else:
                feedback.pushInfo(_tr("Сохранённые профили (%d):") % len(profs))
                for nm in sorted(profs):
                    feedback.pushInfo("  - %s: %s" % (nm, _profile_summary(profs[nm])))
        elif action == 1:
            nm = (self.parameterAsString(parameters, self.NAME, context) or "").strip()
            if not nm:
                raise QgsProcessingException(
                    _tr("Для сохранения укажите «Имя профиля»."))
            anis = self.parameterAsDouble(parameters, self.ANIS, context)
            prof = _make_profile(
                self.parameterAsDouble(parameters, self.NUGGET, context),
                self.parameterAsEnum(parameters, self.MODEL, context),
                self.parameterAsDouble(parameters, self.SILL, context),
                self.parameterAsDouble(parameters, self.RANGE, context),
                azimuth=self.parameterAsDouble(parameters, self.AZIMUTH, context),
                anis=(anis if anis > 0 else 1.0),
                val_pct=self.parameterAsDouble(parameters, self.VAL_PCT, context),
                val_min=self._opt(parameters, self.VAL_MIN, context),
                val_max=self._opt(parameters, self.VAL_MAX, context),
                val_cap=self.parameterAsBool(parameters, self.VAL_CAP, context))
            _save_profile(nm, prof)
            feedback.pushInfo(_tr("Профиль «%s» сохранён: %s") % (nm, _profile_summary(prof)))
        elif action == 2:
            idx = self.parameterAsEnum(parameters, self.PROFILE, context)
            opts = [PROFILE_NONE] + _profile_names()
            if idx <= 0 or idx >= len(opts):
                raise QgsProcessingException(
                    _tr("Выберите профиль для удаления в поле «Профиль»."))
            nm = opts[idx]
            _delete_profile(nm)
            feedback.pushInfo(_tr("Профиль «%s» удалён.") % nm)
            rest = _profile_names()
            feedback.pushInfo(_tr("Осталось профилей: %d%s") % (
                len(rest), (" - " + ", ".join(rest)) if rest else ""))
        elif action == 3:
            n = len(_load_profiles())
            _clear_profiles()
            feedback.pushInfo(_tr("Удалены все профили (%d).") % n)
        return {}


def _write_varmap_report(path, title, m, meta, advice, feedback=None):
    """HTML-отчёт вариограммной карты: хитмап γ(h_x, h_y) (равные оси) и, если
    анизотропия разрешена, эллипс и главная ось поверх. Без plotly - текст."""
    import math as _m
    meta_rows = "".join(
        "<tr><td style='color:#555'>%s</td>"
        "<td style='text-align:right'><b>%s</b></td></tr>" % kv for kv in meta)
    meta_box = (
        _tr("<div style='background:#f5f5f7;border:1px solid #ddd;padding:8px 14px;"
        "border-radius:6px;display:inline-block'><b>Сводка</b>"
        "<div style='margin-top:6px'><table cellpadding='4'>%s</table></div>"
        "</div>") % meta_rows)
    advice_html = ""
    if advice:
        items = "".join("<li>%s</li>" % a for a in advice)
        advice_html = (
            _tr("<div style='background:#f3f7f4;border:1px solid #cde0d6;"
            "padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'>"
            "<b>Что дальше</b><ul style='margin:6px 0'>%s</ul></div>") % items)

    grid = m["grid"]
    cell = m["cell"]
    n_bins = m["n_bins"]
    size = grid.shape[0]
    axis = [(j - n_bins) * cell for j in range(size)]

    chart = ""
    try:
        import plotly.graph_objects as go
        z = [[(None if (v != v) else float(v)) for v in row] for row in grid]
        fig = go.Figure()
        fig.add_trace(go.Heatmap(
            x=axis, y=axis, z=z, colorscale="Viridis",
            colorbar=dict(title="γ"),
            hovertemplate="h_x %{x:.4g}<br>h_y %{y:.4g}<br>γ %{z:.4g}"
                          "<extra></extra>"))
        if m["resolved"]:
            az = _m.radians(m["azimuth"])
            dx, dy = _m.sin(az), _m.cos(az)          # главная ось (E=x,N=y)
            mx, my = _m.cos(az), -_m.sin(az)         # перпендикуляр
            rmaj, rmin = m["range_major"], m["range_minor"]
            ex, ey = [], []
            for t in [k * _m.pi / 60.0 for k in range(121)]:
                ex.append(rmaj * _m.cos(t) * dx + rmin * _m.sin(t) * mx)
                ey.append(rmaj * _m.cos(t) * dy + rmin * _m.sin(t) * my)
            fig.add_trace(go.Scatter(
                x=ex, y=ey, mode="lines",
                line=dict(color="#ffffff", width=2), name=_tr("эллипс анизотропии")))
            fig.add_trace(go.Scatter(
                x=[-rmaj * dx, rmaj * dx], y=[-rmaj * dy, rmaj * dy],
                mode="lines", line=dict(color="#ff5555", width=2, dash="dash"),
                name=_tr("главная ось")))
        fig.update_xaxes(title_text=_tr("лаг по востоку h_x"), zeroline=True)
        fig.update_yaxes(title_text=_tr("лаг по северу h_y"), zeroline=True,
                         scaleanchor="x", scaleratio=1)
        fig.update_layout(height=640, legend=dict(orientation="h"),
                          margin=dict(l=60, r=20, t=30, b=50))
        chart = fig.to_html(full_html=False, include_plotlyjs=True)
    except Exception as e:
        if feedback is not None:
            feedback.pushInfo(_tr("plotly недоступен (%s) - отчёт без графика.") % e)
        chart = (_tr("<p><i>Интерактивный график недоступен (нет plotly). "
                 "Числовые оценки - в сводке выше.</i></p>"))

    html = ("<html><head><meta charset='utf-8'><title>%s</title></head><body>"
            "<h2>%s</h2>%s%s<br>%s%s</body></html>" % (
                title, title, meta_box, advice_html, chart, _version_footer()))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


class VariogramMapAlgorithm(QgsProcessingAlgorithm):
    INPUT, ZFIELD = "INPUT", "ZFIELD"
    N_BINS, MAXLAG, MIN_PAIRS = "N_BINS", "MAXLAG", "MIN_PAIRS"
    OUTPUT_HTML, OUTPUT_RASTER = "OUTPUT_HTML", "OUTPUT_RASTER"
    WRITE_PROFILE = "WRITE_PROFILE"

    def tr(self, s): return _tr(s)
    def helpUrl(self): return _help_url()
    def name(self): return "variogram_map"
    def displayName(self): return self.tr("1.4 Вариограммная карта (анизотропия)")
    def group(self): return self.tr(GROUP)
    def groupId(self): return GROUP_ID
    def createInstance(self): return VariogramMapAlgorithm()

    def shortHelpString(self):
        return _help_version(self.tr(
            "Строит вариограммную карту - поверхность γ(h_x, h_y): для всех пар "
            "берётся вектор разноса (dx, dy) и полудисперсия 0.5·(Δz)², значения "
            "усредняются по 2D-сетке лагов. Анизотропия видна как эллипс: "
            "направление, вдоль которого γ растёт медленнее (длиннее радиус), - "
            "ось максимальной непрерывности (для складчатости - простирание).\n\n"
            "В Журнал и в HTML-отчёт выводятся оценки: азимут главной оси "
            "(геогр., 0=С, по часовой), коэффициент анизотропии (малая/главная) "
            "и радиус. Их можно подставить в структуру вариограммы «2D Kriging» "
            "(азимут, анизотропия, радиус a) - это и есть учёт анизотропии в "
            "кригинге. Оценка индикативная: уточняйте по самому хитмапу.\n\n"
            "Если структура близка к изотропной или радиус меньше ячейки - "
            "анизотропия не оценивается (помечается «не выражена»).\n\n"
            "Опц. растр поверхности (в координатах лага, начало в 0,0) - для "
            "тех, кто хочет видеть карту на холсте.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точки со значениями"),
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения Z"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric))
        self.addParameter(QgsProcessingParameterNumber(
            self.N_BINS, self.tr("Бинов на полуось (детализация карты)"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.N_BINS, 15), minValue=5, maxValue=40))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAXLAG,
            self.tr("Макс. лаг, в единицах слоя (0 = пол-диагонали)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.MAXLAG, 0.0), minValue=0.0))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MIN_PAIRS, self.tr("Мин. число пар в ячейке"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.MIN_PAIRS, 5), minValue=1)))
        self.addParameter(_profile_enum(
            self.WRITE_PROFILE, _tr("Записать анизотропию в профиль"), pick=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, self.tr("Отчёт (HTML)"),
            self.tr("HTML files (*.html)"), optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_RASTER, self.tr("Растр поверхности (опц., в лаг-координатах)"),
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        n_bins = self.parameterAsInt(parameters, self.N_BINS, context)
        maxlag = self.parameterAsDouble(parameters, self.MAXLAG, context)
        min_pairs = self.parameterAsInt(parameters, self.MIN_PAIRS, context)

        xs, ys, vs = _read_points(src, zfield, feedback)
        _warn_data(feedback, xs, ys, vs)
        feedback.pushInfo(_tr("Вариограммная карта: %d точек…") % len(xs))
        m = variogram_map(xs, ys, vs, n_bins=n_bins,
                          maxlag=(maxlag if maxlag > 0 else None),
                          min_pairs=min_pairs)

        if m["subsampled"]:
            feedback.pushInfo(_tr("Точки прорежены до %d (для скорости).") % m["n_used"])
        if m["resolved"]:
            feedback.pushInfo(
                _tr("Анизотропия: азимут главной оси %.0f° (геогр.), "
                "коэффициент %.2f (малая/главная), радиус главной оси %.4g.")
                % (m["azimuth"], m["anis"], m["range_major"]))
            if m["range_capped"]:
                feedback.pushWarning(
                    _tr("Радиус главной оси упёрся в макс. лаг (%.4g): вдоль "
                    "простирания вариограмма на полку не вышла. Радиус - нижняя "
                    "оценка, анизотропия (%.2f) занижена по выраженности. "
                    "Увеличьте «Макс. лаг», либо это признак тренда / очень "
                    "сильной непрерывности.") % (m["maxlag"], m["anis"]))
                feedback.pushInfo(
                    _tr("В «2D Kriging» подставьте азимут=%.0f и анизотропию≈%.2f "
                    "(как ориентир); радиус a задайте больше %.4g по смыслу "
                    "данных.") % (m["azimuth"], m["anis"], m["maxlag"]))
            else:
                feedback.pushInfo(
                    _tr("Подставьте в структуру вариограммы «2D Kriging»: азимут=%.0f, "
                    "анизотропия=%.2f, радиус a=%.4g.") % (
                        m["azimuth"], m["anis"], m["range_major"]))
        else:
            feedback.pushInfo(
                _tr("Анизотропия не выражена (структура близка к изотропной или "
                "радиус меньше ячейки). Можно уменьшить макс. лаг или увеличить "
                "число бинов."))

        # Запись анизотропии в выбранный профиль (азимут, коэффициент и радиус
        # главной оси). Модель, наггет, силл и отсев в профиле сохраняются - их
        # даёт омнинаправленная вариограмма, а карта лишь дописывает геометрию.
        widx = self.parameterAsEnum(parameters, self.WRITE_PROFILE, context)
        if widx > 0:
            pname = ([PROFILE_NONE] + _profile_names())[widx] \
                if widx <= len(_profile_names()) else None
            prof = _get_profile(pname) if pname else None
            if prof is None:
                feedback.pushWarning(
                    _tr("Профиль «%s» не найден - анизотропия не сохранена. "
                    "Сначала сохраните профиль в «Вариограмме» или "
                    "«Кросс-валидации».") % pname)
            elif not m["resolved"]:
                feedback.pushWarning(
                    _tr("Анизотропия не выражена - в профиль писать нечего."))
            else:
                rng = None if m["range_capped"] else float(m["range_major"])
                _save_profile(pname, _merge_anisotropy(
                    prof, float(m["azimuth"]), float(m["anis"]), rng))
                if rng is None:
                    feedback.pushInfo(
                        _tr("В профиль «%s» записаны азимут=%.0f° и анизотропия="
                        "%.2f (радиус оставлен прежним: упёрся в макс. лаг). При "
                        "загрузке профиля они появятся в подписи.")
                        % (pname, m["azimuth"], m["anis"]))
                else:
                    feedback.pushInfo(
                        _tr("В профиль «%s» записаны азимут=%.0f°, анизотропия="
                        "%.2f, радиус a=%.4g. При загрузке профиля они появятся "
                        "в подписи.") % (pname, m["azimuth"], m["anis"], rng))

        results = {}
        src_name = _short(src.sourceName()) if hasattr(src, "sourceName") else zfield
        html_path = self.parameterAsFileOutput(parameters, self.OUTPUT_HTML,
                                               context)
        if html_path:
            meta = [(_tr("Поле Z"), zfield), (_tr("Точек"), "%d" % m["n_used"]),
                    (_tr("Дисперсия (силл)"), "%.4g" % m["sill"]),
                    (_tr("Макс. лаг"), "%.4g" % m["maxlag"]),
                    (_tr("Ячейка лага"), "%.4g" % m["cell"]),
                    (_tr("Бинов на полуось"), "%d" % m["n_bins"])]
            if m["resolved"]:
                rad_str = (_tr("≥ %.4g (упёрся в макс. лаг)") % m["range_major"]
                           if m["range_capped"] else "%.4g" % m["range_major"])
                meta += [(_tr("Азимут главной оси"), "%.0f°" % m["azimuth"]),
                         (_tr("Анизотропия (малая/главная)"), "%.2f" % m["anis"]),
                         (_tr("Радиус главной оси"), rad_str)]
            else:
                meta.append((_tr("Анизотропия"), _tr("не выражена")))
            if m["resolved"] and not m["range_capped"]:
                advice = [
                    _tr("Главная ось непрерывности ~%.0f° (геогр.). Для складчатости "
                    "это направление простирания.") % m["azimuth"],
                    _tr("В «2D Kriging» задайте: азимут=%.0f, анизотропия=%.2f, "
                    "радиус a=%.4g.") % (m["azimuth"], m["anis"], m["range_major"]),
                    _tr("Оценка индикативная - сверьте с формой хитмапа (эллипса).")]
            elif m["resolved"] and m["range_capped"]:
                advice = [
                    _tr("Главная ось непрерывности ~%.0f° (геогр.). Для складчатости "
                    "это направление простирания.") % m["azimuth"],
                    _tr("Радиус главной оси упёрся в макс. лаг (%.4g): вдоль "
                    "простирания вариограмма на полку не вышла - радиус считайте "
                    "нижней оценкой, а анизотропию (%.2f) - заниженной по "
                    "выраженности.") % (m["maxlag"], m["anis"]),
                    _tr("В «2D Kriging» задайте азимут=%.0f и анизотропию≈%.2f как "
                    "ориентир, радиус a возьмите больше %.4g по смыслу данных. "
                    "Чтобы измерить радиус - увеличьте «Макс. лаг».") % (
                        m["azimuth"], m["anis"], m["maxlag"]),
                    _tr("Если γ не выходит на полку даже при широком окне - в данных "
                    "тренд: его убирают до интерполяции либо учитывают видом "
                    "кригинга.")]
            else:
                advice = [
                    _tr("Анизотропия не разрешается на этой сетке: структура близка к "
                    "изотропной либо радиус меньше ячейки."),
                    _tr("Попробуйте уменьшить «Макс. лаг» или увеличить «Бинов на "
                    "полуось», чтобы разрешить ближнюю структуру.")]
            title = _tr("Вариограммная карта %s · %s") % (zfield, src_name)
            try:
                _write_varmap_report(html_path, title, m, meta, advice, feedback)
                results[self.OUTPUT_HTML] = html_path
            except Exception as e:
                feedback.pushInfo(_tr("Не удалось записать HTML-отчёт: %s") % e)

        rast_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_RASTER,
                                                context)
        if rast_path:
            try:
                self._write_surface_raster(rast_path, m, src, context, feedback)
                results[self.OUTPUT_RASTER] = rast_path
                _set_output_name(context, rast_path,
                                 _tr("Вариокарта · %s") % zfield)
            except Exception as e:
                feedback.pushInfo(_tr("Не удалось записать растр поверхности: %s") % e)

        _save_values(self, parameters)
        feedback.setProgress(100)
        return results

    def _write_surface_raster(self, path, m, src, context, feedback):
        from osgeo import gdal
        import numpy as np
        grid = m["grid"]
        cell = m["cell"]
        size = grid.shape[0]
        maxlag = m["maxlag"]
        nd = -9999.0
        arr = np.where(np.isfinite(grid), grid, nd).astype(np.float32)
        arr = arr[::-1, :]                       # строка 0 растра = север (верх)
        drv = gdal.GetDriverByName("GTiff")
        ds = drv.Create(path, size, size, 1, gdal.GDT_Float32)
        # начало в (0,0) в координатах лага: левый-верх = (-maxlag-cell/2, +maxlag+cell/2)
        ds.SetGeoTransform([-maxlag - cell / 2.0, cell, 0.0,
                            maxlag + cell / 2.0, 0.0, -cell])
        try:
            crs = src.sourceCrs()
            if crs is not None and crs.isValid():
                ds.SetProjection(crs.toWkt())
        except Exception:
            pass
        b = ds.GetRasterBand(1)
        b.SetNoDataValue(nd)
        b.WriteArray(arr)
        b.FlushCache()
        ds = None


# ===========================================================================
#  9. Гидравлический градиент и направление потока (гидрогеология)
# ===========================================================================
def _write_grid_tiff(path, array, geotr, crs_wkt, nodata, nx, ny):
    """Пишет одноканальный Float32 GeoTIFF с геопривязкой и nodata."""
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, nx, ny, 1, gdal.GDT_Float32,
                       options=["COMPRESS=LZW", "TILED=YES"])
    ds.SetGeoTransform(geotr)
    if crs_wkt:
        ds.SetProjection(crs_wkt)
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(array)
    band.FlushCache()
    ds = None


class FlowGradientAlgorithm(QgsProcessingAlgorithm):
    INPUT, BAND = "INPUT", "BAND"
    SMOOTH_RADIUS = "SMOOTH_RADIUS"
    VECTOR_STEP = "VECTOR_STEP"
    OUTPUT, OUTPUT_AZIMUTH, OUTPUT_VECTORS = "OUTPUT", "OUTPUT_AZIMUTH", "OUTPUT_VECTORS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return FlowGradientAlgorithm()
    def name(self): return "flow_gradient"
    def displayName(self):
        return self.tr("2.4 Гидравлический градиент и направление потока")

    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "По растру напора (пьезометрической поверхности) строит "
            "гидравлический градиент и направление потока. Вход - растр напора, "
            "например результат «2D Kriging» по уровням в скважинах.\n\n"
            "Выходы: растр модуля градиента |∇h| (безразмерный, м/м), растр "
            "азимута направления потока (компасный, 0 = север, вниз по "
            "градиенту) и точечный слой векторов потока для оформления стрелками "
            "(поля az - азимут, grad - градиент).\n\n"
            "Это геометрия поля напора, без проницаемости: скорость фильтрации "
            "по Дарси (v = −K·∇h) требует коэффициента фильтрации K и здесь не "
            "считается. Изолинии напора стройте инструментом «Изолинии из "
            "растра».\n\nГрадиент усиливает шум грида - при пятнистом результате "
            "включите сглаживание (радиус в ячейках) или сгладьте напор в "
            "«2D Kriging».") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Растр напора")))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BAND, self.tr("Канал"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH_RADIUS,
            self.tr("Сглаживание напора перед расчётом, ячеек (0 = без)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.SMOOTH_RADIUS, 0.0),
            minValue=0.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.VECTOR_STEP, self.tr("Векторы потока: шаг прореживания, ячеек"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.VECTOR_STEP, 8), minValue=1, maxValue=200))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Гидравлический градиент (модуль)")))
        az = QgsProcessingParameterRasterDestination(
            self.OUTPUT_AZIMUTH, self.tr("Направление потока (азимут)"),
            optional=True, createByDefault=True)
        self.addParameter(az)
        vec = QgsProcessingParameterFeatureSink(
            self.OUTPUT_VECTORS, self.tr("Векторы потока (точки)"),
            type=QgsProcessing.TypeVectorPoint, optional=True,
            createByDefault=True)
        self.addParameter(vec)

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        rl = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if rl is None:
            raise QgsProcessingException(self.tr("Не задан растр напора."))
        band = self.parameterAsInt(parameters, self.BAND, context)
        sm_rad = self.parameterAsDouble(parameters, self.SMOOTH_RADIUS, context)
        step = self.parameterAsInt(parameters, self.VECTOR_STEP, context)
        name = _short(rl.name())

        ds = gdal.Open(rl.source())
        if ds is None:
            raise QgsProcessingException(self.tr("Не удалось открыть растр напора."))
        b = ds.GetRasterBand(band)
        arr = b.ReadAsArray().astype(float)
        gt = ds.GetGeoTransform()
        src_nd = b.GetNoDataValue()
        ds = None
        ny, nx = arr.shape
        cellx = abs(gt[1]) or 1.0
        celly = abs(gt[5]) or 1.0
        nodata = -9999.0

        valid = np.isfinite(arr)
        if src_nd is not None:
            valid &= (arr != src_nd)
        if not valid.any():
            raise QgsProcessingException(self.tr("В растре напора нет данных."))

        feedback.pushInfo(_tr("Растр напора %d x %d, ячейка %.4g x %.4g.")
                          % (nx, ny, cellx, celly))
        if sm_rad and sm_rad > 0:
            feedback.pushInfo(_tr("Сглаживание напора (σ=%g яч.)…") % sm_rad)
            arr = _gaussian_nodata(np.where(valid, arr, 0.0), valid, float(sm_rad))
        z = np.where(valid, arr, nodata)

        feedback.setProgress(40)
        mag, az = hydro.head_gradient(z, cellx, celly, nodata)
        feedback.setProgress(60)

        crs = rl.crs()
        crs_wkt = crs.toWkt() if (crs is not None and crs.isValid()) else None

        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        az_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_AZIMUTH, context) or None
        _write_grid_tiff(out_path, mag, gt, crs_wkt, nodata, nx, ny)
        _set_output_name(context, out_path,
                         _tr("Гидравлический градиент · %s") % name)
        results = {self.OUTPUT: out_path}
        if az_path:
            _write_grid_tiff(az_path, az, gt, crs_wkt, nodata, nx, ny)
            _set_output_name(context, az_path,
                             _tr("Направление потока · %s") % name)
            results[self.OUTPUT_AZIMUTH] = az_path
        feedback.setProgress(75)

        # векторное поле стрелок: точки с азимутом и градиентом
        fields = QgsFields()
        fields.append(QgsField("az", QVariant.Double))
        fields.append(QgsField("grad", QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT_VECTORS, context, fields,
            QgsWkbTypes.Point, crs)
        if sink is not None:
            xs, ys, azs, grs = hydro.flow_samples(mag, az, gt, step, nodata)
            for i in range(len(xs)):
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(float(xs[i]), float(ys[i]))))
                f.setAttributes([float(azs[i]), float(grs[i])])
                sink.addFeature(f)
            feedback.pushInfo(
                _tr("Векторов потока: %d (шаг %d яч.). Слой оформлен стрелками "
                "автоматически: поворот по полю «az», размер по «grad». "
                "Символику можно поменять в свойствах слоя.")
                % (len(xs), max(int(step), 1)))
            _set_output_name(context, dest, _tr("Векторы потока · %s") % name)
            _attach_style(context, dest, _style_path("flow_arrows"))
            results[self.OUTPUT_VECTORS] = dest

        _save_values(self, _saved)
        feedback.setProgress(100)
        return results


class ExternalDriftKrigingAlgorithm(QgsProcessingAlgorithm):
    """Кригинг с внешним дрейфом (External Drift) - регрессия-кригинг по
    сторонней переменной, известной всюду (растр). Опирается на то же ядро,
    что и «2D Kriging»: дрейф снимается регрессией, кригуются остатки, дрейф
    возвращается к оценке из растра. Математика кригинга не меняется."""

    INPUT, ZFIELD = "INPUT", "ZFIELD"
    DRIFT_RASTER, DRIFT_BAND, DRIFT_DEG = "DRIFT_RASTER", "DRIFT_BAND", "DRIFT_DEG"
    KTYPE, SKMEAN, NUGGET = "KTYPE", "SKMEAN", "NUGGET"
    RADIUS, MIN_POINTS, MAX_POINTS = "RADIUS", "MIN_POINTS", "MAX_POINTS"
    CELL_SIZE, EXTENT, OUTPUT = "CELL_SIZE", "EXTENT", "OUTPUT"
    CLIP_HULL, HULL_BUFFER, MASK = "CLIP_HULL", "HULL_BUFFER", "MASK"
    OUTPUT_STDERR = "OUTPUT_STDERR"
    VAL_PCT, VAL_MIN, VAL_MAX, VAL_CAP = "VAL_PCT", "VAL_MIN", "VAL_MAX", "VAL_CAP"
    SMOOTH, SMOOTH_RADIUS = "SMOOTH", "SMOOTH_RADIUS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return ExternalDriftKrigingAlgorithm()
    def name(self): return "kriging_external_drift"
    def displayName(self):
        return self.tr("2.2 Кригинг с внешним дрейфом (External Drift)")

    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Кригинг с внешним дрейфом (External Drift): оценка по точкам, "
            "когда поле закономерно связано со сторонней величиной, известной "
            "всюду в виде растра (структурная поверхность соседнего пласта, "
            "грубая региональная модель, сейсмический атрибут).\n\nДрейф "
            "снимается регрессией значения на растр, кригуются остатки, дрейф "
            "возвращается к оценке из того же растра. Это та же схема регрессия-"
            "кригинг, что и флажок «Снять полиномиальный тренд» у «2D Kriging», "
            "только дрейф здесь не функция координат, а функция внешнего "
            "значения. Степень дрейфа 1 (линейный) почти всегда достаточна.\n\n"
            "Вариограмму задавайте по ОСТАТКАМ. Растр дрейфа и точки должны быть "
            "в одной системе координат. Ячейки вне покрытия растра дрейфа "
            "остаются пустыми. Поиск, анизотропия, обрезка и стандартная ошибка "
            "- как у «2D Kriging».") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точечный слой"),
            [QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения (Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric,
            defaultValue=_dv(self, self.ZFIELD, None)))
        dr = QgsProcessingParameterRasterLayer(
            self.DRIFT_RASTER, self.tr("Растр внешнего дрейфа (известен всюду)"))
        dr.setHelp(self.tr(
            "Сторонняя величина s, заданная растром во всей области: соседний "
            "пласт, структурная поверхность, грубая модель, сейсмический "
            "атрибут. Значение поля Z регрессируется на s, кригуются остатки, "
            "дрейф возвращается из растра. Растр должен покрывать область "
            "оценки и быть в той же системе координат, что и точки."))
        self.addParameter(dr)
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.DRIFT_BAND, self.tr("Канал растра дрейфа"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        ddeg = QgsProcessingParameterEnum(
            self.DRIFT_DEG, self.tr("Степень дрейфа"),
            options=[self.tr("1 (линейный)"), self.tr("2 (квадратичный)")],
            defaultValue=_dv(self, self.DRIFT_DEG, 0))
        ddeg.setHelp(self.tr(
            "Связь значения с внешней величиной s. Степень 1 - линейный дрейф "
            "m = a0 + a1·s, обычный выбор для External Drift. Степень 2 "
            "описывает изогнутую связь m = a0 + a1·s + a2·s², но может вобрать "
            "часть реальной структуры в дрейф - после неё посмотрите на "
            "вариограмму остатков."))
        self.addParameter(ddeg)
        _add_kriging_params(self)
        self.addParameter(QgsProcessingParameterBoolean(
            self.SMOOTH, self.tr("Сгладить грид (Гаусс)"),
            defaultValue=_dv(self, self.SMOOTH, False)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH_RADIUS, self.tr("Радиус сглаживания, ячеек"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.SMOOTH_RADIUS, 1.0),
            minValue=0.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Растр кригинга с дрейфом")))
        se = QgsProcessingParameterRasterDestination(
            self.OUTPUT_STDERR, self.tr("Стандартная ошибка кригинга"),
            optional=True, createByDefault=False)
        se.setHelp(self.tr(
            "Необязательный растр стандартной ошибки кригинга остатков "
            "(sqrt дисперсии): мера неопределённости. Дрейф детерминирован и "
            "своей погрешности к ней не добавляет."))
        self.addParameter(se)

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        se_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_STDERR, context) or None
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        src = layer.name() if layer is not None else "data"
        mask = _build_mask(self, parameters, context, feedback, layer)
        path, _, se = _run_kriging_to_tiff(self, parameters, context, feedback,
                                           source, zfield, out_path, mask,
                                           stderr_path=se_path)
        _set_output_name(context, path,
                         _tr("Кригинг+дрейф %s · %s") % (zfield, _short(src)))
        results = {self.OUTPUT: path}
        if se:
            _set_output_name(context, se,
                             _tr("Стд. ошибка · %s · %s") % (zfield, _short(src)))
            results[self.OUTPUT_STDERR] = se
        _save_values(self, _saved)
        feedback.setProgress(100)
        return results


class ExceedanceProbabilityAlgorithm(QgsProcessingAlgorithm):
    """Карта вероятности превышения порога из растров оценки и стандартной
    ошибки кригинга. Постобработка, как «Гидравлический градиент»: своего
    кригинга не делает, считает P(Z>порог) = Φ((оценка−порог)/ошибка) из уже
    готовых растров. Окно «2D Kriging» не трогает - инструмент отдельный."""

    ESTIMATE, STDERR = "ESTIMATE", "STDERR"
    BAND_EST, BAND_SE = "BAND_EST", "BAND_SE"
    THRESHOLD, SIDE, OUTPUT = "THRESHOLD", "SIDE", "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return ExceedanceProbabilityAlgorithm()
    def name(self): return "exceedance_probability"
    def displayName(self):
        return self.tr("2.3 Карта вероятности превышения")

    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Карта вероятности превышения порога по растрам оценки и стандартной "
            "ошибки кригинга. Локальное распределение принимается нормальным, "
            "Z ~ N(оценка, ошибка²), и вероятность считается одной формулой "
            "P(Z>порог) = Φ((оценка−порог)/ошибка). Свой кригинг не выполняется, "
            "берутся готовые растры, поэтому «2D Kriging» остаётся без изменений.\n"
            "\nКак получить входы: запустите «2D Kriging» (или «Кригинг с внешним "
            "дрейфом») и включите необязательный вывод стандартной ошибки. Подайте "
            "сюда растр оценки и растр ошибки - получите растр вероятности 0…1.\n\n"
            "Применение: бортовые содержания (вероятность, что содержание выше "
            "кондиции), зоны риска по любому порогу. Для сильно скошенных полей "
            "нормальное допущение грубовато - тогда точнее индикаторный кригинг по "
            "порогам.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.ESTIMATE, self.tr("Растр оценки (кригинг)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.STDERR, self.tr("Растр стандартной ошибки кригинга")))
        side = QgsProcessingParameterEnum(
            self.SIDE, self.tr("Сторона"),
            options=[self.tr("выше порога: P(Z > порог)"),
                     self.tr("ниже порога: P(Z < порог)")],
            defaultValue=0)
        self.addParameter(side)
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD, self.tr("Порог"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BAND_EST, self.tr("Канал растра оценки"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BAND_SE, self.tr("Канал растра ошибки"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Растр вероятности (0…1)")))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        rl_e = self.parameterAsRasterLayer(parameters, self.ESTIMATE, context)
        rl_s = self.parameterAsRasterLayer(parameters, self.STDERR, context)
        if rl_e is None or rl_s is None:
            raise QgsProcessingException(self.tr(
                "Нужны оба растра: оценка и стандартная ошибка."))
        be = self.parameterAsInt(parameters, self.BAND_EST, context)
        bs = self.parameterAsInt(parameters, self.BAND_SE, context)
        thr = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        above = self.parameterAsEnum(parameters, self.SIDE, context) == 0
        name = _short(rl_e.name())

        ds = gdal.Open(rl_e.source())
        if ds is None:
            raise QgsProcessingException(self.tr("Не удалось открыть растр оценки."))
        eb = ds.GetRasterBand(be)
        est = eb.ReadAsArray().astype(float)
        gt = ds.GetGeoTransform()
        e_nd = eb.GetNoDataValue()
        ny, nx = est.shape
        ds = None

        # стандартную ошибку приводим к сетке оценки (билинейно), если решётки
        # не совпадают. Из одного запуска кригинга они и так совпадают.
        ds = gdal.Open(rl_s.source())
        if ds is None:
            raise QgsProcessingException(self.tr("Не удалось открыть растр ошибки."))
        sb = ds.GetRasterBand(bs)
        s_nd = sb.GetNoDataValue()
        if (ds.RasterXSize, ds.RasterYSize) == (nx, ny) and \
                ds.GetGeoTransform() == gt:
            se = sb.ReadAsArray().astype(float)
        else:
            ds = None
            feedback.pushInfo(_tr(
                "Решётки оценки и ошибки различаются, ошибка приведена к сетке "
                "оценки билинейно."))
            se = _resample_drift_to_grid(rl_s.source(),
                                         gt[0], gt[3] + ny * gt[5], abs(gt[1]),
                                         nx, ny, bs)
            if se is None:
                raise QgsProcessingException(self.tr(
                    "Не удалось привести растр ошибки к сетке оценки."))
        if ds is not None:
            ds = None

        nodata = -9999.0
        valid = np.isfinite(est) & np.isfinite(se)
        if e_nd is not None:
            valid &= (est != e_nd)
        if s_nd is not None:
            valid &= (se != s_nd)
        if not valid.any():
            raise QgsProcessingException(self.tr(
                "Нет ячеек, где заданы и оценка, и ошибка."))

        feedback.setProgress(40)
        prob = exceedance_prob(np.where(valid, est, 0.0),
                               np.where(valid, se, 0.0), thr, above)
        out = np.where(valid, prob, nodata)
        feedback.setProgress(70)

        share = 100.0 * float(np.mean(prob[valid] >= 0.5)) if valid.any() else 0.0
        feedback.pushInfo(_tr(
            "Порог %.4g, сторона %s. Вероятность ≥ 0.5 в %.0f%% ячеек с данными.")
            % (thr, _tr("выше") if above else _tr("ниже"), share))

        crs = rl_e.crs()
        crs_wkt = crs.toWkt() if (crs is not None and crs.isValid()) else None
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        _write_grid_tiff(out_path, out, gt, crs_wkt, nodata, nx, ny)
        side_txt = _tr("P(>%.4g)") % thr if above else _tr("P(<%.4g)") % thr
        _set_output_name(context, out_path,
                         _tr("Вероятность %s · %s") % (side_txt, name))
        _save_values(self, _saved)
        feedback.setProgress(100)
        return {self.OUTPUT: out_path}


class DarcyFluxAlgorithm(QgsProcessingAlgorithm):
    """Удельный расход по закону Дарси. Постобработка над растром напора и
    растрами свойств пласта: к геометрии потока (градиент, направление)
    добавляет проницаемость. Скорость фильтрации q = K·|∇h| (м/сут) и расход
    через единицу ширины Q = T·|∇h| (м²/сут). Своего кригинга не делает -
    растры K и T получают кригингом по точкам (лог-кригинг для K и T)."""

    INPUT, BAND = "INPUT", "BAND"
    KRASTER, KBAND = "KRASTER", "KBAND"
    TRASTER, TBAND = "TRASTER", "TBAND"
    LOG_INPUT = "LOG_INPUT"
    SMOOTH_RADIUS, VECTOR_STEP = "SMOOTH_RADIUS", "VECTOR_STEP"
    OUTPUT_Q, OUTPUT_QW = "OUTPUT_Q", "OUTPUT_QW"
    OUTPUT_AZIMUTH, OUTPUT_VECTORS = "OUTPUT_AZIMUTH", "OUTPUT_VECTORS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return DarcyFluxAlgorithm()
    def name(self): return "darcy_flux"
    def displayName(self):
        return self.tr("2.5 Удельный расход (закон Дарси)")

    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Удельный расход подземного потока по закону Дарси. К геометрии "
            "потока (градиент напора и направление) добавляет свойства пласта, "
            "переводя безразмерный градиент в физический поток.\n\nВходы: растр "
            "напора и хотя бы один из растров свойств - коэффициент фильтрации K "
            "или водопроводимость T. Выходы: скорость фильтрации q = K·|∇h| "
            "(м/сут) и расход через единицу ширины потока Q = T·|∇h| (м²/сут), "
            "плюс направление и стрелки.\n\nКак получить K и T: кригуйте их по "
            "точкам испытаний. K и T обычно лог-нормальны (разброс на порядки), "
            "поэтому кригуйте ln(K) и ln(T), а тут включите «Растры заданы как "
            "ln». Истинная скорость воды v = q/n требует пористости и здесь не "
            "считается. Напорные и безнапорные пласты разумно криговать "
            "раздельно. Растры должны быть в одной системе координат.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Растр напора")))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BAND, self.tr("Канал напора"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.KRASTER, self.tr("Растр коэффициента фильтрации K (м/сут)"),
            optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.TRASTER, self.tr("Растр водопроводимости T (м²/сут)"),
            optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.LOG_INPUT,
            self.tr("Растры K и T заданы как ln (экспонировать)"),
            defaultValue=False))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.KBAND, self.tr("Канал растра K"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.TBAND, self.tr("Канал растра T"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH_RADIUS,
            self.tr("Сглаживание напора перед расчётом, ячеек (0 = без)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.SMOOTH_RADIUS, 0.0),
            minValue=0.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.VECTOR_STEP, self.tr("Векторы потока: шаг прореживания, ячеек"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.VECTOR_STEP, 8), minValue=1, maxValue=200))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_Q, self.tr("Скорость фильтрации q = K·|∇h| (м/сут)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_QW, self.tr("Расход через ширину Q = T·|∇h| (м²/сут)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT_AZIMUTH, self.tr("Направление потока (азимут)"),
            optional=True, createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_VECTORS, self.tr("Векторы потока (точки)"),
            type=QgsProcessing.TypeVectorPoint, optional=True,
            createByDefault=True))

    def _grid(self, src, band, gt, nx, ny):
        """Читает растр свойства, при несовпадении решётки приводит к сетке
        напора билинейно. Возвращает массив с nan вне покрытия/данных."""
        ds = gdal.Open(src)
        if ds is None:
            return None
        if (ds.RasterXSize, ds.RasterYSize) == (nx, ny) and \
                ds.GetGeoTransform() == gt:
            b = ds.GetRasterBand(band)
            a = b.ReadAsArray().astype(float)
            nd = b.GetNoDataValue(); ds = None
            return np.where(a == nd, np.nan, a) if nd is not None else a
        ds = None
        return _resample_drift_to_grid(src, gt[0], gt[3] + ny * gt[5],
                                       abs(gt[1]), nx, ny, band)

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        rl = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        if rl is None:
            raise QgsProcessingException(self.tr("Не задан растр напора."))
        rl_k = self.parameterAsRasterLayer(parameters, self.KRASTER, context)
        rl_t = self.parameterAsRasterLayer(parameters, self.TRASTER, context)
        if rl_k is None and rl_t is None:
            raise QgsProcessingException(self.tr(
                "Задайте хотя бы один растр свойства: K или T."))
        band = self.parameterAsInt(parameters, self.BAND, context)
        kband = self.parameterAsInt(parameters, self.KBAND, context)
        tband = self.parameterAsInt(parameters, self.TBAND, context)
        as_log = self.parameterAsBool(parameters, self.LOG_INPUT, context)
        sm_rad = self.parameterAsDouble(parameters, self.SMOOTH_RADIUS, context)
        step = self.parameterAsInt(parameters, self.VECTOR_STEP, context)
        name = _short(rl.name())

        ds = gdal.Open(rl.source())
        if ds is None:
            raise QgsProcessingException(self.tr("Не удалось открыть растр напора."))
        b = ds.GetRasterBand(band)
        arr = b.ReadAsArray().astype(float)
        gt = ds.GetGeoTransform()
        src_nd = b.GetNoDataValue()
        ds = None
        ny, nx = arr.shape
        cellx = abs(gt[1]) or 1.0
        celly = abs(gt[5]) or 1.0
        nodata = -9999.0

        valid = np.isfinite(arr)
        if src_nd is not None:
            valid &= (arr != src_nd)
        if not valid.any():
            raise QgsProcessingException(self.tr("В растре напора нет данных."))
        if sm_rad and sm_rad > 0:
            arr = _gaussian_nodata(np.where(valid, arr, 0.0), valid, float(sm_rad))
        z = np.where(valid, arr, nodata)

        feedback.setProgress(35)
        mag, az = hydro.head_gradient(z, cellx, celly, nodata)
        gvalid = (mag != nodata) & np.isfinite(mag)

        kgrid = tgrid = None
        if rl_k is not None:
            kgrid = self._grid(rl_k.source(), kband, gt, nx, ny)
            if kgrid is None:
                raise QgsProcessingException(self.tr("Не удалось прочитать растр K."))
            if as_log:
                kgrid = np.exp(kgrid)
        if rl_t is not None:
            tgrid = self._grid(rl_t.source(), tband, gt, nx, ny)
            if tgrid is None:
                raise QgsProcessingException(self.tr("Не удалось прочитать растр T."))
            if as_log:
                tgrid = np.exp(tgrid)
        feedback.setProgress(55)

        crs = rl.crs()
        crs_wkt = crs.toWkt() if (crs is not None and crs.isValid()) else None
        results = {}
        q_primary = None

        if kgrid is not None:
            ok = gvalid & np.isfinite(kgrid)
            q = np.where(ok, mag * kgrid, nodata).astype(np.float32)
            q_primary = q
            qp = self.parameterAsOutputLayer(parameters, self.OUTPUT_Q, context)
            if qp:
                _write_grid_tiff(qp, q, gt, crs_wkt, nodata, nx, ny)
                _set_output_name(context, qp,
                                 _tr("Скорость фильтрации q · %s") % name)
                results[self.OUTPUT_Q] = qp
                vv = q[q != nodata]
                if vv.size:
                    feedback.pushInfo(_tr(
                        "Скорость фильтрации q: медиана %.4g, максимум %.4g м/сут.")
                        % (float(np.median(vv)), float(vv.max())))
        if tgrid is not None:
            ok = gvalid & np.isfinite(tgrid)
            qw = np.where(ok, mag * tgrid, nodata).astype(np.float32)
            if q_primary is None:
                q_primary = qw
            qwp = self.parameterAsOutputLayer(parameters, self.OUTPUT_QW, context)
            if qwp:
                _write_grid_tiff(qwp, qw, gt, crs_wkt, nodata, nx, ny)
                _set_output_name(context, qwp,
                                 _tr("Расход через ширину Q · %s") % name)
                results[self.OUTPUT_QW] = qwp
                vv = qw[qw != nodata]
                if vv.size:
                    feedback.pushInfo(_tr(
                        "Расход через ширину Q: медиана %.4g, максимум %.4g м²/сут.")
                        % (float(np.median(vv)), float(vv.max())))
        feedback.setProgress(75)

        az_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT_AZIMUTH, context) or None
        if az_path:
            _write_grid_tiff(az_path, az, gt, crs_wkt, nodata, nx, ny)
            _set_output_name(context, az_path,
                             _tr("Направление потока · %s") % name)
            results[self.OUTPUT_AZIMUTH] = az_path

        # стрелки: направление по az, размер по удельному расходу (поле grad)
        fields = QgsFields()
        fields.append(QgsField("az", QVariant.Double))
        fields.append(QgsField("grad", QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT_VECTORS, context, fields,
            QgsWkbTypes.Point, crs)
        if sink is not None:
            xs, ys, azs, vals = hydro.flow_samples(q_primary, az, gt, step, nodata)
            for i in range(len(xs)):
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(float(xs[i]), float(ys[i]))))
                f.setAttributes([float(azs[i]), float(vals[i])])
                sink.addFeature(f)
            feedback.pushInfo(_tr(
                "Векторов потока: %d (шаг %d яч.). Поворот по «az», размер по "
                "удельному расходу.") % (len(xs), max(int(step), 1)))
            _set_output_name(context, dest, _tr("Векторы потока · %s") % name)
            _attach_style(context, dest, _style_path("flow_arrows"))
            results[self.OUTPUT_VECTORS] = dest

        _save_values(self, _saved)
        feedback.setProgress(100)
        return results


ALGORITHMS = [
    Kriging2DAlgorithm,
    CategoricalIndicatorAlgorithm,
    RasterToIsolinesAlgorithm,
    ExperimentalVariogramAlgorithm,
    VariogramMapAlgorithm,
    CrossValidationAlgorithm,
    ExampleWellsAlgorithm,
    ProfilesAlgorithm,
    FlowGradientAlgorithm,
    ExternalDriftKrigingAlgorithm,
    ExceedanceProbabilityAlgorithm,
    DarcyFluxAlgorithm,
]
