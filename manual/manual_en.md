---
title: "Isoliner - grids and isolines"
lang: en
toc-title: "Contents"
---

# Introduction

Isoliner is a Processing provider for interpolating point data and building isolines. The kriging core is the KB2D algorithm from GSLIB. The tools are split into two groups: **Grid and isolines** - seven tools of the main processing flow, and **Additional tools** - two specialised computations.

**2D Kriging (points → raster)** - ordinary or simple kriging over a point layer.

**Isolines from raster** - isolines (lines) and contour polygons (bands between isolines) whose boundaries coincide with the lines.

**Variogram map (anisotropy)** - the γ(h_x, h_y) surface with an azimuth and anisotropy estimate, to account for directionality in kriging.

**Variogram cross-validation** - leave-one-out checking to validate and tune kriging parameters by error rather than by eye.

**Create sample wells (demo)** - generates a training point layer with a spatial structure (roof, thickness, component grade) for learning and testing without real data.

The **Additional tools** group holds two specialised computations.

**Categorical indicator kriging** - a class-probability map from a categorical field (mineral type, lithotype): an indicator is built per class and kriged separately, giving a probability raster, a zone map and a confidence raster.

**Hydraulic gradient and flow direction** - from a head raster it builds the hydraulic gradient magnitude, the flow-direction azimuth (down-gradient) and a point layer of flow vectors styled as arrows right away. Hydrogeology without permeability.

Suitable for roof elevations, thicknesses, geomechanical properties, chemistry and any numeric well attribute.

A few terms used below. A variogram describes how much more strongly values differ as the distance between points grows. The sill is the level it reaches (close to the data variance). The nugget (from the "nugget effect") is the jump of the variogram at zero - the scatter at arbitrarily small distances caused by measurement noise and microvariability.

## Installation and location

The main way is from the official QGIS repository. Open Plugins → Manage and Install Plugins → the **All** tab, type "Isoliner" in the search, select the plugin and click **Install**. When installed from the repository, QGIS itself reports new versions and updates the plugin at the press of a button.

![Module tools in the Processing panel: the Isoliner → Grid and isolines group and its tools.](images/ui_toolbox.png){width=55%}

The alternative way is from a ZIP file. Plugins → Manage and Install Plugins → Install from ZIP. This is handy for offline installation and pre-release builds.

After installation the tools appear in the **Processing** panel: provider **Isoliner**, groups **Grid and isolines** and **Additional tools**. Requirements: QGIS 3.16+. There are no external dependencies - only NumPy, GDAL and the built-in Processing algorithms shipped with QGIS are used.

## Updating

When installed from the repository, QGIS shows a notification about a new version - an icon in the status bar and a list on the **Upgradeable** tab of the plugin manager. Updating is a single click. When installed from ZIP, the new version is installed the same way, over the old one.

The plugin reloads cleanly on the fly, no QGIS restart is required. For a quick code reload during development the Plugin Reloader plugin is convenient ("Reload a plugin…" button). Pick Isoliner - the provider and all tools re-register immediately.

## Opening the help

Each tool's dialog has a **Help** button that opens this manual (the PDF bundled with the plugin; on an English interface the English manual opens). The right-hand panel of the dialog additionally shows a short hint for the tool.

# General workflow

A typical scenario has two steps:

2D Kriging: from a point layer and a numeric Z field a raster is built (a regular grid of values).

Isolines from raster: from the resulting raster, isolines and, if needed, filled contour polygons are built.

The steps are independent: **Isolines from raster** works with any raster, not only with a kriging result.

The tools are grouped into two Processing groups. The "Grid and isolines" group is the main processing flow, from kriging to isolines. The "Additional tools" group holds the specialised computations, categorical indicator kriging and the hydraulic gradient with flow direction.

![The whole process on a generated example: wells with measurements (left) are turned into a continuous grid by kriging (centre), from which isolines and contour polygons are built (right).](images/schema_process.png){width=98%}

# 2D Kriging (points → raster)

Ordinary (OK) or simple (SK) kriging over a point layer. Coincident points (the same XY) are averaged over Z. At grid nodes the values of the source points are reproduced exactly (with a zero nugget).

![Kriging estimates the node value as a weighted mean of the nearest wells: the closer the well, the larger its weight. Weights come from the variogram.](images/kriging_weights.png){width=70%}

Main parameters:

| Parameter | What it sets | Default / advice |
|---|---|---|
| Point layer | Source points (wells) for interpolation. | - |
| Selected features only | Compute only over the layer's selected points. | off |
| Value field (Z) | The numeric attribute that is interpolated: roof elevation, thickness, geomechanical property, chemistry, etc. | remembered between runs |
| Kriging type | Ordinary (OK) - estimates the mean locally itself. Simple (SK) - uses the specified **Mean**. | OK |
| Search radius | Radius of the search window for neighbouring points around a node. 0 = whole sample. | 0 (whole sample) |
| Min. number of points | If the window has fewer points, the node stays empty (nodata). | 1 |
| Max. number of points | How many nearest points enter the kriging system. | 24 |
| Cell size | Grid step. 0 = auto = min(extent)/50. | smaller = smoother, but slower |
| Raster extent | The computation rectangle. By layer by default. | by layer |
| Clip to well hull | The raster is clipped to the convex hull of all points - removes extrapolation in empty corners. | recommended on |
| Hull buffer | Expand the hull outward by N map units. | 0 |
| Clip mask | Your own polygon instead of the hull (takes priority) - handy for concave areas. | - |
| Load processing profile | Substitutes a saved profile (nugget, variogram structure, outliers) over the dialog fields. The list refreshes when the window opens. | (none) |

![The 2D Kriging dialog: main parameters. On the right - the short built-in help.](images/ui_kriging_main.png){width=82%}

![For each node only wells within the search radius are taken, and no more than the set number of nearest ones. Points beyond the radius do not take part.](images/search_radius.png){width=56%}

## Automatic values

Cell size = min(extent width, height) / 50.

Variogram correlation range = max(extent width, height) / 3.

Search radius (when 0) = the extent diagonal, i.e. the whole sample is taken.

## Clip to well hull

Kriging computes the whole rectangular extent, so outside the data area the values are extrapolation and produce artefacts (long "fan" isolines in empty corners). The **Clip to well hull** option builds the convex hull of all points (with an optional buffer) and clips the raster to it. The extrapolation disappears. If the actual boundary of the area is concave, set your own polygon in the **Clip mask** - it takes priority over the hull.

## Variogram and nugget

![The variogram scheme: nugget C0, structural contribution C, sill (C0+C) and correlation range a.](images/variogram.png){width=85%}

Kriging relies on a variogram model - it describes how strongly the Z values in two points differ depending on the distance between them. By this model a weight is assigned to each neighbouring well. The model is set in the **Advanced Parameters** section.

![The Advanced Parameters section of the 2D Kriging dialog: nugget C0, one variogram structure (model, sill, range, azimuth, anisotropy) and outlier removal at the end.](images/ui_kriging_adv.png){width=82%}

Variogram model: nugget C0, sill (C0 + C) and correlation range a.

