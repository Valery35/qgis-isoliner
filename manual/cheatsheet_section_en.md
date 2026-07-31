---
geometry: "a4paper, margin=1.1cm"
mainfont: "DejaVu Serif"
sansfont: "DejaVu Sans"
fontsize: 8.4pt
pagestyle: empty
header-includes:
  - \usepackage{titlesec}
  - \titlespacing*{\section}{0pt}{7pt}{3pt}
  - \titlespacing*{\subsection}{0pt}{5pt}{2pt}
  - \setlength{\parskip}{2pt}
  - \usepackage{enumitem}
  - \setlist[itemize]{nosep,leftmargin=1.2em,topsep=2pt}
  - \usepackage{setspace}
  - \AtBeginDocument{\setstretch{0.98}}
  - \setlength{\parindent}{0pt}
---

\begin{center}
{\LARGE\sffamily\bfseries Isoliner · The geological section}\\[2pt]
{\large\sffamily How the module is built and what its awkward parameters mean}\\[3pt]
{\small QGIS 3.16+ · Isoliner v4 · pure NumPy, no GRASS or SAGA}
\end{center}

\vspace{4pt}

# Where it all begins

A section in Isoliner is not a picture but a **coordinate system**. The tool **4.01 Section along a line** turns a line on the map into a drawing plane with distance-elevation axes and produces four things: the drawing of the beds, the terrain line, the axes with a frame, and the **section definition** - a service layer with the `sec_id`, `vex`, `ox` and `oy` fields.

The definition is the key to everything else. All the other tools of the group take it as an input and thanks to it place their objects in the same coordinates as the first drawing. Hence the simple rule: **4.01 first, everything else after**, and the "Section definition" field takes the output of 4.01, not the original line on the map.

There may be several sections at once. They are then told apart by the `sec_id` field and spread on the drawing by a layout - stacked, in a row or in a grid. The offset of each is kept in `ox` and `oy`, and the tools apply it themselves.

# Vertical exaggeration: three numbers instead of one

The most frequent source of confusion. The exaggeration is set in three ways, and they answer different questions.

**A factor** - the vertical scale is so many times larger than the horizontal one. Direct and clear, but it requires knowing the length of the section: a factor of 3 on a kilometre-long profile with a fifty-metre range gives a flat ribbon, while on a hundred-metre one it gives an unreadable accordion.

**The ratio of scales H:V (1:N)** - the familiar drafting notation, the same number from the other side.

**The ratio of the drawing's dimensions** - the factor is chosen automatically so that the width of the drawing relates to its height as required. This is what is needed in most cases: a section of any length fits the sheet equally well. A value of 3 or 4 is a reasonable starting point.

Note: **the vertical exaggeration is one for the whole run.** Otherwise the sections would not be comparable with each other.

**And the main thing about exaggeration.** It distorts **all** the angles on the drawing, not only the dip traces. At a factor of 10 a bed with a true inclination of 5 degrees lands on paper at 41. Angles are therefore not measured on the drawing with a protractor - the numbers are in the attributes for that.

# What reaches the section and how

| Tool | What it places | Rule |
|---|---|---|
| **4.02** | Boreholes with intervals | The hole by depth, the intervals in their own colours |
| **4.03** | Bed composition | Bands by grade from the drilling model |
| **4.04** | Surfaces | Raster roofs and floors as lines |
| **4.05** | Vector objects | Exact intersection with the section line |
| **4.06** | TIN | Triangles, overturned ones included |
| **4.07** | Projection by a corridor | Approximate, everything near the line |
| **4.08** | Back from the section | What was digitized on the drawing, into world coordinates |

The difference between **4.05** and **4.07** is fundamental. The first takes the objects that the section line really crosses and places them at the exact station. The second gathers everything that fell into a corridor of a given width and projects it - approximate by construction, but sometimes the only way.

# 4.05: the rule by object type

The tool decides what to draw from the geometry of the input. Knowing this rule matters: it explains why one layer comes out as verticals and another as points.

- **A line without an elevation** (flat in plan: a fault, a boundary, an outline) - a full-height vertical at the crossing station. The logic: the where is known, the depth is not.
- **A line with an elevation** (three-dimensional: a surface outline, an inclined object) - a point at the real height.
- **A polygon** (a zone in plan: a replacement, a mine field, a licence) - a vertical band over the interval where the section runs through the zone.

A layer of mixed types is taken apart object by object, each along its own branch.

# Dips: apparent, not true

A vertical is honest but poor: the object has an inclination and one wants to see it. When the **dip** and the **dip direction** are given in 4.05, the vertical is replaced by a dip trace and a zone band by a parallelogram.

Here begins the place where people go wrong most often.

**What reaches the drawing is the apparent angle, not the true one.** It depends on the angle at which the section cuts the strike:

\begin{center}
tan(apparent) = tan(true) · cos(dip direction − section azimuth)
\end{center}

