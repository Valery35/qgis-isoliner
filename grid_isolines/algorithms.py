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
    QgsProcessingParameterCrs,
    QgsProcessingParameterVectorLayer,
    QgsProcessingParameterRasterLayer,
    QgsProcessingParameterMultipleLayers,
    QgsProcessingParameterMeshLayer,
    QgsProcessingParameterRasterDestination,
    QgsProcessingOutputNumber,
    QgsProcessingParameterBand,
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
from .fractal import (fractal_dimension_map, fractal_dimension_global,
                      box_count_dimension, divider_dimension,
                      minkowski_dimension)
from . import dem_glo30, osm_overpass, demo_relief
from .hydro_fill import fill_depressions, DEFAULT_EPSILON
from . import topo_flow, topo_surface, topo_t2r

GROUP = _tr("1. Грид и изолинии")
GROUP_ID = "grid_isolines"
GROUP_TOPO = _tr("2. Топография")
GROUP_TOPO_ID = "topography"
GROUP2 = _tr("3. Дополнительные инструменты анализа")
GROUP2_ID = "extra_tools"
GROUP3 = _tr("4. Разрез")
GROUP3_ID = "section"
GROUP4 = _tr("5. Пласт и блочная модель")
GROUP4_ID = "bed_block_model"
GROUP5 = _tr("6. Фрактальный анализ")
GROUP5_ID = "fractal_analysis"

MODEL_LABELS = [_tr("Сферическая"), _tr("Экспоненциальная"), _tr("Гауссова"), _tr("Степенная")]
KTYPE_LABELS = [_tr("Ординарный (OK)"), _tr("Простой (SK)")]

NSTRUCT = 1  # количество структур вариограммы (S2/S3 убраны как неиспользуемые)

CREDIT = ("\n\n- - -\nРазработано при поддержке ООО «Информ++» "
          "(www.informpp.ru).\nСтраница плагина: "
          "www.informpp.ru/главная-страница/qgis-isoliner")


def _credit():
    """Подпись «Разработано при поддержке…» на активном языке."""
    return _tr(CREDIT)


def _advanced(param):
    try:
        flag = QgsProcessingParameterDefinition.Flag.FlagAdvanced     # QGIS 3.x
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
    except Exception:  # nosec
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
            except Exception:  # nosec
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
        except Exception:  # nosec
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
    except Exception:  # nosec
        pass
    try:
        if history:
            md = layer.metadata()
            for line in history:
                md.addHistoryItem(line)
            layer.setMetadata(md)
    except Exception:  # nosec
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
    except Exception:  # nosec
        pass
    try:
        h.append(_tr("Инструмент: %s") % alg.displayName())
    except Exception:  # nosec
        pass
    try:
        h.append(_tr("Создано: %s")
                 % datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
    except Exception:  # nosec
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
        except Exception:  # nosec
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
        except Exception:  # nosec
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
    except Exception:  # nosec
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
        except Exception:  # nosec
            pass
        try:
            if self.order_state is not None and self.role:
                if self.role == "lines":
                    self.order_state.lines_id = layer.id()
                else:
                    self.order_state.polys_id = layer.id()
                from qgis.PyQt.QtCore import QTimer
                QTimer.singleShot(0, self.order_state.reorder)
        except Exception:  # nosec
            pass


def _attach_style(context, path, style_path=None, order_state=None, role=None):
    """Вешает на выходной слой пост-процессор стиля (и порядка, если задан)."""
    try:
        if path and context.willLoadLayerOnCompletion(path):
            pp = _StylePostProcessor(style_path, order_state, role)
            _KEEP_ALIVE.append(pp)
            context.layerToLoadOnCompletionDetails(path).setPostProcessor(pp)
            _PP_PATHS.add(path)
    except Exception:  # nosec
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
        except Exception:  # nosec
            pass


def _set_field_aliases(context, path, aliases):
    """Назначить псевдонимы полей выходному слою после загрузки."""
    try:
        if path and context.willLoadLayerOnCompletion(path):
            pp = _AliasPostProcessor(aliases)
            _KEEP_ALIVE.append(pp)
            context.layerToLoadOnCompletionDetails(path).setPostProcessor(pp)
            _PP_PATHS.add(path)
    except Exception:  # nosec
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
    except Exception:  # nosec
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
    except Exception:  # nosec
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
    except Exception:  # nosec
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
    """Дописать версию и приглашение в конец справки инструмента."""
    v = _plugin_version()
    text = "" if text is None else str(text)
    invite = _tr("Isoliner развивается на задачах реальных предприятий. "
                 "Если вашему производству не хватает функции - напишите "
                 "нам: https://www.informpp.ru/главная-страница/"
                 "предприятиям")
    tail = ("\n\nIsoliner v" + v) if v else ""
    return text + tail + "\n" + invite


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
    except Exception:  # nosec
        pass
    return p


def _add_kriging_params(alg):
    alg.addParameter(QgsProcessingParameterEnum(
        alg.KTYPE, _tr("Тип кригинга"), options=[_tr(x) for x in KTYPE_LABELS],
        defaultValue=_dv(alg, alg.KTYPE, 0)))

    # поиск и сетка - основные параметры
    alg.addParameter(QgsProcessingParameterNumber(
        alg.RADIUS, _tr("Радиус поиска (0 = вся выборка)"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.RADIUS, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MIN_POINTS, _tr("Мин. количество точек"),
        QgsProcessingParameterNumber.Type.Integer,
        defaultValue=_dv(alg, alg.MIN_POINTS, 1), minValue=1))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MAX_POINTS, _tr("Макс. количество точек"),
        QgsProcessingParameterNumber.Type.Integer,
        defaultValue=_dv(alg, alg.MAX_POINTS, 24), minValue=1, maxValue=120))

    cs = QgsProcessingParameterNumber(
        alg.CELL_SIZE, _tr("Размер ячейки (0 = авто, min(охват)/50)"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.CELL_SIZE, 0.0), minValue=0.0)
    try:  # живой показ размера грида; на QGIS 4 (без старого API) - обычное поле
        from .widgets import CellSizeWrapper, WRAPPER_AVAILABLE
        if WRAPPER_AVAILABLE:
            cs.setMetadata({"widget_wrapper": {"class": CellSizeWrapper}})
    except Exception:  # nosec
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
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.HULL_BUFFER, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterVectorLayer(
        alg.MASK, _tr("Маска обрезки (полигон из проекта) - приоритетнее оболочки"),
        types=[QgsProcessing.SourceType.TypeVectorPolygon], optional=True))

    # вариограмма - дополнительные параметры
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.SKMEAN, _tr("Среднее для простого кригинга"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.SKMEAN, 0.0))))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.NUGGET, _tr("Наггет C0"),
        QgsProcessingParameterNumber.Type.Double,
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
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "SILL"), default_sill), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "RANGE"), "%s · %s" % (tag, _tr("радиус корреляции a (0=авто)")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "RANGE"), 0.0), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "AZIMUTH"), "%s · %s" % (tag, _tr("азимут, °")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "AZIMUTH"), 0.0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "ANIS"), "%s · %s" % (tag, _tr("анизотропия (малая/главная)")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "ANIS"), 1.0), minValue=EPS)))

    # отсев/срезка ураганных проб (по значению Z) - в самом конце
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_PCT, _tr("Ураганные пробы: перцентиль обрезки, % (0 = выкл.)"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.VAL_PCT, 0.0), minValue=0.0, maxValue=49.0)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MIN, _tr("Нижняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Type.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MAX, _tr("Верхняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Type.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterBoolean(
        alg.VAL_CAP, _tr("Срезать к границе (capping) вместо удаления"),
        defaultValue=_dv(alg, alg.VAL_CAP, False))))


def _read_points(source, zfield, feedback=None,
                 vmin=None, vmax=None, pct=0.0, cap=False,
                 id_field=None, return_ids=False, request=None):
    idx = source.fields().lookupField(zfield)
    if idx < 0:
        raise QgsProcessingException(
            _tr("Поле «%s» не найдено в слое. Выберите поле значения Z.")
            % zfield)
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
    try:
        from . import trace
        trace.data("Точек на входе: %d" % len(xd))
    except Exception:  # nosec
        pass

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
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.INTERVAL, 1.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.BASE, _tr("Начальный уровень (offset)"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.BASE, 0.0)))
    alg.addParameter(QgsProcessingParameterString(
        alg.LEVELS, _tr("Явные уровни (через пробел) - приоритетнее шага"),
        defaultValue=_dv(alg, alg.LEVELS, ""), optional=True))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.INDEX_EVERY, _tr("Главная изолиния каждая N-я (0 = выкл.)"),
        QgsProcessingParameterNumber.Type.Integer,
        defaultValue=_dv(alg, alg.INDEX_EVERY, 5), minValue=0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MIN_LENGTH, _tr("Мин. длина линии, ед. карты (0 = без фильтра)"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.MIN_LENGTH, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterEnum(
        alg.DENSIFY, _tr("Бикубическое сглаживание изолиний (сгущение грида)"),
        options=[_tr("выкл."), "×2", "×3", "×4"],
        defaultValue=_dv(alg, alg.DENSIFY, 0)))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.SMOOTH_LINE_ITER, _tr("Скругление линий (Chaikin), итераций "
                                  "(0 = выкл.)"),
        QgsProcessingParameterNumber.Type.Integer,
        defaultValue=_dv(alg, alg.SMOOTH_LINE_ITER, 2), minValue=0, maxValue=5))
    alg.addParameter(QgsProcessingParameterString(
        alg.FIELD_NAME, _tr("Имя поля значения"),
        defaultValue=_dv(alg, alg.FIELD_NAME, DEFAULT_FIELD)))


# ===========================================================================
#  1. Точки → растр (кригинг)
# ===========================================================================
def _write_declus_report(path, title, naive, decl, best_cell, sizes, means,
                         vs, wts, feedback):
    """Компактный HTML-отчёт декластеризации: сводка, гистограмма (сырая
    против взвешенной) и кривая среднее-размер ячейки. При отсутствии plotly
    выдаёт таблицу-сводку."""
    delta = (decl - naive)
    pct = (100.0 * delta / naive) if naive else 0.0
    head = (
        "<html><head><meta charset='utf-8'><title>%s</title>"
        "<style>body{font-family:sans-serif;margin:20px;color:#222}"
        "table{border-collapse:collapse;margin:8px 0}"
        "td,th{border:1px solid #ccc;padding:4px 10px;text-align:left}"
        "h2{margin:6px 0}.k{color:#666}</style></head><body>"
        "<h2>%s</h2>" % (title, title))
    tbl = ("<table><tr><th>%s</th><th>%s</th></tr>"
           "<tr><td>%s</td><td>%.4g</td></tr>"
           "<tr><td>%s</td><td><b>%.4g</b></td></tr>"
           "<tr><td>%s</td><td>%+.4g (%+.2f%%)</td></tr>"
           "<tr><td>%s</td><td>%.4g</td></tr></table>" % (
               _tr("Показатель"), _tr("Значение"),
               _tr("Наивное среднее"), naive,
               _tr("Декластеризованное среднее"), decl,
               _tr("Сдвиг"), delta, pct,
               _tr("Размер ячейки"), best_cell))
    hint = "<p class='k'>%s</p>" % _tr(
        "Декластеризованное среднее - представительная оценка для подсчёта "
        "запасов и для «Среднего» простого кригинга. Поле весов wt подаётся "
        "в SGS (3.06) для взвешенной гистограммы.")
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
        vs = np.asarray(vs, float); wts = np.asarray(wts, float)
        ncols = 2 if (sizes is not None) else 1
        fig = make_subplots(rows=1, cols=ncols, subplot_titles=(
            [_tr("Гистограмма: сырая и взвешенная")]
            + ([_tr("Среднее от размера ячейки")] if ncols == 2 else [])))
        bins = np.histogram_bin_edges(vs, bins=min(20, max(5, vs.size // 5)))
        centers = 0.5 * (bins[:-1] + bins[1:])
        widths = np.diff(bins)
        raw, _ = np.histogram(vs, bins=bins, density=True)
        wsum, _ = np.histogram(vs, bins=bins, weights=wts)
        tot = wsum.sum()
        wdens = wsum / (tot * widths) if tot > 0 else wsum * 0.0
        fig.add_trace(go.Bar(x=centers, y=raw, width=widths,
                             name=_tr("сырая"), marker_color="#999999",
                             opacity=0.55), row=1, col=1)
        fig.add_trace(go.Bar(x=centers, y=wdens, width=widths,
                             name=_tr("взвешенная"), marker_color="#1f6fcc",
                             opacity=0.55), row=1, col=1)
        if sizes is not None:
            fig.add_trace(go.Scatter(
                x=np.asarray(sizes), y=np.asarray(means), mode="lines+markers",
                line=dict(color="#1f6fcc"), name=_tr("декл. среднее")),
                row=1, col=2)
            fig.add_trace(go.Scatter(
                x=[best_cell], y=[decl], mode="markers",
                marker=dict(color="#cc3333", size=10), name=_tr("выбор")),
                row=1, col=2)
        fig.update_layout(barmode="overlay", showlegend=True,
                          height=380, margin=dict(t=40, l=40, r=20, b=40))
        chart = fig.to_html(full_html=False, include_plotlyjs="cdn")
    except Exception as e:
        if feedback is not None:
            feedback.pushInfo(_tr("plotly недоступен, отчёт без графиков (%s).")
                              % e)
        chart = ""
    html = head + tbl + hint + chart + _version_footer() + "</body></html>"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


class IsolinerAlgorithm(QgsProcessingAlgorithm):
    """Базовый класс инструментов Isoliner. Оборачивает расчёт журналом
    (trace): имя инструмента, параметры, время, а при сбое - трейсбек на диск
    рядом с окном Processing. Наследники держат тело в _process."""

    def processAlgorithm(self, parameters, context, feedback):
        import time
        from . import trace
        name = self.displayName()
        trace.step("Инструмент: %s" % name)
        trace.data("Параметры: %s" % self._short_params(parameters))
        started = time.time()
        try:
            result = self._process(parameters, context, feedback)
            trace.step("Готово за %.1f с" % (time.time() - started))
            return result
        except Exception as exc:
            trace.fail("%s: расчёт прерван: %s" % (name, exc), exc)
            try:
                feedback.reportError(
                    "Расчёт прерван: %s\n\nПодробности в журнале:\n%s"
                    % (exc, trace.path() or "журнал не заведён"))
            except Exception:  # nosec
                pass
            raise

    def _process(self, parameters, context, feedback):
        raise NotImplementedError

    def _short_params(self, parameters):
        parts = []
        try:
            for key, value in sorted(parameters.items()):
                text = getattr(value, "name", None)
                text = text() if callable(text) else (text or value)
                parts.append("%s=%s" % (key, text))
        except Exception:
            return str(parameters)
        return ", ".join(parts)


class DeclusteringAlgorithm(IsolinerAlgorithm):
    """Ячеистая декластеризация: веса, обратные локальной плотности данных,
    и представительное (декластеризованное) среднее. Порт GSLIB declus."""

    INPUT, ZFIELD, MODE = "INPUT", "ZFIELD", "MODE"
    CELL_SIZE, NCELL, OBJECTIVE = "CELL_SIZE", "NCELL", "OBJECTIVE"
    ASPECT, NOFF = "ASPECT", "NOFF"
    OUTPUT, OUTPUT_HTML = "OUTPUT", "OUTPUT_HTML"

    _MODES = ("auto", "manual")

    def tr(self, s): return _tr(s)
    def createInstance(self): return DeclusteringAlgorithm()
    def name(self): return "declustering"
    def displayName(self): return self.tr("1.01 Декластеризация (веса)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP)
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Ячеистая декластеризация (порт GSLIB declus). Когда пробы "
            "сгущены неравномерно - одни блоки разбурены плотнее, - наивная "
            "глобальная статистика смещается в сторону переразведанных "
            "участков: если гуще бурили богатые зоны, среднее и гистограмма "
            "завышены. Инструмент даёт каждой пробе вес, обратный локальной "
            "плотности (в скоплении меньше, на отшибе больше), и считает "
            "представительное декластеризованное среднее.\n\nРазмер ячейки "
            "подбирается автоматически (свип по размерам, выбор по минимуму "
            "декластеризованного среднего) либо задаётся вручную. На "
            "регулярной сети декластеризация ничего не меняет - веса "
            "равны.\n\nВыход: слой точек с полем весов wt и HTML-отчёт "
            "(сводка, гистограмма сырая против взвешенной, кривая среднего). "
            "Декластеризованное среднее - готовая оценка для подсчёта запасов "
            "и для «Среднего» простого кригинга (1.1). Поле wt подаётся в "
            "гауссову симуляцию (3.06) для взвешенной гистограммы.")
            + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точки со значениями"),
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения (Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric,
            defaultValue=_dv(self, self.ZFIELD, None)))
        self.addParameter(QgsProcessingParameterEnum(
            self.MODE, self.tr("Размер ячейки"),
            options=[self.tr("Авто (свип по размеру)"),
                     self.tr("Ручной размер")],
            defaultValue=_dv(self, self.MODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Размер ячейки для ручного режима"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL_SIZE, 0.0), minValue=0.0))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.OBJECTIVE, self.tr("Цель свипа"),
            options=[self.tr("Минимум среднего (скопления в богатом)"),
                     self.tr("Максимум среднего (скопления в бедном)")],
            defaultValue=_dv(self, self.OBJECTIVE, 0))))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.NCELL, self.tr("Число размеров в свипе"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NCELL, 24), minValue=3, maxValue=200)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ASPECT, self.tr("Соотношение ячейки Y/X"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ASPECT, 1.0), minValue=0.05,
            maxValue=20.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.NOFF, self.tr("Смещений начала сетки (усреднение)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NOFF, 4), minValue=1, maxValue=16)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Точки с весами декластеризации"),
            QgsProcessing.SourceType.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, self.tr("HTML-отчёт"),
            self.tr("HTML (*.html)"), optional=True, createByDefault=True))

    def _process(self, parameters, context, feedback):
        from . import declus as dc
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        xs, ys, vs = _read_points(source, zfield, feedback)
        n = len(xs)
        if n < 2:
            raise QgsProcessingException(self.tr("Слишком мало точек."))

        mode = self._MODES[self.parameterAsEnum(
            parameters, self.MODE, context)]
        aspect = self.parameterAsDouble(parameters, self.ASPECT, context)
        noff = self.parameterAsInt(parameters, self.NOFF, context)
        sizes = means = None
        if mode == "manual":
            cell = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
            if cell <= 0:
                lo, hi = dc.suggest_range(xs, ys)
                cell = float(np.sqrt(lo * hi))
                feedback.pushInfo(self.tr(
                    "Размер ячейки не задан - взят %.4g.") % cell)
            weights, decl = dc.cell_declus(xs, ys, vs, cell, cell * aspect,
                                           noff)
            best_cell = cell
            naive = float(np.mean(vs))
        else:
            obj = "max" if self.parameterAsEnum(
                parameters, self.OBJECTIVE, context) == 1 else "min"
            ncell = self.parameterAsInt(parameters, self.NCELL, context)
            lo, hi = dc.suggest_range(xs, ys)
            res = dc.declus_sweep(xs, ys, vs, lo, hi, ncell=ncell, noff=noff,
                                  aspect=aspect, objective=obj)
            weights = res["weights"]; decl = res["decl_mean"]
            naive = res["naive_mean"]; best_cell = res["best_cell"]
            sizes = res["sizes"]; means = res["means"]

        pct = (100.0 * (decl - naive) / naive) if naive else 0.0
        feedback.pushInfo(self.tr("== Декластеризация =="))
        feedback.pushInfo(self.tr("Точек: %d, размер ячейки: %.4g")
                          % (n, best_cell))
        feedback.pushInfo(self.tr("Наивное среднее:          %.4g") % naive)
        feedback.pushInfo(self.tr("Декластеризованное среднее: %.4g (%+.2f%%)")
                          % (decl, pct))
        feedback.pushInfo(self.tr(
            "Это среднее ставьте в «Среднее» простого кригинга, а поле wt - "
            "в поле весов SGS."))

        valname = _san(zfield) or "z"
        fields = QgsFields()
        fields.append(QgsField(valname, QVariant.Double))
        fields.append(QgsField("wt", QVariant.Double))
        aliases = {valname: self.tr("Значение (%s)") % zfield,
                   "wt": self.tr("Вес декластеризации")}
        crs = source.sourceCrs()
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.Type.Point, crs)
        if sink is not None:
            for i in range(n):
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(float(xs[i]), float(ys[i]))))
                f.setAttributes([float(vs[i]), float(weights[i])])
                sink.addFeature(f)
        _set_output_name(context, dest,
                         self.tr("Веса декластеризации (%s)") % zfield)
        _set_field_aliases(context, dest, aliases)

        results = {self.OUTPUT: dest}
        html_path = self.parameterAsFileOutput(
            parameters, self.OUTPUT_HTML, context)
        if html_path:
            try:
                _write_declus_report(
                    html_path, self.tr("Декластеризация: %s") % zfield,
                    naive, decl, best_cell, sizes, means, vs, weights,
                    feedback)
                results[self.OUTPUT_HTML] = html_path
            except Exception as e:
                feedback.pushWarning(self.tr(
                    "Не удалось записать HTML-отчёт: %s") % e)
        _save_values(self, _saved)
        return results


class Kriging2DAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("1.02 2D Kriging (точки → растр)")

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
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения (Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric,
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
            QgsProcessingParameterNumber.Type.Integer,
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
            QgsProcessingParameterNumber.Type.Double,
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

    def _process(self, parameters, context, feedback):
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
class CategoricalIndicatorAlgorithm(IsolinerAlgorithm):
    """Категориальный индикаторный кригинг по текстовому/категориальному полю.
    На каждый класс строит индикатор 0/1, кригует ординарным кригингом (ядро
    KB2D), нормирует вероятности к сумме 1. Выход: многополосный растр
    вероятностей (полоса на класс), карта зон (самый вероятный класс) и
    уверенность. Кодом класса не кригует - у категорий нет порядка."""
    INPUT, CLASS_FIELD = "INPUT", "CLASS_FIELD"
    WEIGHT_FIELD = "WEIGHT_FIELD"
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
        return self.tr("3.01 Категориальный индикаторный кригинг")
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
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.CLASS_FIELD, self.tr("Категориальное поле (класс)"),
            parentLayerParameterName=self.INPUT,
            defaultValue=_dv(self, self.CLASS_FIELD, None)))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.WEIGHT_FIELD,
            self.tr("Поле весов декластеризации (из 1.01, необязательно)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric, optional=True)))
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Радиус поиска (0 = вся выборка)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.RADIUS, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_POINTS, self.tr("Мин. количество точек"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.MIN_POINTS, 4), minValue=1))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_POINTS, self.tr("Макс. количество точек"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.MAX_POINTS, 24), minValue=1, maxValue=120))
        cs = QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Размер ячейки (0 = авто, min(охват)/50)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL_SIZE, 0.0), minValue=0.0)
        try:
            from .widgets import CellSizeWrapper, WRAPPER_AVAILABLE
            if WRAPPER_AVAILABLE:
                cs.setMetadata({"widget_wrapper": {"class": CellSizeWrapper}})
        except Exception:  # nosec
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

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        field = self.parameterAsString(parameters, self.CLASS_FIELD, context)
        wfield = self.parameterAsString(
            parameters, self.WEIGHT_FIELD, context) or None
        if source is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))

        xs, ys, labels, wraw = [], [], [], []
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
            if wfield:
                try:
                    wraw.append(float(ft.attribute(wfield)))
                except (TypeError, ValueError):
                    wraw.append(np.nan)
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
        wts = None
        if wfield:
            wv = np.asarray(wraw, float)
            if wv.size == len(xs) and np.all(np.isfinite(wv)) and np.all(wv > 0):
                wts = wv
                feedback.pushInfo(self.tr(
                    "Доли классов взвешены декластеризацией (поле «%s»).")
                    % wfield)
            else:
                feedback.pushWarning(self.tr(
                    "Поле весов содержит пустые или неположительные "
                    "значения - веса игнорируются."))
        probs, zone, conf = categorical_indicator_grids(
            xs, ys, labels, classes, xmn, ymn, cell, nx, ny,
            ndmin=ndmin, ndmax=ndmax, radius=radius, nodata=nodata,
            progress=prog, wts=wts)

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
class RasterToIsolinesAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("1.04 Изолинии из растра")

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
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BAND, self.tr("Канал"),
            defaultValue=1,
            parentLayerParameterName=self.INPUT)))
        _add_isoline_params(self)
        self.addParameter(QgsProcessingParameterEnum(
            self.STYLE, self.tr("Стиль изолиний"),
            options=[self.tr(x) for x in self._STYLE_LABELS],
            defaultValue=_dv(self, self.STYLE, 1)))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUTPUT, self.tr("Изолинии (линии)"),
            type=QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUTPUT_POLYGONS, self.tr("Контурные полигоны"),
            type=QgsProcessing.SourceType.TypeVectorPolygon,
            optional=True, createByDefault=True))

    def _process(self, parameters, context, feedback):
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


def _weighted_cv_metrics(fact, est, err, var, w):
    """Метрики кросс-валидации, взвешенные декластеризацией. w=None -> обычные.
    Возвращает (me, mae, rmse, msdr, r)."""
    fact = np.asarray(fact, float); est = np.asarray(est, float)
    err = np.asarray(err, float)
    if w is None:
        wv = np.ones(err.shape)
    else:
        wv = np.asarray(w, float)
    W = float(np.sum(wv)) or 1.0
    me = float(np.sum(wv * err) / W)
    mae = float(np.sum(wv * np.abs(err)) / W)
    rmse = float(np.sqrt(np.sum(wv * err * err) / W))
    msdr = float("nan")
    if var is not None:
        sd = np.sqrt(np.maximum(np.asarray(var, float), 0.0))
        good = sd > 0
        if good.any():
            gw = wv[good]
            msdr = float(np.sum(gw * (err[good] / sd[good]) ** 2)
                         / (float(np.sum(gw)) or 1.0))
    # взвешенная корреляция оценки и факта
    try:
        mf = np.sum(wv * fact) / W
        me_ = np.sum(wv * est) / W
        cov = np.sum(wv * (fact - mf) * (est - me_)) / W
        vf = np.sum(wv * (fact - mf) ** 2) / W
        ve = np.sum(wv * (est - me_) ** 2) / W
        r = float(cov / np.sqrt(vf * ve)) if vf > 0 and ve > 0 else float("nan")
    except Exception:
        r = float("nan")
    return me, mae, rmse, msdr, r


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
    # линия регрессии Best Fit: оценка по факту (est = slope*fact + intercept).
    # slope=1, intercept=0 - идеал; slope<1 - занижение высоких значений
    # (регрессия к среднему, подпись сглаживающих методов).
    _f = np.asarray(fact, float); _e = np.asarray(est, float)
    _mok = np.isfinite(_f) & np.isfinite(_e)
    _slope = _intercept = _angle = None
    if int(_mok.sum()) >= 2 and float(_f[_mok].std()) > 0:
        _slope, _intercept = [float(v) for v in np.polyfit(_f[_mok], _e[_mok], 1)]
        _angle = float(np.degrees(np.arctan(_slope)))
        metrics = list(metrics) + [
            (_tr("Наклон Best Fit"), "%.3f" % _slope,
             _tr("1.0 - идеал, меньше 1 - занижение высоких значений")),
            (_tr("Сдвиг Best Fit"), "%+.3g" % _intercept, _tr("0 - идеал")),
            (_tr("Угол Best Fit"), "%.1f°" % _angle, _tr("45° - идеал")),
        ]
        if feedback is not None:
            feedback.pushInfo(_tr(
                "Best Fit (оценка по факту): наклон %.3f, сдвиг %+.3g, "
                "угол %.1f°") % (_slope, _intercept, _angle))
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
            line=dict(color="#999999", width=2, dash="dash"),
            hoverinfo="skip"),
            row=1, col=1)
        if _slope is not None:
            fig.add_trace(go.Scatter(
                x=[lo, hi],
                y=[_slope * lo + _intercept, _slope * hi + _intercept],
                mode="lines", line=dict(color="#1f6fcc", width=2),
                hoverinfo="skip"), row=1, col=1)
            fig.add_annotation(
                xref="x domain", yref="y domain", x=0.02, y=0.98,
                text=_tr("серая - идеал (1:1), синяя - регрессия"),
                showarrow=False, align="left",
                font=dict(size=10, color="#666"), row=1, col=1)
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
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.RADIUS, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MIN_POINTS, _tr("Мин. количество точек"),
        QgsProcessingParameterNumber.Type.Integer,
        defaultValue=_dv(alg, alg.MIN_POINTS, 1), minValue=1))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MAX_POINTS, _tr("Макс. количество точек"),
        QgsProcessingParameterNumber.Type.Integer,
        defaultValue=_dv(alg, alg.MAX_POINTS, 24), minValue=1, maxValue=120))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.SKMEAN, _tr("Среднее для простого кригинга"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.SKMEAN, 0.0))))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.NUGGET, _tr("Наггет C0"),
        QgsProcessingParameterNumber.Type.Double,
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
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "SILL"), default_sill), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "RANGE"), "%s · %s" % (tag, _tr("радиус корреляции a (0=авто)")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "RANGE"), 0.0), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "AZIMUTH"), "%s · %s" % (tag, _tr("азимут, °")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "AZIMUTH"), 0.0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "ANIS"), "%s · %s" % (tag, _tr("анизотропия (малая/главная)")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "ANIS"), 1.0), minValue=EPS)))

    # отсев/срезка ураганных проб (по значению Z) - в самом конце
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_PCT, _tr("Ураганные пробы: перцентиль обрезки, % (0 = выкл.)"),
        QgsProcessingParameterNumber.Type.Double,
        defaultValue=_dv(alg, alg.VAL_PCT, 0.0), minValue=0.0, maxValue=49.0)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MIN, _tr("Нижняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Type.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.VAL_MAX, _tr("Верхняя граница значения (пусто = нет)"),
        QgsProcessingParameterNumber.Type.Double, optional=True)))
    alg.addParameter(_advanced(QgsProcessingParameterBoolean(
        alg.VAL_CAP, _tr("Срезать к границе (capping) вместо удаления"),
        defaultValue=_dv(alg, alg.VAL_CAP, False))))