### Nugget C0

The nugget is the value the variogram curve tends to as the distance tends to zero. In theory the discrepancy at zero distance should be zero (a point compared with itself), but in practice a step remains. It reflects the fact that the data at very small distances still do not match: measurement and digitizing error, microvariability at a scale finer than the well spacing, the discrepancy of duplicates at one point.

![The nugget close up: the model starts at zero not from 0 but from a "jump" C0. This is the scatter at very short distances (measurement error, microvariability). The plateau C0+C ≈ the data variance.](images/nugget_closeup.png){width=80%}

How the nugget affects the result:

C0 = 0 (default) - kriging is an exact interpolator: the surface must pass exactly through every well. An isolated well with a Z outlier turns into a cone (a "bull's eye").

C0 > 0 - kriging stops reproducing the value at the measurement point exactly and becomes a smoother: near a well the estimate is pulled toward the local mean. The larger the nugget fraction C0 / (C0 + C), the stronger the smoothing.

C0 = the whole sill (pure nugget) - the spatial link is lost, the surface degenerates into a plain mean. This is too much.

**Important - units.** Nugget C0 and sill are set in **absolute units of the data variance** (squared units of Z), not in 0-1 fractions. The default "1" for the sill is a placeholder that almost always needs changing: set the total sill (C0 + the structure contributions C) **close to the data variance**. The level of smoothing is determined not by the absolute nugget value but by its **fraction of the sill** C0 / (C0 + C). A practical order: take the total sill ≈ the variance, then the nugget = 0.2-0.4 of it (i.e. 0.2-0.4 × the variance - an absolute number, not 0.2-0.4 as such). The smaller the nugget, the more detail, but also more local peaks. The larger it is, the smoother the surface, but real structure may be smoothed away. The tool prints the data variance to the Log at start - that is your reference for choosing the sill.

### Structures, range and anisotropy

The sill (plateau) is the level the variogram reaches. It is the sum of the nugget C0 and the structure contribution C. A structure is set by a model (spherical, exponential, Gaussian or power), a contribution C, a range a, an azimuth and an anisotropy.

**Sill: meaning and order of magnitude.** The sill is the upper limit of the differences between points: how much, on average, distant wells differ. It is practically equal to the ordinary data variance. An example for KCl: mean ≈ 25 %, variance ≈ 47.6 (%²), i.e. σ ≈ 6.9 %. So the total sill is set ≈ 47.6. If the nugget C0 ≈ 17 (about 0.35 of the sill), then the structural contribution of the first structure C ≈ 47.6 − 17 ≈ 30. The absolute scale does not affect the grid itself - only the C0 : C ratio matters for the estimates. But it is needed so that the standard-error map and the MSDR in cross-validation are at the real scale (total sill ≈ variance → MSDR ≈ 1). So: do not leave the sill at the default 1, raise it to the data variance.

**Choosing a model.** The spherical and exponential models suit most tasks. The power model has no sill or range in the usual sense: it is used when variability grows with distance and does not reach a plateau (non-stationary increments), so the contribution and range fields for it are conditional. Use the Gaussian model with caution: at a zero or very small nugget it gives a numerically unstable system and artefacts (oscillations, negative weights). Therefore, when the Gaussian model is chosen, the tool enforces a small minimum nugget; set one yourself where possible.

**Data type and mode.** Different data need different settings. Smooth structural surfaces (roof and floor elevations, thicknesses) are better modelled with a long range or a power model under a wide (global, 0) search radius - then the surface comes out smooth. A short radius with local search on such data gives "bull's eyes" and discontinuities in the estimate when the set of neighbouring wells changes. For grades and chemistry (geomechanical properties, gas hazard) kriging works in its own right: here a correct nugget matters and, for a strongly skewed distribution, a data transform (see below on outliers).

The correlation range a is the distance at which the variogram reaches the plateau. Beyond it points practically do not influence each other. At 0 the automatic value max(extent)/3 is taken.

Anisotropy is set by the major-axis azimuth and the ratio of ranges (minor/major). A value of 1 is isotropic (the influence is the same in every direction). A value below 1 shortens the correlation across the major axis - useful for elongated geological structures.

![Anisotropy: if the body is elongated, correlation runs farther along it than across. The search ellipse is set by the major-axis azimuth and the axis ratio (minor/major).](images/anisotropy.png){width=70%}

| Parameter | What it sets | Default / advice |
|---|---|---|
| Mean for simple kriging | Used only with the SK type. | 0 |
| Nugget C0 | The "noise"/jump of the variogram at zero. Suppresses local peaks. In absolute variance units. | 0. For smoothing 0.2-0.4 of the sill |
| Structure i · model | Variogram shape: spherical, exponential, Gaussian, power. | spherical |
| Structure i · sill/contribution C | The structure's contribution to the sill (abs. variance units). The sum C0+C ≈ the data variance. | str. 1 = 1 (replace with ≈ the variance) |
| Structure i · correlation range a | The distance to reach the plateau. 0 = auto = max(extent)/3. | 0 (auto) |
| Structure i · azimuth, ° | The direction of the anisotropy major axis. | 0 |
| Structure i · anisotropy (minor/major) | The ratio of ranges across/along the axis. 1 = isotropic. | 1 |

&nbsp;

![Experimental KCl variogram for the KrII seam and the fitted model: nugget C0≈17, the sill matched the data variance, range ≈13 km. Points beyond the sill are a regional trend.](images/krii_variogram.png){width=85%}

This is how the scheme looks on real data. A variogram is built from the wells: for pairs of points the semivariance is computed and averaged over distances - the result is a cloud (green points) under which a model (the curve) is fitted. From it the kriging parameters are set: the height of the "jump" at zero is the nugget C0, the plateau is the sill (usually close to the data variance), the distance to the plateau is the range a. If at large distances the points rise above the sill, as here, it is a regional trend (non-stationarity). It is either accounted for separately or the search radius is limited.

## Outlier removal

Outliers are anomalously high (or erroneous) values that distort the estimate: a few grade "bonanzas" can pull the whole grade map onto themselves, while clear errors (e.g. a negative thickness) spoil the surface. The **2D Kriging** tool lets you bound such samples right during the computation, without editing the source data. The parameters are in the **Advanced** section.

Removal and capping are a crude practical tool against clear errors. For grades and chemistry be careful: extreme values are often not noise but signal (e.g. contamination spots), and blindly clipping the distribution tails is not worth it. For strongly skewed data it is more correct not to clip the samples but to transform them to something close to normal (logarithm, Box-Cox) or to use indicator kriging - that is beyond removal, but that is exactly how heavy tails are handled in the geostatistics of ores and contamination.

![Outlier removal by example: on the left three outliers give "bull's eyes" (hot spots), on the right after capping to the upper bound the field is calm.](images/outlier_before_after.png){width=98%}

**Two modes.** **Remove** - samples outside the allowed range are discarded (for clearly broken records). **Cap (capping)** - values outside the range are clamped to the bound, while the point itself stays in the computation. Capping is the classic technique for grade outliers: the point's position is not lost, but its influence is limited. The mode is switched by the **Cap to bound (capping) instead of removing** checkbox.

