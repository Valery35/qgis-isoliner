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
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterMeshLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingParameterVectorDestination,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterFileDestination,
    QgsProcessingParameterFolderDestination,
    QgsProcessingParameterDefinition,
    QgsProcessingContext,
    QgsMeshLayer,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsPoint,
    QgsLineString,
    QgsPolygon,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
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
from .mesh3d import grid_to_2dm, sample_bilinear, polygon_mask

GROUP = _tr("1. Грид и изолинии")
GROUP_ID = "grid_isolines"
GROUP2 = _tr("2. Дополнительные инструменты анализа")
GROUP2_ID = "extra_tools"
GROUP3 = _tr("3. Разрез")
GROUP3_ID = "section"
GROUP4 = _tr("4. Пласт и блочная модель")
GROUP4_ID = "bed_block_model"

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


def _set_group(context, group, paths, force=False, history=None):
    """Кладёт загружаемые слои в фиксированную группу дерева (если группа уже
    есть, фреймворк добавляет в неё, новую не плодит; при force - даже для одного
    слоя). Заодно на выходы без собственного пост-процессора вешает финализатор:
    свернуть растр и записать историю создания."""
    paths = list(paths)
    if group and (force or len(paths) >= 2):
        for p in paths:
            try:
                if p and context.willLoadLayerOnCompletion(p):
                    context.layerToLoadOnCompletionDetails(p).groupName = group
            except Exception:
                pass
    for p in paths:
        try:
            if not (p and context.willLoadLayerOnCompletion(p)) or p in _PP_PATHS:
                continue
            det = context.layerToLoadOnCompletionDetails(p)
            has_pp = False
            try:
                has_pp = det.postProcessor() is not None
            except Exception:
                has_pp = False
            if has_pp:
                continue
            pp = _FinalizePostProcessor(history or [])
            _KEEP_ALIVE.append(pp)
            det.setPostProcessor(pp)
            _PP_PATHS.add(p)
        except Exception:
            pass


# Фиксированные группы дерева слоёв для многослойных инструментов
GRP_KRIGING = _tr("Кригинг")
GRP_INDICATOR = _tr("Индикаторный кригинг")
GRP_DRIFT = _tr("Кригинг с внешним дрейфом")
GRP_ISOLINES = _tr("Изолинии")
GRP_FLOW = _tr("Гидравлический градиент")
GRP_DARCY = _tr("Удельный расход")
GRP_SIM = _tr("Гауссова симуляция")
GRP_WELLS_DEMO = _tr("Пример скважин")
GRP_SECTION = _tr("Разрез")
GRP_SECTION_DEMO = _tr("Пример разреза")
GRP_MESH3D = _tr("Поверхности 3D")


# держим пост-процессоры живыми (иначе их соберёт сборщик мусора Python)
_KEEP_ALIVE = []
_PP_PATHS = set()    # пути выходов, на которые уже повешен пост-процессор


def _finalize_layer(layer, history):
    """Общее для всех выходов: свернуть узел растра в дереве (чтобы стопка гридов
    не раздувала панель слоёв) и записать историю создания в метаданные слоя."""
    try:
        from qgis.core import QgsRasterLayer
        if isinstance(layer, QgsRasterLayer):
            node = QgsProject.instance().layerTreeRoot().findLayer(layer.id())
            if node is not None:
                node.setExpanded(False)
    except Exception:
        pass
    try:
        if history:
            md = layer.metadata()
            for line in history:
                md.addHistoryItem(line)
            layer.setMetadata(md)
    except Exception:
        pass


