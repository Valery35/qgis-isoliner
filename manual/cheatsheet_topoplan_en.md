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
  - \AtBeginDocument{\setstretch{0.9}}
  - \setlength{\parindent}{0pt}
---

\begin{center}
{\Large\sffamily\bfseries Isoliner · Terrain from a topographic plan: quarries, cuts, fills, dumps}\\[1pt]
{\small A cheat sheet for the 2.22 → 2.20 → 2.03 chain · two sheets · QGIS 3.16+ · Isoliner v4 · pure NumPy, no GRASS or SAGA}
\end{center}

\vspace{2pt}

# The problem

In a delivered topographic plan, areal quarries, cuts, fills, terricones and dumps **carry no contours inside** - the standard requires it, only the outer outline is agreed by node points. Terrain built from such a plan has a hole exactly where the working is. There is nothing to fill it with except the crests and toes, which the same deliverable describes as a matter of course.

The industry requirements put it plainly: terrain created automatically has to be corrected by hand, agreeing it with the heights of retaining walls, slopes and fills. The chain below replaces that prescribed correction with a rule.

# What is needed from the deliverable

- **Contours** with an elevation in a field or in Z. The ordinary plan layer.
- **Crests and toes** of the areal forms. In a deliverable they are coded: a reinforced crest of a slope, a fill or a cut, the code 62350400 and the like. No classifier holds a code for a toe, it goes as the lower contour or the line of the base.
- Spot heights, thalwegs and the water edge, if present, go into 2.03 as usual. The slope lines carry no elevations, and that is normal: step 1 gives them.

# The chain

**Step 1. Elevations - 2.22 Profiling of slopes.** Input: the crest and toe lines plus the contour layer, adjoining tolerance 0.5 m. Output: the same lines as LineStringZ with a profile, and a separate layer of mute lines with the reason.

The mechanics: a contour **does not run along the crest**, contours are cut by the slope. But the standard requires bringing them up to the object line with node points, and every point carries the elevation of its contour - from them the profile of the line is recovered.

In the log: the median and the minimum of support points per line. One point means a constant elevation along the whole line, that is, no profile came out.

**Step 2. Forms - 2.20 Crests and toes into work.** Input: the lines with elevations from step 1. **Leave the DEM empty** - without it the pairs are assembled from the elevations: the toe is the nearest line lying below the crest. Keep the path limit close to the width of the face. Output: the **Top** and **Bottom** layers sharing the **link** field, plus the unpaired with a reason.

The kind field is read tolerantly: the brow and toe values, the crest codes of the classifiers, names containing the words for a crest or a toe. How every value was read is printed to the log as a table - check it on the very first layer.

**Step 3. Terrain - 2.03 Topo2Raster.** The usual inputs plus **Top of forms** and **Bottom of forms** from step 2, link field. Set the cell size explicitly.

**Step 4. Check.** Contours from 1.04 over the result, on top of the original ones: outside the form they must coincide, inside they must appear where there were none. The residual is measured by 2.12.

# Why the order is what it is

The descent in 2.20 answers a single question: which way is down. That used to require a DEM, and the scenario went in a circle - to build terrain from forms you need pairs, and for pairs by descent you need terrain that does not exist yet. When the lines carry elevations, the answer is already in the data. Hence the order: elevations first, then pairs, then terrain.

# How it is computed

- **A side of a form is a set.** Any number of lines and points with one link value goes into the top or the bottom. All the cases follow: a slope is a crest and a toe, a pit is a closed crest and a point on the floor, a ring dump is two closed lines, a ditch is two crests and a floor line.
- **No correspondence of points is sought.** For a cell the distances to both sides are taken, the weight is their ratio and the elevation is linear in the weight; an overlap is impossible by construction. The body goes in as hard nodes, the border as a barrier, and the relaxation scheme of 2.03 does not change.
- **There is no significance threshold.** Two sides given - a surface is built, one given - a barrier. The standard writes "expressed at the scale of the map" instead of a number, and every client has numbers of their own: 0.5 m for walls, a metre for slopes, two on a road fill.

# What to read in the log

- **2.22:** contours with an elevation, how many lines received a profile, how many stayed mute, the median and the minimum of support points.
- **2.20:** the table of how the kind field was read, the number of forms, toes and crests at them, the unpaired.
- **2.03:** per form - body cells, median width in cells, the mismatch of elevations where the sides converge.

# Common misfires

- **"Forms narrower than two cells".** The cell is coarser than the face: with a 7 m width and a 30 m cell the bench does not exist in the raster at any scale. Refine the cell rather than tune the parameters.
- **The lines stayed mute.** The contours are not brought up to the crest with node points. Raise the adjoining tolerance, but carefully: a large one starts catching foreign contours.
- **The pair did not form.** No toes lie below the crest, or the nearest one is beyond the limit. The reason is in the attribute of the unpaired layer. Flat lines without Z are skipped in the no-DEM mode: put them through 2.22.
- **Values of the kind field are not recognised.** The tool lists them in the log and skips them: it will not guess. Add a field of your own with brow and toe.
- **The surface is smooth where it should be benched.** There are several benches and one link for all of them: every bench needs its own link value.

\vspace{2pt}\vfill\hrule
\vspace{1pt}
{\scriptsize Plugin: plugins.qgis.org/plugins/grid\_isolines · The manual ships with the plugin (doc/Isoliner\_en.pdf) · Inform++ LLC · www.informpp.ru}