**Absolute bounds.** The **Lower value bound** and **Upper value bound** set thresholds in Z units directly. An empty field means the bound is not set. They take priority over the percentile. Example: for thickness set the lower bound to 0 - negative values go away, and the upper, say, to 30 - a clear outlier at 122 m goes away.

**Percentile bounds.** The p-th percentile is the value below which p% of all samples lie. For example, the 5th percentile is the threshold below which only the 5% smallest values lie. The 95th is the threshold above which the 5% largest lie. The **Outliers: clip percentile, %** parameter sets the number p, and the bounds are taken symmetrically: from the p-th to the (100−p)-th percentile. So p = 2 means "treat as outliers the 2% lowest and 2% highest samples": everything below the 2nd and above the 98th percentile is either removed or capped. The larger p, the more aggressive the clipping. P = 0 disables the percentile mode. The convenience is that you do not need to know the absolute thresholds - they are computed from the data itself and suit any distribution and scale.

**Two-sidedness - important for chemistry.** The percentile mode cuts both tails - upper and lower. For grades this is dangerous: KCl = 0 in replacement zones is real geology, and clipping the lower tail would wrongly raise the "empty" areas. So for grade clip only from above: leave the **Lower value bound** empty and set the **Upper** as an absolute (or use the percentile, knowing the bottom will be affected too). For elevations and thicknesses two-sided clipping is usually appropriate.

**Order and Log.** The filter is applied before averaging coincident points. The tool's Log reports how many samples were removed or capped and within which bounds - handy for checking.

## Kriging standard error

![The standard-error map: dark near wells (green points) - the estimate is trustworthy, light in empty corners - few data.](images/stderr_map.png){width=66%}

Besides the estimate itself, kriging gives at every node the error variance - a measure of uncertainty. Its square root, the standard error, is output as an optional second raster (the **Kriging standard error** parameter of the **2D Kriging** tool). The units are the same as the interpolated quantity Z.

A key property: the standard error depends on the geometry of the well layout and the variogram model, but not on the Z values themselves. So it is a map of the observation network's reliability, not of the data scatter. At a well point (with a zero nugget) the error equals zero - there the value is known exactly. As one moves away from wells it grows, and in areas without data it reaches a maximum (roughly the square root of the sill).

**How to read it.** Dark (small) values - the estimate is trustworthy: enough wells nearby. Light (large) - the estimate rests on distant points, effectively extrapolation. These are the first candidates for infill drilling. It is more convenient to compare relatively (where it is larger or smaller), because the absolute value depends on the variogram scale (the sill S1_SILL).

**Important.** This is a model estimate: it is as valid as the variogram you set (nugget, range, anisotropy). At a nugget above zero the error at wells is not zero - the nugget sets a lower "floor" of uncertainty. The standard error is not a strict confidence interval, but as a relative uncertainty map it is very useful.

**Styling.** Give the layer graduated symbology by value (e.g. from dark to red) - and it is immediately clear where the map is reliable and where not.

## Trend removal (regression kriging)

Ordinary kriging estimates the mean locally, within the search window, so it follows a smoothly varying mean on its own. The difficulty appears when the field has a pronounced regional component, such as a general dip of the seam across the area. Then the experimental variogram of the raw value gets inflated: the range is overstated, there is no sill, the shape resembles a power model, and a stable model is hard to fit.

The **Remove polynomial trend** checkbox removes the regional component by least squares before kriging. The residuals are then kriged and the trend is added back to the estimate. The residual variogram returns to its normal shape: it reaches a sill with a nugget, and the range reflects the true scale of correlation rather than the span of the trend. The **Trend degree** field selects a plane or a quadratic surface.

![Trend removal on real data: a seam surface with a pronounced regional dip. The trend is removed by a polynomial, the residuals are kriged, and the trend is added back to the estimate.](images/rk_plasts_real.png){width=85%}

Trend removal helps where the dip is uniform, within a single mine district or a local area. It is not meant for the whole deposit at once. Neighbouring blocks are offset in height, a single polynomial describes them poorly, and local kriging already holds the varying mean, so removing a global trend there tends to hurt. The variogram shows whether it is warranted. If the raw value has a range comparable to the size of the area and no clear sill, a trend is present and worth removing. If the raw variogram already reaches a sill with a small nugget, there is nothing to remove.

Degree 1 is usually enough. Degree 2 captures curvature but can absorb part of the real structure into the trend, so after using it look at the residual variogram. If the sill and nugget become less defined, go back to degree 1. The same checkbox is in the **Variogram cross-validation** tool, where the trend is refit at each validation step, so the gain or loss from removing the trend is visible directly in the RMSE.

After trend removal, fit the variogram on the residuals. In this mode the standard-error raster is the kriging error of the residuals, the trend is treated as deterministic and adds no error of its own.

## Block kriging

Ordinary kriging estimates the value at a point, at the centre of a grid cell. In mining, however, what is usually needed is not a point value but the average over an area, over a mining block, a panel or a reserve-estimation cell. The grade of a useful component in a block is the average over its area, and estimating it as the value at a single point is not quite correct. This is what the **Block kriging** checkbox is for.

When enabled, the mode estimates the average over the grid cell rather than the value at its centre. The cell is conceptually split into an N×N grid of points, the count set by the **Block discretization, N×N per cell** field, and the covariances in the kriging system are averaged over those points. The system is thus solved not for one point but for the whole block at once. This is the classical GSLIB block-kriging scheme.

The block estimate has two consequences, both useful for reserve estimation. The surface comes out smoother than the point one, because averaging over the block damps small fluctuations. And the kriging standard error comes out lower than the point one, because an area average is estimated more reliably than a value at a single point. There is one price. Block kriging does not reproduce the samples at nodes exactly. The average over a block, even one centred on a borehole, does not equal the value at the borehole itself, and that is as it should be.

A 4×4 discretization is almost always enough. A larger N takes longer to compute while accuracy grows only slightly. At N equal to one block kriging degenerates into point kriging, so the minimum value of the field is two, and the mode itself is off by default.

Block kriging combines with trend removal. The residuals are kriged over the block and the trend is added back to the estimate. It also combines with grid smoothing, but block averaging alone is usually enough and extra smoothing is not needed.


# Isolines from raster

Builds isolines (lines) and, by default, contour polygons. Levels are set by a uniform step or by an explicit list. Parameters:

![The Isolines from raster dialog.](images/ui_isolines.png){width=82%}