class _FinalizePostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Пост-процессор по умолчанию для выходов без своего пост-процессора:
    сворачивает растр и пишет историю создания."""
    def __init__(self, history):
        super().__init__()
        self.history = history

    def postProcessLayer(self, layer, context, feedback):
        _finalize_layer(layer, self.history)


def _provenance(alg, parameters=None):
    """История создания слоя: версия плагина, инструмент, дата."""
    import datetime
    h = []
    try:
        h.append(_version_line())
    except Exception:
        pass
    try:
        h.append(_tr("Инструмент: %s") % alg.displayName())
    except Exception:
        pass
    try:
        h.append(_tr("Создано: %s")
                 % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception:
        pass
    return h


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
        _finalize_layer(layer, getattr(self, "history", []))
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
            _PP_PATHS.add(lines_path)
        if polys_path and context.willLoadLayerOnCompletion(polys_path):
            context.layerToLoadOnCompletionDetails(polys_path).setPostProcessor(pp)
            _PP_PATHS.add(polys_path)
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
        _finalize_layer(layer, getattr(self, "history", []))
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
            _PP_PATHS.add(path)
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
        _finalize_layer(layer, getattr(self, "history", []))
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
            _PP_PATHS.add(path)
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
    обнуляются и нормируются, поэтому у края покрытия выборка остаётся корректной.
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
        _set_group(context, GRP_KRIGING, list(results.values()), history=_provenance(self, parameters))
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
        _set_group(context, GRP_INDICATOR, list(results.values()), history=_provenance(self, parameters))
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
        _set_group(context, GRP_ISOLINES, list(results.values()), history=_provenance(self, parameters))
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
        return self.tr("1.7 Создать пример скважин (демо)")

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
            self.N_POINTS, self.tr("Количество скважин"),
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
        _set_group(context, GRP_WELLS_DEMO, list(results.values()), history=_provenance(self, parameters))
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
            self.N_LAGS, self.tr("Количество лагов"),
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
                    (_tr("Количество лагов"), "%d" % n_lags),
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

    def displayName(self): return self.tr("1.6 Профили обработки")

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
        _set_group(context, None, list(results.values()), history=_provenance(self, parameters))
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
def _write_grid_tiff(path, array, geotr, crs_wkt, nodata, nx, ny,
                     band_names=None):
    """Пишет Float32 GeoTIFF с геопривязкой и nodata. array - один 2D-массив
    (один канал) или список массивов (многоканальный грид); band_names -
    подписи каналов той же длины."""
    arrs = list(array) if isinstance(array, (list, tuple)) else [array]
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, nx, ny, len(arrs), gdal.GDT_Float32,
                       options=["COMPRESS=LZW", "TILED=YES"])
    ds.SetGeoTransform(geotr)
    if crs_wkt:
        ds.SetProjection(crs_wkt)
    for i, a in enumerate(arrs, 1):
        band = ds.GetRasterBand(i)
        band.SetNoDataValue(nodata)
        band.WriteArray(a)
        if band_names:
            band.SetDescription(band_names[i - 1])
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
        _set_group(context, GRP_FLOW, list(results.values()), history=_provenance(self, parameters))
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
        _set_group(context, GRP_DRIFT, list(results.values()), history=_provenance(self, parameters))
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
        _set_group(context, None, [out_path], history=_provenance(self, parameters))
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
        _set_group(context, GRP_DARCY, list(results.values()), history=_provenance(self, parameters))
        return results


class SectionDemoAlgorithm(QgsProcessingAlgorithm):
    """Демо-данные для разреза: три гладкие стопкой поверхности (две залежи) с
    падением и волнистой переменной мощностью, плюс линия через площадь. Готово
    для подачи в «Разрез по линии» без кригинга реальных данных."""

    EXTENT, SEED = "EXTENT", "SEED"
    SURF1, SURF2, SURF3 = "SURF1", "SURF2", "SURF3"
    SURF4, SURF5, SURF6 = "SURF4", "SURF5", "SURF6"
    LINE, WELLS = "LINE", "WELLS"
    BED1, BED2 = "BED1", "BED2"
    FAULT, MARKER, ZONE = "FAULT", "MARKER", "ZONE"
    TIN = "TIN"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionDemoAlgorithm()
    def name(self): return "section_demo"
    def displayName(self): return self.tr("3.10 Создать пример для разреза")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Готовый пример для инструментов разреза. Строит шесть гладких "
            "поверхностей, лежащих стопкой, с региональным падением и волнистой "
            "переменной мощностью. Между ними пять пластов в переслаивании: три "
            "вмещающих и два промышленных (2-й и 4-й, тонкие).\n\nПодайте шесть "
            "поверхностей сверху вниз (1...6) и линию в «Разрез по линии». "
            "Получите пять пластов на чертеже и 3D-забор. Кригинг для демо не "
            "нужен, поверхности уже растровые. Заодно выдаются скважины вдоль "
            "линии с отметками поверхностей (h1...h6) для инструмента «Скважины "
            "на разрез», а также по многоканальному гриду на каждый "
            "промышленный пласт. Конвенция каналов: 1 кровля, 2 подошва, "
            "3+ параметры (здесь содержание и минтип, независимые "
            "стохастические поля). Пласт как блочная модель: один файл кормит "
            "«Состав пласта на разрез» (каналы 1/2/3) и 3D-просмотр.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Область (экстент)")))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно генератора (0 = случайно)"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.SEED, 0), minValue=0)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.SURF1, self.tr("Поверхность 1 (кровля верхней вмещающей)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.SURF2, self.tr("Поверхность 2 (кровля 1-го промышленного)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.SURF3, self.tr("Поверхность 3 (подошва 1-го промышленного)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.SURF4, self.tr("Поверхность 4 (кровля 2-го промышленного)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.SURF5, self.tr("Поверхность 5 (подошва 2-го промышленного)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.SURF6, self.tr("Поверхность 6 (подошва нижней вмещающей)")))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.LINE, self.tr("Линия разреза"),
            type=QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.WELLS, self.tr("Скважины вдоль линии (с отметками поверхностей)"),
            type=QgsProcessing.TypeVectorPoint, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.BED1, self.tr("Пласт 1-й пром. (каналы: кровля, подошва, содержание, минтип)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.BED2, self.tr("Пласт 2-й пром. (каналы: кровля, подошва, содержание, минтип)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.FAULT, self.tr("Разлом для пересечения (2D-линия)"),
            type=QgsProcessing.TypeVectorLine, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.MARKER, self.tr("Маркер с отметкой Z (3D-линия)"),
            type=QgsProcessing.TypeVectorLine, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.ZONE, self.tr("Зона замещения для пересечения (полигон)"),
            type=QgsProcessing.TypeVectorPolygon, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.TIN, self.tr("Опрокинутая TIN (3D-грани для пересечения)"),
            type=QgsProcessing.TypeVectorPolygon, optional=True,
            createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if rect is None or rect.isEmpty():
            raise QgsProcessingException(self.tr("Не задан экстент."))
        xmin, xmax = rect.xMinimum(), rect.xMaximum()
        ymin, ymax = rect.yMinimum(), rect.yMaximum()
        seed = self.parameterAsInt(parameters, self.SEED, context)
        rng = np.random.default_rng(seed or None)

        G = 200
        cx = (np.arange(G) / (G - 1.0))[None, :]   # 0..1 вдоль X (запад->восток)
        def wv(f): return _demo_field(rng, G, max(1, int(f * G)))
        # шесть поверхностей стопкой, пять пластов в переслаивании: 1 вмещающая,
        # 2 промышленный, 3 вмещающая, 4 промышленный, 5 вмещающая. Падение,
        # пологая складка вдоль линии, промышленные пласты выклиниваются к востоку.
        e = cx - 0.5                                    # центрировано
        fold = 22.0 * np.sin(2.0 * np.pi * cx)          # пологая складка
        s1 = 100.0 - 80.0 * cx + fold + 10.0 * wv(0.16)  # кровля: падение+складка
        th = [np.clip(18.0 + 10.0 * e + 5.0 * wv(0.20), 4.0, None),  # вмещ.1
              np.clip(6.0 - 4.0 * e + 2.0 * wv(0.22), 1.2, None),    # пром.1 клин
              np.clip(12.0 + 4.0 * wv(0.18), 3.0, None),             # вмещ.2
              np.clip(7.0 - 14.0 * e + 2.0 * wv(0.22), 0.0, None),    # пром.2 выклинивается на восток
              np.clip(20.0 + 6.0 * wv(0.17), 4.0, None)]             # вмещ.3
        surf = [s1]
        for t in th:
            surf.append(surf[-1] - t)              # s2...s6

        cellx = (xmax - xmin) / (G - 1)
        celly = (ymax - ymin) / (G - 1)
        geotr = (xmin - 0.5 * cellx, cellx, 0.0,
                 ymax + 0.5 * celly, 0.0, -celly)
        crs_wkt = crs.toWkt() if (crs is not None and crs.isValid()) else None

        results = {}
        names = (self.tr("Поверхность 1 · кровля (демо)"),
                 self.tr("Поверхность 2 · кровля 1-го пром. (демо)"),
                 self.tr("Поверхность 3 · подошва 1-го пром. (демо)"),
                 self.tr("Поверхность 4 · кровля 2-го пром. (демо)"),
                 self.tr("Поверхность 5 · подошва 2-го пром. (демо)"),
                 self.tr("Поверхность 6 · подошва (демо)"))
        keys = (self.SURF1, self.SURF2, self.SURF3,
                self.SURF4, self.SURF5, self.SURF6)
        for key, grid, name in zip(keys, surf, names):
            path = self.parameterAsOutputLayer(parameters, key, context)
            _write_grid_tiff(path, np.flipud(grid).astype(np.float32),
                             geotr, crs_wkt, -9999.0, G, G)
            _set_output_name(context, path, name)
            results[key] = path

        # состав промышленных пластов (демо): у каждого пласта свой грид.
        # Содержание = независимое крупное поле + мелкая пятнистость + свой
        # латеральный тренд (1-й пром. богаче на западе, 2-й на юге и чуть
        # беднее в среднем); минтип порогом по содержанию своего пласта.
        cy = (np.arange(G) / (G - 1.0))[:, None]   # 0..1 вдоль Y (юг->север)

        def bed_grade(trend, base):
            big = _demo_field(rng, G, max(1, int(0.22 * G)))
            fine = _demo_field(rng, G, max(1, int(0.07 * G)))
            return np.clip(base + 6.0 * big + 3.5 * fine + trend, 6.0, 40.0)

        grade1 = bed_grade(-16.0 * (cx - 0.45), 24.0)
        grade2 = bed_grade(-12.0 * (cy - 0.55), 22.0)
        mintype1 = np.where(grade1 >= 18.0, 1.0, 2.0)
        mintype2 = np.where(grade2 >= 17.0, 1.0, 2.0)
        # конвенция многоканального грида пласта: канал 1 - кровля, канал 2 -
        # подошва, каналы 3+ - параметры (содержание, минтип, ...). Пласт как
        # блочная модель: параметры добавляются каналами без смены формата.
        bnames = [self.tr("кровля"), self.tr("подошва"),
                  self.tr("содержание"), self.tr("минтип")]
        for key, roof, bot, grd, mtp, nm in (
                (self.BED1, surf[1], surf[2], grade1, mintype1,
                 self.tr("Пласт 1-й пром. (демо)")),
                (self.BED2, surf[3], surf[4], grade2, mintype2,
                 self.tr("Пласт 2-й пром. (демо)"))):
            path = self.parameterAsOutputLayer(parameters, key, context)
            if path:
                stack = [np.flipud(a).astype(np.float32)
                         for a in (roof, bot, grd, mtp)]
                _write_grid_tiff(path, stack, geotr, crs_wkt, -9999.0,
                                 G, G, band_names=bnames)
                _set_output_name(context, path, nm)
                results[key] = path
        # линия разреза: ломаная с двумя внутренними изломами (поперёк падения)
        W, H = xmax - xmin, ymax - ymin
        p0 = QgsPointXY(xmin + 0.05 * W, ymin + 0.32 * H)
        p1 = QgsPointXY(xmax - 0.05 * W, ymin + 0.60 * H)
        dirx, diry = p1.x() - p0.x(), p1.y() - p0.y()
        ln = math.hypot(dirx, diry) or 1.0
        ux, uy = dirx / ln, diry / ln           # вдоль линии
        nx, ny = -uy, ux                        # нормаль к линии
        amp = 0.06 * min(W, H)
        pa = QgsPointXY(p0.x() + 0.35 * dirx + amp * nx,
                        p0.y() + 0.35 * diry + amp * ny)
        pb = QgsPointXY(p0.x() + 0.68 * dirx - 0.85 * amp * nx,
                        p0.y() + 0.68 * diry - 0.85 * amp * ny)
        lg = QgsGeometry.fromPolylineXY([p0, pa, pb, p1])
        L = float(lg.length())
        fields = QgsFields()
        fields.append(QgsField("name", QVariant.String))
        sink, dest = self.parameterAsSink(
            parameters, self.LINE, context, fields, QgsWkbTypes.LineString, crs)
        ft = QgsFeature(fields)
        ft.setGeometry(lg)
        ft.setAttributes([self.tr("Разрез 1")])
        sink.addFeature(ft)
        _set_output_name(context, dest, self.tr("Линия разреза (демо)"))
        results[self.LINE] = dest

        # скважины вдоль ломаной: позиция по длине линии плюс малый отступ в
        # коридоре. На каждой берём отметки шести поверхностей (h1...h6).
        corr = 0.006 * L
        nw = 26
        t = rng.uniform(0.04, 0.96, nw)
        off = rng.uniform(-corr, corr, nw)
        wx = np.empty(nw); wy = np.empty(nw)
        for i in range(nw):
            bp = lg.interpolate(float(t[i] * L)).asPoint()
            wx[i] = bp.x() + off[i] * nx
            wy[i] = bp.y() + off[i] * ny
        hs = [_demo_sample(g, wx, wy, xmin, xmax, ymin, ymax) for g in surf]
        wf = QgsFields()
        wf.append(QgsField("name", QVariant.String))
        for j in range(6):
            wf.append(QgsField("h%d" % (j + 1), QVariant.Double))
        wsink, wdest = self.parameterAsSink(
            parameters, self.WELLS, context, wf, QgsWkbTypes.Point, crs)
        if wsink is not None:
            for i in range(nw):
                fw = QgsFeature(wf)
                fw.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(float(wx[i]), float(wy[i]))))
                fw.setAttributes(["SKR-%03d" % (i + 1)]
                                 + [float(hs[j][i]) for j in range(6)])
                wsink.addFeature(fw)
            _set_output_name(context, wdest, self.tr("Скважины (демо)"))
            results[self.WELLS] = wdest

        # демо-векторы для «3.5 Пересечение векторов с разрезом»: разлом
        # (2D-линия без Z) -> вертикаль; маркер (3D-линия с Z) -> точка;
        # зона замещения (полигон) -> полоса. Все пересекают линию разреза.
        md = min(W, H)
        # центр разлома смещён с середины створа (там излом линии), чтобы
        # разлом не выглядел «определением, срезавшим угол»
        bpf = lg.interpolate(0.62 * L).asPoint()
        ff = QgsFields(); ff.append(QgsField("name", QVariant.String))
        fsink, fdest = self.parameterAsSink(
            parameters, self.FAULT, context, ff, QgsWkbTypes.LineString, crs)
        if fsink is not None:
            fe = QgsFeature(ff)
            fe.setGeometry(QgsGeometry.fromPolylineXY([
                QgsPointXY(bpf.x() + 0.18 * md * nx, bpf.y() + 0.18 * md * ny),
                QgsPointXY(bpf.x() - 0.18 * md * nx, bpf.y() - 0.18 * md * ny)]))
            fe.setAttributes([self.tr("Разлом A")]); fsink.addFeature(fe)
            _set_output_name(context, fdest, self.tr("Разлом (демо, 2D)"))
            results[self.FAULT] = fdest
        bpm = lg.interpolate(0.35 * L).asPoint()
        zc = float(_demo_sample(surf[1], np.array([bpm.x()]),
                                np.array([bpm.y()]), xmin, xmax, ymin, ymax)[0])
        mf = QgsFields(); mf.append(QgsField("name", QVariant.String))
        msink, mdest = self.parameterAsSink(
            parameters, self.MARKER, context, mf, QgsWkbTypes.LineStringZ, crs)
        if msink is not None:
            me = QgsFeature(mf)
            me.setGeometry(QgsGeometry(QgsLineString([
                QgsPoint(bpm.x() + 0.15 * md * nx,
                         bpm.y() + 0.15 * md * ny, zc + 6.0),
                QgsPoint(bpm.x() - 0.15 * md * nx,
                         bpm.y() - 0.15 * md * ny, zc - 6.0)])))
            me.setAttributes([self.tr("Маркер K (с Z)")]); msink.addFeature(me)
            _set_output_name(context, mdest, self.tr("Маркер с Z (демо, 3D)"))
            results[self.MARKER] = mdest
        zf = QgsFields(); zf.append(QgsField("name", QVariant.String))
        zsink, zdest = self.parameterAsSink(
            parameters, self.ZONE, context, zf, QgsWkbTypes.Polygon, crs)
        if zsink is not None:
            zx0, zx1 = xmin + 0.55 * W, xmin + 0.85 * W
            zy0, zy1 = ymin + 0.30 * H, ymin + 0.78 * H
            ze = QgsFeature(zf)
            ze.setGeometry(QgsGeometry.fromPolygonXY([[
                QgsPointXY(zx0, zy0), QgsPointXY(zx1, zy0),
                QgsPointXY(zx1, zy1), QgsPointXY(zx0, zy1),
                QgsPointXY(zx0, zy0)]]))
            ze.setAttributes([self.tr("Зона замещения")]); zsink.addFeature(ze)
            _set_output_name(context, zdest, self.tr("Зона (демо, полигон)"))
            results[self.ZONE] = zdest

        # опрокинутая TIN: гладкая лежачая складка маркер-поверхности. Профиль -
        # синусоида в наклонной раме (по падению пластов): длинные крылья, плавный
        # заворот без острых углов, малый размах по высоте (в реальных координатах
        # складки плоские, vex на чертеже их вытягивает). Над одной станцией обе
        # ветви - трасса заворачивается. Грани PolygonZ.
        tf = QgsFields(); tf.append(QgsField("name", QVariant.String))
        tsink, tdest = self.parameterAsSink(
            parameters, self.TIN, context, tf, QgsWkbTypes.PolygonZ, crs)
        if tsink is not None:
            cpt = lg.interpolate(0.5 * L).asPoint()
            du0 = 0.02 * L
            cp2 = lg.interpolate(min(L, 0.5 * L + du0)).asPoint()
            zc = float(_demo_sample(surf[2], np.array([cpt.x()]),
                                    np.array([cpt.y()]), xmin, xmax,
                                    ymin, ymax)[0])
            z2 = float(_demo_sample(surf[2], np.array([cp2.x()]),
                                    np.array([cp2.y()]), xmin, xmax,
                                    ymin, ymax)[0])
            bedslope = (z2 - zc) / du0           # падение пласта вдоль разреза
            ztop = float(_demo_sample(surf[1], np.array([cpt.x()]),
                         np.array([cpt.y()]), xmin, xmax, ymin, ymax)[0])
            zbot = float(_demo_sample(surf[5], np.array([cpt.x()]),
                         np.array([cpt.y()]), xmin, xmax, ymin, ymax)[0])
            span = max(8.0, abs(ztop - zbot))    # мощность пачки в центре
            Lf = 0.0375 * L            # длина складки вдоль разреза
            amp = 0.0125 * L           # горизонтальный размах петли (даёт заворот)
            zamp = 0.015 * span       # размах фолда по высоте (малый)
            wn = 0.02 * md           # ширина поперёк разреза
            npts = 80

            def _prof(t):
                s = Lf * t - 0.5 * Lf
                w = math.sin(2.0 * math.pi * t)
                u = s + amp * w
                z = zc + bedslope * s + zamp * math.sin(2.0 * math.pi * t + 0.6)
                return u, z
            prof = [_prof(t) for t in np.linspace(0.0, 1.0, npts)]

            def _node(u, z, sgn):
                return QgsPoint(cpt.x() + u * ux + sgn * wn * nx,
                                cpt.y() + u * uy + sgn * wn * ny, z)
            ntri = 0
            for i in range(len(prof) - 1):
                u0, z0 = prof[i]; u1, z1 = prof[i + 1]
                quad = (_node(u0, z0, -1), _node(u0, z0, +1),
                        _node(u1, z1, -1), _node(u1, z1, +1))
                for tri in ((quad[0], quad[1], quad[2]),
                            (quad[1], quad[3], quad[2])):
                    ring = QgsLineString(list(tri) + [tri[0]])
                    pg = QgsPolygon(); pg.setExteriorRing(ring)
                    fe = QgsFeature(tf)
                    fe.setGeometry(QgsGeometry(pg))
                    fe.setAttributes([self.tr("Складка (опрокинутая)")])
                    tsink.addFeature(fe); ntri += 1
            _set_output_name(context, tdest, self.tr("Опрокинутая TIN (демо)"))
            results[self.TIN] = tdest

        feedback.pushInfo(_tr(
            "Готово: шесть поверхностей (пять пластов: три вмещающих и два "
            "промышленных), линия и скважины. Поверхности и линию подайте в "
            "«Разрез по линии»; скважины с полями h1...h6 и линию - в «Скважины "
            "на разрез». Разлом, маркер с Z и зона - для «Пересечения векторов с "
            "разрезом», опрокинутая TIN - для «Пересечения TIN с разрезом»."))
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION_DEMO, list(results.values()), history=_provenance(self, parameters))
        return results


def _sample_grid_points(arr, gt, xs, ys, bilinear=True):
    """Выборка значений растра в точках (xs, ys). arr содержит nan на месте
    nodata. Возвращает массив z (nan вне покрытия). Билинейно или ближайший."""
    ny, nx = arr.shape
    fx = (xs - gt[0]) / gt[1] - 0.5
    fy = (ys - gt[3]) / gt[5] - 0.5

    def gather(ix, iy):
        out = np.full(len(xs), np.nan)
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        out[ok] = arr[iy[ok], ix[ok]]
        return out

    if not bilinear:
        return gather(np.round(fx).astype(int), np.round(fy).astype(int))
    x0 = np.floor(fx).astype(int)
    y0 = np.floor(fy).astype(int)
    tx = fx - x0
    ty = fy - y0
    v00 = gather(x0, y0)
    v10 = gather(x0 + 1, y0)
    v01 = gather(x0, y0 + 1)
    v11 = gather(x0 + 1, y0 + 1)
    return (v00 * (1 - tx) * (1 - ty) + v10 * tx * (1 - ty)
            + v01 * (1 - tx) * ty + v11 * tx * ty)


def _valid_runs(mask):
    """Непрерывные участки True длиной >= 2 точек: список (i0, i1) включительно."""
    out = []
    i, n = 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            if j - i >= 2:
                out.append((i, j - 1))
            i = j
        else:
            i += 1
    return out


def _section_vex(feedback, aspect_mode, scale, length, dz):
    """Множитель вертикального масштаба. В режиме Г:В считается из длины линии и
    вертикального размаха так, чтобы чертёж имел заданное отношение ширина:высота;
    иначе scale - прямой множитель. Печатает фактический vex в журнал."""
    if aspect_mode:
        vex = (length / (scale * dz)) if (dz and dz > 0 and scale > 0) else 1.0
        feedback.pushInfo(_tr(
            "Вертикальный масштаб: отношение Г:В ~ %.4g:1, множитель vex ~ %.4g.")
            % (scale, vex))
    else:
        vex = scale or 1.0
        feedback.pushInfo(_tr(
            "Вертикальный масштаб: множитель vex = %.4g.") % vex)
    return vex


def _nice_ticks(lo, hi, n):
    """Хорошо округлённые отметки между lo и hi. Шаг выбирается из ряда
    1, 2, 2.5, 5, 10 (×10^k) так, чтобы число отметок было ближе всего к n."""
    if not (hi > lo) or n < 2:
        return []
    raw = (hi - lo) / (n - 1)
    mag = 10.0 ** math.floor(math.log10(raw))
    best = None
    for f in (1, 2, 2.5, 5, 10):
        step = f * mag
        start = math.ceil(lo / step) * step
        cnt = int(math.floor((hi - start) / step + 1e-9)) + 1
        if cnt < 1:
            continue
        score = abs(cnt - n)
        if best is None or score < best[0]:
            best = (score, step)
    step = best[1]
    v = math.ceil(lo / step) * step
    ticks = []
    while v <= hi + step * 1e-6:
        ticks.append(round(v, 6))
        v += step
    return ticks


class SectionAlgorithm(QgsProcessingAlgorithm):
    """Геологический разрез по линии. На вход - линия разреза и упорядоченный
    сверху вниз набор поверхностей (кровли и подошвы из кригинга). Пласты это
    полосы между соседними поверхностями. Два выхода: 2D-чертёж в осях
    расстояние-высота (для макета и печати) и 3D-забор PolygonZ в реальных
    координатах (для 3D Map View). Свой кригинг не делает, берёт готовые
    растры-поверхности."""

    LINE, SURFACES = "LINE", "SURFACES"
    STEP, VMODE, VEXAG, SAMPLING = "STEP", "VMODE", "VEXAG", "SAMPLING"
    OUTPUT_2D, OUTPUT_3D, OUTPUT_DEF = "OUTPUT_2D", "OUTPUT_3D", "OUTPUT_DEF"
    OUTPUT_CORNERS, OUTPUT_CORNERS_V = "OUTPUT_CORNERS", "OUTPUT_CORNERS_V"
    OUTPUT_AXES, NAXES = "OUTPUT_AXES", "NAXES"
    OUTPUT_TABLE = "OUTPUT_TABLE"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionAlgorithm()
    def name(self): return "section_along_line"
    def displayName(self): return self.tr("3.01 Разрез по линии")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Геологический разрез по линии из набора поверхностей. Поверхности "
            "задаются списком и упорядочиваются сверху вниз (кровля, подошва, "
            "следующая кровля и так далее). Пласты строятся как полосы между "
            "соседними поверхностями, поэтому N поверхностей дают N−1 пластов.\n"
            "\nДва выхода. Чертёж разреза - полигоны в осях расстояние вдоль "
            "линии и высота, с вертикальным преувеличением для макета и печати. "
            "Забор 3D - те же полосы как вертикальные стенки PolygonZ в реальных "
            "координатах, для просмотра в 3D Map View рядом с поверхностями.\n\n"
            "Поверхности обычно получают кригингом (кровля, подошва пласта). "
            "Линию рисуют как обычный линейный слой. Расстояние и высота берутся "
            "в единицах карты. Свой кригинг инструмент не выполняет.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE, self.tr("Линия разреза"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.SURFACES, self.tr("Поверхности сверху вниз (кровли и подошвы)"),
            layerType=QgsProcessing.TypeRaster))
        self.addParameter(QgsProcessingParameterNumber(
            self.STEP, self.tr("Шаг выборки вдоль линии, ед. карты (0 = по ячейке)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.STEP, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_2D, self.tr("Чертёж разреза (расстояние × высота)"),
            type=QgsProcessing.TypeVectorPolygon, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Забор 3D (PolygonZ, реальные координаты)"),
            type=QgsProcessing.TypeVectorPolygon, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_DEF,
            self.tr("Определение разреза (линия с полем vex для других тулз)"),
            type=QgsProcessing.TypeVectorLine, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_CORNERS, self.tr("Угловые точки разреза (чертёж)"),
            type=QgsProcessing.TypeVectorPoint, optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_CORNERS_V, self.tr("Угловые вертикали разреза (чертёж)"),
            type=QgsProcessing.TypeVectorLine, optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_AXES, self.tr("Горизонтальные оси с отметками (чертёж)"),
            type=QgsProcessing.TypeVectorLine, optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_TABLE,
            self.tr("Таблица углов: азимут и расстояние (чертёж)"),
            type=QgsProcessing.TypeVectorPolygon, optional=True,
            createByDefault=False))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.NAXES, self.tr("Количество отметок высоты на осях"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.NAXES, 5), minValue=2, maxValue=50)))

    def _fields(self):
        f = QgsFields()
        f.append(QgsField("bed", QVariant.Int))
        f.append(QgsField("top", QVariant.String))
        f.append(QgsField("bot", QVariant.String))
        f.append(QgsField("t_mean", QVariant.Double))
        f.append(QgsField("seclen", QVariant.Double))
        return f

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.LINE, context)
        if src is None:
            raise QgsProcessingException(self.tr("Не задана линия разреза."))
        layers = self.parameterAsLayerList(parameters, self.SURFACES, context)
        if not layers or len(layers) < 2:
            raise QgsProcessingException(self.tr(
                "Нужно минимум две поверхности (кровля и подошва)."))
        step = self.parameterAsDouble(parameters, self.STEP, context)
        aspect_mode = self.parameterAsEnum(parameters, self.VMODE, context) == 0
        vscale = self.parameterAsDouble(parameters, self.VEXAG, context) or 1.0
        bilinear = self.parameterAsEnum(parameters, self.SAMPLING, context) == 0

        # линия: берём первый линейный объект
        line_geom = None
        nfeat = 0
        for ft in src.getFeatures():
            g = ft.geometry()
            if g is not None and not g.isEmpty():
                nfeat += 1
                if line_geom is None:
                    line_geom = QgsGeometry(g)
        if line_geom is None:
            raise QgsProcessingException(self.tr("В слое нет линии."))
        if nfeat > 1:
            feedback.pushWarning(_tr(
                "В слое несколько линий, разрез построен по первой."))
        length = float(line_geom.length())
        if length <= 0:
            raise QgsProcessingException(self.tr("Длина линии равна нулю."))

        # поверхности: читаем массивы, nodata -> nan
        surfs = []
        cells = []
        for lyr in layers:
            ds = gdal.Open(lyr.source())
            if ds is None:
                raise QgsProcessingException(self.tr(
                    "Не удалось открыть растр: %s") % lyr.name())
            b = ds.GetRasterBand(1)
            a = b.ReadAsArray().astype(float)
            nd = b.GetNoDataValue()
            if nd is not None:
                a = np.where(a == nd, np.nan, a)
            gt = ds.GetGeoTransform()
            ds = None
            surfs.append((a, gt, _short(lyr.name())))
            cells.append(abs(gt[1]) or 1.0)

        if step <= 0:
            step = min(cells)
        nseg = max(2, int(math.ceil(length / step)))
        d = np.linspace(0.0, length, nseg + 1)
        # вершины ломаной обязаны быть пикетами: равномерная сетка почти
        # никогда не попадает в излом, и профиль с 3D-забором срезали бы
        # угол хордой до полушага
        vpts = [(v.x(), v.y()) for v in line_geom.vertices()]
        if len(vpts) > 2:
            vd = [0.0]
            for i in range(1, len(vpts)):
                vd.append(vd[-1] + math.hypot(vpts[i][0] - vpts[i - 1][0],
                                              vpts[i][1] - vpts[i - 1][1]))
            d = np.unique(np.concatenate([d, np.array(vd)]))
            d = d[(d >= 0.0) & (d <= length)]
            tol = max(length * 1e-9, 1e-9)
            keep = np.concatenate([[True], np.diff(d) > tol])
            d = d[keep]
        xs = np.empty(len(d)); ys = np.empty(len(d))
        for i, di in enumerate(d):
            p = line_geom.interpolate(float(di))
            pt = p.asPoint()
            xs[i] = pt.x(); ys[i] = pt.y()

        zs = [_sample_grid_points(a, gt, xs, ys, bilinear)
              for (a, gt, _n) in surfs]

        allz = np.concatenate(zs)
        dz = (float(np.nanmax(allz) - np.nanmin(allz))
              if np.isfinite(allz).any() else 0.0)
        vex = _section_vex(feedback, aspect_mode, vscale, length, dz)
        if np.isfinite(allz).any():
            zmn = float(np.nanmin(allz)); zmx = float(np.nanmax(allz))
        else:
            zmn, zmx = 0.0, 1.0
        pad = 0.05 * (zmx - zmn if zmx > zmn else 1.0)
        frame_zmin, frame_zmax = zmn - pad, zmx + pad

        crs_line = src.sourceCrs()
        f2 = self._fields()
        sink2d, dest2d = self.parameterAsSink(
            parameters, self.OUTPUT_2D, context, f2,
            QgsWkbTypes.Polygon, QgsCoordinateReferenceSystem())
        sink3d, dest3d = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f2,
            QgsWkbTypes.PolygonZ, crs_line)
        fdef = QgsFields()
        fdef.append(QgsField("vex", QVariant.Double))
        fdef.append(QgsField("step", QVariant.Double))
        fdef.append(QgsField("zmin", QVariant.Double))
        fdef.append(QgsField("zmax", QVariant.Double))
        sinkdef, destdef = self.parameterAsSink(
            parameters, self.OUTPUT_DEF, context, fdef,
            QgsWkbTypes.LineString, crs_line)
        if sinkdef is not None:
            fd = QgsFeature(fdef)
            fd.setGeometry(QgsGeometry(line_geom))
            fd.setAttributes([round(vex, 6), step,
                              round(frame_zmin, 6), round(frame_zmax, 6)])
            sinkdef.addFeature(fd)

        # угловые точки/вертикали на узлах ломаной и горизонтальные оси
        # (в осях чертежа). Поля чертежа расширены на 5% вверх и вниз.
        naxes = self.parameterAsInt(parameters, self.NAXES, context) or 5
        fcorn = QgsFields()
        for nm, tp in (("num", QVariant.Int), ("name", QVariant.String),
                       ("pos", QVariant.String), ("d", QVariant.Double),
                       ("x", QVariant.Double), ("y", QVariant.Double),
                       ("az", QVariant.Double), ("label", QVariant.String)):
            fcorn.append(QgsField(nm, tp))
        faxis = QgsFields()
        faxis.append(QgsField("elev", QVariant.Double))
        faxis.append(QgsField("label", QVariant.String))
        crs0 = QgsCoordinateReferenceSystem()
        sinkc, destc = self.parameterAsSink(
            parameters, self.OUTPUT_CORNERS, context, fcorn,
            QgsWkbTypes.Point, crs0)
        sinkcv, destcv = self.parameterAsSink(
            parameters, self.OUTPUT_CORNERS_V, context, fcorn,
            QgsWkbTypes.LineString, crs0)
        sinkax, destax = self.parameterAsSink(
            parameters, self.OUTPUT_AXES, context, faxis,
            QgsWkbTypes.LineString, crs0)
        ftab = QgsFields()
        ftab.append(QgsField("kind", QVariant.String))
        ftab.append(QgsField("text", QVariant.String))
        sinktab, desttab = self.parameterAsSink(
            parameters, self.OUTPUT_TABLE, context, ftab,
            QgsWkbTypes.Polygon, crs0)
        if sinkc is not None:
            _attach_style(context, destc, _style_path("section_corners"))
        if sinkax is not None:
            _attach_style(context, destax, _style_path("section_axes"))
        if sinktab is not None:
            _attach_style(context, desttab, _style_path("section_table"))

        ytop = frame_zmax * vex
        ybot = frame_zmin * vex

        cn_name, cn_az, cn_d = [], [], []
        if sinkc is not None or sinkcv is not None or sinktab is not None:
            vx = [(v.x(), v.y()) for v in line_geom.vertices()]
            if len(vx) >= 2:
                seg = []
                for i in range(len(vx) - 1):
                    dx = vx[i + 1][0] - vx[i][0]; dy = vx[i + 1][1] - vx[i][1]
                    seg.append((math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0)
                dd = [0.0]
                for i in range(1, len(vx)):
                    dd.append(dd[-1] + math.hypot(vx[i][0] - vx[i - 1][0],
                                                  vx[i][1] - vx[i - 1][1]))
                for i in range(len(vx)):
                    az = seg[i] if i < len(seg) else seg[-1]
                    name = "УГ-%d" % (i + 1)
                    xr = round(vx[i][0], 2); yr = round(vx[i][1], 2)
                    dr = round(dd[i], 2); ar = round(az, 2)
                    cn_name.append(name); cn_az.append(ar); cn_d.append(dr)
                    botlab = "X %.2f\nY %.2f" % (xr, yr)
                    base = [i + 1, name]
                    tail = [dr, xr, yr, ar]
                    if sinkc is not None:
                        ft = QgsFeature(fcorn)
                        ft.setGeometry(QgsGeometry.fromPointXY(
                            QgsPointXY(float(dd[i]), ytop)))
                        ft.setAttributes(base + ["верх"] + tail + [name])
                        sinkc.addFeature(ft)
                        fb = QgsFeature(fcorn)
                        fb.setGeometry(QgsGeometry.fromPointXY(
                            QgsPointXY(float(dd[i]), ybot)))
                        fb.setAttributes(base + ["низ"] + tail + [botlab])
                        sinkc.addFeature(fb)
                    if sinkcv is not None:
                        fv = QgsFeature(fcorn)
                        fv.setGeometry(QgsGeometry.fromPolylineXY(
                            [QgsPointXY(float(dd[i]), ybot),
                             QgsPointXY(float(dd[i]), ytop)]))
                        fv.setAttributes(base + ["", dr, xr, yr, ar, name])
                        sinkcv.addFeature(fv)

        if sinktab is not None and len(vx) >= 2:
            # таблица под разрезом: ячейки МЕЖДУ угловыми точками (границы под
            # вертикалями), две строки - длина и азимут отрезка. Слева подписи.
            n = len(vx)
            H = ytop - ybot
            gap = 0.06 * H
            rowh = 0.07 * H
            wlbl = 0.06 * length if length > 0 else 1.0
            top = ybot - gap

            def _cell(cx0, cx1, ry0, ry1, text):
                fc = QgsFeature(ftab)
                fc.setGeometry(QgsGeometry.fromPolygonXY([[
                    QgsPointXY(cx0, ry0), QgsPointXY(cx1, ry0),
                    QgsPointXY(cx1, ry1), QgsPointXY(cx0, ry1),
                    QgsPointXY(cx0, ry0)]]))
                fc.setAttributes(["cell", text])
                sinktab.addFeature(fc)

            # левый столбец подписей: верх - d, низ - Аз
            for r, txt in ((0, "d"), (1, "Аз")):
                _cell(-wlbl, 0.0, top - (r + 1) * rowh, top - r * rowh, txt)
            # столбцы-отрезки между соседними углами
            for k in range(n - 1):
                x0 = float(dd[k]); x1 = float(dd[k + 1])
                seglen = dd[k + 1] - dd[k]
                for r, val in ((0, "%.2f" % seglen), (1, "%.2f" % seg[k])):
                    _cell(x0, x1, top - (r + 1) * rowh, top - r * rowh, val)

        if sinkax is not None:
            for z in _nice_ticks(zmn, zmx, naxes):
                fa = QgsFeature(faxis)
                fa.setGeometry(QgsGeometry.fromPolylineXY(
                    [QgsPointXY(0.0, z * vex), QgsPointXY(length, z * vex)]))
                fa.setAttributes([round(z, 2), "%.2f" % z])
                sinkax.addFeature(fa)

        nbed = 0
        for k in range(len(surfs) - 1):
            ztop, zbot = zs[k], zs[k + 1]
            valid = np.isfinite(ztop) & np.isfinite(zbot) & (ztop > zbot)
            if not valid.any():
                continue
            tname, bname = surfs[k][2], surfs[k + 1][2]
            tmean = float(np.nanmean((ztop - zbot)[valid]))
            for (i0, i1) in _valid_runs(valid):
                idx = range(i0, i1 + 1)
                ridx = range(i1, i0 - 1, -1)
                # 2D: расстояние по X, высота по Y (с преувеличением)
                if sink2d is not None:
                    ring = [QgsPointXY(float(d[i]), float(ztop[i] * vex))
                            for i in idx]
                    ring += [QgsPointXY(float(d[i]), float(zbot[i] * vex))
                             for i in ridx]
                    ring.append(QgsPointXY(ring[0].x(), ring[0].y()))
                    fa = QgsFeature(f2)
                    fa.setGeometry(QgsGeometry.fromPolygonXY([ring]))
                    fa.setAttributes([k + 1, tname, bname, tmean, length])
                    sink2d.addFeature(fa)
                # 3D: вертикальная стенка PolygonZ в реальных координатах
                if sink3d is not None:
                    pts = [QgsPoint(float(xs[i]), float(ys[i]), float(ztop[i]))
                           for i in idx]
                    pts += [QgsPoint(float(xs[i]), float(ys[i]), float(zbot[i]))
                            for i in ridx]
                    pts.append(QgsPoint(pts[0].x(), pts[0].y(), pts[0].z()))
                    poly = QgsPolygon()
                    poly.setExteriorRing(QgsLineString(pts))
                    fb = QgsFeature(f2)
                    fb.setGeometry(QgsGeometry(poly))
                    fb.setAttributes([k + 1, tname, bname, tmean, length])
                    sink3d.addFeature(fb)
            nbed += 1
            feedback.pushInfo(_tr(
                "Пласт %d (%s / %s): средняя мощность %.4g ед.")
                % (k + 1, tname, bname, tmean))

        if nbed == 0:
            raise QgsProcessingException(self.tr(
                "Линия не пересекает поверхности: разрез пуст."))
        feedback.pushInfo(_tr(
            "Разрез построен: %d пластов, длина %.4g ед., шаг %.4g.")
            % (nbed, length, step))
        res = {}
        if sink2d is not None:
            _set_output_name(context, dest2d, _tr("Разрез (чертёж)"))
            res[self.OUTPUT_2D] = dest2d
        if sink3d is not None:
            _set_output_name(context, dest3d, _tr("Разрез (3D-забор)"))
            res[self.OUTPUT_3D] = dest3d
        if sinkdef is not None:
            _set_output_name(context, destdef, _tr("Определение разреза"))
            res[self.OUTPUT_DEF] = destdef
        if sinkc is not None:
            _set_output_name(context, destc, _tr("Угловые точки разреза"))
            res[self.OUTPUT_CORNERS] = destc
        if sinkcv is not None:
            _set_output_name(context, destcv, _tr("Угловые вертикали разреза"))
            res[self.OUTPUT_CORNERS_V] = destcv
        if sinkax is not None:
            _set_output_name(context, destax, _tr("Горизонтальные оси разреза"))
            res[self.OUTPUT_AXES] = destax
        if sinktab is not None:
            _set_output_name(context, desttab, _tr("Таблица углов разреза"))
            res[self.OUTPUT_TABLE] = desttab
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, list(res.values()), force=True, history=_provenance(self, parameters))
        return res


class BoreholesOnSectionAlgorithm(QgsProcessingAlgorithm):
    """Скважины на разрез: проекция точечного слоя скважин на линию разреза.
    Каждая скважина показывается вертикальной колонкой интервалов пластов в осях
    расстояние-высота, поверх чертежа разреза. Границы пластов берутся из
    выбранных полей-отметок: на каждой скважине их значения сортируются по
    убыванию, и соседние пары дают интервалы пластов. Дальние скважины
    отсекаются коридором. То же вертикальное преувеличение, что и у разреза."""

    LINE, WELLS = "LINE", "WELLS"
    LEVELS, LABEL = "LEVELS", "LABEL"
    CORRIDOR, VMODE, VEXAG = "CORRIDOR", "VMODE", "VEXAG"
    DEF = "DEF"
    OUTPUT, OUTPUT_LABELS = "OUTPUT", "OUTPUT_LABELS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return BoreholesOnSectionAlgorithm()
    def name(self): return "boreholes_on_section"
    def displayName(self): return self.tr("3.02 Скважины на разрезе")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Проецирует скважины на линию разреза и показывает их колонками "
            "интервалов пластов в осях расстояние-высота, поверх чертежа из "
            "инструмента «Разрез по линии».\n\nГраницы пластов берутся из "
            "выбранных полей-отметок (кровли и подошвы). На каждой скважине их "
            "значения сортируются по убыванию, соседние пары дают интервалы "
            "пластов, поэтому порядок выбора полей и пропуски не важны. Каждый "
            "интервал получает номер пласта, а колонка - номер скважины.\n\n"
            "Скважина ставится на том расстоянии вдоль линии, куда падает её "
            "проекция. Дальние скважины отсекаются коридором (буфером вокруг "
            "линии). Вертикальное преувеличение задавайте таким же, как в "
            "«Разрез по линии», иначе колонки не лягут на пласты по высоте.") +
            _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE, self.tr("Линия разреза"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DEF,
            self.tr("Определение разреза (для общего масштаба, опционально)"),
            types=[QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.WELLS, self.tr("Скважины"),
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.LEVELS, self.tr("Поля отметок границ пластов (кровли и подошвы)"),
            parentLayerParameterName=self.WELLS,
            type=QgsProcessingParameterField.Numeric, allowMultiple=True))
        self.addParameter(QgsProcessingParameterField(
            self.LABEL, self.tr("Поле номера скважины (для подписи)"),
            parentLayerParameterName=self.WELLS, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CORRIDOR,
            self.tr("Коридор от линии, ед. карты (0 = все скважины)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.CORRIDOR, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Интервалы пластов скважин (чертёж)"),
            type=QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_LABELS, self.tr("Устья скважин (подписи)"),
            type=QgsProcessing.TypeVectorPoint, optional=True,
            createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        lsrc = self.parameterAsSource(parameters, self.LINE, context)
        wsrc = self.parameterAsSource(parameters, self.WELLS, context)
        if lsrc is None or wsrc is None:
            raise QgsProcessingException(self.tr("Не задана линия или скважины."))
        levels = self.parameterAsFields(parameters, self.LEVELS, context)
        if not levels or len(levels) < 2:
            raise QgsProcessingException(self.tr(
                "Нужно минимум два поля отметок (кровля и подошва)."))
        label_f = self.parameterAsString(parameters, self.LABEL, context)
        corridor = self.parameterAsDouble(parameters, self.CORRIDOR, context)
        aspect_mode = self.parameterAsEnum(parameters, self.VMODE, context) == 0
        vscale = self.parameterAsDouble(parameters, self.VEXAG, context) or 1.0

        line_geom = None
        for ft in lsrc.getFeatures():
            g = ft.geometry()
            if g is not None and not g.isEmpty():
                line_geom = QgsGeometry(g)
                break
        if line_geom is None:
            raise QgsProcessingException(self.tr("В слое нет линии."))

        f_seg = QgsFields()
        f_seg.append(QgsField("well", QVariant.String))
        f_seg.append(QgsField("bed", QVariant.Int))
        f_seg.append(QgsField("top", QVariant.Double))
        f_seg.append(QgsField("bot", QVariant.Double))
        f_seg.append(QgsField("offset", QVariant.Double))
        f_lab = QgsFields()
        f_lab.append(QgsField("well", QVariant.String))
        f_lab.append(QgsField("offset", QVariant.Double))

        crs0 = QgsCoordinateReferenceSystem()
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, f_seg,
            QgsWkbTypes.LineString, crs0)
        lsink, ldest = self.parameterAsSink(
            parameters, self.OUTPUT_LABELS, context, f_lab,
            QgsWkbTypes.Point, crs0)

        cols = []
        zmin = zmax = None
        nskip = 0
        for i, ft in enumerate(wsrc.getFeatures()):
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            off = float(line_geom.distance(g))
            if corridor > 0 and off > corridor:
                nskip += 1
                continue
            d = float(line_geom.lineLocatePoint(g))
            if d < 0:
                continue
            vals = []
            for fn in levels:
                v = ft[fn]
                if v is not None:
                    try:
                        vals.append(float(v))
                    except (TypeError, ValueError):
                        pass
            if len(vals) < 2:
                continue
            vals.sort(reverse=True)             # сверху вниз
            name = (str(ft[label_f]) if label_f and ft[label_f] is not None
                    else "%d" % (i + 1))
            cols.append((d, vals, name, off))
            lo, hi = vals[-1], vals[0]
            zmin = lo if zmin is None else min(zmin, lo)
            zmax = hi if zmax is None else max(zmax, hi)

        if not cols:
            raise QgsProcessingException(self.tr(
                "Ни одна скважина не попала в коридор или не имеет отметок."))
        length = float(line_geom.length())
        dz = (zmax - zmin) if (zmin is not None and zmax is not None) else 0.0
        vex = _section_vex(feedback, aspect_mode, vscale, length, dz)
        dsrc = self.parameterAsSource(parameters, self.DEF, context)
        if dsrc is not None:
            _ln, vdef, _st = _read_section_def(dsrc)
            if vdef:
                vex = vdef
                feedback.pushInfo(_tr(
                    "Масштаб взят из определения разреза: vex = %.4g.") % vex)

        nwell = nseg = 0
        for (d, vals, name, off) in cols:
            for k in range(len(vals) - 1):
                top, bot = vals[k], vals[k + 1]
                fa = QgsFeature(f_seg)
                fa.setGeometry(QgsGeometry.fromPolylineXY([
                    QgsPointXY(d, top * vex), QgsPointXY(d, bot * vex)]))
                fa.setAttributes([name, k + 1, top, bot, round(off, 3)])
                sink.addFeature(fa)
                nseg += 1
            if lsink is not None:
                fl = QgsFeature(f_lab)
                fl.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(d, vals[0] * vex)))
                fl.setAttributes([name, round(off, 3)])
                lsink.addFeature(fl)
            nwell += 1

        feedback.pushInfo(_tr(
            "Спроецировано скважин: %d (интервалов %d), пропущено вне коридора %d.")
            % (nwell, nseg, nskip))
        res = {self.OUTPUT: dest}
        _set_output_name(context, dest, _tr("Скважины на разрезе (интервалы)"))
        if lsink is not None:
            _set_output_name(context, ldest, _tr("Устья скважин (подписи)"))
            res[self.OUTPUT_LABELS] = ldest
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, list(res.values()), force=True, history=_provenance(self, parameters))
        return res


class CompositionOnSectionAlgorithm(QgsProcessingAlgorithm):
    """Состав пласта на разрезе: красит полосу одного пласта по гриду состава
    вдоль линии. Два режима. Непрерывное содержание (KCl, нерастворимый остаток)
    режется на тонкие срезы со средним значением - под градиентную заливку.
    Категориальный минтип/фации (сильвинит, замещение, галит) сливается в
    смежные зоны одного класса - под заливку по категориям, зоны замещения видны
    как смена цвета вдоль линии. Один пласт за раз: кровля, подошва и грид
    состава. Свой кригинг не делает."""

    LINE, TOP, BOTTOM, COMP = "LINE", "TOP", "BOTTOM", "COMP"
    TOP_BAND, BOTTOM_BAND, COMP_BAND = "TOP_BAND", "BOTTOM_BAND", "COMP_BAND"
    MODE, STEP, VMODE, VEXAG, SAMPLING = ("MODE", "STEP", "VMODE",
                                          "VEXAG", "SAMPLING")
    DEF = "DEF"
    OUTPUT_2D, OUTPUT_3D = "OUTPUT_2D", "OUTPUT_3D"

    def tr(self, s): return _tr(s)
    def createInstance(self): return CompositionOnSectionAlgorithm()
    def name(self): return "composition_on_section"
    def displayName(self): return self.tr("3.03 Состав пласта на разрезе")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Красит полосу одного пласта на разрезе по гриду состава вдоль линии. "
            "Берёт кровлю, подошву и грид состава, свой кригинг не делает.\n\n"
            "Режим «непрерывное» (содержание KCl, нерастворимый остаток): полоса "
            "режется на тонкие вертикальные срезы, каждый со средним значением, "
            "под градиентную заливку.\n\nРежим «категориальное» (минеральный тип, "
            "фации - сильвинит, замещение, галит): смежные срезы одного класса "
            "сливаются в фациальные зоны, под заливку по категориям. Зоны "
            "замещения видны как смена цвета вдоль линии.\n\nЗапускайте по "
            "каждому промышленному пласту отдельно. Вертикальное преувеличение "
            "задавайте таким же, как в «Разрез по линии».") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE, self.tr("Линия разреза"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DEF,
            self.tr("Определение разреза (для общего масштаба, опционально)"),
            types=[QgsProcessing.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.TOP, self.tr("Кровля пласта (растр)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.BOTTOM, self.tr("Подошва пласта (растр)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.COMP, self.tr("Грид состава (содержание или класс)")))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.TOP_BAND, self.tr("Канал кровли"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.TOP_BAND, 1), minValue=1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BOTTOM_BAND, self.tr("Канал подошвы"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.BOTTOM_BAND, 1), minValue=1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.COMP_BAND, self.tr("Канал состава"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.COMP_BAND, 1), minValue=1)))
        self.addParameter(QgsProcessingParameterEnum(
            self.MODE, self.tr("Состав"),
            options=[self.tr("непрерывное (содержание)"),
                     self.tr("категориальное (минтип, фации)")],
            defaultValue=_dv(self, self.MODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.STEP, self.tr("Шаг выборки вдоль линии, ед. карты (0 = по ячейке)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.STEP, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_2D, self.tr("Состав пласта (чертёж)"),
            type=QgsProcessing.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Состав пласта (3D)"),
            type=QgsProcessing.TypeVectorPolygon, optional=True,
            createByDefault=False))

    @staticmethod
    def _read(path, band=1):
        ds = gdal.Open(path)
        if ds is None or band > ds.RasterCount:
            return None, None
        b = ds.GetRasterBand(band)
        a = b.ReadAsArray().astype(float)
        nd = b.GetNoDataValue()
        if nd is not None:
            a = np.where(a == nd, np.nan, a)
        gt = ds.GetGeoTransform()
        ds = None
        return a, gt

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        lsrc = self.parameterAsSource(parameters, self.LINE, context)
        top_l = self.parameterAsRasterLayer(parameters, self.TOP, context)
        bot_l = self.parameterAsRasterLayer(parameters, self.BOTTOM, context)
        cmp_l = self.parameterAsRasterLayer(parameters, self.COMP, context)
        if not (lsrc and top_l and bot_l and cmp_l):
            raise QgsProcessingException(self.tr(
                "Нужны линия, кровля, подошва и грид состава."))
        categorical = self.parameterAsEnum(parameters, self.MODE, context) == 1
        step = self.parameterAsDouble(parameters, self.STEP, context)
        aspect_mode = self.parameterAsEnum(parameters, self.VMODE, context) == 0
        vscale = self.parameterAsDouble(parameters, self.VEXAG, context) or 1.0
        bilinear = (self.parameterAsEnum(parameters, self.SAMPLING, context) == 0
                    and not categorical)   # класс никогда не интерполируем

        line_geom = None
        for ft in lsrc.getFeatures():
            g = ft.geometry()
            if g is not None and not g.isEmpty():
                line_geom = QgsGeometry(g)
                break
        if line_geom is None:
            raise QgsProcessingException(self.tr("В слое нет линии."))
        length = float(line_geom.length())
        if length <= 0:
            raise QgsProcessingException(self.tr("Длина линии равна нулю."))

        at, gtt = self._read(top_l.source(),
                             self.parameterAsInt(parameters, self.TOP_BAND, context))
        ab, gtb = self._read(bot_l.source(),
                             self.parameterAsInt(parameters, self.BOTTOM_BAND, context))
        ac, gtc = self._read(cmp_l.source(),
                             self.parameterAsInt(parameters, self.COMP_BAND, context))
        if at is None or ab is None or ac is None:
            raise QgsProcessingException(self.tr("Не удалось открыть растр."))

        cell = min(abs(gtt[1]), abs(gtb[1]), abs(gtc[1])) or 1.0
        if step <= 0:
            step = cell
        nseg = max(2, int(math.ceil(length / step)))
        d = np.linspace(0.0, length, nseg + 1)
        xs = np.empty(len(d)); ys = np.empty(len(d))
        for i, di in enumerate(d):
            pt = line_geom.interpolate(float(di)).asPoint()
            xs[i] = pt.x(); ys[i] = pt.y()
        ztop = _sample_grid_points(at, gtt, xs, ys, True)
        zbot = _sample_grid_points(ab, gtb, xs, ys, True)
        comp = _sample_grid_points(ac, gtc, xs, ys, bilinear)

        zall = np.concatenate([ztop, zbot])
        dz = (float(np.nanmax(zall) - np.nanmin(zall))
              if np.isfinite(zall).any() else 0.0)
        vex = _section_vex(feedback, aspect_mode, vscale, length, dz)
        dsrc = self.parameterAsSource(parameters, self.DEF, context)
        if dsrc is not None:
            _ln, vdef, _st = _read_section_def(dsrc)
            if vdef:
                vex = vdef
                feedback.pushInfo(_tr(
                    "Масштаб взят из определения разреза: vex = %.4g.") % vex)

        f = QgsFields()
        f.append(QgsField("value", QVariant.Double))
        f.append(QgsField("class", QVariant.Int))
        f.append(QgsField("d0", QVariant.Double))
        f.append(QgsField("d1", QVariant.Double))
        crs_line = lsrc.sourceCrs()
        sink2d, dest2d = self.parameterAsSink(
            parameters, self.OUTPUT_2D, context, f,
            QgsWkbTypes.Polygon, QgsCoordinateReferenceSystem())
        sink3d, dest3d = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f,
            QgsWkbTypes.PolygonZ, crs_line)

        def emit(i0, i1, value, cls):
            idx = range(i0, i1 + 1); ridx = range(i1, i0 - 1, -1)
            attrs = [value, cls, float(d[i0]), float(d[i1])]
            if sink2d is not None:
                ring = [QgsPointXY(float(d[i]), float(ztop[i] * vex)) for i in idx]
                ring += [QgsPointXY(float(d[i]), float(zbot[i] * vex)) for i in ridx]
                ring.append(QgsPointXY(ring[0].x(), ring[0].y()))
                fa = QgsFeature(f); fa.setGeometry(QgsGeometry.fromPolygonXY([ring]))
                fa.setAttributes(attrs); sink2d.addFeature(fa)
            if sink3d is not None:
                pts = [QgsPoint(float(xs[i]), float(ys[i]), float(ztop[i]))
                       for i in idx]
                pts += [QgsPoint(float(xs[i]), float(ys[i]), float(zbot[i]))
                        for i in ridx]
                pts.append(QgsPoint(pts[0].x(), pts[0].y(), pts[0].z()))
                poly = QgsPolygon(); poly.setExteriorRing(QgsLineString(pts))
                fb = QgsFeature(f); fb.setGeometry(QgsGeometry(poly))
                fb.setAttributes(attrs); sink3d.addFeature(fb)

        npoly = 0
        valid = np.isfinite(ztop) & np.isfinite(zbot) & np.isfinite(comp)
        if categorical:
            cls = np.where(valid, np.round(comp), np.nan)
            i = 0; n = len(d)
            while i < n:
                if valid[i]:
                    j = i
                    while j + 1 < n and valid[j + 1] and cls[j + 1] == cls[i]:
                        j += 1
                    if j > i:
                        emit(i, j, None, int(cls[i])); npoly += 1
                    i = j + 1
                else:
                    i += 1
        else:
            for i in range(len(d) - 1):
                if valid[i] and valid[i + 1]:
                    emit(i, i + 1, float((comp[i] + comp[i + 1]) * 0.5), None)
                    npoly += 1

        if npoly == 0:
            raise QgsProcessingException(self.tr(
                "Пласт и состав не пересекают линию: результат пуст."))
        feedback.pushInfo(_tr("Состав пласта: построено полигонов %d (%s).")
                          % (npoly, self.tr("зоны") if categorical
                             else self.tr("срезы")))
        res = {}
        if sink2d is not None:
            _set_output_name(context, dest2d, _tr("Состав пласта (чертёж)"))
            res[self.OUTPUT_2D] = dest2d
        if sink3d is not None:
            _set_output_name(context, dest3d, _tr("Состав пласта (3D)"))
            res[self.OUTPUT_3D] = dest3d
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, list(res.values()), force=True, history=_provenance(self, parameters))
        return res


def _read_section_def(src, default_vex=1.0):
    """Определение разреза: геометрия первой линии плюс vex и step из полей
    (если есть). Это общий источник линии и масштаба для инструментов разреза."""
    line, vex, step = None, default_vex, 0.0
    names = [f.name().lower() for f in src.fields()]
    for ft in src.getFeatures():
        g = ft.geometry()
        if g is not None and not g.isEmpty():
            line = QgsGeometry(g)
            if "vex" in names:
                try:
                    vex = float(ft["vex"]) or default_vex
                except (TypeError, ValueError, KeyError):
                    pass
            if "step" in names:
                try:
                    step = float(ft["step"]) or 0.0
                except (TypeError, ValueError, KeyError):
                    pass
            break
    return line, vex, step


def _read_section_extent(src):
    """Вертикальный размах рамки разреза (zmin, zmax) из полей определения, если
    они есть (их пишет «Разрез по линии»). Иначе None - высоту возьмут из чертежа
    разреза или из диапазона Z."""
    names = [f.name().lower() for f in src.fields()]
    if "zmin" not in names or "zmax" not in names:
        return None
    for ft in src.getFeatures():
        try:
            zmn = float(ft["zmin"]); zmx = float(ft["zmax"])
        except (TypeError, ValueError, KeyError):
            return None
        return (zmn, zmx) if zmx > zmn else None
    return None


def _line_points(line_geom, length, step):
    """Равномерные точки вдоль линии: массивы d, xs, ys."""
    nseg = max(2, int(math.ceil(length / step)))
    d = np.linspace(0.0, length, nseg + 1)
    xs = np.empty(len(d)); ys = np.empty(len(d))
    for i, di in enumerate(d):
        pt = line_geom.interpolate(float(di)).asPoint()
        xs[i] = pt.x(); ys[i] = pt.y()
    return d, xs, ys


class SectionGridIntersectAlgorithm(QgsProcessingAlgorithm):
    """Пересечение поверхностей-гридов с разрезом. По определению разреза (линия
    и vex) каждый грид выбирается вдоль линии и ложится на чертёж линией
    высота(расстояние). Так на разрез наносят водоносные горизонты, маркирующие
    поверхности, кровлю соли, аномалии - как линии в тех же осях, что и разрез."""

    LINE_DEF, GRIDS, STEP, SAMPLING = "LINE_DEF", "GRIDS", "STEP", "SAMPLING"
    OUTPUT, OUTPUT_3D = "OUTPUT", "OUTPUT_3D"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionGridIntersectAlgorithm()
    def name(self): return "section_intersect_grids"
    def displayName(self): return self.tr("3.04 Пересечение поверхностей с разрезом")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Наносит поверхности-гриды на разрез линиями в осях расстояние-высота. "
            "Каждый грид выбирается вдоль линии разреза, и его сечение ложится на "
            "чертёж рядом с пластами.\n\nЛиния и вертикальный масштаб берутся из "
            "определения разреза (линейный слой с полем vex - его выдаёт «Разрез "
            "по линии»). Поэтому линии гридов совпадают с разрезом без ручной "
            "подгонки.\n\nГодится для водоносных горизонтов, маркирующих "
            "поверхностей, кровли соли, поверхностей аномалий.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE_DEF, self.tr("Определение разреза (линия с полем vex)"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.GRIDS, self.tr("Поверхности-гриды"),
            layerType=QgsProcessing.TypeRaster))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.STEP, self.tr("Шаг выборки вдоль линии (0 = по ячейке)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.STEP, 0.0), minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Линии поверхностей на разрезе (чертёж)"),
            type=QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Линии поверхностей (3D)"),
            type=QgsProcessing.TypeVectorLine, optional=True,
            createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.LINE_DEF, context)
        grids = self.parameterAsLayerList(parameters, self.GRIDS, context)
        if src is None or not grids:
            raise QgsProcessingException(self.tr(
                "Нужны определение разреза и хотя бы один грид."))
        step = self.parameterAsDouble(parameters, self.STEP, context)
        bilinear = self.parameterAsEnum(parameters, self.SAMPLING, context) == 0
        line, vex, dstep = _read_section_def(src)
        if line is None:
            raise QgsProcessingException(self.tr("В определении нет линии."))
        feedback.pushInfo(_tr("Множитель vex из определения: %.4g.") % vex)
        length = float(line.length())
        if step <= 0:
            step = dstep if dstep > 0 else 0.0

        f = QgsFields()
        f.append(QgsField("surface", QVariant.String))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, f,
            QgsWkbTypes.LineString, QgsCoordinateReferenceSystem())
        sink3, dest3 = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f,
            QgsWkbTypes.LineStringZ, src.sourceCrs())

        n = 0
        for lyr in grids:
            ds = gdal.Open(lyr.source())
            if ds is None:
                continue
            b = ds.GetRasterBand(1)
            arr = b.ReadAsArray().astype(float)
            nd = b.GetNoDataValue()
            if nd is not None:
                arr = np.where(arr == nd, np.nan, arr)
            gt = ds.GetGeoTransform()
            ds = None
            st = step if step > 0 else (abs(gt[1]) or 1.0)
            d, xs, ys = _line_points(line, length, st)
            z = _sample_grid_points(arr, gt, xs, ys, bilinear)
            nm = _short(lyr.name())
            for (i0, i1) in _valid_runs(np.isfinite(z)):
                idx = range(i0, i1 + 1)
                if sink is not None:
                    fa = QgsFeature(f)
                    fa.setGeometry(QgsGeometry.fromPolylineXY(
                        [QgsPointXY(float(d[i]), float(z[i] * vex)) for i in idx]))
                    fa.setAttributes([nm]); sink.addFeature(fa)
                if sink3 is not None:
                    fb = QgsFeature(f)
                    fb.setGeometry(QgsGeometry(QgsLineString(
                        [QgsPoint(float(xs[i]), float(ys[i]), float(z[i]))
                         for i in idx])))
                    fb.setAttributes([nm]); sink3.addFeature(fb)
            n += 1
        if n == 0:
            raise QgsProcessingException(self.tr("Гриды не открылись."))
        feedback.pushInfo(_tr("Нанесено поверхностей: %d.") % n)
        res = {self.OUTPUT: dest}
        _set_output_name(context, dest, _tr("Поверхности на разрезе"))
        if sink3 is not None:
            res[self.OUTPUT_3D] = dest3
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, list(res.values()), force=True, history=_provenance(self, parameters))
        return res


class SectionVectorIntersectAlgorithm(QgsProcessingAlgorithm):
    """Пересечение векторных слоёв с разрезом. По определению разреза (линия и
    vex) объекты входного слоя пересекаются с линией разреза и ложатся на чертёж:
    линия без отметки - вертикаль на всю высоту в станции; линия с отметкой Z -
    точка на реальной высоте; полигон - вертикальная полоса на интервале, где
    разрез идёт сквозь зону. В отличие от проекции (приблизительной, по коридору)
    это точное пересечение - только там, где геометрия реально режет линию."""

    LINE_DEF, TARGET, SECTION2D = "LINE_DEF", "TARGET", "SECTION2D"
    ZMIN, ZMAX = "ZMIN", "ZMAX"
    OUT_LINES, OUT_POINTS, OUT_BANDS = "OUT_LINES", "OUT_POINTS", "OUT_BANDS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionVectorIntersectAlgorithm()
    def name(self): return "section_intersect_vectors"
    def displayName(self): return self.tr("3.05 Пересечение векторов с разрезом")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Наносит векторные объекты на разрез по точному пересечению с линией "
            "разреза, в осях расстояние-высота.\n\nПравило по типу объекта. Линия "
            "БЕЗ отметки высоты (плоская в плане - разлом, граница, контур) даёт "
            "вертикаль на всю высоту в станции пересечения: известно где, "
            "неизвестно на какой глубине. Линия С отметкой (3D, координата Z - "
            "наклонный объект, контур поверхности) даёт точку на реальной высоте в "
            "месте пересечения. Полигон (зона в плане - замещение, шахтное поле, "
            "лицензия) даёт вертикальную полосу на интервале, где разрез идёт "
            "сквозь зону.\n\nЛиния и vex берутся из определения разреза. Высота "
            "рамки тоже берётся из определения (его пишет «Разрез по линии»), "
            "поэтому для объектов без Z подавать ничего не нужно. Если в "
            "определении высоты нет, она берётся из чертежа разреза или из "
            "диапазона Z в дополнительных параметрах.\n\n"
            "В отличие от «Проекции объектов на разрез» (приблизительной, по "
            "коридору) это точное пересечение.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE_DEF, self.tr("Определение разреза (линия с полем vex)"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.TARGET, self.tr("Слои для пересечения (линии и полигоны)"),
            layerType=QgsProcessing.TypeVectorAnyGeometry))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.SECTION2D,
            self.tr("Чертёж разреза (для высоты рамки, необязательно)"),
            optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZMIN, self.tr("Низ диапазона Z (если нет чертежа)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.ZMIN, 0.0), optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZMAX, self.tr("Верх диапазона Z (если нет чертежа)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.ZMAX, 0.0), optional=True)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_LINES, self.tr("Вертикали на разрезе (линии без Z)"),
            type=QgsProcessing.TypeVectorLine, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_POINTS, self.tr("Точки пересечения (линии с Z)"),
            type=QgsProcessing.TypeVectorPoint, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_BANDS, self.tr("Полосы зон на разрезе (полигоны)"),
            type=QgsProcessing.TypeVectorPolygon, optional=True,
            createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.LINE_DEF, context)
        layers = self.parameterAsLayerList(parameters, self.TARGET, context)
        if src is None or not layers:
            raise QgsProcessingException(self.tr(
                "Нужны определение разреза и хотя бы один слой для пересечения."))
        line, vex, _step = _read_section_def(src)
        if line is None:
            raise QgsProcessingException(self.tr("В определении нет линии."))
        feedback.pushInfo(_tr("Множитель vex из определения: %.4g.") % vex)

        ybot = ytop = None
        ext_def = _read_section_extent(src)
        if ext_def is not None:
            ybot, ytop = ext_def[0] * vex, ext_def[1] * vex
            feedback.pushInfo(_tr("Высота рамки из определения: %.4g..%.4g.")
                              % (ext_def[0], ext_def[1]))
        if ybot is None:
            sec2d = self.parameterAsSource(parameters, self.SECTION2D, context)
            if sec2d is not None:
                ext = sec2d.sourceExtent()
                if ext is not None and not ext.isEmpty():
                    ybot, ytop = ext.yMinimum(), ext.yMaximum()
        if ybot is None:
            zmn = self.parameterAsDouble(parameters, self.ZMIN, context)
            zmx = self.parameterAsDouble(parameters, self.ZMAX, context)
            if zmx > zmn:
                ybot, ytop = zmn * vex, zmx * vex
        have_height = ybot is not None
        scrs = src.sourceCrs()

        def _pick_label(fields):
            low = {f.name().lower(): f.name() for f in fields}
            for cand in ("name", "label", "имя", "название", "id", "num"):
                if cand in low:
                    return low[cand]
            return None

        pts, lns, bds = [], [], []
        warned_h = False
        for lyr in layers:
            if lyr is None:
                continue
            sname = _short(lyr.name())
            lf = _pick_label(lyr.fields())
            tcrs = lyr.crs()
            xform = None
            if scrs.isValid() and tcrs.isValid() and scrs != tcrs:
                xform = QgsCoordinateTransform(
                    tcrs, scrs, context.transformContext())
            for ft in lyr.getFeatures():
                if feedback.isCanceled():
                    break
                g = ft.geometry()
                if g is None or g.isEmpty():
                    continue
                if xform is not None:
                    g = QgsGeometry(g)
                    g.transform(xform)
                lab = sname
                if lf:
                    try:
                        lab = str(ft[lf])
                    except Exception:
                        lab = sname
                is_poly = (g.type() == QgsWkbTypes.PolygonGeometry)
                ag = g.constGet()
                has_z = bool(ag.is3D()) if ag is not None else False
                inter = line.intersection(g)
                if inter is None or inter.isEmpty():
                    continue
                for part in inter.asGeometryCollection():
                    pt = part.type()
                    if pt == QgsWkbTypes.PointGeometry:
                        pxy = part.asPoint()
                        d = float(line.lineLocatePoint(
                            QgsGeometry.fromPointXY(pxy)))
                        if has_z and not is_poly:
                            dz = float(g.lineLocatePoint(
                                QgsGeometry.fromPointXY(pxy)))
                            zg = g.interpolate(dz)
                            zval = (zg.vertexAt(0).z()
                                    if zg is not None and not zg.isEmpty()
                                    else float("nan"))
                            if zval == zval:
                                pts.append((d, float(zval), sname, lab))
                        elif have_height:
                            lns.append((d, sname, lab))
                        else:
                            warned_h = True
                    elif pt == QgsWkbTypes.LineGeometry:
                        poly = part.asPolyline()
                        if len(poly) < 2:
                            continue
                        d1 = float(line.lineLocatePoint(
                            QgsGeometry.fromPointXY(poly[0])))
                        d2 = float(line.lineLocatePoint(
                            QgsGeometry.fromPointXY(poly[-1])))
                        a, b = min(d1, d2), max(d1, d2)
                        if not have_height:
                            warned_h = True
                        elif b > a:
                            bds.append((a, b, sname, lab))
        if warned_h:
            feedback.pushWarning(_tr(
                "Для объектов без отметки Z нужна высота рамки. Возьмите "
                "определение от «Разрез по линии» (в нём уже есть высота) либо "
                "подайте чертёж разреза или задайте диапазон Z. Такие объекты "
                "пропущены."))
        feedback.pushInfo(_tr("Пересечения: точек %d, вертикалей %d, полос %d.")
                          % (len(pts), len(lns), len(bds)))

        empty = QgsCoordinateReferenceSystem()
        res = {}
        if pts:
            fpoints = QgsFields()
            fpoints.append(QgsField("src", QVariant.String))
            fpoints.append(QgsField("label", QVariant.String))
            fpoints.append(QgsField("d", QVariant.Double))
            fpoints.append(QgsField("z", QVariant.Double))
            sp, dp = self.parameterAsSink(parameters, self.OUT_POINTS, context,
                                          fpoints, QgsWkbTypes.Point, empty)
            if sp is not None:
                for d, z, sname, lab in pts:
                    fa = QgsFeature(fpoints)
                    fa.setGeometry(QgsGeometry.fromPointXY(
                        QgsPointXY(d, z * vex)))
                    fa.setAttributes([sname, lab, d, z])
                    sp.addFeature(fa)
                res[self.OUT_POINTS] = dp
                _set_output_name(context, dp, _tr("Точки на разрезе"))
                _attach_style(context, dp, _style_path("section_vpoints"))
        if lns:
            flines = QgsFields()
            flines.append(QgsField("src", QVariant.String))
            flines.append(QgsField("label", QVariant.String))
            flines.append(QgsField("d", QVariant.Double))
            sl, dl = self.parameterAsSink(parameters, self.OUT_LINES, context,
                                          flines, QgsWkbTypes.LineString, empty)
            if sl is not None:
                for d, sname, lab in lns:
                    fa = QgsFeature(flines)
                    fa.setGeometry(QgsGeometry.fromPolylineXY(
                        [QgsPointXY(d, ybot), QgsPointXY(d, ytop)]))
                    fa.setAttributes([sname, lab, d])
                    sl.addFeature(fa)
                res[self.OUT_LINES] = dl
                _set_output_name(context, dl, _tr("Вертикали на разрезе"))
                _attach_style(context, dl, _style_path("section_vlines"))
        if bds:
            fbands = QgsFields()
            fbands.append(QgsField("src", QVariant.String))
            fbands.append(QgsField("label", QVariant.String))
            fbands.append(QgsField("d1", QVariant.Double))
            fbands.append(QgsField("d2", QVariant.Double))
            sb, db = self.parameterAsSink(parameters, self.OUT_BANDS, context,
                                          fbands, QgsWkbTypes.Polygon, empty)
            if sb is not None:
                for a, b, sname, lab in bds:
                    fa = QgsFeature(fbands)
                    fa.setGeometry(QgsGeometry.fromPolygonXY([[
                        QgsPointXY(a, ybot), QgsPointXY(b, ybot),
                        QgsPointXY(b, ytop), QgsPointXY(a, ytop),
                        QgsPointXY(a, ybot)]]))
                    fa.setAttributes([sname, lab, a, b])
                    sb.addFeature(fa)
                res[self.OUT_BANDS] = db
                _set_output_name(context, db, _tr("Полосы зон на разрезе"))
                _attach_style(context, db, _style_path("section_vbands"))
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, list(res.values()), force=True,
                   history=_provenance(self, parameters))
        return res


class SectionTinIntersectAlgorithm(QgsProcessingAlgorithm):
    """Пересечение TIN (3D-граней) с разрезом. В отличие от растрового грида TIN
    из настоящих 3D-треугольников может нависать и опрокидываться: над одной
    станцией несколько отметок, и трасса заворачивается. Каждый треугольник
    режется вертикальной шторой разреза, отрезки собираются в трассу в осях
    расстояние-высота."""

    LINE_DEF, FACES, MESH = "LINE_DEF", "FACES", "MESH"
    OUTPUT, OUTPUT_3D = "OUTPUT", "OUTPUT_3D"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionTinIntersectAlgorithm()
    def name(self): return "section_intersect_tin"
    def displayName(self): return self.tr("3.06 Пересечение TIN с разрезом")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Режет TIN (поверхность из 3D-треугольников) разрезом и кладёт трассу "
            "на чертёж в осях расстояние-высота.\n\nГлавное отличие от "
            "«Пересечения поверхностей» (3.04, гриды): грид это z = f(x,y), одно "
            "значение на точку, опрокинутое он не возьмёт. TIN из настоящих "
            "3D-граней может нависать: над одной станцией несколько отметок, и "
            "трасса заворачивается - складки с опрокинутыми крыльями ложатся как "
            "есть.\n\nВход - слои 3D-полигонов (PolygonZ, грани TIN; не "
            "треугольники разбиваются веером) и/или меш-слой. Линия и vex берутся "
            "из определения разреза, высота - с самих граней, поэтому для TIN "
            "ничего задавать не нужно.\n\nВнимание: меш QGIS это 2.5D (z как "
            "скаляр на вершине), опрокинутое в нём не представимо. Нависание дают "
            "только настоящие 3D-грани от геомоделлера.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE_DEF, self.tr("Определение разреза (линия с полем vex)"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FACES, self.tr("Грани TIN (слои 3D-полигонов, PolygonZ)"),
            layerType=QgsProcessing.TypeVectorPolygon, optional=True))
        self.addParameter(QgsProcessingParameterMeshLayer(
            self.MESH, self.tr("Меш-слой (2.5D, для общности)"), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Трасса TIN на разрезе (чертёж)"),
            type=QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Трасса TIN (3D)"),
            type=QgsProcessing.TypeVectorLine, optional=True,
            createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.LINE_DEF, context)
        faces = self.parameterAsLayerList(parameters, self.FACES, context) or []
        mesh = self.parameterAsMeshLayer(parameters, self.MESH, context)
        if src is None or (not faces and mesh is None):
            raise QgsProcessingException(self.tr(
                "Нужны определение разреза и хотя бы один слой граней или меш."))
        line, vex, _step = _read_section_def(src)
        if line is None:
            raise QgsProcessingException(self.tr("В определении нет линии."))
        feedback.pushInfo(_tr("Множитель vex из определения: %.4g.") % vex)
        poly_xy = [(v.x(), v.y()) for v in line.vertices()]
        scrs = src.sourceCrs()

        from .kb2d import tin_section_trace, fan_triangulate

        n_tri = 0
        segs = []

        def _emit(tris, sname):
            for s in tin_section_trace(poly_xy, tris):
                segs.append((s[0], s[1], s[2], s[3], sname))
            return len(tris)

        for lyr in faces:
            if lyr is None:
                continue
            sname = _short(lyr.name())
            tcrs = lyr.crs()
            xform = None
            if scrs.isValid() and tcrs.isValid() and scrs != tcrs:
                xform = QgsCoordinateTransform(
                    tcrs, scrs, context.transformContext())
            tris = []
            had3d = False
            for ft in lyr.getFeatures():
                if feedback.isCanceled():
                    break
                g = ft.geometry()
                if g is None or g.isEmpty():
                    continue
                if xform is not None:
                    g = QgsGeometry(g)
                    g.transform(xform)
                for part in g.asGeometryCollection():
                    cg = part.constGet()
                    if cg is None or not hasattr(cg, "exteriorRing"):
                        continue
                    if cg.is3D():
                        had3d = True
                    ring = cg.exteriorRing()
                    if ring is None:
                        continue
                    pts = [(p.x(), p.y(), p.z()) for p in ring.points()]
                    if len(pts) < 3:
                        continue
                    tris.extend(fan_triangulate(pts))
            if not had3d:
                feedback.pushWarning(_tr(
                    "Слой «%s» без 3D-полигонов (нет Z) - пропущен.")
                    % lyr.name())
                continue
            n_tri += _emit(tris, sname)

        if mesh is not None:
            try:
                mesh.updateTriangularMesh()
                tm = mesh.triangularMesh()
                tcrs = mesh.crs()
                xform = None
                if scrs.isValid() and tcrs.isValid() and scrs != tcrs:
                    xform = QgsCoordinateTransform(
                        tcrs, scrs, context.transformContext())
                vv = []
                for p in tm.vertices():
                    if xform is not None:
                        q = xform.transform(QgsPointXY(p.x(), p.y()))
                        vv.append((q.x(), q.y(), p.z()))
                    else:
                        vv.append((p.x(), p.y(), p.z()))
                tris = [(vv[f[0]], vv[f[1]], vv[f[2]])
                        for f in tm.triangles() if len(f) >= 3]
                n_tri += _emit(tris, _short(mesh.name()))
            except Exception as exc:
                feedback.pushWarning(_tr("Меш не прочитан: %s") % str(exc))

        feedback.pushInfo(_tr("Граней обработано: %d, сегментов трассы: %d.")
                          % (n_tri, len(segs)))
        if not segs:
            feedback.pushWarning(_tr(
                "Трасса пуста: TIN не пересекает линию разреза или нет 3D-граней."))

        f = QgsFields()
        f.append(QgsField("src", QVariant.String))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, f,
            QgsWkbTypes.LineString, QgsCoordinateReferenceSystem())
        sink3, dest3 = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f,
            QgsWkbTypes.LineStringZ, scrs)
        for d0, z0, d1, z1, sname in segs:
            if sink is not None:
                fa = QgsFeature(f)
                fa.setGeometry(QgsGeometry.fromPolylineXY(
                    [QgsPointXY(d0, z0 * vex), QgsPointXY(d1, z1 * vex)]))
                fa.setAttributes([sname]); sink.addFeature(fa)
            if sink3 is not None:
                p0 = line.interpolate(d0).asPoint()
                p1 = line.interpolate(d1).asPoint()
                fb = QgsFeature(f)
                fb.setGeometry(QgsGeometry(QgsLineString(
                    [QgsPoint(p0.x(), p0.y(), z0),
                     QgsPoint(p1.x(), p1.y(), z1)])))
                fb.setAttributes([sname]); sink3.addFeature(fb)

        res = {self.OUTPUT: dest}
        _set_output_name(context, dest, _tr("Трасса TIN на разрезе"))
        _attach_style(context, dest, _style_path("section_tin"))
        if sink3 is not None:
            res[self.OUTPUT_3D] = dest3
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, list(res.values()), force=True,
                   history=_provenance(self, parameters))
        return res


class SectionProjectAlgorithm(QgsProcessingAlgorithm):
    """Проекция объектов на разрез. Точки, линии и полигоны проецируются на линию
    разреза: горизонтальная координата - расстояние вдоль линии до проекции,
    высота - отметка вершины (из 3D-геометрии или из поля). Результат в осях
    разреза, поверх чертежа. Обобщение проекции скважин на любые объекты."""

    LINE_DEF, INPUT, ZFIELD, CORRIDOR, OUTPUT = (
        "LINE_DEF", "INPUT", "ZFIELD", "CORRIDOR", "OUTPUT")

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionProjectAlgorithm()
    def name(self): return "section_project_objects"
    def displayName(self): return self.tr("3.07 Проекция объектов на разрез (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Проецирует объекты (точки, линии, полигоны) на разрез. Для каждой "
            "вершины горизонталь - расстояние вдоль линии до её проекции, высота "
            "- отметка из 3D-геометрии или из выбранного поля.\n\nЛиния и "
            "вертикальный масштаб берутся из определения разреза. Дальние объекты "
            "отсекаются коридором. Результат в тех же осях, что и чертёж разреза, "
            "кладётся поверх него.\n\nТак на разрез наносят аномалии, точки "
            "опробования, трассы, контуры - всё, что нужно увидеть в плоскости "
            "разреза.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE_DEF, self.tr("Определение разреза (линия с полем vex)"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Объекты для проекции")))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле отметки (если геометрия без Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CORRIDOR, self.tr("Коридор от линии (0 = все объекты)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.CORRIDOR, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Объекты на разрезе (чертёж)")))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.LINE_DEF, context)
        isrc = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None or isrc is None:
            raise QgsProcessingException(self.tr("Нужны определение и объекты."))
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        corridor = self.parameterAsDouble(parameters, self.CORRIDOR, context)
        line, vex, _st = _read_section_def(src)
        if line is None:
            raise QgsProcessingException(self.tr("В определении нет линии."))
        feedback.pushInfo(_tr("Множитель vex из определения: %.4g.") % vex)

        gtype = QgsWkbTypes.geometryType(isrc.wkbType())
        wkb = {0: QgsWkbTypes.Point, 1: QgsWkbTypes.LineString,
               2: QgsWkbTypes.Polygon}.get(gtype, QgsWkbTypes.Point)
        fields = QgsFields(isrc.fields())
        fields.append(QgsField("offset", QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, wkb,
            QgsCoordinateReferenceSystem())

        def proj_xy(x, y, z):
            dd = float(line.lineLocatePoint(
                QgsGeometry.fromPointXY(QgsPointXY(x, y))))
            return QgsPointXY(dd, z * vex)

        nfeat = nskip = 0
        for ft in isrc.getFeatures():
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            off = float(line.distance(g))
            if corridor > 0 and off > corridor:
                nskip += 1
                continue
            zc = None
            if zfield and ft[zfield] is not None:
                try:
                    zc = float(ft[zfield])
                except (TypeError, ValueError):
                    zc = None
            verts = []
            for v in g.vertices():
                z = zc if zc is not None else v.z()
                if z != z:        # nan
                    z = 0.0
                verts.append(proj_xy(v.x(), v.y(), z))
            if not verts:
                continue
            if wkb == QgsWkbTypes.Point:
                geom = QgsGeometry.fromPointXY(verts[0])
            elif wkb == QgsWkbTypes.LineString:
                geom = QgsGeometry.fromPolylineXY(verts)
            else:
                ring = verts + ([verts[0]] if verts[0] != verts[-1] else [])
                geom = QgsGeometry.fromPolygonXY([ring])
            fa = QgsFeature(fields)
            fa.setGeometry(geom)
            fa.setAttributes(ft.attributes() + [round(off, 3)])
            sink.addFeature(fa)
            nfeat += 1
        if nfeat == 0:
            raise QgsProcessingException(self.tr(
                "Ни один объект не спроецирован (коридор или геометрия)."))
        feedback.pushInfo(_tr(
            "Спроецировано объектов: %d, пропущено вне коридора %d.")
            % (nfeat, nskip))
        _set_output_name(context, dest, _tr("Объекты на разрезе"))
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, [dest], force=True, history=_provenance(self, parameters))
        return {self.OUTPUT: dest}


class SectionUnprojectAlgorithm(QgsProcessingAlgorithm):
    """Спроецировать с разреза. Объекты, нарисованные на чертеже разреза (оси
    расстояние-высота), возвращаются в реальные координаты: горизонталь читается
    как расстояние вдоль линии (точка на линии даёт X, Y), высота - как отметка
    Z = высота / vex. Так нарисованный на разрезе объект попадает в план и в 3D."""

    LINE_DEF, INPUT, OUTPUT = "LINE_DEF", "INPUT", "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionUnprojectAlgorithm()
    def name(self): return "section_unproject"
    def displayName(self): return self.tr("3.08 Спроецировать с разреза (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Возвращает объекты, нарисованные на чертеже разреза, в реальные "
            "координаты. Горизонтальная координата вершины читается как "
            "расстояние вдоль линии (даёт план X, Y), высота - как отметка "
            "Z = высота / vex.\n\nЛиния и vex берутся из определения разреза - "
            "того же, по которому строился чертёж. Геометрия выходит с отметкой "
            "Z в реальной системе координат.\n\nТак нарисованный на разрезе "
            "объект (контур залежи, нарушение, граница) попадает обратно в план "
            "и в 3D.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINE_DEF, self.tr("Определение разреза (линия с полем vex)"),
            types=[QgsProcessing.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Объекты с чертежа разреза")))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Объекты в плане (с отметкой Z)")))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.LINE_DEF, context)
        isrc = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None or isrc is None:
            raise QgsProcessingException(self.tr("Нужны определение и объекты."))
        line, vex, _st = _read_section_def(src)
        if line is None:
            raise QgsProcessingException(self.tr("В определении нет линии."))
        if vex == 0:
            vex = 1.0
        feedback.pushInfo(_tr("Множитель vex из определения: %.4g.") % vex)
        length = float(line.length())

        gtype = QgsWkbTypes.geometryType(isrc.wkbType())
        wkb = {0: QgsWkbTypes.PointZ, 1: QgsWkbTypes.LineStringZ,
               2: QgsWkbTypes.PolygonZ}.get(gtype, QgsWkbTypes.PointZ)
        fields = QgsFields(isrc.fields())
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, wkb, src.sourceCrs())

        def back(px, py):
            dd = min(max(px, 0.0), length)
            p = line.interpolate(dd).asPoint()
            return QgsPoint(p.x(), p.y(), py / vex)

        n = 0
        for ft in isrc.getFeatures():
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            pts = [back(v.x(), v.y()) for v in g.vertices()]
            if not pts:
                continue
            if wkb == QgsWkbTypes.PointZ:
                geom = QgsGeometry(pts[0])
            elif wkb == QgsWkbTypes.LineStringZ:
                geom = QgsGeometry(QgsLineString(pts))
            else:
                if pts[0].x() != pts[-1].x() or pts[0].y() != pts[-1].y():
                    pts.append(QgsPoint(pts[0].x(), pts[0].y(), pts[0].z()))
                poly = QgsPolygon(); poly.setExteriorRing(QgsLineString(pts))
                geom = QgsGeometry(poly)
            fa = QgsFeature(fields)
            fa.setGeometry(geom)
            fa.setAttributes(ft.attributes())
            sink.addFeature(fa)
            n += 1
        if n == 0:
            raise QgsProcessingException(self.tr("Нет объектов для проекции."))
        feedback.pushInfo(_tr("Возвращено в план объектов: %d.") % n)
        _set_output_name(context, dest, _tr("Объекты с разреза в плане"))
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, [dest], force=True, history=_provenance(self, parameters))
        return {self.OUTPUT: dest}


class ShaftUnwrapAlgorithm(QgsProcessingAlgorithm):
    """Развёртка стенки шахтного ствола. Цилиндрический разрез: вокруг оси ствола
    на заданном радиусе берётся окружность с угловым шагом, и поверхности-гриды
    выбираются по ней. Развёртка ложится в оси длина дуги - высота: каждая
    маркирующая поверхность даёт линию пересечения со стенкой ствола."""

    AXIS, RADIUS, SURFACES = "AXIS", "RADIUS", "SURFACES"
    ASTEP, VMODE, VEXAG, SAMPLING = "ASTEP", "VMODE", "VEXAG", "SAMPLING"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return ShaftUnwrapAlgorithm()
    def name(self): return "shaft_unwrap"
    def displayName(self): return self.tr("3.09 Развёртка стенки ствола (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Цилиндрический разрез - развёртка стенки шахтного ствола. Вокруг оси "
            "ствола на заданном радиусе берётся окружность с угловым шагом (по "
            "умолчанию 1 градус), и поверхности-гриды выбираются вдоль неё.\n\n"
            "Развёртка ложится в оси длина дуги по окружности - высота. Каждая "
            "маркирующая поверхность даёт линию своего пересечения со стенкой "
            "ствола: при падении пластов линии наклонены и волнисты.\n\nОсь "
            "задаётся точечным слоем (устье), радиус - в единицах карты. "
            "Вертикальный масштаб как у разреза.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.AXIS, self.tr("Ось ствола (точка устья)"),
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Радиус ствола, ед. карты"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.RADIUS, 4.0), minValue=0.001))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.SURFACES, self.tr("Поверхности-гриды (маркирующие)"),
            layerType=QgsProcessing.TypeRaster))
        self.addParameter(QgsProcessingParameterNumber(
            self.ASTEP, self.tr("Угловой шаг, градусы"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.ASTEP, 1.0), minValue=0.1, maxValue=45.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Развёртка стенки (дуга × высота)"),
            type=QgsProcessing.TypeVectorLine))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        asrc = self.parameterAsSource(parameters, self.AXIS, context)
        grids = self.parameterAsLayerList(parameters, self.SURFACES, context)
        if asrc is None or not grids:
            raise QgsProcessingException(self.tr("Нужны ось и поверхности."))
        r = self.parameterAsDouble(parameters, self.RADIUS, context)
        astep = self.parameterAsDouble(parameters, self.ASTEP, context) or 1.0
        aspect_mode = self.parameterAsEnum(parameters, self.VMODE, context) == 0
        vscale = self.parameterAsDouble(parameters, self.VEXAG, context) or 1.0
        bilinear = self.parameterAsEnum(parameters, self.SAMPLING, context) == 0

        x0 = y0 = None
        for ft in asrc.getFeatures():
            g = ft.geometry()
            if g is not None and not g.isEmpty():
                p = g.asPoint(); x0, y0 = p.x(), p.y(); break
        if x0 is None:
            raise QgsProcessingException(self.tr("В слое оси нет точки."))

        ang = np.arange(0.0, 360.0 + astep * 0.5, astep)
        rad = np.radians(ang)
        xs = x0 + r * np.cos(rad)
        ys = y0 + r * np.sin(rad)
        arc = rad * r                       # длина дуги по окружности
        circ = 2.0 * math.pi * r

        zlist = []
        names = []
        for lyr in grids:
            ds = gdal.Open(lyr.source())
            if ds is None:
                continue
            b = ds.GetRasterBand(1)
            arr = b.ReadAsArray().astype(float)
            nd = b.GetNoDataValue()
            if nd is not None:
                arr = np.where(arr == nd, np.nan, arr)
            gt = ds.GetGeoTransform(); ds = None
            zlist.append(_sample_grid_points(arr, gt, xs, ys, bilinear))
            names.append(_short(lyr.name()))
        if not zlist:
            raise QgsProcessingException(self.tr("Гриды не открылись."))
        allz = np.concatenate(zlist)
        dz = (float(np.nanmax(allz) - np.nanmin(allz))
              if np.isfinite(allz).any() else 0.0)
        vex = _section_vex(feedback, aspect_mode, vscale, circ, dz)

        f = QgsFields()
        f.append(QgsField("surface", QVariant.String))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, f,
            QgsWkbTypes.LineString, QgsCoordinateReferenceSystem())
        for nm, z in zip(names, zlist):
            for (i0, i1) in _valid_runs(np.isfinite(z)):
                fa = QgsFeature(f)
                fa.setGeometry(QgsGeometry.fromPolylineXY(
                    [QgsPointXY(float(arc[i]), float(z[i] * vex))
                     for i in range(i0, i1 + 1)]))
                fa.setAttributes([nm]); sink.addFeature(fa)
        feedback.pushInfo(_tr(
            "Развёртка: поверхностей %d, окружность %.4g ед, шаг %.4g градусов.")
            % (len(names), circ, astep))
        _set_output_name(context, dest, _tr("Развёртка стенки ствола"))
        _save_values(self, _saved)
        _set_group(context, GRP_SECTION, [dest], force=True, history=_provenance(self, parameters))
        return {self.OUTPUT: dest}


class SequentialGaussianSimAlgorithm(QgsProcessingAlgorithm):
    INPUT, FIELD = "INPUT", "FIELD"
    CELL_SIZE, EXTENT = "CELL_SIZE", "EXTENT"
    NREAL, MODEL = "NREAL", "MODEL"
    MAX_POINTS, RADIUS, SEED = "MAX_POINTS", "RADIUS", "SEED"
    THRESHOLD, ABOVE = "THRESHOLD", "ABOVE"
    OUT_MEAN, OUT_STD = "OUT_MEAN", "OUT_STD"
    OUT_P10, OUT_P50, OUT_P90 = "OUT_P10", "OUT_P50", "OUT_P90"
    OUT_PROB = "OUT_PROB"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SequentialGaussianSimAlgorithm()
    def name(self): return "sgsim"
    def displayName(self): return self.tr("2.6 Гауссова симуляция (SGS)")
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID
    def helpUrl(self): return _help_url()

    def shortHelpString(self):
        return _help_version(self.tr(
            "Последовательная гауссова симуляция: ансамбль равновероятных "
            "реализаций вместо одной сглаженной оценки кригинга. Каждая "
            "реализация воспроизводит гистограмму и вариограмму данных и проходит "
            "через скважины, поэтому по набору реализаций видна "
            "НЕОПРЕДЕЛЁННОСТЬ - разброс, квантили P10/P50/P90, вероятность "
            "превышения отсечки. Там, где реализации расходятся, оценка слабая.\n\n"
            "Вариограмма нормальных баллов подбирается автоматически. Выходы - "
            "растры: среднее по ансамблю (E-type), стандартное отклонение "
            "(неопределённость), квантили P10/P50/P90 и при заданном пороге карта "
            "вероятности превышения. Время растёт с размером грида и числом "
            "реализаций - начинайте с грубой ячейки и 50-100 реализаций."))

    def initAlgorithm(self, config=None):
        _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точки (скважины)"),
            types=[QgsProcessing.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.FIELD, self.tr("Поле значения"), parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.Numeric))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Размер ячейки (0 = авто, min(охват)/50)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.CELL_SIZE, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.NREAL, self.tr("Количество реализаций"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.NREAL, 60), minValue=1, maxValue=1000))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD, self.tr("Порог отсечки для вероятности (опционально)"),
            QgsProcessingParameterNumber.Double, optional=True))
        self.addParameter(QgsProcessingParameterBoolean(
            self.ABOVE, self.tr("Вероятность ВЫШЕ порога (иначе ниже)"),
            defaultValue=True))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Охват растра (по умолчанию - по слою)"),
            optional=True))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.MODEL, self.tr("Модель вариограммы баллов"),
            options=[self.tr("авто"), self.tr("сферическая"),
                     self.tr("экспоненциальная"), self.tr("гауссова")],
            defaultValue=_dv(self, self.MODEL, 0))))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_POINTS, self.tr("Макс. число соседей на узел"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.MAX_POINTS, 16), minValue=2, maxValue=64)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Радиус поиска (0 = авто, 3 радиуса вариограммы)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.RADIUS, 0.0), minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно ГСЧ (0 = случайное)"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.SEED, 0), minValue=0)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_MEAN, self.tr("Среднее по ансамблю (E-type)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_STD, self.tr("Стандартное отклонение (неопределённость)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_P10, self.tr("Квантиль P10")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_P50, self.tr("Медиана P50")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_P90, self.tr("Квантиль P90")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_PROB, self.tr("Вероятность превышения порога"),
            optional=True, createByDefault=False))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        field = self.parameterAsString(parameters, self.FIELD, context)
        if source is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))
        xd, yd, vrd = _read_points(source, field, feedback)
        if len(xd) < 8:
            raise QgsProcessingException(self.tr(
                "Слишком мало точек для симуляции (нужно хотя бы 8)."))
        xd = np.asarray(xd); yd = np.asarray(yd); vrd = np.asarray(vrd)

        crs = source.sourceCrs()
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if rect is None or rect.isEmpty():
            rect = source.sourceExtent()
        xmin, ymin = rect.xMinimum(), rect.yMinimum()
        width, height = rect.xMaximum() - xmin, rect.yMaximum() - ymin
        cell = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
        if cell <= 0:
            cell = (min(width, height) / 50.0) or 1.0
        nx = max(int(math.ceil(width / cell)), 1)
        ny = max(int(math.ceil(height / cell)), 1)
        xmn, ymn = xmin + 0.5 * cell, ymin + 0.5 * cell
        nreal = self.parameterAsInt(parameters, self.NREAL, context)
        ndmax = self.parameterAsInt(parameters, self.MAX_POINTS, context)
        seed = self.parameterAsInt(parameters, self.SEED, context)
        seed = None if seed <= 0 else int(seed)
        feedback.pushInfo(_tr("Сетка %d x %d, ячейка %.4g, реализаций %d.")
                          % (nx, ny, cell, nreal))
        if nreal * nx * ny * 4 > 400 * 2 ** 20:
            feedback.pushWarning(_tr(
                "Ансамбль крупный (>400 МБ в памяти). Уменьшите число реализаций "
                "или огрубите ячейку, если не хватит памяти."))

        from .kb2d import (nscore_transform, experimental_variogram,
                           fit_variogram, Variogram, sgsim)
        ns, _sv, _sns = nscore_transform(vrd)
        ev = experimental_variogram(xd, yd, ns, n_lags=15)
        mchoice = self.parameterAsEnum(parameters, self.MODEL, context)
        marg = "auto" if mchoice == 0 else (mchoice - 1)
        fit = fit_variogram(ev["lag"], ev["gamma"], ev["npairs"],
                            model=marg, sill_cap=1.2)
        if fit is None:
            raise QgsProcessingException(self.tr(
                "Не удалось подобрать вариограмму нормальных баллов "
                "(мало точек или нет структуры)."))
        vg = Variogram(fit["nugget"], [{
            "it": fit["model"] + 1, "cc": fit["sill"], "aa": fit["range"],
            "ang": 0.0, "anis": 1.0}])
        feedback.pushInfo(_tr(
            "Вариограмма баллов: %s, наггет %.3f, порог %.3f, радиус %.4g (R2=%.2f).")
            % (MODEL_LABELS[fit["model"]], fit["nugget"], fit["sill"],
               fit["range"], fit["r2"]))
        radius = self.parameterAsDouble(parameters, self.RADIUS, context)
        if radius <= 0:
            radius = min(3.0 * fit["range"], math.hypot(width, height) or 1e12)
        rad2 = radius * radius

        def prog(done, total):
            if feedback.isCanceled():
                raise QgsProcessingException(_tr("Прервано пользователем."))
            feedback.setProgress(int(92.0 * done / max(total, 1)))

        real = sgsim(xd, yd, vrd, vg, xmn, ymn, cell, nx, ny, nreal,
                     ndmin=1, ndmax=ndmax, rad2=rad2, seed=seed, progress=prog)

        mean = real.mean(axis=0).astype(np.float32)
        std = real.std(axis=0).astype(np.float32)
        p10, p50, p90 = np.quantile(real, [0.1, 0.5, 0.9], axis=0).astype(np.float32)
        thr_raw = parameters.get(self.THRESHOLD)
        prob = None
        if thr_raw is not None:
            thr = self.parameterAsDouble(parameters, self.THRESHOLD, context)
            above = self.parameterAsBoolean(parameters, self.ABOVE, context)
            ind = (real > thr) if above else (real < thr)
            prob = ind.mean(axis=0).astype(np.float32)

        geotr = (xmin, cell, 0.0, ymin + ny * cell, 0.0, -cell)
        wkt = None
        if crs is not None and crs.isValid():
            srs = osr.SpatialReference(); srs.ImportFromWkt(crs.toWkt())
            wkt = srs.ExportToWkt()
        drv = gdal.GetDriverByName("GTiff")
        opt = ["COMPRESS=LZW", "TILED=YES"]

        def _write(path, arr):
            ds = drv.Create(path, nx, ny, 1, gdal.GDT_Float32, options=opt)
            ds.SetGeoTransform(geotr)
            if wkt:
                ds.SetProjection(wkt)
            b = ds.GetRasterBand(1)
            b.WriteArray(arr); b.FlushCache()
            ds = None

        res = {}
        outs = [(self.OUT_MEAN, mean, _tr("SGS среднее (E-type)")),
                (self.OUT_STD, std, _tr("SGS стандартное отклонение")),
                (self.OUT_P10, p10, _tr("SGS P10")),
                (self.OUT_P50, p50, _tr("SGS медиана P50")),
                (self.OUT_P90, p90, _tr("SGS P90"))]
        for key, arr, label in outs:
            path = self.parameterAsOutputLayer(parameters, key, context)
            _write(path, arr)
            _set_output_name(context, path, label)
            res[key] = path
        if prob is not None:
            path = self.parameterAsOutputLayer(parameters, self.OUT_PROB, context)
            _write(path, prob)
            _set_output_name(context, path, _tr("SGS вероятность превышения"))
            res[self.OUT_PROB] = path

        _save_values(self, _saved)
        feedback.setProgress(100)
        _set_group(context, GRP_SIM, list(res.values()),
                   history=_provenance(self, parameters))
        return res


class _Mesh3DPostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Включает mesh-слою 3D-отображение, если сборка QGIS поддерживает 3D.
    Qt и qgis._3d лениво и под защитой: в headless и в сборках без 3D просто
    ничего не делает."""
    def postProcessLayer(self, layer, context, feedback):
        try:
            from qgis._3d import QgsMeshLayer3DRenderer, QgsMesh3DSymbol
            sym = QgsMesh3DSymbol()
            try:
                sym.setSmoothedTriangles(True)
            except Exception:
                pass
            r = QgsMeshLayer3DRenderer(sym)
            r.setLayer(layer)
            layer.set3DRenderer(r)
        except Exception:
            pass
        _finalize_layer(layer, getattr(self, "history", None) or [])


