# Checking a relief: a cheatsheet

Three tools answer three different questions. They are run in one order and produce numbers that can be shown to somebody.

| Question | Tool |
|---|---|
| How well the DEM reproduces the source contours | 2.12 Contour residuals against the DEM |
| What happens where there were no contours | 2.11 plus 2.12 |
| Whether the surface went in steps | 2.13 Terracing check of a DEM |

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