| Parameter | What it sets | Default / advice |
|---|---|---|
| Raster | The input raster (e.g. a kriging result). | - |
| Isoline step | A uniform step over Z. 0 = set **Explicit levels**. | - |
| Base level (offset) | Anchors the level grid (levels are multiples of the step from the offset). | 0 |
| Explicit levels | A space-separated list of levels. Takes priority over the step. The decimal separator is a comma or a dot. | - |
| Index isoline every N-th | Every N-th line is flagged is_index = 1 (for thickening). 0 = off. | 5 |
| Min. line length | Drop lines shorter than the threshold (map units). 0 = no filter. | - |
| Bicubic isoline smoothing | Densifies the grid (×2…×4) by bicubic interpolation before contouring - the main isoline-smoothing method, removes "octagons" from a coarse grid. Works for both lines and polygons. off = no densification. | off (×4 on a coarse grid) |
| Line rounding (Chaikin), iterations | An extra light line rounding (Chaikin). Weaker than bicubic smoothing; usually not needed if it is on. 0 = off. | 2 |
| Value field name | The name of the level attribute in the output lines. | ELEV |
| Band (adv.) | The band number of the input raster. | 1 |
| Isolines / Contour polygons | The output layers. Polygons are built by default into a temporary layer. | - |

Output fields: for lines - the level value (ELEV by default) and is_index (1 on index isolines). For polygons - ELEV_MIN and ELEV_MAX (the band range).

## Isoline smoothing

The main way to smooth isolines in this tool is **bicubic smoothing**: before contouring, the grid is densified by bicubic interpolation (×2…×4), and the contours are built on the finer grid. On a coarse grid isolines otherwise look like "octagons" (vertices are placed at cell edges) - densification removes this angularity topologically cleanly. It is implemented in pure NumPy, with no external dependencies; nodata boundaries and internal data "windows" are preserved. Densification affects both lines and contour polygons - the band boundaries still coincide with the isolines. The cost is more cells (×4 = 16 times more), so on a very large grid start with ×2.

In addition there is a light line rounding by the **Chaikin** algorithm (number of iterations). It is weaker than the bicubic one and usually not needed if densification is on; it makes sense as a fast alternative on a coarse grid when you do not want to densify.

Smoothing of the field itself (Gaussian, over the raster) is a separate operation done not here but in the **2D Kriging** tool: there it goes over the grid before contouring and removes not angularity but field bumpiness (the "bull's eyes" around wells). Bicubic smoothing and Gaussian field smoothing complement each other: the first cures grid angularity, the second cures data bumpiness. The contoured kriging raster is not changed in the process - only a temporary copy is smoothed.

## Contour polygons (bands)

Contour polygons are filled bands between neighbouring isolines. They are built not by classifying raster "steps" but by polygonizing the smoothed isolines themselves together with the outline of the raster's valid area: line ends are snapped to the outline, the network is noded and polygonized. The level range of each band is determined by sampling the raster at a representative point of the polygon.

Thanks to this the polygon boundaries coincide with the isolines, and the coverage is continuous (no holes). The polygons carry the ELEV_MIN and ELEV_MAX fields. By default they are built into a temporary layer. To not build them, clear the **Contour polygons** field.

## Layer styling

Lines: set rule-based symbology on is_index - give the index isolines (is_index = 1) a larger width. Label by the level field (ELEV).

Polygons are created with a single symbol. For range fills set graduated symbology by ELEV_MIN (or ELEV_MAX).

The isoline layer is automatically placed above the polygon layer so the lines show over the fill.

# Variogram (experimental)

The tool builds an experimental semivariogram from points, fits a model to it if needed, and produces an HTML report with a chart. It does not compute a grid and is not part of the kriging computation chain directly. Its job is diagnostic: to show the structure of the data's spatial variability and to help set the variogram parameters deliberately, by the look of the cloud rather than by eye.

## Why the preview is needed

Kriging relies on a variogram model: nugget, sill and range. The interpolation weights and the standard-error map depend on them. It is tempting to hand the fitting of these numbers to automation and not think about them. On a clustered drilling grid this is dangerous. Clusters of close wells give a huge number of pairs at short distances and press down the near part of the variogram, so an auto-fit on such a cloud easily yields a confidently wrong nugget. The preview removes this problem: the geologist sees the pair cloud itself, understands where the data are dense and where sparse, and fits the model knowing what lies beneath it.

That is why model fitting in the tool is given as a recommendation, not a finished result. The numbers it suggests should be checked against the look of the chart and only then carried into kriging.

## A short theory

The semivariogram describes how statistically related the parameter values are in two points depending on the distance between them. For a pair of points separated by a distance h, half the squared difference of their values is taken (the semivariance of the increment). These quantities are averaged over distance intervals (lags), giving the curve γ(h). It is a measure not of the "average difference" of values but of the statistical reliability of predicting a value from a neighbour: the smaller γ, the closer the link.

A typical curve has three characteristics. The nugget C0 is the value γ tends to as the distance tends to zero. It reflects variability at a scale finer than the network step, plus measurement error. The sill is the level the curve reaches at large distances. The full sill equals the sum of the nugget and the structural contributions and is ideally close to the data variance. The range (a) is the distance at which the curve reaches the sill - i.e. at which the spatial correlation drops practically to zero. Beyond it points are statistically unrelated. For the exponential and Gaussian models the sill is reached asymptotically, so for them the range is effective.

The nugget and contributions in the tool are set in absolute units of the parameter variance, not as fractions of one. The reference for the full sill is the data variance, which is shown in the report summary.

## Parameters

![The Variogram (experimental) dialog, scrolled to the Advanced Parameters section: model fitting, one structure (model, sill, range, azimuth, anisotropy), outlier removal at the end and the Save profile as field.](images/variogram_dialog.png){width=82%}

| Parameter | What it sets | Default / advice |
|----------|-----------|----------------------|
| Points with values | A point layer of wells or samples. | - |
| Z value field | The numeric attribute to analyse: roof elevation, thickness, grade. | remembered between runs |
| Grouping field (optional) | Builds a separate curve for each field value (e.g. survey type) and overlays them on one chart. | off |
| Number of lags | Into how many distance intervals the pair cloud is split. | 15 |
| Maximum distance | The far edge of the variogram, in layer units (for metric coordinates - metres). 0 = half the extent diagonal. | 0 |
| Fit model (recommendation) | Auto-fit of the nugget, sill, range and model type; the result is remembered for substitution into **2D Kriging**. | on |
| Model to fit (adv.) | Fix the model type or leave the auto-pick of the best by R². | Auto |
| Minimum points per group, % (adv.) | Groups smaller than the threshold are not built and are listed in the Log. The floor is 30 points. | 2 |
| Robust estimator (Cressie-Hawkins) (adv.) | Reduces the influence of rare anomalous pairs. | off |
| Show pair cloud (adv.) | Adds the source pairs (before averaging) to the chart. | off |
| Overlay a given variogram model (adv.) | Draws a model with a manually set nugget, sill and range over the cloud - handy to compare your model with the data. | off |
| Outliers (adv.) | Clip percentile, lower and upper value bounds, capping-to-bound mode instead of removal. At the very end of the list. | off |
| Save profile as | If filled in - the fitted (isotropic) model and the current outlier settings are saved as a processing profile under this name. | empty |
| Variogram table | An output table layer with the variogram points (columns below). | temporary layer |
| Report (HTML) | A report with the cloud, the fitted curve and the data-variance line. | temporary file |

The parameters marked "adv." are in the collapsed **Advanced Parameters** section.

