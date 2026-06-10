# -*- coding: utf-8 -*-
#
# Isoliner — грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Это свободная программа: вы можете распространять её и/или изменять на
# условиях Стандартной общественной лицензии GNU (GNU GPL), опубликованной
# Фондом свободного ПО (FSF), — либо версии 2 Лицензии, либо (на ваше
# усмотрение) любой более поздней версии.
#
# Программа распространяется в надежде на полезность, но БЕЗ КАКИХ-ЛИБО
# ГАРАНТИЙ, в том числе без подразумеваемой гарантии ТОВАРНОГО СОСТОЯНИЯ или
# ПРИГОДНОСТИ ДЛЯ ОПРЕДЕЛЁННОЙ ЦЕЛИ. Подробнее см. GNU GPL.
#
# Полный текст лицензии — в файле LICENSE (на английском, юридически значим).
"""
Группа команд «Грид и изолинии» (порт инструмента Isoliner в QGIS).

Алгоритмы:
  Kriging2DAlgorithm        — точки → растр (ординарный/простой кригинг, KB2D)
  RasterToIsolinesAlgorithm — растр → изолинии (линии) и опционально полигоны

Вариограмма — нуггет + до NSTRUCT вложенных структур (как в исходном Isoliner, AddPar).
Структура с вкладом (порогом) <= 0 не учитывается (кроме первой).
"""
import math

import os
import json
import uuid

import numpy as np
from osgeo import gdal, osr

from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.core import (
    QgsProcessing,
    QgsProcessingAlgorithm,
    QgsProcessingException,
    QgsProcessingUtils,
    QgsProcessingLayerPostProcessorInterface,
    QgsProject,
    QgsSettings,
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
    QgsProcessingParameterDefinition,
)

from .kb2d import Variogram, build_grid, EPS
from .isolines import (
    isolines_from_raster, isolines_and_polygons, compute_levels, DEFAULT_FIELD)

GROUP = "Грид и изолинии"
GROUP_ID = "grid_isolines"

MODEL_LABELS = ["Сферическая", "Экспоненциальная", "Гауссова", "Степенная"]
KTYPE_LABELS = ["Ординарный (OK)", "Простой (SK)"]

NSTRUCT = 3  # макс. число вложенных структур вариограммы

CREDIT = ("\n\n— — —\nРазработано при поддержке ООО «Информ++» "
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
    Двигается только слой полигонов — узел линий не трогаем никогда, поэтому
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
        # всех слоёв (через очередь событий) — так дерево стабильно
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


def _help_url():
    """file:// ссылка на руководство в комплекте плагина (для кнопки «Справка»)."""
    p = os.path.join(os.path.dirname(__file__), "doc", "Isoliner.pdf")
    return QUrl.fromLocalFile(p).toString() if os.path.exists(p) else ""


# --- запоминание введённых значений (в сессии и между запусками) ----------
# не запоминаем источники данных и выходы; ZFIELD (поле Z) запоминаем — удобно
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


# ключи параметров структуры вариограммы
def _sk(i, suffix):
    return "S%d_%s" % (i, suffix)


# ---------------------------------------------------------------------------
#  Параметры вариограммы/поиска — общий набор для кригинга
# ---------------------------------------------------------------------------
def _add_kriging_params(alg):
    alg.addParameter(QgsProcessingParameterEnum(
        alg.KTYPE, _tr("Тип кригинга"), options=KTYPE_LABELS,
        defaultValue=_dv(alg, alg.KTYPE, 0)))

    # поиск и сетка — основные параметры
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
    try:  # живой показ размера грида; на QGIS 4 (без старого API) — обычное поле
        from .widgets import CellSizeWrapper, WRAPPER_AVAILABLE
        if WRAPPER_AVAILABLE:
            cs.setMetadata({"widget_wrapper": {"class": CellSizeWrapper}})
    except Exception:
        pass
    alg.addParameter(cs)

    alg.addParameter(QgsProcessingParameterExtent(
        alg.EXTENT, _tr("Охват растра (по умолчанию — по слою)"),
        optional=True))

    # обрезка экстраполяции — опция
    alg.addParameter(QgsProcessingParameterBoolean(
        alg.CLIP_HULL, _tr("Обрезать по контуру скважин (выпуклая оболочка)"),
        defaultValue=_dv(alg, alg.CLIP_HULL, False)))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.HULL_BUFFER, _tr("Буфер оболочки, ед. карты"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.HULL_BUFFER, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterVectorLayer(
        alg.MASK, _tr("Маска обрезки (полигон из проекта) — приоритетнее оболочки"),
        types=[QgsProcessing.TypeVectorPolygon], optional=True))

    # вариограмма — дополнительные параметры
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.SKMEAN, _tr("Среднее для простого кригинга"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.SKMEAN, 0.0))))
    alg.addParameter(_advanced(QgsProcessingParameterNumber(
        alg.NUGGET, _tr("Наггет C0"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.NUGGET, 0.0), minValue=0.0)))

    # структуры вариограммы (все — дополнительные)
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