A section across the strike gives the true angle. A section along the strike gives **zero**: the object lies flat, and this is not a failure but the truth about the geometry - along the strike the bed really is not inclined.

**The dip direction is required and cannot be derived from the geometry.** One and the same fault may dip east or west, and the line in plan says nothing about it. The silent assumption that the plane is perpendicular to the section is the very error all of this is written for. Without the direction the tool leaves the objects vertical and does not pretend to know more than it does.

**The side of the inclination needs no parameter.** The sign of the cosine decides it: a dip along the section gives a trace going down from left to right, an opposing one the other way.

**The azimuth of the section is taken from the segment.** A section line is usually bent, and the azimuth changes along it. The tool takes the azimuth of the segment carrying the intersection, so on a curved profile one and the same bed gets a different apparent angle in different places. That is how it should be.

**The trace length** is set horizontally in metres, zero means down to the frame. A short pointer trace is usually taken as one and a half to two centimetres on the sheet: at 1:2000 that is 30-40 m.

Three numbers go into the attributes of the output: `dip` (true), `dip_az` and `app_dip` (apparent). The third is what you see on the drawing before the exaggeration is applied.

# 4.12: the attitude out of the data, not out of a table

When a boundary is digitized **with elevations**, the dips need not be given - they are already in the geometry. The tool **4.12** recovers the plane from the outcrop trace and produces `dip` and `dip_az` as ready fields for 4.05.

It computes over all the vertices at once through the eigenvectors of the orientation matrix, not over three points. The reason is technical but important: the familiar fit of the form `z = a·x + b·y + c` falls apart on steep attitudes where the coefficients run to infinity, while through the eigenvectors a vertical plane is handled on a par with a gentle one.

**A trace straight in plan does not define an attitude.** Infinitely many planes pass through one straight line in space - they rotate about it like pages about a spine. The tool sees this and **refuses with a reason** instead of producing a confident number out of rounding noise. The measure is in the `planar` field: zero means points on a line, one means a real spread in two directions.

**A fold is caught separately.** The method assumes one plane for the whole trace, and a long boundary rarely is one: a flexure, an inflection, a displacement by a fault. The residual of the points from the plane goes into `rms`, and a large value means that no single attitude exists here. Then turn on the **window** - the attitude will be computed over a sliding stretch and you will get its change along the boundary instead of one averaged number that does not exist in nature.

# The assembled chain

For a topographic deliverable, where the lines carry no elevations, everything links up like this:

1. **2.22 Elevations from adjoining contours** - a flat boundary receives a profile from the node points where the contours adjoin it.
2. **4.12 Attitude from an outcrop trace** - the dip and the dip direction are computed from the three-dimensional trace.
3. **4.01 Section along a line** - the coordinate system of the drawing is built.
4. **4.05 Intersect vectors with the section** - the objects land on the section with a real inclination.

Not a single external tool, not a single number taken out of thin air.

# Check it on the demo in five minutes

**4.10 Create an example for the section** produces a ready scene: six surfaces, five beds, three section lines, boreholes with intervals, zones, a TIN - and two layers specifically for checking the angles.

**Structural elements** - three lines on the straight Section 2 with one true dip of 25 degrees and different dip directions. The apparent angles come out as 25, 18.25 and zero. The expected value is computed and lies in the `app_exp` field, so the check goes by a number: `app_dip` must match `app_exp`.

**Outcrop traces** - three three-dimensional lines for 4.12: gentle (20 degrees), steep (72) and deliberately straight in plan. For the first two the tool will return the numbers from `dip_true` and `az_true`, the third checks the refusal.

The straight section was chosen for the reference on purpose: on a bent line the azimuth of the segment changes and an unambiguous expected value does not exist.

# Common misfires

- **"Transformation parameters between the coordinate systems are unavailable"** - not an error. The drawing lives in an artificial system with distance-elevation axes and is not tied to the ground.
- **The section came out as a flat ribbon** - the vertical exaggeration was set by a factor without regard to the length. Switch to the ratio of dimensions.
- **The original line was fed into "Section definition"** - it has no `vex` field. The output of 4.01 is needed.
- **The objects stand vertical although the angles are given** - the dip direction is missing or its field is named differently. Check the field names, they are given as strings and looked up in every layer separately.
- **The apparent angle came out zero** - the section runs along the strike. That is the right answer, not a failure.
- **An attribute of an object collided with a service column** (`sec`, `d`, `label`, `dip`, `dip_az`, `app_dip`) - it is renamed with a suffix. Otherwise the attribute would silently replace a coordinate of the section.
- **4.12 refused every trace** - the lines are flat, without Z, or straight in plan. The reason is written into a separate layer.

\vspace{4pt}\vfill\hrule
\vspace{2pt}
{\scriptsize Plugin: plugins.qgis.org/plugins/grid\_isolines · The manual ships with the plugin (doc/Isoliner\_en.pdf) · Inform++ LLC · www.informpp.ru}