def _safe_filename(s, used):
    s = (s or "mesh").strip()
    for ch in '<>:"/\\|?*':
        s = s.replace(ch, "_")
    s = s.strip(". ") or "mesh"
    base, k = s, 2
    while s.lower() in used:
        s = "%s_%d" % (base, k)
        k += 1
    used.add(s.lower())
    return s


class SectionSurfacesToMeshAlgorithm(QgsProcessingAlgorithm):
    """Гриды поверхностей -> mesh-слои 2DM для штатного 3D-вида QGIS. Растровых
    поверхностей в 3D-сцене может быть только одна (террейн), а mesh-слоёв -
    сколько угодно, каждый на своих абсолютных Z. Инструмент пишет каждый грид
    отдельным 2DM и загружает mesh-слои в проект, применяя вертикальное
    преобразование Z' = Z * масштаб + смещение."""

    GRIDS, ZSCALE, ZOFFSET, STEP = "GRIDS", "ZSCALE", "ZOFFSET", "STEP"
    ZBAND = "ZBAND"
    SPACING = "SPACING"
    FOLDER = "FOLDER"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionSurfacesToMeshAlgorithm()
    def name(self): return "surfaces_to_mesh3d"
    def displayName(self): return self.tr("4.04 Поверхности в 3D (меши) (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP4)
    def groupId(self): return GROUP4_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Экспортирует гриды поверхностей в mesh-слои стандартного формата "
            "2DM (MDAL). Такие слои понимают профильный инструмент QGIS, "
            "mesh-калькулятор, штатный 3D-вид и сторонние программы, а пачка "
            "горизонтов кровля-подошва уходит в меши без ручных "
            "конвертаций.\n\nК отметкам при записи "
            "применяется вертикальное преобразование Z' = Z * масштаб + смещение: "
            "масштаб даёт вертикальное преувеличение, смещение разносит горизонты "
            "по высоте. Разнос по Z сдвигает каждый следующий грид на шаг вниз, "
            "превращая слипшуюся стопку в читаемую этажерку. Прореживание "
            "уменьшает число узлов на крупных гридах.\n\n"
            "Слои загружаются в проект и получают 3D-отображение автоматически. "
            "Если сцена уже открыта, включите новые слои в её списке. Ячейки без "
            "данных пропускаются.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.GRIDS, self.tr("Поверхности-гриды"),
            layerType=QgsProcessing.TypeRaster))
        self.addParameter(QgsProcessingParameterNumber(
            self.ZSCALE, self.tr("Масштаб Z (вертикальное преувеличение)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.ZSCALE, 1.0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.ZOFFSET, self.tr("Смещение Z"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.ZOFFSET, 0.0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SPACING, self.tr("Разнос по Z (шаг на каждый следующий грид)"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.SPACING, 0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.STEP, self.tr("Прореживание узлов (каждый N-й)"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.STEP, 1), minValue=1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZBAND, self.tr("Канал высот (Z)"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.ZBAND, 1), minValue=1)))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.FOLDER, self.tr("Папка для мешей (2DM)")))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        grids = self.parameterAsLayerList(parameters, self.GRIDS, context)
        if not grids:
            raise QgsProcessingException(self.tr("Нужен хотя бы один грид."))
        zscale = self.parameterAsDouble(parameters, self.ZSCALE, context)
        zoffset = self.parameterAsDouble(parameters, self.ZOFFSET, context)
        spacing = self.parameterAsDouble(parameters, self.SPACING, context)
        step = self.parameterAsInt(parameters, self.STEP, context)
        folder = self.parameterAsString(parameters, self.FOLDER, context)
        os.makedirs(folder, exist_ok=True)

        used, written = set(), 0
        for k, lyr in enumerate(grids):
            if feedback.isCanceled():
                break
            ds = gdal.Open(lyr.source())
            zband = self.parameterAsInt(parameters, self.ZBAND, context)
            if ds is None or zband > ds.RasterCount:
                feedback.pushWarning(_tr("Грид не открылся: %s") % lyr.name())
                continue
            b = ds.GetRasterBand(zband)
            arr = b.ReadAsArray().astype(float)
            nd = b.GetNoDataValue()
            if nd is not None:
                arr = np.where(arr == nd, np.nan, arr)
            gt = ds.GetGeoTransform()
            ds = None
            fn = os.path.join(folder,
                              _safe_filename(lyr.name(), used) + ".2dm")
            try:
                nv, nt = grid_to_2dm(arr, gt, fn, zscale,
                                     zoffset - spacing * k, step)
            except ValueError:
                feedback.pushWarning(
                    _tr("Грид пропущен (мал или пуст): %s") % lyr.name())
                continue
            feedback.pushInfo(
                _tr("Меш записан: %s (узлов %d, треугольников %d).")
                % (os.path.basename(fn), nv, nt))
            written += 1
            ml = QgsMeshLayer(fn, lyr.name(), "mdal")
            if not ml.isValid():
                feedback.pushWarning(
                    _tr("Слой меша не загрузился: %s") % os.path.basename(fn))
                continue
            try:
                ml.setCrs(lyr.crs())
            except Exception:
                pass
            context.temporaryLayerStore().addMapLayer(ml)
            det = QgsProcessingContext.LayerDetails(
                lyr.name(), context.project(), self.FOLDER)
            det.groupName = GRP_MESH3D
            pp = _Mesh3DPostProcessor()
            pp.history = _provenance(self, parameters)
            _KEEP_ALIVE.append(pp)
            det.setPostProcessor(pp)
            context.addLayerToLoadOnCompletion(ml.id(), det)
        if written == 0:
            raise QgsProcessingException(self.tr("Гриды не открылись."))
        _save_values(self, _saved)
        return {self.FOLDER: folder}


class BedAssembleAlgorithm(QgsProcessingAlgorithm):
    """Собирает многоканальный грид пласта из горизонтов и параметров:
    канал 1 - кровля, канал 2 - подошва, каналы 3+ - параметры. Все входы
    приводятся к сетке кровли билинейной выборкой; имена каналов пишутся
    в описания (у параметров - имена слоёв)."""

    ROOF, BOTTOM = "ROOF", "BOTTOM"
    ROOF_BAND, BOTTOM_BAND = "ROOF_BAND", "BOTTOM_BAND"
    PARAMS = "PARAMS"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return BedAssembleAlgorithm()
    def name(self): return "assemble_bed_grid"
    def displayName(self): return self.tr("4.01 Собрать грид пласта (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP4)
    def groupId(self): return GROUP4_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Собирает многоканальный грид пласта по конвенции плагина: "
            "канал 1 - кровля, канал 2 - подошва, каналы 3 и далее - "
            "параметры (содержание, минтип и любые другие). Кровля задаёт "
            "сетку результата; подошва и параметры билинейно приводятся к "
            "ней, поэтому исходные гриды могут иметь разные сетки. Имена "
            "каналов записываются в описания: «кровля», «подошва», далее "
            "имена слоёв параметров.\n\nОдин собранный файл кормит "
            "«Состав пласта на разрез» (каналы 1/2/3), 3D-просмотр (тела "
            "пластов) и экспорт в меши - это шаг к блочной модели, где "
            "новые параметры добавляются каналами.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.ROOF, self.tr("Кровля (растр)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.BOTTOM, self.tr("Подошва (растр)")))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.PARAMS, self.tr("Параметры (растры, берётся канал 1)"),
            layerType=QgsProcessing.TypeRaster, optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ROOF_BAND, self.tr("Канал кровли"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.ROOF_BAND, 1), minValue=1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BOTTOM_BAND, self.tr("Канал подошвы"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.BOTTOM_BAND, 1), minValue=1)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Грид пласта")))

    @staticmethod
    def _read_band(path, band):
        ds = gdal.Open(path)
        if ds is None or band > ds.RasterCount:
            return None, None
        b = ds.GetRasterBand(band)
        arr = b.ReadAsArray().astype(float)
        nd = b.GetNoDataValue()
        if nd is not None:
            arr = np.where(arr == nd, np.nan, arr)
        gt = ds.GetGeoTransform()
        ds = None
        return arr, gt

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        roof_l = self.parameterAsRasterLayer(parameters, self.ROOF, context)
        bot_l = self.parameterAsRasterLayer(parameters, self.BOTTOM, context)
        params = self.parameterAsLayerList(parameters, self.PARAMS, context) or []
        rb = self.parameterAsInt(parameters, self.ROOF_BAND, context)
        bb = self.parameterAsInt(parameters, self.BOTTOM_BAND, context)
        out = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

        roof, gt = self._read_band(roof_l.source(), rb)
        if roof is None:
            raise QgsProcessingException(self.tr("Гриды не открылись."))
        ny, nx = roof.shape
        xs = gt[0] + (np.arange(nx) + 0.5) * gt[1]
        ys = gt[3] + (np.arange(ny) + 0.5) * gt[5]
        XX, YY = np.meshgrid(xs, ys)

        def to_frame(lyr, band):
            arr, g2 = self._read_band(lyr.source(), band)
            if arr is None:
                return None
            if arr.shape == roof.shape and np.allclose(g2, gt):
                return arr
            return sample_bilinear(arr, g2, XX.ravel(), YY.ravel()) \
                .reshape(roof.shape)

        bot = to_frame(bot_l, bb)
        if bot is None:
            raise QgsProcessingException(self.tr("Гриды не открылись."))
        stack = [roof, bot]
        bnames = [self.tr("кровля"), self.tr("подошва")]
        for lyr in params:
            a = to_frame(lyr, 1)
            if a is None:
                feedback.pushWarning(
                    _tr("Грид не открылся: %s") % lyr.name())
                continue
            stack.append(a)
            bnames.append(lyr.name())
        nod = -9999.0
        stack = [np.where(np.isfinite(a), a, nod).astype(np.float32)
                 for a in stack]
        crs_wkt = roof_l.crs().toWkt() if roof_l.crs().isValid() else ""
        _write_grid_tiff(out, stack, gt, crs_wkt, nod, nx, ny,
                         band_names=bnames)
        feedback.pushInfo(
            _tr("Грид пласта записан: каналов %d.") % len(stack))
        _save_values(self, _saved)
        return {self.OUTPUT: out}


class BedCalculatorAlgorithm(QgsProcessingAlgorithm):
    """Подсчёт по гриду пласта: мощность из каналов кровли и подошвы,
    объём, тоннаж руды и металла, средневзвешенное содержание; сводка по
    всей площади или внутри контура. Мощность и запасы дописываются
    каналами в новый грид пласта."""

    BED = "BED"
    CONTENT_BAND = "CONTENT_BAND"
    DENSITY = "DENSITY"
    CONTOUR = "CONTOUR"
    OUTPUT, REPORT = "OUTPUT", "REPORT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return BedCalculatorAlgorithm()
    def name(self): return "bed_calculator"
    def displayName(self): return self.tr("4.02 Калькулятор пласта (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP4)
    def groupId(self): return GROUP4_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Считает по многоканальному гриду пласта (канал 1 - кровля, "
            "канал 2 - подошва): мощность, объём, тоннаж руды через "
            "плотность и, если задан канал содержания, средневзвешенное по "
            "мощности содержание и тоннаж металла. Сводка - по всей площади "
            "пласта или внутри контура (полигоны подсчётного блока, "
            "домена).\n\nРезультат - грид пласта с дописанными каналами "
            "«мощность» и «запасы руды, т/ячейку» и HTML-отчёт со сводкой. "
            "Ячейки с мощностью меньше нуля (пересечение поверхностей) "
            "обнуляются и считаются отдельно.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.BED, self.tr("Грид пласта (канал 1 кровля, канал 2 подошва)")))
        self.addParameter(QgsProcessingParameterNumber(
            self.CONTENT_BAND,
            self.tr("Канал содержания (0 - без содержания)"),
            QgsProcessingParameterNumber.Integer,
            defaultValue=_dv(self, self.CONTENT_BAND, 3), minValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.DENSITY, self.tr("Плотность руды, т/м³"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.DENSITY, 2.1), minValue=0.01))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CONTOUR, self.tr("Контур подсчёта (полигоны, необязательно)"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Грид пласта с мощностью и запасами")))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.REPORT, self.tr("Отчёт (HTML)"),
            self.tr("HTML-файлы (*.html)"), optional=True,
            createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        bed_l = self.parameterAsRasterLayer(parameters, self.BED, context)
        cband = self.parameterAsInt(parameters, self.CONTENT_BAND, context)
        dens = self.parameterAsDouble(parameters, self.DENSITY, context)
        contour = self.parameterAsSource(parameters, self.CONTOUR, context)
        out = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        report = self.parameterAsFileOutput(parameters, self.REPORT, context)

        ds = gdal.Open(bed_l.source())
        if ds is None or ds.RasterCount < 2:
            raise QgsProcessingException(
                self.tr("Нужен многоканальный грид пласта (каналы 1 и 2)."))
        gt = ds.GetGeoTransform()
        ny, nx = ds.RasterYSize, ds.RasterXSize

        def band(i):
            b = ds.GetRasterBand(i)
            a = b.ReadAsArray().astype(float)
            nd = b.GetNoDataValue()
            if nd is not None:
                a = np.where(a == nd, np.nan, a)
            return a, (b.GetDescription() or "")

        stack, names = [], []
        for i in range(1, ds.RasterCount + 1):
            a, nm = band(i)
            stack.append(a)
            names.append(nm or str(i))
        ds = None
        roof, bot = stack[0], stack[1]
        thick = roof - bot
        neg = int(np.nansum(thick < 0))
        thick = np.where(np.isfinite(thick), np.maximum(thick, 0.0), np.nan)

        cell = abs(gt[1] * gt[5])
        mask = np.isfinite(thick)
        if contour is not None:
            rings = []
            for ft in contour.getFeatures():
                g = ft.geometry()
                if g is None or g.isEmpty():
                    continue
                try:
                    polys = g.asMultiPolygon()
                except Exception:
                    polys = []
                if not polys:
                    try:
                        p1 = g.asPolygon()
                    except Exception:
                        p1 = []
                    polys = [p1] if p1 else []
                for poly in polys:
                    for ring in poly:
                        rings.append([(p.x(), p.y()) for p in ring])
            if rings:
                mask &= polygon_mask(rings, gt, (ny, nx))

        area = float(mask.sum()) * cell
        vol = float(np.nansum(np.where(mask, thick, 0.0))) * cell
        ore_t = vol * dens
        t_valid = thick[mask]
        t_mean = float(np.nanmean(t_valid)) if t_valid.size else 0.0
        t_min = float(np.nanmin(t_valid)) if t_valid.size else 0.0
        t_max = float(np.nanmax(t_valid)) if t_valid.size else 0.0

        grade_mean = metal_t = None
        if cband > 0:
            if cband > len(stack):
                raise QgsProcessingException(
                    self.tr("Канал содержания вне грида."))
            grade = stack[cband - 1]
            w = np.where(mask & np.isfinite(grade), thick, 0.0)
            sw = float(np.nansum(w))
            if sw > 0:
                grade_mean = float(np.nansum(
                    np.where(mask & np.isfinite(grade),
                             thick * grade, 0.0))) / sw
                metal_t = ore_t * grade_mean / 100.0

        ore_cell = np.where(mask, thick * cell * dens, np.nan)
        out_stack = stack + [thick, ore_cell]
        out_names = names + [self.tr("мощность"),
                             self.tr("запасы руды, т/ячейку")]
        nod = -9999.0
        out_stack = [np.where(np.isfinite(a), a, nod).astype(np.float32)
                     for a in out_stack]
        crs_wkt = bed_l.crs().toWkt() if bed_l.crs().isValid() else ""
        _write_grid_tiff(out, out_stack, gt, crs_wkt, nod, nx, ny,
                         band_names=out_names)

        rows = [
            (self.tr("Площадь подсчёта"), "%.4g м²" % area),
            (self.tr("Мощность средняя / мин / макс"),
             "%.2f / %.2f / %.2f м" % (t_mean, t_min, t_max)),
            (self.tr("Объём"), "%.4g м³" % vol),
            (self.tr("Плотность"), "%.3g т/м³" % dens),
            (self.tr("Запасы руды"), "%.4g т" % ore_t),
        ]
        if grade_mean is not None:
            rows.append((self.tr("Содержание (взвешенное по мощности)"),
                         "%.3f" % grade_mean))
            rows.append((self.tr("Запасы металла"), "%.4g т" % metal_t))
        if neg:
            rows.append((self.tr("Ячеек с отрицательной мощностью"),
                         str(neg)))
        for k, v in rows:
            feedback.pushInfo("%s: %s" % (k, v))
        if report:
            html = ["<html><head><meta charset='utf-8'><style>",
                    "body{font-family:sans-serif;margin:2em}",
                    "table{border-collapse:collapse}",
                    "td{border:1px solid #999;padding:6px 12px}",
                    "</style></head><body>",
                    "<h2>%s</h2>" % self.tr("Калькулятор пласта"),
                    "<p>%s</p>" % bed_l.name(), "<table>"]
            for k, v in rows:
                html.append("<tr><td>%s</td><td>%s</td></tr>" % (k, v))
            html.append("</table></body></html>")
            with open(report, "w", encoding="utf-8") as f:
                f.write("\n".join(html))
        _save_values(self, _saved)
        res = {self.OUTPUT: out}
        if report:
            res[self.REPORT] = report
        return res


class BedToBlockModelAlgorithm(QgsProcessingAlgorithm):
    """Грид пласта -> блочная модель: точка-центроид на каждую валидную
    ячейку с атрибутами верха, низа, мощности, объёма, тоннажа и всех
    каналов параметров по их именам. Схема наращивается атрибутами
    (join, калькулятор полей) и готова к делению колонок по вертикали."""

    BED = "BED"
    DENSITY = "DENSITY"
    CONTOUR = "CONTOUR"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return BedToBlockModelAlgorithm()
    def name(self): return "bed_to_block_model"
    def displayName(self): return self.tr("4.03 Грид пласта в блочную модель (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP4)
    def groupId(self): return GROUP4_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Переводит многоканальный грид пласта в блочную модель: точку-"
            "центроид на каждую валидную ячейку. Атрибуты: строка и столбец "
            "ячейки, координаты, верх (top), низ (bot), мощность (thick), "
            "объём (vol), тоннаж руды (ore_t) через плотность и все каналы "
            "параметров под их именами из описаний.\n\nДальше работает "
            "векторный аппарат QGIS: фильтры выражениями, join внешних "
            "таблиц, калькулятор полей - модель наращивается атрибутами без "
            "пересоздания. Контур ограничивает выгрузку подсчётным блоком "
            "или доменом.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.BED, self.tr("Грид пласта (канал 1 кровля, канал 2 подошва)")))
        self.addParameter(QgsProcessingParameterNumber(
            self.DENSITY, self.tr("Плотность руды, т/м³"),
            QgsProcessingParameterNumber.Double,
            defaultValue=_dv(self, self.DENSITY, 2.1), minValue=0.01))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CONTOUR, self.tr("Контур подсчёта (полигоны, необязательно)"),
            [QgsProcessing.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Блочная модель (центроиды)"),
            QgsProcessing.TypeVectorPoint))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        bed_l = self.parameterAsRasterLayer(parameters, self.BED, context)
        dens = self.parameterAsDouble(parameters, self.DENSITY, context)
        contour = self.parameterAsSource(parameters, self.CONTOUR, context)

        ds = gdal.Open(bed_l.source())
        if ds is None or ds.RasterCount < 2:
            raise QgsProcessingException(
                self.tr("Нужен многоканальный грид пласта (каналы 1 и 2)."))
        gt = ds.GetGeoTransform()
        ny, nx = ds.RasterYSize, ds.RasterXSize
        stack, names = [], []
        for i in range(1, ds.RasterCount + 1):
            b = ds.GetRasterBand(i)
            a = b.ReadAsArray().astype(float)
            nd = b.GetNoDataValue()
            if nd is not None:
                a = np.where(a == nd, np.nan, a)
            stack.append(a)
            names.append(b.GetDescription() or ("band%d" % i))
        ds = None
        roof, bot = stack[0], stack[1]
        thick = np.where(np.isfinite(roof - bot),
                         np.maximum(roof - bot, 0.0), np.nan)
        cell = abs(gt[1] * gt[5])
        mask = np.isfinite(thick)
        if contour is not None:
            rings = []
            for ft in contour.getFeatures():
                g = ft.geometry()
                if g is None or g.isEmpty():
                    continue
                try:
                    polys = g.asMultiPolygon()
                except Exception:
                    polys = []
                if not polys:
                    try:
                        p1 = g.asPolygon()
                    except Exception:
                        p1 = []
                    polys = [p1] if p1 else []
                for poly in polys:
                    for ring in poly:
                        rings.append([(p.x(), p.y()) for p in ring])
            if rings:
                mask &= polygon_mask(rings, gt, (ny, nx))

        def _safe(nm, used):
            s = nm.strip() or "band"
            for ch in ' ,;:/\\()"\'':
                s = s.replace(ch, "_")
            base, k = s, 2
            while s in used:
                s = "%s_%d" % (base, k)
                k += 1
            used.add(s)
            return s

        used = {"bid", "row", "col", "x", "y", "top", "bot",
                "thick", "vol", "ore_t"}
        pnames = [_safe(nm, used) for nm in names[2:]]
        fields = QgsFields()
        for nm, tp in (("bid", QVariant.Int), ("row", QVariant.Int),
                       ("col", QVariant.Int), ("x", QVariant.Double),
                       ("y", QVariant.Double), ("top", QVariant.Double),
                       ("bot", QVariant.Double), ("thick", QVariant.Double),
                       ("vol", QVariant.Double), ("ore_t", QVariant.Double)):
            fields.append(QgsField(nm, tp))
        for nm in pnames:
            fields.append(QgsField(nm, QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Point, bed_l.crs())

        idx = np.argwhere(mask)
        total = len(idx)
        bid = 0
        for n, (i, j) in enumerate(idx):
            if feedback.isCanceled():
                break
            if total and n % 5000 == 0:
                feedback.setProgress(100.0 * n / total)
            x = gt[0] + (j + 0.5) * gt[1]
            y = gt[3] + (i + 0.5) * gt[5]
            bid += 1
            th = float(thick[i, j])
            vol = th * cell
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            attrs = [bid, int(i), int(j), float(x), float(y),
                     float(roof[i, j]), float(bot[i, j]), th, vol,
                     vol * dens]
            for a in stack[2:]:
                v = a[i, j]
                attrs.append(float(v) if v == v else None)
            f.setAttributes(attrs)
            sink.addFeature(f)
        _set_output_name(context, dest,
                         self.tr("Блочная модель: %s") % bed_l.name())
        feedback.pushInfo(_tr("Блоков выгружено: %d.") % bid)
        _save_values(self, _saved)
        return {self.OUTPUT: dest}


ALGORITHMS = [
    Kriging2DAlgorithm,
    CategoricalIndicatorAlgorithm,
    RasterToIsolinesAlgorithm,
    ExperimentalVariogramAlgorithm,
    VariogramMapAlgorithm,
    CrossValidationAlgorithm,
    ExampleWellsAlgorithm,
    SectionDemoAlgorithm,
    ProfilesAlgorithm,
    FlowGradientAlgorithm,
    ExternalDriftKrigingAlgorithm,
    ExceedanceProbabilityAlgorithm,
    DarcyFluxAlgorithm,
    SequentialGaussianSimAlgorithm,
    SectionAlgorithm,
    BoreholesOnSectionAlgorithm,
    CompositionOnSectionAlgorithm,
    SectionGridIntersectAlgorithm,
    SectionVectorIntersectAlgorithm,
    SectionTinIntersectAlgorithm,
    SectionProjectAlgorithm,
    SectionUnprojectAlgorithm,
    ShaftUnwrapAlgorithm,
    SectionSurfacesToMeshAlgorithm,
    BedAssembleAlgorithm,
    BedCalculatorAlgorithm,
    BedToBlockModelAlgorithm,
]
