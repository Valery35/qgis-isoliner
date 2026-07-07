# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Двуязычие интерфейса (RU/EN).

Простой словарный слой: исходные строки в коде - русские, при английской
локали QGIS они подменяются на английские по таблице TRANSLATIONS. Если
перевода нет, возвращается исходная (русская) строка - плагин остаётся
рабочим. Модуль не импортирует QGIS на верхнем уровне, поэтому таблицу
переводов можно проверять обычным Python (см. tests/test_i18n.py).

Язык определяется по настройкам QGIS один раз, лениво, при первом вызове tr().
Для тестов и принудительного переключения есть set_language().
"""

_LANG = None  # 'ru' | 'en' (None = ещё не определён)


def set_language(lang):
    """Принудительно задать язык ('ru'/'en'/'en_US'/...). None - сбросить."""
    global _LANG
    if lang is None:
        _LANG = None
        return
    code = str(lang).strip().lower().replace("-", "_").split("_")[0]
    _LANG = "ru" if code == "ru" else "en"


def language():
    """Текущий язык ('ru'/'en'); инициализирует по QGIS при необходимости."""
    if _LANG is None:
        init_from_qgis()
    return _LANG or "en"


def init_from_qgis():
    """Определить язык интерфейса по настройкам QGIS. По умолчанию 'en'."""
    loc = ""
    try:
        from qgis.core import QgsApplication
        loc = QgsApplication.instance().locale() or ""
    except Exception:
        loc = ""
    if not loc:
        try:
            from qgis.PyQt.QtCore import QSettings
            s = QSettings()
            override = s.value("locale/overrideFlag", False, type=bool)
            loc = s.value("locale/userLocale", "") if override else ""
        except Exception:
            loc = ""
    set_language(loc or "en")
    return _LANG


def tr(s):
    """Перевести строку s на активный язык. RU - исходник, EN - по таблице."""
    if _LANG is None:
        init_from_qgis()
    if _LANG == "en":
        return TRANSLATIONS.get(s, s)
    return s


def missing_keys(keys):
    """Какие из переданных русских строк не имеют английского перевода.

    Удобно для теста покрытия: keys - множество строк, реально обёрнутых в
    _tr()/tr() в коде (извлекается AST-обходом)."""
    return [k for k in keys if k not in TRANSLATIONS]


# --- Таблица переводов RU -> EN -------------------------------------------
# Ключ - русская строка ровно как в коде (включая %d/%s, переносы, символы).
# Значение - английский перевод. Покрывает статический интерфейс:
# имена инструментов, подписи параметров, варианты списков, панели справки,
# живые подписи виджетов. Логи и HTML-отчёты переводятся отдельным проходом.

TRANSLATIONS = {
    'Размер ячейки (0 = авто, min(охват)/50)': 'Cell size (0 = auto, min(extent)/50)',
    '1.1 2D Kriging (точки → растр)': '1.1 2D Kriging (points → raster)',
    '1.2 Изолинии из растра': '1.2 Isolines from raster',
    '1.5 Кросс-валидация вариограммы': '1.5 Variogram cross-validation',
    'Грид и изолинии': 'Grid and isolines',
    'Дополнительные инструменты': 'Additional tools',
    '1.7 Создать пример скважин (демо)': '1.7 Create sample wells (demo)',
    '1.3 Вариограмма (экспериментальная)': '1.3 Variogram (experimental)',
    '1.6 Профили обработки': '1.6 Processing profiles',
    '1.4 Вариограммная карта (анизотропия)': '1.4 Variogram map (anisotropy)',
    'Тип кригинга': 'Kriging type',
    'Радиус поиска (0 = вся выборка)': 'Search radius (0 = whole sample)',
    'Мин. количество точек': 'Min. number of points',
    'Макс. количество точек': 'Max. number of points',
    'Охват растра (по умолчанию - по слою)': 'Raster extent (default - by layer)',
    'Обрезать по контуру скважин (выпуклая оболочка)': 'Clip to well hull (convex hull)',
    'Буфер оболочки, ед. карты': 'Hull buffer, map units',
    'Маска обрезки (полигон из проекта) - приоритетнее оболочки': 'Clip mask (polygon from project) - takes priority over the hull',
    'Структура %d': 'Structure %d',
    'порог/вклад C': 'sill/contribution C',
    'порог/вклад C (0 = выкл.)': 'sill/contribution C (0 = off)',
    'Шаг изолиний (0 = задать уровни ниже)': 'Isoline step (0 = set levels below)',
    'Начальный уровень (offset)': 'Base level (offset)',
    'Явные уровни (через пробел) - приоритетнее шага': 'Explicit levels (space-separated) - take priority over step',
    'Главная изолиния каждая N-я (0 = выкл.)': 'Index isoline every N-th (0 = off)',
    'Мин. длина линии, ед. карты (0 = без фильтра)': 'Min. line length, map units (0 = no filter)',
    'Бикубическое сглаживание изолиний (сгущение грида)': 'Bicubic isoline smoothing (grid densification)',
    'Скругление линий (Chaikin), итераций (0 = выкл.)': 'Line rounding (Chaikin), iterations (0 = off)',
    'Имя поля значения': 'Value field name',
    'Стандартная ошибка кригинга': 'Kriging standard error',
    'Необязательный растр стандартной ошибки кригинга (sqrt дисперсии ошибки): мера неопределённости оценки. Мала у скважин, растёт вдали от данных.': 'Optional kriging standard-error raster (sqrt of error variance): a measure of estimate uncertainty. Small near wells, grows away from data.',
    'Скользящий контроль (leave-one-out): каждая скважина по очереди исключается, её значение предсказывается кригингом по остальным, и сравнивается с фактическим. Помогает подобрать вариограмму (наггет, радиус, модель) по ошибке, а не на глаз.\n\nВ Журнал выводятся метрики: ME (смещение, к 0), RMSE (меньше - лучше), MSDR (к 1 - вариограмма адекватна по масштабу), R. Перебирайте параметры и сравнивайте RMSE и MSDR.\n\nСлой остатков (опц.) - точки со следующими полями:\n  • <номер скважины> - если задано «Поле номера скважины»;\n  • <имя проверяемого поля> - фактическое значение (факт);\n  • z_est - оценка кригинга по остальным точкам (LOO);\n  • error - оценка минус факт (минус: занижено, плюс: завышено);\n  • abs_error - модуль ошибки;\n  • std_resid - стандартизованный остаток: error / стандартную ошибку кригинга, со знаком (это не дисперсия).\nПо нему видно, где модель промахивается.\n\nHTML-отчёт (по умолчанию) открывается в просмотрщике результатов: интерактивный график «оценка vs факт», гистограмма ошибок и таблица метрик.': 'Leave-one-out cross-validation: each well is removed in turn, its value is predicted by kriging from the rest, and compared with the actual value. Helps tune the variogram (nugget, range, model) by error rather than by eye.\n\nThe Log reports metrics: ME (bias, → 0), RMSE (lower is better), MSDR (→ 1 means the variogram is adequate in scale), R. Try different parameters and compare RMSE and MSDR.\n\nResiduals layer (opt.) - points with the following fields:\n  • <well number> - if "Well number field" is set;\n  • <validated field name> - the actual value;\n  • z_est - kriging estimate from the other points (LOO);\n  • error - estimate minus actual (minus: under, plus: over);\n  • abs_error - absolute error;\n  • std_resid - standardized residual: error / kriging standard error, signed (not a variance).\nIt shows where the model misses.\n\nThe HTML report (default) opens in the result viewer: an interactive "estimate vs actual" plot, an error histogram and a metrics table.',
    'Создаёт точечный слой «скважин» со случайными координатами в пределах области и значением абстрактного компонента (X, %), имеющим пространственную структуру. Предназначен для обучения и проверки инструментов без реальных данных.\n\nОбласть задаётся экстентом (можно по слою, по холсту карты, вручную координатами или рисованием). «Гладкость» задаёт радиус корреляции как долю охвата (больше - крупнее «пятна»). «Доля наггета» задаёт долю дисперсии, приходящуюся на короткомасштабный шум (чем больше, тем меньше предсказуемость). В Журнал выводится стартовая вариограмма - её уточняют кросс-валидацией.\n\nПоля результата: номер скважины, абсолютная отметка кровли (roof), мощность (thick) и содержание X. Диапазоны кровли и мощности по умолчанию близки к реальным калийным данным; их можно изменить в разделе «Дополнительно».\n\nНеобязательные галки добавляют поля для смежных инструментов: напор (head) для градиента потока и категориальный минтип для индикаторного кригинга. Галка K и T добавляет напор и лог-нормальные поля K (коэф. фильтрации) и T = K·мощность для «Удельного расхода (Дарси)». Включённый вывод «Поверхность дрейфа» даёт растр сторонней поверхности и поле dz, линейно с ней связанное, для кригинга с внешним дрейфом.': 'Creates a point "wells" layer with random coordinates within an area and a value of an abstract component (X, %), with a spatial structure. Intended for learning and testing the tools without real data.\n\nThe area is set by an extent (by layer, by map canvas, manually by coordinates or by drawing). "Smoothness" sets the correlation range as a fraction of the extent (larger - bigger "patches"). "Nugget fraction" sets the share of variance due to short-range noise (larger - less predictable). The Log prints the starting variogram - refine it with cross-validation.\n\nResult fields: well number, absolute roof elevation (roof), thickness (thick) and grade X. The default roof and thickness ranges are close to real potash data; you can change them under "Advanced".\n\nOptional checkboxes add fields for the neighbouring tools: head for the flow gradient and a categorical mineral type for indicator kriging. The K and T checkbox adds head and log-normal K (conductivity) and T = K·thickness fields for the \'Specific discharge (Darcy law)\' tool. Enabling the "Drift surface" output gives a raster of a secondary surface and a dz field linearly related to it, for external drift kriging.',
    'Доля наггета (от дисперсии)': 'Nugget fraction (of variance)',
    'Зерно ГСЧ (0 = случайно)': 'RNG seed (0 = random)',
    'Строит изотропную экспериментальную полувариограмму по точкам: облако пар усредняется по интервалам расстояния (лагам). Помогает увидеть структуру данных и подобрать вариограмму глазом, а не угадывать наггет/радиус.\n\nПоле группировки (необязательно): для каждого значения поля строится своя кривая - удобно сравнить совокупности разной плотности (поверхностная и подземная разведка) и проверить, общая ли у них структура.\n\nПодбор модели (по умолчанию) даёт наггет C0, вклад C, радиус a и модель. Сохраните их в профиль (поле «Сохранить профиль под именем») и подставьте в «2D Kriging». Можно наложить уже заданную модель, чтобы сравнить её с облаком.\n\nHTML-отчёт открывается в просмотрщике результатов: точки по лагам, модель и подобранная кривая, линия дисперсии данных. Слой-таблица (опц.) содержит лаг, γ(h) и количество пар для построения в QGIS.': 'Builds an isotropic experimental semivariogram from points: the pair cloud is averaged over distance intervals (lags). Helps reveal the structure of the data and set the variogram by eye, instead of guessing the nugget/range.\n\nGrouping field (optional): a separate curve is built for each field value - handy to compare populations of different density (surface vs underground survey) and check whether they share a structure.\n\nModel fitting (default) gives nugget C0, contribution C, range a and the model. Save them to a profile (the "Save profile as" field) and apply them in "2D Kriging". You can overlay an already-set model to compare it with the cloud.\n\nThe HTML report opens in the result viewer: points by lag, the model and the fitted curve, the data-variance line. The table layer (opt.) holds lag, γ(h) and the number of pairs for plotting in QGIS.',
    'Управление профилями обработки. Профиль - это именованный набор «вариограмма (Структура 1: наггет, тип, порог, радиус, азимут, оси) + отсев ураганных проб». Профили сохраняют «Вариограмма» и «Кросс-валидация», а подставляет «2D Kriging».\n\nДействие: Показать список (в Журнал), Сохранить вручную (по полям в «Дополнительно»), Удалить выбранный, Очистить все.\n\nСписки профилей в выпадающих полях обновляются при открытии окна: сохранили профиль - переоткройте инструмент, чтобы он появился.': 'Manage processing profiles. A profile is a named set of "variogram (Structure 1: nugget, type, sill, range, azimuth, axes) + outlier removal". Profiles are saved by "Variogram" and "Cross-validation", and applied by "2D Kriging".\n\nAction: Show list (to the Log), Save manually (from the fields under "Advanced"), Delete selected, Clear all.\n\nProfile drop-down lists refresh when the window opens: after saving a profile, reopen the tool so it appears.',
    'Среднее для простого кригинга': 'Mean for simple kriging',
    'Наггет C0': 'Nugget C0',
    'Ураганные пробы: перцентиль обрезки, % (0 = выкл.)': 'Outliers: clip percentile, % (0 = off)',
    'Нижняя граница значения (пусто = нет)': 'Lower value bound (empty = none)',
    'Верхняя граница значения (пусто = нет)': 'Upper value bound (empty = none)',
    'Срезать к границе (capping) вместо удаления': 'Cap to bound (capping) instead of removing',
    'Ординарный/простой кригинг 2D по точечному слою (ядро GSLIB KB2D). Вариограмма: наггет + структура (сферическая, экспоненциальная, гауссова или степенная) с азимутом и анизотропией. Подходит для отметок пласта, мощностей, ФМС, химии и любых числовых атрибутов.\n\nРадиус поиска 0 = по всей выборке; размер ячейки 0 = min(охват)/50; радиус корреляции 0 = max(охват)/3. Опция обрезки убирает экстраполяцию вне контура скважин.': 'Ordinary/simple 2D kriging over a point layer (GSLIB KB2D core). Variogram: nugget + structure (spherical, exponential, Gaussian or power) with azimuth and anisotropy. Suitable for roof elevations, thicknesses, geomechanical properties, chemistry and any numeric attribute.\n\nSearch radius 0 = whole sample; cell size 0 = min(extent)/50; correlation range 0 = max(extent)/3. The clip option removes extrapolation outside the well hull.',
    'Точечный слой': 'Point layer',
    'Поле значения (Z)': 'Value field (Z)',
    'Сгладить грид (Гаусс)': 'Smooth grid (Gaussian)',
    'Радиус сглаживания, ячеек': 'Smoothing radius, cells',
    'Снять полиномиальный тренд': 'Remove polynomial trend',
    'Степень тренда': 'Trend degree',
    '1 (плоскость)': '1 (plane)',
    '2 (квадратичная)': '2 (quadratic)',
    'Региональный тренд снимается МНК перед кригингом, кригуются остатки, тренд возвращается к оценке. Полезно для отметок пласта и мощностей с общим падением. Для химии без тренда эффекта почти нет. Степень 1 обычно достаточна, степень 2 может вобрать часть реальной структуры в тренд - следите за вариограммой остатков.': 'The regional trend is removed by least squares before kriging, the residuals are kriged, and the trend is added back to the estimate. Useful for seam marks and thicknesses with a general dip. For chemistry without a trend the effect is negligible. Degree 1 is usually enough, degree 2 can absorb part of the real structure into the trend, so watch the residual variogram.',
    'Снятие тренда отключено: точек %d, для степени %d нужно больше %d.': 'Detrending off: %d points, degree %d needs more than %d.',
    'Снят тренд степени %d: убрано %.1f%% дисперсии (s данных %.4g, s остатка %.4g). Вариограмму задавайте по остаткам, стандартная ошибка - это ошибка кригинга остатков.': 'Trend of degree %d removed: %.1f%% of variance taken out (data s %.4g, residual s %.4g). Fit the variogram on the residuals, the standard error here is the kriging error of the residuals.',
    'Снят тренд степени %d: убрано %.1f%% дисперсии. Тренд переподбирается на каждом шаге LOO по остальным точкам, вариограмму задавайте по остаткам.': 'Trend of degree %d removed: %.1f%% of variance taken out. The trend is refit at each LOO step on the remaining points, fit the variogram on the residuals.',
    'Блочный кригинг': 'Block kriging',
    'Дискретизация блока, N×N на ячейку': 'Block discretization, N×N per cell',
    'Оценивает СРЕДНЕЕ по ячейке грида, а не значение в её центре: каждая ячейка разбивается на N×N точек дискретизации, ковариации усредняются по блоку. Поверхность глаже, стандартная ошибка ниже точечной - подходит для оценки запасов и содержаний по блоку. Пробы при этом не воспроизводятся точно (среднее блока ≠ значение в точке). Выключено - обычный точечный кригинг.': 'Estimates the AVERAGE over the grid cell rather than the value at its centre: each cell is split into N×N discretization points and the covariances are averaged over the block. The surface is smoother and the standard error is lower than for point kriging - suitable for estimating reserves and grades over a block. Samples are then not reproduced exactly (the block average ≠ the value at a point). Off - ordinary point kriging.',
    'Сколько точек на сторону ячейки берётся для усреднения по блоку (всего N×N). 4×4 достаточно почти всегда; больше - точнее, но медленнее. Действует только при включённом блочном кригинге.': 'How many points per cell side are used to average over the block (N×N in total). 4×4 is almost always enough; more is more accurate but slower. Active only when block kriging is on.',
    'Блочный кригинг: дискретизация %d×%d на ячейку. Оценка - среднее по блоку, стандартная ошибка блочная (ниже точечной). Значения в узлах-пробах точно не воспроизводятся.': 'Block kriging: %d×%d discretization per cell. The estimate is the block average, the standard error is the block error (lower than point). Values at sample nodes are not reproduced exactly.',
    '2.03 Гидравлический градиент и направление потока': '2.03 Hydraulic gradient and flow direction',
    'Добавить поле напора (для градиента потока)': 'Add a head field (for flow gradient)',
    'Поле напора (head): региональный уклон + локальная вариация. Кригуйте head, затем подайте растр в «Гидравлический градиент и направление потока».': 'Head field (head): a regional slope plus local variation. Krige head, then feed the raster to "Hydraulic gradient and flow direction".',
    'Растр напора': 'Head raster',
    'Гидравлический градиент (модуль)': 'Hydraulic gradient (magnitude)',
    'Направление потока (азимут)': 'Flow direction (azimuth)',
    'Векторы потока (точки)': 'Flow vectors (points)',
    'Сглаживание напора перед расчётом, ячеек (0 = без)': 'Smooth head before computing, cells (0 = none)',
    'Векторы потока: шаг прореживания, ячеек': 'Flow vectors: thinning step, cells',
    'Не задан растр напора.': 'No head raster set.',
    'Не удалось открыть растр напора.': 'Could not open the head raster.',
    'В растре напора нет данных.': 'The head raster has no data.',
    'Растр напора %d x %d, ячейка %.4g x %.4g.': 'Head raster %d x %d, cell %.4g x %.4g.',
    'Сглаживание напора (σ=%g яч.)…': 'Smoothing the head (σ=%g cells)…',
    'Гидравлический градиент · %s': 'Hydraulic gradient · %s',
    'Направление потока · %s': 'Flow direction · %s',
    'Векторы потока · %s': 'Flow vectors · %s',
    'Векторов потока: %d (шаг %d яч.). Слой оформлен стрелками автоматически: поворот по полю «az», размер по «grad». Символику можно поменять в свойствах слоя.': 'Flow vectors: %d (step %d cells). The layer is styled as arrows automatically: rotation by the "az" field, size by "grad". You can change the symbology in the layer properties.',
    'По растру напора (пьезометрической поверхности) строит гидравлический градиент и направление потока. Вход - растр напора, например результат «2D Kriging» по уровням в скважинах.\n\nВыходы: растр модуля градиента |∇h| (безразмерный, м/м), растр азимута направления потока (компасный, 0 = север, вниз по градиенту) и точечный слой векторов потока для оформления стрелками (поля az - азимут, grad - градиент).\n\nЭто геометрия поля напора, без проницаемости: скорость фильтрации по Дарси (v = −K·∇h) требует коэффициента фильтрации K и здесь не считается. Изолинии напора стройте инструментом «Изолинии из растра».\n\nГрадиент усиливает шум грида - при пятнистом результате включите сглаживание (радиус в ячейках) или сгладьте напор в «2D Kriging».': 'From a head raster (the piezometric surface) builds the hydraulic gradient and the flow direction. The input is a head raster, for example the result of "2D Kriging" on borehole water levels.\n\nOutputs: a gradient-magnitude raster |∇h| (dimensionless, m/m), a flow-direction azimuth raster (compass, 0 = north, down-gradient) and a point layer of flow vectors to style with arrows (fields az - azimuth, grad - gradient).\n\nThis is the geometry of the head field, without permeability: the Darcy flow velocity (v = −K·∇h) needs the hydraulic conductivity K and is not computed here. Build head isolines with the "Isolines from raster" tool.\n\nThe gradient amplifies grid noise - if the result is patchy, turn on smoothing (radius in cells) or smooth the head in "2D Kriging".',
    'Растр кригинга': 'Kriging raster',
    'Строит изолинии из растра: равномерный шаг или явные уровни (через пробел), главные (утолщённые) изолинии флагом is_index, фильтр коротких линий.\n\nСкругление линий (Chaikin) слегка сглаживает контуры и убирает «октагоны» от грубого грида. Сглаживание самого поля выполняется в инструменте 2D Kriging.\n\nБикубическое сглаживание (сгущение грида ×2…×4) даёт гладкие изолинии без «октагонов» от грубой сетки - это основной способ сглаживания, сильнее скругления линий (Chaikin). Работает и для линий, и для контурных полигонов: границы поясов совпадают с изолиниями.\n\nПо умолчанию строит и контурные полигоны (пояса между изолиниями) во временный слой - их границы СОВПАДАЮТ с изолиниями, покрытие сплошное. Чтобы их не строить - очистите поле «Контурные полигоны».\n\nПоля: линии - значение уровня (по умолчанию ELEV) и is_index (1 у главных); полигоны - ELEV_MIN/ELEV_MAX (диапазон пояса).': 'Builds isolines from a raster: a uniform step or explicit levels (space-separated), index (thicker) isolines via the is_index flag, a short-line filter.\n\nLine rounding (Chaikin) lightly smooths the contours and removes "octagons" from a coarse grid. Smoothing of the field itself is done in the 2D Kriging tool.\n\nBicubic smoothing (grid densification ×2…×4) gives smooth isolines without "octagons" from a coarse grid - it is the main smoothing method, stronger than line rounding (Chaikin). It works for both lines and contour polygons: band boundaries coincide with the isolines.\n\nBy default it also builds contour polygons (bands between isolines) into a temporary layer - their boundaries COINCIDE with the isolines, coverage is continuous. To skip them - clear the "Contour polygons" field.\n\nFields: lines - the level value (default ELEV) and is_index (1 on index lines); polygons - ELEV_MIN/ELEV_MAX (band range).',
    'Растр': 'Raster',
    'Изолинии (линии)': 'Isolines (lines)',
    'Контурные полигоны': 'Contour polygons',
    'Не задан растр.': 'No raster specified.',
    'Точки со значениями': 'Points with values',
    'Поле значения Z': 'Z value field',
    'Поле номера скважины (необязательно)': 'Well number field (optional)',
    'Сохранить профиль под именем (пусто = не сохранять)': 'Save profile as (empty = do not save)',
    'Слой остатков (точки)': 'Residuals layer (points)',
    'Отчёт о кросс-валидации (HTML)': 'Cross-validation report (HTML)',
    'HTML files (*.html)': 'HTML files (*.html)',
    'Область (экстент)': 'Area (extent)',
    'Количество скважин': 'Number of wells',
    'Минимум значения X': 'Minimum of value X',
    'Максимум значения X': 'Maximum of value X',
    'Гладкость (доля охвата)': 'Smoothness (fraction of extent)',
    'Скважины (демо)': 'Wells (demo)',
    'Модель: наггет C0': 'Model: nugget C0',
    'Поле группировки (необязательно, напр. вид разведки)': 'Grouping field (optional, e.g. survey type)',
    'Количество лагов': 'Number of lags',
    'Максимальное расстояние, в единицах слоя (0 = пол-диагонали)': 'Maximum distance, in layer units (0 = half-diagonal)',
    'Подобрать модель (рекомендация)': 'Fit model (recommendation)',
    'Таблица вариограммы (лаг, γ, количество пар)': 'Variogram table (lag, γ, number of pairs)',
    'Отчёт (HTML)': 'Report (HTML)',
    'Действие': 'Action',
    'Имя профиля (для «Сохранить вручную»)': 'Profile name (for "Save manually")',
    'Строит вариограммную карту - поверхность γ(h_x, h_y): для всех пар берётся вектор разноса (dx, dy) и полудисперсия 0.5·(Δz)², значения усредняются по 2D-сетке лагов. Анизотропия видна как эллипс: направление, вдоль которого γ растёт медленнее (длиннее радиус), - ось максимальной непрерывности (для складчатости - простирание).\n\nВ Журнал и в HTML-отчёт выводятся оценки: азимут главной оси (геогр., 0=С, по часовой), коэффициент анизотропии (малая/главная) и радиус. Их можно подставить в структуру вариограммы «2D Kriging» (азимут, анизотропия, радиус a) - это и есть учёт анизотропии в кригинге. Оценка индикативная: уточняйте по самому хитмапу.\n\nЕсли структура близка к изотропной или радиус меньше ячейки - анизотропия не оценивается (помечается «не выражена»).\n\nОпц. растр поверхности (в координатах лага, начало в 0,0) - для тех, кто хочет видеть карту на холсте.': 'Builds a variogram map - the γ(h_x, h_y) surface: for every pair, the separation vector (dx, dy) and the semivariance 0.5·(Δz)² are taken, and values are averaged over a 2D grid of lags. Anisotropy shows as an ellipse: the direction along which γ grows more slowly (longer range) is the axis of maximum continuity (for folding - the strike).\n\nThe Log and the HTML report give estimates: the major-axis azimuth (geographic, 0=N, clockwise), the anisotropy ratio (minor/major) and the range. They can be fed into the "2D Kriging" variogram structure (azimuth, anisotropy, range a) - this is how anisotropy enters kriging. The estimate is indicative: refine it against the heatmap itself.\n\nIf the structure is near-isotropic or the range is smaller than a cell - anisotropy is not estimated (marked "not expressed").\n\nOpt. surface raster (in lag coordinates, origin at 0,0) - for those who want to see the map on the canvas.',
    'Бинов на полуось (детализация карты)': 'Bins per half-axis (map detail)',
    'Макс. лаг, в единицах слоя (0 = пол-диагонали)': 'Max. lag, in layer units (0 = half-diagonal)',
    'Растр поверхности (опц., в лаг-координатах)': 'Surface raster (opt., in lag coordinates)',
    'Не задан точечный слой.': 'No point layer specified.',
    'Канал': 'Band',
    'Минимум точек в группе, % от выборки (пол 30 точек)': 'Minimum points per group, % of sample (floor 30 points)',
    'Модель для подбора': 'Model to fit',
    'Устойчивая оценка (Кресси-Хокинса)': 'Robust estimator (Cressie-Hawkins)',
    'Показать облако пар': 'Show pair cloud',
    'Наложить заданную модель вариограммы': 'Overlay a given variogram model',
    'Модель: тип': 'Model: type',
    'Модель: порог/вклад C': 'Model: sill/contribution C',
    'Модель: радиус корреляции a': 'Model: correlation range a',
    'Модель: азимут, °': 'Model: azimuth, °',
    'Модель: анизотропия (малая/главная)': 'Model: anisotropy (minor/major)',
    'Отсев: перцентиль обрезки, % (0 = выкл.)': 'Outliers: clip percentile, % (0 = off)',
    'Отсев: нижняя граница (пусто = нет)': 'Outliers: lower bound (empty = none)',
    'Отсев: верхняя граница (пусто = нет)': 'Outliers: upper bound (empty = none)',
    'Отсев: срезать к границе вместо удаления': 'Outliers: cap to bound instead of removing',
    'Мин. количество пар в ячейке': 'Min. number of pairs per cell',
    'модель': 'model',
    'радиус корреляции a (0=авто)': 'correlation range a (0=auto)',
    'азимут, °': 'azimuth, °',
    'анизотропия (малая/главная)': 'anisotropy (minor/major)',
    '0 = авто: min(охват)/50': '0 = auto: min(extent)/50',
    'грид: -': 'grid: -',
    ' (авто)': ' (auto)',
    'Профиль «%s»: %s.': 'Profile "%s": %s.',
    ' Расчёт пойдёт по профилю - поля ниже игнорируются.': ' Computation will use the profile - the fields below are ignored.',
    'грид: %d × %d%s': 'grid: %d × %d%s',
    'Профиль не выбран - расчёт по полям диалога.': 'No profile selected - computing from dialog fields.',
    'Профиль не выбран.': 'No profile selected.',
    'Профиль «%s» не найден.': 'Profile "%s" not found.',
    'Сферическая': 'Spherical',
    'Экспоненциальная': 'Exponential',
    'Гауссова': 'Gaussian',
    'Степенная': 'Power',
    'Ординарный (OK)': 'Ordinary (OK)',
    'Простой (SK)': 'Simple (SK)',
    'Авто (лучшая по R²)': 'Auto (best by R²)',
    'Показать список': 'Show list',
    'Сохранить вручную (по полям ниже)': 'Save manually (from fields below)',
    'Удалить выбранный': 'Delete selected',
    'Очистить все': 'Clear all',
    '(не выбран)': '(none)',
    'Загрузить профиль обработки': 'Load processing profile',
    'Профиль (для удаления / просмотра)': 'Profile (for deletion / preview)',
    '\n\n- - -\nРазработано при поддержке ООО «Информ++» (www.informpp.ru).\nСтраница плагина: www.informpp.ru/главная-страница/qgis-isoliner': '\n\n- - -\nDeveloped with the support of Inform++ LLC (www.informpp.ru).\nPlugin page: www.informpp.ru/главная-страница/qgis-isoliner',
    '≥ %.4g (упёрся в макс. лаг)': '≥ %.4g (capped at max lag)',
    '<p><i>Интерактивный график недоступен (нет plotly). Числовые оценки - в сводке выше.</i></p>': '<p><i>Interactive chart unavailable (no plotly). Numeric estimates are in the summary above.</i></p>',
    "<div style='background:#f3f7f4;border:1px solid #cde0d6;padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'><b>Что дальше</b><ul style='margin:6px 0'>%s</ul></div>": "<div style='background:#f3f7f4;border:1px solid #cde0d6;padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'><b>What's next</b><ul style='margin:6px 0'>%s</ul></div>",
    "<div style='background:#f5f5f7;border:1px solid #ddd;padding:8px 14px;border-radius:6px;display:inline-block'><b>Сводка</b><div style='margin-top:6px'><table cellpadding='4'>%s</table></div></div>": "<div style='background:#f5f5f7;border:1px solid #ddd;padding:8px 14px;border-radius:6px;display:inline-block'><b>Summary</b><div style='margin-top:6px'><table cellpadding='4'>%s</table></div></div>",
    'Точек': 'Points',
    'Поле Z': 'Z field',
    'Макс. лаг': 'Max. lag',
    'не выражена': 'not expressed',
    'Ячейка лага': 'Lag cell',
    'главная ось': 'major axis',
    'Анизотропия': 'Anisotropy',
    'Вариокарта · %s': 'Variomap · %s',
    'Бинов на полуось': 'Bins per half-axis',
    'Дисперсия (силл)': 'Variance (sill)',
    'лаг по северу h_y': 'north lag h_y',
    'Радиус главной оси': 'Major-axis range',
    '2.01 Категориальный индикаторный кригинг': '2.01 Categorical indicator kriging',
    'Вероятности минтипа': 'Mineral-type probabilities',
    'Вероятности по классам (многополосный)': 'Class probabilities (multiband)',
    'Добавить категориальное поле минтипа (демо замещения)': 'Add a categorical mineral-type field (replacement demo)',
    'Зоны минтипа': 'Mineral-type zones',
    'Карта зон (самый вероятный класс)': 'Zone map (most likely class)',
    'Категориальное поле (класс)': 'Categorical field (class)',
    'Класс «%s»: всего %d точек, индикаторная вариограмма будет шумной, вероятность по нему ненадёжна.': 'Class "%s": only %d points, the indicator variogram will be noisy and its probability unreliable.',
    'Классов: %d, точек: %d.': 'Classes: %d, points: %d.',
    'Коды зон: ': 'Zone codes: ',
    'Поле mintype (демо): ': 'Field mintype (demo): ',
    'Полосы растра вероятностей подписаны именами классов.': 'The probability raster bands are labelled with the class names.',
    'Сетка %d x %d, ячейка %.4g.': 'Grid %d x %d, cell %.4g.',
    'Слишком мало точек с заданным классом.': 'Too few points with a class value.',
    'Уверенность (макс. вероятность)': 'Confidence (max probability)',
    'Индикаторный кригинг по категориальному полю (минтип, литотип, класс). На каждый класс строится индикатор 0/1, кригуется отдельно (ядро GSLIB KB2D), оценка обрезается в 0-1, затем вероятности по классам нормируются к сумме 1. Кодом класса не кригуем: у категорий нет порядка.\n\nВыход: многополосный растр вероятностей (полоса на класс, в описании полосы - имя класса), растр зон (код самого вероятного класса, соответствие кодов в Журнале) и растр уверенности (максимум вероятности). Пустые и NULL исключаются. Вариограмма каждого индикатора подбирается автоматически (сферическая).': 'Indicator kriging on a categorical field (mineral type, lithotype, class). For each class a 0/1 indicator is built and kriged separately (GSLIB KB2D core), the estimate is clipped to 0-1, then the class probabilities are normalised to sum to 1. The class code is not kriged: categories have no order.\n\nOutputs: a multiband probability raster (one band per class, the class name in the band description), a zone raster (code of the most likely class, the mapping is printed to the log) and a confidence raster (maximum probability). Empty and NULL values are excluded. Each indicator variogram is fitted automatically (spherical).',
    'Стиль изолиний': 'Isoline style',
    'Без стиля': 'No style',
    'Структура / гипсометрия': 'Structure / hypsometry',
    'Мощности': 'Thickness',
    'Гидроизогипсы': 'Water-table contours',
    'Содержания': 'Grades',
    'Депрессия (штрихи вниз)': 'Depression (downhill hachures)',
    'Выбран депрессионный стиль, но «Сторона склона для бергштрихов» выключена - поля dn_sign нет, штрихи не определят сторону. Включите галку, чтобы штрихи смотрели вниз.': 'The depression style is selected but "Downhill side for hachures" is off, so there is no dn_sign field and the hachures cannot pick a side. Enable the checkbox so the hachures point downhill.',
    'Сторона склона для бергштрихов (поле dn_sign)': 'Downhill side for hachures (dn_sign field)',
    'Сторона склона (dn_sign) для бергштрихов…': 'Downhill side (dn_sign) for hachures…',
    'Не удалось вычислить сторону склона (dn_sign): %s': 'Could not compute the downhill side (dn_sign): %s',
    'Мало точек (%d): оценка кригинга и вариограммы неустойчива.': 'Few points (%d): the kriging and variogram estimates are unstable.',
    'Точек с совпадающими координатами: %d. Частая причина вырожденной матрицы и артефактов. Уберите дубли или усредните пробы в одной точке.': 'Points with coinciding coordinates: %d. A common cause of a singular matrix and artefacts. Remove duplicates or average the samples at one location.',
    'Все значения одинаковы: кригинг вырождается, вариограмма нулевая. Проверьте выбранное поле.': 'All values are identical: kriging degenerates and the variogram is zero. Check the selected field.',
    'Азимут главной оси': 'Major-axis azimuth',
    'Записать анизотропию в профиль': 'Write anisotropy to a profile',
    'Анизотропия не выражена - в профиль писать нечего.': 'Anisotropy not resolved, nothing to write to the profile.',
    'Профиль «%s» не найден - анизотропия не сохранена. Сначала сохраните профиль в «Вариограмме» или «Кросс-валидации».': 'Profile "%s" not found, anisotropy not saved. First save a profile in Variogram or Cross-validation.',
    'В профиль «%s» записаны азимут=%.0f° и анизотропия=%.2f (радиус оставлен прежним: упёрся в макс. лаг). При загрузке профиля они появятся в подписи.': 'Written to profile "%s": azimuth=%.0f° and anisotropy=%.2f (range left unchanged: it hit the max lag). They will appear in the caption when the profile is loaded.',
    'В профиль «%s» записаны азимут=%.0f°, анизотропия=%.2f, радиус a=%.4g. При загрузке профиля они появятся в подписи.': 'Written to profile "%s": azimuth=%.0f°, anisotropy=%.2f, range a=%.4g. They will appear in the caption when the profile is loaded.',
    'лаг по востоку h_x': 'east lag h_x',
    'эллипс анизотропии': 'anisotropy ellipse',
    'Анизотропия (малая/главная)': 'Anisotropy (minor/major)',
    'Вариограммная карта %s · %s': 'Variogram map %s · %s',
    'Вариограммная карта: %d точек…': 'Variogram map: %d points…',
    'Не удалось записать HTML-отчёт: %s': 'Could not write the HTML report: %s',
    'Точки прорежены до %d (для скорости).': 'Points subsampled to %d (for speed).',
    'Не удалось записать растр поверхности: %s': 'Could not write the surface raster: %s',
    'plotly недоступен (%s) - отчёт без графика.': 'plotly unavailable (%s) - report without a chart.',
    'Оценка индикативная - сверьте с формой хитмапа (эллипса).': 'The estimate is indicative - check it against the heatmap (ellipse) shape.',
    'В «2D Kriging» задайте: азимут=%.0f, анизотропия=%.2f, радиус a=%.4g.': 'In "2D Kriging" set: azimuth=%.0f, anisotropy=%.2f, range a=%.4g.',
    'Главная ось непрерывности ~%.0f° (геогр.). Для складчатости это направление простирания.': 'Major continuity axis ~%.0f° (geographic). For folding this is the strike direction.',
    'Подставьте в структуру вариограммы «2D Kriging»: азимут=%.0f, анизотропия=%.2f, радиус a=%.4g.': 'Feed into the "2D Kriging" variogram structure: azimuth=%.0f, anisotropy=%.2f, range a=%.4g.',
    'Анизотропия не разрешается на этой сетке: структура близка к изотропной либо радиус меньше ячейки.': 'Anisotropy is not resolved on this grid: the structure is near-isotropic or the range is smaller than a cell.',
    'Попробуйте уменьшить «Макс. лаг» или увеличить «Бинов на полуось», чтобы разрешить ближнюю структуру.': 'Try reducing "Max. lag" or increasing "Bins per half-axis" to resolve the near structure.',
    'Анизотропия: азимут главной оси %.0f° (геогр.), коэффициент %.2f (малая/главная), радиус главной оси %.4g.': 'Anisotropy: major-axis azimuth %.0f° (geographic), ratio %.2f (minor/major), major-axis range %.4g.',
    'В «2D Kriging» подставьте азимут=%.0f и анизотропию≈%.2f (как ориентир); радиус a задайте больше %.4g по смыслу данных.': 'In "2D Kriging" use azimuth=%.0f and anisotropy≈%.2f (as a guide); set range a larger than %.4g per the data.',
    'Если γ не выходит на полку даже при широком окне - в данных тренд: его убирают до интерполяции либо учитывают видом кригинга.': 'If γ does not reach a plateau even in a wide window - the data has a trend: remove it before interpolation or account for it with the kriging type.',
    'Анизотропия не выражена (структура близка к изотропной или радиус меньше ячейки). Можно уменьшить макс. лаг или увеличить количество бинов.': 'Anisotropy not expressed (the structure is near-isotropic or the range is smaller than a cell). You can reduce the max lag or increase the number of bins.',
    'В «2D Kriging» задайте азимут=%.0f и анизотропию≈%.2f как ориентир, радиус a возьмите больше %.4g по смыслу данных. Чтобы измерить радиус - увеличьте «Макс. лаг».': 'In "2D Kriging" set azimuth=%.0f and anisotropy≈%.2f as a guide, and take range a larger than %.4g per the data. To measure the range - increase "Max. lag".',
    'Радиус главной оси упёрся в макс. лаг (%.4g): вдоль простирания вариограмма на полку не вышла - радиус считайте нижней оценкой, а анизотропию (%.2f) - заниженной по выраженности.': 'The major-axis range is capped at the max lag (%.4g): along the strike the variogram did not reach a plateau - treat the range as a lower bound and the anisotropy (%.2f) as understated.',
    'Радиус главной оси упёрся в макс. лаг (%.4g): вдоль простирания вариограмма на полку не вышла. Радиус - нижняя оценка, анизотропия (%.2f) занижена по выраженности. Увеличьте «Макс. лаг», либо это признак тренда / очень сильной непрерывности.': 'The major-axis range is capped at the max lag (%.4g): along the strike the variogram did not reach a plateau. The range is a lower bound and the anisotropy (%.2f) is understated. Increase "Max. lag", or this signals a trend / very strong continuity.',
    'Полигоны · %s': 'Polygons · %s',
    'Изолинии · %s': 'Isolines · %s',
    'Кригинг %s · %s': 'Kriging %s · %s',
    'Остатки CV %s · %s': 'CV residuals %s · %s',
    'Вариограмма %s · %s': 'Variogram %s · %s',
    'Стд. ошибка · %s · %s': 'Std. error · %s · %s',
    ' (показаны ~30000 точек)': ' (showing ~30000 points)',
    ' Радиус подбора (%.4g) достигает края окна (%.4g), это подтверждает: кривая ещё растёт.': ' The fitted range (%.4g) reaches the window edge (%.4g), confirming the curve is still rising.',
    ' Радиус подбора (%.4g) у края окна (%.4g) - считайте его нижней оценкой, при сомнении увеличьте окно и проверьте, стабилизируется ли радиус.': ' The fitted range (%.4g) is at the window edge (%.4g) - treat it as a lower bound; if in doubt, widen the window and check whether the range stabilizes.',
    ' и %d без геометрии': ' and %d without geometry',
    ', анизотропия %.3g по азимуту %.4g°': ', anisotropy %.3g at azimuth %.4g°',
    ', отсев %.4g%%': ', outlier clip %.4g%%',
    '<br>пар %{customdata}': '<br>pairs %{customdata}',
    "<div style='background:#f3f7f4;border:1px solid #cde0d6;padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'><b>Рекомендации</b><ul style='margin:6px 0'>%s</ul></div>": "<div style='background:#f3f7f4;border:1px solid #cde0d6;padding:8px 14px;border-radius:6px;max-width:900px;margin:12px 0'><b>Recommendations</b><ul style='margin:6px 0'>%s</ul></div>",
    "<div style='background:#f5f5f7;border:1px solid #ddd;padding:8px 14px;border-radius:6px'><b>Параметры кригинга</b> <span style='color:#888;font-size:88%%'>(отличные от стандартных)</span><div style='margin-top:6px'>%s</div></div>": "<div style='background:#f5f5f7;border:1px solid #ddd;padding:8px 14px;border-radius:6px'><b>Kriging parameters</b> <span style='color:#888;font-size:88%%'>(differing from defaults)</span><div style='margin-top:6px'>%s</div></div>",
    "<div style='background:#f5f5f7;border:1px solid #ddd;padding:8px 14px;border-radius:6px;display:inline-block'><b>Сводка</b><div style='margin-top:6px'>%s</div></div>": "<div style='background:#f5f5f7;border:1px solid #ddd;padding:8px 14px;border-radius:6px;display:inline-block'><b>Summary</b><div style='margin-top:6px'>%s</div></div>",
    '<p><i>Интерактивный график недоступен (нет plotly). ': '<p><i>Interactive chart unavailable (no plotly). ',
    "<p><i>Интерактивный график недоступен (нет plotly). Значения экспериментальной вариограммы:</i></p><table border='1' cellpadding='4' style='border-collapse:collapse'>%s%s</table>": "<p><i>Interactive chart unavailable (no plotly). Experimental variogram values:</i></p><table border='1' cellpadding='4' style='border-collapse:collapse'>%s%s</table>",
    "<span style='color:#777'>все параметры - стандартные</span>": "<span style='color:#777'>all parameters are default</span>",
    "<table style='border-collapse:collapse' cellpadding='6'><tr><th align='left'>Метрика</th><th>Значение</th><th align='left'>Смысл</th></tr>%s</table>": "<table style='border-collapse:collapse' cellpadding='6'><tr><th align='left'>Metric</th><th>Value</th><th align='left'>Meaning</th></tr>%s</table>",
    "<tr><th align='left'>серия</th><th>h</th><th>γ(h)</th><th>пар</th></tr>": "<tr><th align='left'>series</th><th>h</th><th>γ(h)</th><th>pairs</th></tr>",
    '== Кросс-валидация (leave-one-out) ==': '== Cross-validation (leave-one-out) ==',
    'ME (смещение)': 'ME (bias)',
    'ME (смещение):   %+.4g   (ближе к 0 - лучше)': 'ME (bias):       %+.4g   (closer to 0 is better)',
    'ME близок к 0: систематического смещения нет.': 'ME close to 0: no systematic bias.',
    'ME заметно отличается от 0 (%+.3g): возможен систематический сдвиг - проверьте данные и тип кригинга (для простого - заданное среднее).': 'ME differs noticeably from 0 (%+.3g): possible systematic shift - check the data and the kriging type (for simple kriging, the specified mean).',
    'MSDR близок к 1: масштаб вариограммы подобран адекватно.': 'MSDR close to 1: the variogram scale is adequate.',
    'MSDR заметно больше 1 (%.3g): карта стандартной ошибки занижена. Умножьте наггет C0 и вклады C на MSDR (радиус и модель не трогайте) и пересчитайте - сами оценки не изменятся, поправится только дисперсия кригинга.': "MSDR noticeably above 1 (%.3g): the standard-error map is understated. Multiply nugget C0 and contributions C by MSDR (leave range and model) and recompute - the estimates won't change, only the kriging variance is corrected.",
    'MSDR меньше 1 (%.3g): неопределённость завышена. Разделите наггет C0 и вклады C на MSDR (радиус и модель не трогайте) и пересчитайте - оценки не изменятся.': "MSDR below 1 (%.3g): uncertainty is overstated. Divide nugget C0 and contributions C by MSDR (leave range and model) and recompute - the estimates won't change.",
    'MSDR:            %.3f   (ближе к 1 - лучше)': 'MSDR:            %.3f   (closer to 1 is better)',
    'QQ-график остатков': 'Residuals QQ-plot',
    'R (оценка/факт)': 'R (estimate/actual)',
    'R (оценка/факт): %.3f': 'R (estimate/actual): %.3f',
    'RMSE:            %.4g   (меньше - лучше)': 'RMSE:            %.4g   (lower is better)',
    '|Ошибка|': '|Error|',
    'В поле группировки меньше 2 значений - строю только общую кривую.': 'The grouping field has fewer than 2 values - building only the overall curve.',
    'В растре нет валидных значений.': 'The raster has no valid values.',
    'Верхняя граница': 'Upper bound',
    'Выберите профиль для удаления в поле «Профиль».': 'Select a profile to delete in the "Profile" field.',
    'Высокая корреляция (R=%.2f): оценки хорошо согласуются с фактом.': 'High correlation (R=%.2f): estimates agree well with the actual values.',
    'Гауссова модель с почти нулевым наггетом численно неустойчива (кригинг даёт «бычьи глаза», MSDR разваливается). Задайте небольшой наггет C0.': 'A Gaussian model with a near-zero nugget is numerically unstable (kriging gives "bull\'s eyes", MSDR falls apart). Set a small nugget C0.',
    'Гауссова модель: наггет повышен с %.4g до %.4g для устойчивости (минимум %g%% структурного силла).': 'Gaussian model: nugget raised from %.4g to %.4g for stability (minimum %g%% of the structural sill).',
    'Гистограмма ошибок': 'Error histogram',
    'Главные изолинии: каждая %d-я…': 'Index isolines: every %d-th…',
    'Групп больше 12 - группировка пропущена.': 'More than 12 groups - grouping skipped.',
    'Группы меньше %d точек пропущены: %s.': 'Groups smaller than %d points skipped: %s.',
    'Диаграмму можно построить по слою остатков.</i></p>': 'You can build a chart from the residuals layer.</i></p>',
    'Дисперсия данных': 'Data variance',
    'Дисперсия данных: %.4g. Ориентир: суммарный силл (C0 + вклады C) задавайте близким к ней. Наггет и силл - в абсолютных единицах дисперсии, не 0-1.': 'Data variance: %.4g. Guide: set the total sill (C0 + contributions C) close to it. Nugget and sill are in absolute variance units, not 0-1.',
    'Для сохранения укажите «Имя профиля».': 'To save, specify a "Profile name".',
    'Загружено алгоритмов: %d': 'Algorithms loaded: %d',
    'Задайте шаг изолиний или уровни.': 'Specify an isoline step or levels.',
    'Задан только наггет (структурный вклад C = 0): кригинг выродится в локальное среднее, поверхность будет почти плоской.': 'Only the nugget is set (structural contribution C = 0): kriging degenerates into a local mean and the surface will be almost flat.',
    'Итог: параметры можно утверждать - перенесите ту же вариограмму и настройки поиска в «2D Kriging».': 'Verdict: the parameters can be accepted - carry the same variogram and search settings into "2D Kriging".',
    'Итог: параметры стоит подправить (см. ниже) и повторить кросс-валидацию перед финальным кригингом.': 'Verdict: the parameters should be adjusted (see below) and cross-validation repeated before the final kriging.',
    'Контур валидной области…': 'Valid-area outline…',
    'Контур скважин: выпуклая оболочка…': 'Well outline: convex hull…',
    'Кресси-Хокинса': 'Cressie-Hawkins',
    'Кровля: максимум, м (абс.)': 'Roof: maximum, m (abs.)',
    'Кровля: минимум, м (абс.)': 'Roof: minimum, m (abs.)',
    'Кросс-валидация %s · %s': 'Cross-validation %s · %s',
    'Кросс-валидация по %d точкам…': 'Cross-validation over %d points…',
    'Макс. точек': 'Max. points',
    'Максимальное расстояние': 'Maximum distance',
    'Максимальное расстояние (%.4g) меньше типичного шага между точками (~%.4g) - пар почти нет. Значение задаётся в единицах слоя (обычно метры).': 'The maximum distance (%.4g) is smaller than the typical point spacing (~%.4g) - almost no pairs. The value is in layer units (usually metres).',
    'Максимум значения должен быть больше минимума.': 'The maximum value must be greater than the minimum.',
    'Матерона': 'Matheron',
    'Мин. точек': 'Min. points',
    'Мощность: максимум, м': 'Thickness: maximum, m',
    'Мощность: минимум, м': 'Thickness: minimum, m',
    'Назначение диапазонов поясам…': 'Assigning ranges to bands…',
    'Не задана корректная область (экстент).': 'No valid area (extent) specified.',
    'Не удалось добавить %s: %s': 'Could not add %s: %s',
    'Не удалось открыть растр для сглаживания.': 'Could not open the raster for smoothing.',
    'Не удалось открыть растр для сгущения.': 'Could not open the raster for densification.',
    'Не удалось открыть растр.': 'Could not open the raster.',
    'Не удалось перечислить группы: %s': 'Could not enumerate groups: %s',
    'Не удалось разобрать уровень: %r': 'Could not parse level: %r',
    'Недостаточно валидных точек с числовым значением.': 'Not enough valid points with a numeric value.',
    'Недостаточно уровней для полигонов.': 'Not enough levels for polygons.',
    'Ни один пояс не получил значения.': 'No band received a value.',
    'Нижняя граница': 'Lower bound',
    'Низкая корреляция (R=%.2f): модель слабо предсказывает - попробуйте другой радиус, модель или анизотропию; либо это предел данных (короткомасштабная изменчивость, зоны замещения).': "Low correlation (R=%.2f): the model predicts poorly - try a different range, model or anisotropy; or it is the data's limit (short-range variability, replacement zones).",
    'Нодирование сети линий (GEOS)…': 'Noding the line network (GEOS)…',
    'Номер скважины': 'Well number',
    'Обрезка по маске…': 'Clipping by mask…',
    'Осталось профилей: %d%s': 'Profiles remaining: %d%s',
    'Отсев: перцентиль, %': 'Outliers: percentile, %',
    'Оценка': 'Estimator',
    'Оценка vs факт': 'Estimate vs actual',
    'Оценка кригинга (LOO)': 'Kriging estimate (LOO)',
    'Ошибка (оценка − факт)': 'Error (estimate − actual)',
    'Подбор: модель %s, C0=%.4g, C=%.4g, a=%.4g, R²=%.3f': 'Fit: model %s, C0=%.4g, C=%.4g, a=%.4g, R²=%.3f',
    'Подвыборка точек': 'Point subsampling',
    'Подставлен профиль «%s»: %s.': 'Applied profile "%s": %s.',
    'Полигонизация не дала результата (проверьте изолинии/контур).': 'Polygonization produced nothing (check the isolines/outline).',
    'Полигонизация поясов (GEOS)…': 'Polygonizing bands (GEOS)…',
    'После отсева ураганных проб осталось < 2 точек.': 'After outlier removal fewer than 2 points remain.',
    'После усреднения совпадающих точек осталось < 2 узлов.': 'After averaging coincident points fewer than 2 nodes remain.',
    'Поясов получено (GEOS): %d': 'Bands produced (GEOS): %d',
    'Прервано пользователем.': 'Cancelled by the user.',
    'Продление открытых концов за контур…': 'Extending open ends past the outline…',
    'Пропущено точек: %d без значения «%s»%s. Прочитано: %d.': 'Points skipped: %d without a value for "%s"%s. Read: %d.',
    'Профиль «%s» сохранён: %s': 'Profile "%s" saved: %s',
    'Профиль «%s» сохранён: изотропная модель из автоподбора + текущий отсев. Анизотропию можно задать в кросс-валидации или инструменте «Профили».': 'Profile "%s" saved: isotropic auto-fit model + current outlier removal. Anisotropy can be set in cross-validation or the "Profiles" tool.',
    'Профиль «%s» сохранён: проверенная модель Структуры 1 (с анизотропией, если задана) + отсев.': 'Profile "%s" saved: validated Structure 1 model (with anisotropy, if set) + outlier removal.',
    'Профиль «%s» удалён.': 'Profile "%s" deleted.',
    'Профиль не найден - использую значения из диалога. Список профилей обновляется при открытии окна инструмента.': 'Profile not found - using the dialog values. The profile list refreshes when the tool window opens.',
    'Радиус подбора (%.4g) достигает края окна (%.4g) - вариограмма не вышла на плато, радиус считайте нижней оценкой.': 'The fitted range (%.4g) reaches the window edge (%.4g) - the variogram did not reach a plateau; treat the range as a lower bound.',
    'Радиус поиска': 'Search radius',
    'Рекомендация: модель %s, наггет C0=%.4g, вклад C=%.4g (сумма %.4g), радиус a=%.4g. Качество подгонки R²=%.3f.': 'Recommendation: model %s, nugget C0=%.4g, contribution C=%.4g (total %.4g), range a=%.4g. Fit quality R²=%.3f.',
    'Сгенерировано скважин: %d. Поля: кровля (roof), мощность (thick), содержание X. Дисперсия X ≈ %.4g.': 'Wells generated: %d. Fields: roof, thickness (thick), grade X. Variance of X ≈ %.4g.',
    'Сглаживание грида (σ=%g яч.)…': 'Smoothing the grid (σ=%g cells)…',
    'Сглаживание поля (σ=%g яч.)…': 'Smoothing the field (σ=%g cells)…',
    'Сгущение грида ×%d (бикубика)…': 'Densifying the grid ×%d (bicubic)…',
    'Сетка %d x %d, ячейка %.4g, точек %d, структур %d': 'Grid %d x %d, cell %.4g, points %d, structures %d',
    'Скругление линий (Chaikin, %d итер.)…': 'Line rounding (Chaikin, %d iter.)…',
    'Слишком мало оценённых точек.': 'Too few estimated points.',
    'Совпадающих точек усреднено: %d (осталось %d)': 'Coincident points averaged: %d (%d remaining)',
    'Согласование концов изолиний с контуром…': 'Snapping isoline ends to the outline…',
    'Сохраните модель в профиль (поле «Сохранить профиль под именем»), проверьте «Кросс-валидацией» и подставьте профиль в «2D Kriging».': 'Save the model to a profile (the "Save profile as" field), check it with "Cross-validation" and apply the profile in "2D Kriging".',
    'Сохранённые профили (%d):': 'Saved profiles (%d):',
    'Сохранённых профилей нет.': 'No saved profiles.',
    'Среднее (SK)': 'Mean (SK)',
    'Срезка (capping)': 'Capping',
    'Станд. остаток (со знаком)': 'Std. residual (signed)',
    'Стартовая вариограмма для X (кригинг/кросс-валидация): суммарный силл ≈ %.4g, наггет C0 ≈ %.4g, радиус ≈ %.4g (в единицах координат). Уточните наггет по кросс-валидации до MSDR ≈ 1.': 'Starting variogram for X (kriging/cross-validation): total sill ≈ %.4g, nugget C0 ≈ %.4g, range ≈ %.4g (in coordinate units). Refine the nugget via cross-validation until MSDR ≈ 1.',
    'Структура %d: показатель степенной модели ω=%.3g вне (0; 2) - приведён к диапазону.': 'Structure %d: power-model exponent ω=%.3g outside (0; 2) - clamped to range.',
    'Структура %d: степенная модель - поле «радиус a» это показатель ω (0<ω<2), а не радиус; задан 0, взят ω=1.': 'Structure %d: power model - the "range a" field is the exponent ω (0<ω<2), not a range; 0 given, ω=1 used.',
    'Суммарный порог близок к дисперсии данных (%.4g) - масштаб правдоподобен.': 'The total sill is close to the data variance (%.4g) - the scale is plausible.',
    'Суммарный порог заметно выше дисперсии данных (%.4g) - окно, вероятно, перешагивает тренд или безрудную зону. Уменьшите максимальное расстояние до локального масштаба и проверьте выбросы.': 'The total sill is noticeably above the data variance (%.4g) - the window likely crosses a trend or a barren zone. Reduce the maximum distance to a local scale and check for outliers.',
    'Суммарный порог заметно ниже дисперсии данных (%.4g): вариограмма не вышла на плато - увеличьте максимальное расстояние, возможен тренд или вторая структура.': 'The total sill is noticeably below the data variance (%.4g): the variogram did not reach a plateau - increase the maximum distance; a trend or a second structure is possible.',
    'Точек много - для расчёта пар использована случайная подвыборка %d точек.': 'Many points - a random subsample of %d points was used for pair computation.',
    'Точек оценено': 'Points estimated',
    'Точек оценено: %d из %d': 'Points estimated: %d of %d',
    'Точек экспериментальной вариограммы мало для подбора. Увеличьте количество лагов или максимальное расстояние.': 'Too few experimental variogram points to fit. Increase the number of lags or the maximum distance.',
    'Точек: %d. Дисперсия данных: %.4g (ориентир для суммарного порога).': 'Points: %d. Data variance: %.4g (a guide for the total sill).',
    'Удалены все профили (%d).': 'All profiles deleted (%d).',
    'Ураганные пробы: срезано %d значений к [%.4g; %.4g].': 'Outliers: %d values capped to [%.4g; %.4g].',
    'Ураганные пробы: удалено %d точек вне [%.4g; %.4g]; осталось %d.': 'Outliers: %d points removed outside [%.4g; %.4g]; %d remaining.',
    'Факт (%s)': 'Actual (%s)',
    'Фильтр коротких линий (< %g)…': 'Short-line filter (< %g)…',
    'азимут=%g°': 'azimuth=%g°',
    'анис=%g': 'anis=%g',
    'ближе к 0 - лучше': 'closer to 0 is better',
    'ближе к 1 - лучше': 'closer to 1 is better',
    'все точки': 'all points',
    'выкл.': 'off',
    'да': 'yes',
    'дисперсия данных': 'data variance',
    'заданная модель': 'given model',
    'из %d': 'of %d',
    'корреляция': 'correlation',
    'меньше - лучше': 'lower is better',
    'модель (малая ось)': 'model (minor axis)',
    'наггет C0=%.4g, %s, порог C=%.4g, радиус a=%.4g': 'nugget C0=%.4g, %s, sill C=%.4g, range a=%.4g',
    'недостаточно точек для QQ': 'too few points for QQ',
    'облако пар': 'pair cloud',
    'оценка (LOO)': 'estimate (LOO)',
    'оценка − факт': 'estimate − actual',
    'ошибка (z-оценка)': 'error (z-score)',
    'подобранная модель': 'fitted model',
    'полудисперсия γ(h)': 'semivariance γ(h)',
    'пояса': 'bands',
    'простой (SK)': 'simple (SK)',
    'профиль': 'profile',
    'расстояние h': 'distance h',
    'скв. %{customdata}<br>факт %{x:.3g}<br>оценка %{y:.3g}<extra></extra>': 'well %{customdata}<br>actual %{x:.3g}<br>estimate %{y:.3g}<extra></extra>',
    'скв. %{text}<br>факт %{x:.3g}<br>оценка %{y:.3g}<extra>худшие</extra>': 'well %{text}<br>actual %{x:.3g}<br>estimate %{y:.3g}<extra>worst</extra>',
    'средняя |ошибка|': 'mean |error|',
    'суммарный силл (C0 + вклады C) ≈ дисперсии данных': 'total sill (C0 + contributions C) ≈ data variance',
    'теор. %{x:.2f}<br>ошибка (z) %{y:.2f}<extra></extra>': 'theor. %{x:.2f}<br>error (z) %{y:.2f}<extra></extra>',
    'теор. квантили (норм.)': 'theor. quantiles (norm.)',
    'факт': 'actual',

    # --- Кригинг с внешним дрейфом (External Drift) ---
    '2.02 Кригинг с внешним дрейфом (External Drift)':
        '2.02 External Drift Kriging',
    'Растр внешнего дрейфа (известен всюду)':
        'External drift raster (known everywhere)',
    'Канал растра дрейфа': 'Drift raster band',
    'Степень дрейфа': 'Drift degree',
    '1 (линейный)': '1 (linear)',
    '2 (квадратичный)': '2 (quadratic)',
    'Растр кригинга с дрейфом': 'Drift kriging raster',
    'Кригинг+дрейф %s · %s': 'Kriging+drift %s · %s',
    'Сторонняя величина s, заданная растром во всей области: соседний пласт, '
    'структурная поверхность, грубая модель, сейсмический атрибут. Значение '
    'поля Z регрессируется на s, кригуются остатки, дрейф возвращается из '
    'растра. Растр должен покрывать область оценки и быть в той же системе '
    'координат, что и точки.':
        'A secondary variable s given as a raster across the whole area: an '
        'adjacent seam, a structural surface, a coarse model, a seismic '
        'attribute. The Z field is regressed on s, the residuals are kriged, '
        'and the drift is added back from the raster. The raster must cover '
        'the estimation area and share the CRS of the points.',
    'Связь значения с внешней величиной s. Степень 1 - линейный дрейф '
    'm = a0 + a1·s, обычный выбор для External Drift. Степень 2 описывает '
    'изогнутую связь m = a0 + a1·s + a2·s², но может вобрать часть реальной '
    'структуры в дрейф - после неё посмотрите на вариограмму остатков.':
        'The relation between the value and the external variable s. Degree 1 '
        'is the linear drift m = a0 + a1·s, the usual choice for external '
        'drift. Degree 2 describes a curved relation m = a0 + a1·s + a2·s², '
        'but may absorb part of the real structure into the drift - check the '
        'residual variogram after using it.',
    'Необязательный растр стандартной ошибки кригинга остатков (sqrt '
    'дисперсии): мера неопределённости. Дрейф детерминирован и своей '
    'погрешности к ней не добавляет.':
        'An optional raster of the residual kriging standard error (sqrt of '
        'variance): a measure of uncertainty. The drift is deterministic and '
        'adds no error of its own.',
    'Кригинг с внешним дрейфом (External Drift): оценка по точкам, когда поле '
    'закономерно связано со сторонней величиной, известной всюду в виде '
    'растра (структурная поверхность соседнего пласта, грубая региональная '
    'модель, сейсмический атрибут).\n\nДрейф снимается регрессией значения на '
    'растр, кригуются остатки, дрейф возвращается к оценке из того же растра. '
    'Это та же схема регрессия-кригинг, что и флажок «Снять полиномиальный '
    'тренд» у «2D Kriging», только дрейф здесь не функция координат, а функция '
    'внешнего значения. Степень дрейфа 1 (линейный) почти всегда '
    'достаточна.\n\nВариограмму задавайте по ОСТАТКАМ. Растр дрейфа и точки '
    'должны быть в одной системе координат. Ячейки вне покрытия растра дрейфа '
    'остаются пустыми. Поиск, анизотропия, обрезка и стандартная ошибка - как '
    'у «2D Kriging».':
        'External Drift Kriging: estimation from points when the field is '
        'systematically related to a secondary variable known everywhere as a '
        'raster (the structural surface of an adjacent seam, a coarse regional '
        'model, a seismic attribute).\n\nThe drift is removed by regressing '
        'the value on the raster, the residuals are kriged, and the drift is '
        'added back from the same raster. This is the same regression-kriging '
        'scheme as the "Remove polynomial trend" option of "2D Kriging", only '
        'here the drift is a function of the external value, not of the '
        'coordinates. Drift degree 1 (linear) is almost always '
        'enough.\n\nFit the variogram on the RESIDUALS. The drift raster and '
        'the points must share the CRS. Cells outside the drift raster '
        'coverage are left empty. Search, anisotropy, clipping and the '
        'standard error are as in "2D Kriging".',
    'Не удалось открыть растр дрейфа.': 'Could not open the drift raster.',
    'Не удалось прочитать растр дрейфа.': 'Could not read the drift raster.',
    'Не удалось пересчитать растр дрейфа на сетку кригинга.':
        'Could not resample the drift raster onto the kriging grid.',
    'Растр дрейфа и точки в разных системах координат. Совместите CRS, иначе '
    'выборка дрейфа в скважинах будет неверной.':
        'The drift raster and the points are in different coordinate systems. '
        'Match the CRS, otherwise the drift sampled at the wells will be '
        'wrong.',
    'Внешний дрейф отключён: точек со значением дрейфа %d, для модели нужно '
    'больше %d.':
        'External drift disabled: %d points carry a drift value, the model '
        'needs more than %d.',
    'Отброшено %d точек вне растра дрейфа (нет значения s).':
        '%d points outside the drift raster were dropped (no s value).',
    'Снят внешний дрейф степени %d: убрано %.1f%% дисперсии (s данных %.4g, '
    's остатка %.4g). Кригуются остатки, дрейф возвращается к оценке из '
    'растра. Вариограмму задавайте по остаткам.':
        'External drift of degree %d removed: %.1f%% of the variance taken '
        'out (s of data %.4g, s of residual %.4g). The residuals are kriged '
        'and the drift is added back from the raster. Fit the variogram on '
        'the residuals.',
    '%d ячеек оставлены пустыми: растр дрейфа их не покрывает.':
        '%d cells were left empty: the drift raster does not cover them.',
    'Добавить поверхность дрейфа и поле dz (для внешнего дрейфа)': 'Add a drift surface and a dz field (for external drift)',
    'Поверхность дрейфа (растр, демо)': 'Drift surface (raster, demo)',
    'Поверхность дрейфа (демо)': 'Drift surface (demo)',
    'Растр сторонней поверхности s, известной всюду: подаётся как дрейф в «Кригинг с внешним дрейфом», а поле dz скважин с ним линейно связано. Создаётся только при включённой галке поверхности дрейфа.':
        'A raster of a secondary surface s known everywhere: feed it as the drift to External Drift Kriging, while the wells dz field is linearly related to it. Created only when the drift-surface checkbox is on.',
    'Поверхность дрейфа (растр) и поле dz: dz линейно связано с поверхностью. Запустите «Кригинг с внешним дрейфом» по полю dz с этим растром как дрейфом - сравните с обычным «2D Kriging» по dz без дрейфа.':
        'Drift surface (raster) and dz field: dz is linearly related to the surface. Run External Drift Kriging on dz with this raster as the drift, and compare it with plain 2D Kriging on dz without the drift.',
    'Поверхность дрейфа (растр) + поле dz, для внешнего дрейфа': 'Drift surface (raster) + dz field, for external drift',
    'Включите этот вывод, чтобы получить пару для кригинга с внешним дрейфом: растр гладкой сторонней поверхности s (известна всюду) и поле dz скважин, линейно с ней связанное. Запустите «Кригинг с внешним дрейфом» по полю dz с этим растром как дрейфом. Если вывод пропущен, поле dz не добавляется. По умолчанию выключено.':
        'Enable this output to get a pair for external drift kriging: a raster of a smooth secondary surface s (known everywhere) and a wells dz field linearly related to it. Run External Drift Kriging on dz with this raster as the drift. If the output is skipped, the dz field is not added. Off by default.',
    '1. Грид и изолинии': '1. Grid and isolines',
    '2. Дополнительные инструменты': '2. Additional tools',
    '2.04 Карта вероятности превышения': '2.04 Exceedance probability map',
    'P(<%.4g)': 'P(<%.4g)',
    'P(>%.4g)': 'P(>%.4g)',
    'Вероятность %s · %s': 'Probability %s · %s',
    'Канал растра оценки': 'Estimate raster band',
    'Канал растра ошибки': 'Error raster band',
    'Карта вероятности превышения порога по растрам оценки и стандартной ошибки кригинга. Локальное распределение принимается нормальным, Z ~ N(оценка, ошибка²), и вероятность считается одной формулой P(Z>порог) = Φ((оценка−порог)/ошибка). Свой кригинг не выполняется, берутся готовые растры, поэтому «2D Kriging» остаётся без изменений.\n\nКак получить входы: запустите «2D Kriging» (или «Кригинг с внешним дрейфом») и включите необязательный вывод стандартной ошибки. Подайте сюда растр оценки и растр ошибки - получите растр вероятности 0…1.\n\nПрименение: бортовые содержания (вероятность, что содержание выше кондиции), зоны риска по любому порогу. Для сильно скошенных полей нормальное допущение грубовато - тогда точнее индикаторный кригинг по порогам.': 'An exceedance-probability map from the kriging estimate and standard-error rasters. The local distribution is taken as normal, Z ~ N(estimate, error²), and the probability is one formula P(Z>threshold) = Φ((estimate−threshold)/error). No kriging is run here, ready rasters are used, so \"2D Kriging\" stays unchanged.\n\nHow to get the inputs: run \"2D Kriging\" (or \"External Drift Kriging\") and enable the optional standard-error output. Feed the estimate raster and the error raster here to get a 0…1 probability raster.\n\nUse: cut-off grades (the probability that the grade is above the cut-off), risk zones for any threshold. For strongly skewed fields the normal assumption is rough - indicator kriging by thresholds is then more accurate.',
    'Не удалось открыть растр оценки.': 'Could not open the estimate raster.',
    'Не удалось открыть растр ошибки.': 'Could not open the error raster.',
    'Не удалось привести растр ошибки к сетке оценки.': 'Could not resample the error raster onto the estimate grid.',
    'Нет ячеек, где заданы и оценка, и ошибка.': 'No cells where both the estimate and the error are defined.',
    'Нужны оба растра: оценка и стандартная ошибка.': 'Both rasters are required: the estimate and the standard error.',
    'Порог': 'Threshold',
    'Порог %.4g, сторона %s. Вероятность ≥ 0.5 в %.0f%% ячеек с данными.': 'Threshold %.4g, side %s. Probability ≥ 0.5 in %.0f%% of cells with data.',
    'Растр вероятности (0…1)': 'Probability raster (0…1)',
    'Растр оценки (кригинг)': 'Estimate raster (kriging)',
    'Растр стандартной ошибки кригинга': 'Kriging standard-error raster',
    'Решётки оценки и ошибки различаются, ошибка приведена к сетке оценки билинейно.': 'The estimate and error grids differ; the error was resampled onto the estimate grid bilinearly.',
    'Сторона': 'Side',
    'выше': 'above',
    'выше порога: P(Z > порог)': 'above the threshold: P(Z > threshold)',
    'ниже': 'below',
    'ниже порога: P(Z < порог)': 'below the threshold: P(Z < threshold)',
    '2.05 Удельный расход (закон Дарси)': '2.05 Specific discharge (Darcy law)',
    'Векторов потока: %d (шаг %d яч.). Поворот по «az», размер по удельному расходу.': 'Flow vectors: %d (step %d cells). Rotated by "az", sized by the specific discharge.',
    'Задайте хотя бы один растр свойства: K или T.': 'Provide at least one property raster: K or T.',
    'Канал напора': 'Head band',
    'Канал растра K': 'K raster band',
    'Канал растра T': 'T raster band',
    'Не удалось прочитать растр K.': 'Could not read the K raster.',
    'Не удалось прочитать растр T.': 'Could not read the T raster.',
    'Растр водопроводимости T (м²/сут)': 'Transmissivity raster T (m²/day)',
    'Растр коэффициента фильтрации K (м/сут)': 'Hydraulic conductivity raster K (m/day)',
    'Растры K и T заданы как ln (экспонировать)': 'K and T rasters are given as ln (exponentiate)',
    'Расход через ширину Q = T·|∇h| (м²/сут)': 'Flow per width Q = T·|∇h| (m²/day)',
    'Расход через ширину Q · %s': 'Flow per width Q · %s',
    'Расход через ширину Q: медиана %.4g, максимум %.4g м²/сут.': 'Flow per width Q: median %.4g, maximum %.4g m²/day.',
    'Скорость фильтрации q = K·|∇h| (м/сут)': 'Specific discharge q = K·|∇h| (m/day)',
    'Скорость фильтрации q · %s': 'Specific discharge q · %s',
    'Скорость фильтрации q: медиана %.4g, максимум %.4g м/сут.': 'Specific discharge q: median %.4g, maximum %.4g m/day.',
    'Удельный расход подземного потока по закону Дарси. К геометрии потока (градиент напора и направление) добавляет свойства пласта, переводя безразмерный градиент в физический поток.\n\nВходы: растр напора и хотя бы один из растров свойств - коэффициент фильтрации K или водопроводимость T. Выходы: скорость фильтрации q = K·|∇h| (м/сут) и расход через единицу ширины потока Q = T·|∇h| (м²/сут), плюс направление и стрелки.\n\nКак получить K и T: кригуйте их по точкам испытаний. K и T обычно лог-нормальны (разброс на порядки), поэтому кригуйте ln(K) и ln(T), а тут включите «Растры заданы как ln». Истинная скорость воды v = q/n требует пористости и здесь не считается. Напорные и безнапорные пласты разумно криговать раздельно. Растры должны быть в одной системе координат.': 'Specific groundwater discharge by Darcy\'s law. To the flow geometry (head gradient and direction) it adds the aquifer properties, turning the dimensionless gradient into a physical flux.\n\nInputs: a head raster and at least one property raster - hydraulic conductivity K or transmissivity T. Outputs: specific discharge q = K·|∇h| (m/day) and flow per unit width Q = T·|∇h| (m²/day), plus direction and arrows.\n\nHow to get K and T: krige them from test points. K and T are usually log-normal (orders-of-magnitude spread), so krige ln(K) and ln(T) and tick \"rasters are given as ln\" here. The true water velocity v = q/n needs porosity and is not computed here. Confined and unconfined aquifers are best kriged separately. The rasters must share one coordinate system.',
    'Добавить поля K и T и напор (для удельного расхода)': 'Add K and T fields and head (for the specific discharge)',
    'Поля K и T (демо): K лог-нормально (K ≈ %.4g…%.4g м/сут), T = K·мощность. Для удельного расхода создайте калькулятором поля ln(K) и ln(T), кригуйте их, а при подаче в «Удельный расход (Дарси)» включите галку «Растры заданы как ln». Напор (head) кригуйте как обычно.': 'K and T fields (demo): K is log-normal (K ≈ %.4g…%.4g m/day), T = K·thickness. For the specific discharge, build ln(K) and ln(T) fields with the field calculator, krige them, and when feeding "Specific discharge (Darcy law)" tick "rasters are given as ln". Krige head as usual.',
    '2.03 Карта вероятности превышения': '2.03 Exceedance probability map',
    '2.04 Гидравлический градиент и направление потока': '2.04 Hydraulic gradient and flow direction',
    'ln (для лог-нормальных, напр. K, T)': 'ln (for log-normal, e.g. K, T)',
    'нет': 'none',
    'Преобразование значения': 'Value transform',
    'Логарифм: отброшено %d точек со значением ≤ 0 (ln определён только для положительных).': 'Logarithm: dropped %d points with value ≤ 0 (ln is defined for positive values only).',
    'Логарифм: положительных значений недостаточно для кригинга.': 'Logarithm: not enough positive values for kriging.',
    'Логарифмирование включено: кригуется ln(Z), оценка возвращается через exp (медиана). Вариограмму, наггет и среднее простого кригинга задавайте в единицах ln. Стандартная ошибка пересчитывается в исходные единицы дельта-методом.': 'Logarithm enabled: ln(Z) is kriged and the estimate is returned via exp (median). Set the variogram, nugget and simple-kriging mean in ln units. The standard error is converted back to the original units by the delta method.',
    'Логарифмирование перед кригингом для величин с разбросом на порядки (коэффициент фильтрации, водопроводимость, содержания с длинным правым хвостом). Кригуется ln(Z), оценка возвращается через exp - это медианная (геометрическая) оценка. Стандартная ошибка пересчитывается в исходные единицы дельта-методом. Значения должны быть положительными. Избавляет от ручного создания поля ln(Z). Вариограмму и наггет при этом задавайте в единицах ln.': 'Log-transform before kriging for quantities spanning orders of magnitude (hydraulic conductivity, transmissivity, grades with a long right tail). ln(Z) is kriged and the estimate is returned via exp, which is the median (geometric) estimate. The standard error is converted back to the original units by the delta method. Values must be positive. It removes the need to build an ln(Z) field by hand. Set the variogram and nugget in ln units.',
    '2.06 Разрез по линии': '2.06 Cross-section along a line',
    'В слое несколько линий, разрез построен по первой.': 'The layer has several lines; the section was built along the first.',
    'В слое нет линии.': 'The layer has no line.',
    'Вертикальное преувеличение (для чертежа)': 'Vertical exaggeration (for the drawing)',
    'Выборка растра': 'Raster sampling',
    'Геологический разрез по линии из набора поверхностей. Поверхности задаются списком и упорядочиваются сверху вниз (кровля, подошва, следующая кровля и так далее). Пласты строятся как полосы между соседними поверхностями, поэтому N поверхностей дают N−1 пластов.\n\nДва выхода. Чертёж разреза - полигоны в осях расстояние вдоль линии и высота, с вертикальным преувеличением для макета и печати. Забор 3D - те же полосы как вертикальные стенки PolygonZ в реальных координатах, для просмотра в 3D Map View рядом с поверхностями.\n\nПоверхности обычно получают кригингом (кровля, подошва пласта). Линию рисуют как обычный линейный слой. Расстояние и высота берутся в единицах карты. Свой кригинг инструмент не выполняет.': 'A geological cross-section along a line from a set of surfaces. The surfaces are given as a list and ordered top to bottom (roof, floor, next roof, and so on). Beds are built as bands between adjacent surfaces, so N surfaces give N−1 beds.\n\nTwo outputs. The section drawing - polygons in axes of distance along the line and elevation, with vertical exaggeration for layout and print. The 3D fence - the same bands as vertical PolygonZ walls in real coordinates, to view in the 3D Map View next to the surfaces.\n\nThe surfaces are usually obtained by kriging (the roof and floor of a bed). The line is drawn as an ordinary line layer. Distance and elevation are in map units. The tool runs no kriging of its own.',
    'Длина линии равна нулю.': 'The line length is zero.',
    'Забор 3D (PolygonZ, реальные координаты)': '3D fence (PolygonZ, real coordinates)',
    'Линия не пересекает поверхности: разрез пуст.': 'The line does not cross the surfaces: the section is empty.',
    'Линия разреза': 'Section line',
    'Не задана линия разреза.': 'No section line is set.',
    'Не удалось открыть растр: %s': 'Could not open the raster: %s',
    'Нужно минимум две поверхности (кровля и подошва).': 'At least two surfaces are needed (roof and floor).',
    'Пласт %d (%s / %s): средняя мощность %.4g ед.': 'Bed %d (%s / %s): mean thickness %.4g units.',
    'Поверхности сверху вниз (кровли и подошвы)': 'Surfaces top to bottom (roofs and floors)',
    'Разрез (3D-забор)': 'Section (3D fence)',
    'Разрез (чертёж)': 'Section (drawing)',
    'Разрез построен: %d пластов, длина %.4g ед., шаг %.4g.': 'Section built: %d beds, length %.4g units, step %.4g.',
    'Чертёж разреза (расстояние × высота)': 'Section drawing (distance × elevation)',
    'Шаг выборки вдоль линии, ед. карты (0 = по ячейке)': 'Sampling step along the line, map units (0 = by cell)',
    'билинейно': 'bilinear',
    'ближайший': 'nearest',
    '1.8 Создать пример для разреза': '1.8 Create a section example',
    'Готовый пример для инструмента «Разрез по линии». Строит три гладкие поверхности, лежащие стопкой (кровля верхней залежи, общая граница, подошва нижней), с региональным падением и волнистой переменной мощностью, и линию через площадь.\n\nПодайте три поверхности сверху вниз (1, 2, 3) и линию в «Разрез по линии». Получите два пласта на чертеже и 3D-забор. Кригинг для демо не нужен, поверхности уже растровые.': 'A ready example for the Cross-section along a line tool. It builds three smooth stacked surfaces (the roof of the upper bed, the shared boundary, the floor of the lower bed) with a regional dip and wavy variable thickness, and a line across the area.\n\nFeed the three surfaces top to bottom (1, 2, 3) and the line into Cross-section along a line. You get two beds on the drawing and a 3D fence. No kriging is needed for the demo, the surfaces are already rasters.',
    'Готово: три поверхности (две залежи) и линия. Подайте поверхности сверху вниз (1, 2, 3) и линию в «Разрез по линии».': 'Done: three surfaces (two beds) and a line. Feed the surfaces top to bottom (1, 2, 3) and the line into Cross-section along a line.',
    'Зерно генератора (0 = случайно)': 'Generator seed (0 = random)',
    'Линия разреза (демо)': 'Section line (demo)',
    'Не задан экстент.': 'No extent is set.',
    'Поверхность 1 (кровля верхнего пласта)': 'Surface 1 (roof of the upper bed)',
    'Поверхность 1 · кровля (демо)': 'Surface 1 · roof (demo)',
    'Поверхность 2 (подошва верхнего / кровля нижнего)': 'Surface 2 (floor of the upper / roof of the lower)',
    'Поверхность 2 · граница (демо)': 'Surface 2 · boundary (demo)',
    'Поверхность 3 (подошва нижнего пласта)': 'Surface 3 (floor of the lower bed)',
    'Поверхность 3 · подошва (демо)': 'Surface 3 · floor (demo)',
    'Разрез 1': 'Section 1',
    '3. Разрез': '3. Cross-sections',
    '4. Пласт и блочная модель': '4. Bed and block model',
    '3.01 Разрез по линии': '3.01 Cross-section along a line',
    '3.2 Создать пример для разреза': '3.2 Create a section example',
    '3.3 Скважины на разрез': '3.3 Boreholes on a section',
    'Вертикальное преувеличение (как в разрезе)': 'Vertical exaggeration (as in the section)',
    'Готово: три поверхности (две залежи), линия и скважины. Поверхности и линию подайте в «Разрез по линии»; скважины с полями h1, h2, h3 и линию - в «Скважины на разрез».': 'Done: three surfaces (two beds), a line and boreholes. Feed the surfaces and the line into Cross-section along a line; feed the boreholes with the h1, h2, h3 fields and the line into Boreholes on a section.',
    'Готовый пример для инструмента «Разрез по линии». Строит три гладкие поверхности, лежащие стопкой (кровля верхней залежи, общая граница, подошва нижней), с региональным падением и волнистой переменной мощностью, и линию через площадь.\n\nПодайте три поверхности сверху вниз (1, 2, 3) и линию в «Разрез по линии». Получите два пласта на чертеже и 3D-забор. Кригинг для демо не нужен, поверхности уже растровые. Заодно выдаются скважины вдоль линии с отметками поверхностей (h1, h2, h3) для инструмента «Скважины на разрез».': 'A ready example for the Cross-section along a line tool. It builds three smooth stacked surfaces (the roof of the upper bed, the shared boundary, the floor of the lower bed) with a regional dip and wavy variable thickness, and a line across the area.\n\nFeed the three surfaces top to bottom (1, 2, 3) and the line into Cross-section along a line. You get two beds on the drawing and a 3D fence. No kriging is needed for the demo, the surfaces are already rasters. It also outputs boreholes along the line with surface elevations (h1, h2, h3) for the Boreholes on a section tool.',
    'Интервалы пластов скважин (чертёж)': 'Borehole bed intervals (drawing)',
    'Коридор от линии, ед. карты (0 = все скважины)': 'Corridor from the line, map units (0 = all boreholes)',
    'Не задана линия или скважины.': 'No line or boreholes are set.',
    'Ни одна скважина не попала в коридор или не имеет отметок.': 'No borehole fell within the corridor or has elevations.',
    'Нужно минимум два поля отметок (кровля и подошва).': 'At least two elevation fields are needed (roof and floor).',
    'Поле номера скважины (для подписи)': 'Borehole number field (for the label)',
    'Поля отметок границ пластов (кровли и подошвы)': 'Bed-boundary elevation fields (roofs and floors)',
    'Проецирует скважины на линию разреза и показывает их колонками интервалов пластов в осях расстояние-высота, поверх чертежа из инструмента «Разрез по линии».\n\nГраницы пластов берутся из выбранных полей-отметок (кровли и подошвы). На каждой скважине их значения сортируются по убыванию, соседние пары дают интервалы пластов, поэтому порядок выбора полей и пропуски не важны. Каждый интервал получает номер пласта, а колонка - номер скважины.\n\nСкважина ставится на том расстоянии вдоль линии, куда падает её проекция. Дальние скважины отсекаются коридором (буфером вокруг линии). Вертикальное преувеличение задавайте таким же, как в «Разрез по линии», иначе колонки не лягут на пласты по высоте.': 'Projects boreholes onto the section line and shows them as columns of bed intervals in axes of distance and elevation, on top of the drawing from the Cross-section along a line tool.\n\nThe bed boundaries are taken from the chosen elevation fields (roofs and floors). On each borehole their values are sorted in descending order, adjacent pairs give the bed intervals, so the order of field selection and gaps do not matter. Each interval gets a bed number, and the column gets the borehole number.\n\nThe borehole is placed at the distance along the line where its projection falls. Distant boreholes are cut off by a corridor (a buffer around the line). Set the vertical exaggeration the same as in Cross-section along a line, otherwise the columns will not match the beds in height.',
    'Скважины': 'Boreholes',
    'Скважины вдоль линии (с отметками поверхностей)': 'Boreholes along the line (with surface elevations)',
    'Скважины на разрезе (интервалы)': 'Boreholes on the section (intervals)',
    'Спроецировано скважин: %d (интервалов %d), пропущено вне коридора %d.': 'Boreholes projected: %d (intervals %d), skipped outside the corridor %d.',
    'Устья скважин (подписи)': 'Borehole collars (labels)',
    'Готовый пример для инструментов разреза. Строит шесть гладких поверхностей, лежащих стопкой, с региональным падением и волнистой переменной мощностью. Между ними пять пластов в переслаивании: три вмещающих и два промышленных (2-й и 4-й, тонкие).\n\nПодайте шесть поверхностей сверху вниз (1...6) и линию в «Разрез по линии». Получите пять пластов на чертеже и 3D-забор. Кригинг для демо не нужен, поверхности уже растровые. Заодно выдаются скважины вдоль линии с отметками поверхностей (h1...h6) для инструмента «Скважины на разрез».': 'A ready example for the section tools. It builds six smooth stacked surfaces with a regional dip and wavy variable thickness. Between them are five interbedded beds: three host beds and two industrial beds (the 2nd and 4th, thin).\n\nFeed the six surfaces top to bottom (1...6) and the line into Cross-section along a line. You get five beds on the drawing and a 3D fence. No kriging is needed for the demo, the surfaces are already rasters. It also outputs boreholes along the line with surface elevations (h1...h6) for the Boreholes on a section tool.',
    'Готово: шесть поверхностей (пять пластов: три вмещающих и два промышленных), линия и скважины. Поверхности и линию подайте в «Разрез по линии»; скважины с полями h1...h6 и линию - в «Скважины на разрез».': 'Done: six surfaces (five beds: three host and two industrial), a line and boreholes. Feed the surfaces and the line into Cross-section along a line; feed the boreholes with the h1...h6 fields and the line into Boreholes on a section.',
    'Поверхность 1 (кровля верхней вмещающей)': 'Surface 1 (roof of the upper host)',
    'Поверхность 2 (кровля 1-го промышленного)': 'Surface 2 (roof of the 1st industrial)',
    'Поверхность 3 (подошва 1-го промышленного)': 'Surface 3 (floor of the 1st industrial)',
    'Поверхность 4 (кровля 2-го промышленного)': 'Surface 4 (roof of the 2nd industrial)',
    'Поверхность 5 (подошва 2-го промышленного)': 'Surface 5 (floor of the 2nd industrial)',
    'Поверхность 6 (подошва нижней вмещающей)': 'Surface 6 (floor of the lower host)',
    'Поверхность 2 · кровля 1-го пром. (демо)': 'Surface 2 · roof of 1st industrial (demo)',
    'Поверхность 3 · подошва 1-го пром. (демо)': 'Surface 3 · floor of 1st industrial (demo)',
    'Поверхность 4 · кровля 2-го пром. (демо)': 'Surface 4 · roof of 2nd industrial (demo)',
    'Поверхность 5 · подошва 2-го пром. (демо)': 'Surface 5 · floor of 2nd industrial (demo)',
    'Поверхность 6 · подошва (демо)': 'Surface 6 · floor (demo)',
    '3.4 Состав пласта на разрез': '3.4 Bed composition on a section',
    'Готовый пример для инструментов разреза. Строит шесть гладких поверхностей, лежащих стопкой, с региональным падением и волнистой переменной мощностью. Между ними пять пластов в переслаивании: три вмещающих и два промышленных (2-й и 4-й, тонкие).\n\nПодайте шесть поверхностей сверху вниз (1...6) и линию в «Разрез по линии». Получите пять пластов на чертеже и 3D-забор. Кригинг для демо не нужен, поверхности уже растровые. Заодно выдаются скважины вдоль линии с отметками поверхностей (h1...h6) для инструмента «Скважины на разрез», а также по многоканальному гриду на каждый промышленный пласт. Конвенция каналов: 1 кровля, 2 подошва, 3+ параметры (здесь содержание и минтип, независимые стохастические поля). Пласт как блочная модель: один файл кормит «Состав пласта на разрез» (каналы 1/2/3) и 3D-просмотр.': 'A ready example for the section tools. It builds six smooth stacked surfaces with a regional dip and wavy variable thickness. Between them are five interbedded beds: three host beds and two industrial beds (the 2nd and 4th, thin).\n\nFeed the six surfaces top to bottom (1...6) and the line into Cross-section along a line. You get five beds on the drawing and a 3D fence. No kriging is needed for the demo, the surfaces are already rasters. It also outputs boreholes along the line with surface elevations (h1...h6) for the Boreholes on a section tool, and a multiband grid per industrial bed. The band convention: 1 roof, 2 bottom, 3+ parameters (here content and mineral type, independent stochastic fields). A bed as a block model: one file feeds Bed composition on a section (bands 1/2/3) and the 3D viewer.',
    'Грид состава (содержание или класс)': 'Composition grid (content or class)',
    'Красит полосу одного пласта на разрезе по гриду состава вдоль линии. Берёт кровлю, подошву и грид состава, свой кригинг не делает.\n\nРежим «непрерывное» (содержание KCl, нерастворимый остаток): полоса режется на тонкие вертикальные срезы, каждый со средним значением, под градиентную заливку.\n\nРежим «категориальное» (минеральный тип, фации - сильвинит, замещение, галит): смежные срезы одного класса сливаются в фациальные зоны, под заливку по категориям. Зоны замещения видны как смена цвета вдоль линии.\n\nЗапускайте по каждому промышленному пласту отдельно. Вертикальное преувеличение задавайте таким же, как в «Разрез по линии».': 'Colours the band of one bed on the section by a composition grid along the line. It takes a roof, a floor and a composition grid, and runs no kriging of its own.\n\nThe \'continuous\' mode (KCl content, insoluble residue): the band is cut into thin vertical slices, each with a mean value, for a graduated fill.\n\nThe \'categorical\' mode (mineral type, facies - sylvinite, replacement, halite): adjacent slices of the same class merge into facies zones, for a categorized fill. Replacement zones show as a colour change along the line.\n\nRun it for each industrial bed separately. Set the vertical exaggeration the same as in Cross-section along a line.',
    'Кровля пласта (растр)': 'Bed roof (raster)',
    'Нужны линия, кровля, подошва и грид состава.': 'A line, roof, floor and composition grid are needed.',
    'Пласт и состав не пересекают линию: результат пуст.': 'The bed and composition do not cross the line: the result is empty.',
    'Подошва пласта (растр)': 'Bed floor (raster)',
    'Состав': 'Composition',
    'Состав пласта (3D)': 'Bed composition (3D)',
    'Состав пласта (чертёж)': 'Bed composition (drawing)',
    'Состав пласта: построено полигонов %d (%s).': 'Bed composition: %d polygons built (%s).',
    'зоны': 'zones',
    'категориальное (минтип, фации)': 'categorical (mineral type, facies)',
    'непрерывное (содержание)': 'continuous (content)',
    'срезы': 'slices',
    'Вертикальный масштаб': 'Vertical scale',
    'Вертикальный масштаб: множитель vex = %.4g.': 'Vertical scale: exaggeration vex = %.4g.',
    'Вертикальный масштаб: отношение Г:В ~ %.4g:1, множитель vex ~ %.4g.': 'Vertical scale: H:V ratio ~ %.4g:1, exaggeration vex ~ %.4g.',
    'Значение масштаба (отношение Г:В или множитель)': 'Scale value (H:V ratio or exaggeration)',
    'множитель': 'exaggeration factor',
    'отношение Г:В (ширина:высота)': 'H:V ratio (width:height)',
    '2. Дополнительные инструменты анализа': '2. Additional analysis tools',
    '3.2 Скважины на разрез': '3.2 Boreholes on a section',
    '3.3 Состав пласта на разрез': '3.3 Bed composition on a section',
    '3.4 Создать пример для разреза': '3.4 Create a section example',
    '3.04 Пересечение поверхностей с разрезом': '3.04 Intersect surfaces with the section',
    '3.5 Проекция объектов на разрез': '3.5 Project objects onto the section',
    '3.6 Спроецировать с разреза': '3.6 Unproject from the section',
    '3.7 Развёртка стенки ствола': '3.7 Unwrap a shaft wall',
    '3.8 Создать пример для разреза': '3.8 Create a section example',
    'В определении нет линии.': 'The definition has no line.',
    'В слое оси нет точки.': 'The axis layer has no point.',
    'Возвращает объекты, нарисованные на чертеже разреза, в реальные координаты. Горизонтальная координата вершины читается как расстояние вдоль линии (даёт план X, Y), высота - как отметка Z = высота / vex.\n\nЛиния и vex берутся из определения разреза - того же, по которому строился чертёж. Геометрия выходит с отметкой Z в реальной системе координат.\n\nТак нарисованный на разрезе объект (контур залежи, нарушение, граница) попадает обратно в план и в 3D.': 'Returns objects drawn on the section drawing to real coordinates. The horizontal coordinate of a vertex is read as the distance along the line (giving the plan X, Y), the height as the elevation Z = height / vex.\n\nThe line and vex come from the section definition - the same one the drawing was built with. The geometry comes out with a Z elevation in the real coordinate system.\n\nThis is how an object drawn on the section (an ore outline, a fault, a boundary) gets back into the plan and into 3D.',
    'Возвращено в план объектов: %d.': 'Objects returned to plan: %d.',
    'Гриды не открылись.': 'The grids could not be opened.',
    'Коридор от линии (0 = все объекты)': 'Corridor from the line (0 = all objects)',
    'Линии поверхностей (3D)': 'Surface lines (3D)',
    'Линии поверхностей на разрезе (чертёж)': 'Surface lines on the section (drawing)',
    'Множитель vex из определения: %.4g.': 'Exaggeration vex from the definition: %.4g.',
    'Нанесено поверхностей: %d.': 'Surfaces placed: %d.',
    'Наносит поверхности-гриды на разрез линиями в осях расстояние-высота. Каждый грид выбирается вдоль линии разреза, и его сечение ложится на чертёж рядом с пластами.\n\nЛиния и вертикальный масштаб берутся из определения разреза (линейный слой с полем vex - его выдаёт «Разрез по линии»). Поэтому линии гридов совпадают с разрезом без ручной подгонки.\n\nГодится для водоносных горизонтов, маркирующих поверхностей, кровли соли, поверхностей аномалий.': 'Places surface grids onto the section as lines in distance-elevation axes. Each grid is sampled along the section line, and its trace lies on the drawing next to the beds.\n\nThe line and the vertical scale come from the section definition (a line layer with a vex field produced by Cross-section along a line). So the grid lines match the section without manual fitting.\n\nGood for water tables, marker surfaces, the salt roof, anomaly surfaces.',
    'Нет объектов для проекции.': 'No objects to project.',
    'Ни один объект не спроецирован (коридор или геометрия).': 'No object was projected (corridor or geometry).',
    'Нужны определение и объекты.': 'A definition and objects are needed.',
    'Нужны определение разреза и хотя бы один грид.': 'A section definition and at least one grid are needed.',
    'Нужны ось и поверхности.': 'An axis and surfaces are needed.',
    'Объекты в плане (с отметкой Z)': 'Objects in plan (with Z elevation)',
    'Объекты для проекции': 'Objects to project',
    'Объекты на разрезе': 'Objects on the section',
    'Объекты на разрезе (чертёж)': 'Objects on the section (drawing)',
    'Объекты с разреза в плане': 'Section objects in plan',
    'Объекты с чертежа разреза': 'Objects from the section drawing',
    'Определение разреза': 'Section definition',
    'Определение разреза (линия с полем vex для других тулз)': 'Section definition (a line with a vex field for other tools)',
    'Определение разреза (линия с полем vex)': 'Section definition (a line with a vex field)',
    'Ось ствола (точка устья)': 'Shaft axis (collar point)',
    'Поверхности на разрезе': 'Surfaces on the section',
    'Поверхности-гриды': 'Surface grids',
    'Поверхности-гриды (маркирующие)': 'Surface grids (markers)',
    'Поле отметки (если геометрия без Z)': 'Elevation field (if the geometry has no Z)',
    'Проецирует объекты (точки, линии, полигоны) на разрез. Для каждой вершины горизонталь - расстояние вдоль линии до её проекции, высота - отметка из 3D-геометрии или из выбранного поля.\n\nЛиния и вертикальный масштаб берутся из определения разреза. Дальние объекты отсекаются коридором. Результат в тех же осях, что и чертёж разреза, кладётся поверх него.\n\nТак на разрез наносят аномалии, точки опробования, трассы, контуры - всё, что нужно увидеть в плоскости разреза.': 'Projects objects (points, lines, polygons) onto the section. For each vertex the horizontal coordinate is the distance along the line to its projection, the height is the elevation from the 3D geometry or from a chosen field.\n\nThe line and the vertical scale come from the section definition. Distant objects are cut off by a corridor. The result is in the same axes as the section drawing and is placed on top of it.\n\nThis is how anomalies, sampling points, traces and outlines are placed on the section - anything to be seen in the section plane.',
    'Радиус ствола, ед. карты': 'Shaft radius, map units',
    'Развёртка стенки (дуга × высота)': 'Wall unwrap (arc × elevation)',
    'Развёртка стенки ствола': 'Shaft wall unwrap',
    'Развёртка: поверхностей %d, окружность %.4g ед, шаг %.4g градусов.': 'Unwrap: %d surfaces, circle %.4g units, step %.4g degrees.',
    'Спроецировано объектов: %d, пропущено вне коридора %d.': 'Objects projected: %d, skipped outside the corridor %d.',
    'Угловой шаг, градусы': 'Angular step, degrees',
    'Цилиндрический разрез - развёртка стенки шахтного ствола. Вокруг оси ствола на заданном радиусе берётся окружность с угловым шагом (по умолчанию 1 градус), и поверхности-гриды выбираются вдоль неё.\n\nРазвёртка ложится в оси длина дуги по окружности - высота. Каждая маркирующая поверхность даёт линию своего пересечения со стенкой ствола: при падении пластов линии наклонены и волнисты.\n\nОсь задаётся точечным слоем (устье), радиус - в единицах карты. Вертикальный масштаб как у разреза.': 'A cylindrical section - the unwrapped wall of a mine shaft. Around the shaft axis at a given radius a circle is taken with an angular step (1 degree by default), and the surface grids are sampled along it.\n\nThe unwrap lies in axes of arc length along the circle and elevation. Each marker surface gives the line of its intersection with the shaft wall: where the beds dip the lines are tilted and wavy.\n\nThe axis is set by a point layer (the collar), the radius is in map units. The vertical scale is as in the section.',
    'Шаг выборки вдоль линии (0 = по ячейке)': 'Sampling step along the line (0 = by cell)',
    'Гидравлический градиент': 'Hydraulic gradient',
    'Изолинии': 'Isolines',
    'Индикаторный кригинг': 'Indicator kriging',
    'Кригинг': 'Kriging',
    'Кригинг с внешним дрейфом': 'External drift kriging',
    'Пример разреза': 'Section example',
    'Пример скважин': 'Example boreholes',
    'Разрез': 'Section',
    'Удельный расход': 'Specific discharge',
    'Инструмент: %s': 'Tool: %s',
    'Создано: %s': 'Created: %s',
    'Угловые вертикали разреза': 'Section corner verticals',
    'Угловые вертикали разреза (чертёж)': 'Section corner verticals (drawing)',
    'Угловые точки разреза': 'Section corner points',
    'Угловые точки разреза (чертёж)': 'Section corner points (drawing)',
    'Горизонтальные оси разреза': 'Section horizontal axes',
    'Горизонтальные оси с отметками (чертёж)': 'Horizontal axes with elevation ticks (drawing)',
    'Количество отметок высоты на осях': 'Number of elevation ticks on the axes',
    'Таблица углов разреза': 'Section corner table',
    'Таблица углов: азимут и расстояние (чертёж)': 'Corner table: azimuth and distance (drawing)',
    '3.02 Скважины на разрезе': '3.02 Boreholes on the section',
    '3.03 Состав пласта на разрезе': '3.03 Bed composition on the section',
    '3.5 Проекция объектов на разрез (бета)': '3.5 Project objects onto the section (beta)',
    '3.6 Спроецировать с разреза (бета)': '3.6 Unproject from the section (beta)',
    '3.7 Развёртка стенки ствола (бета)': '3.7 Unwrap a shaft wall (beta)',
    'Масштаб взят из определения разреза: vex = %.4g.': 'Scale taken from the section definition: vex = %.4g.',
    'Определение разреза (для общего масштаба, опционально)': 'Section definition (for a shared scale, optional)',
    '2.06 Гауссова симуляция (SGS)': '2.06 Gaussian simulation (SGS)',
    'SGS P10': 'SGS P10',
    'SGS P90': 'SGS P90',
    'SGS вероятность превышения': 'SGS exceedance probability',
    'SGS медиана P50': 'SGS median P50',
    'SGS среднее (E-type)': 'SGS mean (E-type)',
    'SGS стандартное отклонение': 'SGS standard deviation',
    'Ансамбль крупный (>400 МБ в памяти). Уменьшите количество реализаций или огрубите ячейку, если не хватит памяти.': 'The ensemble is large (>400 MB in memory). Reduce the number of realizations or coarsen the cell if you run out of memory.',
    'Вариограмма баллов: %s, наггет %.3f, порог %.3f, радиус %.4g (R2=%.2f).': 'Score variogram: %s, nugget %.3f, sill %.3f, range %.4g (R2=%.2f).',
    'Вероятность ВЫШЕ порога (иначе ниже)': 'Probability ABOVE the threshold (otherwise below)',
    'Вероятность превышения порога': 'Exceedance probability',
    'Гауссова симуляция': 'Gaussian simulation',
    'Зерно ГСЧ (0 = случайное)': 'RNG seed (0 = random)',
    'Квантиль P10': 'P10 quantile',
    'Квантиль P90': 'P90 quantile',
    'Макс. количество соседей на узел': 'Max neighbours per node',
    'Медиана P50': 'Median P50',
    'Модель вариограммы баллов': 'Score variogram model',
    'Не удалось подобрать вариограмму нормальных баллов (мало точек или нет структуры).': 'Could not fit a normal-score variogram (too few points or no structure).',
    'Поле значения': 'Value field',
    'Порог отсечки для вероятности (опционально)': 'Cut-off threshold for probability (optional)',
    'Последовательная гауссова симуляция: ансамбль равновероятных реализаций вместо одной сглаженной оценки кригинга. Каждая реализация воспроизводит гистограмму и вариограмму данных и проходит через скважины, поэтому по набору реализаций видна НЕОПРЕДЕЛЁННОСТЬ - разброс, квантили P10/P50/P90, вероятность превышения отсечки. Там, где реализации расходятся, оценка слабая.\n\nВариограмма нормальных баллов подбирается автоматически. Выходы - растры: среднее по ансамблю (E-type), стандартное отклонение (неопределённость), квантили P10/P50/P90 и при заданном пороге карта вероятности превышения. Время растёт с размером грида и числом реализаций - начинайте с грубой ячейки и 50-100 реализаций.': 'Sequential Gaussian simulation: an ensemble of equally probable realizations instead of a single smoothed kriging estimate. Each realization reproduces the data histogram and variogram and passes through the boreholes, so the set of realizations shows the UNCERTAINTY - spread, P10/P50/P90 quantiles, exceedance probability. Where the realizations diverge, the estimate is weak.\n\nThe normal-score variogram is fitted automatically. Outputs are rasters: the ensemble mean (E-type), the standard deviation (uncertainty), the P10/P50/P90 quantiles and, when a threshold is set, an exceedance-probability map. Runtime grows with grid size and the number of realizations - start with a coarse cell and 50-100 realizations.',
    'Радиус поиска (0 = авто, 3 радиуса вариограммы)': 'Search radius (0 = auto, 3 variogram ranges)',
    'Сетка %d x %d, ячейка %.4g, реализаций %d.': 'Grid %d x %d, cell %.4g, realizations %d.',
    'Слишком мало точек для симуляции (нужно хотя бы 8).': 'Too few points for simulation (at least 8 needed).',
    'Среднее по ансамблю (E-type)': 'Ensemble mean (E-type)',
    'Стандартное отклонение (неопределённость)': 'Standard deviation (uncertainty)',
    'Точки (скважины)': 'Points (boreholes)',
    'Количество реализаций': 'Number of realizations',
    'авто': 'auto',
    'гауссова': 'gaussian',
    'сферическая': 'spherical',
    'экспоненциальная': 'exponential',
    '3.05 Пересечение векторов с разрезом': '3.05 Vector intersection with the section',
    '3.07 Проекция объектов на разрез (бета)': '3.07 Project objects onto the section (beta)',
    '3.08 Спроецировать с разреза (бета)': '3.08 Unproject from the section (beta)',
    '3.09 Развёртка стенки ствола (бета)': '3.09 Shaft wall unwrap (beta)',
    '3.10 Создать пример для разреза': '3.10 Create a section example',
    '4.04 Поверхности в 3D (меши) (бета)': '4.04 Surfaces to 3D (meshes) (beta)',
    'Экспортирует гриды поверхностей в mesh-слои стандартного формата '
    '2DM (MDAL). Такие слои понимают профильный инструмент QGIS, '
    'mesh-калькулятор, штатный 3D-вид и сторонние программы, а пачка '
    'горизонтов кровля-подошва уходит в меши без ручных '
    'конвертаций.\n\nК отметкам при записи '
    'применяется вертикальное преобразование Z\' = Z * масштаб + смещение: '
    'масштаб даёт вертикальное преувеличение, смещение разносит горизонты '
    'по высоте. Разнос по Z сдвигает каждый следующий грид на шаг вниз, '
    'превращая слипшуюся стопку в читаемую этажерку. Прореживание '
    'уменьшает количество узлов на крупных гридах.\n\n'
    'Слои загружаются в проект и получают 3D-отображение автоматически. '
    'Если сцена уже открыта, включите новые слои в её списке. Ячейки без '
    'данных пропускаются.':
        'Exports surface grids into mesh layers of the standard 2DM '
        'format (MDAL). Such layers are understood by the QGIS profile '
        'tool, the mesh calculator, the built-in 3D view and third-party '
        'software, and a stack of top-bottom horizons goes to meshes '
        'without manual conversions.\n\nA vertical transform Z\' = Z * scale + offset is '
        'applied on write: the scale gives vertical exaggeration, the offset '
        'separates horizons in height. The Z spacing shifts every next grid '
        'one step down, turning a collapsed stack into a readable shelf. '
        'Thinning reduces the node count on '
        'large grids.\n\nThe layers are loaded into the project and get 3D '
        'rendering automatically. If a scene is already open, enable the new '
        'layers in its layer list. Cells without data are skipped.',
    'Масштаб Z (вертикальное преувеличение)': 'Z scale (vertical exaggeration)',
    'Смещение Z': 'Z offset',
    'Разнос по Z (шаг на каждый следующий грид)': 'Z spacing (step per next grid)',
    'Прореживание узлов (каждый N-й)': 'Node thinning (every Nth)',
    'Папка для мешей (2DM)': 'Folder for meshes (2DM)',
    'Нужен хотя бы один грид.': 'At least one grid is required.',
    'Грид не открылся: %s': 'Grid could not be opened: %s',
    'Грид пропущен (мал или пуст): %s': 'Grid skipped (too small or empty): %s',
    'Меш записан: %s (узлов %d, треугольников %d).':
        'Mesh written: %s (%d nodes, %d triangles).',
    'Слой меша не загрузился: %s': 'Mesh layer failed to load: %s',
    'Поверхности 3D': '3D surfaces',
    '3D-просмотр поверхностей (бета)…': '3D surface viewer (beta)…',
    '3D-просмотр поверхностей': '3D surface viewer',
    '3D-просмотр недоступен в этой установке плагина.':
        'The 3D viewer is not available in this plugin installation.',
    'Isoliner - 3D-просмотр поверхностей (бета)': 'Isoliner - 3D surface viewer (beta)',
    'Обновить сцену': 'Update the scene',
    'Вертикальное преувеличение': 'Vertical exaggeration',
    'Разнос по Z (шаг вниз)': 'Z spacing (step down)',
    'Поверхности (растры проекта):': 'Surfaces (project rasters):',
    'Отметьте хотя бы один растр.': 'Check at least one raster.',
    'Показано поверхностей: %d.': 'Surfaces shown: %d.',
    'Пропущено: %s': 'Skipped: %s',
    'Скважины (точки)': 'Boreholes (points)',
    'Поля отметок': 'Elevation fields',
    '(нет)': '(none)',
    'Скважин: %d.': 'Boreholes: %d.',
    'Прозрачность поверхностей (процентов)': 'Surface transparency (percent)',
    'Сверху': 'Top view',
    'Сбоку': 'Side view',
    'Снимок PNG…': 'PNG snapshot…',
    'Сохранить снимок': 'Save the snapshot',
    'Снимок сохранён: %s': 'Snapshot saved: %s',
    'кровля': 'roof',
    'подошва': 'bottom',
    'Пласт 1-й пром. (каналы: кровля, подошва, содержание, минтип)': '1st industrial bed (bands: roof, bottom, content, mineral type)',
    'Пласт 2-й пром. (каналы: кровля, подошва, содержание, минтип)': '2nd industrial bed (bands: roof, bottom, content, mineral type)',
    'Пласт 1-й пром. (демо)': '1st industrial bed (demo)',
    'Пласт 2-й пром. (демо)': '2nd industrial bed (demo)',

    'содержание': 'content',
    'минтип': 'mineral type',
    'Канал кровли': 'Roof band',
    'Канал подошвы': 'Bottom band',
    'Канал состава': 'Composition band',
    'Канал высот (Z)': 'Elevation band (Z)',
    'Канал атрибута': 'Attribute band',
    'Окраска поверхностей атрибутом (растр)': 'Colour surfaces by attribute (raster)',
    'Канал параметра пласта (0 - палитра)': 'Bed parameter band (0 - palette)',
    'канал %d пласта': "bed's band %d",
    '4.01 Собрать грид пласта (бета)': '4.01 Assemble a bed grid (beta)',
    'Собирает многоканальный грид пласта по конвенции плагина: '
    'канал 1 - кровля, канал 2 - подошва, каналы 3 и далее - '
    'параметры (содержание, минтип и любые другие). Кровля задаёт '
    'сетку результата; подошва и параметры билинейно приводятся к '
    'ней, поэтому исходные гриды могут иметь разные сетки. Имена '
    'каналов записываются в описания: «кровля», «подошва», далее '
    'имена слоёв параметров.\n\nОдин собранный файл кормит '
    '«Состав пласта на разрез» (каналы 1/2/3), 3D-просмотр (тела '
    'пластов) и экспорт в меши - это шаг к блочной модели, где '
    'новые параметры добавляются каналами.':
        'Assembles a multiband bed grid by the plugin convention: '
        'band 1 - the roof, band 2 - the bottom, bands 3 and further - '
        'parameters (content, mineral type and any others). The roof sets '
        'the output grid; the bottom and the parameters are resampled to it '
        'bilinearly, so the input grids may have different grids. The band '
        'names are written into the descriptions: roof, bottom, then the '
        'names of the parameter layers.\n\nOne assembled file feeds Bed '
        'composition on a section (bands 1/2/3), the 3D viewer (bed bodies) '
        'and the mesh export - a step towards a block model where new '
        'parameters are added as bands.',
    'Кровля (растр)': 'Roof (raster)',
    'Подошва (растр)': 'Bottom (raster)',
    'Параметры (растры, берётся канал 1)': 'Parameters (rasters, band 1 is taken)',
    'Грид пласта': 'Bed grid',
    'Грид пласта записан: каналов %d.': 'Bed grid written: %d bands.',
    '4.02 Калькулятор пласта (бета)': '4.02 Bed calculator (beta)',
    'Считает по многоканальному гриду пласта (канал 1 - кровля, '
    'канал 2 - подошва): мощность, объём, тоннаж руды через '
    'плотность и, если задан канал содержания, средневзвешенное по '
    'мощности содержание и тоннаж металла. Сводка - по всей площади '
    'пласта или внутри контура (полигоны подсчётного блока, '
    'домена).\n\nРезультат - грид пласта с дописанными каналами '
    '«мощность» и «запасы руды, т/ячейку» и HTML-отчёт со сводкой. '
    'Ячейки с мощностью меньше нуля (пересечение поверхностей) '
    'обнуляются и считаются отдельно.':
        'Computes over a multiband bed grid (band 1 - the roof, band 2 - '
        'the bottom): the thickness, the volume, the ore tonnage via the '
        'density and, if a content band is set, the thickness-weighted '
        'mean content and the metal tonnage. The summary covers the whole '
        'bed area or the inside of a contour (polygons of a reserve block '
        'or a domain).\n\nThe result is a bed grid with the appended '
        'bands "thickness" and "ore, t/cell" plus an HTML report. Cells '
        'with a negative thickness (crossing surfaces) are zeroed and '
        'counted separately.',
    'Грид пласта (канал 1 кровля, канал 2 подошва)': 'Bed grid (band 1 roof, band 2 bottom)',
    'Канал содержания (пусто - без содержания)': 'Content band (empty - no content)',
    'Плотность руды, т/м³': 'Ore density, t/m³',
    'Контур подсчёта (полигоны, необязательно)': 'Reserve contour (polygons, optional)',
    'Грид пласта с мощностью и запасами': 'Bed grid with thickness and reserves',
    'Отчёт (HTML)': 'Report (HTML)',
    'HTML-файлы (*.html)': 'HTML files (*.html)',
    'Нужен многоканальный грид пласта (каналы 1 и 2).': 'A multiband bed grid is required (bands 1 and 2).',
    'Канал содержания вне грида.': 'The content band is outside the grid.',
    'мощность': 'thickness',
    'запасы руды, т/ячейку': 'ore, t/cell',
    'Площадь подсчёта': 'Computed area',
    'Мощность средняя / мин / макс': 'Thickness mean / min / max',
    'Объём': 'Volume',
    'Плотность': 'Density',
    'Запасы руды': 'Ore reserves',
    'Содержание (взвешенное по мощности)': 'Content (thickness-weighted)',
    'Запасы металла': 'Metal reserves',
    'Ячеек с отрицательной мощностью': 'Cells with a negative thickness',
    'Калькулятор пласта': 'Bed calculator',
    '4.03 Грид пласта в блочную модель (бета)': '4.03 Bed grid to a block model (beta)',
    'Переводит многоканальный грид пласта в блочную модель: точку-'
    'центроид на каждую валидную ячейку. Атрибуты: строка и столбец '
    'ячейки, координаты, верх (top), низ (bot), мощность (thick), '
    'объём (vol), тоннаж руды (ore_t) через плотность и все каналы '
    'параметров под их именами из описаний.\n\nДальше работает '
    'векторный аппарат QGIS: фильтры выражениями, join внешних '
    'таблиц, калькулятор полей - модель наращивается атрибутами без '
    'пересоздания. Контур ограничивает выгрузку подсчётным блоком '
    'или доменом.':
        'Converts a multiband bed grid into a block model: a centroid point '
        'per valid cell. The attributes: the cell row and column, the '
        'coordinates, top, bot, thick, vol, the ore tonnage (ore_t) via the '
        'density and all the parameter bands under their names from the '
        'descriptions.\n\nFrom there the QGIS vector machinery works: '
        'expression filters, joins of external tables, the field calculator '
        '- the model grows by attributes without rebuilding. A contour '
        'limits the export to a reserve block or a domain.',
    'Блочная модель (центроиды)': 'Block model (centroids)',
    'Блочная модель: %s': 'Block model: %s',
    'Блоков выгружено: %d.': 'Blocks exported: %d.',
    '2.07 Фрактальная размерность': '2.07 Fractal dimension',
    'Считает карту фрактальной размерности поверхности '
    'вариограммным методом: в скользящем окне строится лог-лог '
    'вариограмма по лагам 1..N ячеек, её наклон даёт показатель '
    'Хёрста H, размерность D = 3 - H. Гладкие дифференцируемые '
    'участки дают D около 2, изрезанные и шумные - ближе к 3; '
    'перепады D подчёркивают зоны тектонических нарушений, границы '
    'блоков и смену характера рельефа кровли.\n\nВыход - грид D, '
    'готовый для «1.2 Изолинии из растра» (галка в дополнительных '
    'добавит H вторым каналом); глобальные D и '
    'H по всей поверхности печатаются в журнал. Малое окно (5-8 '
    'ячеек) показывает микроструктуру, большое (12-20) - '
    'региональные зоны. Растр должен быть в метрической системе '
    'координат.':
        'Computes a fractal-dimension map of a surface by the variogram '
        'method: a log-log variogram over lags of 1..N cells is built in a '
        'sliding window, its slope gives the Hurst exponent H, the '
        'dimension D = 3 - H. Smooth differentiable areas give D near 2, '
        'rugged and noisy ones tend to 3; the steps of D highlight zones '
        'of tectonic disturbance, block boundaries and changes of the roof '
        'relief character.\n\nThe output is a D grid ready for "1.2 '
        'Isolines from a raster" (an advanced checkbox adds H as band 2); '
        'the global D and H over the whole surface are '
        'printed to the log. A small window (5-8 cells) shows the '
        'microstructure, a large one (12-20) - regional zones. The raster '
        'must be in a metric CRS.',
    'Поверхность (растр)': 'Surface (raster)',
    'Канал высот': 'Elevation band',
    'Полурадиус окна, ячеек': 'Window half-radius, cells',
    'Количество лагов вариограммы': 'Number of variogram lags',
    'Фрактальная размерность (D)': 'Fractal dimension (D)',
    'Записать H вторым каналом': 'Write H as band 2',
    'Изолинии по карте D: «1.2 Изолинии из растра», канал 1.': 'Isolines over the D map: "1.2 Isolines from a raster", band 1.',
    'Окно или лаги велики для этого грида.': 'The window or the lags are too large for this grid.',
    'Глобально: D = %.3f, H = %.3f.': 'Globally: D = %.3f, H = %.3f.',
    '2.08 Box-counting маски': '2.08 Mask box-counting',
    'Классический box-counting: растр бинаризуется порогом '
    '(объект - значения больше порога), маска покрывается ячейками '
    'убывающего размера, наклон log N от log(1/размер) даёт одну '
    'размерность D на всю маску. Линейный объект даёт D около 1, '
    'пятно - около 2, изрезанные контуры замещения или выработок - '
    'между. Точность метода на конечных масках порядка ±0.1 - '
    'используйте его для сравнения масок между собой, а не как '
    'абсолютную меру.\n\nРезультат печатается в журнал вместе с '
    'таблицей размеров и счётов и возвращается числом D.':
        'Classic box-counting: the raster is binarised by a threshold '
        '(the object - values above it), the mask is covered by cells of '
        'a decreasing size, the slope of log N versus log(1/size) gives '
        'one dimension D for the whole mask. A linear object gives D near '
        '1, a blob - near 2, rugged replacement or workings outlines - in '
        'between. The accuracy on finite masks is about ±0.1 - use it to '
        'compare masks with each other rather than as an absolute '
        'measure.\n\nThe result is printed to the log with a table of '
        'sizes and counts and returned as the number D.',
    'Растр маски': 'Mask raster',
    'Порог (объект: значение > порога)': 'Threshold (object: value > threshold)',
    'Канал': 'Band',
    'Размерность D': 'Dimension D',
    'Маска пуста: нет значений выше порога.': 'The mask is empty: no values above the threshold.',
    'Пикселей в маске: %d.': 'Pixels in the mask: %d.',
    'Box-counting: D = %.3f.': 'Box-counting: D = %.3f.',
    '2.09 Размерность линий и границ': '2.09 Line and boundary dimension',
    'Считает размерность каждой линии методом циркуля (Ричардсона): линия проходится хордами убывающего раствора, наклон log N от log r даёт D. Прямая даёт 1, изрезанная линия - больше; для изолиний это диагностика сглаживания: пересглаженные изолинии теряют изрезанность и D падает к единице, а сравнение D до и после сглаживания показывает, сколько геометрии съедено.\n\nВыход - те же линии с полями D и steps (количество шагов минимального циркуля); среднее D печатается в журнал. Полигоны принимаются тоже: меряется внешнее кольцо границы. Короткие линии (меньше 30 вершин или очень малой длины) получают пустое D.':
        'Computes the dimension of every line by the divider (Richardson) '
        'method: the line is walked with chords of a decreasing span, the '
        'slope of log N versus log r gives D. A straight line gives 1, a '
        'rugged one - more; for isolines this is a smoothing diagnostic: '
        'oversmoothed isolines lose their ruggedness and D drops towards '
        'one, and comparing D before and after smoothing shows how much '
        'geometry was eaten.\n\nPolygons are accepted too: the exterior '
        'ring of the boundary is measured. The output is the same lines '
        'with the D and steps fields (the step count of the smallest '
        'divider); the mean D is printed to the log. Short lines (fewer '
        'than 30 vertices or of a very small length) get an empty D.',
    'Линии': 'Lines',
    'Линии или полигоны': 'Lines or polygons',
    'Объекты с размерностью': 'Features with the dimension',
    'Линии с размерностью': 'Lines with the dimension',
    'Среднее D по %d линиям: %.3f.': 'Mean D over %d lines: %.3f.',
    '2.10 Размерность Минковского (векторы)': '2.10 Minkowski dimension (vectors)',
    'Box-counting напрямую по векторам: линии и границы полигонов покрываются сеткой убывающего размера, наклон log N от log(1/размер) даёт размерность Минковского. Прямая линия и гладкая граница дают D около 1, речная сеть - 1.1-1.5, сильно изрезанная береговая линия - до 1.3 и выше.\n\nКаждый объект получает поле D_mink; отдельно считается и печатается в журнал D всего слоя как единого множества - для речной сети это размерность сети целиком, она выше размерности отдельных рукавов. Метод дополняет циркуль из 2.9: циркуль меряет извилистость одной линии, Минковский - заполнение плоскости набором объектов.\n\nПараметры: K - ступеней лесенки размеров (8-12 обычно; слишком большое K уводит мелкие ячейки ниже масштаба детальности линии, и D занижается к 1); сдвигов сетки - случайные смещения с минимальным покрытием, снимают привязку к сетке (3-5); фактор уплотнения - шаг выборки вдоль сегментов в долях ячейки, 0 - только вершины. Каждый объект получает и D_r2 - качество лог-лог аппроксимации: ниже 0.85 оценке доверять нельзя.':
        'Box-counting directly over vectors: lines and polygon boundaries '
        'are covered by a grid of a decreasing size, the slope of log N '
        'versus log(1/size) gives the Minkowski dimension. A straight line '
        'and a smooth boundary give D near 1, a river network - 1.1-1.5, a '
        'heavily rugged coastline - up to 1.3 and above.\n\nEvery feature '
        'gets a D_mink field; separately the D of the whole layer as one '
        'set is computed and printed to the log - for a river network that '
        'is the dimension of the network as a whole, higher than that of '
        'the individual branches. The method complements the divider of '
        '2.09: the divider measures the sinuosity of one line, Minkowski - '
        'the plane filling by a set of features.\n\nParameters: K - the '
        'size-ladder steps (8-12 typically; a too large K takes the small '
        'cells below the line detail scale and D drops towards 1); grid '
        'offsets - random shifts with the minimal cover, remove the grid '
        'alignment (3-5); the densify factor - the sampling step along '
        'the segments as a cell fraction, 0 - vertices only. Every feature '
        'also gets D_r2 - the log-log fit quality: below 0.85 the estimate '
        'cannot be trusted.',
    'Количество размеров сетки': 'Number of grid sizes',
    'Размерность слоя': 'Layer dimension',
    'Размерность Минковского слоя: D = %.3f.': 'Minkowski dimension of the layer: D = %.3f.',
    'Количество размеров сетки (K)': 'Number of grid sizes (K)',
    'Сдвигов сетки на размер': 'Grid offsets per size',
    'Фактор уплотнения выборки (0 - вершины)': 'Sampling densify factor (0 - vertices)',
    'R² аппроксимации слоя': 'Layer fit R²',
    'Размерность Минковского слоя: D = %.3f (R² = %.3f).': 'Minkowski dimension of the layer: D = %.3f (R² = %.3f).',
    'R² ниже 0.85: степенной закон не выдержан, оценке '
    'доверять нельзя (уменьшите K или проверьте данные).':
        'R² below 0.85: the power law does not hold, the estimate cannot '
        'be trusted (reduce K or check the data).',

    '2.11 Создать пример для фракталов (демо)': '2.11 Create a fractal example (demo)',
    'Генерирует учебные объекты для фрактальных инструментов: '
    'ветвящуюся речную сеть (поле order - порядок притока), полигон '
    'водосбора с изрезанной границей и отдельную береговую линию '
    '(срединные смещения). Реки подавайте в 2.10 - размерность '
    'сети; берег и границу водосбора - в 2.09 и 2.10; растеризуйте '
    'водосбор - и он же пример для 2.08.':
        'Generates study features for the fractal tools: a branching river '
        'network (the order field - the tributary order), a basin polygon '
        'with a rugged boundary and a separate coastline (midpoint '
        'displacements). Feed the rivers into 2.10 - the network '
        'dimension; the coast and the basin boundary - into 2.09 and 2.10; '
        'rasterise the basin - and it doubles as an example for 2.08.',
    'Охват': 'Extent',
    'Зерно генератора': 'Generator seed',
    'Реки (демо)': 'Rivers (demo)',
    'Водосбор (демо)': 'Basin (demo)',
    'Берег (демо)': 'Coast (demo)',
    'Рек сгенерировано: %d.': 'Rivers generated: %d.',



    'Isoliner развивается на задачах реальных предприятий. '
    'Если вашему производству не хватает функции - напишите '
    'нам: https://www.informpp.ru/главная-страница/'
    'предприятиям':
        'Isoliner grows on the tasks of real mining operations. '
        'If your production is missing a feature - contact us: '
        'https://www.informpp.ru/главная-страница/предприятиям',



    'Фильтр слоёв…': 'Filter layers…',
    'Все': 'All',
    'Ничего': 'None',
    '0 - палитра': '0 - palette',
    'Слои': 'Layers',
    'Векторы': 'Vectors',
    'Параметры слоя': 'Layer settings',
    'Режим': 'Mode',
    'Авто': 'Auto',
    'Поверхность': 'Surface',
    'Тело пласта': 'Bed body',
    'Канал окраски (0 - палитра)': 'Colour band (0 - palette)',
    'Внешний атрибут (растр)': 'External attribute (raster)',
    'канал %d': 'band %d',
    'Окраска': 'Colouring',
    'Палитра': 'Palette',
    'Поле подписи скважин': 'Borehole label field',





    'Тела пластов (канал 1 кровля, канал 2 подошва)': 'Bed bodies (band 1 roof, band 2 bottom)',
    'Тел пластов: %d.': 'Bed bodies: %d.',
    'Плоскость разреза (линия)': 'Section plane (line)',
    'Плоскостей разреза: %d.': 'Section planes: %d.',





    'Окраска: %s [%.4g … %.4g].': 'Colour: %s [%.4g … %.4g].',



    'Слой для пересечения (линии или полигоны)': 'Layer to intersect (lines or polygons)',
    'Поле подписи (необязательно)': 'Label field (optional)',
    'Чертёж разреза (для высоты рамки, необязательно)': 'Section drawing (for frame height, optional)',
    'Низ диапазона Z (если нет чертежа)': 'Bottom of Z range (if no drawing)',
    'Верх диапазона Z (если нет чертежа)': 'Top of Z range (if no drawing)',
    'Вертикали на разрезе (линии без Z)': 'Verticals on the section (lines without Z)',
    'Точки пересечения (линии с Z)': 'Intersection points (lines with Z)',
    'Полосы зон на разрезе (полигоны)': 'Zone bands on the section (polygons)',
    'Вертикали на разрезе': 'Verticals on the section',
    'Точки на разрезе': 'Points on the section',
    'Полосы зон на разрезе': 'Zone bands on the section',
    'Нужны определение разреза и слой для пересечения.': 'A section definition and a layer to intersect are required.',
    'Пересечения: точек %d, вертикалей %d, полос %d.': 'Intersections: %d points, %d verticals, %d bands.',
    'Для объектов без отметки Z нужна высота рамки: подайте чертёж разреза или задайте диапазон Z. Такие объекты пропущены.': 'Objects without a Z elevation need a frame height: supply the section drawing or set a Z range. Such objects were skipped.',
    'Наносит векторные объекты на разрез по точному пересечению с линией разреза, в осях расстояние-высота.\n\nПравило по типу объекта. Линия БЕЗ отметки высоты (плоская в плане - разлом, граница, контур) даёт вертикаль на всю высоту в станции пересечения: известно где, неизвестно на какой глубине. Линия С отметкой (3D, координата Z - наклонный объект, контур поверхности) даёт точку на реальной высоте в месте пересечения. Полигон (зона в плане - замещение, шахтное поле, лицензия) даёт вертикальную полосу на интервале, где разрез идёт сквозь зону.\n\nЛиния и vex берутся из определения разреза. Высота вертикалей и полос (для объектов без Z) берётся из чертежа разреза, если он подан, иначе из диапазона Z в дополнительных параметрах.\n\nВ отличие от «Проекции объектов на разрез» (приблизительной, по коридору) это точное пересечение.': 'Places vector objects on the section by exact intersection with the section line, in distance-elevation axes.\n\nRule by object type. A line WITHOUT an elevation (flat in plan - fault, boundary, contour) gives a full-height vertical at the crossing station: the where is known, the depth is not. A line WITH an elevation (3D, a Z coordinate - an inclined object, a surface contour) gives a point at the real elevation of the crossing. A polygon (a plan zone - replacement, mine field, licence) gives a vertical band over the interval where the section runs through the zone.\n\nThe line and vex are taken from the section definition. The height of verticals and bands (for objects without Z) is taken from the section drawing if supplied, otherwise from the Z range in the advanced parameters.\n\nUnlike "Project objects onto the section" (approximate, corridor-based) this is an exact intersection.',
    'Разлом для пересечения (2D-линия)': 'Fault for intersection (2D line)',
    'Маркер с отметкой Z (3D-линия)': 'Marker with Z elevation (3D line)',
    'Зона замещения для пересечения (полигон)': 'Replacement zone for intersection (polygon)',
    'Разлом (демо, 2D)': 'Fault (demo, 2D)',
    'Разлом A': 'Fault A',
    'Маркер с Z (демо, 3D)': 'Marker with Z (demo, 3D)',
    'Маркер K (с Z)': 'Marker K (with Z)',
    'Зона (демо, полигон)': 'Zone (demo, polygon)',
    'Зона замещения': 'Replacement zone',
    'Готово: шесть поверхностей (пять пластов: три вмещающих и два промышленных), линия и скважины. Поверхности и линию подайте в «Разрез по линии»; скважины с полями h1...h6 и линию - в «Скважины на разрез». Разлом, маркер с Z и зона - для «Пересечения векторов с разрезом».': 'Done: six surfaces (five beds: three host and two ore), a line and boreholes. Feed the surfaces and the line into "Cross-section along a line"; the boreholes with fields h1...h6 and the line into "Boreholes on the section". The fault, the Z marker and the zone are for "Vector intersection with the section".',
    'Высота рамки из определения: %.4g..%.4g.': 'Frame height from the definition: %.4g..%.4g.',
    'Для объектов без отметки Z нужна высота рамки. Возьмите определение от «Разрез по линии» (в нём уже есть высота) либо подайте чертёж разреза или задайте диапазон Z. Такие объекты пропущены.': 'Objects without a Z elevation need a frame height. Use a definition from "Cross-section along a line" (it already carries the height), or supply the section drawing or set a Z range. Such objects were skipped.',
    'Наносит векторные объекты на разрез по точному пересечению с линией разреза, в осях расстояние-высота.\n\nПравило по типу объекта. Линия БЕЗ отметки высоты (плоская в плане - разлом, граница, контур) даёт вертикаль на всю высоту в станции пересечения: известно где, неизвестно на какой глубине. Линия С отметкой (3D, координата Z - наклонный объект, контур поверхности) даёт точку на реальной высоте в месте пересечения. Полигон (зона в плане - замещение, шахтное поле, лицензия) даёт вертикальную полосу на интервале, где разрез идёт сквозь зону.\n\nЛиния и vex берутся из определения разреза. Высота рамки тоже берётся из определения (его пишет «Разрез по линии»), поэтому для объектов без Z подавать ничего не нужно. Если в определении высоты нет, она берётся из чертежа разреза или из диапазона Z в дополнительных параметрах.\n\nВ отличие от «Проекции объектов на разрез» (приблизительной, по коридору) это точное пересечение.': 'Places vector objects on the section by exact intersection with the section line, in distance-elevation axes.\n\nRule by object type. A line WITHOUT an elevation (flat in plan - fault, boundary, contour) gives a full-height vertical at the crossing station: the where is known, the depth is not. A line WITH an elevation (3D, a Z coordinate - an inclined object, a surface contour) gives a point at the real elevation of the crossing. A polygon (a plan zone - replacement, mine field, licence) gives a vertical band over the interval where the section runs through the zone.\n\nThe line and vex are taken from the section definition. The frame height is taken from the definition too (written by "Cross-section along a line"), so nothing needs to be supplied for objects without Z. If the definition has no height, it is taken from the section drawing or from the Z range in the advanced parameters.\n\nUnlike "Project objects onto the section" (approximate, corridor-based) this is an exact intersection.',
    'Слои для пересечения (линии и полигоны)': 'Layers to intersect (lines and polygons)',
    'Нужны определение разреза и хотя бы один слой для пересечения.': 'A section definition and at least one layer to intersect are required.',
    '3.06 Пересечение TIN с разрезом': '3.06 Intersect a TIN with the section',
    'Грани TIN (слои 3D-полигонов, PolygonZ)': 'TIN faces (layers of 3D polygons, PolygonZ)',
    'Меш-слой (2.5D, для общности)': 'Mesh layer (2.5D, for generality)',
    'Трасса TIN на разрезе (чертёж)': 'TIN trace on the section (drawing)',
    'Трасса TIN (3D)': 'TIN trace (3D)',
    'Трасса TIN на разрезе': 'TIN trace on the section',
    'Нужны определение разреза и хотя бы один слой граней или меш.': 'A section definition and at least one faces layer or a mesh are required.',
    'Граней обработано: %d, сегментов трассы: %d.': 'Faces processed: %d, trace segments: %d.',
    'Слой «%s» без 3D-полигонов (нет Z) - пропущен.': 'Layer "%s" has no 3D polygons (no Z) - skipped.',
    'Меш не прочитан: %s': 'Mesh not read: %s',
    'Трасса пуста: TIN не пересекает линию разреза или нет 3D-граней.': 'Empty trace: the TIN does not cross the section line or there are no 3D faces.',
    'Режет TIN (поверхность из 3D-треугольников) разрезом и кладёт трассу на чертёж в осях расстояние-высота.\n\nГлавное отличие от «Пересечения поверхностей» (3.04, гриды): грид это z = f(x,y), одно значение на точку, опрокинутое он не возьмёт. TIN из настоящих 3D-граней может нависать: над одной станцией несколько отметок, и трасса заворачивается - складки с опрокинутыми крыльями ложатся как есть.\n\nВход - слои 3D-полигонов (PolygonZ, грани TIN; не треугольники разбиваются веером) и/или меш-слой. Линия и vex берутся из определения разреза, высота - с самих граней, поэтому для TIN ничего задавать не нужно.\n\nВнимание: меш QGIS это 2.5D (z как скаляр на вершине), опрокинутое в нём не представимо. Нависание дают только настоящие 3D-грани от геомоделлера.': 'Cuts a TIN (a surface of 3D triangles) with the section and places the trace on the drawing in distance-elevation axes.\n\nThe key difference from "Intersect surfaces" (3.04, grids): a grid is z = f(x,y), one value per point, and cannot represent overturning. A TIN of true 3D faces can overhang: several elevations above one station, and the trace folds back - recumbent folds with overturned limbs come out as they are.\n\nInputs are layers of 3D polygons (PolygonZ, TIN faces; non-triangles are fan-split) and/or a mesh layer. The line and vex come from the section definition, the height from the faces themselves, so nothing needs to be set for a TIN.\n\nNote: a QGIS mesh is 2.5D (z as a scalar per vertex), overturning is not representable in it. Overhangs come only from true 3D faces from a geomodeller.',
    'Готово: шесть поверхностей (пять пластов: три вмещающих и два промышленных), линия и скважины. Поверхности и линию подайте в «Разрез по линии»; скважины с полями h1...h6 и линию - в «Скважины на разрез». Разлом, маркер с Z и зона - для «Пересечения векторов с разрезом», опрокинутая TIN - для «Пересечения TIN с разрезом».': 'Done: six surfaces (five beds: three host and two ore), a line and boreholes. Feed the surfaces and the line into "Cross-section along a line"; the boreholes with fields h1...h6 and the line into "Boreholes on the section". The fault, the Z marker and the zone are for "Vector intersection with the section", the overturned TIN for "Intersect a TIN with the section".',
    'Опрокинутая TIN (3D-грани для пересечения)': 'Overturned TIN (3D faces for intersection)',
    'Опрокинутая TIN (демо)': 'Overturned TIN (demo)',
    'Складка (опрокинутая)': 'Fold (overturned)',
    'О плагине…': 'About…',
    'О плагине': 'About',
    'Руководство (PDF)': 'Manual (PDF)',
    'История изменений': 'Changelog',
    'Версия %s': 'Version %s',
    'Исходный код': 'Source code',
    'Сообщить об ошибке': 'Report an issue',
    'Руководство не найдено.': 'Manual not found.',
}
