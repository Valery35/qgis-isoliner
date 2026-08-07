# Isoliner in 15 minutes

You need no data of your own. In every scenario the first step creates or downloads the data itself, so the route can be walked on an empty project.

The module holds 48 tools, and that is daunting at first sight. Start with five: **1.02**, **1.04**, **2.03**, **2.13** and **4.01**. The rest will find you when a task calls for it.

After every step there is a line saying what should come out. If something else came out, that is the place to stop and look into it rather than to move on.

---

## Scenario 1. I have points

Five minutes. Boreholes, samples, measurements, any irregular network of observations.

**Step 1.** **1.10 Create sample boreholes (demo)**
Minimum: nothing to set, defaults are fine. If you have points of your own, skip the step.
*You get:* a point layer in the project.

**Step 2.** **1.02 2D Kriging (points to raster)**
Minimum: **Points**, **Value field**, **Cell size**. The rest by default.
*You get:* a raster and a layer of standard error. The error grows where the points are sparse, and that is the first thing worth looking at.

**Step 3.** **1.04 Isolines from raster**
Minimum: **Raster**, **Interval**, **Isoline style** = Structure / hypsometry. Choose the interval so that the lines are visible but do not merge into solid hatching.
*You get:* isolines with labels and contour bands whose borders coincide with the lines.

Further at will: **1.05** and **1.06** show whether the data hold anisotropy, **1.08** rates the method by cross-validation.

---

## Scenario 2. I need a relief

Fifteen minutes. The main scenario: we build a relief, check it with numbers and treat what is found.

**Step 1.** **2.01 Download DEM by extent**
Minimum: **Relief source** = GEDTM30, **Download extent** over the area of interest. GEDTM30 is a terrain model without forest and buildings, and for building contours it suits better than Copernicus GLO-30, where the height is taken off the treetops.
*You get:* a relief raster in the project coordinate system.

**Step 2.** **1.04 Isolines from raster**
Minimum: **Raster**, **Interval** of 5 or 10 m. The rest by default.
*You get:* a contour layer. From here on it plays the part of source data, as if it had been handed to you.

**Step 3.** **2.05 Flow and accumulation (D8)**
Minimum: the **DEM** from step 1. Leave the filling of depressions on.
*You get:* rasters of flow direction and accumulated area.

**Step 4.** **2.06 River network**
Minimum: the accumulation raster from step 3, **Catchment threshold** in cells. Start with a thousand and tune it until the network looks plausible.
*You get:* watercourse lines directed downstream, that is, ready-made streamlines.

**Step 5.** **2.09 Peaks and pits**
Minimum: the **DEM** from step 1, **Search radius** in cells.
*You get:* points of local maxima with elevations.

**Step 6.** **2.02 Download topographic base by extent**
Minimum: the same **Extent**. From OSM take areal water bodies and dry channels, but not rivers and peaks: those are already computed from the relief in steps 4 and 5 and agree with it, whereas from OSM they come from another source and may not match the matrix.
*You get:* up to five layers in the Topography group.

**Step 7.** **2.03 Topo2Raster (relief from vectors)**
Minimum: **Contours** from step 2 and the **elevation field**, **Streamlines** from step 4, **Spot heights** from step 5 and the elevation field, **Lakes and shoreline** from step 6 and the shoreline elevation field, **Cell size**. Supply cliffs if the topographic base holds any.
*You get:* a new relief raster built from typed vectors rather than from contours alone.

**Step 8.** **2.12 Contour residuals against the DEM**
Minimum: **Contours** from step 2, the **elevation field**, the **DEM** from step 7.
*You get:* a point layer of residuals and an HTML report. Look at the mean offset and at the share of points that missed by more than half the interval.

**Step 9.** **2.13 DEM terracing check**
Minimum: the **DEM** from step 7 and the **Contour interval**, the same as in step 2.
*You get:* the index of attraction of elevations to the levels. Near one means there are no steps, two and above means terracing.

**Step 10.** **2.14 Remove steps (clamped smoothing)**
Needed only if the previous step found terracing. Minimum: **DEM with steps**, **Contour interval**, **Iterations** 50.
*You get:* a corrected relief and a before and after report. The index should come down towards one.

If you want to test the method rather than your data, replace the first step with **2.10 Demo relief** and its **Gully network** tick. Then the true surface is known in advance and it is plain to see what gets lost in the building. Gullies are lost first.

