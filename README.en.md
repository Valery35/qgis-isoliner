# Isoliner — grids and isolines (QGIS)

**English** · [Русский](README.md)

[![Install in QGIS](https://img.shields.io/badge/Install%20in%20QGIS-blue.svg)](https://plugins.qgis.org/plugins/grid_isolines/) [![Plugin page](https://img.shields.io/badge/Plugin%20page-0f766e.svg)](https://www.informpp.ru/%D0%B3%D0%BB%D0%B0%D0%B2%D0%BD%D0%B0%D1%8F-%D1%81%D1%82%D1%80%D0%B0%D0%BD%D0%B8%D1%86%D0%B0/qgis-isoliner)

A Processing-provider plugin for interpolating point data and building isolines.
The **"Grid and isolines"** group contains seven tools:

1. **2D Kriging (points → raster)** — ordinary/simple kriging over a point
   layer. Core: GSLIB KB2D.
2. **Isolines from raster** — isolines (lines) and contour polygons (bands
   between isolines) whose boundaries coincide with the lines.
3. **Variogram (experimental)** — isotropic experimental variogram from points
   with model fitting (nugget, sill, range) and an HTML report. Lets you set the
   variogram from the shape of the cloud rather than by eye.
4. **Variogram map (anisotropy)** — γ(h_x, h_y) surface: anisotropy shows as an
   ellipse; estimates the major-axis azimuth, anisotropy ratio and range to feed
   into kriging.
5. **Variogram cross-validation** — leave-one-out check: validate and tune
   kriging parameters by error, not by eye.
6. **Create sample wells (demo)** — generates a training point layer with roof,
   thickness and a component grade.
7. **Processing profiles** — named sets of "variogram (Structure 1) + outlier
   removal" saved by Variogram and Cross-validation and applied by 2D Kriging.
   Global storage, list management.

Suitable for roof elevations, thicknesses, geomechanical properties, chemistry
and any numeric attribute.

## Installation

**Plugins → Manage and Install Plugins → Install from ZIP**, point to the plugin
archive. The tools appear in the **Processing** panel: provider **"Isoliner"**,
group **"Grid and isolines"**.

Requirements: QGIS 3.16+. Uses only the built-in Processing algorithms
(GDAL/native) plus NumPy and GDAL shipped with QGIS — no external dependencies.

## Update and reload

The plugin reloads cleanly on the fly, without restarting QGIS:

- installing a new version — the same way, **Install from ZIP** over the old one.
- quick code reload during development — **Plugin Reloader** ("Reload a
  plugin…"), pick "Isoliner". The Processing provider and all algorithms
  re-register immediately.

No separate cache cleaning or folder removal is needed.

## Documentation

The full reference for every parameter is shipped with the plugin:
`doc/Isoliner.pdf` (Russian) and `doc/Isoliner_en.pdf` (English). The interface
is bilingual (EN/RU) and follows the QGIS locale; in each tool's dialog the
**"Help"** button opens the PDF in the language of the interface. The right-hand
panel shows a short hint.

## Quick start

1. **Variogram (experimental)** (optional but useful): feed the points and the Z
   field, fit a model (nugget, sill, range). Choose the maximum distance so the
   curve reaches a plateau. The fitted model can be saved as a profile and
   applied in 2D Kriging.
2. **2D Kriging**: pick or generate a point layer and a numeric Z field → get a
   raster. Cell size can stay `0` (auto). To remove extrapolation beyond the
   wells, enable "Clip to well hull".
3. **Isolines from raster**: feed the resulting raster, set a step **or**
   explicit levels. Output: lines and (by default) contour polygons.
4. **Cross-validation** (optional, useful before the final grid): feed the same
   points and Z field, set the variogram — ME/RMSE/MSDR/R metrics appear in the
   Log. Aim for MSDR ≈ 1 and carry the parameters into 2D Kriging.

## Parameters

### 2D Kriging
- **Kriging type** — ordinary (OK) or simple (SK, uses "Mean").
- **Search radius** (`0` = whole sample), **min/max number of points**.
- **Cell size** (`0` = `min(extent)/50`), with a live grid-size readout.
- **Extent** — by layer by default.
- **Clip to well hull** — convex hull of all points (+ buffer), or an explicit
  mask polygon (takes priority over the hull).
- **Variogram** (under "Advanced") — nugget + structure (spherical/exponential/
  Gaussian/power), azimuth, anisotropy. Correlation range `0` = `max(extent)/3`.
- **Smooth grid (Gaussian)** (off by default) — Gaussian smoothing of the output
  raster, radius in cells. The smoothed grid is the kriging output.
- **Load processing profile** — applies a saved profile (nugget, variogram
  structure, outliers) over the dialog fields. The list refreshes when the
  window opens.

### Isolines from raster
- **Step** of isolines **or** **explicit levels** (space-separated; decimal
  separator comma or dot). Levels take priority over step.
- **Index isoline every N-th** — `is_index` flag on the bold ones.
- **Min line length** and **line rounding** (Chaikin, iterations; removes
  "octagons" from a coarse grid).
- **Value field name** for lines (default `ELEV`).
- **Contour polygons** — built by default into a temporary layer. To skip them,
  clear this field.

### Variogram (experimental)
- **Points** and **Z value field**. Optional **grouping field** (e.g. survey
  type) — a separate curve per value, handy to compare populations of different
  density.
- **Number of lags** and **maximum distance** (in layer units; `0` =
  half-diagonal). Set the window so the curve reaches a plateau.
- **Fit model (recommendation)** — nugget, sill, range and model in pure NumPy,
  no third-party dependencies. **Overlay a given model** — compare your model
  against the cloud.
- **Minimum points per group** (`%` of sample, floor 30) — small groups are not
  drawn. **Outlier removal** (under "Advanced") — as in kriging.
- **HTML report** with the cloud, the fitted curve and the data-variance line.
- **Save profile as** — save the fitted (isotropic) model and current outlier
  settings as a processing profile for use in 2D Kriging.

### Variogram cross-validation
- **Points** and **Z value field**. Optional **well number field** (carried into
  the residual layer).
- **Variogram and search** — the same parameters as in 2D Kriging (kriging type,
  nugget, structure, radius, number of points): what you validate here transfers
  into kriging one-to-one.
- **Load processing profile** — apply a saved profile (nugget, structure,
  outliers) over the fields. The list refreshes when the window opens.
- **Outlier removal** (under "Advanced") — as in kriging.
- Metrics in the Log: **ME** (bias, → 0), **MAE**, **RMSE** (lower is better),
  **MSDR** (→ 1 means the variogram is adequate in scale), **R**.
- Optional **residual layer** (points) with fields for actual, estimate
  (`z_est`), error (`error`/`abs_error`) and standardized residual `std_resid` —
  shows where the model misses. The layer is named after the validated field,
  with readable field aliases.
- **HTML report** (default) — an interactive "estimate vs actual" plot and an
  error histogram (plotly) with a metrics table. Opens in the result viewer.
  Without plotly — a text-metrics report.
- **Save profile as** — save the validated model (with the set anisotropy) and
  outliers as a processing profile.

### Create sample wells (demo)
- **Area (extent)** — by layer, by map canvas, by coordinates or by drawing.
- **Number of wells**. **Min/max of value X**. Roof and thickness ranges
  (defaults as for the Kr-II seam).
- **Smoothness (fraction of extent)** — correlation range (larger = larger
  "patches").
- **Nugget fraction (of variance)** — short-range noise (less predictability).
- The starting variogram is printed to the Log. The data is usable for
  kriging/CV.

### Processing profiles
- A profile is a named set of "variogram (Structure 1: nugget, type, sill,
  range, azimuth, axes) + outlier removal". Stored globally in QgsSettings
  (across projects).
- Variogram saves a profile (isotropic auto-fit) and Cross-validation saves one
  (with anisotropy). 2D Kriging applies it via the **Load profile** field.
- The **Processing profiles** tool: action **Show list** / **Save manually**
  (from fields) / **Delete selected** / **Clear all**.
- Profile drop-downs refresh when the tool window opens.

## Outputs and styling

- **Grid (raster)**: the main kriging result as GeoTIFF — Z estimate on the
  grid. Optionally a **kriging standard error** (sqrt of error variance) is
  produced — a reliability measure: small near wells, growing away from data.
- **Lines**: a level field (`ELEV` by default) + `is_index` (1 on the index
  lines). Easy to style: a rule on `is_index` gives index lines a heavier width.
  Label by the level field.
- **Polygons**: `ELEV_MIN`/`ELEV_MAX` — the band range. Created with a single
  symbol. For range fills set **graduated** symbology by `ELEV_MIN` (or
  `ELEV_MAX`). Band boundaries coincide with the isolines, coverage is
  continuous (no holes).
- The isoline layer is automatically placed **above** the polygon layer so lines
  show over the fill.

## Smoothing

Field smoothing is a raster operation, so it lives in the **2D Kriging** tool
(the **"Smooth grid (Gaussian)"** option, off by default, radius in cells). The
smoothed raster is the kriging output, and isolines are then built exactly on it
— the level range matches the visible raster. Gaussian smoothing of the
continuous field is more robust than smoothing each line separately: contours
don't cross even in dense areas and stay smooth at corners.

The **Isolines from raster** tool keeps a light line rounding (Chaikin, number
of iterations). On a coarse grid isolines otherwise look like "octagons":
gdal:contour places vertices at cell edges. If the grid is coarse, increase the
rounding iterations or reduce the cell size in kriging. Radial/fan lines in empty
corners are extrapolation beyond the data. Enable "Clip to well hull" in kriging
(or set a mask polygon) — isolines and polygons will be limited to the data area.

## How the polygons are built

Polygons are built not by classifying raster "steps" but by polygonizing the
smoothed isolines themselves together with the contour of the raster's valid
area: line ends are snapped to the contour (snap of end points only), the network
is noded (`splitwithlines`) and polygonized. The level range of each band is
determined by sampling the raster at a representative point of the polygon. That
is why polygon boundaries coincide with the lines, including at the very edge.

## Notes

- Auto values: cell = `min(extent)/50`, correlation range = `max(extent)/3`,
  search radius = extent diagonal (whole sample).
- Coincident points (one XY = several samples) are averaged over Z. The kriging
  matrix is regularized; on instability it falls back to inverse distances. At
  nodes values are reproduced exactly.
- Kriging computes the whole rectangular extent — outside the well hull this is
  extrapolation. Clipping to the hull removes it (and also limits
  isolines/polygons to the data area).
- Entered parameter values (including the chosen Z field) are remembered between
  runs.
- **Variogram cross-validation** (leave-one-out): ME/RMSE/MSDR/R metrics and a
  residual layer — tune nugget/range/model by error, not by eye.
- **Outlier removal** (under "Advanced"): Z value bounds by absolute or
  percentile, with "remove" or "cap" (capping) mode.
- Optional **"kriging standard error"** output (sqrt of error variance) — an
  uncertainty map: small near wells, growing away from data.
- The `kb2d.py` engine does not depend on QGIS. Smoke tests are in `tests/`
  (`python grid_isolines/tests/test_kb2d.py`, no QGIS).

## Embedding in your own provider

```python
from .grid_isolines.algorithms import ALGORITHMS
for cls in ALGORITHMS:
    self.addAlgorithm(cls())
```

## License

The plugin is distributed under **GNU GPL v2 or later** (GPL-2.0-or-later) — the
same license as QGIS itself. Full text in the `LICENSE` file.
© Inform++ LLC (www.informpp.ru).

## Changelog

Full list — in `metadata.txt` (`changelog` field). The user manual (PDF) is
bilingual (EN/RU).

- **2.1.0** — trend removal (regression kriging) in **2D Kriging** and
  **Cross-validation**: a "Remove polynomial trend" checkbox and a trend degree
  (plane or quadratic). The trend is removed by least squares before kriging,
  the residuals are kriged, and the trend is added back; useful for seam marks
  and thicknesses with a general dip within an area. The **Variogram map** gains
  a "Write anisotropy to a profile" field: the azimuth, the coefficient and the
  major-axis range are written into a chosen profile and shown in the caption on
  load. Manual and PDFs (EN/RU) updated.

- **2.0.0** — full bilingual plugin (EN/RU): the interface language follows
  the QGIS locale. Dialogs, drop-down options, hints and help panels, plus
  logs, warnings, exceptions, HTML reports, output layer names and
  residual-field aliases are all translated. Bilingual manual:
  `doc/Isoliner.pdf` (RU) + `doc/Isoliner_en.pdf` (EN); the **"Help"** button
  opens the PDF by interface language. Dictionary-based i18n engine with
  coverage and import tests.
- **1.9.1** — bicubic isoline smoothing (grid densification ×2…×4 before
  contouring, off by default) - smooth lines without "octagons", for both
  lines and polygons; belt boundaries coincide with isolines. Belt
  polygonization moved to direct GEOS calls (robust on dense networks).
- **1.9.0** — new "Variogram map (anisotropy)" tool: γ(h_x, h_y) surface,
  anisotropy ellipse, estimated azimuth/ratio/range to feed into kriging.
  If the major range reaches the window edge (γ not on plateau), a warning marks
  it as a lower bound.
- **1.8.3** — Gaussian model: enforced minimum nugget for numerical stability;
  QGIS 4 compatibility; robust contour polygons in QGIS 4 (GEOS 3.14);
  refined manual wording.
- **1.8.2** — bilingual plugin description (EN/RU) in the QGIS catalog and an
  English README for an international audience.
- **1.8.1** — added a link to the plugin's web page (www.informpp.ru) in each
  tool's help panel.
- **1.8.0** — processing profiles: named "variogram (Structure 1) + outliers"
  sets with global storage. Variogram and Cross-validation save a profile, 2D
  Kriging applies it (**Load profile** field, replacing the old checkbox). New
  sixth tool **Processing profiles**. Variogram structures 2 and 3 removed (one
  left, with azimuth and anisotropy); unified parameter order — outlier removal
  last. Terminology: "isotropic" instead of "omnidirectional".
- earlier versions — see `metadata.txt`.