The output is a **Report (HTML)** with the chart, the fitted curve and the data-variance line, plus an optional **Variogram table** - the experimental variogram points as a geometry-less layer (one row per lag of each series). From it you can build your own chart in QGIS or export the values. Its columns:

| Field | Type | What it holds |
|------|-----|--------------|
| **series** | string | The series: "all points" or the grouping-field value, if set. |
| **lag** | double | The mean distance between points in the interval (lag), in layer units. |
| **gamma** | double | The semivariance γ(h): the mean of half the squared value differences over the pairs of this lag (or the robust Cressie-Hawkins estimate, if enabled). |
| **npairs** | integer | The number of point pairs that fell into the lag. A small number of pairs means the variogram point is unreliable. |

## The grouping field and a mixed-density survey

The optional **Grouping field** builds a separate variogram for each field value and overlays them on one chart. This is needed when the sample is collected by networks of different nature and density, for example surface and underground exploration. By feeding the survey type into the grouping, you can see whether these populations share a structure or each has its own.

Mixing mixed-density networks does not create artefacts by itself, but it distorts the overall variogram. A dense network gives many pairs at short lags and shapes the near part of the curve, a sparse network works on the far lags. A single model stretched over such a cloud turns out to be a mixture of two structures and describes neither correctly. The grouping shows this mixture, and the decision whether it is legitimate to combine the populations stays with the geologist. Declustering is not applied to the pair cloud here. Its weights are meant to correct the histogram and the mean, not the variogram pairs, where each pair is equally valid regardless of grid density.

![Roof elevation for the KrII seam, grouped by survey type: the underground network lies noticeably lower (a more homogeneous area), the detailed survey gives a high nugget. The different populations are visible at once.](images/variogram_by_type.png){width=70%}

## Three typical geological situations

Seam elevations, thicknesses and component grades have different geostatistical characteristics, and it is useful to see them side by side. The illustration shows variograms of three parameters of one industrial seam, computed in a single distance window.

![Three parameter types of one seam in a single window: roof elevation (almost zero nugget, smooth surface), thickness (nugget about a third, spherical) and grade (nugget comparable to the contribution, a noisy parameter).](images/variograms_three_params.png){width=98%}

Roof elevation is a smooth surface. The nugget is almost zero, the range large, the model close to Gaussian, the fit quality very high. Neighbouring wells give almost the same elevation, the variability is large-scale. Kriging works confidently. There is a subtlety here: a Gaussian model with an almost zero nugget is numerically unstable and gives the characteristic "bull's eyes" on the map. A small nugget should be set by hand.

Thickness is an intermediate case. The nugget makes up a noticeable fraction of the sill, the range is medium, the model more often spherical. About half the variability is structural, half small-scale. This is a typical working variogram.

The component grade is the noisiest parameter. The nugget is comparable to the structural contribution or exceeds it, the curve rises slowly, the fit quality is lower, and the model is poorly distinguishable from neighbouring types. The main variability sits at a scale finer than the sampling grid. Kriging smooths such a parameter heavily, and cross-validation shows a large error. Grade is predictably worse than elevations and thicknesses, and that is normal.

## Maximum distance and reaching the plateau

The most common mistake is too large a maximum distance. If you leave the automatic value at half the diagonal, on an elongated deposit the window stretches over tens of kilometres. The lags begin to link points across barren gaps and inter-block breaks, the variogram catches the regional trend instead of the local structure, and the fit yields a range larger than the window itself and a sill several times the variance. The sign of trouble is simple: the fitted model's range is comparable to the window or exceeds it. This means the curve has not reached a plateau and the sill is obtained by extrapolation.

The cure is to reduce the maximum distance to the local scale and to check that the variogram has reached the plateau. On a grade example for one seam, with a 6-kilometre window the fit gave a range of about 9 kilometres and a sill below the variance, i.e. the curve had not yet reached the plateau. With a 12-kilometre window it did, giving a range of about 18 kilometres and a full sill close to the data variance. The real correlation range turned out larger than it looked in the narrow window, and the right answer came precisely from checking that the curve reaches the plateau.

At the same time the window must not step over large barren zones. On a drilling grid they are visible by the drop in point density, and the variogram should be built within a single ore block, otherwise the local geology mixes with regional tectonics.

## The workflow with cross-validation

The variogram gives a starting model, and **Variogram cross-validation** checks it. The order is as follows. First an experimental variogram is built with a maximum distance at which the curve reaches a plateau, and the fitted nugget, contribution, range and model are taken. Then these numbers are carried into cross-validation and the leave-one-out metrics are assessed. The fitted and validated model is conveniently saved as a **processing profile** (the **Save profile as** field) and substituted into **2D Kriging** via the **Load processing profile** field - see the section on the Processing profiles tool.

The mean error ME should be near zero, meaning there is no systematic error. The root-mean-square error RMSE shows the absolute accuracy. The MSDR deserves separate attention - the ratio of the squared error to the kriging variance. If it is noticeably above one, kriging underestimates the uncertainty and the standard-error map is understated.

Correcting the MSDR is done exactly, not by eye. In ordinary kriging, multiplying the whole variogram by a constant factor does not change the estimate, since the weights depend only on the shape of the curve, not on its scale. Only the kriging variance changes. So it is enough to multiply the nugget and contributions by the current MSDR value, leaving the range and model unchanged, and repeat cross-validation. The ME, MAE, RMSE and R metrics do not shift, while the MSDR comes to one, and the error map becomes honest.

After scaling, the full sill may turn out above the data variance. On a clustered grid this is not an error. The naive variance is understated because dense well clusters pull it down, while the true scatter over the area is larger. The excess of the sill over the variance here is a consequence of the uneven grid.

The finished and validated model then only needs to be carried into **2D Kriging** to compute the grid, and after that, if needed, into **Isolines from raster**.

# Variogram map (anisotropy)

The tool builds a variogram map - the semivariance surface γ as a function of the two-dimensional separation vector (h_x, h_y). An ordinary variogram averages all directions into one curve and loses directionality; the map, by contrast, shows how the continuity of the parameter depends on direction. From it you can see whether there is anisotropy in the data and where the axis of maximum continuity points. The tool is diagnostic: it does not compute a grid but helps to set the azimuth and anisotropy in the 2D Kriging variogram structure deliberately.

## What anisotropy is and why to see it

An isotropic variogram assumes the link between values depends only on the distance between points, not on direction. For folded and elongated geological bodies this is not so. Along strike the seam is sustained, across it it changes faster: the same difference in roof elevations is gained over kilometres along the fold but over hundreds of metres across it. If this is not accounted for, kriging smooths the field equally in all directions and blurs the real elongation of the structure.

A variogram map reveals the directionality directly. For each pair of points not only the distance is taken but also the direction of the vector between them, and the semivariance of the increment is spread over a two-dimensional grid of lags. Where γ grows slowly and the map stays dark far from the centre, continuity is high. Where γ grows fast, continuity is low. The low-γ area as a whole stretches into an ellipse whose long axis is the direction of maximum continuity - for folding this is the strike direction.

## How to read the map

