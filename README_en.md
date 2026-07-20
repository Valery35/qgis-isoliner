# Isoliner — grids and isolines (QGIS)

**English** · [Русский](README.md)

[![Install in QGIS](https://img.shields.io/badge/Install%20in%20QGIS-blue.svg)](https://plugins.qgis.org/plugins/grid_isolines/) [![Plugin page](https://img.shields.io/badge/Plugin%20page-0f766e.svg)](https://www.informpp.ru/%D0%B3%D0%BB%D0%B0%D0%B2%D0%BD%D0%B0%D1%8F-%D1%81%D1%82%D1%80%D0%B0%D0%BD%D0%B8%D1%86%D0%B0/qgis-isoliner)

A Processing-provider plugin for interpolating point data, building isolines and working with terrain.
The tools are split into five Processing groups — **"Grid and isolines"**, **"Topography"**, **"Additional analysis tools"**, **"Cross-sections"** and **"Fractal analysis"** — forty-four in all:

> Isoliner grows on the tasks of real mining operations. Need a feature for your production - contact us: [the "For enterprises" page](https://www.informpp.ru/главная-страница/предприятиям).

**Found a bug or have a suggestion?** Open an [Issue](https://github.com/Valery35/qgis-isoliner/issues) with your QGIS version, what you were doing, and data or a project to reproduce — it makes the bug easier to repeat and faster to fix.

### "1. Grid and isolines" group

- **1.01 Declustering (weights)** — cell declustering (GSLIB declus): weights inverse to local density, a representative mean for reserves and SK. Auto or manual cell size, HTML report.
- **1.02 2D Kriging (points -> raster)** — ordinary/simple kriging, point or block, with trend removal, the kriging standard error and outlier trimming. The core is GSLIB KB2D.
- **1.03 Minimum curvature (points -> raster)** — a deterministic alternative to kriging without a variogram: biharmonic with tension, anisotropy. Common for geophysical field maps.
- **1.04 Isolines from raster** — isolines (lines) and contour polygons (bands between isolines) whose boundaries coincide with the lines.
- **1.05 Variogram (experimental)** — an isotropic experimental variogram with model fitting (nugget, sill, range) and an HTML report. Accepts weights from 1.01.
- **1.06 Variogram map (anisotropy)** — the γ(h_x, h_y) surface: anisotropy shows as an ellipse. Estimates the azimuth, the anisotropy ratio and the range for kriging.
- **1.07 Variogram cross-validation** — leave-one-out control: tuning kriging parameters by the ME/RMSE/MSDR/R metrics rather than by eye.
- **1.08 Method cross-validation (LOO)** — comparing methods (kriging or minimum curvature) Surfer-style: a random subset, area filters, a neighbor exclusion buffer, an HTML report.
- **1.09 Processing profiles** — named "variogram + trimming" sets saved by the variogram tools and loaded into 2D Kriging. A global store.
- **1.10 Create sample wells (demo)** — a training point layer: roof, thickness, grade, rock type, head, a drift surface.
- **1.11 Create a geophysical-profiles example (demo)** — two modes: resistivity survey (an apparent-resistivity anomaly spot, SP, IP) and subsidence (a trough by survey rounds).

### "2. Topography" group

- **2.01 Download DEM by extent** — Copernicus DEM GLO-30 from an open store, no registration or keys: a seamless mosaic, reprojection into a metric CRS, hydrological correction by a checkbox.
- **2.02 Download base topography by extent** — OpenStreetMap layers for terrain work: watercourses (ready streamlines), water bodies, peaks with elevations, cliffs and embankments, the coastline.
- **2.03 Topo2Raster (terrain from vectors)** — terrain from points, contours, streamlines, cliffs and lakes by multigrid interpolation in the spirit of ANUDEM: a membrane frame plus minimum-curvature polishing.
- **2.04 Fill depressions** — the Planchon-Darboux method with a tunable epsilon: pits only up to the spill level, or a through slope across flats.
- **2.05 Flow and accumulation (D8)** — flow directions in ArcGIS codes and accumulation by a vectorized sweep. A 2000×2000 grid in seconds.
- **2.06 River network** — links from heads and junctions downstream, the Strahler order, the output fits 2.03 as streamlines.
- **2.07 Basins and watersheds** — from pour points snapped to the accumulation maximum, or automatically from mouths. Polygons and a label raster.
- **2.08 Slope and aspect** — the Horn 3×3 kernel as in gdaldem, aspect as the downslope azimuth, flat cells flagged.
- **2.09 Peaks** — local maxima with two filters: the radius suppresses secondary tops, the drop cuts off bumps.
- **2.10 Demo relief** — deterministic synthetic terrain for examples, tests and offline work. Also outputs gauge points for 2.15.
- **2.15 Gauge point report** — watershed morphometry from a closure point: area, elevations, mean basin slope, length, fall and slope of the main stream. Polygons with attributes and an HTML report.

### "3. Additional analysis tools" group

- **3.01 Categorical indicator kriging** — builds 0/1 indicators per class of a categorical field (rock type, lithotype), kriges and normalizes the probabilities. Output: a probability raster, a zone map, a confidence raster.
- **3.02 External Drift Kriging** — estimation when the field is tied to an external quantity known everywhere as a raster. The drift is removed by regression, the residuals are kriged.
- **3.03 Exceedance probability map** — builds P(Z>threshold) from the estimate and kriging-error rasters. Cut-off grades, risk zones.
- **3.04 Hydraulic gradient and flow direction** — from a head raster builds |∇h|, the flow azimuth and a point layer of flow vectors styled with arrows.
- **3.05 Specific discharge (Darcy law)** — from a head raster and K, T rasters computes the filtration velocity and the discharge per width.
- **3.06 Gaussian simulation (SGS)** — an ensemble of equally probable realizations: E-type, standard deviation, P10/P50/P90 quantiles, exceedance probability.
- **3.07 Density from measurements (variable support)** — a measurement is spread over its support (point+sigma, line corridor, polygon mask) with mass preserved, dasymetric refinement, effective sigma.
- **3.08 Create a density example (demo)** — points, lines and polygons with a round total mass to verify the invariant.

### "4. Cross-sections" group

- **4.01 Cross-section along a line** — a geological section along a line and a stack of surfaces: beds as bands, two outputs (a distance×elevation drawing and a 3D fence of PolygonZ). The order of the surfaces comes from the project layer tree. By default it builds a section for every line of the layer, with a layout of the drawings and a common vertical scale, the sections being told apart by the sec and sec_id fields.
- **4.02 Boreholes on the section (drilling model)** — boreholes from a collar and interval layer pair (the minimal mining-package model) onto every drawing of the definition at once. Fields are found automatically by the contract names, collars are reprojected into the definition CRS, the tolerant reader reports a summary to the log, colours and a legend by code, clipping by the frame and by the drawing bands, collar labels from number, an optional 3D output.
- **4.03 Bed composition on the section** — paints the bed band by a composition grid: continuous grade as slices, categorical rock type as facies zones.
- **4.04 Intersect surfaces with the section** — surface grids onto the section as lines (aquifers, marker surfaces).
- **4.05 Vector intersection with the section** — lines and polygons by exact intersection: a line without Z as a vertical, with Z as a point, a polygon as a band.
- **4.06 Intersect a TIN with the section** — cuts 3D faces and meshes with the section: overhangs and overturned folds are reproduced.
- **4.07 Project objects onto the section (beta)** — points, lines and polygons onto the section drawing, the elevation from 3D or a field.
- **4.08 Unproject from the section (beta)** — the reverse projection of objects drawn on the section back into real coordinates with Z.
- **4.09 Shaft wall unwrap (beta)** — a cylindrical section around an axis in arc-elevation axes.
- **4.10 Create a section example** — a stack of surfaces, a line and boreholes with elevation fields, to try the section without kriging.

### "5. Fractal analysis" group

- **5.01 Fractal dimension** — a D = 3 − H map by the variogram method in a moving window: contrasts highlight faults and block boundaries.
- **5.02 Box-counting of masks** — one D per binary mask: replacement and workings outlines compared by a number.
- **5.03 Dimension of lines and boundaries** — D of each line by the divider method: diagnostics of oversmoothed isolines.
- **5.04 Minkowski dimension (vectors)** — box-counting directly over lines and polygon boundaries, without rasterization.
- **5.05 Create a fractal example (demo)** — a river network with tributary orders, a catchment with a ragged boundary, a coastline.

Suitable for bed elevations, thicknesses, rock properties, chemistry and any numeric attribute.

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

- **4.6.0** — the new 2.15 Gauge point report tool (watershed morphometry from a closure point), demo gauge points in 2.10, demo placement next to the project layers, a fix for the QGIS crash on loading several output layers, and removal of the legacy h1...h6 demo boreholes.
- **4.5.0** — 4.02: column clipping by the drawing instead of roof and floor lines, one input takes the band polygons from 4.01.
- **4.4.0** — 4.02: column clipping by the roof and floor lines from 4.04.
- **4.3.0** — 4.02: column clipping by the drawing frame zmin and zmax from the definition, with a tolerance and a switch.
- **4.2.0** — 4.02: the collar label comes from the number field of the collar layer, name and label are synonyms, hole_id stays in the attributes.
- **4.1.1** — 4.02: collars are reprojected into the definition CRS on reading, with CRS and nearest collar diagnostics on screen.
- **4.1.0** — the collar and interval drilling-data model and the new batch 4.02 Boreholes on the section (drilling model) tool: fields found automatically, a tolerant reader with a log summary, colours and a legend by code, input memory, a 3D output. Demo 4.10 outputs the collar and interval pair. The former 4.02 on the h1...h6 fields is removed.bilingual (EN/RU).

- **4.0.2** — fixed cross-section output (group 4): the drawing no longer falls off the map canvas in projects with local CRSs. Drawing layers now get an engineering CRS instead of an empty one. In 4.01 the surface order is taken from the project layer tree by default (manual order stays available via a checkbox).
- **4.0.1** — fixed DEM georeferencing in 2.01 for CRSs with northing-easting axis order (GSK-2011 and other Gauss-Kruger, Krassovsky): the raster no longer flies to a mirror location, and "Zoom to layer" works. UTM was not affected.
- **4.0.0** — the 3D part moved to a separate Isoliner 3D module (publication pending): the 3D viewer, the "Bed and block model" group (5.01-5.07), 2DM meshes, polyhedra and the bundled pyqtgraph/PyOpenGL. The package slimmed from ~12 to ~8 MB. Fractal tools renumbered from 6.xx to 5.xx; tool identifiers unchanged, user models keep working. Version 3.2.0 with the full former functionality stays in the catalog.
- **3.2.0** - 2.03 supports a variable water edge (river slope from polygon node Z), 2.04 reworked into "Terrain preparation" with FPDEMS smoothing (edge-preserving), a smoothing checkbox added to 2.01.
- **3.1.1** - fixed GEDTM30 height scale (was ten times too small): the COG is read via translate -unscale.
- **3.1.0** - tool 2.01 gained a terrain-source choice: besides Copernicus GLO-30 (DSM) there is now GEDTM30 (forest-free DTM, CC BY 4.0) by OpenGeoHub, more accurate under forest canopy.
- **3.0.4** — catalog security-scanner compatibility: narrow exception types instead of a broad except with a silent pass/continue.
- **3.0.3** — fixed a Topo2Raster crash on QGIS 4 with single-part geometries (asMultiPoint/asMultiPolyline/asMultiPolygon).
- **3.0.2** — topography output layers are added to the tree collapsed.
- **3.0.1** — QGIS 4 shakedown: 2.01 and 2.10 accept user CRSs without an EPSG code (WKT into the warp), topography outputs land in a "Topography" layer-tree group, output-file descriptions in the tool help, a Topo2Raster lesson in the manual.
- **3.0.0** — a new "2. Topography" group (ten tools): Copernicus GLO-30 DEM download by extent without registration, an OSM base map, Topo2Raster from vectors in the spirit of ANUDEM, depression filling, D8 flow and accumulation, a river network with Strahler orders, basins, slope and aspect, peaks, a demo relief. The labels of the former groups shifted (additional 3, cross-sections 4, bed 5, fractals 6), tool identifiers unchanged.
- **2.65.0** - work log (isoliner.log with versions, parameters and timing; traceback on failure), an Isoliner toolbar with 3D and About buttons, a 3D icon.
- **2.64.1** - a step-by-step density appendix (2.07/2.08) and demo-layer fields in the manual.
- **2.64.0** - new tools 2.07 "Density from measurements (variable support)" and 2.08 demo: a measurement is spread over its support (point+sigma, line corridor, polygon), mass conserved, three-band output, dasymetry.
- **2.63.5** - fixed the missing 1.10 heading in the PDF outline.
- **2.63.4** - the "kriging or minimum curvature" note in 1.11 strengthened with a subsidence example.
- **2.63.3** - fixed a kriging crash on a missing Z field (a clear message instead of KeyError); added a "Demo layer fields" appendix to the manual.
- **2.63.2** - adaptive output layer names on generation (1.11 by mode, section demo wells distinct from 1.10).
- **2.63.1** - subsidence (1.11) capped at 2 m, uniform sign by choice, strict zeros at the edges.
- **2.63.0** - tool 1.11 extended: electrical-prospecting mode (rho_k as a spot, SP, IP, z, rho_true) and subsidence mode (a trough across tours). The anomaly is now a spot, not a stripe.
- **2.62.0** - new tool 1.11 "Create a geophysical-profiles example (demo)": electrical-prospecting profiles with pickets, rho_k (Ohm*m) and SP (mV), with a low-resistivity anomaly.
- **2.61.0** — fractal tools moved into a separate group "5. Fractal analysis" (5.01-5.05), formerly in group 2. No logic changes.
- **2.60.0** — declustering weights now also in cross-validations (1.07, 1.08 - weighted metrics) and indicator kriging (2.01 - indicator toward the declustered class proportion).
- **2.59.0** — declustering weights (the wt field from 1.01) are now accepted by the variogram (1.05) and variogram map (1.06): pairs are weighted so clusters do not inflate the near lags.
- **2.58.1** — Group 1 reordered and switched to two-digit numbering (1.01…1.10) for correct toolbox sorting.
- **2.58.0** — new tool 1.0 "Declustering (weights)" (a GSLIB declus port): density-based weights and a declustered mean; the weight field feeds SGS for a weighted histogram.
- **2.57.0** — cross-validation report (1.5, 1.9): a Best-fit regression line on the estimate-vs-fact plot plus its slope/intercept/angle in the metrics (a range-bias indicator).
- **2.56.1** — trimmed unused subpackages from the bundled pyqtgraph (console, flowchart, multiprocess, exporters, configfile), clearing security-scanner warnings.
- **2.56.0** — new tool 1.9 "Method cross-validation (LOO)": leave-one-out control for kriging and minimum curvature, with an error layer and an HTML report.
- **2.55.0** — new tool 1.8 "Minimum curvature (points -> raster)": biharmonic-with-tension gridding, a deterministic alternative to kriging.
- **2.54.0** — tool 4.07: the suite loads as separate per-bed layers (visibility control) with gentler folds; the standalone "Folded bed" example was removed.
- **2.53.0** — tool 4.07: the suite is now folded beds, each a separate feature coloured individually in 3D, with adaptive layer names. 3D viewer: framing accounts for the exaggerated height.
- **2.52.0** — 3D viewer: a section contour of bodies cut by the section plane, and boreholes drawn as cylinders with intervals coloured by lithology.
- **2.51.0** — tool 4.07: new examples "Folded bed" (a fold train) and "Suite" (a stack of beds).
- **2.50.1** — 3D viewer: fixed a crash when showing bodies/surfaces without a section plane, plus the click query and the solid-colour picker on newer pyqtgraph builds (QGIS 4).
- **2.50.0** — tool 4.07: placement now comes from the extent (map view), with new "Thickness" and "Base elevation" parameters and the Z range printed to the log. 3D viewer: a "Bodies" tab shows polygon layers with Z as volumetric bodies.
- **2.49.0** — tool 4.07 "Create a polyhedral example (beta)": a PolyhedralSurface Z / TIN Z demo (QGIS 3.40 and newer); MultiPolygon Z fallback.
- **2.48.5** — group 4 left beta; the manual: a "Lessons and self-check" appendix with a write-off test.
- **2.48.4** — density as a dens field in the block model; band drop-downs in 4.03 and 2.03.
- **2.48.3** — fix: the "Density band" parameter moved from 4.02 back to 4.03.
- **2.48.2** — fix of the vertical split in 4.03; per-cell density from a grid band.
- **2.48.1** — domains: 4.05 zone contours into a grid band, 4.06 reserve difference (write-off).
- **2.48.0** — 3D: a section trace on the surfaces; 4.03: a vertical column split (z_from/z_to).
- **2.47.7** — fix: the "Custom colour" swatch crashed on the first click for a layer without settings.
- **2.47.6** — 3D: a per-layer "Custom colour" with a swatch and a click-to-pick dialog.
- **2.47.5** — 3D: borehole labels are thinned, texts no longer overlap.
- **2.47.4** — the manual: seven article figures placed into the chapters, the intro image block fixed.
- **2.47.3** — the manual: nine new screenshots across the fresh chapters.
- **2.47.2** — the manual: the 3D viewer chapter rewritten for the current UI, a "For enterprises" section.
- **2.47.1** — the help-footer invitation links to the "For enterprises" page.
- **2.47.0** — 2.10: K, grid offsets, a densify factor and D_r2 - the fit quality per feature.
- **2.46.1** — terminology: consistent count wording in the Russian parameter labels.
- **2.46.0** — group 2 renumbered to 2.01-2.11: proper ordering in the toolbox.
- **2.45.0** — raster band choice everywhere as drop-downs with band names (14 parameters in nine tools).
- **2.44.0** — 2.10 Minkowski dimension over vectors, 2.11 a demo (rivers, a basin, a coast); 2.9 accepts polygons.
- **2.43.0** — the fractal tools 2.07-2.09 left beta; the manual chapters extended with workflows.
- **2.42.1** — 2.7: a single-band D grid by default, ready for dimension isolines; H by a checkbox.
- **2.42.0** — a fractal pair: 2.8 mask box-counting and 2.9 line dimension by the divider method.
- **2.41.0** — a new tool 2.7 "Fractal dimension": a D and H map by the variogram method.
- **2.40.2** — the manual: an overview chapter "Kriging kinds: which one to pick" with a cheat sheet.
- **2.40.1** — a custom-development invitation in the tool-help footer.
- **2.40.0** — tool ordering: a "4. Bed and block model" group (4.01-4.04, beta), the demo generators last in their groups; the manual renumbered.
- **2.39.0** — a new tool 3.14 "Bed grid to a block model": centroids with attributes, a contour-limited export.
- **2.38.0** — 3D viewer: click to query the block model - all bands and the thickness at the point, a hit marker.
- **2.37.0** — a new tool 3.13 "Bed calculator": thickness, volume, ore and metal reserves, a contour summary, an HTML report.
- **2.36.1** — 3D viewer: borehole labels above the masts.
- **2.36.0** — 3D viewer: a single "Colouring" field per layer (palette / own band / external raster).
- **2.35.1** — 3D viewer: fixed the "All"/"None" buttons crash.
- **2.35.0** — 3D viewer: per-layer settings (mode, bands, external attribute), "Layers"/"Vectors" tabs.
- **2.34.0** — a new tool 3.12 "Assemble a bed grid"; 3D viewer: a layer-set filter, band drop-downs with names.
- **2.33.1** — the manual: attribute tables of the generated layers (demos 1.6 and 3.10, the 3.01 outputs).
- **2.33.0** — the manual: chapters on the 3D viewer and 3.11, a bed-grid scheme, fresh screenshots; the PDFs rebuilt.
- **2.32.4** — 3D viewer: a mast above the borehole collar, the rod stays visible inside bodies.
- **2.32.3** — 3D viewer: fixed a section-plane crash on QGIS 4 (asMultiPolyline).
- **2.32.2** — 3.01: the polyline vertices are forced into the stationing, bends are no longer cut by a chord on the profile, the bands and the 3D fence.
- **2.32.1** — demo: the fault moved off the section-line bend (it visually masqueraded as the definition).
- **2.32.1** — demo: the fault moved off the section-line bend.
- **2.32.0** — 3D viewer: the section plane from a 3.01 definition as a vertical translucent ribbon (zmin/zmax from the fields).
- **2.31.0** — 3D viewer: a bed body is coloured by its own parameter band, an external attribute applies to single-band surfaces only.
- **2.30.0** — 3D viewer: a "Bed bodies" mode — the roof and bottom from bands 1 and 2 are closed with a skirt into a watertight body; a step towards wireframe and block models.
- **2.29.1** — the bed-grid convention: band 1 roof, 2 bottom, 3+ parameters; the demo outputs 4-band bed grids.
- **2.29.0** — multiband bed grids: the demo outputs a 3-band grid per industrial bed (roof, content, mineral type), 3.03/3.11/the 3D viewer gained band selection.
- **2.28.0** — demo 3.10: independent stochastic composition per industrial bed; 3D viewer: a colour-scale legend, transparency, top/side views, a PNG snapshot.
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
- **2.20.1** — restored the Help button in the "2.06 Gaussian simulation (SGS)" dialog.
- **2.20.0** — new tool "2.06 Gaussian simulation (SGS)": an ensemble of equally probable realizations instead of a single smoothed estimate. Mean (E-type), standard deviation, P10/P50/P90 quantiles and an exceedance-probability map — uncertainty, not just the estimate. The score variogram is fitted automatically; pure NumPy on top of the kriging core. The "Number of …" labels were reworded in Russian.
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
