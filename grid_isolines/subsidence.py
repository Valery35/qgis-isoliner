# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Деформации земной поверхности по оседаниям. Ядро без QGIS.

Формулы взяты из «Указаний по защите рудников от затопления и охране
подрабатываемых объектов на Верхнекамском месторождении» (ГИ УрО РАН),
раздел 4. Шестая редакция для ПАО «Уралкалий» и Указания Талицкого ГОКа
2024 года в этой части совпадают, редакция 2014 года отличается только
формулой горизонтальной деформации (l₀ вместо L).

Единицы ядра - метры для длин и безразмерные деформации. Пересчёт в мм/м
и 10⁻⁶ 1/м, в которых их показывает база наблюдений, делает вызывающий.

Кривизна по п. 4.24 - это вторая разность оседаний, положительных вниз:
K = (iₙ - iₙ₋₁) / l꜀. В центре мульды она отрицательна (сжатие), у края
положительна (растяжение), как в таблице 2 редакции 2014 года. Наклон
берётся по модулю.
"""
import math

import numpy as np

# Типовая функция распределения относительного оседания S(z) в главных
# сечениях полумульды, таблица 1 Указаний (одинакова во всех редакциях).
S_Z = np.round(np.arange(21) * 0.05, 2)
S_TABLE = np.array([1.000, 0.980, 0.950, 0.900, 0.840, 0.740, 0.655, 0.540,
                    0.440, 0.360, 0.290, 0.235, 0.188, 0.150, 0.120, 0.093,
                    0.072, 0.052, 0.034, 0.017, 0.000])

# Коэффициенты приведения к 15-метровому интервалу, п. 4.19.2, 4.20.2, 4.23.
BETA_TILT = 0.1414
BETA_CURV = 0.1768
BETA_EPS = 0.3535

# Переход от кривизны к горизонтальной деформации, п. 4.27.
BETA_E = 0.05
BETA_OE = 1.8
ALPHA_OE = 3.8
K_REF = 1e-4        # 1/м, λ = |K| / 1e-4

# Углы сдвижения, п. 4.8-4.10: граничный 55° у постоянных границ, 65° у
# временных и длительно остановленных; угол полных сдвижений 55°.
DELTA_PERMANENT = 55.0
DELTA_TEMPORARY = 65.0
PSI_FULL = 55.0


def _q(length, beta):
    """Общая форма коэффициента приведения: 1 + β·√(l/15 - 1)."""
    ll = np.clip(np.asarray(length, dtype=float), 15.0, 45.0)
    q = 1.0 + beta * np.sqrt(ll / 15.0 - 1.0)
    return float(q) if q.ndim == 0 else q


def q_tilt(length):
    """qᵢ: 1.0 при l ≤ 15 м, 1.2 при l ≥ 45 м."""
    return _q(length, BETA_TILT)


def q_curv(length):
    """qₖ: 1.0 при l ≤ 15 м, 1.25 при l ≥ 45 м."""
    return _q(length, BETA_CURV)


def q_eps(length):
    """q_ε: 1.0 при l ≤ 15 м, 1.5 при l ≥ 45 м."""
    return _q(length, BETA_EPS)


def m_e(curv):
    """Коэффициент перехода от кривизны к горизонтальной деформации.

    mₑ = βₑ·βₖ, βₖ = 1 + βₒₑ(1 - λ)^αₒₑ при λ < 1, иначе 1; λ = |K|/1e-4.
    Работает и с числом, и с массивом, nan проходит насквозь.
    """
    k = np.abs(np.asarray(curv, dtype=float))
    lam = k / K_REF
    with np.errstate(invalid="ignore"):
        bk = np.where(lam < 1.0,
                      1.0 + BETA_OE * np.power(np.clip(1.0 - lam, 0.0, 1.0),
                                               ALPHA_OE),
                      1.0)
    out = BETA_E * bk
    out = np.where(np.isfinite(k), out, np.nan)
    return float(out) if out.ndim == 0 else out


def eps_from_curvature(curv, length):
    """ε = mₑ·K·L (п. 4.34), знак берётся от кривизны."""
    k = np.asarray(curv, dtype=float)
    out = m_e(k) * k * float(length)
    return float(out) if np.ndim(out) == 0 else out


def half_trough_length(depth, delta=DELTA_PERMANENT, psi=PSI_FULL):
    """L = (ctg δ₀ + ctg ψ)·H, п. 4.10."""
    return (1.0 / math.tan(math.radians(delta))
            + 1.0 / math.tan(math.radians(psi))) * float(depth)


# --- типовая функция ------------------------------------------------------

def _pchip_slopes(x, y):
    """Наклоны кусочно-кубического монотонного сплайна (Фрич-Карлсон)."""
    h = np.diff(x)
    d = np.diff(y) / h
    m = np.zeros_like(y)
    for k in range(1, len(y) - 1):
        if d[k - 1] * d[k] <= 0:
            m[k] = 0.0
        else:
            w1 = 2 * h[k] + h[k - 1]
            w2 = h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    # на оси мульды S симметрична, наклон нулевой; на краю - односторонний
    m[0] = 0.0
    m[-1] = d[-1]
    return m


_S_SLOPES = _pchip_slopes(S_Z, S_TABLE)


def s_func(z):
    """S(z) по таблице 1 монотонным кубическим сплайном.

    S = 1 при z ≤ 0 (плоское дно мульды) и 0 при z ≥ 1. В узлах таблицы
    значение точное, между узлами S убывает монотонно, как и положено
    функции оседания.
    """
    z = np.asarray(z, dtype=float)
    zz = np.clip(z, 0.0, 1.0)
    idx = np.clip(np.searchsorted(S_Z, zz, side="right") - 1, 0, len(S_Z) - 2)
    x0 = S_Z[idx]
    h = S_Z[idx + 1] - x0
    t = (zz - x0) / h
    y0, y1 = S_TABLE[idx], S_TABLE[idx + 1]
    m0, m1 = _S_SLOPES[idx], _S_SLOPES[idx + 1]
    h00 = (2 * t + 1) * (1 - t) ** 2
    h10 = t * (1 - t) ** 2
    h01 = t * t * (3 - 2 * t)
    h11 = t * t * (t - 1)
    out = h00 * y0 + h10 * h * m0 + h01 * y1 + h11 * h * m1
    return float(out) if out.ndim == 0 else out


# --- деформации по реперам профильной линии ------------------------------

def profile_deformations(dist, eta, reduce=True):
    """Наклоны интервалов и кривизна в реперах, п. 4.22 и 4.24.

    dist - положения реперов вдоль профиля, м, по возрастанию;
    eta  - оседания, м, положительные вниз.

    Возвращает (tilt, curv): tilt длиной n-1 (интервалы), безразмерный, со
    знаком по ходу профиля (рост оседания положителен), как в ведомости
    базы наблюдений; curv длиной n (в крайних реперах nan), со знаком.
    Кривизна считается по знаковым наклонам, иначе у оси мульды, где наклон
    меняет направление, она теряла бы знак.
    """
    d = np.asarray(dist, dtype=float)
    e = np.asarray(eta, dtype=float)
    n = len(d)
    if n < 2:
        return np.array([]), np.full(n, np.nan)
    ln = np.diff(d)
    ok = ln > 0
    slope = np.full(n - 1, np.nan)
    slope[ok] = np.diff(e)[ok] / ln[ok]
    qi = q_tilt(ln) if reduce else np.ones_like(ln)
    signed = slope * qi
    curv = np.full(n, np.nan)
    for k in range(1, n - 1):
        lc = 0.5 * (ln[k - 1] + ln[k])
        if lc <= 0 or not (np.isfinite(slope[k - 1]) and np.isfinite(slope[k])):
            continue
        qk = q_curv(lc) if reduce else 1.0
        # по п. 4.24 разность берётся по наклонам, уже приведённым к 15 м
        curv[k] = (signed[k] - signed[k - 1]) / lc * qk
    return signed, curv


# --- деформации по гриду ---------------------------------------------------

def _bilinear(z, fr, fc):
    """Значения растра в дробных индексах (центры ячеек в целых).

    За краем растра nan, пропуск в любой из четырёх ячеек даёт nan.
    """
    ny, nx = z.shape
    fr = np.asarray(fr, dtype=float)
    fc = np.asarray(fc, dtype=float)
    out = np.full(fr.shape, np.nan)
    ok = (fr >= 0) & (fr <= ny - 1) & (fc >= 0) & (fc <= nx - 1)
    if ny < 2 or nx < 2 or not ok.any():
        return out
    r0 = np.minimum(np.floor(fr[ok]).astype(int), ny - 2)
    c0 = np.minimum(np.floor(fc[ok]).astype(int), nx - 2)
    a = fr[ok] - r0
    b = fc[ok] - c0
    out[ok] = (z[r0, c0] * (1 - a) * (1 - b) + z[r0, c0 + 1] * (1 - a) * b
               + z[r0 + 1, c0] * a * (1 - b) + z[r0 + 1, c0 + 1] * a * b)
    return out


def grid_deformations(eta, cell, base, reduce=True):
    """Наклон и кривизна по гриду оседаний на базе ``base`` метров.

    Разности берутся не по соседним ячейкам, а на расстоянии базы, как по
    реперам: наклон по двум точкам на ±base/2, кривизна по трём точкам на
    ±base. Так число с грида сравнимо с числом по профильной линии и с
    допусками, заданными для 15-метрового интервала. Ячейка мельче базы
    только уточняет положение, на величину она не влияет.

    eta - оседания, м, положительные вниз; nan - пропуски.
    Возвращает словарь растров (безразмерных):
      tilt   - наибольший наклон, по модулю;
      azimuth - азимут наибольшего наклона, градусы от севера по часовой,
                в сторону роста оседания (к центру мульды); nan, где плоско;
      k_dir  - кривизна вдоль направления наибольшего наклона;
      k_max, k_min - главные кривизны (k_max ≥ k_min);
      k_max_az - азимут главной кривизны k_max.
    Строки растра идут сверху вниз (север наверху), как в GeoTIFF.
    """
    z = np.asarray(eta, dtype=float)
    ny, nx = z.shape
    rr, cc = np.mgrid[0:ny, 0:nx].astype(float)
    h = float(base) / float(cell)          # база в ячейках
    hh = h / 2.0

    def at(dr, dc):
        return _bilinear(z, rr + dr, cc + dc)

    # восток - вдоль столбцов вправо, север - вверх по строкам (минус ряд)
    e_p, e_m = at(0, hh), at(0, -hh)
    n_p, n_m = at(-hh, 0), at(hh, 0)
    gx = (e_p - e_m) / base            # d eta / d east
    gy = (n_p - n_m) / base            # d eta / d north
    qi = q_tilt(base) if reduce else 1.0
    qk = q_curv(base) if reduce else 1.0
    tilt = np.hypot(gx, gy) * qi
    with np.errstate(invalid="ignore"):
        az = np.degrees(np.arctan2(gx, gy)) % 360.0
    flat = ~(tilt > 1e-12)
    az = np.where(flat, np.nan, az)

    c0 = z
    exx = (at(0, h) - 2 * c0 + at(0, -h)) / base ** 2
    eyy = (at(-h, 0) - 2 * c0 + at(h, 0)) / base ** 2
    # смешанная производная по четырём углам квадрата со стороной base
    exy = (at(-hh, hh) - at(hh, hh) - at(-hh, -hh) + at(hh, -hh)) / base ** 2
    # По п. 4.24 кривизна берётся по наклонам, уже приведённым к 15 м, и
    # сама приводится ещё раз: множитель qᵢ·qₖ, как у профильной линии.
    exx *= qi * qk
    eyy *= qi * qk
    exy *= qi * qk
    with np.errstate(invalid="ignore", divide="ignore"):
        norm = np.hypot(gx, gy)
        ux = np.where(norm > 0, gx / norm, np.nan)
        uy = np.where(norm > 0, gy / norm, np.nan)
    tr = 0.5 * (exx + eyy)
    k_dir = exx * ux * ux + 2 * exy * ux * uy + eyy * uy * uy
    # На плоском дне направления наклона нет. Там берётся средняя
    # кривизна: она близка к нулю и не оставляет пропуска посреди мульды.
    k_dir = np.where(np.isfinite(ux), k_dir, tr)
    disc = np.sqrt(np.maximum(0.25 * (exx - eyy) ** 2 + exy ** 2, 0.0))
    k_max = tr + disc
    k_min = tr - disc
    with np.errstate(invalid="ignore"):
        theta = 0.5 * np.arctan2(2 * exy, exx - eyy)   # от оси восток
    k_max_az = (90.0 - np.degrees(theta)) % 180.0
    return {"tilt": tilt, "azimuth": az, "k_dir": k_dir,
            "k_max": k_max, "k_min": k_min, "k_max_az": k_max_az}


def sample_along(eta, gt, pts):
    """Значения растра в точках карты (билинейно). gt - как у GDAL."""
    z = np.asarray(eta, dtype=float)
    pts = np.asarray(pts, dtype=float).reshape(-1, 2)
    fc = (pts[:, 0] - gt[0]) / gt[1] - 0.5
    fr = (pts[:, 1] - gt[3]) / gt[5] - 0.5
    return _bilinear(z, fr, fc)


# --- демо-мульда ------------------------------------------------------------

def demo_trough(nx, ny, cell, depth, d11, d12, eta_max,
                delta=DELTA_PERMANENT, psi=PSI_FULL):
    """Мульда над прямоугольной выработкой по типовой функции.

    Выработка D11 × D12 м стоит в центре растра, длинной стороной по оси
    восток-запад. Полная подработка (χ ≥ 1.4) даёт плоское дно размером
    D - 2·ctgψ·H, от его края полумульда длиной L = (ctgδ₀ + ctgψ)·H.
    Оседание η = ηₘ·S(zx)·S(zy): в главных сечениях через дно второй
    множитель равен единице, и профиль совпадает с табличным точно.

    Возвращает (eta, info): eta в метрах, положительное вниз, строки сверху
    вниз; info - словарь с L, размерами дна и χ по обеим осям.
    """
    ctg_d = 1.0 / math.tan(math.radians(delta))
    ctg_p = 1.0 / math.tan(math.radians(psi))
    L = (ctg_d + ctg_p) * depth
    px = max(0.0, d11 / 2.0 - ctg_p * depth)      # полудлина дна
    py = max(0.0, d12 / 2.0 - ctg_p * depth)
    xs = (np.arange(nx) + 0.5) * cell - nx * cell / 2.0
    ys = (np.arange(ny) + 0.5) * cell - ny * cell / 2.0
    zx = np.maximum(np.abs(xs) - px, 0.0) / L
    zy = np.maximum(np.abs(ys) - py, 0.0) / L
    eta = eta_max * np.outer(s_func(zy), s_func(zx))
    info = {"L": L, "plateau_x": 2 * px, "plateau_y": 2 * py,
            "chi_x": d11 / depth, "chi_y": d12 / depth}
    return eta, info
