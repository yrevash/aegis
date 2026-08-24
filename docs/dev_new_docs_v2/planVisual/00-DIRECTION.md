# Aegis — the wow pass: make the run visible, and stop the app shouting

## Context

Aegis is technically finished (backend 2112 · aegis 2388 · web 370, 37 compliance controls
enforced, two frameworks in full). The hackathon is **2026-08-30**. The remaining risk is not
technical — it is that nobody remembers it.

Two pieces of feedback drive this work:

1. **7–8 senior reviewers:** *"too much data, too complex, not good UX — the average user does
   not want this much overload."* This is about the **whole application**, not one screen.
2. **A previous winner:** *"you're good technically, you don't need to build more tech — you need
   visual wow factors that people remember after watching the presentation."*

The owner's own evidence agrees. At regional finals the two things that won were **live system
moments**: a real trading chart with real-time ML catching fraud, and a live graph + RAG visual.
Both were *the system working, visibly, in real time* — not branding, not decoration.

Independent research says the same: judges remember *"putting a model's decision on screen"*, they
*"associate visual clarity with product maturity"*, and the advice is to **optimise for 1–2
primary features** rather than spread effort. On the wait specifically: never leave the screen
static more than a couple of seconds, and a skeleton that mirrors the shape of the answer makes a
wait feel **30–40% shorter**.

### The finding that reframes the whole task

Three exploration agents audited the codebase. **The wow factors are already built and switched
off.** This is not a build problem, it is a wiring and restraint problem.

- A run is **40–60 s**. Roughly **one second of it is animated.** 25–45 s is static pixels.
  `console/stageTimeline.ts:5-16` already says it: *"Eleven of those twenty-nine seconds are the
  two guardrails, and the console spent them showing a spinner."*
- `console/FlowCanvas.tsx` (521 L, React Flow) **already animates the live 17-node agent graph** —
  active-node pip, animated edges while running. But `ChatConsole.tsx:638` calls `setView('run')`
  inside `send()`, so **asking a question actively switches away from it.** Verified directly.
- `beatFromSignal(run.lastSignal)` is computed on **every live turn** (`ChatConsole.tsx:178`) and
  passed **only** into `{settled && <ResultTabs beat={beat}/>}` — a component that mounts after the
  run ends. A real run emits **144 events**; every one of them is currently an invisible tick.
- `RunPreview.tsx` draws the honest path a question will take — and is **deleted the instant the
  question is sent** (`ChatConsole.tsx:840`).
- `.animate-scan` and `.animate-idle-breathe` exist in `globals.css` with **zero uses**.
- Framer Motion 13 is installed; only **10 `<motion.*>` elements** exist app-wide.
- `public/3d/{cube,slab,torus,sphere}.png` — **247 KB committed, zero references**, with a
  `manifest.json` that claims they are used on the landing hero and Jobs blocks.

And the density complaint is a **compliance gap with Aegis's own rules**, not a missing rule.
DESIGN.md §9 already forbids *"a paragraph where a chart, a badge or a tooltip would do"*; §4
already says an `Absence` is *"one line, not three."* Reality: **184 InfoTips / 3,385 words** (56
of them ≥25 words, worst is 95), **99 `Absence` boxes each rendering three `<p>`s**, 34 nav
tooltips averaging 28.5 words (one is **129 words inside a `title=` attribute**), and **23 of 35
screens draw no chart at all.** The repo already diagnosed it in
`docs/dev_new_docs_v2/frontend-redesign/03-AI-TEAM-PASS.md`: *"a text bomb with a lid on it."*

### Decisions taken by the owner

| | Decision |
|---|---|
| **Identity** | An **abstract signature mark**, not a creature. A mark that *does something* — state driven by the live run. DESIGN.md §7's "no characters" stands. |
| **Density** | **Calm it, don't remove it.** Every screen and number stays; enforce the existing rules. |
| **Wow scope** | **One rehearsed demo path made unforgettable**, everything else merely clean. |
| **Git** | **A new branch. Nothing on `main`.** |

---

## SCOPE OF THIS EXECUTION — documents only

**Approving this plan authorises writing and pushing the plan documents, and nothing else.**
No component is touched, no visual work begins. The owner reviews the documents first.

```
git checkout -b docs/wow-pass-plan     # nothing on main
```

Write, under `docs/dev_new_docs_v2/planVisual/`:

| File | Contents |
|---|---|
| `00-DIRECTION.md` | This document — the problem, the reframing, the decisions taken |
| `01-DEMO-PATH.md` | The second-by-second choreography, with file:line for every beat |
| `02-DENSITY-RULES.md` | The mechanical cut rules and the 7 demo-path screens |
| `03-SIGNATURE-MARK.md` | Mark states and the wire signals that drive each |
| `04-EVIDENCE.md` | The three audit reports verbatim — the measured basis for all of it |

Then: commit, push **the branch only**, and **stop every service** (frontend, backend, Superset,
Qdrant, Temporal — leaving Postgres/Redis/Neo4j, which are boot-started system services).