def _read_points(source, zfield, feedback=None):
    idx = source.fields().lookupField(zfield)
    xs, ys, vs = [], [], []
    for f in source.getFeatures():
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        v = f[idx]
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        p = g.asPoint()
        xs.append(p.x()); ys.append(p.y()); vs.append(v)
    if len(xs) < 2:
        raise QgsProcessingException(
            "Недостаточно валидных точек с числовым значением.")

    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    vs = np.asarray(vs, float)

    # схлопывание совпадающих точек (один XY = несколько проб) -> среднее Z.
    # без этого матрица кригинга вырождается: дыры NoData и «разлёт» ±1e15.
    # допуск задаём относительно охвата (а не фиксированными мм/градусами) —
    # так сливаются только практически совпадающие точки в любой системе
    # координат (метры/градусы/футы), без риска «склеить» разные скважины
    span = max(float(xs.max() - xs.min()), float(ys.max() - ys.min()), 1e-9)
    tol = span * 1e-7
    key = np.round(np.column_stack([xs, ys]) / tol) * tol
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    if len(uniq) < len(xs):
        sums = np.zeros(len(uniq)); cnts = np.zeros(len(uniq))
        np.add.at(sums, inv, vs)
        np.add.at(cnts, inv, 1.0)
        vs = sums / cnts
        xs = uniq[:, 0]
        ys = uniq[:, 1]
        if feedback is not None:
            feedback.pushInfo(
                "Совпадающих точек усреднено: %d (осталось %d)" %
                (len(inv) - len(uniq), len(uniq)))
    if len(xs) < 2:
        raise QgsProcessingException(
            "После усреднения совпадающих точек осталось < 2 узлов.")
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
            # для степенной модели «радиус a» — это показатель степени ω
            # (0<ω<2), а НЕ радиус: авто = max(охват)/3 здесь даст переполнение
            if rng <= 0:
                rng = 1.0
                if feedback:
                    feedback.pushWarning(
                        "Структура %d: степенная модель — поле «радиус a» это "
                        "показатель ω (0<ω<2), а не радиус; задан 0, взят ω=1." % i)
            elif not (0.0 < rng < 2.0):
                if feedback:
                    feedback.pushWarning(
                        "Структура %d: показатель степенной модели ω=%.3g вне "
                        "(0; 2) — приведён к диапазону." % (i, rng))
                rng = min(max(rng, 0.05), 1.999)
        elif rng <= 0:
            rng = auto_range
        az = alg.parameterAsDouble(parameters, _sk(i, "AZIMUTH"), context)
        anis = alg.parameterAsDouble(parameters, _sk(i, "ANIS"), context)
        sill_total += max(sill, 0.0)
        structures.append({"it": model, "cc": max(sill, 0.0),
                           "aa": rng, "ang": az, "anis": anis})
    if not structures:  # первая структура с нулевым порогом — оставим как чистый эффект
        structures.append({"it": 1, "cc": 0.0, "aa": auto_range,
                           "ang": 0.0, "anis": 1.0})
    if feedback and nugget > 0 and sill_total <= 0:
        feedback.pushWarning(
            "Задан только наггет (структурный вклад C = 0): кригинг выродится "
            "в локальное среднее, поверхность будет почти плоской.")
    return Variogram(nugget, structures)


