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

from qgis.PyQt.QtCore import QCoreApplication, QUrl, QVariant
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
    Variogram, build_grid, clip_outliers, cross_validate, EPS,
    experimental_variogram, fit_variogram, model_curve,
    MODEL_SPHERICAL, MODEL_EXPONENTIAL, MODEL_GAUSSIAN)
from .isolines import (
    isolines_from_raster, isolines_and_polygons, compute_levels, DEFAULT_FIELD,
    _gaussian_nodata)

GROUP = "Грид и изолинии"
GROUP_ID = "grid_isolines"

MODEL_LABELS = ["Сферическая", "Экспоненциальная", "Гауссова", "Степенная"]
KTYPE_LABELS = ["Ординарный (OK)", "Простой (SK)"]

NSTRUCT = 1  # число структур вариограммы (S2/S3 убраны как неиспользуемые)

CREDIT = ("\n\n- - -\nРазработано при поддержке ООО «Информ++» "
          "(www.informpp.ru).")


def _tr(s):
    return QCoreApplication.translate("GridIsolines", s)


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
    """file:// ссылка на руководство в комплекте плагина (для кнопки «Справка»)."""
    p = os.path.join(os.path.dirname(__file__), "doc", "Isoliner.pdf")
    return QUrl.fromLocalFile(p).toString() if os.path.exists(p) else ""


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


# Профили обработки: именованные наборы «вариограмма (Структура 1) + наггет +
# отсев ураганных проб». Хранятся глобально в QgsSettings (кросс-проектно),
# отдельно от per-algorithm настроек. «Вариограмма» и «Кросс-валидация» их
# сохраняют, «2D Kriging» подставляет, отдельный инструмент «Профили» правит.
PROFILE_NONE = "(не выбран)"


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
        s = ("наггет C0=%.4g, %s, порог C=%.4g, радиус a=%.4g" % (
            p["nugget"], MODEL_LABELS[int(p["model"])], p["sill"], p["range"]))
        if float(p.get("anis", 1.0)) != 1.0 or float(p.get("azimuth", 0.0)) != 0.0:
            s += (", анизотропия %.3g по азимуту %.4g°" % (
                p.get("anis", 1.0), p.get("azimuth", 0.0)))
        if float(p.get("val_pct", 0.0)) != 0.0:
            s += (", отсев %.4g%%" % p["val_pct"])
        return s
    except Exception:
        return "профиль"


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
            "Профиль не найден - использую значения из диалога. "
            "Список профилей обновляется при открытии окна инструмента.")
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
        "Подставлен профиль «%s»: %s." % (name, _profile_summary(prof)))
    return parameters