**Verification before the push** — the plan must be real, not plausible. Every file:line claim in
the documents gets re-checked against the working tree, and any that does not resolve is corrected
or removed. The claims that matter most, already verified during planning:

- `ChatConsole.tsx:638` `setView('run')` inside `send()` — **confirmed**
- `beat` reaching only `{settled && <ResultTabs beat={beat}/>}` (`:178`, `:234`) — **confirmed**
- `RunPreview` mounted only in the empty-console block (`:350`) — **confirmed**
- `Guardrail.layer` on the wire (`lib/stream.ts:172`) — **confirmed**
- `public/3d/*.png` 247 KB with zero `src/` references — **confirmed**
- 10 `<motion.*>` elements app-wide — **confirmed**

---

## Wave 1 — the switched-off wins (highest impact ÷ effort in the whole plan)

These are small, surgical, and unlock visuals that already exist and are already tested.

| # | Change | File | Effect |
|---|---|---|---|
| 1.1 | Do **not** force `setView('run')` on send — keep the live flow visible, or split the running layout so the flow spine sits above the run panel | `console/ChatConsole.tsx:638` | The marquee visual becomes visible for the 40 s it was built for |
| 1.2 | Pass `beat` into the **running** header and trust checks, not only `ResultTabs` | `console/ChatConsole.tsx:178,234` | 144 wire events become 144 visible ticks. `.animate-beat` already exists (`globals.css:438-450`) |
| 1.3 | **Promote `RunPreview` from an idle-only strip to the run's persistent scaffold**, lighting each beat and rail as it lands | `console/ChatConsole.tsx:350`, `RunPreview.tsx` | This is the single best-shaped fix in the plan — see below |
| 1.4 | Fix the t=0 lie: `LaneBoard` says *"This run reported no per-agent identity"* for the first ~5 s of **every** run, including team runs, because `routing` is null | `console/LaneBoard.tsx:459-462` | Say "sizing the run" until the first event arrives |

**These four are the plan's core.** If nothing else ships, ship these.

---

## Wave 2 — the demo path, choreographed

The rehearsed sequence: **ask → fan out → an attack is blocked → the gate pauses → the graph
lights up → the answer lands with provenance.**

Every dead pocket gets filled with something **true**. No fake progress bars, no invented
percentages — the honesty guarantees are the product's thesis and are enforced by tests.

| Window | Today | Fix |
|---|---|---|
| **guard_input, 3.1–7.5 s** | 6 static chips + a ms counter | Light the **beat as a whole** for its real duration; chips stay static (per-rail timings are `None` on the wire — see `01-DEMO-PATH.md` §4). Only the **deciding** layer locks green/red, because only it is measured |
| **plan_team → lanes, ~1.4 s** | one label | `RunPreview` stages lighting (1.3) |
| **lane model calls, 3–8 s each** | frozen `thinking · step 2/4` | Per-lane **live elapsed counter** (the browser-clock pattern already exists at `RunStages.tsx:343-353`), plus pace the reasoning block through the existing `useRevealedText`/`revealPace.ts` instead of dumping it in one frame |
| **synthesize, 3.8 s** | one label | Lane cards animate their verdict as they fold in (Framer Motion, already installed) |
| **guard_output, 7.8 s** | 6 static chips, answer already written | The single best-value copy change in the app: say loudly *"The answer is written. Screening six output rails before releasing any of it."* The sentence already exists at `stageTimeline.ts:103-105` as a 0.72rem caption |

### The scaffold — why `RunPreview` is the keystone

`RunPreview.tsx` already draws **the four beats and all twelve named rails** — input
(`schema · denylist · PII · prompt injection · content safety · topic`) and output
(`schema · content filter · denylist · content safety · grounding · PII`) — read from the same
source the live stage list reads. It currently lives *inside the empty-console block*
(`ChatConsole.tsx:350`) as "the one thing an empty console can say that is true", and vanishes the
moment a question is asked.

Promoting it to a persistent, lighting scaffold is exactly the pattern the research names — *a
skeleton that mirrors the shape of the answer makes the wait feel 30–40% shorter* — and it is
**honest by construction**, because those twelve rails are the real ones. It turns the two
guardrail windows (11 of 29 seconds) from a spinner into the platform's core argument, visibly
walking its own safety chain.

`Guardrail.layer` (`lib/stream.ts:172` — *"which rail layer produced the verdict"*) is already on
the wire, so the **deciding** rail can lock green or red on arrival. That is measured, not
animated fiction: the chase is framed as "the rails, in order" with an elapsed clock, and only the
deciding layer makes a claim.

### The one new live-data moment

The analogue of the winning trading-chart demo is **the attack that gets stopped, on screen, as it
happens.** `RetrievalStep` is documented as *"carries the graph delta so the viz can animate"*
(`lib/stream.ts:181`), and the injection block is measured at **six milliseconds, zero tokens,
zero dollars** (`persona-ai-team.md:1300`) — the sharpest single fact in the product. The moment
to build: send a prompt-injection question, and let the input rail visibly stop at
`prompt injection`, snap red, and post the 6 ms / $0 receipt while the rest of the chain greys
out — no model was ever called. Exact framing to be refined by the Plan agent's design.