There are two ways to check against the truth. By eye: build isolines over the restored relief with the same interval and lay them over the contours of step 2 in another colour, the lines should coincide and part only on the summits above the last contour. By number: the raster calculator, the expression "restored minus truth", and the statistics of the difference in the layer properties. At an interval of 5 m expect a mean near zero and a spread of about a metre, with the largest discrepancies on the peaks. Adding the peak points from step 5 into step 7 shrinks those summit discrepancies noticeably, and that is exactly the answer to why topographers write peak elevations on maps.

---

## Scenario 3. I need a section

Three minutes.

**Step 1.** **4.10 Create a sample for the section**
Minimum: nothing to set, defaults are fine.
*You get:* a set of layers in the Section sample group.

**Step 2.** **4.01 Section along a line**
Minimum: **Section line**, **Surfaces top to bottom**, **Vertical scale**.
*You get:* a section drawing in engineering coordinates, with axes, elevations and bands of the beds.

---

## Scenario 4. I need a rating curve for a cross-section

Three minutes.

**Step 1.** **6.04 Example river (demo)**
Minimum: nothing to set, everything by default.
*You get:* sections with the fields filled in, a table of soundings, a valley surface and a reference curve.

**Step 2.** **6.01 Cross-sections and rating curves**
Minimum: **Cross-sections**, the rest is picked up by the field names.
*You get:* a table of the dependence of discharge on level by parts and in total, the section profiles and an HTML report with a table for every section.

**Step 3.** **6.02 Flood extent polygon**
Minimum: the valley **Surface** from step 1, the **Sections** and the **Rating curve** from step 2, a **Discharge**, say 60.
*You get:* the flood extent and a raster of depth. For every section a level is found backwards along the curve, and a water surface rises from them.

---

## Scenario 5. I have my own sections and a DEM

This path is for those who already have data. It also answers the question of what exactly the tool is missing when the result looks odd.

**What the sections must carry.** Elevations in the Z of the vertices: a surveyed elevation is more accurate than any terrain model, so the source is the geometry of the section itself. If there is no Z, the tool says so.

**What is worth adding as fields.** `div_l` and `div_r` for the part boundaries as distances along the profile, `n_left`, `n_channel`, `n_right` for the roughness, `slope` for the slope, `km` for the chainage, `sec` for the name. The fields are picked up by these names on their own.

Without them the computation goes through, but the whole profile is treated as one channel with defaults, and the discharges come out several times too large: a floodplain hundreds of metres wide gets the roughness of a channel. This is the most frequent cause of implausible numbers.

**Step 1.** **6.03 Import section tables** - if the profiles are kept as tables of distance and elevation pairs.
Minimum: the **Table of soundings**. If the sections are already digitized on the map, supply them as a layer - the soundings will lie along the real lines.
*You get:* lines with Z ready for the next step. Skip this step if your sections already carry elevations.

**Step 2.** **6.01 Cross-sections and rating curves**
Minimum: the **Sections**. The slope can be computed from the chain if you give the chainage and tick the corresponding box.
*You get:* the curve, the profiles, the drawing footer, the ground elevations and a report with a graph. Check the accepted slope and roughness in the log: it also shows whether the fields were picked up or the defaults were taken.

**Step 3.** **6.02 Flood extent polygon**
Minimum: the **Surface** (your DEM), the **Sections on the map**, the **Rating curve** from step 2 and a desired **Discharge**.
*You get:* the flood extent and the depth. The discharge can be changed as many times as you like: the curve does not depend on it, and there is no need to run 6.01 again.

### Three places where people usually stumble

**One elevation for the whole valley.** The level is not constant along the river, upstream it is higher. Supplying a single level as a plane floods the valley where the water does not reach. That is why the levels go by section.

**The drawing layer of levels.** In the output "Probability levels (drawing)" the horizontal axis carries the distance along the section rather than a map coordinate. For 6.02 you need the output **Levels at the sections on the map**. The tool recognizes this and says so plainly.

**A discharge above the curve.** The curve is built up to the top of the profile, and in a catastrophic scenario the water rises above the banks. Such a section is skipped with a warning. If you need large scenarios, raise the upper elevation in 6.01 and the curve will continue with a margin.

---

## Where to go next

The manual describes every tool in detail, with parameters and caveats. This page holds neither options nor theory on purpose: its job is to walk you through the module once, not to replace the documentation.

OpenStreetMap data: © OpenStreetMap contributors, ODbL. The Copernicus GLO-30 and GEDTM30 matrices are distributed freely.
