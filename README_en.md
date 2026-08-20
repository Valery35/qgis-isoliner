# Isoliner — grids and isolines (QGIS)

**English** · [Русский](README.md)

[![Install in QGIS](https://img.shields.io/badge/Install%20in%20QGIS-blue.svg)](https://plugins.qgis.org/plugins/grid_isolines/) [![Plugin page](https://img.shields.io/badge/Plugin%20page-0f766e.svg)](https://www.informpp.ru/%D0%B3%D0%BB%D0%B0%D0%B2%D0%BD%D0%B0%D1%8F-%D1%81%D1%82%D1%80%D0%B0%D0%BD%D0%B8%D1%86%D0%B0/qgis-isoliner)

A Processing-provider plugin for interpolating point data, building isolines and working with terrain.
The tools are split into seven Processing groups — **"Grid and isolines"**, **"Topography"**, **"Additional analysis tools"**, **"Cross-sections"**, **"Geological model"** (beta), **"River hydrology"** and **"Fractal analysis"** — sixty-eight in all:

**Two companion plugins work alongside it.**
[Isoliner3D](https://github.com/Valery35/qgis-isoliner3d) shows the
surfaces, the bed bodies and the boreholes in a separate 3D scene and
computes reserves over a block model.
[Topoliner](https://github.com/Valery35/topoliner) puts the topology of
polygon and line layers in order: nodes, dangles, overshoots, and
simplification that keeps the shared boundaries. The usual order is
Topoliner on the source outlines first, then Isoliner on the
interpolation, then Isoliner3D on the display.

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
- **1.09 Create sample wells (demo)** — a training point layer: roof, thickness, grade, rock type, head, a drift surface.
- **1.10 Create a geophysical-profiles example (demo)** — two modes: resistivity survey (an apparent-resistivity anomaly spot, SP, IP) and subsidence (a trough by survey rounds).

### "2. Topography" group

- **2.01 Download DEM by extent** — Copernicus DEM GLO-30 from an open store, no registration or keys: a seamless mosaic, reprojection into a metric CRS, hydrological correction by a checkbox.
- **2.02 Download base topography by extent** — OpenStreetMap layers for terrain work: watercourses (ready streamlines), water bodies, peaks with elevations, cliffs and embankments, the coastline.
- **2.03 Topo2Raster (terrain from vectors)** — terrain from points, contours, streamlines, cliffs and lakes by multigrid interpolation in the spirit of ANUDEM: a membrane frame plus minimum-curvature polishing.
- **2.04 Terrain preparation** — depression filling by the Planchon-Darboux method with a tunable epsilon and smoothing: pits only up to the spill level, or a through slope across flats.
- **2.05 Flow and accumulation (D8)** — flow directions in ArcGIS codes and accumulation by a vectorized sweep. A 2000×2000 grid in seconds.
- **2.06 River network** — links from heads and junctions downstream, the Strahler order, the output fits 2.03 as streamlines.
- **2.07 Basins and watersheds** — from pour points snapped to the accumulation maximum, or automatically from mouths. Polygons and a label raster.
- **2.08 Slope and aspect** — the Horn 3×3 kernel as in gdaldem, aspect as the downslope azimuth, flat cells flagged.
- **2.09 Peaks and pits** — local extremes of both signs with two filters: the radius suppresses secondary tops, the relief threshold cuts off bumps and puddles. The elevation is written into the geometry Z: the spot points go to DXF together with the contours and remove the flat caps of a surface at closed contours.
- **2.10 Demo relief** — deterministic synthetic terrain for examples, tests and offline work. Outputs gauge points for 2.15, ditch traces for 2.16 and, optionally, a pair of surfaces with work areas for 2.18.
- **2.11 Split contours for validation** — splits a contour layer into a working and a held-out part for strict evaluation of the build: held-out lines do not go into the interpolation.
- **2.12 Contour residuals against a DEM** — compares contour elevations with the built surface: reproduction and held-out control, a summary and a map of deviations.
- **2.13 DEM terracing diagnostics** — vertical curvature as a marker of steps from contour lines: where the surface goes in terraces, it was built from contours with no relief in between.
- **2.14 Remove steps (clamped smoothing)** — smoothing clamped by a tolerance to the source elevations, with an HTML report before and after: the steps go while the surface does not drift.
- **2.15 Gauge point report** — watershed morphometry from a closure point: area, elevations, mean basin slope, length, fall and slope of the main stream. Polygons with attributes and an HTML report.
- **2.16 Catchment. Lines and outlines (ditches, open pits)** — the area intercepted by a hillside ditch, a gutter or an open pit outline. A line is rasterised and taken as the intake, a polygon is treated as an intake in its entirety, the catchment is collected by flow. Burning the trace is a separate checkbox.
- **2.18 Cut and fill (earthwork volumes)** — volumes between two surfaces, or a surface and an elevation: fill, cut, balance. Grids are aligned bilinearly, the dead band cuts background noise, work areas are counted separately. A difference raster and an HTML statement.
- **2.19 Crest and toe candidates** - the places where the slope changes fastest, traced into lines with the drop, the length and the kind in the attributes.
- **2.20 Crests and toes into work** - elevations off the DEM, crest-toe forms by descending the slope, ready Top and Bottom inputs.
- **2.21 Create a demo open pit** - a pit with benches, a ramp, a dump and a ditch plus the true structural lines as a reference.
- **2.22 Elevations from adjoining contours** — elevations for structural lines from the contours they adjoin: an input for mute crests and toes without a DEM.
- **2.23 Flow lines from points, lines and outlines** — where the water will run from a given place: downhill along D8 from every starting cell, stopping at a water body, at a watercourse by the accumulation threshold, at the edge of the sheet or at a merge with a trace already walked. It answers where the water from a dump or a site will go.

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

- **4.01 Cross-section along a line** — a geological section along a line and a stack of surfaces: beds as bands, a distance×elevation drawing, a 3D fence of PolygonZ and surface lines. A single surface is a legitimate case: a section over the terrain without beds, with the frame bottom set by elevation. The order of the surfaces comes from the project layer tree. By default it builds a section for every line of the layer, with a layout of the drawings and a common vertical scale, the sections being told apart by the sec and sec_id fields.
- **4.02 Boreholes on the section (drilling model)** — boreholes from a collar and interval layer pair (the minimal mining-package model) onto every drawing of the definition at once. Fields are found automatically by the contract names, collars are reprojected into the definition CRS, the tolerant reader reports a summary to the log, colours and a legend by code, clipping by the frame and by the drawing bands, collar labels from number, an optional 3D output.
- **4.03 Bed composition on the section** — paints the bed band by a composition grid: continuous grade as slices, categorical rock type as facies zones.
- **4.04 Intersect surfaces with the section** — surface grids onto the section as lines (aquifers, marker surfaces).
- **4.05 Vector intersection with the section** — lines and polygons by exact intersection: a line without Z as a vertical, with Z as a point, a polygon as a band. Clipping from above by the terrain line and from below by the bottom line of the drawing, a per-feature bottom from a field, the attributes of the source layers carried onto the drawing.
- **4.06 Intersect a TIN with the section** — cuts 3D faces and meshes with the section: overhangs and overturned folds are reproduced.
- **4.07 Project objects onto the section** — points, lines and polygons onto the section drawing, the elevation from 3D or a field.
- **4.08 Unproject from the section** — the reverse projection of objects drawn on the section back into real coordinates with Z.
- **4.09 Shaft wall unwrap (beta)** — a cylindrical section around an axis in arc-elevation axes.
- **4.10 Create a section example** — a stack of surfaces, a line and boreholes with elevation fields, to try the section without kriging.
- **4.11 Bed reference template** — adds the bundled bed reference to the project (37 rows of the Verkhnekamskoye deposit): code, bedding order, body kind and colour. It is read by 4.01 and 4.02, and the band and column colours come from it.
- **4.12 Attitude from an outcrop trace** — dip azimuth and dip angle from the trace of a surface on the relief: three trace points give a plane, the attitude goes into the attributes.

### "5. Geological model" group (beta)

- **5.01 Consistency of a bed stack** — checks roofs and floors built separately: pinch-out, negative thickness, overlap of neighbours, a map of the smallest gap, a sign of a reversed order. The bed order comes from the layer tree or the reference.
- **5.02 Example section (demo)** — a teaching section of the Verkhnekamskoye type: the full column from the bed reference, a recumbent fold with triple penetration, a pinch-out, a salt dome with a dissolution mirror, a subcrop map, the water-protective sequence, a drilling model.
- **5.03 Assembly of a stack from the relief** — the column from the top down, body after body along the reference: a raster of thickness from the boreholes, the floor by subtracting from the overlying surface. The thickness is reduced to the vertical by the dip of the hole, the absence of a body in a borehole is a zero. The datum may be a traced contact rather than the relief - then the assembly goes both ways and the error is halved. Overlaps do not happen by construction.
- **5.04 Correction of a stack by the statistics of thicknesses** — a confidence interval from the measurements, cutting the thicknesses by its bounds and reassembling. Separates an artefact of interpolation from geology: on a persistent body a thickness outside the interval is almost certainly a run-away of the method between boreholes.
- **5.05 Model manifest** — the roles of the layers in the project properties: the datum, a contact, the mirror, the collars, the intervals, the axis surveys, the reference, the observations. The tools read the manifest but do not require it.
- **5.06 Folding of a surface** — the spread of elevations around the local slope plus the rate of its growth with scale. A plain variance on a general slope is large everywhere, hence a plane is fitted in the window.

### "6. River hydrology" group

- **6.01 Cross-sections and rating curves** — the dependence of discharge on level for a section: the profile along the vertices, the division into the left bank, the channel and the right bank, the discharge by Manning separately on every part. Roughness and slope from the fields of the section, the slope can be computed from the chain of sections. Probability and observed levels as lines onto the drawing, the footer of a gauging sheet as a row per part, ground elevations and distances as points. An HTML report with the profile and the Q(H) graph on the page itself.
- **6.02 Flood extent polygon** — cutting the surface by a water level with an extent and a raster of depth. The level can be set by a discharge: it is taken backwards along the curve.
- **6.03 Import section tables** — distance and elevation pairs from existing programs become lines with Z. If the sections are digitized on the map, the soundings lie along the real lines. Computation properties are carried along with the profile.
- **6.04 Example river (demo)** — a chain of sections with a known answer, a table of soundings, a valley surface, a reference curve, teaching probability discharges and observed levels.

**Fields of the hydrology group.** Sections: `sec` the name, `km` the chainage in km, `div_l` and `div_r` the part boundaries as distances along the profile in m, `n_left`, `n_channel`, `n_right` the Manning roughness by part (dimensionless), `slope` the slope in m/m, `role` the role of the line when supplied as three lines. Probability discharges: `prob` in percent, `q` in m3/s. Observed levels: `level` in m, `label` as text. The full list of fields of every input and output with units is in the [manual](manual/manual_en.md).

### "7. Fractal analysis" group

- **7.01 Fractal dimension** — a D = 3 − H map by the variogram method in a moving window: contrasts highlight faults and block boundaries.
- **7.02 Box-counting of masks** — one D per binary mask: replacement and workings outlines compared by a number.
- **7.03 Dimension of lines and boundaries** — D of each line by the divider method: diagnostics of oversmoothed isolines.
- **7.04 Minkowski dimension (vectors)** — box-counting directly over lines and polygon boundaries, without rasterization.
- **7.05 Example for fractals (demo)** — a river network with tributary orders, a catchment with a ragged boundary, a coastline.

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

Full list — in [CHANGELOG.md](CHANGELOG.md); `metadata.txt` of the plugin keeps
the last ten versions. The user manual (PDF) is

- **5.6.60—5.6.61** — the gauging section sheet and the thinning of contours. In 1.04 the thinning of contours by a share of a grid cell appeared, a quarter by default: it stands in the shared core of the lines and of the belt boundaries, before the belts are assembled, so the common boundary of neighbouring belts stays common. The form of 6.01 was unloaded, the footer is no longer built as geometry and is drawn by presentation over the attributes of the line of a part, and the spare outputs were removed. The profiles are named morphological sections, and a vertical scale of elevations was added to the common layer of the drawing.
- **5.6.52—5.6.59** — the gauging section sheet. The drawing of a section is given as one line layer: the profile by parts, the lines of the levels, the footer. The parts are told apart by the kind field, and the presentation is hung by a style on it. The ground elevation lies in the Z coordinate of the vertices of the profile, and the properties of a part in the attributes of its line, so the footer can be built by presentation alone. A ratio of the scales appeared, vertical to horizontal: with a map at 1:1000 and a ratio of 10 the vertical scale comes out at 1:100. The footer is built from rows of three kinds: by parts, by points of the profile, and as a continuous band across several parts at once. The chainage is counted from the start of the route. On the rating curve plot the mean velocity joined the discharge and the area.
- **5.6.38—5.6.51** — catchments and the elevations of belts. 2.16 and 2.07 carry the fields of the source object into the output, and 2.17 «Report on a ready catchment» appeared, with the mean slope of the hillsides after SP 33-101-2003. 6.01 gives the rating curve as a vector drawing with scales and ticks of the probability levels. The Z elevation option in 1.04 started working on belts: a vertex takes the nearer of the two boundaries of its belt, and the sampling is two-step — bilinear, and by the nearest cell only where the bilinear one is empty. The guard of undefined names was extended over the whole package.
- **5.6.35—5.6.37** — kriging speed. The system matrix is cached by the set of neighbours, and the neighbours themselves are selected through a grid of cells instead of scanning the whole sample. One and a half times faster on sparse data, fourfold on dense. The estimates did not change in a single cell. An overflow in the power variogram model is fixed.
- **5.6.32—5.6.33** — pits and common conveniences. The round trip from a section works over every wall: an object returns to the line of its own section rather than the first one. Clipping by a mask appeared in minimum curvature, indicator kriging and gaussian simulation. The value field Z became optional in six tools: the elevation is taken from the geometry of the point.
- **5.6.21—5.6.30** — local anisotropy and sections. Kriging handles a direction of elongation that varies across the ground: every sample carries its own strike in an attribute, and the map follows the curved structure rather than a single azimuth over the area. Discarded outliers are given as a separate layer with a reason. The computation runs inside the clipping mask only: on a diagonally elongated area that is up to nine times faster. Object projection works over every section of the definition rather than the first one. The section gained a frame top and a step for the elevation ticks. The model manifest now works both ways: 4.01 and 4.02 find layers by their roles.
- **5.6.0—5.6.20** — sections and variograms. The variogram moved out of the QGIS settings into a table layer: 1.05 fits and records the model, 1.06 writes the anisotropy into it, 1.02 and 1.07 read it. The table travels with the project, goes into the repository and is edited in the QGIS attribute table. 4.02 accepts inclinometry: the axis is built by minimum curvature, the intervals are carried along it, and the corridor selection runs over the whole axis. The interval table became optional, and on a hole_id mismatch the tool names the keys from both sides and recognises the cause. 2.22 was renamed to Profiling of slopes and now accepts spot heights and polygons. 4.07 gained the checkboxes for the name and the style of the source layer.
- **5.0.0** — faults became a running theme. Three tools accept them: in 1.02 a barrier of visibility in the neighbourhood selection, in 1.03 a barrier on the grid edges with a membrane at the line, in 1.04 the cutting of isolines along the line and a corridor that removes the strip of interpolation across the break. The fault is never turned into cells, so there are no steps along cell edges and no need to split the area into blocks. A dying end is told apart from one abutting a neighbouring fault, and ends left short are named in the log. The documentation of parameters is complete and guarded by a test, and the manual now carries the names of the tool groups.
- **4.96.0—4.99.1** — faults carried through to the map. In 1.02 the barrier is tested by exact geometry, the fault is never turned into cells and no empty cells appear along the line. In 1.03 faults arrived for the first time: the barrier lives on the edges between nodes, and a membrane works right at the line. In 1.04 the isolines are cut along the line, and a corridor one cell wide removes the strip of crowded isolines across the break. The price of the approximation is named too: the covariances between measurements stay Euclidean, otherwise the kriging system becomes singular. Faults are documented in the tool help and in the manual.
- **4.92.0—4.95.0** — faults arrived in 1.02: lines act as barriers of influence, a sample beyond a fault does not enter the neighbourhood of a cell, and the surface breaks along the line. A cell on the line itself is written as nodata, so the break is visible to everything that works off the grid and contour polygons close along the fault by themselves. A fallback filter of narrow strips by thickness was added to 1.04. Tool names were brought to a single form. In the tests, guards that stayed silent in a full run were repaired.
- **4.91.0** — the geological model has grown up to assembly: a stack from the relief and from a datum surface, a correction by the statistics of thicknesses, the model manifest, a map of folding, telling a cut from a pinch-out by the mirror. In hydrology the flood extent is built for any desired discharge over a curve already computed.
- **4.74.1** — probability and observed levels on the drawing, the footer of a gauging sheet, a report with graphics: the profile with the levels and the graph of discharge against level on the page itself.
- **4.69.1** — the new "6. River hydrology" group: cross-sections and rating curves by Manning with a separate count over the channel and the floodplains, a flood extent polygon with the backward move along the curve, an import of sounding tables laid along digitized lines, a demo river with a known answer. Fractal analysis moved to the seventh group, the geological model is marked beta.
- **4.63.0** — 4.02 gains a new output: label anchors of intervals, a point in the middle of every interval with all its attributes. Composite grade labels attach to points and place normally.
- **4.62.0** — the demo became a full section of the Verkhnekamskoye type: the whole column from the reference, erosion from the dissolution level, a subcrop map, the water-protective sequence, a folder output.
- **4.59.0–4.60.0** — a salt dome with a dissolved top in the demo, the cut carried onto the mirror without steps, the group layer order in the tree made deterministic.
- **4.58.0–4.58.1** — the fold sized to the stack thickness, the pinch-out boundary bent, the demo reference aligned with the layers.
- **4.56.0–4.57.3** — the new "5. Geological model" group: 5.01 stack consistency, 5.02 the demo stack. Fractal analysis became the sixth group.
- **Ready Processing models** in `models/`: the "Terrain from a topographic plan" chain (2.22 -> 2.20 -> 2.03) in a single run, with the mute and unpaired line outputs.
- The **"Terrain from a topographic plan"** cheat sheet (RU/EN): the 2.22 → 2.20 → 2.03 chain for areal quarries, cuts, fills and dumps, where the standard describes no contours inside.

- **4.55.0** — a quick start in the manuals (seven scenarios by task rather than by tool), "What to build" presets in the 2.10 and 2.21 demos, thinning of band vertices in 4.05, the tool-tree generator repaired.
- **4.51.0** — the consolidated release of the dip branch: 4.05 with the apparent angle and inclined bands, 4.12 "Attitude from an outcrop trace", a demo with an analytic reference, the "Geological section" cheat-sheet article.
- **4.47.0** — the DEM in 2.20 became optional: with elevations on the lines the pairs are assembled without terrain, and the topographic scenario no longer goes in a circle.
- **4.46.0** — 2.20 reads the kind field tolerantly: classifier codes and names carrying the words for a crest or a toe, with a table of decisions in the log.
- **4.45.0** — the weight of measured elevations in 2.03: in a shared cell a measurement outweighs a digitized contour vertex.
- **4.44.0** — 2.22 "Elevations from adjoining contours": mute crests receive a profile from the adjoining node points.
- **4.43.0** — the surface between structural lines in 2.03: the Top of forms and Bottom of forms inputs, a core on the distance method, a section in the manual.
- **4.42.0** — a bent-crest fill in the demo to measure the price of the distance method, the detector thresholds gained a physical floor instead of a tie to the maximum.
- **4.41.0** — the homonym "подошва" untangled in the translation dictionary (bed bottom vs bench toe), the test now fails on conflicting keys.
- **4.40.0** — 2.20 moved from pairs to forms: one toe with a set of crests at it sharing a link field, no duplicates in the Bottom layer.
- **4.39.0** — the colouring of the 2.19 candidates became meaningful: classes appear only when the drop really varies.
- **4.38.0** — the extent in 2.21 places the demo and no longer stretches it to the size of the extent.
- **4.37.0** — the style of the output layers is no longer overwritten by the grouping in the layer tree.
- **4.36.0** — the styling of structural lines moved from QML into code, the drop probe base in 2.19 raised to 8 cells.
- **4.35.0** — 2.20 "Crests and toes into work": elevations off the DEM, pairs by descending the slope, ready inputs for surface building.
- **4.34.0** — 2.19 "Crest and toe candidates" and 2.21 "Create a demo open pit": the start of the structural-lines branch.
- **4.33.0** — two checkboxes in 4.05: keep the name and keep the style of the source layer. A categorised geology colouring lands on the section as it is.
- **4.32.0** — 3.01 got a "Nugget share" parameter: zero makes the estimate exact next to the data, and a borehole keeps its class in its own cell. The fitted nugget and range per class are printed to the log.
- **4.31.0** — 4.05 clips zones and faults from below by the bottom line of the drawing, a pair to the clipping from above. The "beta" mark is off 4.07 and 4.08.
- **4.30.0** — 4.05 clips zones and faults from above by the terrain line of the drawing, the bottom of bands is read from a feature field.
- **4.29.0** — 4.05 carries the feature attributes onto the drawing: bands and verticals are coloured by their own fields.
- **4.28.0** — thalwegs in 2.03 may carry elevations: a survey along the channel sets the bed, not only the direction of fall.
- **4.27.0** — the 4.10 demo outputs its own bed reference matching the codes of its intervals.
- **4.26.1** — the demo boreholes are given a section of their own in the 4.10 help, in the manuals and in the README.
- **4.26.0** — 2.03 got a build boundary: a polygon limits the surface, the mask is applied after interpolation.
- **4.25.0** — 2.18 outputs a layer of areas with volumes in the attributes and can clip the difference raster by those areas.
- **4.24.0** — catalogue release, folding in the 4.18-4.23 nightlies.
- **4.23.1** — with a single surface 4.01 no longer creates layers for bed bands and the 3D fence: suppressing them in the result is not enough, a destination parameter registers the layer for loading by itself.
- **4.23.0** — the section over a single surface was brought to a working state, plus the new "Surface lines on the drawing" output.
- **4.22.1** — layer memory: the liveness guard was closed on its second entrance, direct parameter defaults used to bypass it.
- **4.22.0** — 4.01 builds a section over a single surface, and the frame bottom by elevation appeared in the advanced parameters.
- **4.21.1** — the 2.18 statement prints numbers with digit grouping, areas in hectares, and a total row in the per-area table.
- **4.21.0** — demo relief 2.10 outputs a pair of surfaces for 2.18: a design pad and work areas. The pad elevation is the mean, at which the net is exactly zero.
- **4.20.0** — the new 2.18 Cut and fill (earthwork volumes) tool.
- **4.19.1** — 2.16 renamed to "Catchment. Lines and outlines": the tool accepts polygons as well.
- **4.19.0** — 2.17 Densify contours by secants was removed. The technique was checked by measurement: on 2.03 Topo2Raster the attraction of elevations to the levels grew from 1.09 to 1.14, as did the curvature and the depression filling, so the technique gave no benefit.
- **4.18.3** — the memory of input layers no longer substitutes dead references: the id is checked for resolvability in the current project.
- **4.17.0** — pre-release pass: the RU and EN manuals gained a section on 4.11, parameter labels were aligned, the PDFs were rebuilt.
- **4.16.1** — the composite bed КрIIIа+б (the sum of КрIIIа, КрIIIа-б and КрIIIб) is back in the reference template, above КрIIIб-в. Composite bodies are painted grey #969696: the colour of a conglomerate does not follow from its parts, a person sets it.
- **4.16.0** — the new 4.11 Bed reference template tool adds the template straight to the project. The reference drop-down in 4.01 and 4.02 is limited to tables without geometry. The word optional is gone from the parameter labels, QGIS adds that mark itself.
- **4.15.0** — the palettes folder and all the Leapfrog palette code are gone: the sections now have a single source of colour, the bed reference. The code matching rules (KpII_top finds КрII) are kept in full.
- **4.14.0** — the bed reference template ships with the plugin (the templates folder, xlsx and csv). Manuals: the colour sections of 4.01 and 4.02 were rewritten around the reference, the previous ones described a palette the tools no longer had.
- **4.13.0** — the burn in 2.16 gained the chute-sloping-to-the-outfall profile: the bottom descends monotonically and the water does not spill over the bank on wavy relief (a technique from Ivan Ivanov).
- **4.12.0** — 2.09 is renamed Peaks and pits and always searches both signs, the elevation goes into the geometry Z. Spot points at the extremes remove the flat caps of a surface built from contours alone.
- **4.11.1** — vector boundaries from the probabilities in 3.01: level lines and band polygons between them with a ready colouring from green to red. The levels are set by a parameter, 0.25, 0.5 and 0.75 by default. They are built from the probability channel rather than from the zone map: a zone holds only the winner in a cell, so a contour of it runs in steps along the cell edges.
- **4.9.0** — a nugget breakdown in 1.05: the first lag and, by name, the pairs of points that lifted it. A warning when the variogram fit is good for nothing, R2 below 0.1 or a nugget above half of the total sill.
- **4.8.2** — the side switch in 1.04 now reverses the labels as well, not the hachures alone.
- **4.8.1** — fixed the write-value-into-Z tick: the layer with Z was written to a separate file while the flat original was loaded into the project.
- **4.8.0** — 2.16 accepts polygons as well as lines: an open pit outline, a wall, any closed intake. The write-value-into-Z tick in 1.04. A scope note in 2.15 and 2.16.
- **4.7.1** — palette reading without the xml modules: a self-written scanner replaces the standard parser that the repository security scan rejects.
- **4.7.0** — the Leapfrog colour palette (.lfc) in 4.01 and 4.02, the new 2.16 Catchment of a line (ditch) tool, demo ditch traces in 2.10, and gap counting in borehole columns.
- **4.6.1** — Qt6 compatibility per the repository check: the fallback colour category used an empty QVariant instead of NULL from qgis.core. The pytest cache is no longer shipped, the secret scanner treated it as a finding.
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
