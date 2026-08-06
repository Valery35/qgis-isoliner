---
geometry: "a4paper, margin=0.85cm"
mainfont: "DejaVu Serif"
sansfont: "DejaVu Sans"
fontsize: 8pt
pagestyle: empty
header-includes:
  - \usepackage{titlesec}
  - \titlespacing*{\section}{0pt}{4pt}{2pt}
  - \titlespacing*{\subsection}{0pt}{3pt}{1pt}
  - \setlength{\parskip}{1pt}
  - \usepackage{enumitem}
  - \setlist[itemize]{nosep,leftmargin=1.1em,topsep=1pt}
  - \usepackage{setspace}
  - \AtBeginDocument{\setstretch{0.92}}
  - \setlength{\parindent}{0pt}
---

\begin{center}
{\Large\sffamily\bfseries Isoliner · River hydrology: from a section to a flood extent}\\[1pt]
{\small Cheat sheet for the "6. River hydrology" group · QGIS 3.16+ · Isoliner v4 plugin · pure NumPy, no external dependencies}
\end{center}

\vspace{2pt}

# The pipeline: from a section to a flood polygon

**Step 1. Sections.** Lines across the valley with elevations in the **Z of the vertices**. A surveyed elevation is more accurate than any terrain model, so the source is the geometry itself rather than a DEM sample. If the profiles are kept as tables of distance and elevation pairs - **6.03 Import section tables**; digitized lines go there as well, the soundings will lie along them.

**Step 2. The curve - 6.01 Cross-sections and rating curves.** Input: the layer of sections. Outputs: the table of the curve, the profiles, the levels, the drawing footer, the ground elevations, an HTML report with a graph. The computation is separate by part: the discharge by Manning on each, the sum over the section.

**Step 3. Flooding - 6.02 Flood extent polygon.** Input: a DEM, the **Sections on the map**, the table of the curve from step 2 and a desired discharge. Outputs: the flood extent and a raster of depth. The curve does not depend on the discharge, so the discharge can be changed as many times as needed without going back to step 2.

A first acquaintance: **6.04 Example river (demo)** gives the sections, a table of soundings, a valley surface, a reference curve, probability discharges and observed levels. A discrepancy with the reference is an error of the tool rather than of the data.

# Fields of a section: names and units

They are picked up by these names on their own, an explicit choice is always senior. The picked ones are printed to the log.

| field | what | units |
|---|---|---|
| `sec` | section name, the lines are grouped by it | text |
| `km` | chainage from the mouth | km |
| `role` | role of the line when supplied as three lines | left, channel, right |
| `div_l`, `div_r` | part boundaries, distances along the profile | m |
| `n_left`, `n_channel`, `n_right` | Manning roughness | dimensionless |
| `slope` | slope of the water surface | m/m |

The slope is dimensionless: 0.0004 rather than per mille. In the drawing footer it is also output in per mille as the `slope_ppm` field. The roughness is the Manning coefficient: 0.030 for a clean channel, 0.070 for an overgrown floodplain; in the footer the inverse `n_inv` goes next to it, that is what is printed on a gauging section.

Other tables: probability discharges - `prob` in percent and `q` in m3/s; observed levels - `level` in metres and `label` as text; soundings for the import - `sec`, `dist` in metres, `elev` in metres, `km`.

# What the gauging sheet gets

- **Section profiles** and **levels** as lines in drawing coordinates. The levels are clipped at the water edge: the water does not climb the banks, and a shoal or a closed depression breaks the mirror into segments.
- **The drawing footer** - a row per part: width, mean depth, area, wetted perimeter, radius, slope in per mille, roughness and its inverse, velocity, discharge, share of the total.
- **Ground elevations and distances** as points, these are the bottom rows of the sheet.
- **An HTML report** - for every section the profile with the levels and a graph of discharge against level, the pictures embedded into the page.

The sheet is assembled by a print layout: the tool gives the data, the design lives in the template.

# Levels: computed and measured

Probability discharges are supplied as a table of probability and discharge pairs. The tool does not compute them from observation series, that is hydrological statistics. For every discharge a level is found along the curve, and the levels come out as lines with ready labels of the **UVV1%** kind.

Observed levels have their own table: an elevation and a label, in the manner of **UV 472.90 X/2021**. This is a measurement, it does not rely on the curve. In the layer of levels the computed ones are marked `kind=prob` and the measured ones `kind=obs` - the field gives them different styling.

# Three places where people usually stumble

**Empty fields of parts and roughness.** The computation goes through, but the whole profile is treated as one channel with defaults, and the discharges come out several times too large: a floodplain hundreds of metres wide gets the roughness of a channel. This is the most frequent cause of implausible numbers. Check the log: it shows whether the fields were picked up or the defaults were taken.

**One elevation for the whole valley.** The level is not constant along the river, upstream it is higher. Cutting the whole area by a single elevation floods the valley where the water does not reach. That is why the levels go by section and a water surface rises from them.

