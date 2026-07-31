# Checking a relief: a cheatsheet

Three tools answer three different questions. They are run in one order and produce numbers that can be shown to somebody.

| Question | Tool |
|---|---|
| How well the DEM reproduces the source contours | 2.12 Contour residuals against the DEM |
| What happens where there were no contours | 2.11 plus 2.12 |
| Whether the surface went in steps | 2.13 Terracing check of a DEM |

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

## A full run on the demo data, ten minutes

The validation data ship with the plugin, nothing needs downloading.

**Step 1. Make a relief with gullies.** Tool **2.10 Demo relief**, tick **Gully and ravine network**. You get a synthetic relief with incised thalwegs. The gullies are not decoration: a narrow cut between adjacent contours is the hardest place for any interpolation, and every defect shows up exactly there.

**Step 2. Take contours off it.** Tool **1.04 Isolines from a raster**, interval 5 metres. These will be our source data, and at the same time our reference, because we know the true relief.

**Step 3. Split the contours.** Tool **2.11 Split contours for validation**, hold out every fourth elevation. Two layers come out, both with a **hold** field.

**Step 4. Build the relief anew.** Tool **2.03 Topo2Raster**, fed only with the **Contours for building** layer. The held-out contours have not been seen by the tool.

**Step 5. Measure the residual.** Tool **2.12**, with the contours supplied as the combined set (both layers from step 3) and the DEM from step 4. Two lines appear in the log, reproduction of the input and prediction on the held-out set.

**Step 6. Check for terracing.** Tool **2.13**, the DEM from step 4 and the contour layer to detect the interval.

## What you should get

Reproduction of the input gives an SD noticeably below half the contour interval. If it is above, the interpolator does not hold its own data, and that alone is worth investigating.

Prediction on the held-out set is always worse. This is normal and expected. What matters is the gap: how many times larger the second figure is than the first. A gap of two or three times is ordinary for a relief with gullies, a gap of an order of magnitude means the shape between the contours is restored badly.

An attraction ratio near one means there is no terracing. One and a half is a reason to look at the curvature raster by eye, two and above means steps.

## Where to look when the numbers are bad

A large mean means a systematic shift in elevation. Look for an error in the data: mixed-up units, a shifted base elevation, the wrong height system.

A large SD with a mean near zero means the forms are being cut off. Look at the spread of the residual by elevation in the report: if the residual grows towards the thalwegs, the interpolation is smoothing the incisions away and breaklines are needed.

A noticeable share of misses beyond half the interval means that contours taken from the built DEM will not match the source ones. The map stops agreeing with itself, and this is visible to anyone who overlays the two layers.

An attraction ratio above two means terracing. The curvature raster will show bands repeating the pattern of the contours. The cure is breaklines and denser sampling, not smoothing: smoothing removes the steps together with the forms.

## Checking on your own data

The order is the same, only steps 1 and 2 are unnecessary: the contours are your own. Start at step 3.

If the interval is not uniform, with contours collected from different maps or at different accuracy, set the interval by hand in 2.12 and 2.13. Automatic detection takes the smallest difference between adjacent elevations and on a mixed set will return too small a number.

If the contours are sparsely digitised, set a sampling step along the contour in 2.12. Zero means vertices only, and on long straight legs there will be few samples.

## What these numbers do not measure

The residual measures the agreement of the surface with the contours, not the correspondence to the ground. If the contours were surveyed wrongly, the DEM will reproduce their error and all the figures will look excellent. A real accuracy assessment needs independent elevations, from a geodetic survey for instance.