# ===========================================================================
#  3. Кросс-валидация вариограммы (leave-one-out)
# ===========================================================================
class CrossValidationAlgorithm(IsolinerAlgorithm):
    INPUT, ZFIELD = "INPUT", "ZFIELD"
    KTYPE, SKMEAN, NUGGET = "KTYPE", "SKMEAN", "NUGGET"
    RADIUS, MIN_POINTS, MAX_POINTS = "RADIUS", "MIN_POINTS", "MAX_POINTS"
    VAL_PCT, VAL_MIN, VAL_MAX, VAL_CAP = "VAL_PCT", "VAL_MIN", "VAL_MAX", "VAL_CAP"
    IDFIELD = "IDFIELD"
    WEIGHT_FIELD = "WEIGHT_FIELD"
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
        return self.tr("1.07 Кросс-валидация вариограммы")

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
            types=[QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения Z"), parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(QgsProcessingParameterField(
            self.IDFIELD, self.tr("Поле номера скважины (необязательно)"),
            parentLayerParameterName=self.INPUT, optional=True))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.WEIGHT_FIELD,
            self.tr("Поле весов декластеризации (из 1.01, необязательно)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric, optional=True)))
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
            type=QgsProcessing.SourceType.TypeVectorPoint, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, self.tr("Отчёт о кросс-валидации (HTML)"),
            self.tr("HTML files (*.html)"), optional=True, createByDefault=True))

    def _process(self, parameters, context, feedback):
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
        wfield = self.parameterAsString(
            parameters, self.WEIGHT_FIELD, context) or None
        cvw = None
        if wfield:
            _wx, _wy, _wv, wids = _read_points(
                source, zfield, feedback,
                vmin=_opt(self.VAL_MIN), vmax=_opt(self.VAL_MAX),
                pct=pct, cap=cap, id_field=wfield, return_ids=True)
            try:
                wv = np.array([float(w) if w is not None else np.nan
                               for w in wids], float)
                if wv.size == len(xd) and np.all(np.isfinite(wv)) \
                        and np.all(wv > 0):
                    cvw = wv
                    feedback.pushInfo(_tr(
                        "Метрики взвешены декластеризацией (поле «%s»).")
                        % wfield)
                else:
                    feedback.pushWarning(_tr(
                        "Поле весов содержит пустые или неположительные "
                        "значения - веса игнорируются."))
            except Exception:
                feedback.pushWarning(_tr(
                    "Не удалось прочитать поле весов - веса игнорируются."))

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
        me, mae, rmse, msdr, r = _weighted_cv_metrics(
            vrd[ok], est[ok], err, var[ok],
            (cvw[ok] if cvw is not None else None))

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
            QgsWkbTypes.Type.Point, source.sourceCrs())
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
    на количество валидных отсчётов. Возвращает поле со средним 0 и ст.откл. 1."""
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


class ExampleWellsAlgorithm(IsolinerAlgorithm):
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
        return self.tr("1.10 Создать пример скважин (демо)")

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
            QgsProcessingParameterNumber.Type.Integer, defaultValue=300,
            minValue=5, maxValue=200000))
        self.addParameter(QgsProcessingParameterNumber(
            self.VMIN, self.tr("Минимум значения X"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.VMAX, self.tr("Максимум значения X"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=50.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH, self.tr("Гладкость (доля охвата)"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.15,
            minValue=0.02, maxValue=0.6))
        for key, label, dv in (
                (self.ROOF_MIN, _tr("Кровля: минимум, м (абс.)"), -250.0),
                (self.ROOF_MAX, _tr("Кровля: максимум, м (абс.)"), -50.0),
                (self.THICK_MIN, _tr("Мощность: минимум, м"), 1.0),
                (self.THICK_MAX, _tr("Мощность: максимум, м"), 8.0)):
            p = QgsProcessingParameterNumber(
                key, self.tr(label), QgsProcessingParameterNumber.Type.Double,
                defaultValue=dv)
            _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterNumber(
            self.NUGGET_FRAC, self.tr("Доля наггета (от дисперсии)"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.35,
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
            QgsProcessingParameterNumber.Type.Integer, defaultValue=0, minValue=0)
        _advanced(p); self.addParameter(p)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Скважины (демо)"),
            type=QgsProcessing.SourceType.TypeVectorPoint))
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

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.Point, crs)
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
        QgsProcessingParameterNumber.Type.Double,
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
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "SILL"), default_sill), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "RANGE"), "%s · %s" % (tag, _tr("радиус корреляции a (0=авто)")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "RANGE"), 0.0), minValue=0.0)))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "AZIMUTH"), "%s · %s" % (tag, _tr("азимут, °")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "AZIMUTH"), 0.0))))
        alg.addParameter(_advanced(QgsProcessingParameterNumber(
            _sk(i, "ANIS"), "%s · %s" % (tag, _tr("анизотропия (малая/главная)")),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(alg, _sk(i, "ANIS"), 1.0), minValue=EPS)))


# палитра для серий (группы): зелёный как основной цвет плагина + контрастные
_VG_COLORS = ["#1f6f54", "#c0552b", "#345b9c", "#9c7a1f", "#7d4a8c",
              "#2f8f8f", "#a23b5e", "#5c6b2f"]


def _fit_advice(fit, data_var, maxlag=None):
    """Короткие рекомендации по подобранной модели."""
    tips = []
    if not fit:
        return [_tr("Точек экспериментальной вариограммы мало для подбора. "
                "Увеличьте количество лагов или максимальное расстояние.")]
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


class GeophysProfilesDemoAlgorithm(IsolinerAlgorithm):
    """Демонстрационные геофизические профили. Два режима: электроразведка
    (ρк, ЕП, ВП с низкоомной аномалией-пятном) и оседания (мульда сдвижения
    по турам). Дополнительно отметка z и истинное значение без шума."""
    EXTENT = "EXTENT"
    N_PROFILES = "N_PROFILES"
    PICKET_STEP = "PICKET_STEP"
    MODE = "MODE"
    RHO_BG, RHO_MIN = "RHO_BG", "RHO_MIN"
    SP_AMP, VP_AMP, NOISE = "SP_AMP", "VP_AMP", "NOISE"
    SUBS_MAX, N_TOURS = "SUBS_MAX", "N_TOURS"
    SUBS_SIGN = "SUBS_SIGN"
    Z_BASE, Z_AMP, SEED = "Z_BASE", "Z_AMP", "SEED"
    OUTPUT = "OUTPUT"

    _MODES = ("electro", "subsidence")

    def tr(self, s): return _tr(s)
    def helpUrl(self): return _help_url()
    def name(self): return "geophysprofiles"
    def displayName(self):
        return self.tr("1.11 Создать пример геофизических профилей (демо)")
    def group(self): return self.tr(GROUP)
    def groupId(self): return "grid_isolines"
    def createInstance(self): return GeophysProfilesDemoAlgorithm()

    def shortHelpString(self):
        return _help_version(self.tr(
            "Создаёт точечный слой геофизических профилей для обучения и "
            "проверки инструментов без реальных данных. Параллельные профили с "
            "пикетами. Два режима.\n\nЭлектроразведка: кажущееся сопротивление "
            "ρк (Ом·м), потенциал естественного поля ЕП (мВ) и вызванная "
            "поляризация ВП (мВ/В). Заложена низкоомная аномалия компактным "
            "пятном (обводнение или замещение), а не полосой, поэтому профили "
            "не синхронны. ρк проваливается с фоновых десятков Ом·м до единиц, "
            "ЕП даёт отрицательный минимум. Поле rho_k интерполируется 2D "
            "Kriging или минимальной кривизной, аномалия оконтуривается "
            "изолиниями.\n\nОседания (мульда): оседание (мм) в виде мульды "
            "сдвижения над отработанной площадью, по нескольким турам. По одним "
            "пикетам можно посчитать разность между турами.\n\nВо всех режимах "
            "добавлены отметка z (м) и истинное значение без шума для проверки "
            "точности интерполяции против эталона. Диапазоны и число туров "
            "меняются в разделе «Дополнительно».\n\nПоля электроразведки: "
            "profile, picket_m, pk, z, rho_k, rho_true, sp, vp. Поля оседаний: "
            "profile, picket_m, pk, tour, z, settle, settle_true."))

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Область (экстент)")))
        self.addParameter(QgsProcessingParameterEnum(
            self.MODE, self.tr("Режим"),
            options=[self.tr("Электроразведка (ρк, ЕП, ВП)"),
                     self.tr("Оседания (мульда)")], defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.N_PROFILES, self.tr("Число профилей"),
            QgsProcessingParameterNumber.Type.Integer, defaultValue=4,
            minValue=1, maxValue=50))
        self.addParameter(QgsProcessingParameterNumber(
            self.PICKET_STEP, self.tr("Шаг пикетов, м"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=20.0,
            minValue=0.1))
        for key, label, dv, lo, hi in (
                (self.RHO_BG, _tr("Фоновое ρк, Ом·м"), 60.0, 1.0, 1e6),
                (self.RHO_MIN, _tr("Минимальное ρк в аномалии, Ом·м"),
                 10.0, 0.1, 1e6),
                (self.SP_AMP, _tr("Амплитуда аномалии ЕП, мВ (обычно < 0)"),
                 -100.0, -1e4, 1e4),
                (self.VP_AMP, _tr("Амплитуда аномалии ВП, мВ/В"),
                 15.0, 0.0, 1e4),
                (self.NOISE, _tr("Шум ρк (доля, лог-масштаб)"),
                 0.06, 0.0, 0.5),
                (self.SUBS_MAX, _tr("Максимальное оседание (мульда), мм"),
                 400.0, 1.0, 2000.0),
                (self.Z_BASE, _tr("Отметка поверхности: база, м"),
                 120.0, -1e4, 1e4),
                (self.Z_AMP, _tr("Отметка поверхности: амплитуда, м"),
                 15.0, 0.0, 1e4)):
            p = QgsProcessingParameterNumber(
                key, self.tr(label), QgsProcessingParameterNumber.Type.Double,
                defaultValue=dv, minValue=lo, maxValue=hi)
            _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterNumber(
            self.N_TOURS, self.tr("Число туров (для оседаний)"),
            QgsProcessingParameterNumber.Type.Integer, defaultValue=2,
            minValue=1, maxValue=20)
        _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterEnum(
            self.SUBS_SIGN, self.tr("Знак оседания"),
            options=[self.tr("Вниз (отрицательное)"),
                     self.tr("Величина (положительное)")], defaultValue=0)
        _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно ГСЧ (0 - случайно)"),
            QgsProcessingParameterNumber.Type.Integer, defaultValue=1, minValue=0)
        _advanced(p); self.addParameter(p)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Геофизические профили"),
            QgsProcessing.SourceType.TypeVectorPoint))

    def _process(self, parameters, context, feedback):
        from . import geodemo
        feedback.pushInfo(_version_line())
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if rect.isEmpty():
            raise QgsProcessingException(self.tr("Не задан охват."))
        mode = self._MODES[self.parameterAsEnum(parameters, self.MODE, context)]
        n_prof = self.parameterAsInt(parameters, self.N_PROFILES, context)
        step = self.parameterAsDouble(parameters, self.PICKET_STEP, context)
        z_base = self.parameterAsDouble(parameters, self.Z_BASE, context)
        z_amp = self.parameterAsDouble(parameters, self.Z_AMP, context)
        seed = self.parameterAsInt(parameters, self.SEED, context)
        args = dict(n_profiles=n_prof, picket_step=step, z_base=z_base,
                    z_amp=z_amp, seed=seed)
        ext = (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(),
               rect.yMaximum())

        fields = QgsFields()
        fields.append(QgsField("profile", QVariant.Int))
        fields.append(QgsField("picket_m", QVariant.Double))
        fields.append(QgsField("pk", QVariant.String))

        if mode == "subsidence":
            subs = self.parameterAsDouble(parameters, self.SUBS_MAX, context)
            n_tours = self.parameterAsInt(parameters, self.N_TOURS, context)
            positive = self.parameterAsEnum(
                parameters, self.SUBS_SIGN, context) == 1
            d = geodemo.gen_subsidence(
                *ext, subs_max=subs, n_tours=n_tours, positive=positive,
                noise=max(subs * 0.01, 1.0), **args)
            for nm in ("tour", "z", "settle", "settle_true"):
                fields.append(QgsField(
                    nm, QVariant.Int if nm == "tour" else QVariant.Double))
            cols = ("profile", "picket_m", "__pk__", "tour", "z", "settle",
                    "settle_true")
            aliases = {"profile": self.tr("Профиль"),
                       "picket_m": self.tr("Пикет, м"),
                       "pk": self.tr("Пикет (ПК)"), "tour": self.tr("Тур"),
                       "z": self.tr("Отметка z, м"),
                       "settle": self.tr("Оседание, мм"),
                       "settle_true": self.tr("Оседание без шума, мм")}
        else:
            rho_bg = self.parameterAsDouble(parameters, self.RHO_BG, context)
            rho_min = self.parameterAsDouble(parameters, self.RHO_MIN, context)
            sp_amp = self.parameterAsDouble(parameters, self.SP_AMP, context)
            vp_amp = self.parameterAsDouble(parameters, self.VP_AMP, context)
            noise = self.parameterAsDouble(parameters, self.NOISE, context)
            d = geodemo.gen_profiles(
                *ext, rho_bg=rho_bg, rho_min=rho_min, sp_amp=sp_amp,
                vp_amp=vp_amp, noise=noise, **args)
            for nm in ("z", "rho_k", "rho_true", "sp", "vp"):
                fields.append(QgsField(nm, QVariant.Double))
            cols = ("profile", "picket_m", "__pk__", "z", "rho_k", "rho_true",
                    "sp", "vp")
            aliases = {"profile": self.tr("Профиль"),
                       "picket_m": self.tr("Пикет, м"),
                       "pk": self.tr("Пикет (ПК)"), "z": self.tr("Отметка z, м"),
                       "rho_k": self.tr("ρк, Ом·м"),
                       "rho_true": self.tr("ρк без шума, Ом·м"),
                       "sp": self.tr("ЕП, мВ"), "vp": self.tr("ВП, мВ/В")}

        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.Type.Point, crs)
        if sink is None:
            raise QgsProcessingException(self.tr("Не создан слой результата."))
        xs = d["x"]; ys = d["y"]; pkm = d["picket_m"]
        n = len(d["profile"])
        for i in range(n):
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(float(xs[i]), float(ys[i]))))
            attrs = []
            for c in cols:
                if c == "__pk__":
                    attrs.append(geodemo.pk_label(pkm[i]))
                elif c == "profile" or c == "tour":
                    attrs.append(int(d[c][i]))
                else:
                    attrs.append(float(d[c][i]))
            f.setAttributes(attrs)
            sink.addFeature(f)

        n_prof_act = int(len(set(d["profile"].tolist())))
        if mode == "subsidence":
            feedback.pushInfo(self.tr(
                "Оседания: профилей %d, туров %d, точек %d. Мульда до %.1f мм. "
                "Разность settle между турами по одним пикетам даёт скорость "
                "оседания.") % (n_prof_act,
                int(len(set(d["tour"].tolist()))), n,
                float(d["settle"].min())))
            layer_name = self.tr("Профили оседаний (мульда, туров: %d)") \
                % int(len(set(d["tour"].tolist())))
        else:
            rho = d["rho_k"]; sp = d["sp"]
            feedback.pushInfo(self.tr(
                "Электроразведка: профилей %d, точек %d. ρк %.4g..%.4g Ом·м, "
                "ЕП %.1f..%.1f мВ.") % (n_prof_act, n, float(rho.min()),
                float(rho.max()), float(sp.min()), float(sp.max())))
            feedback.pushInfo(self.tr(
                "Интерполируйте rho_k (2D Kriging или минимальная кривизна) и "
                "постройте изолинии - аномалия-пятно оконтурится. Поле rho_true "
                "- эталон без шума для проверки точности."))
            layer_name = self.tr("Профили электроразведки (ρк, ЕП, ВП)")
        _set_output_name(context, dest, layer_name)
        _set_field_aliases(context, dest, aliases)
        return {self.OUTPUT: dest}


# ===========================================================================
#  4. Экспериментальная вариограмма (изотропная) + подбор модели
# ===========================================================================
def _num_attr(ft, field, default):
    """Числовое значение поля объекта или default при пустом/нечисловом."""
    if not field:
        return default
    try:
        v = ft.attribute(field)
        if v is None:
            return default
        fv = float(v)
        return fv if np.isfinite(fv) else default
    except (TypeError, ValueError):
        return default


def _iter_points(g):
    if QgsWkbTypes.isMultiType(g.wkbType()):
        for p in g.asMultiPoint():
            yield (p.x(), p.y())
    else:
        p = g.asPoint()
        yield (p.x(), p.y())


def _iter_lines(g):
    if QgsWkbTypes.isMultiType(g.wkbType()):
        parts = g.asMultiPolyline()
    else:
        parts = [g.asPolyline()]
    for line in parts:
        if len(line) >= 2:
            yield np.array([(p.x(), p.y()) for p in line], float)


def _iter_polygons(g):
    if QgsWkbTypes.isMultiType(g.wkbType()):
        polys = g.asMultiPolygon()
    else:
        polys = [g.asPolygon()]
    for poly in polys:
        rings = [np.array([(p.x(), p.y()) for p in ring], float)
                 for ring in poly if len(ring) >= 3]
        if rings:
            yield rings


class VariableSupportDensityAlgorithm(IsolinerAlgorithm):
    """3.07 Плотность по замерам с переменной опорой. Замер размазывается по
    носителю (точка+сигма, линия-коридор, полигон), масса сохраняется, плотность
    обратна площади носителя. Выход - трёхканальный растр (плотность, Σm·σ, Σm),
    самодостаточный для дописывания сериями."""
    INPUT = "INPUT"
    MASS_FIELD, PREC_FIELD = "MASS_FIELD", "PREC_FIELD"
    FROM_FIELD, TO_FIELD = "FROM_FIELD", "TO_FIELD"
    DEFAULT_SIGMA = "DEFAULT_SIGMA"
    EXTENT, CELL, EDGE = "EXTENT", "CELL", "EDGE"
    DASY, APPEND = "DASY", "APPEND"
    OUTPUT, OUTPUT_SIGMA = "OUTPUT", "OUTPUT_SIGMA"
    NODATA = -9999.0

    def tr(self, s): return _tr(s)
    def helpUrl(self): return _help_url()
    def name(self): return "vardensity"
    def displayName(self):
        return self.tr("3.07 Плотность по замерам (переменная опора)")
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID
    def createInstance(self): return VariableSupportDensityAlgorithm()

    def shortHelpString(self):
        return _help_version(self.tr(
            "Оценка плотности, где замер задан не точкой, а носителем конечного "
            "размера: точка с сигмой неопределённости, отрезок линии (коридор "
            "полуширины), полигон. Единичная масса замера размазывается по "
            "носителю. Масса сохраняется, плотность обратна площади носителя, "
            "поэтому грубые привязки (регион, «где-то на Каме») самоослабляются "
            "геометрически, без порогов.\n\nЭто оценка плотности (сколько и "
            "где), не интерполяция значения - для значений остаётся кригинг. "
            "Тип геометрии один на запуск, смешение - серией запусков в один "
            "растр (дописывание).\n\nПоля: масса (по умолчанию 1 на объект); "
            "точность (для точек сигма в единицах карты, для линий полуширина "
            "коридора); для линий from_m/to_m - вырезка интервала по линейной "
            "привязке.\n\nВыход - трёхканальный растр: канал 1 плотность (масса "
            "на км², не зависит от размера ячейки), каналы 2-3 служебные (Σm·σ и "
            "Σm), чтобы дописывание и карта эффективной сигмы были точны. "
            "Необязательный второй растр - средневзвешенная сигма по ячейке "
            "(карта эффективной точности, аналог кригинговой дисперсии).\n\n"
            "Инвариант: интеграл плотности равен сумме масс входа, пишется в "
            "лог. Дазиметрия для полигонов - масса пропорциональна "
            "вспомогательному растру (население и т.п.), при пустом растре "
            "внутри полигона откат на равномерное. Слой должен быть в "
            "метрической системе координат."))

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Замеры (точки, линии или полигоны)")))
        self.addParameter(QgsProcessingParameterField(
            self.MASS_FIELD, self.tr("Поле массы (по умолчанию 1)"),
            parentLayerParameterName=self.INPUT, optional=True,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(QgsProcessingParameterField(
            self.PREC_FIELD,
            self.tr("Поле точности (сигма точки / полуширина линии)"),
            parentLayerParameterName=self.INPUT, optional=True,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL, self.tr("Размер ячейки, м"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=50.0,
            minValue=1e-6))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Область (по умолчанию по слою)"),
            optional=True))
        self.addParameter(QgsProcessingParameterEnum(
            self.EDGE, self.tr("Носитель за краем области"),
            options=[self.tr("Донормировать внутри"),
                     self.tr("Потерять массу (с предупреждением)")],
            defaultValue=0))
        p = QgsProcessingParameterNumber(
            self.DEFAULT_SIGMA,
            self.tr("Сигма по умолчанию, м (0 - полуячейка)"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0, minValue=0.0)
        _advanced(p); self.addParameter(p)
        for key, lab in ((self.FROM_FIELD, _tr("Поле from_m (линии, интервал)")),
                         (self.TO_FIELD, _tr("Поле to_m (линии, интервал)"))):
            p = QgsProcessingParameterField(
                key, self.tr(lab), parentLayerParameterName=self.INPUT,
                optional=True, type=QgsProcessingParameterField.DataType.Numeric)
            _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterRasterLayer(
            self.DASY, self.tr("Вспом. растр для дазиметрии (полигоны)"),
            optional=True)
        _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterRasterLayer(
            self.APPEND,
            self.tr("Дописать в существующий растр (3 канала)"), optional=True)
        _advanced(p); self.addParameter(p)
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Плотность (переменная опора)")))
        se = QgsProcessingParameterRasterDestination(
            self.OUTPUT_SIGMA, self.tr("Эффективная сигма (необязательно)"),
            optional=True, createByDefault=False)
        self.addParameter(se)

    def _process(self, parameters, context, feedback):
        from . import density as D
        feedback.pushInfo(_version_line())
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Не задан слой замеров."))
        crs = source.sourceCrs()
        if crs is not None and crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "Слой в градусах. Перепроецируйте в метрическую систему "
                "координат."))
        gtype = QgsWkbTypes.geometryType(source.wkbType())
        cell = self.parameterAsDouble(parameters, self.CELL, context)
        edge_lose = self.parameterAsEnum(parameters, self.EDGE, context) == 1
        renorm = not edge_lose
        dsig = self.parameterAsDouble(parameters, self.DEFAULT_SIGMA, context)
        mass_f = self.parameterAsString(parameters, self.MASS_FIELD, context)
        prec_f = self.parameterAsString(parameters, self.PREC_FIELD, context)
        from_f = self.parameterAsString(parameters, self.FROM_FIELD, context)
        to_f = self.parameterAsString(parameters, self.TO_FIELD, context)

        # --- сетка: из растра дописывания либо из экстента ---
        append_lyr = self.parameterAsRasterLayer(parameters, self.APPEND,
                                                 context)
        acc = snum = wsum = None
        if append_lyr is not None:
            ds = gdal.Open(append_lyr.source())
            if ds is None or ds.RasterCount < 3:
                raise QgsProcessingException(self.tr(
                    "Растр дописывания должен иметь 3 канала (плотность, "
                    "Σm·σ, Σm)."))
            gt = ds.GetGeoTransform()
            nx, ny = ds.RasterXSize, ds.RasterYSize
            cell = abs(gt[1])
            xmin = gt[0]; ymin = gt[3] + ny * gt[5]     # gt[5] < 0
            gs = D.GridSpec(xmin, ymin, cell, nx, ny)
            b1 = np.flipud(ds.GetRasterBand(1).ReadAsArray().astype(float))
            b2 = np.flipud(ds.GetRasterBand(2).ReadAsArray().astype(float))
            b3 = np.flipud(ds.GetRasterBand(3).ReadAsArray().astype(float))
            b1 = np.where(b1 == self.NODATA, 0.0, b1)
            acc = b1 * gs.cell_area_km2()               # плотность -> масса
            snum = np.where(b2 == self.NODATA, 0.0, b2)
            wsum = np.where(b3 == self.NODATA, 0.0, b3)
            ds = None
            feedback.pushInfo(self.tr("Дописывание в растр %d×%d, ячейка %.4g.")
                              % (nx, ny, cell))
        else:
            ext = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
            if ext.isEmpty():
                ext = source.sourceExtent()
            gs = D.GridSpec.from_extent(ext.xMinimum(), ext.yMinimum(),
                                        ext.xMaximum(), ext.yMaximum(), cell)
            acc, snum, wsum = gs.new_acc()

        # --- дазиметрический вспом. растр -> на сетку (ближайший) ---
        aux_grid = None
        dasy_lyr = self.parameterAsRasterLayer(parameters, self.DASY, context)
        if dasy_lyr is not None:
            aux_grid = self._sample_raster(dasy_lyr, gs)

        log = []
        in_mass = 0.0
        n_obj = 0
        for ft in source.getFeatures():
            if feedback.isCanceled():
                break
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            mass = _num_attr(ft, mass_f, 1.0)
            prec = _num_attr(ft, prec_f, None)
            in_mass += (mass if (mass and np.isfinite(mass)) else 0.0)
            n_obj += 1
            if gtype == QgsWkbTypes.GeometryType.PointGeometry:
                for p in _iter_points(g):
                    sig = prec if prec is not None else (dsig or None)
                    D.add_point(acc, snum, wsum, gs, p[0], p[1], mass, sig,
                                renorm_inside=renorm, log=log)
            elif gtype == QgsWkbTypes.GeometryType.LineGeometry:
                half = prec if prec is not None else (dsig or None)
                fr = _num_attr(ft, from_f, None)
                to = _num_attr(ft, to_f, None)
                for verts in _iter_lines(g):
                    v = verts
                    if from_f or to_f:
                        v = D.cut_polyline(verts, fr, to)
                        if v is None:
                            continue
                    D.add_line(acc, snum, wsum, gs, v, mass, half,
                               renorm_inside=renorm, log=log)
            else:  # polygon
                for rings in _iter_polygons(g):
                    mask = D.rasterize_polygon(gs, rings)
                    D.add_polygon(acc, snum, wsum, gs, mask, mass,
                                  aux=aux_grid, log=log)

        density, eff, total = D.finalize(acc, snum, wsum, gs,
                                         nodata=self.NODATA)
        # --- инвариант ---
        feedback.pushInfo(self.tr(
            "Объектов: %d. Масса входа: %.6g. Масса на сетке: %.6g. "
            "Расхождение: %.3g.") % (n_obj, in_mass, total, in_mass - total))
        if abs(in_mass - total) > 1e-6 * max(1.0, abs(in_mass)):
            feedback.pushWarning(self.tr(
                "Часть массы за пределами области (см. режим края)."))
        for line in log[:50]:
            feedback.pushInfo(line)

        geotr = (gs.xmin, gs.cell, 0.0, gs.ymin + gs.ny * gs.cell, 0.0,
                 -gs.cell)
        wkt = None
        if crs is not None and crs.isValid():
            srs = osr.SpatialReference(); srs.ImportFromWkt(crs.toWkt())
            wkt = srs.ExportToWkt()
        drv = gdal.GetDriverByName("GTiff")
        opt = ["COMPRESS=LZW", "TILED=YES"]
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        ds = drv.Create(out_path, gs.nx, gs.ny, 3, gdal.GDT_Float32,
                        options=opt)
        ds.SetGeoTransform(geotr)
        if wkt:
            ds.SetProjection(wkt)
        for i, (arr, name) in enumerate((
                (density, self.tr("плотность, масса/км²")),
                (snum, "sum m*sigma"), (wsum, "sum m"))):
            b = ds.GetRasterBand(i + 1)
            b.SetDescription(name)
            if i == 0:
                b.SetNoDataValue(self.NODATA)
            b.WriteArray(np.flipud(arr).astype(np.float32)); b.FlushCache()
        ds = None
        _set_output_name(context, out_path,
                         self.tr("Плотность (переменная опора)"))
        results = {self.OUTPUT: out_path}

        sig_path = self.parameterAsOutputLayer(parameters, self.OUTPUT_SIGMA,
                                               context)
        if sig_path:
            ds = drv.Create(sig_path, gs.nx, gs.ny, 1, gdal.GDT_Float32,
                            options=opt)
            ds.SetGeoTransform(geotr)
            if wkt:
                ds.SetProjection(wkt)
            b = ds.GetRasterBand(1); b.SetNoDataValue(self.NODATA)
            b.WriteArray(np.flipud(eff).astype(np.float32)); b.FlushCache()
            ds = None
            _set_output_name(context, sig_path,
                             self.tr("Эффективная сигма"))
            results[self.OUTPUT_SIGMA] = sig_path
        return results

    @staticmethod
    def _sample_raster(layer, gs):
        """Ближайшая выборка растра в узлы сетки gs (ориентация gs: строка 0
        снизу)."""
        ds = gdal.Open(layer.source())
        if ds is None:
            return None
        gt = ds.GetGeoTransform()
        arr = ds.GetRasterBand(1).ReadAsArray().astype(float)
        nd = ds.GetRasterBand(1).GetNoDataValue()
        ds = None
        if nd is not None:
            arr = np.where(arr == nd, 0.0, arr)
        cx = gs.col_centers()
        cy = gs.row_centers()
        col = ((cx - gt[0]) / gt[1]).astype(int)
        row = ((cy - gt[3]) / gt[5]).astype(int)          # gt[5] < 0
        col = np.clip(col, 0, arr.shape[1] - 1)
        row = np.clip(row, 0, arr.shape[0] - 1)
        return arr[np.ix_(row, col)]


class DensityDemoAlgorithm(IsolinerAlgorithm):
    """3.08 Демо-генератор к 3.07. Синтетический набор с круглой суммарной
    массой (1000) для проверки инварианта глазами: точки, линии, полигоны и
    вспомогательный растр для дазиметрии."""
    EXTENT, SEED = "EXTENT", "SEED"
    OUT_POINTS, OUT_LINES = "OUT_POINTS", "OUT_LINES"
    OUT_POLYGONS, OUT_AUX = "OUT_POLYGONS", "OUT_AUX"
    CELL_AUX = "CELL_AUX"

    def tr(self, s): return _tr(s)
    def helpUrl(self): return _help_url()
    def name(self): return "densitydemo"
    def displayName(self):
        return self.tr("3.08 Создать пример для плотности (демо)")
    def group(self): return self.tr(GROUP2)
    def groupId(self): return GROUP2_ID
    def createInstance(self): return DensityDemoAlgorithm()

    def shortHelpString(self):
        return _help_version(self.tr(
            "Синтетический набор для инструмента 3.07 с известной суммарной "
            "массой, чтобы проверить инвариант глазами. Точки (масса 500, "
            "сигмы от долей ячейки до крупных), линии (масса 200, у одной "
            "вырезка интервала from_m/to_m), полигоны (масса 300, один под "
            "дазиметрию) и вспомогательный растр. Итого масса 1000.\n\nЗапустите "
            "3.07 на слое точек - интеграл плотности должен дать 500, на линиях "
            "200, на полигонах 300. Поля: mass, prec, from_m, to_m."))

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Область (экстент)")))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_AUX, self.tr("Ячейка вспом. растра, м"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=50.0,
            minValue=1e-6))
        p = QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно ГСЧ (0 - случайно)"),
            QgsProcessingParameterNumber.Type.Integer, defaultValue=1, minValue=0)
        _advanced(p); self.addParameter(p)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_POINTS, self.tr("Демо-точки"),
            QgsProcessing.SourceType.TypeVectorPoint))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_LINES, self.tr("Демо-линии"),
            QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_POLYGONS, self.tr("Демо-полигоны"),
            QgsProcessing.SourceType.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_AUX, self.tr("Вспом. растр для дазиметрии")))

    def _process(self, parameters, context, feedback):
        from . import density as D
        feedback.pushInfo(_version_line())
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if rect.isEmpty():
            raise QgsProcessingException(self.tr("Не задан охват."))
        seed = self.parameterAsInt(parameters, self.SEED, context)
        cell = self.parameterAsDouble(parameters, self.CELL_AUX, context)
        ds = D.demo_dataset(rect.xMinimum(), rect.yMinimum(),
                            rect.xMaximum(), rect.yMaximum(), seed=seed)

        # --- точки ---
        pf = QgsFields()
        pf.append(QgsField("mass", QVariant.Double))
        pf.append(QgsField("prec", QVariant.Double))
        psink, pdest = self.parameterAsSink(
            parameters, self.OUT_POINTS, context, pf, QgsWkbTypes.Type.Point, crs)
        for x, y, m, s in ds["points"]:
            f = QgsFeature(pf)
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
            f.setAttributes([float(m), float(s)])
            psink.addFeature(f)

        # --- линии ---
        lf = QgsFields()
        lf.append(QgsField("mass", QVariant.Double))
        lf.append(QgsField("prec", QVariant.Double))
        lf.append(QgsField("from_m", QVariant.Double))
        lf.append(QgsField("to_m", QVariant.Double))
        lsink, ldest = self.parameterAsSink(
            parameters, self.OUT_LINES, context, lf, QgsWkbTypes.Type.LineString,
            crs)
        for ln in ds["lines"]:
            f = QgsFeature(lf)
            f.setGeometry(QgsGeometry.fromPolylineXY(
                [QgsPointXY(x, y) for x, y in ln["verts"]]))
            f.setAttributes([float(ln["mass"]), float(ln["half"]),
                             None if ln["from_m"] is None else float(ln["from_m"]),
                             None if ln["to_m"] is None else float(ln["to_m"])])
            lsink.addFeature(f)

        # --- полигоны ---
        gf = QgsFields()
        gf.append(QgsField("mass", QVariant.Double))
        gf.append(QgsField("dasy", QVariant.Int))
        gsink, gdest = self.parameterAsSink(
            parameters, self.OUT_POLYGONS, context, gf, QgsWkbTypes.Type.Polygon,
            crs)
        for pg in ds["polygons"]:
            f = QgsFeature(gf)
            rings = [[QgsPointXY(x, y) for x, y in ring] for ring in pg["rings"]]
            f.setGeometry(QgsGeometry.fromPolygonXY(rings))
            f.setAttributes([float(pg["mass"]), 1 if pg["dasy"] else 0])
            gsink.addFeature(f)

        # --- вспом. растр (градиент, для дазиметрии) ---
        gs = D.GridSpec.from_extent(rect.xMinimum(), rect.yMinimum(),
                                    rect.xMaximum(), rect.yMaximum(), cell)
        gx = (gs.col_centers() - rect.xMinimum())
        gx = gx / max(gx.max(), 1e-9)
        aux = (0.2 + gx[None, :] + 0.0 * gs.row_centers()[:, None])
        geotr = (gs.xmin, gs.cell, 0.0, gs.ymin + gs.ny * gs.cell, 0.0,
                 -gs.cell)
        wkt = None
        if crs is not None and crs.isValid():
            srs = osr.SpatialReference(); srs.ImportFromWkt(crs.toWkt())
            wkt = srs.ExportToWkt()
        aux_path = self.parameterAsOutputLayer(parameters, self.OUT_AUX,
                                               context)
        drv = gdal.GetDriverByName("GTiff")
        rds = drv.Create(aux_path, gs.nx, gs.ny, 1, gdal.GDT_Float32,
                         options=["COMPRESS=LZW", "TILED=YES"])
        rds.SetGeoTransform(geotr)
        if wkt:
            rds.SetProjection(wkt)
        rds.GetRasterBand(1).WriteArray(np.flipud(aux).astype(np.float32))
        rds.GetRasterBand(1).FlushCache(); rds = None

        feedback.pushInfo(self.tr(
            "Демо создано. Масса: точки 500, линии 200, полигоны 300, итого "
            "1000. Прогоните 3.07 на каждом слое - интеграл плотности должен "
            "совпасть."))
        _set_output_name(context, pdest, self.tr("Демо-точки (плотность)"))
        _set_output_name(context, ldest, self.tr("Демо-линии (плотность)"))
        _set_output_name(context, gdest, self.tr("Демо-полигоны (плотность)"))
        _set_output_name(context, aux_path, self.tr("Вспом. растр (дазиметрия)"))
        return {self.OUT_POINTS: pdest, self.OUT_LINES: ldest,
                self.OUT_POLYGONS: gdest, self.OUT_AUX: aux_path}


class ExperimentalVariogramAlgorithm(IsolinerAlgorithm):
    INPUT, ZFIELD, GROUP_FIELD = "INPUT", "ZFIELD", "GROUP_FIELD"
    WEIGHT_FIELD = "WEIGHT_FIELD"
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
        return self.tr("1.05 Вариограмма (экспериментальная)")

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
            "(опц.) содержит лаг, γ(h) и количество пар для построения в QGIS."))

    def createInstance(self):
        return ExperimentalVariogramAlgorithm()

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точки со значениями"),
            types=[QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения Z"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(QgsProcessingParameterField(
            self.GROUP_FIELD,
            self.tr("Поле группировки (необязательно, напр. вид разведки)"),
            parentLayerParameterName=self.INPUT, optional=True))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.WEIGHT_FIELD,
            self.tr("Поле весов декластеризации (из 1.01, необязательно)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MIN_GROUP_PCT,
            self.tr("Минимум точек в группе, % от выборки (пол 30 точек)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.MIN_GROUP_PCT, 2.0),
            minValue=0.0, maxValue=100.0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.N_LAGS, self.tr("Количество лагов"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.N_LAGS, 15), minValue=3, maxValue=100))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAXLAG, self.tr("Максимальное расстояние, в единицах слоя (0 = пол-диагонали)"),
            QgsProcessingParameterNumber.Type.Double,
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
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.VAL_PCT, 0.0), minValue=0.0, maxValue=49.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MIN, self.tr("Нижняя граница значения (пусто = нет)"),
            QgsProcessingParameterNumber.Type.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MAX, self.tr("Верхняя граница значения (пусто = нет)"),
            QgsProcessingParameterNumber.Type.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.VAL_CAP, self.tr("Срезать к границе (capping) вместо удаления"),
            defaultValue=_dv(self, self.VAL_CAP, False))))
        self.addParameter(QgsProcessingParameterString(
            self.SAVE_PROFILE,
            self.tr("Сохранить профиль под именем (пусто = не сохранять)"),
            optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Таблица вариограммы (лаг, γ, количество пар)"),
            type=QgsProcessing.SourceType.TypeVector, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, self.tr("Отчёт (HTML)"),
            self.tr("HTML files (*.html)"), optional=True, createByDefault=True))

    def _opt(self, parameters, name, context):
        v = parameters.get(name, None)
        if v is None or v == "":
            return None
        return self.parameterAsDouble(parameters, name, context)

    def _process(self, parameters, context, feedback):
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
        wfield = self.parameterAsString(
            parameters, self.WEIGHT_FIELD, context) or None
        wts = None
        if wfield:
            xs, ys, vs, wids = _read_points(
                source, zfield, feedback, vmin=vmin, vmax=vmax, pct=pct,
                cap=cap, id_field=wfield, return_ids=True)
            try:
                wv = np.array([float(w) if w is not None else np.nan
                               for w in wids], float)
                if np.all(np.isfinite(wv)) and np.all(wv > 0):
                    wts = wv
                    feedback.pushInfo(_tr(
                        "Пары взвешены декластеризацией (поле «%s»).") % wfield)
                else:
                    feedback.pushWarning(_tr(
                        "Поле весов содержит пустые или неположительные "
                        "значения - веса игнорируются."))
            except Exception:
                feedback.pushWarning(_tr(
                    "Не удалось прочитать поле весов - веса игнорируются."))
        else:
            xs, ys, vs = _read_points(source, zfield, feedback,
                                      vmin=vmin, vmax=vmax, pct=pct, cap=cap)
        _warn_data(feedback, xs, ys, vs)
        data_var = float(np.var(vs))
        feedback.pushInfo(_tr("Точек: %d. Дисперсия данных: %.4g (ориентир для "
                          "суммарного порога).") % (len(xs), data_var))
        # порог размера группы: % от выборки, но не меньше 30 точек
        group_min = max(int(round(min_group_pct / 100.0 * len(xs))), 30)

        ev = experimental_variogram(xs, ys, vs, n_lags=n_lags, maxlag=maxlag,
                                     robust=robust, wts=wts,
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
                req.setFlags(QgsFeatureRequest.Flag.NoGeometry)
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
                    except Exception:  # nosec
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

        # таблица-слой (без геометрии): лаг, γ, количество пар, группа
        results = {}
        fields = QgsFields()
        fields.append(QgsField("series", QVariant.String))
        fields.append(QgsField("lag", QVariant.Double))
        fields.append(QgsField("gamma", QVariant.Double))
        fields.append(QgsField("npairs", QVariant.Int))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, QgsWkbTypes.Type.NoGeometry)
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
class ProfilesAlgorithm(IsolinerAlgorithm):
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

    def displayName(self): return self.tr("1.09 Профили обработки")

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
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.MODEL, self.tr("Модель: тип"),
            options=[_tr(x) for x in MODEL_LABELS], defaultValue=0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SILL, self.tr("Модель: порог/вклад C"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=1.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.RANGE, self.tr("Модель: радиус корреляции a"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.AZIMUTH, self.tr("Модель: азимут, °"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ANIS, self.tr("Модель: анизотропия (малая/главная)"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=1.0, minValue=EPS)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_PCT, self.tr("Отсев: перцентиль обрезки, % (0 = выкл.)"),
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0,
            minValue=0.0, maxValue=49.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MIN, self.tr("Отсев: нижняя граница (пусто = нет)"),
            QgsProcessingParameterNumber.Type.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.VAL_MAX, self.tr("Отсев: верхняя граница (пусто = нет)"),
            QgsProcessingParameterNumber.Type.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.VAL_CAP, self.tr("Отсев: срезать к границе вместо удаления"),
            defaultValue=False)))

    def _opt(self, parameters, name, context):
        v = parameters.get(name, None)
        if v is None or v == "":
            return None
        return self.parameterAsDouble(parameters, name, context)

    def _process(self, parameters, context, feedback):
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


class VariogramMapAlgorithm(IsolinerAlgorithm):
    INPUT, ZFIELD = "INPUT", "ZFIELD"
    WEIGHT_FIELD = "WEIGHT_FIELD"
    N_BINS, MAXLAG, MIN_PAIRS = "N_BINS", "MAXLAG", "MIN_PAIRS"
    OUTPUT_HTML, OUTPUT_RASTER = "OUTPUT_HTML", "OUTPUT_RASTER"
    WRITE_PROFILE = "WRITE_PROFILE"

    def tr(self, s): return _tr(s)
    def helpUrl(self): return _help_url()
    def name(self): return "variogram_map"
    def displayName(self): return self.tr("1.06 Вариограммная карта (анизотропия)")
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
            types=[QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения Z"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.WEIGHT_FIELD,
            self.tr("Поле весов декластеризации (из 1.01, необязательно)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric, optional=True)))
        self.addParameter(QgsProcessingParameterNumber(
            self.N_BINS, self.tr("Бинов на полуось (детализация карты)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.N_BINS, 15), minValue=5, maxValue=40))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAXLAG,
            self.tr("Макс. лаг, в единицах слоя (0 = пол-диагонали)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.MAXLAG, 0.0), minValue=0.0))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MIN_PAIRS, self.tr("Мин. количество пар в ячейке"),
            QgsProcessingParameterNumber.Type.Integer,
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

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        src = self.parameterAsSource(parameters, self.INPUT, context)
        if src is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        n_bins = self.parameterAsInt(parameters, self.N_BINS, context)
        maxlag = self.parameterAsDouble(parameters, self.MAXLAG, context)
        min_pairs = self.parameterAsInt(parameters, self.MIN_PAIRS, context)

        wfield = self.parameterAsString(
            parameters, self.WEIGHT_FIELD, context) or None
        wts = None
        if wfield:
            xs, ys, vs, wids = _read_points(
                src, zfield, feedback, id_field=wfield, return_ids=True)
            try:
                wv = np.array([float(w) if w is not None else np.nan
                               for w in wids], float)
                if np.all(np.isfinite(wv)) and np.all(wv > 0):
                    wts = wv
                    feedback.pushInfo(_tr(
                        "Пары взвешены декластеризацией (поле «%s»).") % wfield)
                else:
                    feedback.pushWarning(_tr(
                        "Поле весов содержит пустые или неположительные "
                        "значения - веса игнорируются."))
            except Exception:
                feedback.pushWarning(_tr(
                    "Не удалось прочитать поле весов - веса игнорируются."))
        else:
            xs, ys, vs = _read_points(src, zfield, feedback)
        _warn_data(feedback, xs, ys, vs)
        feedback.pushInfo(_tr("Вариограммная карта: %d точек…") % len(xs))
        m = variogram_map(xs, ys, vs, n_bins=n_bins,
                          maxlag=(maxlag if maxlag > 0 else None),
                          min_pairs=min_pairs, wts=wts)

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
                "количество бинов."))

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
        except Exception:  # nosec
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


class FlowGradientAlgorithm(IsolinerAlgorithm):
    INPUT, BAND = "INPUT", "BAND"
    SMOOTH_RADIUS = "SMOOTH_RADIUS"
    VECTOR_STEP = "VECTOR_STEP"
    OUTPUT, OUTPUT_AZIMUTH, OUTPUT_VECTORS = "OUTPUT", "OUTPUT_AZIMUTH", "OUTPUT_VECTORS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return FlowGradientAlgorithm()
    def name(self): return "flow_gradient"
    def displayName(self):
        return self.tr("3.04 Гидравлический градиент и направление потока")

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
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BAND, self.tr("Канал"),
            defaultValue=1,
            parentLayerParameterName=self.INPUT)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH_RADIUS,
            self.tr("Сглаживание напора перед расчётом, ячеек (0 = без)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.SMOOTH_RADIUS, 0.0),
            minValue=0.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.VECTOR_STEP, self.tr("Векторы потока: шаг прореживания, ячеек"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.VECTOR_STEP, 8), minValue=1, maxValue=200))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Гидравлический градиент (модуль)")))
        az = QgsProcessingParameterRasterDestination(
            self.OUTPUT_AZIMUTH, self.tr("Направление потока (азимут)"),
            optional=True, createByDefault=True)
        self.addParameter(az)
        vec = QgsProcessingParameterFeatureSink(
            self.OUTPUT_VECTORS, self.tr("Векторы потока (точки)"),
            type=QgsProcessing.SourceType.TypeVectorPoint, optional=True,
            createByDefault=True)
        self.addParameter(vec)

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.Point, crs)
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


class ExternalDriftKrigingAlgorithm(IsolinerAlgorithm):
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
        return self.tr("3.02 Кригинг с внешним дрейфом (External Drift)")

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
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения (Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric,
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
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.DRIFT_BAND, self.tr("Канал растра дрейфа"),
            defaultValue=1,
            parentLayerParameterName=self.DRIFT_RASTER)))
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
            QgsProcessingParameterNumber.Type.Double,
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

    def _process(self, parameters, context, feedback):
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


class ExceedanceProbabilityAlgorithm(IsolinerAlgorithm):
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
        return self.tr("3.03 Карта вероятности превышения")

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
            QgsProcessingParameterNumber.Type.Double, defaultValue=0.0))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BAND_EST, self.tr("Канал растра оценки"),
            defaultValue=1, parentLayerParameterName=self.ESTIMATE)))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BAND_SE, self.tr("Канал растра ошибки"),
            defaultValue=1, parentLayerParameterName=self.STDERR)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Растр вероятности (0…1)")))

    def _process(self, parameters, context, feedback):
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


class DarcyFluxAlgorithm(IsolinerAlgorithm):
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
        return self.tr("3.05 Удельный расход (закон Дарси)")

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
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BAND, self.tr("Канал напора"),
            defaultValue=1,
            parentLayerParameterName=self.INPUT)))
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
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.KBAND, self.tr("Канал растра K"),
            defaultValue=1,
            parentLayerParameterName=self.KRASTER)))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.TBAND, self.tr("Канал растра T"),
            defaultValue=1,
            parentLayerParameterName=self.TRASTER)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SMOOTH_RADIUS,
            self.tr("Сглаживание напора перед расчётом, ячеек (0 = без)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.SMOOTH_RADIUS, 0.0),
            minValue=0.0, maxValue=10.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.VECTOR_STEP, self.tr("Векторы потока: шаг прореживания, ячеек"),
            QgsProcessingParameterNumber.Type.Integer,
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
            type=QgsProcessing.SourceType.TypeVectorPoint, optional=True,
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

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.Point, crs)
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


class SectionDemoAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("4.10 Создать пример для разреза")
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
            QgsProcessingParameterNumber.Type.Integer,
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
            type=QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.WELLS, self.tr("Скважины вдоль линии (с отметками поверхностей)"),
            type=QgsProcessing.SourceType.TypeVectorPoint, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.BED1, self.tr("Пласт 1-й пром. (каналы: кровля, подошва, содержание, минтип)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.BED2, self.tr("Пласт 2-й пром. (каналы: кровля, подошва, содержание, минтип)"),
            optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.FAULT, self.tr("Разлом для пересечения (2D-линия)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.MARKER, self.tr("Маркер с отметкой Z (3D-линия)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True, createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.ZONE, self.tr("Зона замещения для пересечения (полигон)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.TIN, self.tr("Опрокинутая TIN (3D-грани для пересечения)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True,
            createByDefault=True))

    def _process(self, parameters, context, feedback):
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
            parameters, self.LINE, context, fields, QgsWkbTypes.Type.LineString, crs)
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
            parameters, self.WELLS, context, wf, QgsWkbTypes.Type.Point, crs)
        if wsink is not None:
            for i in range(nw):
                fw = QgsFeature(wf)
                fw.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(float(wx[i]), float(wy[i]))))
                fw.setAttributes(["SKR-%03d" % (i + 1)]
                                 + [float(hs[j][i]) for j in range(6)])
                wsink.addFeature(fw)
            _set_output_name(context, wdest, self.tr("Скважины разреза (демо)"))
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
            parameters, self.FAULT, context, ff, QgsWkbTypes.Type.LineString, crs)
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
            parameters, self.MARKER, context, mf, QgsWkbTypes.Type.LineStringZ, crs)
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
            parameters, self.ZONE, context, zf, QgsWkbTypes.Type.Polygon, crs)
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
            parameters, self.TIN, context, tf, QgsWkbTypes.Type.PolygonZ, crs)
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
    1, 2, 2.5, 5, 10 (×10^k) так, чтобы количество отметок было ближе всего к n."""
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


class SectionAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("4.01 Разрез по линии")
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.SURFACES, self.tr("Поверхности сверху вниз (кровли и подошвы)"),
            layerType=QgsProcessing.SourceType.TypeRaster))
        self.addParameter(QgsProcessingParameterNumber(
            self.STEP, self.tr("Шаг выборки вдоль линии, ед. карты (0 = по ячейке)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.STEP, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_2D, self.tr("Чертёж разреза (расстояние × высота)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Забор 3D (PolygonZ, реальные координаты)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_DEF,
            self.tr("Определение разреза (линия с полем vex для других тулз)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_CORNERS, self.tr("Угловые точки разреза (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorPoint, optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_CORNERS_V, self.tr("Угловые вертикали разреза (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_AXES, self.tr("Горизонтальные оси с отметками (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True,
            createByDefault=False))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_TABLE,
            self.tr("Таблица углов: азимут и расстояние (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True,
            createByDefault=False))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.NAXES, self.tr("Количество отметок высоты на осях"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NAXES, 5), minValue=2, maxValue=50)))

    def _fields(self):
        f = QgsFields()
        f.append(QgsField("bed", QVariant.Int))
        f.append(QgsField("top", QVariant.String))
        f.append(QgsField("bot", QVariant.String))
        f.append(QgsField("t_mean", QVariant.Double))
        f.append(QgsField("seclen", QVariant.Double))
        return f

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.Polygon, QgsCoordinateReferenceSystem())
        sink3d, dest3d = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f2,
            QgsWkbTypes.Type.PolygonZ, crs_line)
        fdef = QgsFields()
        fdef.append(QgsField("vex", QVariant.Double))
        fdef.append(QgsField("step", QVariant.Double))
        fdef.append(QgsField("zmin", QVariant.Double))
        fdef.append(QgsField("zmax", QVariant.Double))
        sinkdef, destdef = self.parameterAsSink(
            parameters, self.OUTPUT_DEF, context, fdef,
            QgsWkbTypes.Type.LineString, crs_line)
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
            QgsWkbTypes.Type.Point, crs0)
        sinkcv, destcv = self.parameterAsSink(
            parameters, self.OUTPUT_CORNERS_V, context, fcorn,
            QgsWkbTypes.Type.LineString, crs0)
        sinkax, destax = self.parameterAsSink(
            parameters, self.OUTPUT_AXES, context, faxis,
            QgsWkbTypes.Type.LineString, crs0)
        ftab = QgsFields()
        ftab.append(QgsField("kind", QVariant.String))
        ftab.append(QgsField("text", QVariant.String))
        sinktab, desttab = self.parameterAsSink(
            parameters, self.OUTPUT_TABLE, context, ftab,
            QgsWkbTypes.Type.Polygon, crs0)
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


class BoreholesOnSectionAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("4.02 Скважины на разрезе")
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DEF,
            self.tr("Определение разреза (для общего масштаба, опционально)"),
            types=[QgsProcessing.SourceType.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.WELLS, self.tr("Скважины"),
            types=[QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.LEVELS, self.tr("Поля отметок границ пластов (кровли и подошвы)"),
            parentLayerParameterName=self.WELLS,
            type=QgsProcessingParameterField.DataType.Numeric, allowMultiple=True))
        self.addParameter(QgsProcessingParameterField(
            self.LABEL, self.tr("Поле номера скважины (для подписи)"),
            parentLayerParameterName=self.WELLS, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CORRIDOR,
            self.tr("Коридор от линии, ед. карты (0 = все скважины)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CORRIDOR, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Интервалы пластов скважин (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_LABELS, self.tr("Устья скважин (подписи)"),
            type=QgsProcessing.SourceType.TypeVectorPoint, optional=True,
            createByDefault=True))

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.LineString, crs0)
        lsink, ldest = self.parameterAsSink(
            parameters, self.OUTPUT_LABELS, context, f_lab,
            QgsWkbTypes.Type.Point, crs0)

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


class CompositionOnSectionAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("4.03 Состав пласта на разрезе")
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DEF,
            self.tr("Определение разреза (для общего масштаба, опционально)"),
            types=[QgsProcessing.SourceType.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.TOP, self.tr("Кровля пласта (растр)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.BOTTOM, self.tr("Подошва пласта (растр)")))
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.COMP, self.tr("Грид состава (содержание или класс)")))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.TOP_BAND, self.tr("Канал кровли"),
            defaultValue=_dv(self, self.TOP_BAND, 1),
            parentLayerParameterName=self.TOP)))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BOTTOM_BAND, self.tr("Канал подошвы"),
            defaultValue=_dv(self, self.BOTTOM_BAND, 1),
            parentLayerParameterName=self.BOTTOM)))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.COMP_BAND, self.tr("Канал состава"),
            defaultValue=_dv(self, self.COMP_BAND, 1),
            parentLayerParameterName=self.COMP)))
        self.addParameter(QgsProcessingParameterEnum(
            self.MODE, self.tr("Состав"),
            options=[self.tr("непрерывное (содержание)"),
                     self.tr("категориальное (минтип, фации)")],
            defaultValue=_dv(self, self.MODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.STEP, self.tr("Шаг выборки вдоль линии, ед. карты (0 = по ячейке)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.STEP, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_2D, self.tr("Состав пласта (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Состав пласта (3D)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True,
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

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.Polygon, QgsCoordinateReferenceSystem())
        sink3d, dest3d = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f,
            QgsWkbTypes.Type.PolygonZ, crs_line)

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


class SectionGridIntersectAlgorithm(IsolinerAlgorithm):
    """Пересечение поверхностей-гридов с разрезом. По определению разреза (линия
    и vex) каждый грид выбирается вдоль линии и ложится на чертёж линией
    высота(расстояние). Так на разрез наносят водоносные горизонты, маркирующие
    поверхности, кровлю соли, аномалии - как линии в тех же осях, что и разрез."""

    LINE_DEF, GRIDS, STEP, SAMPLING = "LINE_DEF", "GRIDS", "STEP", "SAMPLING"
    OUTPUT, OUTPUT_3D = "OUTPUT", "OUTPUT_3D"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionGridIntersectAlgorithm()
    def name(self): return "section_intersect_grids"
    def displayName(self): return self.tr("4.04 Пересечение поверхностей с разрезом")
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.GRIDS, self.tr("Поверхности-гриды"),
            layerType=QgsProcessing.SourceType.TypeRaster))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.STEP, self.tr("Шаг выборки вдоль линии (0 = по ячейке)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.STEP, 0.0), minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Линии поверхностей на разрезе (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Линии поверхностей (3D)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True,
            createByDefault=False))

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.LineString, QgsCoordinateReferenceSystem())
        sink3, dest3 = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f,
            QgsWkbTypes.Type.LineStringZ, src.sourceCrs())

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


class SectionVectorIntersectAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("4.05 Пересечение векторов с разрезом")
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.TARGET, self.tr("Слои для пересечения (линии и полигоны)"),
            layerType=QgsProcessing.SourceType.TypeVectorAnyGeometry))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.SECTION2D,
            self.tr("Чертёж разреза (для высоты рамки, необязательно)"),
            optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZMIN, self.tr("Низ диапазона Z (если нет чертежа)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ZMIN, 0.0), optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZMAX, self.tr("Верх диапазона Z (если нет чертежа)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ZMAX, 0.0), optional=True)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_LINES, self.tr("Вертикали на разрезе (линии без Z)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_POINTS, self.tr("Точки пересечения (линии с Z)"),
            type=QgsProcessing.SourceType.TypeVectorPoint, optional=True,
            createByDefault=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_BANDS, self.tr("Полосы зон на разрезе (полигоны)"),
            type=QgsProcessing.SourceType.TypeVectorPolygon, optional=True,
            createByDefault=True))

    def _process(self, parameters, context, feedback):
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
                is_poly = (g.type() == QgsWkbTypes.GeometryType.PolygonGeometry)
                ag = g.constGet()
                has_z = bool(ag.is3D()) if ag is not None else False
                inter = line.intersection(g)
                if inter is None or inter.isEmpty():
                    continue
                for part in inter.asGeometryCollection():
                    pt = part.type()
                    if pt == QgsWkbTypes.GeometryType.PointGeometry:
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
                    elif pt == QgsWkbTypes.GeometryType.LineGeometry:
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
                                          fpoints, QgsWkbTypes.Type.Point, empty)
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
                                          flines, QgsWkbTypes.Type.LineString, empty)
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
                                          fbands, QgsWkbTypes.Type.Polygon, empty)
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


class SectionTinIntersectAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("4.06 Пересечение TIN с разрезом")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP3)
    def groupId(self): return GROUP3_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Режет TIN (поверхность из 3D-треугольников) разрезом и кладёт трассу "
            "на чертёж в осях расстояние-высота.\n\nГлавное отличие от "
            "«Пересечения поверхностей» (4.04, гриды): грид это z = f(x,y), одно "
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.FACES, self.tr("Грани TIN (слои 3D-полигонов, PolygonZ)"),
            layerType=QgsProcessing.SourceType.TypeVectorPolygon, optional=True))
        self.addParameter(QgsProcessingParameterMeshLayer(
            self.MESH, self.tr("Меш-слой (2.5D, для общности)"), optional=True))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Трасса TIN на разрезе (чертёж)"),
            type=QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT_3D, self.tr("Трасса TIN (3D)"),
            type=QgsProcessing.SourceType.TypeVectorLine, optional=True,
            createByDefault=False))

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.LineString, QgsCoordinateReferenceSystem())
        sink3, dest3 = self.parameterAsSink(
            parameters, self.OUTPUT_3D, context, f,
            QgsWkbTypes.Type.LineStringZ, scrs)
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


class SectionProjectAlgorithm(IsolinerAlgorithm):
    """Проекция объектов на разрез. Точки, линии и полигоны проецируются на линию
    разреза: горизонтальная координата - расстояние вдоль линии до проекции,
    высота - отметка вершины (из 3D-геометрии или из поля). Результат в осях
    разреза, поверх чертежа. Обобщение проекции скважин на любые объекты."""

    LINE_DEF, INPUT, ZFIELD, CORRIDOR, OUTPUT = (
        "LINE_DEF", "INPUT", "ZFIELD", "CORRIDOR", "OUTPUT")

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionProjectAlgorithm()
    def name(self): return "section_project_objects"
    def displayName(self): return self.tr("4.07 Проекция объектов на разрез (бета)")
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Объекты для проекции")))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле отметки (если геометрия без Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CORRIDOR, self.tr("Коридор от линии (0 = все объекты)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CORRIDOR, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Объекты на разрезе (чертёж)")))

    def _process(self, parameters, context, feedback):
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
        wkb = {0: QgsWkbTypes.Type.Point, 1: QgsWkbTypes.Type.LineString,
               2: QgsWkbTypes.Type.Polygon}.get(gtype, QgsWkbTypes.Type.Point)
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
            if wkb == QgsWkbTypes.Type.Point:
                geom = QgsGeometry.fromPointXY(verts[0])
            elif wkb == QgsWkbTypes.Type.LineString:
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


class SectionUnprojectAlgorithm(IsolinerAlgorithm):
    """Спроецировать с разреза. Объекты, нарисованные на чертеже разреза (оси
    расстояние-высота), возвращаются в реальные координаты: горизонталь читается
    как расстояние вдоль линии (точка на линии даёт X, Y), высота - как отметка
    Z = высота / vex. Так нарисованный на разрезе объект попадает в план и в 3D."""

    LINE_DEF, INPUT, OUTPUT = "LINE_DEF", "INPUT", "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SectionUnprojectAlgorithm()
    def name(self): return "section_unproject"
    def displayName(self): return self.tr("4.08 Спроецировать с разреза (бета)")
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
            types=[QgsProcessing.SourceType.TypeVectorLine]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Объекты с чертежа разреза")))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Объекты в плане (с отметкой Z)")))

    def _process(self, parameters, context, feedback):
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
        wkb = {0: QgsWkbTypes.Type.PointZ, 1: QgsWkbTypes.Type.LineStringZ,
               2: QgsWkbTypes.Type.PolygonZ}.get(gtype, QgsWkbTypes.Type.PointZ)
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
            if wkb == QgsWkbTypes.Type.PointZ:
                geom = QgsGeometry(pts[0])
            elif wkb == QgsWkbTypes.Type.LineStringZ:
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


class ShaftUnwrapAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("4.09 Развёртка стенки ствола (бета)")
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
            types=[QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Радиус ствола, ед. карты"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.RADIUS, 4.0), minValue=0.001))
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.SURFACES, self.tr("Поверхности-гриды (маркирующие)"),
            layerType=QgsProcessing.SourceType.TypeRaster))
        self.addParameter(QgsProcessingParameterNumber(
            self.ASTEP, self.tr("Угловой шаг, градусы"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ASTEP, 1.0), minValue=0.1, maxValue=45.0))
        self.addParameter(QgsProcessingParameterEnum(
            self.VMODE, self.tr("Вертикальный масштаб"),
            options=[self.tr("отношение Г:В (ширина:высота)"),
                     self.tr("множитель")],
            defaultValue=_dv(self, self.VMODE, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.VEXAG, self.tr("Значение масштаба (отношение Г:В или множитель)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.VEXAG, 10.0), minValue=0.01))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.SAMPLING, self.tr("Выборка растра"),
            options=[self.tr("билинейно"), self.tr("ближайший")],
            defaultValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Развёртка стенки (дуга × высота)"),
            type=QgsProcessing.SourceType.TypeVectorLine))

    def _process(self, parameters, context, feedback):
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
            QgsWkbTypes.Type.LineString, QgsCoordinateReferenceSystem())
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


class SequentialGaussianSimAlgorithm(IsolinerAlgorithm):
    INPUT, FIELD = "INPUT", "FIELD"
    WEIGHT_FIELD = "WEIGHT_FIELD"
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
    def displayName(self): return self.tr("3.06 Гауссова симуляция (SGS)")
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
            types=[QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.FIELD, self.tr("Поле значения"), parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.WEIGHT_FIELD,
            self.tr("Поле весов декластеризации (из 1.01, необязательно)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric, optional=True)))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Размер ячейки (0 = авто, min(охват)/50)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL_SIZE, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.NREAL, self.tr("Количество реализаций"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NREAL, 60), minValue=1, maxValue=1000))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD, self.tr("Порог отсечки для вероятности (опционально)"),
            QgsProcessingParameterNumber.Type.Double, optional=True))
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
            self.MAX_POINTS, self.tr("Макс. количество соседей на узел"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.MAX_POINTS, 16), minValue=2, maxValue=64)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Радиус поиска (0 = авто, 3 радиуса вариограммы)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.RADIUS, 0.0), minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно ГСЧ (0 = случайное)"),
            QgsProcessingParameterNumber.Type.Integer,
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

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        field = self.parameterAsString(parameters, self.FIELD, context)
        if source is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))
        wfield = self.parameterAsString(
            parameters, self.WEIGHT_FIELD, context) or None
        wts = None
        if wfield:
            xd, yd, vrd, wids = _read_points(
                source, field, feedback, id_field=wfield, return_ids=True)
            try:
                wv = np.array([float(w) if w is not None else np.nan
                               for w in wids], float)
                if np.all(np.isfinite(wv)) and np.all(wv > 0):
                    wts = wv
                    feedback.pushInfo(self.tr(
                        "Гистограмма строится с весами декластеризации "
                        "(поле «%s»).") % wfield)
                else:
                    feedback.pushWarning(self.tr(
                        "Поле весов содержит пустые или неположительные "
                        "значения - веса игнорируются."))
            except Exception:
                feedback.pushWarning(self.tr(
                    "Не удалось прочитать поле весов - веса игнорируются."))
        else:
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
                "Ансамбль крупный (>400 МБ в памяти). Уменьшите количество реализаций "
                "или огрубите ячейку, если не хватит памяти."))

        from .kb2d import (nscore_transform, experimental_variogram,
                           fit_variogram, Variogram, sgsim)
        ns, _sv, _sns = nscore_transform(vrd, wts=wts)
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
                     ndmin=1, ndmax=ndmax, rad2=rad2, seed=seed, progress=prog,
                     wts=wts)

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
            except Exception:  # nosec
                pass
            r = QgsMeshLayer3DRenderer(sym)
            r.setLayer(layer)
            layer.set3DRenderer(r)
        except Exception:  # nosec
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


class SectionSurfacesToMeshAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("5.04 Поверхности в 3D (меши)")
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
            "уменьшает количество узлов на крупных гридах.\n\n"
            "Слои загружаются в проект и получают 3D-отображение автоматически. "
            "Если сцена уже открыта, включите новые слои в её списке. Ячейки без "
            "данных пропускаются.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterMultipleLayers(
            self.GRIDS, self.tr("Поверхности-гриды"),
            layerType=QgsProcessing.SourceType.TypeRaster))
        self.addParameter(QgsProcessingParameterNumber(
            self.ZSCALE, self.tr("Масштаб Z (вертикальное преувеличение)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ZSCALE, 1.0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.ZOFFSET, self.tr("Смещение Z"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ZOFFSET, 0.0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.SPACING, self.tr("Разнос по Z (шаг на каждый следующий грид)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.SPACING, 0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.STEP, self.tr("Прореживание узлов (каждый N-й)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.STEP, 1), minValue=1)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZBAND, self.tr("Канал высот (Z)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.ZBAND, 1), minValue=1)))
        self.addParameter(QgsProcessingParameterFolderDestination(
            self.FOLDER, self.tr("Папка для мешей (2DM)")))

    def _process(self, parameters, context, feedback):
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
            except Exception:  # nosec
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


class BedAssembleAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("5.01 Собрать грид пласта")
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
            layerType=QgsProcessing.SourceType.TypeRaster, optional=True))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.ROOF_BAND, self.tr("Канал кровли"),
            defaultValue=_dv(self, self.ROOF_BAND, 1),
            parentLayerParameterName=self.ROOF)))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BOTTOM_BAND, self.tr("Канал подошвы"),
            defaultValue=_dv(self, self.BOTTOM_BAND, 1),
            parentLayerParameterName=self.BOTTOM)))
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

    def _process(self, parameters, context, feedback):
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


class BedCalculatorAlgorithm(IsolinerAlgorithm):
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
    def displayName(self): return self.tr("5.02 Калькулятор пласта")
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
        self.addParameter(QgsProcessingParameterBand(
            self.CONTENT_BAND,
            self.tr("Канал содержания (пусто - без содержания)"),
            defaultValue=_dv(self, self.CONTENT_BAND, 3),
            parentLayerParameterName=self.BED, optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.DENSITY, self.tr("Плотность руды, т/м³"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.DENSITY, 2.1), minValue=0.01))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CONTOUR, self.tr("Контур подсчёта (полигоны, необязательно)"),
            [QgsProcessing.SourceType.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Грид пласта с мощностью и запасами")))
        self.addParameter(QgsProcessingParameterFileDestination(
            self.REPORT, self.tr("Отчёт (HTML)"),
            self.tr("HTML-файлы (*.html)"), optional=True,
            createByDefault=True))

    def _process(self, parameters, context, feedback):
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


class BedToBlockModelAlgorithm(IsolinerAlgorithm):
    """Грид пласта -> блочная модель: точка-центроид на каждую валидную
    ячейку с атрибутами верха, низа, мощности, объёма, тоннажа и всех
    каналов параметров по их именам. Схема наращивается атрибутами
    (join, калькулятор полей) и готова к делению колонок по вертикали."""

    BED = "BED"
    DENSITY = "DENSITY"
    DENS_BAND = "DENS_BAND"
    CONTOUR = "CONTOUR"
    NZ = "NZ"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return BedToBlockModelAlgorithm()
    def name(self): return "bed_to_block_model"
    def displayName(self): return self.tr("5.03 Грид пласта в блочную модель")
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
            "или доменом.\n\nПараметр «Слоёв по вертикали» делит каждую "
            "колонку на N блоков между кровлей и подошвой: у каждого свои "
            "z_from, z_to, номер слоя lay и доля объёма. Содержание "
            "копируется в под-блоки (по вертикали оно не разбурено). Это "
            "заготовка настоящей 3D-модели.\n\nПлотность берётся из "
            "числа выше или, если задан «Канал плотности», из этого канала "
            "грида поячеечно - для переменной по площади плотности руды.")
            + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.BED, self.tr("Грид пласта (канал 1 кровля, канал 2 подошва)")))
        self.addParameter(QgsProcessingParameterNumber(
            self.DENSITY, self.tr("Плотность руды, т/м³"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.DENSITY, 2.1), minValue=0.01))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.DENS_BAND,
            self.tr("Канал плотности (пусто - брать значение выше)"),
            parentLayerParameterName=self.BED, optional=True)))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CONTOUR, self.tr("Контур подсчёта (полигоны, необязательно)"),
            [QgsProcessing.SourceType.TypeVectorPolygon], optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.NZ, self.tr("Слоёв по вертикали (деление колонки)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NZ, 1), minValue=1, maxValue=100)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Блочная модель (центроиды)"),
            QgsProcessing.SourceType.TypeVectorPoint))

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        bed_l = self.parameterAsRasterLayer(parameters, self.BED, context)
        dens = self.parameterAsDouble(parameters, self.DENSITY, context)
        _db = self.parameterAsString(parameters, self.DENS_BAND, context)
        try:
            dens_band = int(_db) if _db not in (None, "") else 0
        except (TypeError, ValueError):
            dens_band = 0
        contour = self.parameterAsSource(parameters, self.CONTOUR, context)
        nz = max(self.parameterAsInt(parameters, self.NZ, context), 1)

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
        if dens_band and dens_band <= len(stack):
            dens_arr = stack[dens_band - 1]
        else:
            dens_arr = np.full_like(roof, dens, dtype=float)
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

        used = {"bid", "row", "col", "lay", "x", "y", "top", "bot",
                "z_from", "z_to", "thick", "vol", "dens", "ore_t"}
        pnames = [_safe(nm, used) for nm in names[2:]]
        fields = QgsFields()
        for nm, tp in (("bid", QVariant.Int), ("row", QVariant.Int),
                       ("col", QVariant.Int), ("lay", QVariant.Int),
                       ("x", QVariant.Double), ("y", QVariant.Double),
                       ("top", QVariant.Double), ("bot", QVariant.Double),
                       ("z_from", QVariant.Double), ("z_to", QVariant.Double),
                       ("thick", QVariant.Double), ("vol", QVariant.Double),
                       ("dens", QVariant.Double), ("ore_t", QVariant.Double)):
            fields.append(QgsField(nm, tp))
        for nm in pnames:
            fields.append(QgsField(nm, QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.Point, bed_l.crs())

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
            r_top = float(roof[i, j])
            r_bot = float(bot[i, j])
            th_full = float(thick[i, j])
            d_ij = float(dens_arr[i, j])
            if not (d_ij == d_ij) or d_ij <= 0:
                d_ij = dens
            dz = th_full / nz
            params = [(float(a[i, j]) if a[i, j] == a[i, j] else None)
                      for a in stack[2:]]
            for L in range(nz):
                zf = r_top - L * dz          # сверху вниз
                zt = r_top - (L + 1) * dz
                bid += 1
                vol = dz * cell
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(x, y)))
                attrs = [bid, int(i), int(j), L, float(x), float(y),
                         r_top, r_bot, zf, zt, dz, vol, d_ij, vol * d_ij]
                attrs.extend(params)
                f.setAttributes(attrs)
                sink.addFeature(f)
        _set_output_name(context, dest,
                         self.tr("Блочная модель: %s") % bed_l.name())
        feedback.pushInfo(_tr("Блоков выгружено: %d.") % bid)
        _save_values(self, _saved)
        return {self.OUTPUT: dest}


class DomainsToGridAlgorithm(IsolinerAlgorithm):
    """Полигоны доменов -> добавочный канал грида пласта с кодом домена
    в каждой ячейке. Дальше калькулятор и блочная модель считают по
    доменам, а разность двух состояний даёт списание запасов."""

    BED = "BED"
    DOMAINS = "DOMAINS"
    FIELD = "FIELD"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return DomainsToGridAlgorithm()
    def name(self): return "domains_to_grid"
    def displayName(self): return self.tr("5.05 Домены в канал пласта")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP4)
    def groupId(self): return GROUP4_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Растеризует полигоны доменов в добавочный канал грида пласта: "
            "каждой ячейке присваивается код домена, в который она попадает "
            "(0 - вне доменов). Код берётся из числового поля слоя или, если "
            "поле не задано, это порядковый номер объекта от 1. Каналы "
            "исходного грида сохраняются, канал «domain» дописывается "
            "последним.\n\nДальше домен работает как обычный параметр: "
            "калькулятор пласта считает по контуру домена, блочная модель "
            "фильтруется по коду. Списание запасов - это разность двух "
            "состояний домена: посчитайте запасы по контуру до и после "
            "погашения, вычтите. Контуры доменов должны лежать в той же "
            "системе координат, что и грид.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.BED, self.tr("Грид пласта")))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.DOMAINS, self.tr("Полигоны доменов"),
            [QgsProcessing.SourceType.TypeVectorPolygon]))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.FIELD, self.tr("Поле кода домена (число, необязательно)"),
            parentLayerParameterName=self.DOMAINS, optional=True,
            type=QgsProcessingParameterField.DataType.Numeric)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Грид пласта с каналом domain")))

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        bed_l = self.parameterAsRasterLayer(parameters, self.BED, context)
        domains = self.parameterAsSource(parameters, self.DOMAINS, context)
        field = self.parameterAsString(parameters, self.FIELD, context)
        out = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

        ds = gdal.Open(bed_l.source())
        if ds is None:
            raise QgsProcessingException(self.tr("Грид не открылся."))
        gt = ds.GetGeoTransform()
        ny, nx = ds.RasterYSize, ds.RasterXSize
        stack, names = [], []
        for i in range(1, ds.RasterCount + 1):
            b = ds.GetRasterBand(i)
            a = b.ReadAsArray().astype(np.float32)
            nd = b.GetNoDataValue()
            if nd is not None:
                a = np.where(a == nd, np.nan, a).astype(np.float32)
            stack.append(a)
            names.append(b.GetDescription() or ("band%d" % i))
        ds = None

        domain = np.zeros((ny, nx), dtype=np.float32)
        n_assigned = 0
        for k, ft in enumerate(domains.getFeatures(), start=1):
            if feedback.isCanceled():
                break
            g = ft.geometry()
            if g is None or g.isEmpty():
                continue
            code = k
            if field:
                v = ft[field]
                if v is not None:
                    try:
                        code = float(v)
                    except (TypeError, ValueError):
                        code = k
            rings = []
            try:
                mp = g.asMultiPolygon()
            except Exception:
                mp = []
            if not mp:
                try:
                    p1 = g.asPolygon()
                except Exception:
                    p1 = []
                mp = [p1] if p1 else []
            for poly in mp:
                for ring in poly:
                    rings.append([(p.x(), p.y()) for p in ring])
            if not rings:
                continue
            m = polygon_mask(rings, gt, (ny, nx))
            domain[m] = code
            n_assigned += int(m.sum())

        stack.append(domain)
        names.append("domain")
        crs_wkt = bed_l.crs().toWkt() if bed_l.crs().isValid() else ""
        _write_grid_tiff(out, stack, gt, crs_wkt, -9999.0, nx, ny,
                         band_names=names)
        feedback.pushInfo(
            _tr("Домены записаны в канал %d. Ячеек в доменах: %d.")
            % (len(stack), n_assigned))
        _save_values(self, _saved)
        return {self.OUTPUT: out}


