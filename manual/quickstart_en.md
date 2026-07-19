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

**Step 5.** **2.09 Peaks**
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

## Where to go next

The manual describes every tool in detail, with parameters and caveats. This page holds neither options nor theory on purpose: its job is to walk you through the module once, not to replace the documentation.

OpenStreetMap data: © OpenStreetMap contributors, ODbL. The Copernicus GLO-30 and GEDTM30 matrices are distributed freely.
