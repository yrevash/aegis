# Evidence

The measured basis for the other four documents. Three exploration agents audited the working tree
on 2026-08-24. Every claim below was read out of the code, not inferred.

---

## A. Visual capability — what exists

### A.1 Libraries: everything installed is used, and there is no WebGL

| Library | Installed | Imported | Where |
|---|---|---|---|
| `recharts` ^3.10.1 | yes | **17 files** | all of `charts/`, `Gauge`, forecast, redteam, ops, analytics, dashboard |
| `@xyflow/react` ^12.11.3 | yes | **1 file** | `console/FlowCanvas.tsx`, mounted `ChatConsole.tsx:912` |
| `react-force-graph-2d` ^1.29.1 | yes | **1 file** | `graph/KnowledgeGraph.tsx:10` (dynamic, `ssr:false`) |
| `motion` (Framer 13) ^13.1.0 | yes | 5 files, **only 10 `<motion.*>` elements** | 3 of the 5 import only `useReducedMotion` |
| `three` / `@react-three/fiber` / `drei` | **NOT INSTALLED** | — | — |

**No unused viz dependency exists.** The one orphan is an asset: `web/public/3d/{cube,slab,torus,sphere}.png`
— **247 KB**, with a `manifest.json` declaring `"used": "landing hero, Jobs stage block"` — and a
repo-wide grep across `web/src` returns **zero references**. Generator `web/scripts/render-3d.mjs`
still exists.

### A.2 Already-built live visuals

- **`console/FlowCanvas.tsx`** (521 L) — React Flow over the compiled 17-node / 23-edge graph.
  Active-node pip `:127-131`; edges animated **only while running** `:364`. Custom `StageNode` with
  four handles so the `reflect → plan` self-repair loop bows sideways `:88-95`.
- **`console/orchestration.ts`** (557 L) — pure, tested flow model: `buildFlowMap`, `layoutFlow`
  (deterministic layered DAG), `resolveFlow` (live status), `isEdgeActive`, `isEdgeNotTaken`.
  Topology read off the compiled LangGraph via `GET /agent/topology`; the committed snapshot is
  asserted equal to the live graph by `backend/tests/api/test_agent_topology.py:114`.
- **`graph/KnowledgeGraph.tsx`** (323 L) — canvas 2D with fully custom paint. Traversed edges
  recolour and thicken `:205-214`; **3 directional particles per touched edge** `:215-219`; active
  halo pulses on a `performance.now()` sine `:236-240`; camera `zoomToFit` `:150-157`; **scopes to
  the evidence subgraph** once a run touches nodes `:139-146`.
- **`console/LaneBoard.tsx`** — the fan-out. Container-queried grid (2-up ≥30rem, 3-up ≥48rem)
  *"because a fan-out is concurrent, and a vertical stack renders four simultaneous agents as a
  queue"*. Screenshot evidence at `testdata/audit/fanout-4-lanes.png`.
- **`jobs/PipelineIso.tsx`** (672 L) — working SVG isometric on a single non-nested projection.

### A.3 Brand

- `brand/AegisMark.tsx` (88 L) — single-path falcon, `viewBox="0 0 218 136"`, `currentColor`.
- `brand/AegisLockup.tsx` (35 L) — falcon + wordmark, one source for all five placements.
- `console/AssistantBot.tsx` (113 L) + `botEyes.ts` (62 L) — line-art robot, pupils tracking the
  pointer via CSS vars inside rAF, **zero React renders per frame**; still during a run.
- 69 recoloured Storyset illustrations behind `illustration/Scene.tsx`.

### A.4 Motion

Tokens `globals.css:26-31`: `--dur-fast:120ms`, `--dur-base:200ms`, `--dur-slow:320ms`,
`--dur-count:900ms`, plus two easings.

| Utility | Uses |
|---|---|
| `.animate-pip` | 11 |
| `.animate-reveal` | 6 |
| `.animate-trace-in` | 5 |
| `.animate-scan` | **0 — dead** |
| `.animate-idle-breathe` | **0 — dead** |

Both dead utilities are the two effects deliberately removed from the knowledge graph, citing
DESIGN.md §6's ban on infinite loops (see comments at `KnowledgeGraph.tsx:76-84, 186-194`).

`prefers-reduced-motion` is handled in two layers — a global kill-switch at `globals.css:516-522`
and 10 components branching in JS — **but roughly a third of animated elements are unguarded**:
~54 `animate-spin` + 12 `animate-pip` + others against only 36 `motion-reduce:animate-none` guards.

---

## B. The run wait

### B.1 Measured timeline

`console/stageTimeline.ts:5-16`, pinned in `web/tests/console/stageTimeline.test.mjs:52-85`:

```
guard_input      3,142 ms
route               10 ms
plan_team        1,444 ms
agent:research   3,396 ms  ┐ concurrent
agent:knowledge  8,605 ms  ┘
run_team        12,254 ms
synthesize       3,795 ms
guard_output     7,789 ms
stream               0 ms
                --------
top-level       28,434 ms
```

Live captures: team-of-2 **23.7 s / 22,979 tok / $0.0126 / 144 events**
(`persona-ai-team.md:86-121`); single-lane client **35.4 s** (`persona-client.md:95-96`); Access
demo **39,667 ms** and **62,023 ms**; `P95 LATENCY 55.3s`.

**~25–45 s of a 40–60 s run is static pixels. Roughly one second is animated.**