class ReserveDeltaAlgorithm(IsolinerAlgorithm):
    """Разность двух блочных моделей по совпадающим ячейкам: списание
    запасов между состояниями (было -> стало)."""

    BEFORE = "BEFORE"
    AFTER = "AFTER"
    FIELD = "FIELD"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return ReserveDeltaAlgorithm()
    def name(self): return "reserve_delta"
    def displayName(self): return self.tr("5.06 Разность запасов (списание)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP4)
    def groupId(self): return GROUP4_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Считает разность двух блочных моделей по ячейкам с одинаковыми "
            "row и col: сколько запаса убыло между состояниями «было» и "
            "«стало». Для каждой ячейки вычитается выбранное поле (по "
            "умолчанию ore_t), результат - точки со значениями delta "
            "(было минус стало), before и after.\n\nЭто прямой путь "
            "оперативного списания: модель до погашения камер минус модель "
            "после - и сумма delta по контуру даёт списанный тоннаж. Модели "
            "должны быть построены из одного грида (совпадающая нарезка row "
            "и col).") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.BEFORE, self.tr("Модель «было» (центроиды)"),
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.AFTER, self.tr("Модель «стало» (центроиды)"),
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.FIELD, self.tr("Поле запаса"),
            parentLayerParameterName=self.BEFORE,
            defaultValue="ore_t",
            type=QgsProcessingParameterField.DataType.Numeric)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Разность (центроиды)"),
            QgsProcessing.SourceType.TypeVectorPoint))

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        before = self.parameterAsSource(parameters, self.BEFORE, context)
        after = self.parameterAsSource(parameters, self.AFTER, context)
        field = self.parameterAsString(parameters, self.FIELD, context) \
            or "ore_t"

        def _key_vals(src):
            d = {}
            for ft in src.getFeatures():
                try:
                    key = (ft["row"], ft["col"], ft["lay"]
                           if "lay" in [f.name() for f in src.fields()]
                           else 0)
                except KeyError:
                    continue
                v = ft[field] if field in [f.name() for f in src.fields()] \
                    else None
                d[key] = (float(v) if v is not None else 0.0, ft.geometry())
            return d

        db = _key_vals(before)
        da = _key_vals(after)
        fields = QgsFields()
        for nm in ("row", "col", "lay"):
            fields.append(QgsField(nm, QVariant.Int))
        for nm in ("before", "after", "delta"):
            fields.append(QgsField(nm, QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.Point, before.sourceCrs())
        total_delta = 0.0
        for key, (vb, geom) in db.items():
            va = da.get(key, (0.0, None))[0]
            delta = vb - va
            total_delta += delta
            f = QgsFeature(fields)
            f.setGeometry(geom)
            f.setAttributes([int(key[0]), int(key[1]), int(key[2]),
                             vb, va, delta])
            sink.addFeature(f)
        feedback.pushInfo(
            _tr("Суммарное списание по полю %s: %.6g.") % (field, total_delta))
        _set_output_name(context, dest, self.tr("Разность (центроиды)"))
        _save_values(self, _saved)
        return {self.OUTPUT: dest}


class MinCurvatureAlgorithm(IsolinerAlgorithm):
    """Гридирование методом минимальной кривизны (бигармония с натяжением).
    Поверхность как тонкая упругая пластина через данные с минимумом изгиба
    (Briggs, 1974). Часто применяется для карт геофизических полей."""

    INPUT, ZFIELD = "INPUT", "ZFIELD"
    EXTENT, CELL_SIZE = "EXTENT", "CELL_SIZE"
    MAX_RESIDUAL, MAX_ITER, RELAX = "MAX_RESIDUAL", "MAX_ITER", "RELAX"
    TENSION, BOUNDARY_TENSION, ANISO = "TENSION", "BOUNDARY_TENSION", "ANISO"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return MinCurvatureAlgorithm()
    def name(self): return "min_curvature"
    def displayName(self):
        return self.tr("1.03 Минимальная кривизна (точки → растр)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP)
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Строит грид методом минимальной кривизны: поверхность ведёт "
            "себя как тонкая упругая пластина, проходящая через данные с "
            "минимумом изгиба (решение бигармонического уравнения). Метод "
            "неточный - данные воспроизводятся приближённо, зато поверхность "
            "максимально гладкая, поэтому его любят для карт геофизических "
            "полей и любых плавных величин.\n\nНатяжение подмешивает "
            "мембранный член: 0 - чистая минимальная кривизна, 1 - "
            "натянутая мембрана (меньше выбросов между пробами). Отдельно "
            "задаётся натяжение на границе. Решение итеративное (SOR обходом "
            "девятью цветами): сетка сходится, пока изменение узла не станет "
            "меньше порога невязки или не исчерпаются итерации.\n\nРазмер "
            "ячейки 0 = min(охват)/50. Порог невязки 0 = 0.01 процента от "
            "размаха данных. Выход - грид, готовый для «1.2 Изолинии из "
            "растра». Это детерминированная альтернатива кригингу без "
            "подбора вариограммы; кригинг же даёт оценку с погрешностью.")
            + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точечный слой"),
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения (Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric,
            defaultValue=_dv(self, self.ZFIELD, None)))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Охват (0 = по точкам)"), optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL_SIZE, self.tr("Размер ячейки (0 = авто, min(охват)/50)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL_SIZE, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.TENSION, self.tr("Натяжение (0 - мин. кривизна, 1 - мембрана)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.TENSION, 0.0),
            minValue=0.0, maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_RESIDUAL,
            self.tr("Порог невязки (0 = авто, 0.01% размаха)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.MAX_RESIDUAL, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MAX_ITER, self.tr("Максимум итераций"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.MAX_ITER, 100000),
            minValue=10, maxValue=5000000))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BOUNDARY_TENSION, self.tr("Натяжение на границе"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.BOUNDARY_TENSION, 0.0),
            minValue=0.0, maxValue=1.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.RELAX, self.tr("Коэффициент релаксации (SOR)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.RELAX, 1.85),
            minValue=0.1, maxValue=1.99)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ANISO, self.tr("Анизотропия (отношение осей Y/X)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ANISO, 1.0),
            minValue=0.05, maxValue=20.0)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Грид (минимальная кривизна)")))

    def _process(self, parameters, context, feedback):
        import math
        from . import mincurv as mc
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        xs, ys, vs = _read_points(source, zfield, feedback)

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
        nx = max(int(math.ceil(width / cell)), 2)
        ny = max(int(math.ceil(height / cell)), 2)

        tension = self.parameterAsDouble(parameters, self.TENSION, context)
        btens = self.parameterAsDouble(
            parameters, self.BOUNDARY_TENSION, context)
        relax = self.parameterAsDouble(parameters, self.RELAX, context)
        aniso = self.parameterAsDouble(parameters, self.ANISO, context)
        max_iter = self.parameterAsInt(parameters, self.MAX_ITER, context)
        max_res = self.parameterAsDouble(
            parameters, self.MAX_RESIDUAL, context)
        zrange = float(np.nanmax(vs) - np.nanmin(vs)) or 1.0
        tol = max_res if max_res > 0 else max(1e-6, 1e-4 * zrange)
        feedback.pushInfo(self.tr(
            "Сетка %d x %d, ячейка %.4g. Порог невязки %.4g.")
            % (nx, ny, cell, tol))

        z0, fixed = mc.grid_points(xs, ys, vs, xmin, ymin, cell, nx, ny)
        feedback.pushInfo(self.tr("Узлов-данных: %d из %d.")
                          % (int(fixed.sum()), nx * ny))

        def prog(it, mx, res):
            if feedback.isCanceled():
                raise QgsProcessingException(self.tr("Прервано пользователем."))
            feedback.setProgress(min(95, int(95.0 * it / max(mx, 1))))

        grid, iters, last = mc.solve(
            z0, fixed, tension=tension, boundary_tension=btens,
            max_iter=max_iter, tol=tol, relax=relax, aniso=aniso,
            progress=prog)
        if last <= tol:
            feedback.pushInfo(self.tr(
                "Сошлось за %d итераций (невязка %.4g).") % (iters, last))
        else:
            feedback.pushWarning(self.tr(
                "Достигнут потолок %d итераций, невязка %.4g больше порога "
                "%.4g. Увеличьте число итераций или порог невязки.")
                % (iters, last, tol))

        nodata = -9999.0
        geotr = (xmin, cell, 0.0, ymin + ny * cell, 0.0, -cell)
        wkt = crs.toWkt() if (crs is not None and crs.isValid()) else None
        out_path = self.parameterAsOutputLayer(
            parameters, self.OUTPUT, context)
        _write_grid_tiff(out_path, grid.astype("float32"), geotr, wkt,
                         nodata, nx, ny, band_names=[zfield])
        _save_values(self, _saved)
        return {self.OUTPUT: out_path}


class MethodCrossValidationAlgorithm(IsolinerAlgorithm):
    """Скользящий контроль (leave-one-out) метода гридирования: кригинг или
    минимальная кривизна. Оценивает качество метода по ошибке, как в Surfer:
    случайная выборка точек, фильтры, буфер исключения соседей."""

    INPUT, ZFIELD, IDFIELD = "INPUT", "ZFIELD", "IDFIELD"
    WEIGHT_FIELD = "WEIGHT_FIELD"
    METHOD = "METHOD"
    KTYPE, SKMEAN, NUGGET = "KTYPE", "SKMEAN", "NUGGET"
    RADIUS, MIN_POINTS, MAX_POINTS = "RADIUS", "MIN_POINTS", "MAX_POINTS"
    VAL_PCT, VAL_MIN, VAL_MAX, VAL_CAP = \
        "VAL_PCT", "VAL_MIN", "VAL_MAX", "VAL_CAP"
    EXTENT, CELL_SIZE = "EXTENT", "CELL_SIZE"
    TENSION, MAX_RESIDUAL, MAX_ITER = "TENSION", "MAX_RESIDUAL", "MAX_ITER"
    RELAX, BOUNDARY_TENSION, ANISO = "RELAX", "BOUNDARY_TENSION", "ANISO"
    N_VALIDATE, SEED = "N_VALIDATE", "SEED"
    FILTER_EXTENT, ZMIN, ZMAX = "FILTER_EXTENT", "ZMIN", "ZMAX"
    EXCL_X, EXCL_Y = "EXCL_X", "EXCL_Y"
    OUTPUT, OUTPUT_HTML = "OUTPUT", "OUTPUT_HTML"

    _METHODS = ("kriging", "mincurv")

    def tr(self, s): return _tr(s)
    def createInstance(self): return MethodCrossValidationAlgorithm()
    def name(self): return "method_crossvalidation"
    def displayName(self):
        return self.tr("1.08 Кросс-валидация метода (LOO)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP)
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Скользящий контроль (leave-one-out) для метода гридирования - "
            "кригинга или минимальной кривизны. Каждая проверяемая точка по "
            "очереди исключается, её значение предсказывается методом по "
            "остальным и сравнивается с фактом. По ошибкам считаются ME "
            "(смещение), MAE, RMSE и R - объективная оценка качества метода "
            "и сравнение методов между собой.\n\nКак в Surfer: можно "
            "проверять случайную выборку из N точек (на больших данных "
            "быстрее), ограничить проверку подобластью (фильтр по охвату и "
            "по значению) и задать буфер исключения - соседние точки в "
            "прямоугольнике вокруг проверяемой не участвуют в её оценке "
            "(нужно для сгущённых кластеров, иначе оценка просто повторяет "
            "соседа).\n\nВыходы: слой точек с ошибками и HTML-отчёт (график "
            "оценка/факт, гистограмма, метрики). Для минимальной кривизны "
            "переоценка идёт с тёплого старта от полного решения, поэтому "
            "каждая точка считается быстро, но на очень больших выборках "
            "уменьшайте N.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.INPUT, self.tr("Точки со значениями"),
            [QgsProcessing.SourceType.TypeVectorPoint]))
        self.addParameter(QgsProcessingParameterField(
            self.ZFIELD, self.tr("Поле значения (Z)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric,
            defaultValue=_dv(self, self.ZFIELD, None)))
        self.addParameter(QgsProcessingParameterField(
            self.IDFIELD, self.tr("Поле номера скважины (необязательно)"),
            parentLayerParameterName=self.INPUT, optional=True))
        self.addParameter(_advanced(QgsProcessingParameterField(
            self.WEIGHT_FIELD,
            self.tr("Поле весов декластеризации (из 1.01, необязательно)"),
            parentLayerParameterName=self.INPUT,
            type=QgsProcessingParameterField.DataType.Numeric, optional=True)))
        self.addParameter(QgsProcessingParameterEnum(
            self.METHOD, self.tr("Метод"),
            options=[self.tr("Кригинг"), self.tr("Минимальная кривизна")],
            defaultValue=_dv(self, self.METHOD, 0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.N_VALIDATE,
            self.tr("Проверяемых точек (0 = авто, min(N, 100))"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.N_VALIDATE, 0), minValue=0))
        self.addParameter(QgsProcessingParameterNumber(
            self.EXCL_X, self.tr("Буфер исключения по X (0 = выкл.)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EXCL_X, 0.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.EXCL_Y, self.tr("Буфер исключения по Y (0 = выкл.)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EXCL_Y, 0.0), minValue=0.0))
        # --- параметры кригинга (вариограмма и поиск) ---
        _add_cv_params(self)
        # --- параметры минимальной кривизны ---
        self.addParameter(_advanced(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Мин. кривизна: охват сетки (0 = по точкам)"),
            optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.CELL_SIZE,
            self.tr("Мин. кривизна: размер ячейки (0 = авто)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL_SIZE, 0.0), minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.TENSION, self.tr("Мин. кривизна: натяжение (0..1)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.TENSION, 0.0),
            minValue=0.0, maxValue=1.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_RESIDUAL,
            self.tr("Мин. кривизна: порог невязки (0 = авто)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.MAX_RESIDUAL, 0.0), minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_ITER, self.tr("Мин. кривизна: максимум итераций"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.MAX_ITER, 100000), minValue=10)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BOUNDARY_TENSION,
            self.tr("Мин. кривизна: натяжение на границе"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.BOUNDARY_TENSION, 0.0),
            minValue=0.0, maxValue=1.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.RELAX, self.tr("Мин. кривизна: коэффициент релаксации"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.RELAX, 1.85),
            minValue=0.1, maxValue=1.99)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ANISO, self.tr("Мин. кривизна: анизотропия (Y/X)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.ANISO, 1.0),
            minValue=0.05, maxValue=20.0)))
        # --- фильтр области проверки ---
        self.addParameter(_advanced(QgsProcessingParameterExtent(
            self.FILTER_EXTENT,
            self.tr("Проверять только в охвате (0 = везде)"), optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZMIN, self.tr("Проверять при Z не ниже (пусто = нет)"),
            QgsProcessingParameterNumber.Type.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ZMAX, self.tr("Проверять при Z не выше (пусто = нет)"),
            QgsProcessingParameterNumber.Type.Double, optional=True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно ГСЧ (0 = случайно)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.SEED, 0), minValue=0))
        )
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Ошибки кросс-валидации"),
            QgsProcessing.SourceType.TypeVectorPoint))
        html = QgsProcessingParameterFileDestination(
            self.OUTPUT_HTML, self.tr("HTML-отчёт"),
            self.tr("HTML (*.html)"), optional=True, createByDefault=True)
        self.addParameter(html)

    def _process(self, parameters, context, feedback):
        import math
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        if source is None:
            raise QgsProcessingException(self.tr("Не задан точечный слой."))
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        idfield = self.parameterAsString(
            parameters, self.IDFIELD, context) or None
        method = self._METHODS[self.parameterAsEnum(
            parameters, self.METHOD, context)]

        def _opt(name):
            v = parameters.get(name, None)
            if v is None or v == "":
                return None
            return self.parameterAsDouble(parameters, name, context)
        pct = self.parameterAsDouble(parameters, self.VAL_PCT, context)
        cap = self.parameterAsBool(parameters, self.VAL_CAP, context)
        xs, ys, vs, ids = _read_points(
            source, zfield, feedback,
            vmin=_opt(self.VAL_MIN), vmax=_opt(self.VAL_MAX),
            pct=pct, cap=cap, id_field=idfield, return_ids=True)
        n = len(xs)
        wfield = self.parameterAsString(
            parameters, self.WEIGHT_FIELD, context) or None
        mcw = None
        if wfield:
            _wx, _wy, _wv, wids = _read_points(
                source, zfield, feedback,
                vmin=_opt(self.VAL_MIN), vmax=_opt(self.VAL_MAX),
                pct=pct, cap=cap, id_field=wfield, return_ids=True)
            try:
                wv = np.array([float(w) if w is not None else np.nan
                               for w in wids], float)
                if wv.size == n and np.all(np.isfinite(wv)) and np.all(wv > 0):
                    mcw = wv
                    feedback.pushInfo(self.tr(
                        "Метрики взвешены декластеризацией (поле «%s»).")
                        % wfield)
                else:
                    feedback.pushWarning(self.tr(
                        "Поле весов содержит пустые или неположительные "
                        "значения - веса игнорируются."))
            except Exception:
                feedback.pushWarning(self.tr(
                    "Не удалось прочитать поле весов - веса игнорируются."))

        # --- отбор проверяемых точек: фильтр области и по значению ---
        crs = source.sourceCrs()
        cand = np.ones(n, dtype=bool)
        frect = self.parameterAsExtent(
            parameters, self.FILTER_EXTENT, context, crs)
        if frect is not None and not frect.isEmpty():
            cand &= (xs >= frect.xMinimum()) & (xs <= frect.xMaximum()) \
                & (ys >= frect.yMinimum()) & (ys <= frect.yMaximum())
        zmin = _opt(self.ZMIN); zmax = _opt(self.ZMAX)
        if zmin is not None:
            cand &= vs >= zmin
        if zmax is not None:
            cand &= vs <= zmax
        cand_idx = np.where(cand)[0]
        if len(cand_idx) < 2:
            raise QgsProcessingException(self.tr(
                "После фильтров осталось меньше двух проверяемых точек."))

        nreq = self.parameterAsInt(parameters, self.N_VALIDATE, context)
        if nreq <= 0:
            nreq = min(len(cand_idx), 100)
        nreq = min(nreq, len(cand_idx))
        seed = self.parameterAsInt(parameters, self.SEED, context)
        rng = np.random.default_rng(seed if seed > 0 else None)
        if nreq < len(cand_idx):
            val_idx = np.sort(rng.choice(cand_idx, size=nreq, replace=False))
        else:
            val_idx = cand_idx
        excl_x = self.parameterAsDouble(parameters, self.EXCL_X, context)
        excl_y = self.parameterAsDouble(parameters, self.EXCL_Y, context)
        nodata = -9999.0
        feedback.pushInfo(self.tr(
            "Метод: %s. Проверяем %d из %d точек.")
            % (self.tr("кригинг") if method == "kriging"
               else self.tr("минимальная кривизна"), len(val_idx), n))

        est = np.full(len(val_idx), np.nan)
        var = None
        used_params = None

        if method == "kriging":
            ktype = 1 if self.parameterAsEnum(
                parameters, self.KTYPE, context) == 0 else 0
            skmean = self.parameterAsDouble(parameters, self.SKMEAN, context)
            nugget = self.parameterAsDouble(parameters, self.NUGGET, context)
            radius = self.parameterAsDouble(parameters, self.RADIUS, context)
            ndmin = self.parameterAsInt(parameters, self.MIN_POINTS, context)
            ndmax = self.parameterAsInt(parameters, self.MAX_POINTS, context)
            width = float(xs.max() - xs.min())
            height = float(ys.max() - ys.min())
            auto_range = max(width, height) / 3.0 or 1.0
            if radius <= 0:
                radius = math.hypot(width, height) or 1e12
            rad2 = radius * radius
            vg = _build_variogram(self, parameters, context, nugget,
                                  auto_range, feedback)
            var = np.full(len(val_idx), np.nan)
            from .kb2d import krige_point
            idxall = np.arange(n)
            for k, i in enumerate(val_idx):
                if feedback.isCanceled():
                    raise QgsProcessingException(self.tr("Прервано."))
                drop = idxall == i
                if excl_x > 0 or excl_y > 0:
                    drop = drop | ((np.abs(xs - xs[i]) <= excl_x)
                                   & (np.abs(ys - ys[i]) <= excl_y))
                keep = ~drop
                if int(keep.sum()) < max(1, ndmin):
                    continue
                e, v = krige_point(
                    float(xs[i]), float(ys[i]), xs[keep], ys[keep], vs[keep],
                    vg, ktype, skmean, ndmin, ndmax, rad2, nodata,
                    return_var=True)
                if e != nodata:
                    est[k] = e
                    var[k] = v
                feedback.setProgress(int(95.0 * (k + 1) / len(val_idx)))
            used_params = _cv_used_params(self, parameters, context)
        else:
            from . import mincurv as mc
            rect = self.parameterAsExtent(
                parameters, self.EXTENT, context, crs)
            if rect is None or rect.isEmpty():
                rect = source.sourceExtent()
            xmin, xmax = rect.xMinimum(), rect.xMaximum()
            ymin, ymax = rect.yMinimum(), rect.yMaximum()
            width, height = xmax - xmin, ymax - ymin
            cell = self.parameterAsDouble(parameters, self.CELL_SIZE, context)
            if cell <= 0:
                cell = (min(width, height) / 50.0) or 1.0
            nx = max(int(math.ceil(width / cell)), 2)
            ny = max(int(math.ceil(height / cell)), 2)
            tension = self.parameterAsDouble(parameters, self.TENSION, context)
            btens = self.parameterAsDouble(
                parameters, self.BOUNDARY_TENSION, context)
            relax = self.parameterAsDouble(parameters, self.RELAX, context)
            aniso = self.parameterAsDouble(parameters, self.ANISO, context)
            max_iter = self.parameterAsInt(parameters, self.MAX_ITER, context)
            max_res = self.parameterAsDouble(
                parameters, self.MAX_RESIDUAL, context)
            zr = float(np.nanmax(vs) - np.nanmin(vs)) or 1.0
            tol = max_res if max_res > 0 else max(1e-6, 1e-4 * zr)
            feedback.pushInfo(self.tr(
                "Сетка %d x %d, ячейка %.4g.") % (nx, ny, cell))

            def prog(done, total):
                if feedback.isCanceled():
                    raise QgsProcessingException(self.tr("Прервано."))
                feedback.setProgress(int(95.0 * done / max(total, 1)))

            est, _zf = mc.loo_estimates(
                xs, ys, vs, xmin, ymin, cell, nx, ny, val_idx,
                tension=tension, boundary_tension=btens, relax=relax,
                aniso=aniso, tol=tol, base_iter=max_iter,
                loo_iter=max(2000, max_iter // 20),
                excl_x=excl_x, excl_y=excl_y, progress=prog)
            used_params = [
                (self.tr("Метод"), self.tr("минимальная кривизна")),
                (self.tr("Натяжение"), "%.3g" % tension),
                (self.tr("Сетка"), "%d x %d" % (nx, ny)),
                (self.tr("Ячейка"), "%.4g" % cell),
            ]

        fact = vs[val_idx]
        ok = np.isfinite(est)
        nvalid = int(ok.sum())
        if nvalid < 2:
            raise QgsProcessingException(self.tr(
                "Слишком мало оценённых точек."))
        err = est[ok] - fact[ok]
        _w_ok = (mcw[val_idx][ok] if mcw is not None else None)
        me, mae, rmse, msdr, r = _weighted_cv_metrics(
            fact[ok], est[ok], err,
            (var[ok] if var is not None else None), _w_ok)

        feedback.pushInfo(self.tr("== Кросс-валидация метода (LOO) =="))
        feedback.pushInfo(self.tr("Точек оценено: %d из %d")
                          % (nvalid, len(val_idx)))
        feedback.pushInfo(self.tr("ME (смещение):   %+.4g") % me)
        feedback.pushInfo("MAE:             %.4g" % mae)
        feedback.pushInfo(self.tr("RMSE:            %.4g") % rmse)
        if var is not None:
            feedback.pushInfo(self.tr("MSDR:            %.3f") % msdr)
        feedback.pushInfo(self.tr("R (оценка/факт): %.3f") % r)
        advice = _cv_advice(me, mae, rmse, msdr, r)
        for a in advice:
            feedback.pushInfo("• " + a)

        # --- слой ошибок ---
        valname = _san(zfield) or "z"
        idname = (_san(idfield) or "well_id") if idfield else None
        fields = QgsFields()
        aliases = {}
        if idname:
            fields.append(QgsField(idname, QVariant.String))
            aliases[idname] = self.tr("Номер скважины")
        fields.append(QgsField(valname, QVariant.Double))
        aliases[valname] = self.tr("Факт (%s)") % zfield
        fields.append(QgsField("z_est", QVariant.Double))
        aliases["z_est"] = self.tr("Оценка метода (LOO)")
        fields.append(QgsField("error", QVariant.Double))
        aliases["error"] = self.tr("Ошибка (оценка − факт)")
        fields.append(QgsField("abs_error", QVariant.Double))
        aliases["abs_error"] = self.tr("|Ошибка|")
        if var is not None:
            fields.append(QgsField("std_resid", QVariant.Double))
            aliases["std_resid"] = self.tr("Станд. остаток (со знаком)")
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.Point, crs)
        if sink is not None:
            for k, i in enumerate(val_idx):
                if not ok[k]:
                    continue
                f = QgsFeature(fields)
                f.setGeometry(QgsGeometry.fromPointXY(
                    QgsPointXY(float(xs[i]), float(ys[i]))))
                e = float(est[k] - fact[k])
                attrs = []
                if idname:
                    attrs.append(None if ids[i] is None else str(ids[i]))
                attrs += [float(fact[k]), float(est[k]), e, abs(e)]
                if var is not None:
                    s = float(np.sqrt(max(var[k], 0.0)))
                    attrs.append((e / s) if s > 0 else None)
                f.setAttributes(attrs)
                sink.addFeature(f)
        mlabel = (self.tr("кригинг") if method == "kriging"
                  else self.tr("мин. кривизна"))
        _set_output_name(context, dest,
                         self.tr("Ошибки CV (%s) %s") % (mlabel, zfield))
        _set_field_aliases(context, dest, aliases)

        results = {self.OUTPUT: dest}
        html_path = self.parameterAsFileOutput(
            parameters, self.OUTPUT_HTML, context)
        if html_path:
            metrics = [
                (self.tr("Метод"), mlabel, ""),
                (self.tr("ME (смещение)"), "%+.4g" % me,
                 self.tr("ближе к 0 - лучше")),
                ("MAE", "%.4g" % mae, self.tr("средняя |ошибка|")),
                ("RMSE", "%.4g" % rmse, self.tr("меньше - лучше")),
            ]
            if var is not None:
                metrics.append(("MSDR", "%.3f" % msdr,
                                self.tr("ближе к 1 - лучше")))
            metrics += [
                (self.tr("R (оценка/факт)"), "%.3f" % r,
                 self.tr("корреляция")),
                (self.tr("Точек оценено"), "%d" % nvalid,
                 self.tr("из %d") % len(val_idx)),
            ]
            title = self.tr("Кросс-валидация метода: %s · %s") % (
                mlabel, zfield)
            ids_ok = ([ids[i] for k, i in enumerate(val_idx) if ok[k]]
                      if (idfield and ids is not None) else None)
            try:
                _write_cv_report(html_path, title, metrics, advice,
                                 fact[ok], est[ok], err, ids_ok,
                                 used_params, feedback)
                results[self.OUTPUT_HTML] = html_path
            except Exception as e:
                feedback.pushWarning(self.tr(
                    "Не удалось записать HTML-отчёт: %s") % e)
        _save_values(self, _saved)
        return results


class FractalDimensionAlgorithm(IsolinerAlgorithm):
    """Карта фрактальной размерности поверхности вариограммным методом:
    локальный наклон лог-лог вариограммы в скользящем окне даёт H,
    D = 3 - H. Гладкие участки - около 2, изрезанные - к 3."""

    RASTER, BAND = "RASTER", "BAND"
    WINDOW, MAX_LAG = "WINDOW", "MAX_LAG"
    WITH_H = "WITH_H"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return FractalDimensionAlgorithm()
    def name(self): return "fractal_dimension"
    def displayName(self): return self.tr("6.01 Фрактальная размерность")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP5)
    def groupId(self): return GROUP5_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Считает карту фрактальной размерности поверхности "
            "вариограммным методом: в скользящем окне строится лог-лог "
            "вариограмма по лагам 1..N ячеек, её наклон даёт показатель "
            "Хёрста H, размерность D = 3 - H. Гладкие дифференцируемые "
            "участки дают D около 2, изрезанные и шумные - ближе к 3; "
            "перепады D подчёркивают зоны тектонических нарушений, границы "
            "блоков и смену характера рельефа кровли.\n\nВыход - грид D, "
            "готовый для «1.2 Изолинии из растра» (галка в дополнительных "
            "добавит H вторым каналом); глобальные D и "
            "H по всей поверхности печатаются в журнал. Малое окно (5-8 "
            "ячеек) показывает микроструктуру, большое (12-20) - "
            "региональные зоны. Растр должен быть в метрической системе "
            "координат.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.RASTER, self.tr("Поверхность (растр)")))
        self.addParameter(QgsProcessingParameterNumber(
            self.WINDOW, self.tr("Полурадиус окна, ячеек"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.WINDOW, 8), minValue=2))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_LAG, self.tr("Количество лагов вариограммы"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.MAX_LAG, 4), minValue=2,
            maxValue=12)))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BAND, self.tr("Канал высот"),
            defaultValue=_dv(self, self.BAND, 1),
            parentLayerParameterName=self.RASTER)))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.WITH_H, self.tr("Записать H вторым каналом"),
            defaultValue=bool(_dv(self, self.WITH_H, False)))))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Фрактальная размерность (D)")))

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        lyr = self.parameterAsRasterLayer(parameters, self.RASTER, context)
        band = self.parameterAsInt(parameters, self.BAND, context)
        window = self.parameterAsInt(parameters, self.WINDOW, context)
        max_lag = self.parameterAsInt(parameters, self.MAX_LAG, context)
        out = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)

        ds = gdal.Open(lyr.source())
        if ds is None or band > ds.RasterCount:
            raise QgsProcessingException(self.tr("Гриды не открылись."))
        b = ds.GetRasterBand(band)
        arr = b.ReadAsArray().astype(float)
        nd = b.GetNoDataValue()
        if nd is not None:
            arr = np.where(arr == nd, np.nan, arr)
        gt = ds.GetGeoTransform()
        ny, nx = arr.shape
        ds = None

        if max_lag >= min(ny, nx) // 4 or 2 * window >= min(ny, nx):
            raise QgsProcessingException(
                self.tr("Окно или лаги велики для этого грида."))
        feedback.setProgress(5)
        D, H = fractal_dimension_map(arr, window=window, max_lag=max_lag)
        feedback.setProgress(80)
        Dg, Hg = fractal_dimension_global(arr, max_lag=max_lag)
        feedback.pushInfo(
            _tr("Глобально: D = %.3f, H = %.3f.") % (Dg, Hg))
        with_h = self.parameterAsBool(parameters, self.WITH_H, context)
        nodv = -9999.0
        stack = [np.where(np.isfinite(D), D, nodv).astype(np.float32)]
        bnames = ["D"]
        if with_h:
            stack.append(np.where(np.isfinite(H), H, nodv)
                         .astype(np.float32))
            bnames.append("H")
        crs_wkt = lyr.crs().toWkt() if lyr.crs().isValid() else ""
        _write_grid_tiff(out, stack, gt, crs_wkt, nodv, nx, ny,
                         band_names=bnames)
        feedback.pushInfo(_tr(
            "Изолинии по карте D: «1.2 Изолинии из растра», канал 1."))
        _save_values(self, _saved)
        return {self.OUTPUT: out}


