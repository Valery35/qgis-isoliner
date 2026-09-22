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
{\small A cheat sheet for the "2. Topography" group · QGIS 3.16+ · Isoliner plugin · no GRASS or SAGA}
\end{center}

\vspace{2pt}

# Pipeline: from a DEM to Topo2Raster

**Step 1. DEM - 2.01 Download DEM by extent.** Set the extent, keep the defaults. Output: a 30 m terrain raster in a metric CRS.

**Step 2. Contours - 1.04 Isolines from raster.** Input: the DEM of step 1. Isoline step 10 m, value field name **ELEV**. Leave the minimum line length at 0, because short closed lines are hilltops. Turn contour polygons off.

**Step 3. Streamlines - 2.06 River network.** Input: the DEM of step 1. Threshold 1000 cells, the lower the threshold, the denser the network. Line vertices already run downstream, as Topo2Raster requires.

**Step 4. Peak and pit elevations - 2.09 Peaks and pits.** Input: the DEM of step 1. Window radius 500 m, drop 20 m. Output: points with the **z**, **drop** and **kind** (peak or pit) fields, the elevation also sits in the geometry Z. Without these points Topo2Raster cuts hilltops and hollows inside closed contours into flat pads.

**Step 5. Water bodies and cliffs - 2.02 Download base topography by extent.** The same extent. Take from OSM what a raster cannot give, that is water bodies and cliffs. OSM watercourses and peaks are a fallback for steps 3-4 when the ele field is filled.

**Step 6. Terrain - 2.03 Topo2Raster (terrain from vectors).** Contours from step 2 (the ELEV field), streamlines from step 3, elevation points from step 4 (the z field), lakes from step 5, the cliffs of step 5 into **Cliffs (smoothing barriers)** so the drop is not smeared. Cell 30 m, extent empty. Output: the terrain raster.

Check: run 1.04 over the result with the same step and overlay it on the contours of step 2. The lines must coincide. Contours from a digitized plan replace steps 1-2. All outputs land in the **Topography** group of the layer tree.

# Tool reference: input, output, key parameters

\begingroup\footnotesize\renewcommand{\arraystretch}{1.3}

| Tool | Input | Output | Key parameters |
|:--|:--|:--|:--|
| **2.01** Download DEM by extent | map extent | GeoTIFF float32, meters | source: GLO-30 (DSM) or GEDTM30 (DTM, forest removed). Cell 30 m. CRS: empty = project/UTM |
| **2.02** Download base topography by extent | map extent | watercourses, water bodies, peaks (ele), cliffs | coastline off. Large extent: shrink it or raise the extent area limit |
| **1.04** Isolines from raster | DEM | lines with ELEV | step 5-10 m. Min. line length 0, short closed lines are hilltops. Default smoothing. Polygons off |
| **2.03** Topo2Raster | contours (elevation field), points, streamlines, cliffs, lakes | GeoTIFF float32 | points or contours required. Streamlines run downstream: OSM and 2.06 fit as is. Edge: node Z (river slope) > field elevation (plane) > shore minimum |
| **2.04** Terrain preparation | DEM | prepared DEM | FPDEMS smoothing (edge-preserving) and/or depression filling (epsilon 0.001 for D8). Both by checkboxes |
| **2.05** Flow and accumulation (D8) | DEM | directions (ArcGIS codes, byte) + accumulation (cells, float32) | codes: E=1, SE=2, S=4, SW=8, W=16, NW=32, N=64, NE=128, sink=0 |
| **2.06** River network | DEM | lines: order (Strahler), acc\_out, length\_m | threshold = head catchment / cell area. 1000 at 30 m ≈ 0.9 km² |
| **2.07** Basins and watersheds | DEM, pour points (empty = mouths) | polygons: basin, area\_m2 (+ label raster) | without points, basins start at mouths by threshold. Snap radius 150 m |
| **2.08** Slope and aspect | DEM | two rasters, degrees | Horn 3×3 as gdaldem. Aspect: downslope azimuth, flats = -1 |
| **2.09** Peaks and pits | DEM | points: z, drop, kind (PointZ) | both signs always. A 500 m window radius keeps one peak per window, a 20 m drop discards bumps and small hollows |
| **2.15** Gauge point report | DEM, gauge points | gauge watersheds (polygons), HTML report | a point snaps to the accumulation maximum within 150 m |
| **2.16** Catchment of a line or an outline | DEM, lines or polygons | catchment polygon, area | a polygon is an intake as a whole, the outline and everything inside. For a pit the catchment does not depend on flow directions in the hollow |
| **2.18** Cut and fill | two surfaces, or a surface and an elevation | difference raster, HTML statement | the sign is "after minus before". Grids are aligned bilinearly. The dead band discards background noise |

\endgroup

# Common mistakes

- **A degree CRS.** The group works in meters. 2.01 reprojects itself: an empty target CRS = the project CRS if metric, otherwise UTM at the center. Custom CRSs without an EPSG code work like any other.
- **The river threshold is tuned.** Start at 1000 and change it threefold in the needed direction. Check by overlaying the 2.06 network on the OSM watercourses.
- **The Overpass server limits requests.** It rejects an oversized OSM request, and the tool then switches to a mirror. Keep the extent modest.
- **Closed basins.** Depression filling erases karst and subsidence troughs, so uncheck it.

\vspace{1pt}\hrule
\begin{footnotesize}Data: Copernicus DEM © ESA · GEDTM30 © OpenGeoHub CC BY 4.0 · © OpenStreetMap, ODbL. Plugin: plugins.qgis.org/plugins/grid\_isolines · The manual ships with the plugin (doc/Isoliner\_en.pdf) · Inform++ LLC · www.informpp.ru\end{footnotesize}