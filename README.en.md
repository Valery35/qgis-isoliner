# Isoliner — grids and isolines (QGIS)

**English** · [Русский](README.md)

[![Install in QGIS](https://img.shields.io/badge/Install%20in%20QGIS-blue.svg)](https://plugins.qgis.org/plugins/grid_isolines/) [![Plugin page](https://img.shields.io/badge/Plugin%20page-0f766e.svg)](https://www.informpp.ru/%D0%B3%D0%BB%D0%B0%D0%B2%D0%BD%D0%B0%D1%8F-%D1%81%D1%82%D1%80%D0%B0%D0%BD%D0%B8%D1%86%D0%B0/qgis-isoliner)

A Processing-provider plugin for interpolating point data and building isolines.
The tools are split into three Processing groups — **"Grid and isolines"**, **"Additional analysis tools"** and **"Cross-sections"** — twenty-four in all:

**Found a bug or have a suggestion?** Open an [Issue](https://github.com/Valery35/qgis-isoliner/issues) and include your QGIS version, what you did, and attach the data or project to reproduce it — that makes the bug easy to repeat and faster to fix.

### "Grid and isolines" group

- **1.1 2D Kriging (points → raster)** — ordinary/simple kriging over a point layer, point or block, with trend removal and an optional log transform (for log-normal K, T). Core: GSLIB KB2D.
- **1.2 Isolines from raster** — isolines (lines) and contour polygons (bands between isolines) whose boundaries coincide with the lines.
- **1.3 Variogram (experimental)** — isotropic experimental variogram from points with model fitting (nugget, sill, range) and an HTML report. Lets you set the variogram from the shape of the cloud rather than by eye.
- **1.4 Variogram map (anisotropy)** — γ(h_x, h_y) surface: anisotropy shows as an ellipse. Estimates the major-axis azimuth, anisotropy ratio and range to feed into kriging.
- **1.5 Variogram cross-validation** — leave-one-out check: validate and tune kriging parameters by error, not by eye.
- **1.6 Create sample wells (demo)** — generates a training point layer with roof, thickness and a grade. Adds fields for the related tools: head, a categorical mineral type, and, as a separate output, a drift surface (raster) with a dz field.
- **1.7 Processing profiles** — named sets of "variogram (Structure 1) + outlier removal" saved by Variogram and Cross-validation and applied by 2D Kriging. Global storage, list management.

### "Additional analysis tools" group

- **2.1 Categorical indicator kriging** — for a categorical field (mineral type, lithotype) it builds a 0/1 indicator per class, krige each with the KB2D core and normalises the probabilities. Outputs: a multiband probability raster, a zone map and a confidence raster.
- **2.2 External Drift Kriging** — estimation from points when the field is related to a secondary variable known everywhere as a raster (the structural surface of an adjacent seam, a coarse model, a seismic attribute). The drift is removed by regression, the residuals are kriged, and the drift is added back from the raster. The same regression-kriging scheme as trend removal, only here the drift is a function of the external value, not of the coordinates.
- **2.3 Exceedance probability map** — from the kriging estimate and standard-error rasters it builds P(Z>threshold) = Φ((estimate−threshold)/error) under a normal local distribution. A post-processor, runs no kriging of its own. Cut-off grades, risk zones.
- **2.4 Hydraulic gradient and flow direction** — from a head raster it builds the hydraulic gradient |∇h|, the flow-direction azimuth (down-gradient) and a point layer of flow vectors (styled as arrows automatically). Hydrogeology without permeability (with permeability — 2.5).
- **2.5 Specific discharge (Darcy law)** — from a head raster and aquifer-property rasters (K, T) it computes the specific discharge q = K·|∇h| (m/day) and the flow per width Q = T·|∇h| (m²/day). A post-processor, runs no kriging of its own (krige K and T in log space).
- **2.6 Gaussian simulation (SGS)** — sequential Gaussian simulation: an ensemble of equally probable realizations instead of a single smoothed estimate. Mean (E-type), standard deviation, P10/P50/P90 quantiles and an exceedance-probability map — uncertainty, not just the mean. Pure NumPy on top of the kriging core.

### "Cross-sections" group

- **3.01 Cross-section along a line** — from a line and a top-to-bottom set of surfaces it builds a geological section: beds as bands between adjacent surfaces. Two outputs — a distance × elevation drawing (for a layout) and a 3D PolygonZ fence (for the 3D Map View). Several beds at once.
- **3.02 Boreholes on the section** — projects boreholes onto the section line and draws them as columns of bed intervals on top of the drawing. Bed boundaries from chosen elevation fields, distant ones cut off by a corridor, labelled by borehole number.
- **3.03 Bed composition on the section** — colours a bed band by a composition grid along the line. Continuous content (KCl, HO) is cut into slices for a gradient, categorical mineral type merges into facies zones (replacement zones visible). One bed at a time, 2D and 3D outputs.
- **3.04 Intersect surfaces with the section** — places surface grids onto the section as lines (water tables, marker surfaces, anomalies). Line and vex from the section definition.
- **3.05 Vector intersection with the section** — lines and polygons onto the section by exact intersection: a line without Z gives a full-height vertical, a line with Z a point at the elevation, a polygon a band over the zone interval.
- **3.06 Intersect a TIN with the section** — cuts the section through a surface of 3D faces (PolygonZ) and/or a mesh. Unlike a grid it takes overhangs and overturning: several elevations above one station, the trace folds.
- **3.07 Project objects onto the section (beta)** — projects points, lines and polygons onto the section line (elevation from 3D or a field), corridor filter. Generalises the borehole projection.
- **3.08 Unproject from the section (beta)** — reverse projection: objects drawn on the drawing are returned to real coordinates with a Z elevation.
- **3.09 Unwrap a shaft wall (beta)** — a cylindrical section: a circle around the axis with an angular step, surfaces give intersection lines with the wall in arc-elevation axes.
- **3.10 Create a section example** — six stacked surfaces (five beds: three host and two industrial), a line and boreholes along it (h1...h6 elevation fields), to try the cross-section and boreholes on a section at once without kriging.
- **3.11 Surfaces to 3D (meshes)** — surface grids to 2DM mesh layers for the built-in QGIS 3D view (a scene holds one raster terrain but any number of meshes), with a vertical Z transform (scale and offset) and node thinning.

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
panel shows a short hint. The manual and the changelog are also reachable from the **Plugins → Isoliner** menu (**About** and **Manual (PDF)**).

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
- **Block kriging** (off by default) and **Block discretization, N×N per cell**
  (default `4`) — estimates the average over the grid cell rather than the value
  at its centre. The cell is split into N×N discretization points and the
  covariances are averaged over the block (GSLIB KB2D scheme). The surface is
  smoother and the standard error is lower than for point kriging — for
  estimating reserves and grades over a block. Samples at nodes are then not
  reproduced exactly.
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
- Fields for the related tools: checkboxes **head** (for the flow gradient) and a **categorical mineral type** (for indicator kriging), plus the **drift surface** output - an s raster and a related dz field for external drift.
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

- **2.27.5** — released to the main channel, the experimental flag removed; functionally identical to 2.27.4.
- **2.27.4** — the pyqtgraph/PyOpenGL bundle sanitised for the scanner (console/flowchart/multiprocess/exporters removed, .ui loading and pickle-restore disabled), the 3D viewer is back in the published package.
- **2.27.3** — without pyqtgraph/PyOpenGL the 3D viewer menu item is hidden, the pip hint removed.
- **2.27.2** — the published ZIP ships without bundled pyqtgraph/PyOpenGL (plugins.qgis.org scanner requirement); the branch is marked experimental.
- **2.27.1** — 3D viewer: "Apply colouring to" — the attribute paints one surface or all of them.
- **2.27.0** — 3D viewer: surfaces coloured by an attribute grid (grade, thickness, error) on a shared colour scale.
- **2.26.4** — menu: the 3D viewer is the first item, the "Manual (PDF)" item removed (the button in "About" remains).
- **2.26.3** — 3D viewer: borehole collars as round balls instead of square sprites.
- **2.26.2** — 3D viewer: the camera fits both surfaces and boreholes.
- **2.26.1** — 3D viewer: an empty borehole list on QGIS 4 fixed (Qgis.GeometryType with a fallback).
- **2.26.0** — 3D viewer: boreholes as vertical rods from numeric elevation fields (h1...h6 auto-checked); 3.11 reframed as an export to the 2DM mesh format (MDAL).
- **2.25.2** — 3D viewer: fixed a crash on Qt6 (QGIS 4), Qt enum names now with a Qt5/Qt6 fallback.
- **2.25.1** — pyqtgraph and PyOpenGL are bundled with the plugin (libs), the 3D viewer works without pip; system copies take priority.
- **2.25.0** — a built-in "3D surface viewer (beta)" in the "Plugins → Isoliner" menu on pyqtgraph.opengl: horizons as coloured meshes, exaggeration and Z spacing in the window, independent of the stock 3D view (Qt3D); needs pyqtgraph and PyOpenGL.
- **2.24.1** — 3.11: a "Z spacing" parameter, every next grid shifts one step down, the horizon stack unfolds into a shelf.
- **2.24.0** — new tool "3.11 Surfaces to 3D (meshes)": grids to 2DM mesh layers for the built-in QGIS 3D view, vertical Z transform (scale and offset), node thinning.
- **2.23.0** — a "Plugins → Isoliner" menu: "About" (version, links, changelog from metadata.txt) and "Manual (PDF)" by interface language.
- **2.22.7** — manual: panel numbers in the tool headings, tool-dialog screenshots and field tables for the cross-section tools (3.04-3.06, 3.10) and the Gaussian simulation (2.6); both PDFs (EN/RU) rebuilt.
- **2.22.1** — the demo TIN reshaped into a realistic recumbent overturned fold (was a narrow coiled ribbon).
- **2.22.x** — the demo TIN refined into a compact smooth recumbent overturned fold following bed dip (shape and size iterations).
- **2.22.0** — new tool "3.06 Intersect a TIN with the section": a TIN of 3D faces is cut by the section, overturned folds are reproduced (a grid cannot). Section tool numbers became two-digit (3.01-3.10); the demo gained an overturned TIN.
- **2.21.2** — "3.5 Vector intersection" accepts a list of layers in one run; the src field keeps the source layer.
- **2.21.1** — "3.5 Vector intersection": the frame height is now stored in the section definition (no need to supply the drawing for 2D objects); empty outputs are not created.
- **2.21.0** — new tool "3.5 Vector intersection with the section" (lines and polygons onto the section by exact intersection). Section betas shifted to 3.6-3.8, demo to 3.9; the demo gained a fault, a Z marker and a replacement zone.
- **2.20.1** — restored the Help button in the "2.6 Gaussian simulation (SGS)" dialog.
- **2.20.0** — new tool "2.6 Gaussian simulation (SGS)": an ensemble of equally probable realizations instead of a single smoothed estimate. Mean (E-type), standard deviation, P10/P50/P90 quantiles and an exceedance-probability map — uncertainty, not just the estimate. The score variogram is fitted automatically; pure NumPy on top of the kriging core. The "Number of …" labels were reworded in Russian.
- **2.19.1** — horizontal axis labels placed to the left of the line (robust anchoring).
- **2.19.0** — Boreholes/Bed composition on the section take the scale from the section definition (vertical match); renamed to "on the section"; 3.5-3.7 marked "(beta)"; a schematic added to the manual.
- **2.18.6** — horizontal axis labels placed to the left of the line.
- **2.18.5** — corner table: cells between corners (borders under the verticals), rows for segment length and azimuth, white fill.
- **2.18.4** — revert: the Advanced flag is not used on output layers (the stock Processing dialog does not move outputs there). Outputs are back in normal order.
- **2.18.2** — new optional output: a corner table (azimuth and distance) as a polygon layer below the section, rendered on the canvas.
- **2.18.1** — corner points: bottom label only X and Y (azimuth and distance kept as fields for a table), upward smaller triangle, symmetric shelf, axis tick count matches the request more closely.
- **2.18.0** — corner points top and bottom (X, Y, distance, azimuth, name УГ-N, triangle/shelf style), horizontal axes with elevation ticks, drawing margins +5%, demo line is a polyline.
- **2.17.0** — Cross-section along a line optionally outputs corner points and verticals at the polyline nodes (node number, segment azimuth).
- **2.16.0** — raster outputs are created collapsed in the tree (grids no longer bloat the panel), each layer gets a creation history (version, tool, date).
- **2.15.1** — multi-output tools place their layers into a fixed tree group (re-runs add into it); the section family goes into one "Section" group.
- **2.15.0** — the section lineup on a shared **section definition** (line + vex, output by 3.1): intersect surfaces with the section (3.4), project objects (3.5), unproject (3.6), unwrap a shaft wall (3.7). The section clips pinch-outs, the demo has a pinching bed.
- **2.14.1** — the "Additional analysis tools" group (renamed), tidy order in the "Cross-sections" group: section, boreholes, composition, example.
- **2.14.0** — the section vertical scale can be set by an H:V ratio (width:height), the exaggeration is computed automatically and printed to the log. In all three section tools.
- **2.13.2** — the section demo is more expressive (dip, fold, wedges), demo boreholes hug the line.
- **2.13.1** — fixed the boreholes layer creation in the section demo.
- **2.13.0** — a new tool **Bed composition on a section** (3.4): colours a bed band by a composition grid along the line (continuous content as a gradient, categorical mineral type as facies zones). The section demo now also outputs composition grids of the industrial beds.
- **2.12.1** — the section demo was extended to six surfaces (five beds: three host and two industrial).
- **2.12.0** — a new tool **Boreholes on a section** (3.3): projects boreholes onto the line and draws columns of bed intervals on top of the drawing. The section example generator now also outputs boreholes.
- **2.11.1** — the section tools were moved to a new **"Cross-sections"** group: Cross-section along a line is now 3.1, Create a section example is 3.2.
- **2.11.0** — a new tool **Create a section example** (1.8): prepares surfaces and a line for a quick try of the cross-section along a line.
- **2.10.0** — a new tool **Cross-section along a line** (2.6): from a line and a set of surfaces it builds a geological section (beds between roof and floor). Two outputs: a distance × elevation drawing for a layout and a 3D fence for the 3D Map View.
- **2.9.3** — the "Value transform" (ln) list in 2D Kriging was moved under the Z field, closer and more visible.
- **2.9.2** — downhill hachures (the "hachures down" style): fixed direction on QGIS 4 (the line-offset sign convention changed there relative to QGIS 3). QGIS 3 unchanged.
- **2.9.1** — the demo K generation was brought to a realistic range (≈ 0.006…4 m/day, tail clipped), without single spikes into the hundreds.
- **2.9.0** — **2D Kriging** gained an optional value log transform (ln/exp with a delta-method standard error) - kriging of log-normal K, T without a hand-built ln field. Tools 2.3 and 2.4 were swapped so the hydrogeology (gradient 2.4 and specific discharge 2.5) sits together. The example generator gained K and T fields.
- **2.8.0** — a new tool **Specific discharge (Darcy law)** (2.5): from a head raster and K/T rasters it computes the specific discharge (m/day) and flow per width (m²/day). Hydrogeology now with permeability.
- **2.7.1** — illustrations for the exceedance probability map were added to the manual (the tool window and a sample map).
- **2.7.0** — a new tool **Exceedance probability map** (2.4): from the kriging estimate and standard-error rasters it builds P(Z>threshold) under a normal local distribution. A separate post-processor, it does not change the "2D Kriging" window. Cut-off grades, risk zones for any threshold.
- **2.6.1** — tool groups are numbered ("1. Grid and isolines", "2. Additional analysis tools") so the group order in the Processing tree is the same in the Russian and English locales; illustrations were added to the manual (the External Drift Kriging window, the tool tree, and an ordinary-vs-external-drift comparison).
- **2.6.0** — a new tool **External Drift Kriging**: regression kriging against an external raster known everywhere (an adjacent seam, a coarse model, a seismic attribute). The drift is removed by regression, the residuals are kriged, and the drift is added back from the raster. Degree 1 or 2; search, anisotropy, clipping and the standard error as in 2D Kriging. The demo generator can output a drift surface (raster) and a related dz field for an end-to-end check. Tools are renumbered per group: "Grid and isolines" 1.1-1.7, "Additional analysis tools" 2.1-2.3 (the old flat 1-10 sorted as strings).
- **2.5.0** — a new tool **Hydraulic gradient and flow direction**
  (hydrogeology): from a head raster it builds the gradient magnitude |∇h|, the
  flow-direction azimuth (down-gradient) and a point layer of flow vectors
  styled as arrows automatically. The geometry of the head field without
  permeability, the
  Darcy velocity is not computed. Optional smoothing of the head before
  computing. The sample-wells generator gained a head field option with a
  regional slope for an end-to-end learning cycle.

- **2.4.0** — the **2D Kriging** tool gained **block kriging** (a **Block
  kriging** checkbox and a **Block discretization, N×N per cell** field, 4×4 by
  default). It estimates the average over a grid cell rather than the value at
  its centre: the cell is split into N×N discretization points and the
  covariances are averaged over the block (GSLIB KB2D scheme). The surface is
  smoother and the standard error is lower than for point kriging — for
  estimating reserves and grades over a block. Off by default: ordinary point
  kriging. Trend removal and block kriging work together.

- **2.3.0** — a new tool **Categorical indicator kriging**: for a categorical
  field it builds an indicator per class, krige each with the KB2D core and
  normalises the probabilities to sum to 1, producing a multiband probability
  raster, a zone map and a confidence raster. The sample-wells generator gains a
  categorical mineral-type option for teaching.

- **2.2.0** — the **Isolines from raster** tool gains an **Isoline style**
  choice with two bundled presets (the `styles` folder): structural (default)
  and depression with downhill hachures. The style is applied to the layer
  automatically. The depression style turns on the downhill-side computation by
  itself: lines get a `dn_sign` field by sampling the source raster on both
  sides of the line, so the hachures on index contours point toward the low
  ground. No separate checkbox.

- **2.1.1** — quiet conveniences. The selected processing profile is remembered
  between sessions and prefilled when **2D Kriging** and **Cross-validation**
  open. Run parameters are saved only on success. Data-conditioning warnings are
  added to the log: few points, coinciding coordinates, identical values.

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