class BoxCountingAlgorithm(IsolinerAlgorithm):
    """Box-counting: одна размерность D на бинарную маску растра."""

    RASTER, BAND, THRESHOLD = "RASTER", "BAND", "THRESHOLD"
    OUT_D = "OUT_D"

    def tr(self, s): return _tr(s)
    def createInstance(self): return BoxCountingAlgorithm()
    def name(self): return "box_counting"
    def displayName(self): return self.tr("6.02 Box-counting маски")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP5)
    def groupId(self): return GROUP5_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Классический box-counting: растр бинаризуется порогом "
            "(объект - значения больше порога), маска покрывается ячейками "
            "убывающего размера, наклон log N от log(1/размер) даёт одну "
            "размерность D на всю маску. Линейный объект даёт D около 1, "
            "пятно - около 2, изрезанные контуры замещения или выработок - "
            "между. Точность метода на конечных масках порядка ±0.1 - "
            "используйте его для сравнения масок между собой, а не как "
            "абсолютную меру.\n\nРезультат печатается в журнал вместе с "
            "таблицей размеров и счётов и возвращается числом D.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.RASTER, self.tr("Растр маски")))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD, self.tr("Порог (объект: значение > порога)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.THRESHOLD, 0.5)))
        self.addParameter(_advanced(QgsProcessingParameterBand(
            self.BAND, self.tr("Канал"),
            defaultValue=_dv(self, self.BAND, 1),
            parentLayerParameterName=self.RASTER)))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_D, self.tr("Размерность D")))

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        lyr = self.parameterAsRasterLayer(parameters, self.RASTER, context)
        thr = self.parameterAsDouble(parameters, self.THRESHOLD, context)
        band = self.parameterAsInt(parameters, self.BAND, context)
        ds = gdal.Open(lyr.source())
        if ds is None or band > ds.RasterCount:
            raise QgsProcessingException(self.tr("Гриды не открылись."))
        b = ds.GetRasterBand(band)
        arr = b.ReadAsArray().astype(float)
        nd = b.GetNoDataValue()
        if nd is not None:
            arr = np.where(arr == nd, np.nan, arr)
        ds = None
        mask = np.isfinite(arr) & (arr > thr)
        npx = int(mask.sum())
        if npx == 0:
            raise QgsProcessingException(
                self.tr("Маска пуста: нет значений выше порога."))
        D, sizes, counts = box_count_dimension(mask)
        feedback.pushInfo(_tr("Пикселей в маске: %d.") % npx)
        for s, c in zip(sizes, counts):
            feedback.pushInfo("  %4d px -> N = %d" % (s, c))
        feedback.pushInfo(_tr("Box-counting: D = %.3f.") % D)
        _save_values(self, _saved)
        return {self.OUT_D: float(D)}


class LineDimensionAlgorithm(IsolinerAlgorithm):
    """Размерность линий методом циркуля: D каждой линии атрибутом."""

    LINES = "LINES"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return LineDimensionAlgorithm()
    def name(self): return "line_dimension"
    def displayName(self):
        return self.tr("6.03 Размерность линий и границ")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP5)
    def groupId(self): return GROUP5_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Считает размерность каждой линии методом циркуля (Ричардсона):"
            " линия проходится хордами убывающего раствора, наклон log N "
            "от log r даёт D. Прямая даёт 1, изрезанная линия - больше; "
            "для изолиний это диагностика сглаживания: пересглаженные "
            "изолинии теряют изрезанность и D падает к единице, а сравнение"
            " D до и после сглаживания показывает, сколько геометрии "
            "съедено.\n\nВыход - те же линии с полями D и steps (количество "
            "шагов минимального циркуля); среднее D печатается в журнал. "
            "Полигоны принимаются тоже: меряется внешнее кольцо границы. "
            "Короткие линии (меньше 30 вершин или очень малой длины) "
            "получают пустое D.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LINES, self.tr("Линии или полигоны"),
            [QgsProcessing.SourceType.TypeVectorLine, QgsProcessing.SourceType.TypeVectorPolygon]))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Объекты с размерностью"),
            QgsProcessing.SourceType.TypeVectorAnyGeometry))

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.LINES, context)
        fields = QgsFields(src.fields())
        fields.append(QgsField("D", QVariant.Double))
        fields.append(QgsField("steps", QVariant.Int))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            src.wkbType(), src.sourceCrs())
        total = src.featureCount() or 1
        dsum, dcnt = 0.0, 0
        for k, ft in enumerate(src.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(100.0 * k / total)
            g = ft.geometry()
            pts = []
            if g is not None and not g.isEmpty():
                parts = []
                try:
                    parts = g.asMultiPolyline()
                except Exception:
                    parts = []
                if not parts:
                    try:
                        pl = g.asPolyline()
                        parts = [pl] if pl else []
                    except Exception:
                        parts = []
                if not parts:  # полигоны: внешние кольца
                    try:
                        mp = g.asMultiPolygon()
                    except Exception:
                        mp = []
                    if not mp:
                        try:
                            p1 = g.asPolygon()
                            mp = [p1] if p1 else []
                        except Exception:
                            mp = []
                    parts = [poly[0] for poly in mp if poly]
                best = max(parts, key=len) if parts else []
                pts = [(p.x(), p.y()) for p in best]
            D = float("nan"); steps = 0
            if len(pts) >= 3:
                D, _r, ss = divider_dimension(np.array(pts))
                steps = int(ss[-1]) if ss else 0
            f = QgsFeature(fields)
            f.setGeometry(g)
            f.setAttributes(list(ft.attributes()) +
                            [None if D != D else round(D, 4), steps])
            sink.addFeature(f)
            if D == D:
                dsum += D; dcnt += 1
        if dcnt:
            feedback.pushInfo(
                _tr("Среднее D по %d линиям: %.3f.") % (dcnt, dsum / dcnt))
        _set_output_name(context, dest, self.tr("Линии с размерностью"))
        _save_values(self, _saved)
        return {self.OUTPUT: dest}


class MinkowskiDimensionAlgorithm(IsolinerAlgorithm):
    """Размерность Минковского векторных объектов: box-counting сеткой
    убывающего размера прямо по линиям и границам полигонов, без
    растеризации. D каждого объекта атрибутом плюс D слоя целиком."""

    FEATURES = "FEATURES"
    N_SIZES, OFFSETS, DENSIFY = "N_SIZES", "OFFSETS", "DENSIFY"
    OUTPUT = "OUTPUT"
    OUT_D, OUT_R2 = "OUT_D", "OUT_R2"

    def tr(self, s): return _tr(s)
    def createInstance(self): return MinkowskiDimensionAlgorithm()
    def name(self): return "minkowski_dimension"
    def displayName(self):
        return self.tr("6.04 Размерность Минковского (векторы)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP5)
    def groupId(self): return GROUP5_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Box-counting напрямую по векторам: линии и границы полигонов "
            "покрываются сеткой убывающего размера, наклон log N от "
            "log(1/размер) даёт размерность Минковского. Прямая линия и "
            "гладкая граница дают D около 1, речная сеть - 1.1-1.5, "
            "сильно изрезанная береговая линия - до 1.3 и выше.\n\n"
            "Каждый объект получает поле D_mink; отдельно считается и "
            "печатается в журнал D всего слоя как единого множества - для "
            "речной сети это размерность сети целиком, она выше "
            "размерности отдельных рукавов. Метод дополняет циркуль из "
            "2.9: циркуль меряет извилистость одной линии, Минковский - "
            "заполнение плоскости набором объектов.\n\nПараметры: K - "
            "ступеней лесенки размеров (8-12 обычно; слишком большое K "
            "уводит мелкие ячейки ниже масштаба детальности линии, и D "
            "занижается к 1); сдвигов сетки - случайные смещения с "
            "минимальным покрытием, снимают привязку к сетке (3-5); фактор "
            "уплотнения - шаг выборки вдоль сегментов в долях ячейки, 0 - "
            "только вершины. Каждый объект получает и D_r2 - качество "
            "лог-лог аппроксимации: ниже 0.85 оценке доверять нельзя.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.FEATURES, self.tr("Линии или полигоны"),
            [QgsProcessing.SourceType.TypeVectorLine, QgsProcessing.SourceType.TypeVectorPolygon]))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.N_SIZES, self.tr("Количество размеров сетки (K)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.N_SIZES, 8), minValue=4,
            maxValue=12)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.OFFSETS, self.tr("Сдвигов сетки на размер"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.OFFSETS, 3), minValue=1,
            maxValue=5)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.DENSIFY, self.tr("Фактор уплотнения выборки (0 - вершины)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.DENSIFY, 0.5), minValue=0.0,
            maxValue=2.0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Объекты с размерностью"),
            QgsProcessing.SourceType.TypeVectorAnyGeometry))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_D, self.tr("Размерность слоя")))
        self.addOutput(QgsProcessingOutputNumber(
            self.OUT_R2, self.tr("R² аппроксимации слоя")))

    @staticmethod
    def _geom_polylines(g):
        parts = []
        try:
            parts = g.asMultiPolyline()
        except Exception:
            parts = []
        if not parts:
            try:
                pl = g.asPolyline()
                parts = [pl] if pl else []
            except Exception:
                parts = []
        if not parts:
            try:
                mp = g.asMultiPolygon()
            except Exception:
                mp = []
            if not mp:
                try:
                    p1 = g.asPolygon()
                    mp = [p1] if p1 else []
                except Exception:
                    mp = []
            for poly in mp:
                parts.extend(poly)          # внешние кольца и дырки
        return [np.array([(p.x(), p.y()) for p in part], dtype=float)
                for part in parts if len(part) >= 2]

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        src = self.parameterAsSource(parameters, self.FEATURES, context)
        n_sizes = self.parameterAsInt(parameters, self.N_SIZES, context)
        offsets = self.parameterAsInt(parameters, self.OFFSETS, context)
        densify = self.parameterAsDouble(parameters, self.DENSIFY, context)
        fields = QgsFields(src.fields())
        fields.append(QgsField("D_mink", QVariant.Double))
        fields.append(QgsField("D_r2", QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            src.wkbType(), src.sourceCrs())
        total = src.featureCount() or 1
        all_parts = []
        for k, ft in enumerate(src.getFeatures()):
            if feedback.isCanceled():
                break
            feedback.setProgress(90.0 * k / total)
            g = ft.geometry()
            parts = self._geom_polylines(g) if g is not None else []
            all_parts.extend(parts)
            D = r2 = float("nan")
            if parts:
                D, r2, _s, _c = minkowski_dimension(
                    parts, n_sizes=n_sizes, offsets=offsets,
                    densify=densify)
            f = QgsFeature(fields)
            f.setGeometry(g)
            f.setAttributes(list(ft.attributes()) +
                            [None if D != D else round(D, 4),
                             None if r2 != r2 else round(r2, 4)])
            sink.addFeature(f)
        Dlayer = R2layer = float("nan")
        if all_parts:
            Dlayer, R2layer, sizes, counts = minkowski_dimension(
                all_parts, n_sizes=n_sizes, offsets=offsets,
                densify=densify)
            for s, c in zip(sizes, counts):
                feedback.pushInfo("  %.6g -> N = %d" % (s, c))
        feedback.pushInfo(
            _tr("Размерность Минковского слоя: D = %.3f (R² = %.3f).")
            % (Dlayer, R2layer))
        if R2layer == R2layer and R2layer < 0.85:
            feedback.pushWarning(_tr(
                "R² ниже 0.85: степенной закон не выдержан, оценке "
                "доверять нельзя (уменьшите K или проверьте данные)."))
        _set_output_name(context, dest, self.tr("Объекты с размерностью"))
        _save_values(self, _saved)
        return {self.OUTPUT: dest, self.OUT_D: float(Dlayer),
                self.OUT_R2: float(R2layer)}


class FractalDemoAlgorithm(IsolinerAlgorithm):
    """Демо для фрактальных инструментов: речная сеть с притоками,
    полигон водосбора и изрезанная береговая линия."""

    EXTENT = "EXTENT"
    SEED = "SEED"
    OUT_RIVERS, OUT_BASIN, OUT_COAST = "OUT_RIVERS", "OUT_BASIN", "OUT_COAST"

    def tr(self, s): return _tr(s)
    def createInstance(self): return FractalDemoAlgorithm()
    def name(self): return "fractal_demo"
    def displayName(self):
        return self.tr("6.05 Создать пример для фракталов (демо)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP5)
    def groupId(self): return GROUP5_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Генерирует учебные объекты для фрактальных инструментов: "
            "ветвящуюся речную сеть (поле order - порядок притока), полигон "
            "водосбора с изрезанной границей и отдельную береговую линию "
            "(срединные смещения). Реки подавайте в 6.04 - размерность "
            "сети; берег и границу водосбора - в 6.03 и 6.04; растеризуйте "
            "водосбор - и он же пример для 6.02.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Охват")))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно генератора"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.SEED, 1), minValue=0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_RIVERS, self.tr("Реки (демо)"),
            QgsProcessing.SourceType.TypeVectorLine))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_BASIN, self.tr("Водосбор (демо)"),
            QgsProcessing.SourceType.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_COAST, self.tr("Берег (демо)"),
            QgsProcessing.SourceType.TypeVectorLine))

    @staticmethod
    def _midpoint(p0, p1, rough, depth, rng):
        """Фрактальная ломаная срединных смещений между двумя точками."""
        pts = [np.array(p0, float), np.array(p1, float)]
        for _ in range(depth):
            out = [pts[0]]
            for a, b in zip(pts[:-1], pts[1:]):
                m = (a + b) / 2.0
                d = b - a
                n = np.array([-d[1], d[0]])
                L = float(np.hypot(*d))
                m = m + n / (L + 1e-12) * rng.normal(0, rough * L)
                out += [m, b]
            pts = out
        return np.array(pts)

    def _process(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        ext = self.parameterAsExtent(parameters, self.EXTENT, context)
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        seed = self.parameterAsInt(parameters, self.SEED, context)
        rng = np.random.default_rng(seed)
        W = ext.width(); H = ext.height()
        x0 = ext.xMinimum(); y0 = ext.yMinimum()

        f_riv = QgsFields()
        f_riv.append(QgsField("order", QVariant.Int))
        sink_r, dest_r = self.parameterAsSink(
            parameters, self.OUT_RIVERS, context, f_riv,
            QgsWkbTypes.Type.LineString, crs)
        f_nm = QgsFields()
        f_nm.append(QgsField("name", QVariant.String))
        sink_b, dest_b = self.parameterAsSink(
            parameters, self.OUT_BASIN, context, f_nm,
            QgsWkbTypes.Type.Polygon, crs)
        sink_c, dest_c = self.parameterAsSink(
            parameters, self.OUT_COAST, context, f_nm,
            QgsWkbTypes.Type.LineString, crs)

        # --- речная сеть: рекурсивное ветвление со срединной шершавостью
        rivers = []

        def branch(p, ang, L, order, depth):
            if depth <= 0 or L < 0.02 * min(W, H):
                return
            q = p + L * np.array([np.cos(ang), np.sin(ang)])
            pl = self._midpoint(p, q, 0.10, 3, rng)
            rivers.append((order, pl))
            k = rng.integers(2, 4)
            for i in range(k):
                tt = 0.3 + 0.6 * rng.random()
                bp = pl[int(tt * (len(pl) - 1))]
                da = np.deg2rad(25 + 25 * rng.random()) *                     (1 if i % 2 == 0 else -1)
                branch(bp, ang + da, L * (0.45 + 0.15 * rng.random()),
                       order + 1, depth - 1)

        start = np.array([x0 + 0.5 * W, y0 + 0.06 * H])
        branch(start, np.pi / 2, 0.8 * H, 1, 4)
        for order, pl in rivers:
            f = QgsFeature(f_riv)
            f.setGeometry(QgsGeometry.fromPolylineXY(
                [QgsPointXY(px, py) for px, py in pl]))
            f.setAttributes([int(order)])
            sink_r.addFeature(f)

        # --- водосбор: изрезанный овал вокруг сети
        cx, cy = x0 + 0.5 * W, y0 + 0.5 * H
        nang = 24
        base = []
        for i in range(nang):
            a = 2 * np.pi * i / nang
            r = (0.42 + 0.06 * rng.random())
            base.append((cx + r * W * np.cos(a), cy + r * H * np.sin(a)))
        ring = []
        for a, b in zip(base, base[1:] + base[:1]):
            seg = self._midpoint(a, b, 0.16, 3, rng)
            ring.extend(seg[:-1])
        ring.append(ring[0])
        f = QgsFeature(f_nm)
        f.setGeometry(QgsGeometry.fromPolygonXY(
            [[QgsPointXY(px, py) for px, py in ring]]))
        f.setAttributes([self.tr("Водосбор (демо)")])
        sink_b.addFeature(f)

        # --- берег: одна сильно изрезанная линия через охват
        coast = self._midpoint((x0 + 0.05 * W, y0 + 0.85 * H),
                               (x0 + 0.95 * W, y0 + 0.7 * H), 0.22, 8, rng)
        f = QgsFeature(f_nm)
        f.setGeometry(QgsGeometry.fromPolylineXY(
            [QgsPointXY(px, py) for px, py in coast]))
        f.setAttributes([self.tr("Берег (демо)")])
        sink_c.addFeature(f)

        for dest, nm in ((dest_r, self.tr("Реки (демо)")),
                         (dest_b, self.tr("Водосбор (демо)")),
                         (dest_c, self.tr("Берег (демо)"))):
            _set_output_name(context, dest, nm)
        feedback.pushInfo(_tr("Рек сгенерировано: %d.") % len(rivers))
        _save_values(self, _saved)
        return {self.OUT_RIVERS: dest_r, self.OUT_BASIN: dest_b,
                self.OUT_COAST: dest_c}


class PolyhedralDemoAlgorithm(IsolinerAlgorithm):
    """Демо полиэдральной поверхности: тело пласта, куб или тетраэдр как
    нативный PolyhedralSurface Z (QGIS 3.40+) или TIN Z. На сборках до 3.40
    вывод деградирует до MultiPolygon Z с предупреждением. Задача - показать
    сам тип геометрии и проверить, что сборка QGIS и плагин QSFCGAL с ним
    работают."""

    EXAMPLE = "EXAMPLE"
    EXTENT = "EXTENT"
    NX = "NX"
    THICKNESS = "THICKNESS"
    BASE = "BASE"
    N_BEDS = "N_BEDS"
    AS_TIN = "AS_TIN"
    OUTPUT = "OUTPUT"

    _KINDS = ("bed", "suite", "cube", "tetra")

    def tr(self, s): return _tr(s)
    def createInstance(self): return PolyhedralDemoAlgorithm()
    def name(self): return "polyhedral_demo"
    def displayName(self):
        return self.tr("5.07 Создать пример полиэдра (бета)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP4)
    def groupId(self): return GROUP4_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Создаёт демонстрационную полиэдральную поверхность, чтобы "
            "посмотреть сам тип геометрии в 3D и проверить его на своей "
            "сборке QGIS. Варианты примера: тело пласта, свита (стопка "
            "складчатых пластов, каждый пласт грузится отдельным слоем для "
            "управления видимостью и красится своим цветом), куб и "
            "тетраэдр. Тело пласта - водонепроницаемая "
            "оболочка из кровли, подошвы и боковой юбки, тот же приём, что "
            "и в будущем экспорте тела пласта. Плановое "
            "положение и размер берутся из охвата (окна вида), по вертикали "
            "тело занимает от отметки залегания до отметки плюс мощность. "
            "Тип геометрии плоский, поэтому в 2D-виде Z не виден - диапазон "
            "Z печатается в журнал, а само тело удобно смотреть в окне "
            "Модули - Isoliner - 3D-просмотр поверхностей, вкладка Тела. "
            "Нативный PolyhedralSurface Z доступен с QGIS 3.40, там же "
            "работает плагин QSFCGAL (резка и булевы операции над телами). "
            "На более старых сборках вывод деградирует до MultiPolygon Z. "
            "Флаг TIN выдаёт триангулированную поверхность (тип TIN Z).")
            + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterEnum(
            self.EXAMPLE, self.tr("Пример"),
            options=[self.tr("Тело пласта"),
                     self.tr("Свита (стопка складчатых пластов)"),
                     self.tr("Куб"), self.tr("Тетраэдр")],
            defaultValue=_dv(self, self.EXAMPLE, 0)))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Охват (окно вида) - размещение и размер"),
            optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.THICKNESS, self.tr("Мощность, ед. карты"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.THICKNESS, 25.0), minValue=0.001))
        self.addParameter(QgsProcessingParameterNumber(
            self.NX, self.tr("Разбиение тела пласта (ячеек по стороне)"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NX, 8), minValue=2, maxValue=80))
        self.addParameter(QgsProcessingParameterBoolean(
            self.AS_TIN, self.tr("Выдать как TIN (триангулировать)"),
            defaultValue=_dv(self, self.AS_TIN, False)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BASE, self.tr("Отметка залегания (подошва), ед. карты"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.BASE, 0.0))))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.N_BEDS, self.tr("Пластов в свите"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.N_BEDS, 3), minValue=2, maxValue=8)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Полиэдр (демо)"),
            QgsProcessing.SourceType.TypeVectorPolygon, optional=True))

    @staticmethod
    def _resolve_wkb(*names):
        """Первый доступный enum QgsWkbTypes из перечисленных имён."""
        for nm in names:
            t = getattr(QgsWkbTypes, nm, None)
            if t is not None:
                return t, nm
        return None, None

    def _load_suite_layers(self, beds, crs, context, feedback):
        """Разносит пласты свиты по отдельным слоям в проекте (управление
        видимостью). Возвращает True при успехе. При любой проблеме с API
        загрузки возвращает False - вызывающий пишет свиту одним слоем."""
        try:
            from . import polyhedral as poly
            from qgis.core import QgsVectorLayer, QgsProcessingContext
            authid = crs.authid() if crs is not None else ""
            uri = "MultiPolygonZ" + (("?crs=%s" % authid) if authid else "")
            store = context.temporaryLayerStore()
            proj = QgsProject.instance()
            for k, bp in enumerate(beds, 1):
                g = QgsGeometry.fromWkt(poly.patches_to_wkt(bp, "MULTIPOLYGON"))
                if g is None or g.isNull():
                    return False
                nm = self.tr("Свита: пласт %d") % k
                lyr = QgsVectorLayer(uri, nm, "memory")
                if not lyr.isValid():
                    return False
                pr = lyr.dataProvider()
                pr.addAttributes([QgsField("bed", QVariant.Int),
                                  QgsField("watertight", QVariant.Int)])
                lyr.updateFields()
                _ne, n_open = poly.edge_audit(bp)
                ft = QgsFeature(lyr.fields())
                ft.setGeometry(g)
                ft.setAttributes([k, 1 if n_open == 0 else 0])
                pr.addFeature(ft)
                lyr.updateExtents()
                store.addMapLayer(lyr)
                details = QgsProcessingContext.LayerDetails(nm, proj, nm)
                context.addLayerToLoadOnCompletion(lyr.id(), details)
            feedback.pushInfo(self.tr(
                "Свита загружена отдельными слоями по пласту: %d.") % len(beds))
            return True
        except Exception as e:
            feedback.pushInfo(self.tr(
                "Не удалось разнести свиту по слоям (%s) - вывод одним "
                "слоем.") % e)
            return False

    def _process(self, parameters, context, feedback):
        from . import polyhedral as poly
        feedback.pushInfo(_version_line())
        _saved = dict(parameters)
        idx = self.parameterAsEnum(parameters, self.EXAMPLE, context)
        kind = self._KINDS[idx]
        nx = self.parameterAsInt(parameters, self.NX, context)
        thickness = self.parameterAsDouble(parameters, self.THICKNESS, context)
        base = self.parameterAsDouble(parameters, self.BASE, context)
        n_beds = self.parameterAsInt(parameters, self.N_BEDS, context)
        as_tin = self.parameterAsBool(parameters, self.AS_TIN, context)

        # footprint берётся из охвата (окна вида); пустой охват - дефолт
        crs = QgsProject.instance().crs()
        ext = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if ext is None or ext.isEmpty() or ext.width() <= 0 \
                or ext.height() <= 0:
            cx = cy = 0.0
            size = 200.0
        else:
            cx, cy = ext.center().x(), ext.center().y()
            size = min(ext.width(), ext.height())

        # общий вертикальный размах: подошва - base, кровля - base + мощность
        cz = base + thickness / 2.0
        x0, y0 = cx - size / 2.0, cy - size / 2.0

        # свита: пробуем разнести пласты по отдельным слоям (управление
        # видимостью); при неудаче API загрузки - свита одним слоем ниже
        if kind == "suite":
            beds = poly.suite_beds(n=n_beds, nx=nx, ny=nx, size=size,
                                   x0=x0, y0=y0, base=base, thickness=thickness)
            if as_tin:
                beds = [poly._triangulate(bp) for bp in beds]
            if self._load_suite_layers(beds, crs, context, feedback):
                allp = [p for bp in beds for p in bp]
                zmin, zmax = poly.z_range(allp)
                feedback.pushInfo(self.tr(
                    "Диапазон Z: %.3f .. %.3f (ед. карты).") % (zmin, zmax))
                _save_values(self, _saved)
                return {}
            wkt_kind = "TIN" if as_tin else "POLYHEDRALSURFACE"
            objs = []
            for k, bp in enumerate(beds, 1):
                _ne, n_open = poly.edge_audit(bp)
                objs.append(dict(patches=bp, name="suite_bed_%d" % k, bed=k,
                                 np=len(bp), watertight=(n_open == 0),
                                 open=n_open))
        else:
            if kind == "bed":
                patches, wkt_kind, meta = poly.build_example(
                    "bed", as_tin=as_tin, nx=nx, ny=nx, size=size,
                    x0=x0, y0=y0, base=base + thickness, thickness=thickness)
            elif kind == "cube":
                patches, wkt_kind, meta = poly.build_example(
                    "cube", as_tin=as_tin, cx=cx, cy=cy, cz=cz,
                    sx=size, sy=size, sz=thickness)
            else:  # tetra
                patches, wkt_kind, meta = poly.build_example(
                    "tetra", as_tin=as_tin, cx=cx, cy=cy, cz=cz, size=size)
            objs = [dict(patches=patches, name=meta["name"], bed=1,
                         np=meta["patches"], watertight=meta["watertight"],
                         open=meta["open_edges"])]

        # желаемый нативный тип, решаем по первому объекту
        if wkt_kind == "TIN":
            native_wkb, _nm = self._resolve_wkb("TINZ", "TinZ", "TIN")
        else:
            native_wkb, _nm = self._resolve_wkb(
                "PolyhedralSurfaceZ", "PolyhedralSurface")
        mpoly_wkb, _ = self._resolve_wkb("MultiPolygonZ")

        use_native = False
        if native_wkb is not None:
            g0 = QgsGeometry.fromWkt(
                poly.patches_to_wkt(objs[0]["patches"], wkt_kind))
            use_native = g0 is not None and not g0.isNull()
        if use_native:
            used_kind, used_wkb = wkt_kind, native_wkb
        else:
            used_kind, used_wkb = "MULTIPOLYGON", mpoly_wkb
            feedback.pushWarning(self.tr(
                "Нативный тип {0} на этой сборке недоступен - вывод как "
                "MultiPolygon Z. Нативный PolyhedralSurface / TIN и QSFCGAL "
                "доступны с QGIS 3.40.").format(wkt_kind))

        for o in objs:
            g = QgsGeometry.fromWkt(
                poly.patches_to_wkt(o["patches"], used_kind))
            if g is None or g.isNull():
                raise QgsProcessingException(self.tr(
                    "Не удалось собрать геометрию из WKT."))
            o["geom"] = g

        fields = QgsFields()
        fields.append(QgsField("name", QVariant.String))
        fields.append(QgsField("kind", QVariant.String))
        fields.append(QgsField("patches", QVariant.Int))
        fields.append(QgsField("watertight", QVariant.Int))
        fields.append(QgsField("bed", QVariant.Int))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, used_wkb, crs)
        if sink is None:
            raise QgsProcessingException(self.tr(
                "Не задан выходной слой. Укажите «Полиэдр (демо)» "
                "(например, временный слой)."))

        for o in objs:
            f = QgsFeature(fields)
            f.setGeometry(o["geom"])
            f.setAttributes([o["name"], used_kind, int(o["np"]),
                             1 if o["watertight"] else 0, int(o["bed"])])
            sink.addFeature(f)

        titles = {
            "bed": self.tr("Пласт (демо)"),
            "suite": self.tr("Свита x%d (демо)") % n_beds,
            "cube": self.tr("Куб (демо)"),
            "tetra": self.tr("Тетраэдр (демо)"),
        }
        _set_output_name(context, dest, titles[kind])

        total_np = sum(o["np"] for o in objs)
        feedback.pushInfo(self.tr("Тип геометрии: %s Z.") % used_kind)
        if len(objs) > 1:
            feedback.pushInfo(self.tr("Объектов: %d, граней всего: %d.")
                              % (len(objs), total_np))
        else:
            feedback.pushInfo(self.tr("Граней: %d.") % total_np)
        allp = [p for o in objs for p in o["patches"]]
        zmin, zmax = poly.z_range(allp)
        feedback.pushInfo(self.tr("Диапазон Z: %.3f .. %.3f (ед. карты).")
                          % (zmin, zmax))
        n_open_total = sum(o["open"] for o in objs)
        if n_open_total == 0:
            feedback.pushInfo(self.tr("Оболочка замкнута (водонепроницаема)."))
        else:
            feedback.pushWarning(self.tr(
                "Оболочка НЕ замкнута: открытых рёбер %d.") % n_open_total)
        _save_values(self, _saved)
        return {self.OUTPUT: dest}


