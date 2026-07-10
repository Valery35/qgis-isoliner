# -*- coding: utf-8 -*-
#
# Isoliner - грид и изолинии (QGIS).
# © 2026 ООО «Информ++» (www.informpp.ru).
# SPDX-License-Identifier: GPL-2.0-or-later
#
"""Генератор демонстрационных геофизических профилей.

Два режима:

- Электроразведка: параллельные профили с пикетами, кажущимся сопротивлением
  ρк (Ом·м), потенциалом естественного поля ЕП (мВ) и вызванной поляризацией
  ВП (мВ/В). В данные заложена низкоомная аномалия компактным пятном, а не
  полосой, поэтому профили не синхронны и при интерполяции виден локальный
  очаг обводнения или замещения.

- Оседания (мульда): те же профили реперов с оседанием (мм) в виде мульды
  сдвижения над отработанной площадью, по нескольким турам наблюдений. По
  одним пикетам считается разность между турами.

Дополнительные поля: отметка z (м) и истинное значение без шума (для проверки
точности интерполяции против эталона). Чистый NumPy, без QGIS, под
headless-тестом (tests/test_geodemo.py).
"""
import numpy as np


def pk_label(m):
    """Пикет в метрах -> метка ПК (100-метровые пикеты): 520 -> «ПК5+20»."""
    m = float(m)
    n = int(m // 100)
    plus = int(round(m - n * 100.0))
    if plus >= 100:
        n += 1
        plus -= 100
    return "ПК%d+%02d" % (n, plus)


def _blob(px, y, cx, cy, sx, sy):
    """Компактное 2D-пятно (гаусс) в точках профиля на высоте y."""
    return np.exp(-(((px - cx) ** 2) / (2.0 * sx * sx)
                    + ((y - cy) ** 2) / (2.0 * sy * sy)))


def _geometry(xmin, ymin, xmax, ymax, n_profiles, picket_step):
    """Пикеты вдоль профилей. Возвращает px (метки X), pys (Y профилей),
    x0 (начало профиля), length, h."""
    w = float(xmax - xmin)
    h = float(ymax - ymin)
    if w <= 0 or h <= 0:
        raise ValueError("пустой охват")
    mx = 0.05 * w
    my = 0.10 * h
    x0 = xmin + mx
    length = (xmax - mx) - x0
    step = float(picket_step) if picket_step > 0 else max(length / 40.0, 1.0)
    npk = max(2, int(length // step) + 1)
    px = x0 + np.arange(npk) * step
    n_profiles = max(1, int(n_profiles))
    if n_profiles == 1:
        pys = np.array([(ymin + ymax) / 2.0])
    else:
        pys = np.linspace(ymin + my, ymax - my, n_profiles)
    return px, pys, x0, length, h


def _relief(px, y, x0, length, h, ymin, z_base, z_amp):
    """Плавная отметка поверхности (м): база плюс низкочастотная волна."""
    u = (px - x0) / max(length, 1e-9)
    v = (y - ymin) / max(h, 1e-9)
    return z_base + z_amp * (np.sin(2.0 * np.pi * (u * 1.3 + 0.15))
                             * np.cos(np.pi * (v * 1.1 + 0.2)))


def gen_profiles(xmin, ymin, xmax, ymax, n_profiles=4, picket_step=20.0,
                 rho_bg=60.0, rho_min=10.0, sp_amp=-100.0, vp_bg=5.0,
                 vp_amp=15.0, noise=0.06, z_base=120.0, z_amp=15.0, seed=0):
    """Электроразведка. Возвращает dict массивов: profile, picket_m, x, y, z,
    rho_k, rho_true, sp, vp."""
    rng = np.random.default_rng(seed if seed and seed > 0 else None)
    px, pys, x0, length, h = _geometry(xmin, ymin, xmax, ymax,
                                       n_profiles, picket_step)
    npk = px.size

    # пятно на одном из внутренних профилей (не с краю), смещено по X
    ip_anom = pys.size // 2 if pys.size < 3 else int(rng.integers(1, pys.size - 1))
    cx = x0 + rng.uniform(0.35, 0.65) * length
    cy = pys[ip_anom] + rng.uniform(-0.4, 0.4) * (h / max(pys.size, 1))
    sx = 0.12 * length
    sy = 0.16 * h                                    # компактно по Y -> пятно

    # лёгкий 2D-фон, чтобы профили не были синхронны
    fx = rng.uniform(0.8, 1.6)
    fy = rng.uniform(0.6, 1.2)
    phx = rng.uniform(0, 2 * np.pi)
    phy = rng.uniform(0, 2 * np.pi)

    lr_bg = np.log10(max(rho_bg, 1e-3))
    lr_min = np.log10(max(rho_min, 1e-3))
    depth = lr_bg - lr_min

    prof, pkm, xs, ys, zs = [], [], [], [], []
    rho, rho_t, sp, vp = [], [], [], []
    for ip, y in enumerate(pys, start=1):
        g = _blob(px, y, cx, cy, sx, sy)
        u = (px - x0) / max(length, 1e-9)
        v = (y - ymin) / max(h, 1e-9)
        bg = 0.08 * np.sin(2 * np.pi * fx * u + phx) * np.cos(
            2 * np.pi * fy * v + phy)
        lr_true = lr_bg - depth * g + bg
        lr = lr_true + rng.normal(0.0, noise, npk)
        prof.extend([ip] * npk)
        pkm.extend((px - x0).tolist())
        xs.extend(px.tolist())
        ys.extend([y] * npk)
        zs.extend(_relief(px, y, x0, length, h, ymin, z_base, z_amp).tolist())
        rho.extend(np.power(10.0, lr).tolist())
        rho_t.extend(np.power(10.0, lr_true).tolist())
        sp.extend((sp_amp * g + rng.normal(0.0, 3.0, npk)).tolist())
        vp.extend((vp_bg + vp_amp * g + rng.normal(0.0, 1.0, npk)).tolist())
    return dict(profile=np.array(prof, int),
                picket_m=np.array(pkm, float),
                x=np.array(xs, float), y=np.array(ys, float),
                z=np.array(zs, float),
                rho_k=np.array(rho, float), rho_true=np.array(rho_t, float),
                sp=np.array(sp, float), vp=np.array(vp, float))


def gen_subsidence(xmin, ymin, xmax, ymax, n_profiles=4, picket_step=20.0,
                   subs_max=400.0, n_tours=2, noise=4.0, positive=False,
                   z_base=120.0, z_amp=15.0, seed=0):
    """Оседания. Мульда сдвижения (мм) над отработанной площадью, по n_tours
    турам. Знак единый: вниз (отрицательное) при positive=False или величина
    (положительное) при positive=True. По краям строго нули: шум гаснет к краю
    и хвост мульды обнуляется. Предел по модулю - 2 м. Возвращает dict:
    profile, picket_m, x, y, z, tour, settle, settle_true."""
    rng = np.random.default_rng(seed if seed and seed > 0 else None)
    px, pys, x0, length, h = _geometry(xmin, ymin, xmax, ymax,
                                       n_profiles, picket_step)
    npk = px.size
    n_tours = max(1, int(n_tours))
    subs_max = min(abs(float(subs_max)), 2000.0)      # не больше 2 м
    sgn = 1.0 if positive else -1.0

    # мульда - компактная чаша по центру отработки
    cx = x0 + rng.uniform(0.4, 0.6) * length
    cy = (ymin + ymax) / 2.0 + rng.uniform(-0.1, 0.1) * h
    sx = 0.20 * length
    sy = 0.22 * h

    prof, pkm, xs, ys, zs = [], [], [], [], []
    tour, settle, settle_t = [], [], []
    for t in range(1, n_tours + 1):
        depth_t = subs_max * (t / float(n_tours))     # мульда углубляется
        for ip, y in enumerate(pys, start=1):
            g = _blob(px, y, cx, cy, sx, sy)
            g = np.where(g < 0.02, 0.0, g)             # строгие нули по краям
            s_true = sgn * depth_t * g
            s = s_true + rng.normal(0.0, noise, npk) * g   # шум гаснет к краю
            prof.extend([ip] * npk)
            pkm.extend((px - x0).tolist())
            xs.extend(px.tolist())
            ys.extend([y] * npk)
            zs.extend(_relief(px, y, x0, length, h, ymin,
                              z_base, z_amp).tolist())
            tour.extend([t] * npk)
            settle.extend(s.tolist())
            settle_t.extend(s_true.tolist())
    return dict(profile=np.array(prof, int),
                picket_m=np.array(pkm, float),
                x=np.array(xs, float), y=np.array(ys, float),
                z=np.array(zs, float), tour=np.array(tour, int),
                settle=np.array(settle, float),
                settle_true=np.array(settle_t, float))