At the centre of the map lies the zero lag: a value at a point always equals itself, so γ here is zero and the centre is the darkest. As one moves away from the centre the points are separated farther and γ grows. The h_x axis points east, the h_y axis north, the scale on both axes is the same. The map is point-symmetric: a pair and its mirror image give the same semivariance, so the picture is the same in opposite directions.

Anisotropy is read from the shape of the dark area. If it is round - the structure is isotropic, direction plays no role. If it is elongated - along its long axis γ grows more slowly, i.e. in this direction values are linked over a larger distance. Hints are drawn over the map: a white ellipse by the estimated ranges and a red dashed line along the major axis.

![A variogram map: the dark (low γ, high continuity) area is elongated at an azimuth of about 135°. The white ellipse and the red dashed major axis show the estimated direction and anisotropy.](images/varmap_ellipse.png){width=80%}

## Parameters

![The Variogram map (anisotropy) dialog with the Advanced Parameters section expanded.](images/ui_varmap.png){width=58%}

| Parameter | What it sets | Default / advice |
|----------|-----------|----------------------|
| Points with values | A point layer of wells or samples. | - |
| Z value field | The numeric attribute to analyse: roof elevation, thickness, grade. | remembered between runs |
| Bins per half-axis (map detail) | Into how many cells each lag half-axis is split. The map comes out (2N+1)×(2N+1) in size. More bins - a more detailed map, but fewer pairs per cell and more noise. | 15 |
| Max. lag, in layer units | The map window size, in layer units (for metric coordinates - metres). 0 = half the extent diagonal. | 0 |
| Min. number of pairs per cell (adv.) | Cells with fewer pairs are left empty. Cuts off noisy far lags where pairs are few. | 5 |
| Report (HTML) | A report with the heatmap, the ellipse, the major axis and a summary of estimates. | temporary file |
| Surface raster (opt.) | The γ surface as a raster in lag coordinates (see below). Not created by default. | off |

The parameter marked "adv." is in the collapsed **Advanced Parameters** section.

## Estimating the azimuth, anisotropy and range

Besides the map itself the tool outputs to the Log and the HTML report three numbers: the major-axis azimuth (geographic, 0 - north, clockwise), the anisotropy coefficient as the ratio of the minor axis to the major (1 - isotropic, less - more elongated) and the major-axis range. The estimate works like this: along each direction the lag at which γ reaches the plateau (close to the data variance) is found, the ranges are smoothed over azimuth, the major axis is taken at the largest range, and the minor perpendicular to it.

These three numbers are substituted into the 2D Kriging variogram structure: azimuth, anisotropy (minor/major) and range a. This is exactly how anisotropy enters kriging. The estimate is indicative: it should be checked against the shape of the heatmap itself, not transferred blindly. The azimuth the map determines most reliably; the range and the coefficient are cruder, especially on a sparse network.

To avoid transferring the numbers by hand, the dialog has a **Write anisotropy to a profile** field. Pick a previously saved profile, and the azimuth, the coefficient and the major-axis range are written into it on top of the model and nugget set in **Variogram**. The next time the profile is loaded in **2D Kriging**, these values are applied automatically and appear in the caption under the profile list. If the range hit the window, it is left unchanged and only the azimuth and the coefficient are updated.

If the structure is close to isotropic or the major-axis range turns out smaller than a few map cells, anisotropy is not estimated and is marked in the report as "not expressed". In this case the ranges lie at the grid level and the directionality is unreliable - it is more honest to report this than to give a random azimuth. It helps to reduce the max. lag or increase the number of bins to resolve the near structure.

## When the range hits the window

If along the major axis γ does not manage to reach the plateau within the window, the range is returned equal to the max. lag, and a warning appears in the report and the Log: the range hit the max. lag, this is a lower bound. This is the same situation as for an ordinary variogram (see "Maximum distance and reaching the plateau"): the curve did not reach the plateau, and the sill is obtained by extrapolation. On the map the sign is simple - the dark area along the major axis stretches to the very edge.

In this case the range a cannot be carried into kriging as is: the real correlation length is larger than the window, and the anisotropy coefficient is understated in strength (the field is in fact even more anisotropic). The azimuth, meanwhile, is usually determined normally. The cure is to increase the max. lag so the map captures the plateau. And if γ does not reach the plateau even in a wide window, a trend dominates the data - it is removed before interpolation or accounted for with the appropriate kriging type.

## The surface raster

If desired, the map is also saved as a raster (the **Surface raster** field). It is the same γ surface but in lag coordinates: the origin at (0, 0), the pixel size equal to the lag cell. The raster is not georeferenced - it lies in the separation space, not in the deposit plan - and is meant for those who want to spin the map on the QGIS canvas, apply their own colour scale or measure a lag with a ruler. The HTML report is enough for the anisotropy estimate itself.

# Variogram cross-validation

![The idea of cross-validation: the kriging estimate from the remaining points (vertical) is compared with the actual value (horizontal). The tighter the cloud lies on the estimate = actual diagonal, the more accurate the prediction.](images/crossval.png){width=70%}

![What the cross-validation HTML report looks like: on the left the "estimate vs actual" chart with the diagonal and metrics (example - KCl for the KrII seam), on the right the error histogram. A dense cloud along the diagonal - the model works. A band at an actual value near 0 - replacement zones. The histogram is symmetric about 0 - no bias.](images/krii_crossval.png){width=98%}

The tool checks how well the variogram is fitted, by the leave-one-out method: each well in turn is excluded, its value is predicted by kriging from all the rest, and compared with the actual one. This way the parameters (nugget, range, model) are tuned by error rather than subjectively.

![The Variogram cross-validation dialog. The Well number field enables well labels in the report.](images/ui_crossval.png){width=82%}

The Log outputs the metrics:

**ME (mean error)** - the systematic error. Should be close to 0 (unbiasedness).

**MAE and RMSE** - the mean and root-mean-square prediction error. The smaller, the more accurate. But RMSE alone is not enough: it is minimal at a zero nugget (overfitting), although the uncertainty is then estimated wrongly.

**MSDR (standardized error)** - the mean square of the error divided by the kriging standard error. Should be close to 1. If MSDR is noticeably above 1 - the variance is underestimated (the nugget or sill are small). If below 1 - overestimated.

**R** - the "estimate - actual" correlation coefficient.

It is useful to distinguish two sides. The "estimate - actual" cloud and the RMSE speak of the **prediction accuracy**. How correct the **model** itself is - i.e. whether the variogram honestly describes the uncertainty - is shown by the standardized errors: an MSDR near 1 and the QQ-plot. For kriging both sides are valuable: a small RMSE with an MSDR near 1 means the model both predicts well and does not deceive itself about its own accuracy. Chasing RMSE alone is not allowed - it is minimal at a zero nugget, where the uncertainty is understated.

In practice try several variogram variants and compare. A good model gives ME near 0, a small RMSE and an MSDR near 1. If the RMSE pulls toward a zero nugget while the MSDR is huge - this is a sign of overfitting. A small nugget calibrates the uncertainty.

