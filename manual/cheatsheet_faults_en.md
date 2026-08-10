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
{\Large\sffamily\bfseries Isoliner · Faults: from the grid to the map}\\[1pt]
{\small Cheat sheet on faults · QGIS 3.16+ · Isoliner plugin v4.99+ · pure NumPy}
\end{center}

\vspace{2pt}

# A five-minute run on demo data

**Step 1. Teaching data - 1.09 Example wells (demo).** Set the extent, and in the advanced section set **Fault throw** to 20. Outputs: a well point layer and a **Fault (demo)** layer, a line with a dying end.

**Step 2. Grid - 1.02 2D Kriging.** Input: the wells of step 1, field **roof**. Supply the line of step 1 to the **Faults** field. Leave everything else at its default. The log will report the number of fault segments.

**Step 3. Map - 1.04 Isolines from raster.** Input: the grid of step 2. Supply the same line to the **Faults** field. Leave **Corridor width at a fault** at 1.

Done. The isolines break exactly along the line, the boundaries of the contour polygons run along it as well, and above the end of the fault the surface closes up.

The same line layer is accepted by **1.03 Minimum curvature** in place of step 2, if you would rather not fit a variogram.

# What goes where

\begingroup\footnotesize\renewcommand{\arraystretch}{1.3}

| Tool | What the fault does | Key parameters |
|:--|:--|:--|
| **1.02** 2D Kriging | barrier of influence: a measurement whose segment crosses the line does not enter the sample of the cell | the line layer only. The wing of a cell follows by itself |
| **1.03** Minimum curvature | barrier on the grid edges: an edge crossed by the line drops out of the stencil | the line layer only. A membrane works at the line |
| **1.04** Isolines from raster | cuts the isolines along the line, and the line enters the network that builds the belts | **Corridor width** (cells, default 1), **Smallest polygon thickness** (default 0) |
| **1.10** Example wells | creates a teaching fault and shifts the values on one side | **Fault throw** (units of the value, default 0) |

\endgroup

A fault is an ordinary line layer: draw it by hand, load it from a shapefile, take it from a survey. Nothing special is needed in the attributes. The `throw` field of the demo fault is for reference, the tools do not read it.

# What looks odd and is nevertheless right

- **A bundle of isolines at the end of the fault.** That is how it should be: the throw falls to zero and all intermediate levels fit into a narrow strip. In the model the decay falls on a single cell, so the bundle is shorter than for a real fault.
- **A short stretch of crowded isolines right at the end.** The corridor deliberately does not reach there: the break has come to nothing, the surface closes up and there is nothing to tear.
- **A slightly less smooth surface at the line (1.03 only).** Within a band two nodes wide a membrane is used: the minimum-curvature stencil reaches two cells out and would otherwise step over the break.
- **The line does not reach the edges of the area.** Nor should it. For a dying fault the influence goes round the end, and that is what tells the method apart from gridding blocks separately.

# Common pitfalls

- **Faults supplied to the grid only.** The most common one. The isolines do break by themselves, but a strip of crowded isolines is left along the line and the belt boundaries do not follow the fault. Supply the same layer to 1.04 as well.
- **The corridor is switched off (0).** The strip of crowded isolines across the break stays. That is interpolation across a break the bed does not know about. The default of one cell removes it.
- **The corridor is set too wide.** Isolines that run along the fault for a reason will start to disappear. There is no point taking less than a cell either: the jump occupies exactly one cell.
- **The polygon thickness threshold set just in case.** On a steep surface with a fine interval a normal belt is itself narrower than a cell, and a threshold of one cell will mow the map down. The tool warns in the log if it has filtered out more than half of the belts. Leave it at 0 when faults are supplied.
- **A fault along an axis of the mine grid.** It works: a node falling exactly on the line is assigned to one side. There is a separate test for that.

# The price of the approximation

Only the neighbours are selected by visibility. The covariances between the measurements themselves stay Euclidean: zeroing them is not allowed, the matrix loses positive definiteness and the kriging system becomes singular. That is why kriging with faults is rare.

In practice: near the line the weights of the neighbours are computed without regard for the break lying between two of those neighbours. At distances of the order of the correlation range this is negligible, on a very dense network at the line it can slightly lower the contrast. The break itself is held by the selection of neighbours and does not go anywhere.

\vspace{1pt}\hrule
{\footnotesize Plugin: plugins.qgis.org/plugins/grid\_isolines · Manual included (doc/Isoliner.pdf), the "Faults" sections in chapters 1.02, 1.03 and 1.04 · Inform++ LLC · www.informpp.ru}
