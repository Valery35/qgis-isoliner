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
{\Large\sffamily\bfseries Isoliner · Checking a relief: a demo run and a reference}\\[1pt]
{\small A cheat sheet for tools 2.10-2.13 · QGIS 3.16+ · Isoliner plugin · no GRASS or SAGA}
\end{center}

\vspace{2pt}

Three tools answer three different questions. They run in one order and produce numbers that can be shown to a client.

| Question | Tool |
|---|---|
| How well the DEM reproduces the source contours | 2.12 Contour residuals against the DEM |
| What happens where there were no contours | 2.11 Split contours for validation, then 2.12 |
| Whether the surface went in steps | 2.13 Terracing check of a DEM |

# A full run on the demo, ten minutes

The validation data ship with the plugin, nothing needs downloading. The true surface is known in advance, so you can see what exactly is lost in building.

**Step 1. A relief with gullies - 2.10 Demo relief**, tick **Gully and ravine network**. A narrow cut between adjacent contours is the hardest place for any interpolation, and every defect shows up exactly there.

**Step 2. Contours - 1.04 Isolines from raster**, isoline step 5 m. These are the source data and the reference at the same time, because the true relief is known.

**Step 3. Split - 2.11 Split contours for validation**, hold out every 4th elevation. Two layers come out, both with a **hold** field.

**Step 4. Build anew - 2.03 Topo2Raster**, fed only with **Contours for building**. The tool has not seen the held-out contours.

**Step 5. Residuals - 2.12 Contour residuals against the DEM.** Supply the contours as the combined set (both layers of step 3) and the DEM of step 4. Two figures appear in the log, reproduction of the input and prediction on the held-out set.

**Step 6. Terracing - 2.13 Terracing check of a DEM.** The DEM of step 4 and the contours to detect the interval.

On your own data steps 1 and 2 are not needed, start at step 3.

# What the numbers mean

| Quantity | Normal | What a departure says |
|---|---|---|
| Mean residual | near zero, within a tenth of the interval | the surface is systematically shifted in elevation |
| SD of the residual | under half the interval | forms are cut off or the data are noisy |
| Share of misses beyond half an interval | up to 5% | contours from such a DEM will not sit where the originals were |
| Gap between the two figures | prediction worse, but less than threefold | threefold and more means the shape between contours is not restored |
| Level attraction (2.13) | near 1.0 | 1.5 is worth a look, 2.0 and above means terracing |

There is always a gap between reproduction and prediction, and that is normal. An interpolator is bound to hold what it has seen. For a relief with gullies a gap of two or three times is ordinary. Threefold and more means the shape between the contours is restored badly, and the 2.12 report says so.

# Where to look when the numbers are bad

**A large mean** means a systematic shift in elevation. Look for an error in the data, such as mixed-up units, a shifted base elevation or the wrong height system.

**A large SD with a mean near zero** means the forms are being cut off. Look at the residual by elevation in the report. If it grows towards the thalwegs, the interpolation smooths the incisions away and breaklines are needed.

**A noticeable share of misses beyond half the interval** means that contours taken from the built DEM will not match the source ones. The map stops agreeing with itself, and anyone who overlays the two layers will see it.

**Attraction above two** means terracing. The curvature raster shows bands repeating the pattern of the contours. Breaklines and denser sampling help, smoothing does not, because it removes the steps together with the forms.

# Where to look on the map

**Residual points** coloured by the **resid** field with a diverging ramp. Where the surface runs low or high over whole areas, it is not noise.

**Vertical curvature** from 2.13. If its bands repeat the pattern of the contours, that is terracing, even when the attraction ratio has not yet reached two.

**The phase histogram** in the 2.13 report. A flat histogram means a healthy surface, a peak in the middle means the elevations cling to the levels.

**A profile across a gully** through the QGIS elevation profile. A narrow cut is either reproduced or shaved off, and that needs no statistics.

# Frequent questions

**Why not check against all the contours at once.** The interpolator has seen them, so the figure looks good and means nothing. That is a check of input reproduction, not of accuracy.

**Why split by elevation rather than by feature.** The neighbouring pieces of the same contour give the answer away, so a level has to disappear entirely.

**Why the extreme elevations are never held out.** Beyond the range of the set the interpolator extrapolates, and a residual there would measure something other than what the check is for.

**What if the interval is detected wrongly.** Set it manually in 2.12 and 2.13. Auto-detection takes the smallest difference between adjacent elevations and on a set from different maps returns too small a number.

**The contours are sparsely digitised.** Set a sampling step along the contour in 2.12. At zero only vertices are used, and long straight legs get few samples.

# What these numbers do not measure

The residual measures the agreement of the surface with the contours, not the correspondence to the ground. If the contours were surveyed wrongly, the DEM reproduces their error and all the figures look excellent. A real accuracy assessment needs independent elevations, from a geodetic survey for instance.

\vfill\vspace{1pt}\hrule
\begin{footnotesize}Plugin: plugins.qgis.org/plugins/grid\_isolines · The manual ships with the plugin (doc/Isoliner\_en.pdf) · Inform++ LLC · www.informpp.ru\end{footnotesize}