def _run_kriging_to_tiff(alg, parameters, context, feedback, source, zfield,
                         out_path, mask_layer=None):
    """Считывает параметры кригинга, строит грид, пишет GeoTIFF. -> (path, nodata).

    mask_layer (опц.) — полигональный слой/путь для обрезки экстраполяции.
    """
    ktype = 1 if alg.parameterAsEnum(parameters, alg.KTYPE, context) == 0 else 0
    skmean = alg.parameterAsDouble(parameters, alg.SKMEAN, context)
    nugget = alg.parameterAsDouble(parameters, alg.NUGGET, context)
    radius = alg.parameterAsDouble(parameters, alg.RADIUS, context)
    ndmin = alg.parameterAsInt(parameters, alg.MIN_POINTS, context)
    ndmax = alg.parameterAsInt(parameters, alg.MAX_POINTS, context)
    cell = alg.parameterAsDouble(parameters, alg.CELL_SIZE, context)

    xd, yd, vrd = _read_points(source, zfield, feedback)

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
    feedback.pushInfo("Сетка %d x %d, ячейка %.4g, точек %d, структур %d" %
                      (nx, ny, cell, len(xd), vg.nst))

    def prog(done, total):
        if feedback.isCanceled():
            raise QgsProcessingException("Прервано пользователем.")
        feedback.setProgress(int(80.0 * done / total))

    grid = build_grid(xd, yd, vrd, vg, ktype, skmean, ndmin, ndmax,
                      rad2, nodata, xmn, ymn, cell, nx, ny, progress=prog)

    # путь записи: при обрезке пишем во временный, затем клипуем в out_path
    write_path = out_path
    if mask_layer is not None:
        write_path = os.path.join(QgsProcessingUtils.tempFolder(),
                                  "krig_%s.tif" % uuid.uuid4().hex)

    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(write_path, nx, ny, 1, gdal.GDT_Float32,
                       options=["COMPRESS=LZW", "TILED=YES"])
    ds.SetGeoTransform((xmin, cell, 0.0, ymin + ny * cell, 0.0, -cell))
    crs = source.sourceCrs()
    if crs is not None and crs.isValid():
        srs = osr.SpatialReference()
        srs.ImportFromWkt(crs.toWkt())
        ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(nodata)
    band.WriteArray(grid)
    band.FlushCache()
    ds = None
    feedback.setProgress(83)

    if mask_layer is not None:
        from qgis import processing
        feedback.pushInfo("Обрезка по маске…")
        processing.run("gdal:cliprasterbymasklayer", {
            "INPUT": write_path,
            "MASK": mask_layer,
            "NODATA": nodata,
            "CROP_TO_CUTLINE": False,
            "KEEP_RESOLUTION": True,
            "OUTPUT": out_path,
        }, context=context, feedback=feedback, is_child_algorithm=True)
    feedback.setProgress(85)
    return out_path, nodata


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
#  Параметры построения изолиний — общий набор
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
        alg.LEVELS, _tr("Явные уровни (через пробел) — приоритетнее шага"),
        defaultValue=_dv(alg, alg.LEVELS, ""), optional=True))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.INDEX_EVERY, _tr("Главная изолиния каждая N-я (0 = выкл.)"),
        QgsProcessingParameterNumber.Integer,
        defaultValue=_dv(alg, alg.INDEX_EVERY, 5), minValue=0))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.MIN_LENGTH, _tr("Мин. длина линии, ед. карты (0 = без фильтра)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.MIN_LENGTH, 0.0), minValue=0.0))
    alg.addParameter(QgsProcessingParameterBoolean(
        alg.SMOOTH, _tr("Сглаживать"), defaultValue=_dv(alg, alg.SMOOTH, True)))
    alg.addParameter(QgsProcessingParameterNumber(
        alg.SMOOTH_RADIUS, _tr("Радиус сглаживания поля, ячеек (0 = без него)"),
        QgsProcessingParameterNumber.Double,
        defaultValue=_dv(alg, alg.SMOOTH_RADIUS, 1.0),
        minValue=0.0, maxValue=10.0))
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

    def tr(self, s): return _tr(s)
    def createInstance(self): return Kriging2DAlgorithm()
    def name(self): return "kriging2d"
    def displayName(self): return self.tr("2D Kriging (точки → растр)")

    def helpUrl(self): return _help_url()
    def group(self): return GROUP
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return self.tr(
            "Ординарный/простой кригинг 2D по точечному слою (порт инструмента "
            "Isoliner, GSLIB KB2D). Вариограмма: наггет + до %d вложенных структур. "
            "Подходит для отметок пласта, мощностей, ФМС, химии и любых "
            "числовых атрибутов.\n\nРадиус поиска 0 = по всей выборке; "
            "размер ячейки 0 = min(охват)/50; радиус корреляции 0 = "
            "max(охват)/3. Опция обрезки убирает экстраполяцию вне контура "
            "скважин." % NSTRUCT + CREDIT)

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
        self.addParameter(QgsProcessingParameterRasterDestination(
            self.OUTPUT, self.tr("Растр кригинга")))

    def processAlgorithm(self, parameters, context, feedback):
        _save_values(self, parameters)
        source = self.parameterAsSource(parameters, self.INPUT, context)
        zfield = self.parameterAsString(parameters, self.ZFIELD, context)
        out_path = self.parameterAsOutputLayer(parameters, self.OUTPUT, context)
        layer = self.parameterAsVectorLayer(parameters, self.INPUT, context)
        src = layer.name() if layer is not None else "data"
        mask = _build_mask(self, parameters, context, feedback, layer)
        path, _ = _run_kriging_to_tiff(self, parameters, context, feedback,
                                       source, zfield, out_path, mask)
        _set_output_name(context, path,
                         "Кригинг %s · %s" % (zfield, _short(src)))
        feedback.setProgress(100)
        return {self.OUTPUT: path}


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
    def displayName(self): return self.tr("Изолинии из растра")

    def helpUrl(self): return _help_url()
    def group(self): return GROUP
    def groupId(self): return GROUP_ID

    def shortHelpString(self):
        return self.tr(
            "Строит изолинии из растра: равномерный шаг или явные уровни "
            "(через пробел), главные (утолщённые) изолинии флагом is_index, "
            "фильтр коротких линий.\n\nСглаживание: поле (растр) сглаживается "
            "гауссовым фильтром (радиус в ячейках), затем линии слегка "
            "скругляются (Chaikin). Изолинии плавны и не пересекаются даже в "
            "густых местах; скругление убирает «октагоны» от грубого грида.\n\n"
            "По умолчанию строит и "
            "контурные полигоны (пояса между изолиниями) во временный слой — их "
            "границы СОВПАДАЮТ с изолиниями, покрытие сплошное. Чтобы их не "
            "строить — очистите поле «Контурные полигоны».\n\nПоля: линии — "
            "значение уровня (по умолчанию ELEV) и is_index (1 у главных); "
            "полигоны — ELEV_MIN/ELEV_MAX (диапазон пояса)." + CREDIT)

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
        smooth = self.parameterAsBool(parameters, self.SMOOTH, context)
        sm_rad = self.parameterAsDouble(parameters, self.SMOOTH_RADIUS, context)
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
                min_len, smooth, sm_rad, sm_line, field_name, True, nodata,
                out_dest, poly_dest, context, feedback)
            out, poly = res["lines"], res["polygons"]
            _set_output_name(context, out, "Изолинии · %s" % name)
            _set_output_name(context, poly, "Полигоны · %s" % name)
            _order_lines_above(context, out, poly)   # изолинии над полигонами
            results = {self.OUTPUT: out, self.OUTPUT_POLYGONS: poly}
        else:
            out = isolines_from_raster(
                rl.source(), band, interval, base, levels, index_every,
                min_len, smooth, sm_rad, sm_line, field_name, True, nodata,
                out_dest, context, feedback)
            _set_output_name(context, out, "Изолинии · %s" % name)
            results = {self.OUTPUT: out}

        feedback.setProgress(100)
        return results


ALGORITHMS = [
    Kriging2DAlgorithm,
    RasterToIsolinesAlgorithm,
]
