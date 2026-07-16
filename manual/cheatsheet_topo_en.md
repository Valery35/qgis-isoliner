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
{\Large\sffamily\bfseries Isoliner · Topography: from DEM to catchments}\\[1pt]
{\small A cheat sheet for the "2. Topography" group · QGIS 3.16+ · Isoliner plugin v4 · pure NumPy, no GRASS or SAGA}
\end{center}

\vspace{2pt}

# Pipeline: from a DEM to Topo2Raster

**Step 1. DEM - 2.01 Download DEM by extent.** Set the extent, keep the defaults. Output: a 30 m terrain raster in a metric CRS.

**Step 2. Contours - 1.04 Isolines from raster.** Input: the DEM of step 1. A 10 m contour step, the **ELEV** field, do not raise the minimum length (short closed lines are hilltops), turn contour polygons off.

**Step 3. Streamlines - 2.06 River network.** Input: the DEM of step 1. Threshold 1000 cells, a denser network - a lower threshold. Line vertices already run downstream, as Topo2Raster requires.

**Step 4. Summit marks - 2.09 Peaks.** Input: the DEM of step 1. Radius 500 m, drop 20 m. Output: points with the **z** field - the cure for tops above the last closed contour.

**Step 5. Water bodies and cliffs - 2.02 Download base topography by extent.** The same extent. From OSM we take what a raster cannot give: water bodies (flat planes) and cliffs (barriers). OSM watercourses and peaks are a fallback for steps 3-4 when ele is filled.

**Step 6. Terrain - 2.03 Topo2Raster.** The contours of step 2 (the ELEV field), streamlines - the network of step 3, elevation points - the peaks of step 4 (the z field), lakes - the water bodies of step 5, cliffs as barriers (the step is not smeared). Cell 30 m, the extent empty. Output: the topographic terrain raster.

Control: 1.04 over the result with the same step, overlaid on the contours of step 2 - the lines must coincide. Contours from a digitized plan replace steps 1-2. All outputs land in the **Topography** group of the layer tree.

# Tool reference: input, output, key parameters

\begingroup\footnotesize\renewcommand{\arraystretch}{1.3}

| Tool | Input | Output | Key parameters |
|:--|:--|:--|:--|
| **2.01** Download DEM | map extent | GeoTIFF float32, meters | source: GLO-30 (DSM) or GEDTM30 (DTM, forest removed). Cell 30 m. CRS: empty = project/UTM |
| **2.02** OSM base map | map extent | watercourses, water bodies, peaks (ele), cliffs | coastline off. Large extent: shrink it or raise the limit |
| **1.04** Isolines from raster | DEM | lines with ELEV | step 5-10 m. Do not raise min length: short closed lines are hilltops. Moderate smoothing. Polygons off |
| **2.03** Topo2Raster | contours (elevation field), points, streamlines, cliffs, lakes | GeoTIFF float32 | points or contours required. Streamlines run downstream: OSM and 2.06 fit as is. Edge: node Z (river slope) > field elevation (plane) > shore minimum |
| **2.04** Terrain preparation | DEM | prepared DEM | FPDEMS smoothing (edge-preserving) and/or depression filling (epsilon 0.001 for D8). Both by checkboxes |
| **2.05** Flow D8 | DEM | directions (ArcGIS codes, byte) + accumulation (cells, float32) | codes: E=1, SE=2, S=4, SW=8, W=16, NW=32, N=64, NE=128, sink=0 |
| **2.06** River network | DEM | lines: order (Strahler), acc\_out, length\_m | threshold = head catchment / cell area. 1000 at 30 m ≈ 0.9 km² |
| **2.07** Basins | DEM, pour points (opt.) | polygons: basin, area\_m2 (+ label raster) | without points: from mouths by threshold. Point snap to the accumulation max, 150 m |
| **2.08** Slope and aspect | DEM | two rasters, degrees | Horn 3×3 as gdaldem. Aspect: downslope azimuth, flats = -1 |
| **2.09** Peaks | DEM | points: z, drop | a 500 m radius suppresses secondary tops, a 20 m drop cuts bumps |

\endgroup

# Common pitfalls

- **A degree CRS.** The group works in meters. 2.01 reprojects itself: an empty target CRS = the project CRS if metric, otherwise UTM at the center. User CRSs without an EPSG code are fully supported.
- **The river threshold is tuned.** Start at 1000 and adjust threefold. A DEM check: overlay the 2.06 network on the OSM watercourses.
- **Overpass is not elastic.** Large requests get cut by OSM servers, on a failure the tool switches to a mirror. Keep the extent modest.
- **Closed basins.** Karst and subsidence troughs are erased by the hydro correction: uncheck the filling.

\vspace{1pt}\hrule
{\footnotesize Data: Copernicus DEM © ESA · GEDTM30 © OpenGeoHub CC BY 4.0 · © OpenStreetMap, ODbL. Plugin: plugins.qgis.org/plugins/grid\_isolines · The manual ships with the plugin (doc/Isoliner\_en.pdf) · Inform++ LLC · www.informpp.ru}