# --- 2. Топография -----------------------------------------------------


class DemDownloadAlgorithm(IsolinerAlgorithm):
    """2.01 Загрузка ЦМР по рамке: Copernicus GLO-30 из открытого
    хранилища через /vsicurl/, VRT-мозаика без швов, обязательный
    варп в метрическую СК, гидрокоррекция флажком."""

    EXTENT = "EXTENT"
    TARGET_CRS = "TARGET_CRS"
    CELL = "CELL"
    FILL = "FILL"
    EPSILON = "EPSILON"
    MAX_TILES = "MAX_TILES"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return DemDownloadAlgorithm()
    def name(self): return "dem_download"
    def displayName(self):
        return self.tr("2.01 Загрузка ЦМР по рамке")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Загружает Copernicus DEM GLO-30 по рамке из открытого "
            "хранилища, без регистрации и ключей. Плитки собираются в "
            "мозаику без швов и перепроецируются в метрическую систему "
            "координат с кубической интерполяцией. Сырые градусные "
            "плитки в анализ не попадают, поэтому укрупнённый шаг "
            "GLO-30 по долготе севернее 50 широты обрабатывается "
            "автоматически. Флажок гидрокоррекции заполняет ложные "
            "понижения, чтобы вода текла вниз. Выход готов для "
            "изолиний (1.04) и всей группы Топография. Выход: GeoTIFF "
            "float32, высоты в метрах, nodata -32768, слой попадает в "
            "группу Топография дерева слоёв. "
            "Данные: Copernicus DEM © ESA.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Рамка загрузки")))
        self.addParameter(QgsProcessingParameterCrs(
            self.TARGET_CRS,
            self.tr("Целевая СК (пусто: СК проекта или UTM по центру)"),
            optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL, self.tr("Размер ячейки, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL, dem_glo30.DEFAULT_CELL),
            minValue=1.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL,
            self.tr("Гидрологическая коррекция (заполнение понижений)"),
            defaultValue=_dv(self, self.FILL, True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.EPSILON, self.tr("Epsilon уклона при заполнении, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EPSILON, DEFAULT_EPSILON),
            minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_TILES, self.tr("Предел числа плиток 1x1 градус"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.MAX_TILES,
                             dem_glo30.DEFAULT_MAX_TILES), minValue=1)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("ЦМР (метрическая СК)")))

    def _resolve_target_srs(self, parameters, context, bbox):
        """Целевая СК: (подпись для журнала, epsg или None, wkt или None).

        Пользовательские СК без кода EPSG (локальные шахтные сетки)
        полноправны: уходят в варп строкой WKT."""
        crs = self.parameterAsCrs(parameters, self.TARGET_CRS, context)
        if crs is None or not crs.isValid():
            project = context.project()
            pcrs = project.crs() if project else None
            if pcrs is not None and pcrs.isValid() and not pcrs.isGeographic():
                crs = pcrs
            else:
                lon_c = (bbox[0] + bbox[2]) / 2.0
                lat_c = (bbox[1] + bbox[3]) / 2.0
                epsg = dem_glo30.utm_epsg_for(lon_c, lat_c)
                return "EPSG:%d (UTM)" % epsg, epsg, None
        if crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "Целевая СК должна быть метрической, градусные "
                "гриды в анализ не пускаем."))
        auth = crs.authid()
        if auth.startswith("EPSG:"):
            return auth, int(auth.split(":")[1]), None
        return auth or crs.description(), None, crs.toWkt()

    def _process(self, parameters, context, feedback):
        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        ext = self.parameterAsExtent(parameters, self.EXTENT, context,
                                     crs_4326)
        bbox = (ext.xMinimum(), ext.yMinimum(),
                ext.xMaximum(), ext.yMaximum())
        cell = self.parameterAsDouble(parameters, self.CELL, context)
        do_fill = self.parameterAsBoolean(parameters, self.FILL, context)
        epsilon = self.parameterAsDouble(parameters, self.EPSILON, context)
        max_tiles = self.parameterAsInt(parameters, self.MAX_TILES, context)
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT,
                                               context)
        srs_label, dst_epsg, dst_wkt = self._resolve_target_srs(
            parameters, context, bbox)
        feedback.pushInfo(self.tr("Целевая СК: %s") % srs_label)

        gdal.UseExceptions()
        gdal.SetConfigOption("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
        gdal.SetConfigOption("GDAL_HTTP_TIMEOUT", "60")
        gdal.SetConfigOption("GDAL_HTTP_MAX_RETRY", "3")
        gdal.SetConfigOption("GDAL_HTTP_RETRY_DELAY", "2")
        gdal.SetConfigOption("VSI_CACHE", "TRUE")
        try:
            dem_glo30.fetch_dem(
                bbox, out_path, gdal, osr, dst_epsg=dst_epsg,
                dst_wkt=dst_wkt, cell=cell, max_tiles=max_tiles,
                feedback=feedback)
        except dem_glo30.DemSourceError as exc:
            raise QgsProcessingException(str(exc))
        if feedback.isCanceled():
            return {}

        if do_fill:
            feedback.pushInfo(self.tr("Гидрологическая коррекция..."))
            ds = gdal.Open(out_path, gdal.GA_Update)
            band = ds.GetRasterBand(1)
            z = band.ReadAsArray().astype(np.float64)
            nodata = band.GetNoDataValue()
            mask = (z == nodata) if nodata is not None else None
            filled, n_raised, max_raise = fill_depressions(
                z, nodata_mask=mask, epsilon=epsilon, feedback=feedback)
            if mask is not None:
                filled[mask] = nodata
            band.WriteArray(filled.astype(np.float32))
            band.FlushCache()
            ds = None
            feedback.pushInfo(
                self.tr("Поднято ячеек: %d, максимальный подъём: %.2f м")
                % (n_raised, max_raise))
        feedback.pushInfo(self.tr("Данные: Copernicus DEM © ESA."))
        _topo_group_layer(context, out_path, self.tr("Топография"))
        return {self.OUTPUT: out_path}


class TopobaseDownloadAlgorithm(IsolinerAlgorithm):
    """2.02 Загрузка топоосновы по рамке: водотоки, водоёмы, вершины,
    обрывы и береговая линия из OpenStreetMap через Overpass."""

    EXTENT = "EXTENT"
    GET_WATERCOURSES = "GET_WATERCOURSES"
    GET_WATERBODIES = "GET_WATERBODIES"
    GET_PEAKS = "GET_PEAKS"
    GET_BREAKS = "GET_BREAKS"
    GET_COASTLINE = "GET_COASTLINE"
    MAX_AREA = "MAX_AREA"
    TIMEOUT = "TIMEOUT"
    OUT_WATERCOURSES = "OUT_WATERCOURSES"
    OUT_WATERBODIES = "OUT_WATERBODIES"
    OUT_PEAKS = "OUT_PEAKS"
    OUT_BREAKS = "OUT_BREAKS"
    OUT_COASTLINE = "OUT_COASTLINE"

    def tr(self, s): return _tr(s)
    def createInstance(self): return TopobaseDownloadAlgorithm()
    def name(self): return "topobase_download"
    def displayName(self):
        return self.tr("2.02 Загрузка топоосновы по рамке")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Загружает по рамке из OpenStreetMap слои для работы с "
            "рельефом: водотоки (в OSM рисуются вниз по течению, это "
            "готовые тальвеги), водоёмы как плоскости постоянной "
            "высоты, вершины с отметками ele, обрывы и насыпи как "
            "линии разрыва, береговую линию. Выход в СК проекта. "
            "Публичные серверы Overpass имеют лимиты, для больших "
            "территорий уменьшайте рамку. Водоёмы берутся из замкнутых "
            "контуров way, составные мультиполигоны пока пропускаются. "
            "Выход: до пяти слоёв в группе Топография. Общие поля name "
            "и osm_id, у водотоков дополнительно waterway, у водоёмов "
            "water, у вершин ele (высота, м), у обрывов kind. "
            "Данные: © участники OpenStreetMap, ODbL.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Рамка загрузки")))
        for key, title, dv in (
                (self.GET_WATERCOURSES, "Водотоки (тальвеги)", True),
                (self.GET_WATERBODIES, "Водоёмы", True),
                (self.GET_PEAKS, "Вершины с отметками", True),
                (self.GET_BREAKS, "Обрывы и насыпи", True),
                (self.GET_COASTLINE, "Береговая линия", False)):
            self.addParameter(QgsProcessingParameterBoolean(
                key, self.tr(title), defaultValue=_dv(self, key, dv)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MAX_AREA, self.tr("Предел площади рамки, кв. градусов"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.MAX_AREA, osm_overpass.MAX_BBOX_DEG2),
            minValue=0.001)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.TIMEOUT, self.tr("Таймаут запроса, с"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.TIMEOUT, osm_overpass.DEFAULT_TIMEOUT),
            minValue=10)))
        for key, title in (
                (self.OUT_WATERCOURSES, "Водотоки"),
                (self.OUT_WATERBODIES, "Водоёмы (полигоны)"),
                (self.OUT_PEAKS, "Вершины"),
                (self.OUT_BREAKS, "Обрывы и насыпи (линии)"),
                (self.OUT_COASTLINE, "Береговая линия (линии)")):
            self.addParameter(QgsProcessingParameterFeatureSink(
                key, self.tr(title), optional=True, createByDefault=True))

    @staticmethod
    def _make_fields(spec):
        fields = QgsFields()
        for fname, ftype in spec:
            fields.append(QgsField(fname, ftype))
        return fields

    def _process(self, parameters, context, feedback):
        crs_4326 = QgsCoordinateReferenceSystem("EPSG:4326")
        ext = self.parameterAsExtent(parameters, self.EXTENT, context,
                                     crs_4326)
        bbox = (ext.xMinimum(), ext.yMinimum(),
                ext.xMaximum(), ext.yMaximum())
        max_area = self.parameterAsDouble(parameters, self.MAX_AREA, context)
        timeout = self.parameterAsInt(parameters, self.TIMEOUT, context)

        osm = osm_overpass
        layers = set()
        for key, lname in ((self.GET_WATERCOURSES, osm.LAYER_WATERCOURSES),
                           (self.GET_WATERBODIES, osm.LAYER_WATERBODIES),
                           (self.GET_PEAKS, osm.LAYER_PEAKS),
                           (self.GET_BREAKS, osm.LAYER_BREAKS),
                           (self.GET_COASTLINE, osm.LAYER_COASTLINE)):
            if self.parameterAsBoolean(parameters, key, context):
                layers.add(lname)
        if not layers:
            raise QgsProcessingException(self.tr(
                "Не выбран ни один слой для загрузки."))
        try:
            osm.check_bbox(*bbox, max_deg2=max_area)
            query = osm.build_query(*bbox, layers=layers, timeout=timeout)
            data = osm.run_query(query, timeout=timeout, feedback=feedback)
        except osm.OsmSourceError as exc:
            raise QgsProcessingException(str(exc))
        parsed = osm.parse_elements(data)

        project = context.project()
        pcrs = project.crs() if project else crs_4326
        if not pcrs.isValid():
            pcrs = crs_4326
        transform = QgsCoordinateTransform(crs_4326, pcrs,
                                           context.transformContext())

        def to_project(coords):
            return [transform.transform(QgsPointXY(lon, lat))
                    for lon, lat in coords]

        common = [("name", QVariant.String), ("osm_id", QVariant.LongLong)]
        specs = (
            (self.OUT_WATERCOURSES, osm.LAYER_WATERCOURSES,
             QgsWkbTypes.Type.LineString,
             common + [("waterway", QVariant.String)], "line"),
            (self.OUT_WATERBODIES, osm.LAYER_WATERBODIES,
             QgsWkbTypes.Type.Polygon,
             common + [("water", QVariant.String)], "polygon"),
            (self.OUT_PEAKS, osm.LAYER_PEAKS, QgsWkbTypes.Type.Point,
             common + [("ele", QVariant.Double)], "point"),
            (self.OUT_BREAKS, osm.LAYER_BREAKS, QgsWkbTypes.Type.LineString,
             common + [("kind", QVariant.String)], "line"),
            (self.OUT_COASTLINE, osm.LAYER_COASTLINE,
             QgsWkbTypes.Type.LineString, list(common), "line"),
        )
        results = {}
        for key, lname, wkb, field_spec, geom_kind in specs:
            if lname not in layers:
                continue
            feats = parsed.get(lname, [])
            feedback.pushInfo(self.tr("Получено объектов (%s): %d")
                              % (lname, len(feats)))
            fields = self._make_fields(field_spec)
            sink, dest_id = self.parameterAsSink(
                parameters, key, context, fields, wkb, pcrs)
            if sink is None:
                continue
            for item in feats:
                if geom_kind == "line":
                    pieces = osm.clip_line_to_bbox(item["coords"], *bbox)
                else:
                    pieces = [item["coords"]]
                for piece in pieces:
                    feat = QgsFeature(fields)
                    pts = to_project(piece)
                    if geom_kind == "point":
                        geom = QgsGeometry.fromPointXY(pts[0])
                    elif geom_kind == "polygon":
                        geom = QgsGeometry.fromPolygonXY([pts])
                    else:
                        geom = QgsGeometry.fromPolylineXY(pts)
                    feat.setGeometry(geom)
                    for fname, _unused in field_spec:
                        val = item["attrs"].get(fname)
                        if val is not None:
                            feat[fname] = val
                    sink.addFeature(feat)
            results[key] = dest_id
        feedback.pushInfo(self.tr(
            "Данные: © участники OpenStreetMap, ODbL."))
        for dest in results.values():
            _topo_group_layer(context, dest, self.tr("Топография"))
        return results


class TopoFillDepressionsAlgorithm(IsolinerAlgorithm):
    """2.04 Заполнение понижений: Planchon-Darboux с epsilon."""

    INPUT = "INPUT"
    EPSILON = "EPSILON"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return TopoFillDepressionsAlgorithm()
    def name(self): return "fill_depressions"
    def displayName(self):
        return self.tr("2.04 Заполнение понижений")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Заполняет ложные понижения ЦМР методом Планшона-Дарбу, "
            "чтобы поток не останавливался в ямах. Epsilon задаёт "
            "минимальный уклон на плоских участках. При нулевом "
            "значении поднимаются только настоящие ямы ровно до уровня "
            "слива, при положительном дополнительно строится сквозной "
            "уклон через плоскости. Ячейки на границе грида и рядом с "
            "nodata считаются стоками. В отчёт выводится число "
            "поднятых ячеек и максимальный подъём. Выход: GeoTIFF "
            "float32 с сеткой и nodata входа, слой в группе "
            "Топография.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Входная ЦМР")))
        self.addParameter(QgsProcessingParameterNumber(
            self.EPSILON, self.tr("Epsilon уклона, м (0: только ямы)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EPSILON, DEFAULT_EPSILON),
            minValue=0.0))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("ЦМР без понижений")))

    def _process(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        epsilon = self.parameterAsDouble(parameters, self.EPSILON, context)
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT,
                                               context)
        gdal.UseExceptions()
        src = gdal.Open(layer.source())
        if src is None:
            raise QgsProcessingException(self.tr(
                "Не удалось открыть входной растр через GDAL."))
        band = src.GetRasterBand(1)
        z = band.ReadAsArray().astype(np.float64)
        nodata = band.GetNoDataValue()
        mask = (z == nodata) if nodata is not None else None
        filled, n_raised, max_raise = fill_depressions(
            z, nodata_mask=mask, epsilon=epsilon, feedback=feedback)
        if mask is not None:
            filled[mask] = nodata
        driver = gdal.GetDriverByName("GTiff")
        dst = driver.Create(out_path, src.RasterXSize, src.RasterYSize, 1,
                            gdal.GDT_Float32,
                            options=["COMPRESS=DEFLATE", "TILED=YES"])
        dst.SetGeoTransform(src.GetGeoTransform())
        dst.SetProjection(src.GetProjection())
        out_band = dst.GetRasterBand(1)
        if nodata is not None:
            out_band.SetNoDataValue(nodata)
        out_band.WriteArray(filled.astype(np.float32))
        out_band.FlushCache()
        dst = None
        src = None
        feedback.pushInfo(
            self.tr("Поднято ячеек: %d, максимальный подъём: %.2f м")
            % (n_raised, max_raise))
        _topo_group_layer(context, out_path, self.tr("Топография"))
        return {self.OUTPUT: out_path}


class TopoDemoReliefAlgorithm(IsolinerAlgorithm):
    """2.10 Демо-рельеф: детерминированный синтетический грид."""

    NX = "NX"
    NY = "NY"
    CELL = "CELL"
    SEED = "SEED"
    CRS = "CRS"
    INT16 = "INT16"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return TopoDemoReliefAlgorithm()
    def name(self): return "topo_demo_relief"
    def displayName(self):
        return self.tr("2.10 Демо-рельеф")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Создаёт синтетический рельеф: наклонная равнина, холмы, "
            "извилистая долина с постоянным падением. Рельеф "
            "детерминирован по зерну. Между холмами осознанно остаются "
            "локальные понижения, чтобы инструменту заполнения было "
            "что показывать. Служебный инструмент для примеров "
            "руководства и работы без сети, живые данные даёт "
            "инструмент 2.01. Выход: GeoTIFF float32 (или int16 "
            "флажком) в группе Топография.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterNumber(
            self.NX, self.tr("Ширина, ячеек"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NX, demo_relief.DEFAULT_NX),
            minValue=20))
        self.addParameter(QgsProcessingParameterNumber(
            self.NY, self.tr("Высота, ячеек"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.NY, demo_relief.DEFAULT_NY),
            minValue=20))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL, self.tr("Размер ячейки, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL, demo_relief.DEFAULT_CELL),
            minValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно генератора"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.SEED, demo_relief.DEFAULT_SEED),
            minValue=0))
        self.addParameter(QgsProcessingParameterCrs(
            self.CRS, self.tr("СК выхода (метрическая)"),
            defaultValue="EPSG:32640"))
        self.addParameter(_advanced(QgsProcessingParameterBoolean(
            self.INT16, self.tr("Компактный int16 (для поставки демо)"),
            defaultValue=False)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Демо-рельеф")))

    def _process(self, parameters, context, feedback):
        nx = self.parameterAsInt(parameters, self.NX, context)
        ny = self.parameterAsInt(parameters, self.NY, context)
        cell = self.parameterAsDouble(parameters, self.CELL, context)
        seed = self.parameterAsInt(parameters, self.SEED, context)
        crs = self.parameterAsCrs(parameters, self.CRS, context)
        as_int16 = self.parameterAsBoolean(parameters, self.INT16, context)
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT,
                                               context)
        if crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "Нужна метрическая СК."))
        auth = crs.authid()
        epsg = int(auth.split(":")[1]) if auth.startswith("EPSG:") else None
        wkt = None if epsg is not None else crs.toWkt()
        gdal.UseExceptions()
        try:
            z = demo_relief.generate(nx=nx, ny=ny, cell=cell, seed=seed)
        except ValueError as exc:
            raise QgsProcessingException(str(exc))
        demo_relief.write_geotiff(z, out_path, gdal, osr, cell=cell,
                                  epsg=epsg, wkt=wkt, as_int16=as_int16)
        feedback.pushInfo(self.tr("Готово: %dx%d ячеек, зерно %d.")
                          % (nx, ny, seed))
        _topo_group_layer(context, out_path, self.tr("Топография"))
        return {self.OUTPUT: out_path}


class _CollapseNodePostProcessor(QgsProcessingLayerPostProcessorInterface):
    """Сворачивает узел слоя в дереве после загрузки: длинные легенды
    растров не распахиваются на всю панель. Экземпляр обязан жить
    дольше вызова (QGIS хранит только указатель), поэтому один
    синглтон на модуль."""

    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def postProcessLayer(self, layer, context, feedback):
        try:
            project = context.project()
            if project is None or layer is None:
                return
            node = project.layerTreeRoot().findLayer(layer.id())
            if node is not None:
                node.setExpanded(False)
        except Exception:
            pass


def _topo_group_layer(context, layer_id, name):
    """Положить выходной слой в группу дерева слоёв (QGIS 3.24+)
    и свернуть его узел. На старых сборках без groupName слой
    добавляется как обычно, сворачивание работает везде."""
    if not layer_id:
        return
    try:
        if context.willLoadLayerOnCompletion(layer_id):
            details = context.layerToLoadOnCompletionDetails(layer_id)
            if hasattr(details, "groupName"):
                details.groupName = name
            details.setPostProcessor(_CollapseNodePostProcessor.instance())
    except Exception:
        pass


def _topo_read_dem(layer, tr):
    """Прочитать ЦМР слоя через GDAL: (z, mask, gt, proj, cell)."""
    gdal.UseExceptions()
    src = gdal.Open(layer.source())
    if src is None:
        raise QgsProcessingException(tr(
            "Не удалось открыть входной растр через GDAL."))
    band = src.GetRasterBand(1)
    z = band.ReadAsArray().astype(np.float64)
    nodata = band.GetNoDataValue()
    mask = (z == nodata) if nodata is not None else ~np.isfinite(z)
    gt = src.GetGeoTransform()
    proj = src.GetProjection()
    cell = abs(gt[1])
    if abs(abs(gt[5]) - cell) > 1e-6 * max(cell, 1.0):
        raise QgsProcessingException(tr(
            "Ячейка ЦМР не квадратная, переинтерполируйте грид."))
    src = None
    return z, mask, gt, proj, cell


def _topo_write_raster(path, arr, gt, proj, gdal_type, nodata=None):
    """Записать массив в GeoTIFF со сжатием."""
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, arr.shape[1], arr.shape[0], 1, gdal_type,
                       options=["COMPRESS=DEFLATE", "TILED=YES"])
    ds.SetGeoTransform(gt)
    ds.SetProjection(proj)
    band = ds.GetRasterBand(1)
    if nodata is not None:
        band.SetNoDataValue(nodata)
    band.WriteArray(arr)
    band.FlushCache()
    ds = None


def _topo_cell_xy(gt, row, col):
    """Координаты центра ячейки."""
    x = gt[0] + (col + 0.5) * gt[1] + (row + 0.5) * gt[2]
    y = gt[3] + (col + 0.5) * gt[4] + (row + 0.5) * gt[5]
    return x, y


def _topo_prepare_flow(layer, do_fill, epsilon, feedback, tr):
    """Общий вход 2.05-2.07: чтение, заполнение, D8, аккумуляция."""
    z, mask, gt, proj, cell = _topo_read_dem(layer, tr)
    if do_fill:
        feedback.pushInfo(tr("Заполнение понижений..."))
        z, n_raised, max_raise = fill_depressions(
            z, nodata_mask=mask, epsilon=max(epsilon, 1e-6),
            feedback=feedback)
        feedback.pushInfo(tr("Поднято ячеек: %d, максимальный подъём: %.2f м")
                          % (n_raised, max_raise))
    feedback.pushInfo(tr("Направления стока D8..."))
    dir_idx, downstream = topo_flow.d8_directions(z, nodata_mask=mask)
    feedback.pushInfo(tr("Аккумуляция..."))
    acc = topo_flow.flow_accumulation(downstream, z.shape, nodata_mask=mask)
    return z, mask, gt, proj, cell, dir_idx, downstream, acc


