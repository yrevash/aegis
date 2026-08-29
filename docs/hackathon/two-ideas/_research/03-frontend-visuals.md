# Frontend, visuals and demonstrability — comparative research

> Lane: screens, signature visualisations, demo choreography, 7-day frontend feasibility.
> Scope note: this is a clean-room judgement on the two briefs alone.

---

## Executive answer

- **The team's prior is wrong. PS-17 is the more visually winnable problem, and it is not close.** Score: **PS-17 8.5/10, PS-04 6.0/10.**
- **PS-04's visuals are legible but generic.** Every artefact PS-04 naturally produces — portfolio heatmap, fan chart, driver waterfall, ranked watchlist — is a chart type the incumbents already ship (nCino Continuous Credit Monitoring, Abrigo Portfolio Risk, Moody's Early Warning System) [1][2][3][4] and that a CTO jury has seen many times. The driver waterfall in particular reads to a technical judge as "you called `shap.plots.waterfall()`" [5], because that is literally the default plot's shape.
- **Two of PS-04's headline visuals are actively contested in the literature.** The Bernanke Review of Bank of England forecasting (April 2024) recommended the Bank *stop* publishing fan charts and de-emphasise a central forecast in favour of alternative scenarios; the Bank accepted all recommendations [6][7][8]. Putting a fan chart on the hero slide of a 2026 finance demo is a mild own-goal in front of a finance-literate panel.
- **PS-04's core claim cannot be proved on screen.** "72% chance of breach in 60 days" on synthetic data is unfalsifiable inside five minutes. The only screen that answers "why should I believe that number" is a reliability diagram / backtest [9][10] — a deliberately unglamorous chart whose honest message is "we are approximately calibrated." That is the right engineering answer and a weak demo beat.
- **PS-17's supposed frontend weakness is its greatest visual strength.** The National Finale inject — an amendment changes an SLA threshold *after* breaches were flagged — is a **state transition**, and state transitions are the only thing in either problem statement that can legitimately *animate*. Heer & Robertson (InfoVis 2007) found animated transitions significantly improve both object tracking and value estimation over static transitions [11] — so animating the re-evaluation is perceptually justified, not decoration.
- **PS-17 has a signature visualisation nobody else will show: the bitemporal as-of plane.** Fowler's bitemporal diagram — *actual/valid time on the x-axis, record time on the y-axis* [12] — rendered as an interactive plane with a draggable as-of crosshair, is a visual object almost no hackathon team and no shipping CLM product puts in front of a user. XTDB's TraderX demo is the closest published precedent and it only ships **1-D** sliders, explicitly noting a 2-D bitemporal view was not attempted [13][14].
- **The sharpest unclaimed visual in either problem: the conclusion diff.** Every CLM vendor redlines *documents* — Sirion, Icertis, Ironclad, Juro all compare clause text and track who changed what [15][16][17]. **Nobody diffs the derived conclusions.** A ledger that animates "14 events re-evaluated · 4 breaches vacated · 2 breaches created · ₹X leakage swing" beside the clause redline is a genuinely novel screen, and it is the single most patentable-looking thing in this lane.
- **Feasibility is close but PS-17's hero is the riskier build.** PS-04's six screens are ~11 build-days of well-trodden charting. PS-17's six are ~14 build-days, of which the bitemporal plane is a bespoke `visx` build (~2.5 days) with no library that does it for you. **This is the entire conditionality of the verdict** (see "What would change the verdict").
- **Library call:** ECharts (Apache 2.0, canvas+SVG, has heatmap/graph/custom series and a timeline component) for everything conventional; `visx` for the two bespoke heroes only; Cytoscape.js for the provenance graph; hand-rolled divs for the span waterfall. Named traps: vis-timeline, Perfetto embedding, Sigma.js/WebGL, React Flow, raw D3.
- **All of this runs on bare Windows with no Docker** — every recommendation is a client-side npm package. The things that would break that constraint (Perfetto's trace_processor, Jaeger, Grafana Pyroscope) are exactly the ones listed as traps.

---

## PS-17: Contract Obligation, SLA & Commercial Leakage Monitor — frontend analysis

### 1. The screen inventory

Six screens. One of them is the whole pitch.

**S1 — Obligation Register (entry, not hero).**
A dense table over the extracted obligation set: contract → clause → obligation → owner → SLA target → current evidence status. Each row carries a **provenance chip strip** — small typed badges distinguishing *recorded fact* / *AI inference* / *user input* / *automated action* / *human decision*, which the brief demands as an explicit separation (§04). Filters on owner, deadline, breach status. What the user does: triage. What it proves: extraction actually happened at volume, and the system never lets an inference masquerade as a fact. Keep this screen deliberately plain — it is the "we did the boring part properly" screen, and its job is to be over in 30 seconds.

**S2 — The As-Of Workspace (HERO — this goes on the title slide).**
Left two-thirds: the **bitemporal plane** (detailed in §2 below) — a 2-D chart with valid/effective time on x, record/system time on y, shaded regions for each version of the SLA threshold, and a draggable crosshair. Right third: the *conclusions as of that coordinate* — the live breach list, the leakage total, the open notices. Dragging the crosshair down the record-time axis rewinds not the data but **the system's knowledge**. The demo line is: *"This is not a date filter. This is what we knew, when we knew it."*
What it proves to a jury: that the durable domain model in §04 of the brief is real, that "represent late, corrected or conflicting versions without losing earlier evidence" was implemented rather than asserted, and that the team understands the difference between valid time and transaction time [12][18] — which is a genuine backend-depth signal made visible.

**S3 — Evidence & Provenance Graph (single breach).**
A directed graph in the W3C PROV shape — *Entity / Activity / Agent* [19] — for one flagged breach: the clause node, the SLA records, the service events, the invoice, the credit, the extraction activity, the human who approved. Node shape encodes PROV type; edge labels are `wasDerivedFrom` / `wasGeneratedBy` / `wasAttributedTo`. Click any node to see the raw source snippet.
What the user does: answers "on what basis?" in one click. What it proves: provenance is a data structure, not a log file. Note that PROV-O-Viz renders PROV graphs as Sankey diagrams [19] — resist that; for ~40–150 nodes a laid-out DAG reads far better than a Sankey, which implies flow volume you do not have.

**S4 — Amendment Impact / Re-evaluation Diff (the inject screen).**
Split view. **Left:** the clause redline — old SLA threshold struck through, new one inserted, with effective-from date. This is the familiar half, and it is familiar on purpose: it is exactly what Sirion/Icertis/Ironclad show [15][16][17]. **Right:** the **conclusion diff ledger** — the unfamiliar half. Rows animate in as the re-evaluation runs: `EVT-0412 breach → not-a-breach (vacated)`, `EVT-0517 ok → breach (new)`, with a running counter and a leakage delta. A "blocked" lane shows actions the idempotency guard refused to re-fire (the notice that was already sent must not be sent twice — brief §04 explicitly requires "preventing duplicate requests, duplicate transactions or repeated external actions").
This screen is the pitch's climax. See §3.

**S5 — Reasoning Trace (agent-loop span waterfall).**
An OpenTelemetry-shaped waterfall: one horizontal bar per span, nested by parent, coloured by span kind (retrieval / LLM call / tool call / policy check / human gate). Selecting a re-evaluated event jumps straight to the trace for *that* re-evaluation. Failed spans render red with the retry chain visible; a partially-succeeded workflow shows the successful half committed and the failed half queued — which is the brief's "continue safely through partial failures" made visual.
Precedent: Honeycomb's trace waterfall and the pattern of pivoting from an outlier straight into the trace that explains it [20][21]; SigNoz and the OTel ecosystem render the same shape from OTLP JSON [22][23].

**S6 — Action & Approval Queue.**
Prepared-but-unsent actions: draft service-credit claim, draft breach notice, draft renewal reminder. Each carries an **autonomy-level badge** (L1 observe → L5 act) and a named human owner. The demo point is what the system *refuses* to do: legal interpretation, contractual notice and material settlement stay human-owned per the brief. A "why is this gated" popover shows the policy rule that gated it.

### 2. The signature visualisation — the bitemporal as-of plane

**The idea.** Take Fowler's bitemporal diagram literally and make it interactive. Fowler's figure puts *actual time* on the x-axis and *record time* on the y-axis, with shaded bands showing what the value was believed to be in each region of that plane [12]. He frames the point exactly as PS-17 frames it: bitemporal history exists because "communication is neither perfect nor instantaneous," and because actions get taken "based on a past state that's retroactively changed" [12]. That is the inject, described by Martin Fowler, three years before this hackathon.

**What makes it striking rather than a dashboard.** Three things:

1. **It is 2-D, and everyone else's time UI is 1-D.** A date picker is a point. A timeline is a line. This is a *plane*, and the second axis is the one that carries the audit story. XTDB — a database whose entire pitch is bitemporality — shipped only 1-D sliders in its published demo UI, and the write-up is explicit that the sliders are `_valid_from` markers, not a two-axis view [13][14]. If the state of the art in a bitemporal *database vendor's own demo* is a 1-D slider, a working 2-D plane is a differentiated artefact.
2. **The amendment is visible as geometry.** Before the amendment, the plane is one shaded region. The amendment adds a new **horizontal band** (a new record-time stratum) that overrides part of the past *in valid time but not in record time*. The jury sees an L-shaped, non-rectangular region appear. That shape *is* the retroactive correction. No prose required.
3. **Two draggable handles produce four demonstrably different answers** from the same database: (valid=now, record=now) = today's truth; (valid=3 weeks ago, record=now) = what we now believe was true then; (valid=3 weeks ago, record=3 weeks ago) = what we believed then, which is what the notice we already sent was based on; (valid=now, record=3 weeks ago) = the stale conclusion the old system would still be showing. The fourth quadrant is the money shot — it is a live demonstration of the failure mode the brief calls out: "silently preserving an outdated conclusion."

**Runners-up, ranked.** The provenance graph (S3) is good but is table stakes — it is the screen every team building anything "evidence-backed" will attempt. The clause-to-evidence linkage view collapses naturally into S3, don't build it twice. The amendment diff (S4) is *co-equal* with the plane, not a runner-up: build both, they are the two halves of one argument.

**Implementation shape.** Rectangles in a 2-D cartesian space with two linked brushes. `visx` gives you `scaleTime`, `Group`, `Bar`, `Brush` and axes with no chart-shaped opinions imposed [24][25] — this is precisely the "custom visualisation system" case where its low-level primitives pay for themselves. Do **not** try to bend a charting library into it.

### 3. The "time-travel" question — is re-evaluation dramatic or a log line?

**Answer: it is dramatic, and the pattern to steal is Redux DevTools, not a diff viewer.**

The relevant prior art:

- **Redux DevTools** gives the canonical grammar: an *action timeline* (every state-changing event, chronologically, with payload and computed state diff), a *scrubber* at the bottom that instantly recomputes the UI as you drag, and per-action *state diffing* — plus the ability to disable an action from the timeline and watch everything downstream recompute [26][27]. That last affordance is exactly the amendment inject: toggle the amendment off and on, and the breach list recomputes in front of the jury. **Steal the scrubber-plus-diff-pane layout wholesale.**
- **Replay.io** contributes the more radical idea: retroactively add a print statement to an execution that already happened, and the console fills in "as if it's always been there" [28][29]. The PS-17 analogue is *retroactively adding an obligation rule and watching the historical breach set repopulate*. That is a stretch goal, but it is a 20-second demo beat with enormous impact if it works.
- **Event sourcing / Retroactive Event.** Fowler's Retroactive Event pattern is the backend that licenses the frontend: identify a **branch point** where the recorded past diverges from what should have happened, then either *rebuild* (revert to a snapshot before the branch point and replay forward) or *rewind* (reverse events backward to the branch point), then replay with the correction applied [30]. Rejected events are "marked to ignore on replay, maintaining history while preventing reprocessing" [30]. **Every one of those nouns is a UI element**: the branch point is a marker on the record-time axis; rebuild-vs-rewind is a visible strategy label; the marked-ignored events are struck-through rows that stay visible. Fowler notes the pattern demands substantial architecture and is "unsuitable for most systems" [30] — which is the point: implementing it is the backend-depth claim, and showing it is the frontend claim, and they are the same screen.

**The choreography that makes it land (the 40 seconds that win the hackathon):**

1. Breach list sits at 18 flagged, ₹ leakage total visible. Freeze one second so the numbers register.
2. Amendment drops (drag a PDF onto the workspace — physical, visible, obviously not pre-baked).
3. A **branch-point marker** slides onto the record-time axis of the plane. Pause here. Say the word "branch point."
4. Affected events **highlight and re-queue** — a visible working set, not a spinner. The count is on screen: "14 events require re-evaluation."
5. Rows resolve one at a time, ~120 ms apart, each with a staged transition: colour change → position change. Staged rather than simultaneous, per Heer & Robertson, who found animated transitions significantly outperform static ones for object tracking and value estimation and that viewers prefer staged animation [11]. Four rows flip green (vacated), two flip red (newly breaching).
6. Counters settle. The headline number changes on screen — from 18 flagged to 16, and the leakage total swings.
7. One row stays grey with a lock icon: the notice already dispatched. Hover: "action already performed — idempotency key `NTC-0412` — cannot be re-fired; escalated for human review."

Step 7 is what a CTO panel will remember. The animation is the theatre; the row that *refuses* to animate is the engineering.

**Verdict on the sub-question: the belief that PS-17 has "caveats in the front end" is exactly backwards.** PS-17's frontend problem was misdiagnosed as "contracts are boring text, there's nothing to draw." The actual content of PS-17 is *state under retroactive change*, which is one of the most visually rich subjects in software — it is the entire premise of the time-travel debugging genre, an entire category of developer tools that exists because this material is compelling to look at.

### 4. Difficulty and 7-day feasibility

| Screen | Build cost | Library | Risk |
| --- | --- | --- | --- |
| S1 Obligation Register | 1.0 d | TanStack Table + Tailwind | Low |
| **S2 Bitemporal plane** | **2.5 d** | **`visx` (scales, Group, Bar, Brush)** | **High — the one real risk** |
| S3 Provenance graph | 1.5 d | **Cytoscape.js** (`dagre`/`breadthfirst` layout) | Medium |
| **S4 Amendment diff ledger** | **2.0 d** | Recharts/none + `framer-motion` for row transitions; `react-diff-viewer` or Monaco diff for the clause half | Medium |
| S5 Span waterfall | 1.0 d | **Hand-rolled** absolutely-positioned divs over OTLP JSON | Low |
| S6 Approval queue | 1.0 d | Plain components | Low |
| Shell, routing, light theme, polish | 2.5 d | — | — |
| **Total** | **~11.5 d** | | |

For a small team over 7 days that is 2 frontend people or 1.5 with help. **Tight but shippable — provided S2 is started on day 1, not day 4.**

**Library calls, with reasons:**

- **`visx` — for S2 only.** ~15 KB, Airbnb, low-level primitives (scales, axes, shapes, groups) rather than finished charts; it is what custom trading dashboards and bespoke interactive charts are built on [24][25]. The documented cost is real: teams without D3 experience take roughly 2–3× longer to build their first chart than with Recharts [25]. **Mitigation: use it for one screen, and have the person with the most D3 exposure own that screen.**
- **Cytoscape.js — for S3.** The richest all-in-one graph toolkit; correct when layouts and graph algorithms are part of the product rather than decoration [31][32]. At ~40–150 provenance nodes, its layouts and hit-testing are the value; rendering performance is a non-issue.
- **Apache ECharts — for anything conventional.** Apache Software Foundation project, canvas *and* SVG rendering, ships heatmap / graph / treemap / candlestick / boxplot / custom series, a timeline component and a theme builder [33]. Bundle is ~100–300 KB gz depending on modules imported [25] — import modules, not the barrel.
- **Recharts** for small supporting charts where composition speed beats control [25].
- **Hand-rolled span waterfall.** An OTLP trace waterfall is nested bars positioned by `(start - traceStart)/duration`. It is a day. Do not import an observability platform to draw a bar chart.

**Named traps — do not touch these:**

- **vis-timeline.** It is a scheduling/Gantt component; its data model is items-on-tracks with drag-to-edit, and its semantics fight a bitemporal model rather than expressing it. It is the right answer for "generic interactive project timeline" [34] and the wrong answer for this.
- **ECharts as the timeline engine.** Timelines are not its primary use case: limited interactivity (no drag-to-edit) and SVG rendering caps scale; it is recommended only when the timeline is one panel of a multi-chart dashboard and you want stylistic consistency [34]. Fine for S1's sparklines, wrong for S2.
- **Perfetto.** Perfetto renders method calls as flame graphs and can ingest external trace formats [35], but it is a full trace-processor application, not an embeddable React component. Embedding it is a multi-day rabbit hole and it drags a native binary onto a Windows demo machine. Take the *visual grammar*, write the 200 lines.
- **Sigma.js / WebGL graph renderers.** Sigma handles 100 K+ nodes via WebGL [31][32], which is a real strength and completely irrelevant here. You have 150 nodes. WebGL buys you nothing and costs you label rendering and hit-testing.
- **React Flow.** Excellent for node-based editors and low-code workflow builders, weaker for graph *exploration* and analytics [31]. The provenance graph is read-only exploration. Wrong tool, and it will tempt the team into building an editor nobody asked for.
- **Raw D3.** In a 7-day window with React, D3's imperative DOM ownership fights React's. Use `visx`, which is D3's maths without D3's DOM.

**Light/white theme discipline.** Define one explicit token palette; do not rely on a library's defaults. The bitemporal plane needs a **sequential** ramp for record-time strata (not categorical) so that "later knowledge" reads as monotone — and the four state colours in the diff ledger (vacated / new / unchanged / blocked) must be distinguishable without relying on red-green alone. ECharts' theme builder handles the conventional charts [33]; the `visx` screens are hand-tokened anyway.

### 5. The 5-minute demo storyboard

| Time | Screen | What the jury sees | What it proves |
| --- | --- | --- | --- |
| 0:00–0:30 | S1 | Register: 240 obligations across 12 contracts, 18 flagged, ₹ leakage headline. Provenance chips visibly typed. | Volume is real; fact ≠ inference. |
| 0:30–1:15 | S3 | Drill into one breach → provenance graph blooms. Click the invoice node → raw source snippet. | Evidence is a structure, not a citation string. |
| 1:15–2:00 | S2 | Drag the record-time crosshair back three weeks. The breach **disappears**. "This is not a filter. We did not know yet." Drag valid-time back with record-time at now — it reappears. | Bitemporality implemented, not claimed. |
| **2:00–2:50** | **S4** | **Drop the amendment PDF. Branch-point marker lands. 14 events re-queue. Rows flip one by one: 4 vacated, 2 created. Headline moves 18 → 16. One row locks grey: notice already sent.** | **The inject, answered — with the idempotency guard visible.** |
| 2:50–3:30 | S5 | Click a vacated breach → span waterfall of *its* re-evaluation. A red span with a retry chain, and the partial-failure recovery path. | Per-loop observability; safe partial failure. |
| 3:30–4:20 | S6 | Prepared credit claim and draft notice, each with an autonomy badge and a named human owner. The notice will not send. "Why is this gated" → the policy rule. | Human-owned decisions are enforced, not promised. |
| 4:20–5:00 | S2 | Return to the plane. Scrub the crosshair back and forth across the amendment boundary. The conclusions breathe. Close on the L-shaped region. | One image the panel carries out of the room. |

Note the structure: the demo **opens and closes on the same screen**, and that screen is the one on the title slide. The middle is evidence for the claim the hero screen makes.

---

## PS-04: AI-Powered Dynamic Covenant Monitoring & Early Warning — frontend analysis

### 1. The screen inventory

**S1 — Portfolio Triage (entry).**
120 synthetic borrowers, ranked by urgency × exposure. Either a sortable table with inline sparklines, or a heatmap (borrower × covenant, cell = headroom or breach probability). Filters by industry, facility type, RM owner. What it proves: the system operates at portfolio scale rather than on one cherry-picked borrower.
**Candour required:** this screen is a commodity. It is what nCino's Continuous Credit Monitoring pitches as "a single, intuitive dashboard" giving "a deeper understanding of a relationship's credit health," and what Abrigo ships as concentrations/watch-list reporting [1][2][3][4]. A jury of CTOs will not score originality for it. Build it in half a day and move on.

**S2 — Borrower Deterioration Trajectory (HERO candidate).**
One borrower, one covenant. X = time, ~180 days back and 90 forward. A solid line for the realised ratio history, then a **forecast band** past today, and a horizontal **covenant threshold line**. The visual event is where the band crosses the line. Annotate the crossing point: "median crossing day 52; 30-day P(breach) 0.14, 60-day 0.51, 90-day 0.72."
This is the strongest visual PS-04 has, and it is a good one — the crossing point is a genuinely legible moment. Two problems (see §2).

**S3 — Driver Attribution.**
A waterfall from base rate to this borrower's probability, one bar per driver (utilisation ramp, payment lateness, treasury outflow, industry stress, concentration), plus a strip of small-multiple sparklines for the raw signals so the attribution can be checked against the data. This is the SHAP local-explanation shape: start at the expected value, each row shows the positive/negative contribution moving toward the model output, ordered by absolute contribution [5][36].

**S4 — Horizon Comparison (30/60/90).**
Three panels, one per horizon. The strong version replaces bare percentages with **quantile dotplots** — see §2.

**S5 — Calibration / Backtest.**
Reliability diagram: predicted probability binned on x, observed frequency on y, diagonal reference. Above the diagonal = under-forecast, below = over-forecast [9][10]. Plus a retrospective: "60 days ago the model said X for these 12 borrowers; 7 breached."
This screen is what separates a serious PS-04 build from a plausible one, and every team that skips it will be asked about it in Q&A.

**S6 — Intervention & Warning Trail.**
Recommended action per borrower (covenant waiver discussion / collateral review / RM outreach / escalate to workout), with the auditable trail the brief requires: the data, the trends, the calculations, the reasoning, timestamped.

### 2. The signature visualisation — and why it is harder to make striking

**The best candidate is S2's threshold crossing, not the heatmap and not the waterfall.** The heatmap is commodity [1][2][3][4]. The waterfall is recognisable as a library default [5][36] — a technical judge sees `shap.plots.waterfall()`, and that reads as integration rather than invention. The crossing point is the only PS-04 visual with an *event* in it.

**Problem one: the fan chart is contested prior art.** The Bernanke Review of Bank of England forecasting (April 2024) recommended the Bank de-emphasise its central forecast and end the practice of showing a fan of probabilities around a most likely outturn, in favour of alternative scenarios; the Bank accepted all recommendations [6][7][8]. Critiques on record include that fan widths only ever seemed to increase [7] and that the MPC was overestimating uncertainty [37]. Fan charts are not *bad* — they remain among the better ways to jointly convey expectation and uncertainty [7] — but "central bank publicly retired this" is a fair Q&A hit, and it is avoidable.

**Problem two — the deeper one: PS-04's uncertainty is unfalsifiable in the room.** A fan band on synthetic data is a picture of a claim. The jury cannot check it. PS-17's as-of scrubber, by contrast, is *self-verifying* — the judge drags a handle and the conclusion visibly changes, and the change is checkable against the amendment sitting right there on the left of the screen.

**The upgrade that makes PS-04's hero defensible: quantile dotplots.** Replace (or supplement) the continuous band with discrete outcome dots. Fernandes et al. (CHI 2018) found quantile dotplots with 50 outcomes produced decisions with **expected payoffs 97% of optimal (95% CI [95%, 98%])** — about 5 percentage points better than a no-uncertainty control — with lower within-subject variance (3 pp vs 4 pp higher for control); CDF plots performed nearly as well, and *textual* uncertainty underperformed and was sensitive to which interval was quoted [38][39]. The broader finding across the Hullman/Kay/Padilla line of work is that **frequency-framed** displays — quantile dotplots and hypothetical outcome plots — outperform other distributional visualisations [40][41][42].

So the strongest honest framing for PS-04's hero is: *"Do not show a risk officer '72%'. Show them 50 dots, 36 of which are on the wrong side of the covenant line."* That is a defensible, citable design decision and it is the single best thing PS-04's frontend can do. **Hypothetical outcome plots** (animated frames, each a draw from the predictive distribution [40][43]) are a tempting second — animating 30 possible borrower trajectories crossing the line is genuinely arresting — but they are a stretch: HOPs cost animation infrastructure, and an animated hero is fragile in a live demo.

**Implementation shape.** A quantile dotplot is `inverseCDF(evenly spaced p)` binned into stacked dots — a well-documented recipe with public Vega and Observable implementations to work from [44][45][46]. Roughly a day in `visx`, and worth it.

**The idea PS-04 should steal from PS-17.** Add a retrospective time slider to S5: rewind the portfolio to a past date, show what the model said *then*, then reveal what actually happened. That is PS-04's only genuine state-change moment, and it is a borrowed mechanic. Notably, this is PS-17's *native* material.

### 3. The "time-travel" question, applied to PS-04

PS-04 has no equivalent temporal drama. Its state changes are monotone — a probability drifts from 0.4 to 0.6 as data arrives. There is no branch point, no retroactive correction, no conclusion that flips. The one exception is the backtest rewind in S5, which is (a) retrospective rather than live, and (b) an admission screen rather than a triumph screen.

A team could manufacture drama by injecting a shock signal mid-demo — a bad news event, a sudden utilisation spike — and watching the forecast jump. That works, but it is *a number changing*, and a number changing is a fundamentally weaker visual event than *a conclusion inverting while its audit trail stays intact*.

### 4. Difficulty and 7-day feasibility

| Screen | Build cost | Library | Risk |
| --- | --- | --- | --- |
| S1 Portfolio triage | 0.5 d | ECharts `heatmap` + TanStack Table | Low |
| S2 Trajectory + band + threshold | 1.5 d | ECharts (line + `custom` band) or Recharts `Area` | Low |
| S2b Quantile dotplot upgrade | 1.0 d | `visx` (recipe from Vega/Observable [44][45][46]) | Medium |
| S3 Driver waterfall + sparklines | 1.0 d | ECharts (stacked-bar waterfall) | Low |
| S4 Horizon comparison | 0.5 d | reuse S2b | Low |
| S5 Calibration + backtest | 1.0 d | Recharts scatter + reference line | Low |
| S6 Intervention & trail | 1.0 d | Plain components | Low |
| Shell, routing, light theme, polish | 2.5 d | — | — |
| **Total** | **~9 d** | | |

**PS-04 is meaningfully cheaper and lower-risk to build — ~9 days versus ~11.5, with no single high-risk screen.** This is a real advantage and the honest counterweight to everything above. If the team's frontend capacity is one person, that gap matters more than any argument about distinctiveness.

**Library calls.** ECharts does almost all of this natively — heatmap, line, custom series for the confidence band, stacked bars for the waterfall, theme builder for the light palette [33]. `visx` only for the dotplot. **No graph library needed** — which is also a tell: PS-04 has no relational structure worth drawing, and relational structure is what makes a screen look like engineering.

**Trap specific to PS-04:** the temptation to add more charts. Six good screens beat twelve. A wall of KPI cards is the single most common way this problem statement gets built, and it is exactly what the incumbents already ship [1][2][3][4].

### 5. The 5-minute demo storyboard

| Time | Screen | What the jury sees | What it proves |
| --- | --- | --- | --- |
| 0:00–0:40 | S1 | 120 borrowers, heatmap ranked by urgency × exposure. Top row flashing amber. | Portfolio scale. |
| 0:40–1:40 | S2 | One borrower. History line, forecast band, covenant threshold line, crossing at ~day 52. Annotated. | The core capability. |
| 1:40–2:30 | S3 | Waterfall from base rate to 0.72; small-multiple sparklines confirm utilisation ramp and treasury outflow. | Drivers are attributed, not asserted. |
| 2:30–3:10 | S4 | 30/60/90 toggle, quantile dotplots: 7 dots, 25 dots, 36 dots on the wrong side of the line. | Uncertainty communicated in a form people act on correctly [38][39]. |
| 3:10–3:50 | S5 | Reliability diagram near the diagonal; retrospective: "60 days ago we flagged 12; 7 breached." | Why the number should be believed. |
| 3:50–4:40 | S6 | Recommended intervention, prioritised queue, full warning trail. | Actionability + audit. |
| 4:40–5:00 | S1 | Back to portfolio. Aggregate exposure at risk. | Business impact. |

**The structural weakness is visible in the table.** Minutes 0:40 through 3:50 are *four different chart types in sequence*. The judge is being shown things, not shown something happening. There is no verb in this storyboard, and no moment where an interaction changes a conclusion. Compare PS-17's 2:00–2:50 row.

---

## Head-to-head verdict for this lane

### Scores

| | PS-17 | PS-04 |
| --- | --- | --- |
| **Hero screen distinctiveness** | 9 — bitemporal plane; no incumbent and few teams will show a 2-D time UI [13][14] | 5 — threshold crossing is good but familiar; the surrounding screens are commodity [1][2][3][4] |
| **Is there a *moment*?** | 10 — the amendment ripple is a genuine dramatic beat with perceptual justification [11] | 4 — a number moves |
| **Backend legibility (does the UI make hard engineering visible?)** | 9 — bitemporality, idempotency, partial-failure recovery all become pixels | 6 — the model's difficulty stays inside the model |
| **Self-verifiability in the room** | 9 — the judge drags a handle and checks the result against the amendment | 4 — synthetic-data probabilities cannot be checked live |
| **Build risk / 7-day feasibility** | 6 — ~11.5 d, one genuinely hard screen | 8 — ~9 d, nothing above medium risk |
| **Uniqueness vs. what other teams will build** | 9 | 4 |
| **Overall (visual winnability)** | **8.5** | **6.0** |

### Winner: PS-17, by ~2.5 points on a 10-point scale.

**The team's prior — "PS-04 has the stronger visual story, PS-17 has caveats in the front end" — does not survive scrutiny. It is inverted.**

The prior was formed, I think, by comparing *raw material*: covenant monitoring comes with numbers and numbers come with charts, whereas contracts come with text and text does not draw. That comparison is real but it evaluates the wrong thing. What wins a 5-minute CTO demo is not chart density; it is **one legible moment in which something the jury did not expect visibly happens, and the thing that happens is obviously hard.** PS-04's material is chart-rich and moment-poor. PS-17's material is chart-poor and moment-rich — and moments are the scarcer resource.

The second inversion: PS-04's abundance of natural charts is a *liability* on the uniqueness and originality axes, because those same charts are already shipping in nCino, Abrigo and Moody's products [1][2][3][4] and every rival team will independently arrive at the same heatmap-plus-waterfall layout. PS-17's frontend is harder to imagine, which is exactly why fewer competitors will imagine it.

The third inversion: the National Finale inject, which reads on paper as PS-17's hardest *backend* requirement, is in fact its greatest *frontend* gift. It is a scripted, guaranteed, judge-visible state transition placed in the demo by the organisers. PS-04's brief contains no equivalent gift.

### What would change the verdict

1. **If the bitemporal plane is not working by end of day 4, PS-17 loses this lane outright.** Without it, PS-17 degrades to a table, a graph and a log line — and PS-04's chart suite beats that comfortably. **This is a hard go/no-go gate, and it should be written into the plan as one.** Mitigation: build a static version of the plane on day 1 with hardcoded rectangles, before the backend can feed it; the interactivity can land on day 5.
2. **If frontend capacity is one person rather than two.** The 2.5-day cost gap plus the concentration of risk in a single bespoke screen makes PS-17's plan brittle at that staffing level. At one frontend engineer, PS-04's lower-risk 9 days is the defensible choice and the verdict narrows to roughly 7.0 vs 6.5.
3. **If PS-04 commits fully to the uncertainty-visualisation literature** — quantile dotplots over bare percentages [38][39][40], a real calibration screen [9][10], and a retrospective time slider — it climbs to ~7.5. That is a genuinely differentiated PS-04 and it is achievable in the window. It still loses, because it is a better version of a screen the jury recognises, versus a screen the jury does not.
4. **If a rival team also builds a time-travel UI for PS-17.** Possible but unlikely — the pattern lives in developer tooling [26][28][30], not in enterprise-contract software, and the cross-over is not obvious. If it happens, PS-17's edge collapses to execution quality.
5. **If the panel skews quantitative-finance rather than software.** A panel of credit-risk quants may score PS-04's forecasting substance above PS-17's temporal mechanics regardless of the visuals. The brief says CTOs, which favours PS-17.

---

## Risks and open questions

- **The bitemporal plane can read as confusing rather than clever.** Two time axes is a genuinely hard idea and it is being explained in ~45 seconds. **Mitigation: never show both axes moving at once.** Lock record-time, move valid-time, narrate. Then lock valid-time, move record-time, narrate. Only then move both. Also label the axes in domain language — "when it was true" / "when we knew it" — not "valid time" / "transaction time."
- **Animation is fragile live.** A 6-second ripple animation that stutters on the demo machine damages more than it gains. **Mitigation:** cap the animation at 6 seconds, make every animation interruptible-and-idempotent (end state must be correct if animation is skipped), and pre-warm the browser. Record a fallback screen capture.
- **The provenance graph will look like a hairball if it is allowed to grow.** Cap displayed nodes at ~40 for the demo path with progressive expansion; do not render the full corpus graph as a hero.
- **PS-04's calibration screen is a double-edged sword.** It builds credibility and it invites "how did you validate this on synthetic data?" There is no fully satisfying answer. Prepare the honest one — generate the synthetic corpus from a known generative process, backtest against it, and state the limitation before the jury does.
- **`visx` learning-curve risk is documented and real** — 2–3× first-chart time for teams without D3 experience [25]. If nobody on the team has D3 exposure, the ~2.5-day estimate for the bitemporal plane should be read as ~4 days, and risk 1 above becomes acute.
- **Open question I could not resolve:** I found no published example of a shipping *product* with a 2-D interactive bitemporal chart — only Fowler's static diagram [12] and XTDB's 1-D sliders [13][14]. This is encouraging for uniqueness and it is also a warning: there may be a usability reason the pattern is rare that I have not found. **Recommend a 2-hour paper-prototype test on a non-team member before committing day 1 to it.** `[UNVERIFIED — I could not find a usability study of 2-D bitemporal interfaces either supporting or refuting the pattern.]`
- **Icertis Vera Obligations' actual UI could not be verified directly** — the product page returned a login wall on fetch [16]. Claims about its dashboard content rest on secondary summaries and comparison sites [15][16][17]; treat "Icertis ships KPI dashboards, not conclusion diffs" as well-supported in direction but not first-party-verified.
- **Bloomberg DRSK screen layout** is described in vendor and academic material as displaying model inputs on-screen with override-able fields for scenario/sensitivity analysis [47][48] — the *override-and-see-what-changes* interaction is worth stealing for either problem statement, but I could not obtain a screenshot to verify the exact layout. `[Partially verified — behaviour documented, layout not seen.]`

---

## Sources

Fetched and read in full:

1. Apache ECharts — Features. https://echarts.apache.org/en/feature.html
2. Martin Fowler — *Bitemporal History*. https://martinfowler.com/articles/bitemporal-history.html
3. Martin Fowler — *Retroactive Event*. https://martinfowler.com/eaaDev/RetroactiveEvent.html
4. JUXT — *Bitemporal TraderX — XTDB Reflections (Part 2)*. https://www.juxt.pro/blog/bitemporal-traderx-part2/
5. UW Interactive Data Lab — *Uncertainty Displays Using Quantile Dotplots or CDFs Improve Transit Decision-Making* (Fernandes, Walls, Munson, Hullman, Kay; CHI 2018), abstract & findings page. https://idl.uw.edu/papers/uncertainty-bus

Consulted via search-result summaries (URLs verified as returned by search; page content not independently fetched unless noted above):

6. nCino — Continuous Credit Monitoring. https://www.ncino.com/continuous-credit-monitoring
7. nCino — Portfolio Analytics. https://www.ncino.com/solutions/portfolio-analytics
8. nCino — Credit Portfolio Management. https://www.ncino.com/credit-portfolio-management
9. Abrigo — Portfolio Risk Management. https://www.abrigo.com/software/portfolio-risk-cecl/
10. Moody's — AI-Powered Loan Monitoring / Lending Suite. https://www.moodys.com/web/en/us/solutions/lending/loan-monitoring.html
11. Moody's Analytics — CreditLens. https://www.moodysanalytics.com/product-list/creditlens
12. SHAP documentation — waterfall plot. https://shap.readthedocs.io/en/latest/example_notebooks/api_examples/plots/waterfall.html
13. Aidan Cooper — *A Non-Technical Guide to Interpreting SHAP Analyses*. https://www.aidancooper.co.uk/a-non-technical-guide-to-interpreting-shap-analyses/
14. MNI — *Bernanke Recommends BoE Axes Fan Charts* (12 Apr 2024). https://www.mnimarkets.com/articles/bernanke-recommends-boe-axes-fan-charts
15. CNBC — *Bernanke Review: Bank of England scraps fan charts in forecast overhaul* (12 Apr 2024). https://www.cnbc.com/2024/04/12/bernanke-review-bank-of-england-scraps-fan-charts-in-forecast-overhaul.html
16. Bank of England — *Forecasting for monetary policy making and communication at the Bank of England: a review* (the Bernanke Review). https://www.bankofengland.co.uk/independent-evaluation-office/forecasting-for-monetary-policy-making-and-communication-at-the-bank-of-england-a-review
17. Bank of England — Response to the review. https://www.bankofengland.co.uk/independent-evaluation-office/forecasting-for-monetary-policy-making-and-communication-at-the-bank-of-england-a-review/response-forecasting-for-monetary-policy-making-and-communication-at-the-bank-of-england-a-review
18. ScienceDirect — *Reactions to the Bernanke Review from Bank of England watchers*. https://www.sciencedirect.com/science/article/pii/S0169207025000342
19. University of Warwick press release — Wallis on BoE overestimating uncertainty. https://warwick.ac.uk/newsandevents/pressreleases/ne1000000083106/
20. PNAS — *Stable reliability diagrams for probabilistic classifiers*. https://www.pnas.org/doi/10.1073/pnas.2016191118
21. arXiv — *Evaluating probabilistic classifiers: Reliability diagrams and score decompositions revisited*. https://arxiv.org/pdf/2008.03033
22. Stanford Vis Group — Heer & Robertson, *Animated Transitions in Statistical Data Graphics* (InfoVis 2007). http://vis.stanford.edu/papers/animated-transitions
23. Heer & Robertson, PDF. https://idl.cs.washington.edu/files/2007-AnimatedTransitions-InfoVis.pdf
24. W3C — *PROV Model Primer*. https://www.w3.org/TR/prov-primer/
25. PROV-O-Viz — PROV Provenance Visualizer. http://provoviz.org/
26. Honeycomb Docs — Trace Waterfall. https://docs.honeycomb.io/reference/honeycomb-ui/query/trace-waterfall
27. Honeycomb — BubbleUp. https://www.honeycomb.io/platform/bubbleup
28. SigNoz — *Understanding Flame Graphs for Distributed Tracing*. https://signoz.io/blog/flamegraphs/
29. SigNoz — *OpenTelemetry Visualization: A Practical Guide*. https://signoz.io/blog/opentelemetry-visualization/
30. Perfetto Docs — Visualizing external trace formats. https://perfetto.dev/docs/getting-started/other-formats
31. LogRocket — *Redux DevTools: Tips and tricks for faster debugging*. https://blog.logrocket.com/redux-devtools-tips-tricks-for-faster-debugging/
32. Medium (The Web Tub) — *Time Travel in React Redux apps using the Redux DevTools*. https://medium.com/the-web-tub/time-travel-in-react-redux-apps-using-the-redux-devtools-5e94eba5e7c0
33. Replay.io blog — *Introduction to time travel debugging*. https://blog.replay.io/introduction-to-time-travel-debugging
34. Replay.io docs — Print statements. https://docs.replay.io/reference-guide/debugging/print-statements
35. Filip Hric — *Time-travelling with Replay.io*. https://filiphric.com/time-travelling-with-replayio
36. XTDB — *Time in XTDB*. https://docs.xtdb.com/about/time-in-xtdb.html
37. XTDB v1 docs — Bitemporality. https://v1-docs.xtdb.com/concepts/bitemporality/
38. JUXT — *The Value of Bitemporality*. https://www.juxt.pro/blog/value-of-bitemporality/
39. Wikipedia — Temporal database (valid time / transaction time, Snodgrass terminology, SQL:2011). https://en.wikipedia.org/wiki/Temporal_database
40. Sirion — *What is Contract Redlining Software?* https://www.sirion.ai/library/clm-platform/contract-redlining-software/
41. Icertis — *Contract Redlining Basics*. https://www.icertis.com/contracting-basics/what-is-contract-redlining/
42. Icertis — Vera Obligations product page. https://www.icertis.com/products/ai-applications/vera-obligations/ `[NOT VERIFIED — fetch returned an Optimizely login wall; claims about its UI rest on secondary sources 40, 41, 43]`
43. Summize — *Contract Redlining: The Process, Challenges & Tech*. https://www.summize.com/resources/contract-redlining
44. LogRocket — *Best React chart libraries in 2026*. https://blog.logrocket.com/best-react-chart-libraries-2026/
45. PkgPulse — *Recharts vs Chart.js vs Nivo vs visx: React Charting 2026*. https://www.pkgpulse.com/guides/recharts-vs-chartjs-vs-nivo-vs-visx-react-charting-2026
46. PkgPulse — *Cytoscape.js vs vis-network vs Sigma.js 2026: Graph Visualization Decision Guide*. https://www.pkgpulse.com/guides/cytoscape-vs-vis-network-vs-sigma-graph-visualization-2026
47. Cambridge Intelligence — *React Graph Visualization Guide: Libraries, Best Practices & Implementation*. https://cambridge-intelligence.com/blog/react-graph-visualization-library/
48. DEV Community — *Top 7 Timeline Visualization Components for Modern Web Apps in 2026*. https://dev.to/lenormor/top-7-timeline-visualization-components-for-modern-web-apps-in-2026-420l
49. Vega — Quantile Dot Plot example. https://vega.github.io/vega/examples/quantile-dot-plot/
50. Observable — *Quantile Dotplots* (Benito-Santos, after Kay). https://observablehq.com/@ale0xb/quantile-dotplots
51. GitHub — mjskay/when-ish-is-my-bus, quantile-dotplots.md. https://github.com/mjskay/when-ish-is-my-bus/blob/master/quantile-dotplots.md
52. Padilla, Kay & Hullman — *Uncertainty Visualization* (review chapter, PDF). http://space.ucmerced.edu/Downloads/publications/Uncertainty_Visualization_Padilla_Kay_Hullman_2022.pdf
53. Hullman et al. — *Hypothetical Outcome Plots Help Untrained Observers Judge Trends in Ambiguous Data* (PDF). https://users.eecs.northwestern.edu/~jhullman/hops_jobs_pfs.pdf
54. Bloomberg Professional Services — *Assessing and incorporating credit default risk analytics into investment analysis* (DRSK). https://www.bloomberg.com/professional/insights/risk/assessing-incorporating-credit-default-risk-analytics-investment-analysis/
55. SSRN — Bondioli, Goldberg, Hu, Li, Maalaoui, Stein, *The Bloomberg Corporate Default Risk Model (DRSK) for Public Firms*. https://papers.ssrn.com/sol3/papers.cfm?abstract_id=3911300

**Inline citation index used above:** [1] nCino CCM (src 6), [2] nCino Portfolio Analytics (src 7), [3] Abrigo (src 9), [4] Moody's EWS/Lending (src 10, 11), [5] SHAP waterfall (src 12), [6] MNI Bernanke (src 14), [7] ScienceDirect reactions (src 18), [8] CNBC / BoE review (src 15, 16, 17), [9] PNAS reliability diagrams (src 20), [10] arXiv reliability diagrams (src 21), [11] Heer & Robertson (src 22, 23), [12] Fowler bitemporal (src 2), [13] JUXT TraderX (src 4), [14] XTDB docs (src 36, 37), [15] Sirion redlining (src 40), [16] Icertis (src 41, 42), [17] Summize (src 43), [18] Snodgrass/temporal DB (src 39), [19] W3C PROV / PROV-O-Viz (src 24, 25), [20] Honeycomb waterfall (src 26), [21] Honeycomb BubbleUp (src 27), [22] SigNoz flame graphs (src 28), [23] SigNoz OTel viz (src 29), [24] visx via LogRocket (src 44), [25] PkgPulse charting (src 45), [26] LogRocket Redux DevTools (src 31), [27] Medium Redux time travel (src 32), [28] Replay.io intro (src 33), [29] Replay.io print statements (src 34, 35), [30] Fowler Retroactive Event (src 3), [31] Cambridge Intelligence graph libs (src 47), [32] PkgPulse graph libs (src 46), [33] Apache ECharts (src 1), [34] DEV timeline components (src 48), [35] Perfetto (src 30), [36] Aidan Cooper SHAP (src 13), [37] Warwick/Wallis (src 19), [38] Fernandes et al. CHI 2018 (src 5), [39] IDL abstract (src 5), [40] Padilla/Kay/Hullman review (src 52), [41] Hullman HOPs (src 53), [42] JUXT value of bitemporality (src 38), [43] HOPs PDF (src 53), [44] Vega quantile dot plot (src 49), [45] Observable quantile dotplots (src 50), [46] mjskay repo (src 51), [47] Bloomberg DRSK insights (src 54), [48] SSRN DRSK paper (src 55).