### B.2 The stream contract

20 event variants (`backend/src/app/api/schemas.py`, mirrored in `web/src/lib/stream.ts:432-452`).
`describeEvent.tsx` **does** now cover `reflection` `:190`, `routing` `:203`, `agent_status` `:214`,
`synthesis` `:224`, `memory` `:239` — **but it is only reached via `TraceTab`, which mounts only
when `settled`** (`ChatConsole.tsx:184, 229`). All of it is post-mortem.

The live path is `deriveActivity` in `agentLanes.ts:382-511`.

Fields that matter for this plan:
- `Guardrail.layer: string | null` — *"which rail layer produced the verdict"* (`stream.ts:172`)
- `RetrievalStep` — *"carries the graph delta so the viz can animate"* (`stream.ts:181`)

### B.3 The two switched-off visuals — verified directly

```
ChatConsole.tsx:638   setView('run')          ← inside send()
ChatConsole.tsx:178   const beat = beatFromSignal(run.lastSignal)
ChatConsole.tsx:234   {settled && <ResultTabs beat={beat} …/>}
ChatConsole.tsx:350   <RunPreview />          ← inside the empty-console block only
```

### B.4 Known gap

`signalForEvent` (`web/src/config/signals.ts:60-86`) has no case for `reflection` / `routing` /
`agent_status` / `synthesis` / `memory` / `node_finished` / `reasoning` — they fall to
`default: 'neutral'`. `runReducer.ts:203-217` shadows it with `signalForRunEvent`, so the beat hue
is correct; any *other* consumer gets grey for the fan-out.

---

## C. Density

### C.1 Top 10 by weight

| # | Section | Files | LOC | Cards | `<p>` | Charts | InfoTip | Prose words |
|---|---|---|---|---|---|---|---|---|
| 1 | **console** | 48 | 10,412 | 14 | **100** | 4 | 14 | **2,617** |
| 2 | **documents** | 10 | 4,365 | **42** | 29 | 6 | **23** | 1,557 |
| 3 | **jobs** | 9 | 3,730 | 28 | 26 | 2 | 20 | 1,391 |
| 4 | memory | 16 | 2,944 | 15 | 32 | 4 | 10 | 920 |
| 5 | dashboard | 11 | 2,283 | 14 | 21 | 6 | 12 | 610 |
| 6 | **mcp** | 6 | 2,063 | 8 | 16 | **0** | 16 | 1,261 |
| 7 | stack | 6 | 1,810 | 24 | 11 | 3 | 10 | 250 |
| 8 | analytics | 4 | 1,686 | 15 | 13 | 9 | 3 | 638 |
| 9 | llmops | 7 | 2,033 | 12 | 16 | 5 | 10 | 682 |
| 10 | forecast | 10 | 1,843 | 13 | 16 | 2 | 9 | 581 |

`jobs/JobsView.tsx:746` admits in its own comment: *"**It is nine panels**, so it is behind a
disclosure rather than deleted."*

### C.2 Global prose channels

| Channel | Instances | Words |
|---|---|---|
| `InfoTip` | **184** | **3,385** (56 are ≥25 w; max 95) |
| `Receipt` | 125 | ~2,420 |
| `Absence` | **99** | ~2,802 — **three `<p>`s each** (`Receipt.tsx:106-115`) |
| bare `<p>` ≥12 w | 67 | 1,678 |
| nav `tooltip` | 34 | **970** (mean 28.5; max 129) |

**23 of 35 screens import nothing from `components/charts/`.** `02-REMAINING-PORTALS.md` measured
the same thing independently: *"The headline: 19 of 21 screens draw no chart at all."*

### C.3 The diagnosis already in the repo

`docs/dev_new_docs_v2/frontend-redesign/03-AI-TEAM-PASS.md`:

> *"Relocating an essay into an `InfoTip` produces a screen that measures clean and still reads
> heavy — a text bomb with a lid on it."*

And the text rule it states: *"prefer deleting redundant prose over relocating it… No
section-intro paragraphs. A card title plus a chart is complete… If a panel exists only to hold one
sentence, delete the panel."* Target reading order: **visual → numeral → one line of provenance →
nothing else.**

---

## D. External research

- **Loading:** *"Never leave the screen static for more than a couple of seconds without a text
  change. A skeleton screen that mirrors the shape of the answer makes the wait feel roughly 30 to
  40 percent shorter than a blank panel with a spinner."*
- **Judging:** *"putting a model's decision on screen is the part judges remember"*; *"judges often
  associate visual clarity with product maturity, even in early prototypes"*; *"optimize for 1 to 2
  primary features to maximize the judges WOW factor."*
- **Mascots in B2B:** they aid recall, but *"a mascot works best as a recognition tool… and does
  nothing for making a brand feel warmer when that's not the actual problem"*, and *"embodying your
  brand does not mean becoming cartoonish, childish or less serious."*
- **`bergside/awesome-design-skills`** — a catalogue of 67 design-token skill files. The
  **"Impeccable"** skill specifically is a warm-cream / burnt-orange editorial-poster system
  (primary `#CC8800`, Chakra Petch + JetBrains Mono, 4/8/12/16/24/32 spacing). It is a different
  product's identity from Aegis's light-blue enterprise system, and sits close to a recognisable
  AI-generated-design cliché. **Recommendation: take the repo as a catalogue, not that skill as a
  direction.**
