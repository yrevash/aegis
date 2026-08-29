# PS-17 — experience and visuals

The team's going-in view was that PS-17 has "caveats in the front end". The research inverted
that: **PS-17 8.5, PS-04 6.0.** This file is why, and how to realise it.

## Why the prior was wrong

The prior compared **raw material** — covenants come with numbers, numbers come with charts;
contracts come with text. True, and it evaluates the wrong thing. A five-minute CTO demo is won by
**one legible moment where something unexpected visibly happens and is obviously hard**. PS-04 is
chart-rich and moment-poor. PS-17 is chart-poor and moment-rich, and moments are the scarcer
resource.

The National Finale inject is a **state transition** — the only thing in either brief that can
legitimately animate. Heer & Robertson (InfoVis 2007) found animated transitions significantly
improve both object tracking and value estimation over static ones, so animating the re-evaluation
is perceptually justified rather than decorative.

## The hero: the bitemporal as-of plane

Fowler's bitemporal diagram — **valid time on x, record time on y** — rendered as an interactive
plane with a draggable as-of crosshair.

Drag knowledge-time back to before the amendment and the verdicts **re-colour live**. One
interaction proves the data model, the provenance fan-out and audit replay simultaneously.

**Nobody ships this.** XTDB's TraderX demo is the closest published precedent and it ships only
**1-D** sliders, explicitly noting a 2-D bitemporal view was not attempted. No shipping CLM
product puts this in front of a user.

*Build cost: ~2.5 days, bespoke `visx`, no library does it for you. This is the day-4 gate.*

## The sharpest unclaimed visual: the conclusion diff

Every CLM vendor redlines **documents** — Sirion, Icertis, Ironclad, Juro all compare clause text
and track who changed what. **Nobody diffs the derived conclusions.**

A ledger that animates, beside the clause redline:

```
14 events re-evaluated  ·  4 breaches vacated  ·  2 breaches created  ·  ₹X leakage swing
```

— with **one row locked grey** because the notice already fired and the idempotency guard refuses
to re-send. That grey row is the "oh no" beat of the entire pitch, and it is the most
patentable-looking artefact in the build.

Steal the layout from **Redux DevTools**: scrubber on top, diff pane below.

## Screen inventory

| # | Screen | What it proves |
| --- | --- | --- |
| 1 | **Portfolio / leakage worklist** | Prioritised by time-to-forfeiture × value × confidence — the Entitlement Clock, not severity |
| 2 | **Contract → obligation tree** with span-anchored highlights | Provenance: click any extracted term, land on the character offsets in the source PDF |
| 3 | **The bitemporal as-of plane** ★ hero | The data model, live |
| 4 | **The conclusion diff / re-adjudication ledger** ★ | The inject, answered |
| 5 | **Evidence reconciliation** — three coloured bars over one incident, red lock on the action button | `CONTESTED` state blocking automated action |
| 6 | **Action ledger + autonomy panel** | Idempotency, approval gates, the five-level matrix |
| 7 | **Trace / span waterfall** | Click a conclusion → the exact spans and source documents that produced it |

## Library call

- **ECharts** (Apache 2.0, canvas+SVG, heatmap/graph/custom series, timeline component) —
  everything conventional.
- **`visx`** — the two bespoke heroes only.
- **Cytoscape.js** — the provenance graph.
- **Hand-rolled divs** — the span waterfall.

**Named traps:** vis-timeline, Perfetto embedding, Sigma.js/WebGL, React Flow, raw D3.

Everything recommended is a client-side npm package, so the whole frontend runs on bare Windows
with no Docker. The things that would break that constraint — Perfetto's `trace_processor`,
Grafana Pyroscope — are exactly the ones on the trap list.

## The five-minute storyboard

| Time | What the jury sees |
| --- | --- |
| 0:00 | Worklist. "₹X at risk, sorted by days to forfeiture — not by severity. The money is lost when the claim window closes, not when the SLA breaks." |
| 0:45 | Click a breach → obligation tree → click the threshold → **lands on the highlighted span in the signed PDF**. "Every number is either a quoted span or a model's guess, and you can always tell which." |
| 1:30 | Evidence reconciliation. Three sources, three durations, one incident. Action button locked red. "The systems don't disagree by being empty. They disagree by being confidently different." |
| 2:15 | **The amendment lands.** Drop the amendment into the workspace. |
| 2:30 | **The conclusion diff animates.** 14 re-evaluated, 4 vacated, 2 created. Then: the grey row. "This notice already went out. We can't un-send it. So the system doesn't try — it quarantines it for a human." ← *the beat* |
| 3:30 | Drag the as-of crosshair back before the amendment. Verdicts re-colour. "This is what we believed on 12 July. Reproducible, not reconstructed." |
| 4:00 | **Kill the worker mid-action.** Restart. No double-send. "Idempotency is invisible, which is why nobody builds it." |
| 4:30 | Autonomy panel + trace waterfall. "Five levels. The brief says legal interpretation, notice and settlement stay human. Here's where the machine stops." |

## The day-4 gate and the fallback

**Gate: the bitemporal plane must render real data by end of day 4.**

PS-17 costs ~11.5–14 frontend-days against PS-04's ~9–11, and nearly all the risk concentrates in
that one bespoke screen. If it is not working:

**Fallback** — ship the conclusion diff (screen 4) as the hero and demote the plane to a 1-D
knowledge-time slider above the ledger. The diff is the more novel artefact anyway; the plane is
the more impressive one. Losing the plane costs roughly 1.5 points on this lane and does not
change the overall recommendation. Losing *both* collapses PS-17 to a table plus a log line, which
loses — hence the gate.

With one frontend engineer rather than two, this lane narrows to roughly 7.0 vs 6.5.