class FlowD8Algorithm(IsolinerAlgorithm):
    """2.05 Сток и аккумуляция D8."""

    INPUT = "INPUT"
    FILL = "FILL"
    EPSILON = "EPSILON"
    OUT_DIR = "OUT_DIR"
    OUT_ACC = "OUT_ACC"

    def tr(self, s): return _tr(s)
    def createInstance(self): return FlowD8Algorithm()
    def name(self): return "flow_d8"
    def displayName(self):
        return self.tr("2.05 Сток и аккумуляция (D8)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Считает направления стока D8 (Jenson-Domingue) и "
            "аккумуляцию: сколько ячеек стекает в каждую, включая её "
            "саму. Направления кодируются как в ArcGIS: E=1, SE=2, "
            "S=4, SW=8, W=16, NW=32, N=64, NE=128, сток=0. Береговая "
            "ячейка льёт в nodata, ячейка на рамке уходит с грида "
            "только без более низкого соседа внутри. Флажок заполнения "
            "понижений включён по умолчанию: на сырой ЦМР поток "
            "останавливается в ямах. Выход: два растра в группе "
            "Топография, направления GeoTIFF byte (nodata 255) и "
            "аккумуляция GeoTIFF float32 в ячейках (nodata -1).")
            + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Входная ЦМР")))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL, self.tr("Заполнить понижения перед расчётом"),
            defaultValue=_dv(self, self.FILL, True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.EPSILON, self.tr("Epsilon уклона при заполнении, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EPSILON, DEFAULT_EPSILON),
            minValue=0.0)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_DIR, self.tr("Направления стока (коды ESRI)")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_ACC, self.tr("Аккумуляция, ячеек")))

    def _process(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        do_fill = self.parameterAsBoolean(parameters, self.FILL, context)
        epsilon = self.parameterAsDouble(parameters, self.EPSILON, context)
        out_dir = self.parameterAsOutputLayer(parameters, self.OUT_DIR,
                                              context)
        out_acc = self.parameterAsOutputLayer(parameters, self.OUT_ACC,
                                              context)
        z, mask, gt, proj, cell, dir_idx, downstream, acc = \
            _topo_prepare_flow(layer, do_fill, epsilon, feedback, self.tr)
        esri = topo_flow.dir_to_esri(dir_idx)
        esri[mask] = 255
        _topo_write_raster(out_dir, esri, gt, proj, gdal.GDT_Byte,
                           nodata=255)
        acc_out = acc.astype(np.float32)
        acc_out[mask] = -1.0
        _topo_write_raster(out_acc, acc_out, gt, proj, gdal.GDT_Float32,
                           nodata=-1.0)
        feedback.pushInfo(self.tr("Максимальная аккумуляция: %d ячеек")
                          % int(acc.max()))
        _topo_group_layer(context, out_dir, self.tr("Топография"))
        _topo_group_layer(context, out_acc, self.tr("Топография"))
        return {self.OUT_DIR: out_dir, self.OUT_ACC: out_acc}


class RiverNetworkAlgorithm(IsolinerAlgorithm):
    """2.06 Речная сеть по порогу аккумуляции, порядок Стралера."""

    INPUT = "INPUT"
    THRESHOLD = "THRESHOLD"
    FILL = "FILL"
    EPSILON = "EPSILON"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return RiverNetworkAlgorithm()
    def name(self): return "river_network"
    def displayName(self):
        return self.tr("2.06 Речная сеть")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Извлекает речную сеть: ячейки с аккумуляцией не ниже "
            "порога связываются в звенья от истоков и слияний вниз по "
            "течению. Вершины линий идут вниз по течению, как "
            "водотоки в OSM, выход годится тальвегами для "
            "Topo2Raster. Поля: порядок Стралера, аккумуляция в "
            "замыкании звена, длина. Порог в ячейках: площадь "
            "водосбора истока, делённая на площадь ячейки. Для ЦМР "
            "30 м порог 1000 даёт начало рек с водосбора около "
            "0.9 кв. км. Выход: линейный слой в группе Топография с "
            "полями order, acc_out и length_m.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Входная ЦМР")))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD, self.tr("Порог аккумуляции, ячеек"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.THRESHOLD, 1000.0), minValue=2.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL, self.tr("Заполнить понижения перед расчётом"),
            defaultValue=_dv(self, self.FILL, True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.EPSILON, self.tr("Epsilon уклона при заполнении, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EPSILON, DEFAULT_EPSILON),
            minValue=0.0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Речная сеть"),
            QgsProcessing.SourceType.TypeVectorLine))

    def _process(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        threshold = self.parameterAsDouble(parameters, self.THRESHOLD,
                                           context)
        do_fill = self.parameterAsBoolean(parameters, self.FILL, context)
        epsilon = self.parameterAsDouble(parameters, self.EPSILON, context)
        z, mask, gt, proj, cell, dir_idx, downstream, acc = \
            _topo_prepare_flow(layer, do_fill, epsilon, feedback, self.tr)
        feedback.pushInfo(self.tr("Трассировка сети..."))
        links = topo_flow.river_network(downstream, acc, threshold, z.shape)

        fields = QgsFields()
        fields.append(QgsField("order", QVariant.Int))
        fields.append(QgsField("acc_out", QVariant.Double))
        fields.append(QgsField("length_m", QVariant.Double))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.LineString, layer.crs())
        nx = z.shape[1]
        for lk in links:
            pts = []
            for idx in lk["cells"]:
                r, c = divmod(int(idx), nx)
                pts.append(QgsPointXY(*_topo_cell_xy(gt, r, c)))
            geom = QgsGeometry.fromPolylineXY(pts)
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat["order"] = int(lk["order"])
            feat["acc_out"] = float(lk["acc_out"])
            feat["length_m"] = float(geom.length())
            sink.addFeature(feat)
        feedback.pushInfo(self.tr("Звеньев сети: %d") % len(links))
        _topo_group_layer(context, dest_id, self.tr("Топография"))
        return {self.OUTPUT: dest_id}


class BasinsAlgorithm(IsolinerAlgorithm):
    """2.07 Бассейны и водоразделы."""

    INPUT = "INPUT"
    POUR_POINTS = "POUR_POINTS"
    SNAP = "SNAP"
    THRESHOLD = "THRESHOLD"
    FILL = "FILL"
    EPSILON = "EPSILON"
    OUT_POLY = "OUT_POLY"
    OUT_RASTER = "OUT_RASTER"

    def tr(self, s): return _tr(s)
    def createInstance(self): return BasinsAlgorithm()
    def name(self): return "basins"
    def displayName(self):
        return self.tr("2.07 Бассейны и водоразделы")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Делит территорию на бассейны. С точками замыкания каждая "
            "точка притягивается к ячейке с наибольшей аккумуляцией в "
            "радиусе притяжки и собирает весь свой водосбор. Без "
            "точек бассейны строятся от устьев: ячеек, откуда поток "
            "покидает грид, с аккумуляцией не ниже порога. Границы "
            "полигонов - водоразделы. Ячейки, не попавшие ни в один "
            "бассейн, получают метку 0 и в полигоны не выводятся. "
            "Выход: полигоны в группе Топография с полями basin и "
            "area_m2, опционально растр меток GeoTIFF int32 "
            "(nodata 0).") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Входная ЦМР")))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.POUR_POINTS, self.tr("Точки замыкания (пусто: устья)"),
            [QgsProcessing.SourceType.TypeVectorPoint], optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.SNAP, self.tr("Радиус притяжки точек, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.SNAP, 150.0), minValue=0.0)))
        self.addParameter(QgsProcessingParameterNumber(
            self.THRESHOLD,
            self.tr("Порог аккумуляции устья, ячеек (без точек)"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.THRESHOLD, 1000.0), minValue=1.0))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL, self.tr("Заполнить понижения перед расчётом"),
            defaultValue=_dv(self, self.FILL, True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.EPSILON, self.tr("Epsilon уклона при заполнении, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EPSILON, DEFAULT_EPSILON),
            minValue=0.0)))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUT_POLY, self.tr("Бассейны (полигоны)"),
            QgsProcessing.SourceType.TypeVectorPolygon))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_RASTER, self.tr("Бассейны (растр меток)"),
            optional=True, createByDefault=False))

    def _seeds_from_points(self, source, layer_crs, context, gt, shape,
                           acc, snap_m, cell, feedback):
        seeds = {}
        ny, nx = shape
        transform = QgsCoordinateTransform(source.sourceCrs(), layer_crs,
                                           context.transformContext())
        inv = gdal.InvGeoTransform(gt)
        r_cells = int(round(snap_m / cell))
        label = 0
        for feat in source.getFeatures():
            geom = feat.geometry()
            if geom.isEmpty():
                continue
            pt = geom.asPoint()
            try:
                pt = transform.transform(pt)
            except Exception:
                pass
            px, py = gdal.ApplyGeoTransform(inv, pt.x(), pt.y())
            c, r = int(px), int(py)
            if not (0 <= r < ny and 0 <= c < nx):
                continue
            r0, r1 = max(0, r - r_cells), min(ny, r + r_cells + 1)
            c0, c1 = max(0, c - r_cells), min(nx, c + r_cells + 1)
            win = acc[r0:r1, c0:c1]
            dr, dc = np.unravel_index(int(np.argmax(win)), win.shape)
            label += 1
            seeds[(r0 + int(dr)) * nx + (c0 + int(dc))] = label
        if seeds:
            feedback.pushInfo(self.tr("Точек замыкания принято: %d")
                              % len(seeds))
        return seeds

    def _process(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        points = self.parameterAsSource(parameters, self.POUR_POINTS,
                                        context)
        snap_m = self.parameterAsDouble(parameters, self.SNAP, context)
        threshold = self.parameterAsDouble(parameters, self.THRESHOLD,
                                           context)
        do_fill = self.parameterAsBoolean(parameters, self.FILL, context)
        epsilon = self.parameterAsDouble(parameters, self.EPSILON, context)
        out_raster = self.parameterAsOutputLayer(parameters, self.OUT_RASTER,
                                                 context)
        z, mask, gt, proj, cell, dir_idx, downstream, acc = \
            _topo_prepare_flow(layer, do_fill, epsilon, feedback, self.tr)

        seeds = None
        if points is not None:
            seeds = self._seeds_from_points(
                points, layer.crs(), context, gt, z.shape, acc, snap_m,
                cell, feedback)
            if not seeds:
                raise QgsProcessingException(self.tr(
                    "Ни одна точка замыкания не попала на грид."))
        feedback.pushInfo(self.tr("Разметка бассейнов..."))
        lab = topo_flow.basins(downstream, z.shape, seeds=seeds,
                               nodata_mask=mask, acc=acc,
                               threshold=threshold)
        n_basins = int(lab.max())
        feedback.pushInfo(self.tr("Бассейнов: %d") % n_basins)

        if out_raster:
            _topo_write_raster(out_raster, lab.astype(np.int32), gt, proj,
                               gdal.GDT_Int32, nodata=0)

        fields = QgsFields()
        fields.append(QgsField("basin", QVariant.Int))
        fields.append(QgsField("area_m2", QVariant.Double))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUT_POLY, context, fields,
            QgsWkbTypes.Type.MultiPolygon, layer.crs())

        from osgeo import ogr
        mem_ds = gdal.GetDriverByName("MEM").Create(
            "", lab.shape[1], lab.shape[0], 1, gdal.GDT_Int32)
        mem_ds.SetGeoTransform(gt)
        mem_ds.SetProjection(proj)
        mem_ds.GetRasterBand(1).WriteArray(lab.astype(np.int32))
        drv = ogr.GetDriverByName("Memory")
        ogr_ds = drv.CreateDataSource("basins")
        ogr_lyr = ogr_ds.CreateLayer("basins", None, ogr.wkbPolygon)
        ogr_lyr.CreateField(ogr.FieldDefn("basin", ogr.OFTInteger))
        band = mem_ds.GetRasterBand(1)
        gdal.Polygonize(band, band, ogr_lyr, 0)  # маска: метка > 0

        counts = np.bincount(lab.ravel(), minlength=n_basins + 1)
        parts = {}
        for ogr_feat in ogr_lyr:
            b = int(ogr_feat.GetField("basin"))
            if b <= 0:
                continue
            g = ogr_feat.GetGeometryRef()
            parts.setdefault(b, []).append(g.ExportToWkt())
        for b in sorted(parts):
            wkts = parts[b]
            if len(wkts) == 1:
                geom = QgsGeometry.fromWkt(wkts[0])
            else:
                geom = QgsGeometry.unaryUnion(
                    [QgsGeometry.fromWkt(w) for w in wkts])
            geom.convertToMultiType()
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat["basin"] = b
            feat["area_m2"] = float(counts[b]) * cell * cell
            sink.addFeature(feat)
        ogr_ds = None
        mem_ds = None
        _topo_group_layer(context, dest_id, self.tr("Топография"))
        results = {self.OUT_POLY: dest_id}
        if out_raster:
            _topo_group_layer(context, out_raster, self.tr("Топография"))
            results[self.OUT_RASTER] = out_raster
        return results


class SlopeAspectAlgorithm(IsolinerAlgorithm):
    """2.08 Уклон и экспозиция (Horn 3x3)."""

    INPUT = "INPUT"
    OUT_SLOPE = "OUT_SLOPE"
    OUT_ASPECT = "OUT_ASPECT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return SlopeAspectAlgorithm()
    def name(self): return "slope_aspect"
    def displayName(self):
        return self.tr("2.08 Уклон и экспозиция")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Уклон в градусах и экспозиция по ядру Horn 3x3, как в "
            "gdaldem. Экспозиция - азимут спуска в градусах от севера "
            "по часовой стрелке, у плоских ячеек -1. Ячейки nodata и "
            "их соседи получают nodata: ядро через дыры не считаем. "
            "Выход: два растра GeoTIFF float32 в группе Топография, "
            "nodata -9999.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Входная ЦМР")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_SLOPE, self.tr("Уклон, градусы")))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUT_ASPECT, self.tr("Экспозиция, градусы")))

    def _process(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        out_slope = self.parameterAsOutputLayer(parameters, self.OUT_SLOPE,
                                                context)
        out_aspect = self.parameterAsOutputLayer(parameters, self.OUT_ASPECT,
                                                 context)
        z, mask, gt, proj, cell, = _topo_read_dem(layer, self.tr)
        slope, aspect = topo_surface.slope_aspect(z, cell, nodata_mask=mask)
        nd = -9999.0
        slope = np.where(np.isfinite(slope), slope, nd).astype(np.float32)
        aspect = np.where(np.isfinite(aspect), aspect, nd).astype(np.float32)
        _topo_write_raster(out_slope, slope, gt, proj, gdal.GDT_Float32,
                           nodata=nd)
        _topo_write_raster(out_aspect, aspect, gt, proj, gdal.GDT_Float32,
                           nodata=nd)
        _topo_group_layer(context, out_slope, self.tr("Топография"))
        _topo_group_layer(context, out_aspect, self.tr("Топография"))
        return {self.OUT_SLOPE: out_slope, self.OUT_ASPECT: out_aspect}


class PeaksAlgorithm(IsolinerAlgorithm):
    """2.09 Вершины: локальные максимумы с фильтром превышения."""

    INPUT = "INPUT"
    RADIUS = "RADIUS"
    MIN_DROP = "MIN_DROP"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return PeaksAlgorithm()
    def name(self): return "peaks_extract"
    def displayName(self):
        return self.tr("2.09 Вершины")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Находит вершины: ячейки, самые высокие в квадратном окне "
            "заданного радиуса, с превышением над минимумом окна не "
            "меньше порога. Радиус отсекает второстепенные макушки "
            "рядом с главной, превышение отсекает кочки на равнине. "
            "Выход: точечный слой в группе Топография с полями z "
            "(высота, м) и drop (превышение, м).") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Входная ЦМР")))
        self.addParameter(QgsProcessingParameterNumber(
            self.RADIUS, self.tr("Радиус окна, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.RADIUS, 500.0), minValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(
            self.MIN_DROP, self.tr("Минимальное превышение, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.MIN_DROP, 20.0), minValue=0.0))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Вершины (точки)"),
            QgsProcessing.SourceType.TypeVectorPoint))

    def _process(self, parameters, context, feedback):
        layer = self.parameterAsRasterLayer(parameters, self.INPUT, context)
        radius = self.parameterAsDouble(parameters, self.RADIUS, context)
        min_drop = self.parameterAsDouble(parameters, self.MIN_DROP, context)
        z, mask, gt, proj, cell = _topo_read_dem(layer, self.tr)
        peaks = topo_surface.find_peaks(z, cell, radius, min_drop,
                                        nodata_mask=mask)
        fields = QgsFields()
        fields.append(QgsField("z", QVariant.Double))
        fields.append(QgsField("drop", QVariant.Double))
        sink, dest_id = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Type.Point, layer.crs())
        for r, c, zv, dv in peaks:
            feat = QgsFeature(fields)
            feat.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(*_topo_cell_xy(gt, r, c))))
            feat["z"] = zv
            feat["drop"] = dv
            sink.addFeature(feat)
        feedback.pushInfo(self.tr("Найдено вершин: %d") % len(peaks))
        _topo_group_layer(context, dest_id, self.tr("Топография"))
        return {self.OUTPUT: dest_id}


class Topo2RasterAlgorithm(IsolinerAlgorithm):
    """2.03 Topo2Raster: рельеф из векторных ограничений."""

    POINTS = "POINTS"
    POINTS_FIELD = "POINTS_FIELD"
    CONTOURS = "CONTOURS"
    CONTOURS_FIELD = "CONTOURS_FIELD"
    STREAMS = "STREAMS"
    BREAKLINES = "BREAKLINES"
    LAKES = "LAKES"
    LAKES_FIELD = "LAKES_FIELD"
    EXTENT = "EXTENT"
    CELL = "CELL"
    ITERATIONS = "ITERATIONS"
    MIN_DROP = "MIN_DROP"
    FILL = "FILL"
    EPSILON = "EPSILON"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)
    def createInstance(self): return Topo2RasterAlgorithm()
    def name(self): return "topo2raster"
    def displayName(self):
        return self.tr("2.03 Topo2Raster (рельеф из векторов)")
    def helpUrl(self): return _help_url()
    def group(self): return self.tr(GROUP_TOPO)
    def groupId(self): return GROUP_TOPO_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Строит рельеф из векторных данных мультисеточной "
            "интерполяцией от грубой сетки к тонкой, по мотивам "
            "ANUDEM. Каждый тип входа работает своим ограничением: "
            "точки высот и изолинии - жёсткие узлы, тальвеги - "
            "принудительное падение вниз по течению (вершины линий "
            "должны идти вниз по течению, водотоки OSM и выход "
            "инструмента 2.06 подходят как есть), обрывы - барьер "
            "сглаживания, поверхности по сторонам независимы, озёра - "
            "горизонтальные плоскости: с высотой в поле приколоты к "
            "ней, без высоты уровень берётся по минимуму берега. "
            "Нужен хотя бы один слой с высотами: точки или изолинии. "
            "Все слои приводятся к СК первого заданного слоя, она "
            "должна быть метрической. Финальное заполнение понижений "
            "флажком. Выход: GeoTIFF float32, высоты в метрах, nodata "
            "-9999, слой в группе Топография.") + _credit())

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.POINTS, self.tr("Точки высот"),
            [QgsProcessing.SourceType.TypeVectorPoint], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.POINTS_FIELD, self.tr("Поле высоты точек"),
            parentLayerParameterName=self.POINTS, optional=True,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.CONTOURS, self.tr("Изолинии"),
            [QgsProcessing.SourceType.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.CONTOURS_FIELD, self.tr("Поле высоты изолиний"),
            parentLayerParameterName=self.CONTOURS, optional=True,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.STREAMS, self.tr("Тальвеги (вниз по течению)"),
            [QgsProcessing.SourceType.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.BREAKLINES, self.tr("Обрывы (барьеры сглаживания)"),
            [QgsProcessing.SourceType.TypeVectorLine], optional=True))
        self.addParameter(QgsProcessingParameterFeatureSource(
            self.LAKES, self.tr("Озёра (плоскости)"),
            [QgsProcessing.SourceType.TypeVectorPolygon], optional=True))
        self.addParameter(QgsProcessingParameterField(
            self.LAKES_FIELD, self.tr("Поле уровня озёр (пусто: по берегу)"),
            parentLayerParameterName=self.LAKES, optional=True,
            type=QgsProcessingParameterField.DataType.Numeric))
        self.addParameter(QgsProcessingParameterExtent(
            self.EXTENT, self.tr("Охват (пусто: по слоям)"), optional=True))
        self.addParameter(QgsProcessingParameterNumber(
            self.CELL, self.tr("Размер ячейки, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.CELL, topo_t2r.DEFAULT_CELL),
            minValue=0.1))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.ITERATIONS, self.tr("Итераций сглаживания на уровень"),
            QgsProcessingParameterNumber.Type.Integer,
            defaultValue=_dv(self, self.ITERATIONS,
                             topo_t2r.DEFAULT_ITERATIONS), minValue=10)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.MIN_DROP, self.tr("Минимальное падение тальвега, м/ячейку"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.MIN_DROP,
                             topo_t2r.DEFAULT_MIN_DROP), minValue=0.0)))
        self.addParameter(QgsProcessingParameterBoolean(
            self.FILL, self.tr("Заполнить понижения в итоге"),
            defaultValue=_dv(self, self.FILL, True)))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.EPSILON, self.tr("Epsilon уклона при заполнении, м"),
            QgsProcessingParameterNumber.Type.Double,
            defaultValue=_dv(self, self.EPSILON, DEFAULT_EPSILON),
            minValue=0.0)))
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Рельеф")))

    # --- извлечение геометрии --------------------------------------

    @staticmethod
    def _transformer(source, target_crs, context):
        return QgsCoordinateTransform(source.sourceCrs(), target_crs,
                                      context.transformContext())

    def _iter_lines(self, source, target_crs, context):
        tf = self._transformer(source, target_crs, context)
        for feat in source.getFeatures():
            geom = feat.geometry()
            if geom.isEmpty():
                continue
            try:
                geom.transform(tf)
            except Exception:
                continue
            try:
                multi = geom.asMultiPolyline()
            except TypeError:
                multi = []  # QGIS 4: одиночная линия бросает TypeError
            if not multi:
                line = geom.asPolyline()
                multi = [line] if line else []
            for line in multi:
                if len(line) >= 2:
                    yield feat, np.array([[p.x(), p.y()] for p in line])

    def _collect_points(self, source, field, target_crs, context):
        pts = []
        tf = self._transformer(source, target_crs, context)
        for feat in source.getFeatures():
            geom = feat.geometry()
            if geom.isEmpty():
                continue
            zv = feat[field] if field else None
            if zv is None:
                continue
            try:
                geom.transform(tf)
            except Exception:
                continue
            try:
                mp = geom.asMultiPoint()
            except TypeError:
                mp = []  # QGIS 4: одиночная точка бросает TypeError
            if not mp:
                mp = [geom.asPoint()]
            for p in mp:
                pts.append((p.x(), p.y(), float(zv)))
        return pts

    def _collect_contours(self, source, field, target_crs, context, cell):
        pts = []
        for feat, xy in self._iter_lines(source, target_crs, context):
            zv = feat[field] if field else None
            if zv is None:
                continue
            dense = topo_t2r.densify(xy, cell)
            for x, y in dense:
                pts.append((float(x), float(y), float(zv)))
        return pts

    def _collect_lakes(self, source, field, target_crs, context):
        lakes = []
        tf = self._transformer(source, target_crs, context)
        for feat in source.getFeatures():
            geom = feat.geometry()
            if geom.isEmpty():
                continue
            try:
                geom.transform(tf)
            except Exception:
                continue
            zv = feat[field] if field else None
            try:
                polys = geom.asMultiPolygon()
            except TypeError:
                polys = []  # QGIS 4: одиночный полигон бросает TypeError
            if not polys:
                poly = geom.asPolygon()
                polys = [poly] if poly else []
            for rings in polys:
                arr = [np.array([[p.x(), p.y()] for p in ring])
                       for ring in rings if len(ring) >= 4]
                if arr:
                    lakes.append((arr, float(zv) if zv is not None
                                  else None))
        return lakes

    # --- расчёт ------------------------------------------------------

    def _process(self, parameters, context, feedback):
        cell = self.parameterAsDouble(parameters, self.CELL, context)
        iterations = self.parameterAsInt(parameters, self.ITERATIONS,
                                         context)
        min_drop = self.parameterAsDouble(parameters, self.MIN_DROP, context)
        do_fill = self.parameterAsBoolean(parameters, self.FILL, context)
        epsilon = self.parameterAsDouble(parameters, self.EPSILON, context)
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT,
                                               context)
        src_points = self.parameterAsSource(parameters, self.POINTS, context)
        src_contours = self.parameterAsSource(parameters, self.CONTOURS,
                                              context)
        src_streams = self.parameterAsSource(parameters, self.STREAMS,
                                             context)
        src_breaks = self.parameterAsSource(parameters, self.BREAKLINES,
                                            context)
        src_lakes = self.parameterAsSource(parameters, self.LAKES, context)
        f_points = self.parameterAsString(parameters, self.POINTS_FIELD,
                                          context)
        f_contours = self.parameterAsString(parameters, self.CONTOURS_FIELD,
                                            context)
        f_lakes = self.parameterAsString(parameters, self.LAKES_FIELD,
                                         context)

        sources = [s for s in (src_points, src_contours, src_streams,
                               src_breaks, src_lakes) if s is not None]
        if src_points is None and src_contours is None:
            raise QgsProcessingException(self.tr(
                "Нужен хотя бы один слой с высотами: точки или изолинии."))
        target_crs = sources[0].sourceCrs()
        if target_crs.isGeographic():
            raise QgsProcessingException(self.tr(
                "СК первого слоя должна быть метрической, градусные "
                "гриды в анализ не пускаем."))

        pts = []
        if src_points is not None:
            if not f_points:
                raise QgsProcessingException(self.tr(
                    "Укажите поле высоты точек."))
            pts += self._collect_points(src_points, f_points, target_crs,
                                        context)
        if src_contours is not None:
            if not f_contours:
                raise QgsProcessingException(self.tr(
                    "Укажите поле высоты изолиний."))
            pts += self._collect_contours(src_contours, f_contours,
                                          target_crs, context, cell)
        if not pts:
            raise QgsProcessingException(self.tr(
                "Во входных слоях не нашлось ни одного узла с высотой."))
        pts = np.array(pts, dtype=np.float64)
        feedback.pushInfo(self.tr("Узлов с высотой: %d") % len(pts))

        streams = []
        if src_streams is not None:
            streams = [xy for _f, xy in self._iter_lines(
                src_streams, target_crs, context)]
        breaklines = []
        if src_breaks is not None:
            breaklines = [xy for _f, xy in self._iter_lines(
                src_breaks, target_crs, context)]
        lakes = []
        if src_lakes is not None:
            lakes = self._collect_lakes(src_lakes, f_lakes, target_crs,
                                        context)

        extent = self.parameterAsExtent(parameters, self.EXTENT, context,
                                        target_crs)
        if extent.isEmpty():
            xmin = float(pts[:, 0].min())
            ymin = float(pts[:, 1].min())
            xmax = float(pts[:, 0].max())
            ymax = float(pts[:, 1].max())
            pad = 2.0 * cell
            ext = (xmin - pad, ymin - pad, xmax + pad, ymax + pad)
        else:
            ext = (extent.xMinimum(), extent.yMinimum(),
                   extent.xMaximum(), extent.yMaximum())

        feedback.pushInfo(self.tr("Мультисеточная интерполяция..."))
        try:
            z, x0, y_top = topo_t2r.topo2raster(
                pts, streams, breaklines, lakes, ext, cell,
                iterations=iterations, min_drop=min_drop,
                feedback=feedback)
        except topo_t2r.Topo2RasterError as exc:
            raise QgsProcessingException(str(exc))
        if feedback.isCanceled():
            return {}

        if do_fill:
            feedback.pushInfo(self.tr("Заполнение понижений..."))
            z, n_raised, max_raise = fill_depressions(
                z, epsilon=max(epsilon, 1e-6), feedback=feedback)
            feedback.pushInfo(
                self.tr("Поднято ячеек: %d, максимальный подъём: %.2f м")
                % (n_raised, max_raise))

        gt = (x0, cell, 0.0, y_top, 0.0, -cell)
        srs = osr.SpatialReference()
        auth = target_crs.authid()
        if auth.startswith("EPSG:"):
            srs.ImportFromEPSG(int(auth.split(":")[1]))
        else:
            srs.ImportFromWkt(target_crs.toWkt())
        gdal.UseExceptions()
        _topo_write_raster(out_path, z.astype(np.float32), gt,
                           srs.ExportToWkt(), gdal.GDT_Float32,
                           nodata=-9999.0)
        feedback.pushInfo(self.tr("Готово: %dx%d ячеек.")
                          % (z.shape[1], z.shape[0]))
        _topo_group_layer(context, out_path, self.tr("Топография"))
        return {self.OUTPUT: out_path}


ALGORITHMS = [
    DeclusteringAlgorithm,
    Kriging2DAlgorithm,
    MinCurvatureAlgorithm,
    RasterToIsolinesAlgorithm,
    ExperimentalVariogramAlgorithm,
    VariogramMapAlgorithm,
    CrossValidationAlgorithm,
    MethodCrossValidationAlgorithm,
    ProfilesAlgorithm,
    ExampleWellsAlgorithm,
    GeophysProfilesDemoAlgorithm,
    CategoricalIndicatorAlgorithm,
    SectionDemoAlgorithm,
    FlowGradientAlgorithm,
    ExternalDriftKrigingAlgorithm,
    ExceedanceProbabilityAlgorithm,
    DarcyFluxAlgorithm,
    SequentialGaussianSimAlgorithm,
    VariableSupportDensityAlgorithm,
    DensityDemoAlgorithm,
    DemDownloadAlgorithm,
    TopobaseDownloadAlgorithm,
    TopoFillDepressionsAlgorithm,
    TopoDemoReliefAlgorithm,
    FlowD8Algorithm,
    RiverNetworkAlgorithm,
    BasinsAlgorithm,
    PeaksAlgorithm,
    SlopeAspectAlgorithm,
    Topo2RasterAlgorithm,
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
    DomainsToGridAlgorithm,
    ReserveDeltaAlgorithm,
    PolyhedralDemoAlgorithm,
    FractalDimensionAlgorithm,
    BoxCountingAlgorithm,
    LineDimensionAlgorithm,
    MinkowskiDimensionAlgorithm,
    FractalDemoAlgorithm,
]