**The drawing layer of levels instead of the map one.** In the output "Probability levels (drawing)" the horizontal axis carries the distance along the section rather than a coordinate. For 6.02 you need the output **Levels at the sections on the map**. The tool recognizes this and refuses with an explanation.

# A river DEM with a bed: what to click

The task: to get a relief where the banks are steep, the water edge sits at a surveyed elevation and a bed is drawn in the channel. The tool is **2.03 Topo2Raster**, the "2. Topography" group.

**Prepare the layers.**

1. Contours - lines with an elevation field. Digitizing of the map or **1.04 Isolines from a raster** over an existing DEM.
2. The water edge - a polygon of the river or the lake. Put the elevation into a field or into the Z of the vertices.
3. The thalweg - a line along the bed of the channel, vertices **downstream**.
4. Bed soundings - points with elevations. A few per pool and riffle will do.
5. Cliffs - lines along the banks, if the bank is steep.

**Fill in the form of 2.03.**

- **Contours** - the layer of contours, **Contour elevation field** - the elevation field.
- **Elevation points** - the bed soundings, **Point elevation field** - the elevation field. If the soundings have no field, the elevation is taken from Z.
- **Thalwegs (downstream)** - the layer of the thalweg.
- **Lakes and the water edge** - the polygon of the water edge, **Water edge elevation field** - the field with the elevation, or leave it empty if the elevation is in the Z of the vertices.
- **Water edge as a plane** - **clear the box**. With it the whole area inside the polygon is filled with a flat elevation and a table comes out instead of a channel. Without it only the shore line is fixed, and the bed inside is drawn from the soundings and the thalweg.
- **Cliffs (smoothing barriers)** - the layer of banks, if there is one.
- **Cell size, m** - by the table of scales below.
- **Fill depressions at the end** - **clear the box**. Otherwise the channel is levelled out at the last step: for the filling it is an ordinary pit. The tool warns about it in the log.

**Check the result.**

- **1.04 Isolines from a raster** over the DEM you got, at the same interval, overlaid on the original contours: the lines must coincide.
- A profile across the channel (**4.03** or the QGIS profile tool): the bed must lie below the water edge and the banks must be steeper than on the map.
- If the bed is flat, either the **Water edge as a plane** box is still on, or there are neither soundings nor a thalweg.

**For a lake or a pond it is the other way round**: leave the **Water edge as a plane** box on and supply neither soundings nor a thalweg. Then a flat mirror comes out inside the polygon.

# Scale and the limits of applicability

The cell is chosen from the scale of the map, about 0.2 mm at the scale of the original: more often it is taken twice as coarse so as not to draw detail that does not exist.

| scale | cell | usual interval | what limits it |
|---|---|---|---|
| 1:10,000 | 2-5 m | 2 m | suits a flood computation on a plain |
| 1:25,000 | 5-10 m | 5 m | the floodplain reads coarsely, the water edge on the map is conventional |
| 1:50,000 | 10-20 m | 10 m | a preliminary estimate of the spread only |
| 1:100,000 | 20-30 m | 20 m | does not suit flooding at all |

**The vertical accuracy matters more than the planimetric one.** Between the contours the relief is a guess of the interpolator, and its error is comparable to half the interval. On a flat floodplain at an interval of 5 m these are the same metres by which the flood rises: the extent will come out with an error of hundreds of metres horizontally, because the slope of the floodplain is small.

**Hence the rule.** From a 1:25,000 map a flood zone is an estimate of the order of magnitude rather than a design document. For a justification soundings, a survey or laser scanning are needed, and then the map serves only as a filler between them.

**A topographic map holds no bed at all.** The channel on it is sewn up by the plane of the water edge, and a DEM from the map gives a flat surface in place of the river. If there are no soundings, the rating curve should be computed from sections with surveyed elevations in Z, and the DEM left for the flooded area above the water edge. Mixing these two sources in one profile is not allowed: the bed would come out of thin air.

# What the method promises and what it does not

The Manning formula describes steady uniform flow. The curve is a hydraulic characteristic of the section rather than a computation of a release wave.

The slope enters the discharge under a square root, so an error in it tells directly. On lowland rivers it is small, and the accepted value is always printed to the log.

The curve is built up to the top of the profile. In a catastrophic scenario the water rises above the banks, and such a section is skipped with a warning; to compute large scenarios raise the upper elevation in 6.01.

Connectivity of the flood zone with the channel is not checked: closed depressions away from the river stay in the extent, and that is visible on the map.

Bathymetry is not restored: the bed between surveyed sections is a separate task with its own anisotropy. For a DEM with a bed there is **2.03 Topo2Raster** with the water edge in the contour mode: it fixes the shore line and leaves the surface inside to the sounding points and the thalweg. The filling of depressions must be switched off then, otherwise the channel is levelled out at the last step.

\vspace{3pt}
\begin{center}
{\small Developed with the support of Inform++ LLC · www.informpp.ru · github.com/Valery35/qgis-isoliner}
\end{center}
