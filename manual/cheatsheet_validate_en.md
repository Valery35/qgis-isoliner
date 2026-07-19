# Validating a relief: cheatsheet

One page for the whole cycle. It answers the question that usually ends an argument about DEM quality: and how would you prove that.

## Three tools

| Tool | What it does |
|---|---|
| 2.11 Split contours for validation | Holds out every Nth elevation in full, so there is something to check against |
| 2.12 Contour residuals against the DEM | Measures the disagreement between contour elevations and the built surface |
| 2.13 Terracing check of a DEM | Looks for steps: benches at the levels and drops between them |

## Working order, five steps

**Step 1. Split.** Tool 2.11, fed with the source contours and the elevation field. Hold out every 4th. Two layers come out, both with a **hold** field.

**Step 2. Build.** By whatever method you use, 2.03 Topo2Raster for instance, but only from the **Contours for building** layer. This matters: build from all of them and there is nothing left to check.

**Step 3. Measure.** Tool 2.12. Feed it both sets together (merge the layers) and the built DEM. It recognises the **hold** field by itself and prints two lines.

**Step 4. Read the two figures.** The first, reproduction of the input, shows how well the surface holds the contours it has seen. The second, prediction on the held-out set, shows what it does between them. What matters is the gap.

**Step 5. Check for steps.** Tool 2.13 over the same DEM, taking the interval from the contours. A ratio near one means there are no steps.

## What the numbers mean

| Quantity | Normal | What a departure says |
|---|---|---|
| Mean residual | near zero | The surface is systematically shifted in elevation |
| SD of the residual | well under half the interval | Forms are cut off or the data are noisy |
| Missing by half an interval | a few percent | Contours from such a DEM will not sit where the originals were |
| Gap between the two figures | prediction worse by half again to twice | Threefold and more means the model memorises the input and fails to restore the shape between contours |
| Level attraction (2.13) | near 1.0 | 1.5 is worth a look, 2.0 and above is terracing |

There is always a gap between reproduction and prediction, and that is normal. An interpolator is bound to hold what it has seen. The trouble is not that the second figure is worse, but when it is worse several times over.

## Where to look with your eyes

Numbers answer how much, pictures answer where.

**The residual point layer**, coloured by the **resid** field with a diverging ramp. Systematic patches show up at once: where the surface runs low or high over whole areas, it is not noise.

**The vertical curvature raster** from 2.13. If its bands repeat the pattern of the contours, that is terracing, even when the attraction ratio has not yet reached two.

**The phase histogram** in the 2.13 report. A flat one means a healthy surface, a peak in the middle means the elevations cling to the levels.

**A profile across a gully** through the QGIS **Elevation Profile**. The most telling of all: a narrow cut is either reproduced or shaved off, and that needs no statistics.

## Test data out of the box

Tool 2.10 Demo relief with the **Gully and ravine network** tick gives a deterministic relief with incised thalwegs. Tool 1.04 Isolines from a raster then takes contours off it, and the cycle closes: the true surface is known in advance.

Such a cycle is more useful than any argument: build the relief from the extracted contours, compare it with the source raster and see what exactly was lost. Gullies are lost first, which is why the fragment is made of gullies and ravines.

## Frequent questions

**Why not check against all the contours at once.** Because the interpolator has seen them. The figure will look good and mean nothing. That is a check of input reproduction, not of accuracy.

**Why split by elevation rather than by feature.** The neighbouring pieces of the same contour give the answer away. A level has to disappear entirely.

**Why the extreme elevations are never held out.** Beyond the range of the set the interpolator extrapolates. A residual there would measure something other than what the check is for.

**What if the interval is detected wrongly.** Set it by hand in the advanced parameters. Auto-detection takes the smallest difference between adjacent levels and gets confused when the set is assembled from different sources.

---

Isoliner, Informpp LLC, https://www.informpp.ru/