def _profile_enum(key, label, pick=False):
    """Выпадающий список профилей с подписью-обёрткой (значения профиля
    строкой ниже). На QGIS без старого API виджетов - обычный список."""
    p = QgsProcessingParameterEnum(
        key, _tr(label), options=[PROFILE_NONE] + _profile_names(),
        defaultValue=0)
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
        alg.KTYPE, _tr("Тип кригинга"), options=KTYPE_LABELS,
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
            options=MODEL_LABELS, defaultValue=_dv(alg, _sk(i, "MODEL"), 0))))
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
            "Пропущено точек: %d без значения «%s»%s. Прочитано: %d." %
            (skipped_value, zfield,
             (" и %d без геометрии" % skipped_geom) if skipped_geom else "",
             len(xs)))
    if len(xs) < 2:
        raise QgsProcessingException(
            "Недостаточно валидных точек с числовым значением.")

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
                    "Ураганные пробы: срезано %d значений к [%.4g; %.4g]." %
                    (nch, lo, hi))
        else:
            ncut = int(np.count_nonzero(~keep))
            xs, ys, vs = xs[keep], ys[keep], vs[keep]
            if return_ids:
                ids = ids[keep]
            if feedback is not None:
                feedback.pushInfo(
                    "Ураганные пробы: удалено %d точек вне [%.4g; %.4g]; "
                    "осталось %d." % (ncut, lo, hi, len(xs)))
        if len(xs) < 2:
            raise QgsProcessingException(
                "После отсева ураганных проб осталось < 2 точек.")

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
                "Совпадающих точек усреднено: %d (осталось %d)" %
                (len(inv) - len(uniq), len(uniq)))
    if len(xs) < 2:
        raise QgsProcessingException(
            "После усреднения совпадающих точек осталось < 2 узлов.")
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
                        "Структура %d: степенная модель - поле «радиус a» это "
                        "показатель ω (0<ω<2), а не радиус; задан 0, взят ω=1." % i)
            elif not (0.0 < rng < 2.0):
                if feedback:
                    feedback.pushWarning(
                        "Структура %d: показатель степенной модели ω=%.3g вне "
                        "(0; 2) - приведён к диапазону." % (i, rng))
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
            "Задан только наггет (структурный вклад C = 0): кригинг выродится "
            "в локальное среднее, поверхность будет почти плоской.")
    return Variogram(nugget, structures)


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
    feedback.pushInfo("Сетка %d x %d, ячейка %.4g, точек %d, структур %d" %
                      (nx, ny, cell, len(xd), vg.nst))
    feedback.pushInfo(
        "Дисперсия данных: %.4g. Ориентир: суммарный силл (C0 + вклады C) "
        "задавайте близким к ней. Наггет и силл - в абсолютных единицах "
        "дисперсии, не 0-1." % float(np.var(vrd)))

    def prog(done, total):
        if feedback.isCanceled():
            raise QgsProcessingException("Прервано пользователем.")
        feedback.setProgress(int(80.0 * done / total))

    res = build_grid(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
                     rad2, nodata, xmn, ymn, cell, nx, ny, progress=prog,
                     with_variance=want_se)
    grid, segrid = res if want_se else (res, None)

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
        feedback.pushInfo("Сглаживание грида (σ=%g яч.)…" % sm_rad)
        gvalid = np.isfinite(grid) & (grid != nodata)
        gsm = _gaussian_nodata(grid, gvalid, float(sm_rad))
        grid = np.where(gvalid, gsm, nodata).astype(grid.dtype)
    if mask_layer is not None:
        feedback.pushInfo("Обрезка по маске…")
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
    feedback.pushInfo("Контур скважин: выпуклая оболочка…")
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
    alg.addParameter(QgsProcessingParameterNumber(
        alg.SMOOTH_LINE_ITER, _tr("Скругление линий, итераций (0 = выкл.)"),
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

    SMOOTH, SMOOTH_RADIUS = "SMOOTH", "SMOOTH_RADIUS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return Kriging2DAlgorithm()
    def name(self): return "kriging2d"
    def displayName(self): return self.tr("1. 2D Kriging (точки → растр)")

    def helpUrl(self): return _help_url()
    def group(self): return GROUP
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
            "скважин." + CREDIT))

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
        _add_kriging_params(self)
        self.addParameter(_profile_enum(
            self.PROFILE, "Загрузить профиль обработки"))
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
        _save_values(self, parameters)
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
                         "Кригинг %s · %s" % (zfield, _short(src)))
        results = {self.OUTPUT: path}
        if se:
            _set_output_name(context, se,
                             "Стд. ошибка · %s · %s" % (zfield, _short(src)))
            results[self.OUTPUT_STDERR] = se
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
    FIELD_NAME, OUTPUT, OUTPUT_POLYGONS = "FIELD_NAME", "OUTPUT", "OUTPUT_POLYGONS"

    def tr(self, s): return _tr(s)
    def createInstance(self): return RasterToIsolinesAlgorithm()
    def name(self): return "raster_to_isolines"
    def displayName(self): return self.tr("2. Изолинии из растра")

    def helpUrl(self): return _help_url()
    def group(self): return GROUP
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return _help_version(self.tr(
            "Строит изолинии из растра: равномерный шаг или явные уровни "
            "(через пробел), главные (утолщённые) изолинии флагом is_index, "
            "фильтр коротких линий.\n\nСкругление линий (Chaikin) слегка "
            "сглаживает контуры и убирает «октагоны» от грубого грида. "
            "Сглаживание самого поля выполняется в инструменте 2D Kriging.\n\n"
            "По умолчанию строит и "
            "контурные полигоны (пояса между изолиниями) во временный слой - их "
            "границы СОВПАДАЮТ с изолиниями, покрытие сплошное. Чтобы их не "
            "строить - очистите поле «Контурные полигоны».\n\nПоля: линии - "
            "значение уровня (по умолчанию ELEV) и is_index (1 у главных); "
            "полигоны - ELEV_MIN/ELEV_MAX (диапазон пояса)." + CREDIT))

    def initAlgorithm(self, config=None):
        self._defaults = _load_defaults(self)
        self.addParameter(QgsProcessingParameterRasterLayer(
            self.INPUT, self.tr("Растр")))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.BAND, self.tr("Канал"),
            QgsProcessingParameterNumber.Integer, defaultValue=1, minValue=1)))
        _add_isoline_params(self)
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUTPUT, self.tr("Изолинии (линии)"),
            type=QgsProcessing.TypeVectorLine))
        self.addParameter(QgsProcessingParameterVectorDestination(
            self.OUTPUT_POLYGONS, self.tr("Контурные полигоны"),
            type=QgsProcessing.TypeVectorPolygon,
            optional=True, createByDefault=True))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        _save_values(self, parameters)
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

        if poly_dest:
            # линии и пояса строятся из ОДНОГО набора линий -> границы совпадают
            res = isolines_and_polygons(
                rl.source(), band, interval, base, levels, index_every,
                min_len, False, 0.0, sm_line, field_name, True, nodata,
                out_dest, poly_dest, context, feedback)
            out, poly = res["lines"], res["polygons"]
            _set_output_name(context, out, "Изолинии · %s" % name)
            _set_output_name(context, poly, "Полигоны · %s" % name)
            _order_lines_above(context, out, poly)   # изолинии над полигонами
            results = {self.OUTPUT: out, self.OUTPUT_POLYGONS: poly}
        else:
            out = isolines_from_raster(
                rl.source(), band, interval, base, levels, index_every,
                min_len, False, 0.0, sm_line, field_name, True, nodata,
                out_dest, context, feedback)
            _set_output_name(context, out, "Изолинии · %s" % name)
            results = {self.OUTPUT: out}

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
            tips.append("MSDR заметно больше 1 (%.3g): карта стандартной ошибки "
                        "занижена. Умножьте наггет C0 и вклады C на MSDR (радиус "
                        "и модель не трогайте) и пересчитайте - сами оценки не "
                        "изменятся, поправится только дисперсия кригинга." % msdr)
            good = False
        elif msdr < 0.7:
            tips.append("MSDR меньше 1 (%.3g): неопределённость завышена. "
                        "Разделите наггет C0 и вклады C на MSDR (радиус и модель "
                        "не трогайте) и пересчитайте - оценки не изменятся." % msdr)
            good = False
        else:
            tips.append("MSDR близок к 1: масштаб вариограммы подобран адекватно.")
    if abs(me) > 0.1 * rr:
        tips.append("ME заметно отличается от 0 (%+.3g): возможен "
                    "систематический сдвиг - проверьте данные и тип кригинга "
                    "(для простого - заданное среднее)." % me)
        good = False
    else:
        tips.append("ME близок к 0: систематического смещения нет.")
    if r == r:
        if r < 0.5:
            tips.append("Низкая корреляция (R=%.2f): модель слабо предсказывает - "
                        "попробуйте другой радиус, модель или анизотропию; либо "
                        "это предел данных (короткомасштабная изменчивость, "
                        "зоны замещения)." % r)
            good = False
        elif r >= 0.8:
            tips.append("Высокая корреляция (R=%.2f): оценки хорошо согласуются "
                        "с фактом." % r)
    if good:
        tips.insert(0, "Итог: параметры можно утверждать - перенесите ту же "
                       "вариограмму и настройки поиска в «2D Kriging».")
    else:
        tips.insert(0, "Итог: параметры стоит подправить (см. ниже) и повторить "
                       "кросс-валидацию перед финальным кригингом.")
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
        items.append(("Тип кригинга", "простой (SK)"))
        items.append(("Среднее (SK)", "%.4g" % pd(alg.SKMEAN)))
    nug = pd(alg.NUGGET)
    if nug != 0:
        items.append(("Наггет C0", "%.4g" % nug))
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
            parts.append("азимут=%g°" % az)
        anis = pd(_sk(i, "ANIS"))
        if anis != 1:
            parts.append("анис=%g" % anis)
        if parts:
            items.append(("Структура %d" % i, ", ".join(parts)))
    if pd(alg.RADIUS) != 0:
        items.append(("Радиус поиска", "%g" % pd(alg.RADIUS)))
    if pi(alg.MIN_POINTS) != 1:
        items.append(("Мин. точек", "%d" % pi(alg.MIN_POINTS)))
    if pi(alg.MAX_POINTS) != 24:
        items.append(("Макс. точек", "%d" % pi(alg.MAX_POINTS)))
    if pd(alg.VAL_PCT) != 0:
        items.append(("Отсев: перцентиль, %", "%.4g" % pd(alg.VAL_PCT)))
    vmin = popt(alg.VAL_MIN)
    if vmin is not None:
        items.append(("Нижняя граница", "%.4g" % vmin))
    vmax = popt(alg.VAL_MAX)
    if vmax is not None:
        items.append(("Верхняя граница", "%.4g" % vmax))
    if pb(alg.VAL_CAP):
        items.append(("Срезка (capping)", "да"))
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
    table = ("<table style='border-collapse:collapse' cellpadding='6'>"
             "<tr><th align='left'>Метрика</th><th>Значение</th>"
             "<th align='left'>Смысл</th></tr>%s</table>" % rows)
    if used_params:
        prows = "".join(
            "<tr><td style='color:#555'>%s</td>"
            "<td style='text-align:right'><b>%s</b></td></tr>" % kv
            for kv in used_params)
        params_inner = ("<table style='border-collapse:collapse' "
                        "cellpadding='4'>%s</table>" % prows)
    else:
        params_inner = ("<span style='color:#777'>все параметры - "
                        "стандартные</span>")
    params_box = (
        "<div style='background:#f5f5f7;border:1px solid #ddd;"
        "padding:8px 14px;border-radius:6px'>"
        "<b>Параметры кригинга</b> "
        "<span style='color:#888;font-size:88%%'>(отличные от стандартных)</span>"
        "<div style='margin-top:6px'>%s</div></div>" % params_inner)
    table = ("<div style='display:flex;gap:24px;flex-wrap:wrap;"
             "align-items:flex-start'><div>%s</div><div>%s</div></div>"
             % (table, params_box))
    advice_html = ""
    if advice:
        items = "".join("<li>%s</li>" % a for a in advice)
        advice_html = (
            "<div style='background:#f3f7f4;border:1px solid #cde0d6;"
            "padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'>"
            "<b>Рекомендации</b><ul style='margin:6px 0'>%s</ul></div>" % items)
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
            note = " (показаны ~30000 точек)"
        else:
            sel = _np.arange(n); note = ""
        fx = fact[sel]; ey = est[sel]
        lab_sel = [labels[i] for i in sel]
        lo = float(min(fx.min(), ey.min())); hi = float(max(fx.max(), ey.max()))
        fig = make_subplots(
            rows=2, cols=2, row_heights=[0.58, 0.42],
            specs=[[{"colspan": 2}, None], [{}, {}]],
            subplot_titles=("Оценка vs факт" + note,
                            "Гистограмма ошибок", "QQ-график остатков"),
            vertical_spacing=0.12)
        fig.add_trace(go.Scattergl(
            x=fx, y=ey, mode="markers",
            marker=dict(size=4, color="#1f6f54", opacity=0.35),
            customdata=lab_sel,
            hovertemplate="скв. %{customdata}<br>факт %{x:.3g}"
                          "<br>оценка %{y:.3g}<extra></extra>"),
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
                hovertemplate="скв. %{text}<br>факт %{x:.3g}"
                              "<br>оценка %{y:.3g}<extra>худшие</extra>"),
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
                hovertemplate="теор. %{x:.2f}<br>ошибка (z) %{y:.2f}<extra></extra>"),
                row=2, col=2)
            fig.add_trace(go.Scatter(
                x=[ql, qh], y=[ql, qh], mode="lines",
                line=dict(color="#cc3333", width=2), hoverinfo="skip"),
                row=2, col=2)
            fig.update_xaxes(title_text="теор. квантили (норм.)", row=2, col=2)
            fig.update_yaxes(title_text="ошибка (z-оценка)", row=2, col=2)
        else:
            fig.add_annotation(text="недостаточно точек для QQ",
                               showarrow=False, row=2, col=2)
        fig.update_xaxes(title_text="факт", row=1, col=1)
        fig.update_yaxes(title_text="оценка (LOO)", row=1, col=1)
        fig.update_xaxes(title_text="оценка − факт", row=2, col=1)
        fig.update_layout(showlegend=False, height=720,
                          margin=dict(l=50, r=20, t=50, b=50))
        chart = fig.to_html(full_html=False, include_plotlyjs=True)
    except Exception as e:
        if feedback is not None:
            feedback.pushInfo("plotly недоступен (%s) - отчёт без графика." % e)
        chart = "<p><i>Интерактивный график недоступен (нет plotly). "
        chart += "Диаграмму можно построить по слою остатков.</i></p>"
    html = (
        "<html><head><meta charset='utf-8'><title>%s</title></head><body>"
        "<h2>%s</h2>%s%s<br>%s%s</body></html>" % (
            title, title, table, advice_html, chart, _version_footer()))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(html)