The optional residuals layer (points with fields: the actual value under the validated field's name, z_est, error, abs_error and std_resid, plus the well number if an ID field is set) shows where the model misses: large residuals by absolute value are problem areas, systematic residual signs are a local trend. The layer is automatically named after the validated field and the source, and the fields have aliases - readable names (visible in the attribute table and field properties). std_resid is the standardized residual (estimate − actual) / the kriging standard error, signed: minus - kriging underestimated, plus - overestimated (it is not a variance, a variance is always ≥ 0).

The residuals-layer fields:

| Field | Alias | Description |
|------|-----------|----------|
| `<well number>` | Well number | The value of the chosen ID field (if **Well number field** is set). |
| `<field name>` | Actual (field name) | The actual value of the validated field. |
| `z_est` | Kriging estimate (LOO) | The estimate from the other points (leave-one-out). |
| `error` | Error (estimate − actual) | Estimate minus actual. Minus - underestimated, plus - overestimated. |
| `abs_error` | \|Error\| | The absolute value of the error, \|error\|. |
| `std_resid` | Std. residual (signed) | (estimate − actual) / the kriging standard error, signed. Not a variance (which is ≥ 0). |

Besides the residuals layer the tool by default produces an HTML report (on plotly): an interactive "estimate vs actual" chart with the diagonal, an error histogram, a residuals QQ-plot and a metrics table with a recommendations block. The data variance is added to the table - a reference for the total sill C0+C. Next to the metrics table a **Kriging parameters** block is shown: only the settings that differ from the defaults are listed (nugget, sill, range, outliers and so on), so you can see which parameters produced these metrics. On the "estimate vs actual" chart, hovering over a point shows the well number and the values, and the eight wells with the largest residuals by absolute value are labelled right on the chart - they are convenient to check first. The report opens in the QGIS result viewer (or in a browser). If plotly is unavailable in the QGIS build, the report is still created - with the metrics table but without charts.

**The residuals QQ-plot.** Shows the shape of the error distribution. The errors are normalized to their own variance (a z-score) and compared with the normal distribution, so the chart reads by shape at any calibration. The uncertainty scale is handled separately by the MSDR in the metrics table. The horizontal axis is the normal-distribution quantiles, the vertical is the normalized error. If the errors are normal, the points lie on the red diagonal. Deviations read at once. Curled ends (S-shaped) - heavy tails, i.e. more large misses than under normality. An overall arc - skew, worth considering a value transform. A separate group broken off the line - an alien population in the data, for example barren samples from replacement zones (where the component is practically absent). Normality matters because the MSDR and the standard-error map rest on it.

![Four typical shapes of the residuals QQ-plot: normal (points on the diagonal), heavy tails, skew and a second population - a group broken off in the tail.](images/qq_example.png){width=92%}

**The main thing - what to do with the results.** The point of the tool is, before building the grid, to approve or correct the whole set of parameters you will then set in **2D Kriging**. This is both the variogram (nugget, sill, range, model, anisotropy) and the kriging settings themselves (search radius, min/max points, type - ordinary or simple): cross-validation computes kriging with exactly the same settings, so a good set is carried into the **2D Kriging** tool unchanged. The order of decisions:

- ME near 0, MSDR near 1, the RMSE and R suit you - the set can be approved: carry these same parameters (the variogram and the search settings) into **2D Kriging** and build the surface.
- MSDR noticeably above 1 - kriging is too "sure of itself", the standard-error map will be understated: increase the nugget C0 or the sill and check again.
- MSDR below 1 - the uncertainty is overstated: reduce the nugget or the sill.
- ME noticeably different from 0 - a systematic shift: check the data and the kriging type (for simple kriging - the specified mean).
- A large RMSE and a low R - the model predicts poorly: try a different range, model or anisotropy (azimuth and axis ratio). If nothing helps - it is the data's limit: short-range variability the network does not catch (e.g. ore replacement zones - on the chart above this is the vertical band at an actual value near 0).

The residuals layer prompts pointwise: where the residuals are large - the network should be densified (add wells) or the samples checked. Where the residuals are systematically of one sign over the area - there is a local trend kriging did not account for.

In sum: this tool is the last step before the final kriging. First you calibrate the variogram here by error, then set the same parameters in **2D Kriging** - and the surface together with the standard-error map come out justified rather than fitted subjectively.

A note on speed: the check solves kriging as many times as there are points, so on large sets (tens of thousands of wells) it runs noticeably longer. Reduce the sample if needed.

# Create sample wells (demo)

The **Create sample wells (demo)** tool builds a point layer with random coordinates and three structured fields: the absolute roof elevation (roof), the thickness (thick) and the grade of an abstract component X (%). The roof and thickness ranges are set after the model of an industrial seam (KrII). The tool is meant for learning and testing kriging, isolines and cross-validation without real data.

![The Create sample wells (demo) dialog.](images/ui_demo.png){width=82%}

Parameters: the area (the extent - it can be set by layer, by map canvas, by coordinates or by drawing on the map). The number of wells. The minimum and maximum of value X. The roof and thickness ranges (by default - as for KrII). The smoothness (a fraction of the extent - it sets the correlation range: a larger value means larger areas of homogeneity). The nugget fraction (the fraction of the variance falling on short-range noise; the larger it is, the lower the predictability). In the **Advanced** section - the random-number generator seed for reproducibility.

At start the Log outputs the starting variogram (total sill ≈ the data variance, nugget, range). The generated data have a recoverable variogram, so it is convenient to learn the whole cycle on them: build a grid in **2D Kriging**, then isolines, and check the parameters with cross-validation.

Two checkboxes add optional fields for learning the related tools. **Add a categorical mineral-type field** adds a mintype field with a silvinite background and replacement spots for categorical indicator kriging. **Add a head field** adds a head field with a pronounced regional slope for the hydraulic gradient: krige head, feed the raster to the flow tool, and the arrows follow the head downhill.

# Processing profiles

A profile is a named set of processing settings for one parameter: the variogram (nugget C0, model type, contribution C, range a, azimuth and anisotropy axes) plus outlier removal (percentile, bounds, capping mode). Profiles are handy when a project has several seams or zones of different variability: you fit a model for a seam once and reuse it in kriging without re-entering the numbers.

Profiles are stored globally in the QGIS settings, so they are available across all projects: build a seam's model once - apply it anywhere. A profile describes one variogram structure - exactly as much as kriging uses.

![The Processing profiles dialog: the action, the choice of profile with its parameters in the line below and the manual-entry fields in the Advanced Parameters section.](images/ui_profiles.png){width=82%}

## Where profiles come from

- **Variogram** - the **Save profile as** field. The fitted model is saved. The curve is built isotropic, so the azimuth and axes are written as neutral (0 and 1) - anisotropy is set later.
- **Cross-validation** - the **Save profile as** field. The validated model is saved together with the set anisotropy. This is the main way to get a profile with an azimuth and axes.
- **Processing profiles** - the **Save manually** action: all profile values are entered in the fields of the **Advanced Parameters** section.

## Application

In the **2D Kriging** and **Cross-validation** tools the **Load processing profile** field substitutes the chosen profile over the dialog fields. What exactly is substituted is printed to the Log.

## Management

The **Processing profiles** tool itself manages the storage via the **Action** parameter:

| Action | What it does |
|----------|-----------|
| Show list | Outputs all profiles with their parameters to the Log. |
| Save manually | Saves a profile with the name from the **Profile name** field by the values of the fields in **Advanced**. |
| Delete selected | Deletes the profile chosen in the **Profile** field. |
| Clear all | Deletes all profiles. |

Saving under an existing name overwrites the profile. The profile lists in the drop-down fields (the choice for deletion, the load in kriging) refresh when the tool window opens: after saving a profile, reopen the tool so it appears in the list.

Below the profile drop-down, in the line beneath it, the parameters of the chosen profile are shown (nugget, type, contribution, range, azimuth, axes, outliers). In **2D Kriging** and **Cross-validation** a reminder is shown there as well that the computation will use the profile rather than the dialog fields. On QGIS builds without the old widget API the caption does not appear - an ordinary list remains (this does not affect the work).

# Categorical indicator kriging

The **Categorical indicator kriging** tool builds a probability map from a categorical field: mineral type, lithotype, any text class. Unlike ordinary kriging, which interpolates a number, here it estimates how likely each class is at every point of the area. This is what you need where the type matters rather than the magnitude: where to expect replacement, where the seam composition changes, where the boundary between varieties runs.

![The Categorical indicator kriging dialog.](images/ui_categorical_en.png){width=80%}

Parameters: a point layer and a categorical field. Search radius, minimum and maximum number of points, cell size and raster extent are all as in **2D Kriging**, with the same defaults. Empty values and NULL are excluded.

## How it is computed

Coding the classes as numbers 1, 2, 3 and interpolating that code is not allowed. Categories have no order, class 3 is not "farther" than class 1, and a mean between them is meaningless. So the tool takes the indicator route. For each class an indicator is built: one where the borehole is of that class, zero everywhere else. Each indicator is kriged separately by ordinary kriging, like an ordinary field, and yields a surface from zero to one, which is the class probability. The indicator variogram is fitted automatically with a spherical model from the experimental one.

Separate indicators do not sum to exactly one and may go slightly out of range, a known property of the method. So the estimate of each class is clipped to zero-one, and then the class probabilities are normalised so that in every cell they sum to one.

## What you get

Three results. A multiband probability raster, one band per class, the class name written into the band description. A zone map, the code of the most likely class in the cell, with the code to class mapping printed to the log. An optional confidence raster, the maximum probability in the cell, which shows where the class is firm and where zones compete and the boundary runs.

![Categorical indicator kriging result: a map of the most likely mineral type, a silvinite background with replacement spots, boreholes drawn on top.](images/indk_result_en.png){width=85%}

The categorical approach is convenient because it needs no boundary drawn in advance. There is no need to decide whether partial replacement counts as dangerous. All types are mapped as they are, and the required combination of classes is assembled later from the probabilities. Rare classes with few boreholes give a noisy variogram, the tool warns about this in the log, so read the probability of such a class with caution.

To learn the tool without real data, switch on **Add a categorical mineral-type field** in **Create sample wells (demo)**. A mintype field is added to the layer with a silvinite background and replacement spots after a mine, ready to run the tool on.

# Hydraulic gradient and flow direction

The **Hydraulic gradient and flow direction** tool works with the head field, that is the piezometric surface, and shows where and how steeply groundwater flows. The input is a head raster, usually the result of **2D Kriging** on borehole water levels. For a hydrogeologist this is as natural a step after building the head surface as isolines are after kriging.

![Flow vectors over the head surface: the arrows go down-gradient, from high head (warm tones) to low (cool), on top of the head isolines. The arrow length grows with the steepness of the gradient.](images/flow_result.png){width=78%}

There are three outputs. The **gradient-magnitude raster** shows the steepness of the head surface, the hydraulic gradient i equals the magnitude of ∇h and is dimensionless. The **azimuth raster** holds the flow direction in degrees, where zero is north and the count goes clockwise. The point layer of **flow vectors** is thinned over the grid and styled as arrows right away, so the flow pattern is visible without touching the symbology.

The direction is computed strictly. Water flows down-gradient, from higher head to lower, so the arrow points towards the falling surface. On flat areas, where the head is almost constant, the direction is undefined and the azimuth there is left empty.

## Without permeability

The tool describes the geometry of the head field, not the flow velocity. The Darcy filtration velocity equals minus the hydraulic conductivity K times the gradient, and it needs K itself, which the tool neither asks for nor computes. In other words, the map answers where and how steeply, but not how fast. Once K is available over the area, the velocity can be obtained by multiplying the gradient by the conductivity in a separate step.

## Parameters and smoothing

The input is the **head raster** and its **band**. The **flow vectors, thinning step** parameter sets how many cells apart to place an arrow so they do not merge, eight by default. The **smooth head before computing** parameter removes fine grid ripple, set in cells, off by default.

Smoothing is switched on for substance, not for looks. Differentiation amplifies noise, so even a clean kriging grid can give a patchy gradient field with jittery arrows. A light smoothing brings the picture back to a readable form. The same effect can be had by smoothing the head itself back in **2D Kriging**.

## Arrows from points

The vector layer is points, and the arrows are drawn by the symbology. The preset is applied automatically. The arrow marker is rotated by the **az** field, so it shows the flow direction, and its size is scaled by the **grad** field, so the arrow is longer where the gradient is steeper. The size is set in millimetres and does not depend on the map scale. The symbology can be changed in the layer properties. If you need a classic quiver diagram, where the arrow length is laid out in map units, the marker is replaced with a geometry generator, the recipe is in the styles folder next to the preset.

## The learning cycle

To walk the whole path without real data, switch on **Add a head field** in **Create sample wells (demo)**. A head field with a pronounced regional slope is added to the layer. Build a grid from it in **2D Kriging**, feed the raster here, and the arrows follow the head downhill. The same end-to-end scenario as for the other tools, only about hydrogeology.

# Typical situations and solutions

| What you see | Cause | Solution |
|---|---|---|
| Concentric "bull's eyes", cones | Kriging pulls the value exactly through outlier wells (nugget 0). | Set a nugget C0 (0.2-0.4 of the sill, in absolute variance units). And/or enable grid smoothing in **2D Kriging**. |
| Angular isolines ("octagons") | A coarse grid: vertices are placed at cell edges. | Increase **Line rounding** to 3 or reduce the cell size in kriging. |
| Radial/fan lines in empty corners | Extrapolation beyond the data. | Enable **Clip to well hull** or set a clip mask. |
| Isolines cross in dense areas | Formerly - a consequence of smoothing each line. | Smoothing is done over the field (in **2D Kriging**). Increase the grid-smoothing radius there. |
| Polygons of one colour | By default the layer is created with a single symbol. | Set graduated symbology by ELEV_MIN. |

# License and support

The plugin is distributed under the GNU GPL v2 or later (GPL-2.0-or-later) - the same as QGIS itself. The full text is in the bundled LICENSE file. © Inform++ LLC, www.informpp.ru.