---

## Wave 3 — the signature mark

An abstract mark with states driven by real signals:

```
idle      closed          fan-out   splits into N lanes
thinking  slow rotation   blocked   snaps shut + block hue
                          approved  opens + ok hue
```

**Open question to settle before building:** the console already contains **two** identity
elements — `brand/AegisMark.tsx` (a real single-path falcon, animatable via stroke-dash with no
new assets) and `console/AssistantBot.tsx` (an existing mascot with rAF pointer-tracking pupils
that *goes still during a run*). Adding a third would be clutter. The likely right answer is to
**give the existing bot the state machine** — it is already in the console, already reacts to
`running`, and is already documented as distinct from the brand mark — rather than draw a new
thing. To be confirmed against the Plan agent's argument.

---

## Wave 4 — the density pass ("calm it, don't remove it")

Mechanical rules, no judgement calls:

1. **InfoTip ceiling: 25 words.** 56 currently exceed it. Over the ceiling → cut, don't relocate.
2. **`Absence` is one line.** `primitives/Receipt.tsx:106-115` renders three `<p>`s; DESIGN.md §4
   says one. Fixing the primitive fixes **99 instances** at once.
3. **Nav tooltips: 12 words.** 34 average 28.5; one is 129 words in a `title=` attribute, which is
   functionally invisible and pure maintenance cost.
4. **No section-intro paragraphs.** A card title plus a chart is complete.
5. **Charts before prose** on the demo-path screens only — not all 23 chartless screens. Several
   (compliance, approvals, settings) are honestly tables and should stay tables.

**Priority: only the 7 screens a 10-minute demo actually reaches** — Overview, Console, Approvals,
Database, Savings/Forecast, Guardrails, Access demo. The other 28 get the primitive-level fixes
(2) and (3) for free and nothing else.

---

## Wave 5 — cheap correctness that reads as polish

- Wire the **orphaned `public/3d/*.png`** into the landing hero and Jobs blocks as their own
  manifest already claims, or delete them. 247 KB currently ships and renders nowhere.
- Delete `layout/TrustBar.tsx` — dead, mounted nowhere.
- **`prefers-reduced-motion` is unguarded on roughly a third of animated elements** — a DESIGN.md
  §6 violation and an accessibility one.

---

## Explicitly NOT doing

- **No three.js / WebGL.** Not installed, and DESIGN.md §7 already measured that a pre-rendered
  AVIF beats shipping a renderer for a still hero (**4.5 kB vs 236 kB gzip**).
- **No 3D knowledge graph.** `+305 kB gzip`, and `nodeCanvasObject`/`nodePointerAreaPaint` do not
  exist in `ForceGraph3D` — the entire paint layer would be a rewrite, not a port.
- **No cute mascot.** Owner's decision, and DESIGN.md §7.
- **No "Impeccable" design skill.** The reviewer named it, but it is a warm-cream/burnt-orange
  editorial-poster system (`#CC8800`, Chakra Petch) — a different product's identity, and close to
  a recognisable AI-generated-design cliché. Take the repo as a catalogue, not that skill as a
  direction.
- **No new screens, no deletions.** Owner's decision.

---

## Verification

1. `cd web && npx tsc --noEmit && npm test` — baseline **370 passing**; the design suite
   (`lightThemeOnly`, `oneRamp`, `badgeContrast`, `healthStrip`) must stay green.
2. `web/scripts/shoot.mjs` — the 390/834/1440/1920 sweep, asserting no horizontal body overflow
   and no console errors per screen.
3. **A real timed run** with the console open, recorded — the pass fails if any window longer than
   2 s is still static.
4. `/web-interface-guidelines` on the changed components.
5. Backend suites untouched — this is presentation only.

---

## The honest risk in this plan

Three things could go wrong, and naming them is cheaper than discovering them on stage.

1. **Wave 1 could destabilise the console.** Keeping the flow canvas visible during a run changes
   the running layout, and `ChatConsole` is a 48-file, 10,412-LOC tree that two of five personas
   land on by default. This is the highest-value and highest-blast-radius change in the plan. It
   gets its own commit and its own screenshot sweep.
2. **"Calm it" can slide into "gut it."** The prose being cut includes genuinely load-bearing
   honesty — a `Receipt` naming a figure's origin, an `Absence` stating what is missing and why.
   That text *is* the product's thesis and the jury rubric rewards it. The rules in Wave 4 cut
   *restatement and essays*, never provenance. Every `Receipt` stays.
3. **A visual that implies measurement we do not have would be worse than the dead air.** The
   guardrail chase animates *order*, and only the deciding layer — which is on the wire — makes a
   claim. No invented per-layer timings, no fake percentages.

## Note on the pending design pass

A Plan agent is still designing the fine choreography, the signature-mark argument (build on the
existing bot vs. a new mark) and the live-data framing. Its output folds into `01-DEMO-PATH.md`
and `03-SIGNATURE-MARK.md` when it lands — it refines detail inside these waves, and does not
change the shape of the plan or the scope of this execution.
