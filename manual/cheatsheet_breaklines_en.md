---
geometry: "a4paper, margin=0.85cm"
mainfont: "DejaVu Serif"
sansfont: "DejaVu Sans"
fontsize: 7.6pt
pagestyle: empty
header-includes:
  - \usepackage{titlesec}
  - \titlespacing*{\section}{0pt}{4pt}{2pt}
  - \titlespacing*{\subsection}{0pt}{3pt}{1pt}
  - \setlength{\parskip}{1pt}
  - \usepackage{enumitem}
  - \setlist[itemize]{nosep,leftmargin=1.1em,topsep=1pt}
  - \usepackage{setspace}
  - \AtBeginDocument{\setstretch{0.86}}
  - \setlength{\parindent}{0pt}
---

\begin{center}
{\Large\sffamily\bfseries Isoliner · Structural lines: crests and toes}\\[1pt]
{\small A cheat sheet for tools 2.19-2.21 · QGIS 3.16+ · Isoliner plugin v4 · pure NumPy, no GRASS or SAGA}
\end{center}

\vspace{2pt}

# Pipeline: from a dense survey to crest-toe pairs

**Step 1. Terrain.** A dense survey (UAV, laser): on a sparse one there is no formal evidence of a crest in the data at all. A **ground** raster, not a DSM: vegetation and machinery are removed by classification before Isoliner. Cell from density: 20-25 cm at 20 points per m², 15 cm at 40. Without your own data use **2.21 Create a demo open pit**.

**Step 2. Candidates - 2.19 Crest and toe candidates.** Input: the terrain of step 1. The noise cut-off is 0.5 m, the minimum length 10 cells, the probe base 8 cells. Output: lines with the fields **drop** (across the line, m), **length\_m**, **slope\_deg** and **kind** (brow or toe). The layer arrives coloured by the drop.

**Step 3. Selection - by eye, not by recomputation.** Look into the log: it holds the percentiles of the drop over all candidates. Set a layer filter `"drop" > 1.0` and move the number while watching the map. There is no need to rerun 2.19, and the threshold you choose is your criterion of a bench.

**Step 4. Forms - 2.20 Crests and toes into work.** Input: the candidates of step 2 (the whole layer, the drop cut-off is available here too) and the same DEM. Keep the descent path limit close to the width of the face. Output: **Top** and **Bottom** with a shared **link** field, elevations taken off the DEM into the Z geometry, plus a layer of unpaired lines with the reason.

**Step 5. Onwards.** The forms go into surface building between lines, into 2.03 as structural constraints, or to an export into AutoCAD and Credo as 3D lines.

A check on the demo: candidates over the true lines of 2.21. They must lie along them, break on the ramp arc, and give two crests with no toes at the ditch.

# Tool reference: input, output, key parameters

\begingroup\footnotesize

| Tool | Input | Output | Key parameters |
|---|---|---|---|
| **2.19** Crest and toe candidates | DEM (dense, ground) | lines: kind, drop, length\_m, slope\_deg | the 0.5 m drop cut-off removes ruts. Length 10 cells. The probe base is 8 cells: the drop is measured within it, not across the whole bench |
| **2.20** Crests and toes into work | candidates of 2.19, the same DEM | Top and Bottom (LineStringZ, kind, link) + the unpaired with a reason | descent path limit 50 m, by the width of the face. The share of agreeing probes 0.4 is in the advanced parameters |
| **2.21** Create a demo open pit | nothing | DEM + true lines (kind, link) | 3 benches, height 10 m, noise 3 cm. Dump and ditch by checkboxes. The seed repeats the run |

\endgroup

# How it works, so that nothing has to be guessed

- **The evidence of a break** is the magnitude of the slope gradient, not the slope itself. On an even face, however steep, the slope is constant and the evidence is small. The evidence is large where the slope changes, that is on a crest and on a toe.
- **The sign of the curvature** splits the lines: a convex break is a crest, a concave one is a toe. The same **kind** field sends a line into Top or Bottom in 2.20.
- **A break without a drop is not a break.** A ridge of the evidence must gain the given relief drop in its neighbourhood, otherwise it is discarded before any thresholds. Centimetre noise never gains it, the neighbourhood of a crest always does. This is the main filter, and it is physical rather than statistical.
- **Forms are assembled by descent, not by proximity.** From the probe vertices of a crest a descent follows the flow directions to a toe, and the toes vote. Water from a crest runs exactly to its own toe, and on a curved wall with narrow berms this is the only way not to take the neighbouring bench.
- **A form is one toe with a set of crests at it.** The tracing cuts a long crest into pieces, all the pieces descend to the same toe, and the toe is written into the output once rather than once per piece.
- **The drop is measured within the probe base.** On a ten-metre bench with a three-cell base the drop reads three metres. The base is 8 cells by default, set it by the width of the face.
- **The significance threshold stays with the human.** A formal definition of a crest does not exist. What exists is the drop you are prepared to call a bench, and it differs on a quarry, on a road embankment and on a river bank.

# Common pitfalls

- **A DSM instead of ground.** Crowns and truck sides are honest breaks, the detector will find them. Classify before Isoliner.
- **A cell coarser than the bench.** A bench narrower than two cells does not exist in a raster. Refine the cell rather than tune the parameters.
- **An empty output.** Look at the drop percentiles in the log: if the maximum is below the cut-off, the cut-off is to blame.
- **A line has glued two benches.** Visible in the 2.20 log as a low share of agreeing probes, and such a line goes into the unpaired. Cured by raising the drop cut-off or by cutting the line by hand.
- **A gentle junction at the toe.** Where a face passes smoothly into the base, the evidence is weak and the toe may not be found at all. This is an honest limit of the method rather than a failure: there is physically no break there.
- **The descent did not reach a toe.** The path limit is too small, or the crest is false.

\vfill\vspace{1pt}\hrule
{\footnotesize Plugin: plugins.qgis.org/plugins/grid\_isolines · The manual ships with the plugin (doc/Isoliner\_en.pdf) · Inform++ LLC · www.informpp.ru}