def _add_cv_params(alg):
    """Параметры для кросс-валидации: вариограмма и поиск, без сетки/растра."""
    alg.addParameter(QgsProcessingParameterEnum(
        alg.KTYPE, _tr("Тип кригинга"), options=KTYPE_LABELS,
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
            options=MODEL_LABELS, defaultValue=_dv(alg, _sk(i, "MODEL"), 0))))
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
    PROFILE = "PROFILE"
    OUTPUT = "OUTPUT"
    OUTPUT_HTML = "OUTPUT_HTML"
    SAVE_PROFILE = "SAVE_PROFILE"

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def name(self):
        return "crossvalidation"

    def displayName(self):
        return self.tr("4. Кросс-валидация вариограммы")

    def group(self):
        return self.tr("Грид и изолинии")

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
        self.addParameter(_profile_enum(
            self.PROFILE, "Загрузить профиль обработки"))
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
                    "Профиль «%s» сохранён: проверенная модель Структуры 1 "
                    "(с анизотропией, если задана) + отсев." % pname.strip())
        nodata = -9999.0

        dvar = float(np.var(vrd))
        feedback.pushInfo(
            "Дисперсия данных: %.4g. Ориентир: суммарный силл (C0 + вклады C) "
            "задавайте близким к ней. Наггет и силл - в абсолютных единицах "
            "дисперсии, не 0-1." % dvar)
        feedback.pushInfo("Кросс-валидация по %d точкам…" % len(xd))

        def prog(done, total):
            if feedback.isCanceled():
                raise QgsProcessingException("Прервано пользователем.")
            feedback.setProgress(int(95.0 * done / total))
        est, var = cross_validate(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
                                  rad2, nodata, progress=prog)

        ok = est != nodata
        nvalid = int(ok.sum())
        if nvalid < 2:
            raise QgsProcessingException("Слишком мало оценённых точек.")
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

        feedback.pushInfo("== Кросс-валидация (leave-one-out) ==")
        feedback.pushInfo("Точек оценено: %d из %d" % (nvalid, len(xd)))
        feedback.pushInfo("ME (смещение):   %+.4g   (ближе к 0 - лучше)" % me)
        feedback.pushInfo("MAE:             %.4g" % mae)
        feedback.pushInfo("RMSE:            %.4g   (меньше - лучше)" % rmse)
        feedback.pushInfo("MSDR:            %.3f   (ближе к 1 - лучше)" % msdr)
        feedback.pushInfo("R (оценка/факт): %.3f" % r)
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
            aliases[idname] = "Номер скважины"
        fields.append(QgsField(valname, QVariant.Double))
        aliases[valname] = "Факт (%s)" % zfield
        fields.append(QgsField("z_est", QVariant.Double))
        aliases["z_est"] = "Оценка кригинга (LOO)"
        fields.append(QgsField("error", QVariant.Double))
        aliases["error"] = "Ошибка (оценка − факт)"
        fields.append(QgsField("abs_error", QVariant.Double))
        aliases["abs_error"] = "|Ошибка|"
        fields.append(QgsField("std_resid", QVariant.Double))
        aliases["std_resid"] = "Станд. остаток (со знаком)"
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
                         "Остатки CV %s · %s" % (zfield, _short(src)))
        _set_field_aliases(context, dest, aliases)

        # HTML-отчёт (интерактивный график + метрики)
        html_path = self.parameterAsFileOutput(
            parameters, self.OUTPUT_HTML, context)
        results = {self.OUTPUT: dest}
        if html_path:
            metrics = [
                ("ME (смещение)", "%+.4g" % me, "ближе к 0 - лучше"),
                ("MAE", "%.4g" % mae, "средняя |ошибка|"),
                ("RMSE", "%.4g" % rmse, "меньше - лучше"),
                ("MSDR", "%.3f" % msdr, "ближе к 1 - лучше"),
                ("R (оценка/факт)", "%.3f" % r, "корреляция"),
                ("Дисперсия данных", "%.4g" % dvar,
                 "суммарный силл (C0 + вклады C) ≈ дисперсии данных"),
                ("Точек оценено", "%d" % nvalid, "из %d" % len(xd)),
            ]
            title = "Кросс-валидация %s · %s" % (zfield, _short(src))
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
                feedback.pushInfo("Не удалось записать HTML-отчёт: %s" % e)
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
    SEED = "SEED"
    OUTPUT = "OUTPUT"

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def name(self):
        return "examplewells"

    def displayName(self):
        return self.tr("5. Создать пример скважин (демо)")

    def group(self):
        return self.tr("Грид и изолинии")

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
            "разделе «Дополнительно»."))

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
                (self.ROOF_MIN, "Кровля: минимум, м (абс.)", -250.0),
                (self.ROOF_MAX, "Кровля: максимум, м (абс.)", -50.0),
                (self.THICK_MIN, "Мощность: минимум, м", 1.0),
                (self.THICK_MAX, "Мощность: максимум, м", 8.0)):
            p = QgsProcessingParameterNumber(
                key, self.tr(label), QgsProcessingParameterNumber.Double,
                defaultValue=dv)
            _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterNumber(
            self.NUGGET_FRAC, self.tr("Доля наггета (от дисперсии)"),
            QgsProcessingParameterNumber.Double, defaultValue=0.35,
            minValue=0.0, maxValue=0.7)
        _advanced(p); self.addParameter(p)
        p = QgsProcessingParameterNumber(
            self.SEED, self.tr("Зерно ГСЧ (0 = случайно)"),
            QgsProcessingParameterNumber.Integer, defaultValue=0, minValue=0)
        _advanced(p); self.addParameter(p)
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, self.tr("Скважины (демо)"),
            type=QgsProcessing.TypeVectorPoint))

    def processAlgorithm(self, parameters, context, feedback):
        feedback.pushInfo(_version_line())
        crs = self.parameterAsExtentCrs(parameters, self.EXTENT, context)
        rect = self.parameterAsExtent(parameters, self.EXTENT, context, crs)
        if rect.isEmpty() or rect.width() <= 0 or rect.height() <= 0:
            raise QgsProcessingException("Не задана корректная область (экстент).")
        n = self.parameterAsInt(parameters, self.N_POINTS, context)
        vmin = self.parameterAsDouble(parameters, self.VMIN, context)
        vmax = self.parameterAsDouble(parameters, self.VMAX, context)
        if vmax <= vmin:
            raise QgsProcessingException("Максимум значения должен быть больше минимума.")
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

        fields = QgsFields()
        fields.append(QgsField("well", QVariant.String))
        fields.append(QgsField("roof", QVariant.Double))
        fields.append(QgsField("thick", QVariant.Double))
        fields.append(QgsField("X", QVariant.Double))
        sink, dest = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields,
            QgsWkbTypes.Point, crs)
        for i in range(n):
            f = QgsFeature(fields)
            f.setGeometry(QgsGeometry.fromPointXY(
                QgsPointXY(float(xs[i]), float(ys[i]))))
            f.setAttributes(["SK-%04d" % (i + 1), float(roof[i]),
                             float(thick[i]), float(valsX[i])])
            sink.addFeature(f)

        rng_m = 2.0 * smooth * max(xmax - xmin, ymax - ymin)
        var = float(np.var(valsX))
        feedback.pushInfo(
            "Сгенерировано скважин: %d. Поля: кровля (roof), мощность (thick), "
            "содержание X. Дисперсия X ≈ %.4g." % (n, var))
        feedback.pushInfo(
            "Стартовая вариограмма для X (кригинг/кросс-валидация): суммарный "
            "силл ≈ %.4g, наггет C0 ≈ %.4g, радиус ≈ %.4g (в единицах "
            "координат). Уточните наггет по кросс-валидации до MSDR ≈ 1." %
            (var, nug * var, rng_m))
        _set_output_name(context, dest, "Скважины (демо)")
        # псевдонимы полей на демо-слое не ставим: этот слой создан, чтобы
        # подавать его в кригинг/кросс-валидацию, а псевдонимы на временном
        # слое вызывают предупреждения «не совместимы с временными слоями»
        # при дальнейшей обработке. Имена полей (well, roof, thick, X) понятны.
        feedback.setProgress(100)
        return {self.OUTPUT: dest}


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
            options=MODEL_LABELS, defaultValue=_dv(alg, _sk(i, "MODEL"), 0))))
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
        return ["Точек экспериментальной вариограммы мало для подбора. "
                "Увеличьте число лагов или максимальное расстояние."]
    name = MODEL_LABELS[fit["model"]].lower()
    total = fit["nugget"] + fit["sill"]
    tips.append("Рекомендация: модель %s, наггет C0=%.4g, вклад C=%.4g "
                "(сумма %.4g), радиус a=%.4g. Качество подгонки R²=%.3f."
                % (name, fit["nugget"], fit["sill"], total,
                   fit["range"], fit["r2"]))
    # радиус у края окна: модель не вышла на плато, порог экстраполирован
    edge = bool(maxlag and maxlag > 0 and fit["range"] >= 0.9 * maxlag)
    if data_var and data_var > 0:
        rel = total / data_var
        if rel < 0.6:
            msg = ("Суммарный порог заметно ниже дисперсии данных (%.4g): "
                   "вариограмма не вышла на плато - увеличьте максимальное "
                   "расстояние, возможен тренд или вторая структура." % data_var)
            if edge:
                msg += (" Радиус подбора (%.4g) достигает края окна (%.4g), "
                        "это подтверждает: кривая ещё растёт."
                        % (fit["range"], maxlag))
            tips.append(msg)
        elif rel > 1.6:
            tips.append("Суммарный порог заметно выше дисперсии данных (%.4g) - "
                        "окно, вероятно, перешагивает тренд или безрудную зону. "
                        "Уменьшите максимальное расстояние до локального "
                        "масштаба и проверьте выбросы." % data_var)
        else:
            msg = ("Суммарный порог близок к дисперсии данных (%.4g) - "
                   "масштаб правдоподобен." % data_var)
            if edge:
                msg += (" Радиус подбора (%.4g) у края окна (%.4g) - считайте "
                        "его нижней оценкой, при сомнении увеличьте окно и "
                        "проверьте, стабилизируется ли радиус."
                        % (fit["range"], maxlag))
            tips.append(msg)
    elif edge:
        tips.append("Радиус подбора (%.4g) достигает края окна (%.4g) - "
                    "вариограмма не вышла на плато, радиус считайте нижней "
                    "оценкой." % (fit["range"], maxlag))
    if fit["model"] == MODEL_GAUSSIAN and fit["nugget"] < 0.05 * (total or 1.0):
        tips.append("Гауссова модель с почти нулевым наггетом численно "
                    "неустойчива (кригинг даёт «бычьи глаза», MSDR "
                    "разваливается). Задайте небольшой наггет C0.")
    tips.append("Сохраните модель в профиль (поле «Сохранить профиль под "
                "именем»), проверьте «Кросс-валидацией» и подставьте профиль "
                "в «2D Kriging».")
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
        "<div style='background:#f5f5f7;border:1px solid #ddd;"
        "padding:8px 14px;border-radius:6px;display:inline-block'>"
        "<b>Сводка</b><div style='margin-top:6px'>%s</div></div>"
        % _meta_table(meta))
    advice_html = ""
    if advice:
        items = "".join("<li>%s</li>" % a for a in advice)
        advice_html = (
            "<div style='background:#f3f7f4;border:1px solid #cde0d6;"
            "padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'>"
            "<b>Рекомендации</b><ul style='margin:6px 0'>%s</ul></div>" % items)

    chart = ""
    try:
        import plotly.graph_objects as go
        fig = go.Figure()
        if cloud is not None and len(cloud[0]):
            fig.add_trace(go.Scattergl(
                x=cloud[0], y=cloud[1], mode="markers",
                marker=dict(size=3, color="#bbbbbb", opacity=0.25),
                name="облако пар", hoverinfo="skip"))
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
                               ("<br>пар %{customdata}" if npairs is not None
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
                          annotation_text="дисперсия данных",
                          annotation_position="right")
        fig.update_xaxes(title_text="расстояние h", rangemode="tozero")
        fig.update_yaxes(title_text="полудисперсия γ(h)", rangemode="tozero")
        fig.update_layout(height=560, legend=dict(orientation="h"),
                          margin=dict(l=60, r=20, t=30, b=50))
        chart = fig.to_html(full_html=False, include_plotlyjs=True)
    except Exception as e:
        if feedback is not None:
            feedback.pushInfo("plotly недоступен (%s) - отчёт без графика." % e)
        head = ("<tr><th align='left'>серия</th><th>h</th><th>γ(h)</th>"
                "<th>пар</th></tr>")
        body = ""
        for s in series:
            np_ = s.get("npairs")
            for i in range(len(s["lag"])):
                body += ("<tr><td>%s</td><td style='text-align:right'>%.4g</td>"
                         "<td style='text-align:right'>%.4g</td>"
                         "<td style='text-align:right'>%s</td></tr>" % (
                             s["label"], s["lag"][i], s["gamma"][i],
                             (np_[i] if np_ is not None else "")))
        chart = ("<p><i>Интерактивный график недоступен (нет plotly). "
                 "Значения экспериментальной вариограммы:</i></p>"
                 "<table border='1' cellpadding='4' "
                 "style='border-collapse:collapse'>%s%s</table>" % (head, body))

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

    FIT_LABELS = ["Авто (лучшая по R²)", "Сферическая",
                  "Экспоненциальная", "Гауссова"]

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def name(self): return "experimental_variogram"

    def displayName(self):
        return self.tr("3. Вариограмма (экспериментальная)")

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
            options=self.FIT_LABELS, defaultValue=_dv(self, self.FIT_MODEL, 0))))
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
        data_var = float(np.var(vs))
        feedback.pushInfo("Точек: %d. Дисперсия данных: %.4g (ориентир для "
                          "суммарного порога)." % (len(xs), data_var))
        # порог размера группы: % от выборки, но не меньше 30 точек
        group_min = max(int(round(min_group_pct / 100.0 * len(xs))), 30)

        ev = experimental_variogram(xs, ys, vs, n_lags=n_lags, maxlag=maxlag,
                                     robust=robust,
                                     cloud_max=(20000 if show_cloud else 0))
        if ev["subsampled"]:
            feedback.pushInfo("Точек много - для расчёта пар использована "
                              "случайная подвыборка %d точек." % ev["n_used"])
        if maxlag and maxlag > 0:
            W = float(xs.max() - xs.min()); H = float(ys.max() - ys.min())
            spacing = (W * H / max(len(xs), 1)) ** 0.5 if W > 0 and H > 0 else 0.0
            if spacing > 0 and maxlag < spacing:
                feedback.pushWarning(
                    "Максимальное расстояние (%.4g) меньше типичного шага между "
                    "точками (~%.4g) - пар почти нет. Значение задаётся в "
                    "единицах слоя (обычно метры)." % (maxlag, spacing))
        series = [{"label": "все точки", "lag": ev["lag"], "gamma": ev["gamma"],
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
                feedback.pushInfo("Не удалось перечислить группы: %s" % e)
                vals = []
            if len(vals) > 12:
                feedback.pushWarning("Групп больше 12 - группировка пропущена.")
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
                    feedback.pushInfo("Группы меньше %d точек пропущены: %s."
                                      % (group_min, txt))
            else:
                feedback.pushInfo("В поле группировки меньше 2 значений - "
                                  "строю только общую кривую.")

        # подбор модели по общей кривой (рекомендация)
        fit = None
        if do_fit:
            model_arg = "auto" if fit_choice == 0 else (fit_choice - 1)
            fit = fit_variogram(ev["lag"], ev["gamma"], ev["npairs"],
                                model=model_arg)
            if fit:
                feedback.pushInfo(
                    "Подбор: модель %s, C0=%.4g, C=%.4g, a=%.4g, R²=%.3f" % (
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
                            "Профиль «%s» сохранён: изотропная модель из "
                            "автоподбора + текущий отсев. Анизотропию можно "
                            "задать в кросс-валидации или инструменте "
                            "«Профили»." % pname.strip())

        # наложение заданной модели
        model_curves = None
        if show_model:
            w = float(xs.max() - xs.min()); h = float(ys.max() - ys.min())
            auto_range = max(w, h) / 3.0 or 1.0
            nugget = self.parameterAsDouble(parameters, self.NUGGET, context)
            vg = _build_variogram(self, parameters, context, nugget, auto_range,
                                  feedback)
            mc = model_curve(vg, ev["maxlag"])
            model_curves = [{"label": "заданная модель", "h": mc[0],
                             "gamma": mc[1], "color": "#cc3333"}]
            if len(mc) == 3:
                model_curves.append({"label": "модель (малая ось)", "h": mc[0],
                                     "gamma": mc[2], "color": "#cc3333",
                                     "dash": "dot"})
        # кривая подобранной модели
        if fit:
            vgf = Variogram(fit["nugget"], [{
                "it": fit["model"] + 1, "cc": fit["sill"], "aa": fit["range"],
                "ang": 0.0, "anis": 1.0}])
            hf, gf = model_curve(vgf, ev["maxlag"])
            mc = {"label": "подобранная модель", "h": hf, "gamma": gf,
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
            _emit("все точки", ev["lag"], ev["gamma"], ev["npairs"])
            for s in series[1:]:
                _emit(s["label"], s["lag"], s["gamma"], s["npairs"])
            _set_output_name(context, dest,
                             "Вариограмма %s · %s" % (zfield, _short(src)))
            results[self.OUTPUT] = dest

        # HTML-отчёт
        html_path = self.parameterAsFileOutput(parameters, self.OUTPUT_HTML,
                                               context)
        if html_path:
            meta = [("Поле Z", zfield), ("Точек", "%d" % len(xs)),
                    ("Дисперсия данных", "%.4g" % data_var),
                    ("Число лагов", "%d" % n_lags),
                    ("Максимальное расстояние", "%.4g" % ev["maxlag"]),
                    ("Оценка", "Кресси-Хокинса" if robust else "Матерона")]
            if ev["subsampled"]:
                meta.append(("Подвыборка точек", "%d" % ev["n_used"]))
            title = "Вариограмма %s · %s" % (zfield, _short(src))
            cloud = ((ev["cloud_h"], ev["cloud_g"]) if show_cloud and
                     ev["cloud_h"].size else None)
            try:
                _write_variogram_report(html_path, title, series, data_var, fit,
                                        model_curves, advice, meta, cloud, feedback)
                results[self.OUTPUT_HTML] = html_path
            except Exception as e:
                feedback.pushInfo("Не удалось записать HTML-отчёт: %s" % e)

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
    ACTION_LABELS = ["Показать список", "Сохранить вручную (по полям ниже)",
                     "Удалить выбранный", "Очистить все"]

    def tr(self, s): return _tr(s)

    def helpUrl(self): return _help_url()

    def createInstance(self): return ProfilesAlgorithm()

    def name(self): return "profiles"

    def displayName(self): return self.tr("6. Профили обработки")

    def group(self): return GROUP

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
            options=self.ACTION_LABELS, defaultValue=0))
        self.addParameter(_profile_enum(
            self.PROFILE, "Профиль (для удаления / просмотра)", pick=True))
        self.addParameter(QgsProcessingParameterString(
            self.NAME, self.tr("Имя профиля (для «Сохранить вручную»)"),
            optional=True))
        self.addParameter(_advanced(QgsProcessingParameterNumber(
            self.NUGGET, self.tr("Модель: наггет C0"),
            QgsProcessingParameterNumber.Double, defaultValue=0.0, minValue=0.0)))
        self.addParameter(_advanced(QgsProcessingParameterEnum(
            self.MODEL, self.tr("Модель: тип"),
            options=MODEL_LABELS, defaultValue=0)))
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
                feedback.pushInfo("Сохранённых профилей нет.")
            else:
                feedback.pushInfo("Сохранённые профили (%d):" % len(profs))
                for nm in sorted(profs):
                    feedback.pushInfo("  - %s: %s" % (nm, _profile_summary(profs[nm])))
        elif action == 1:
            nm = (self.parameterAsString(parameters, self.NAME, context) or "").strip()
            if not nm:
                raise QgsProcessingException(
                    "Для сохранения укажите «Имя профиля».")
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
            feedback.pushInfo("Профиль «%s» сохранён: %s" % (nm, _profile_summary(prof)))
        elif action == 2:
            idx = self.parameterAsEnum(parameters, self.PROFILE, context)
            opts = [PROFILE_NONE] + _profile_names()
            if idx <= 0 or idx >= len(opts):
                raise QgsProcessingException(
                    "Выберите профиль для удаления в поле «Профиль».")
            nm = opts[idx]
            _delete_profile(nm)
            feedback.pushInfo("Профиль «%s» удалён." % nm)
            rest = _profile_names()
            feedback.pushInfo("Осталось профилей: %d%s" % (
                len(rest), (" - " + ", ".join(rest)) if rest else ""))
        elif action == 3:
            n = len(_load_profiles())
            _clear_profiles()
            feedback.pushInfo("Удалены все профили (%d)." % n)
        return {}


ALGORITHMS = [
    Kriging2DAlgorithm,
    RasterToIsolinesAlgorithm,
    ExperimentalVariogramAlgorithm,
    CrossValidationAlgorithm,
    ExampleWellsAlgorithm,
    ProfilesAlgorithm,
]
