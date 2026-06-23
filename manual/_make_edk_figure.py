# -*- coding: utf-8 -*-
"""Иллюстрация для руководства: обычный кригинг vs кригинг с внешним дрейфом.
Считается НАСТОЯЩИМ ядром плагина (kb2d), а не имитацией: build_grid + ExternalDrift.
Делает две языковые версии: edk_result.png (RU), edk_result_en.png (EN)."""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "grid_isolines"))
import kb2d  # noqa: E402

W, H = 1000.0, 800.0
rng = np.random.default_rng(7)


def s_fun(x, y):
    """Сторонняя поверхность s (дрейф): известна всюду, выраженная региональная
    форма (наклон + крупная волна). Имитирует подстилающий пласт / атрибут."""
    return (-200.0 + 0.05 * x - 0.06 * y
            + 38.0 * np.sin(x / 250.0) + 26.0 * np.cos(y / 200.0))


def local_fun(x, y):
    """Локальная мелкомасштабная структура (то, что кригуется по остаткам)."""
    bumps = [(250, 230, 16, 120), (760, 560, -14, 150), (520, 660, 12, 110)]
    v = np.zeros_like(np.asarray(x, float))
    for cx, cy, amp, rad in bumps:
        v += amp * np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * rad ** 2)))
    return v


A0, A1 = 12.0, 1.0


def dz_true(x, y):
    return A0 + A1 * s_fun(x, y) + local_fun(x, y)


# --- разреженные скважины с намеренным разрывом (data void), где видна разница --
cand_x = rng.uniform(0, W, 140)
cand_y = rng.uniform(0, H, 140)
void = (cand_x > 540) & (cand_x < 860) & (cand_y > 250) & (cand_y < 560)
keep = ~void
xw, yw = cand_x[keep][:52], cand_y[keep][:52]
dz = dz_true(xw, yw) + rng.normal(0, 1.2, len(xw))

# --- сетка как в build_grid (строка 0 = север) ------------------------------
cell = 10.0
nx, ny = int(W / cell), int(H / cell)
xmn, ymn = 0.5 * cell, 0.5 * cell
nodata = -9999.0
ix = np.arange(nx)
iy = ny - np.arange(ny)                      # для строки row: iy = ny-row
X = (xmn + ix * cell)[None, :] * np.ones((ny, 1))
Y = (ymn + (iy - 1) * cell)[:, None] * np.ones((1, nx))
s_grid = s_fun(X, Y)

# --- обычный (ординарный) кригинг по dz -------------------------------------
v_dz = float(np.var(dz))
vg_ok = kb2d.Variogram(0.12 * v_dz, [{"it": 1, "cc": 0.88 * v_dz,
                                      "aa": W / 3.0, "ang": 0.0, "anis": 1.0}])
ok = kb2d.build_grid(xw, yw, dz, vg_ok, 1, 0.0, 1, 24, 1e18, nodata,
                     xmn, ymn, cell, nx, ny)
ok = np.where(ok != nodata, ok, np.nan)

# --- кригинг с внешним дрейфом: дрейф по s, кригинг остатков, дрейф обратно ---
s_w = s_fun(xw, yw)
drift = kb2d.ExternalDrift.fit(s_w, dz, 1)
res = drift.residuals(s_w, dz)
v_r = float(np.var(res))
vg_ked = kb2d.Variogram(0.2 * v_r, [{"it": 1, "cc": 0.8 * v_r,
                                     "aa": W / 4.0, "ang": 0.0, "anis": 1.0}])
ked = kb2d.build_grid(xw, yw, res, vg_ked, 1, 0.0, 1, 24, 1e18, nodata,
                      xmn, ymn, cell, nx, ny)
m = ked != nodata
ked_full = np.full((ny, nx), np.nan)
ked_full[m] = ked[m] + drift(s_grid.ravel()).reshape(ny, nx)[m]

share = 100.0 * (1.0 - v_r / v_dz)
print("wells: %d   drift removed %.0f%% of variance" % (len(xw), share))

# --- общий масштаб цвета для панелей dz -------------------------------------
allv = np.concatenate([ok[np.isfinite(ok)], ked_full[np.isfinite(ked_full)]])
vmin, vmax = np.percentile(allv, 1), np.percentile(allv, 99)
ext = [0, W, 0, H]
cmap = "turbo"

TEXT = {
    "ru": {
        "s": "Внешняя поверхность (дрейф) s",
        "ok": "Обычный кригинг по dz",
        "ked": "Кригинг с внешним дрейфом по dz",
        "wells": "%d скважин (точки)" % len(xw),
        "note": "В разрыве данных обычный кригинг сглаживается к среднему,\n"
                "а кригинг с дрейфом повторяет форму внешней поверхности.",
    },
    "en": {
        "s": "External surface (drift) s",
        "ok": "Ordinary kriging of dz",
        "ked": "External drift kriging of dz",
        "wells": "%d wells (points)" % len(xw),
        "note": "In the data void, ordinary kriging relaxes to the mean,\n"
                "while external drift kriging follows the external surface.",
    },
}


def render(lang):
    t = TEXT[lang]
    fig, ax = plt.subplots(1, 3, figsize=(13.2, 4.3), constrained_layout=True)
    # панель 1 — внешняя поверхность s (своя шкала)
    ax[0].imshow(s_grid, extent=ext, origin="upper", cmap=cmap, aspect="auto")
    ax[0].set_title(t["s"], fontsize=11)
    # панели 2,3 — общая шкала dz
    for a, grid, title in ((ax[1], ok, t["ok"]), (ax[2], ked_full, t["ked"])):
        im = a.imshow(grid, extent=ext, origin="upper", cmap=cmap,
                      vmin=vmin, vmax=vmax, aspect="auto")
        a.set_title(title, fontsize=11)
    for a in ax:
        a.scatter(xw, yw, s=12, c="white", edgecolors="black",
                  linewidths=0.5, zorder=3)
        # рамка разрыва данных
        a.add_patch(plt.Rectangle((540, 250), 320, 310, fill=False,
                                  edgecolor="black", lw=1.0, ls="--", zorder=4))
        a.set_xticks([]); a.set_yticks([])
        a.set_xlim(0, W); a.set_ylim(0, H)
    cb = fig.colorbar(im, ax=ax[1:], fraction=0.046, pad=0.02)
    cb.set_label("dz")
    fig.suptitle(t["note"], fontsize=9, y=1.02)
    out = os.path.join(os.path.dirname(__file__), "images",
                       "edk_result.png" if lang == "ru" else "edk_result_en.png")
    fig.savefig(out, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("saved", out)


render("ru")
render("en")
