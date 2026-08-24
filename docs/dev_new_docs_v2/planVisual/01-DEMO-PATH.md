# The demo path, second by second

> ## ⚠ BLOCKER — do this before any UI work
>
> **The human gate is the product's signature moment and no question reliably reaches it.**
>
> `web/src/config/personas.ts:71-90` states that the gate is unreachable from natural language and
> that reaching it *"needs a read-side tool in `backend/src/app/adapter/tools.py` (a LOW-risk
> `find_requests`)"*. **That tool now exists** — verified in `tools.py`. The docstring was never
> updated, no seed chip was added, and no phrasing has been pinned.
>
> **Day-one task:** find the phrasing that reliably reaches the gate on the demo box (the candidate
> shape is *"close the oldest resolved billing case"*), watching the stream for `approval_required`.
> Then add it as `sampleQueries[3]` in `personas.ts`, rewrite the stale paragraph, and add a fourth
> entry to `scripts/verify_e2e.py` with `"expect": ["tool_call", "approval_required"]` so it cannot
> silently regress the night before.
>
> **If it cannot be pinned, Turn C is cut and the demo is Turns A and B.** Decide this on day one,
> not on the day.

## 0. The demo is one session, three turns

Not one run. The arc is **normal → attacked → gated**, which is also the argument: the platform is
useful, then it is safe, then it is accountable.

| Turn | Question | Wall | What it proves |
|---|---|---|---|
| **A** | `Which requests are breaching SLA? What does our escalation policy require? What does the runbook say? Who approves it?` | 40–60 s | Fan-out to 4 lanes, retrieval, provenance, both rails |
| **B** | an injection payload | ~2 s | The input rail blocks and names the deciding layer |
| **C** | the gate question (see blocker above) | ~30 s | `find_requests` → `update_request_status` (HIGH) → human gate |

Turn A is already `sampleQueries[1]` in `web/src/config/personas.ts` and is already measured at
`depth=team fanout=4`. **Use the chip; do not retype it on stage.**

What is on screen for every second of it. Nothing here invents a measurement: every beat is
driven by an event already on the wire.

> **The rule this document exists to enforce.** A visual that implies precision we do not have is
> worse than the dead air it replaces. Where the wire reports a fact, draw it. Where it reports
> `None`, draw nothing — see §4's correction on per-rail timings, which is the case that nearly
> got this wrong.

---

## 1. The measured baseline

From `web/src/components/console/stageTimeline.ts:5-16`, pinned as a fixture in
`web/tests/console/stageTimeline.test.mjs:52-85`:

| Node | Measured | On screen today |
|---|---|---|
| `guard_input` | **3,142 ms** (7.5 s on the demo box) | 6 static chips + a ms counter |
| `route` | 10 ms | one label flip |
| `plan_team` | 1,444 ms | one label |
| `run_team` | **12,254 ms** (agents 3,396 ‖ 8,605) | lane cards; still between beats |
| `synthesize` | 3,795 ms | one label |
| `guard_output` | **7,789 ms** | 6 static chips, answer already written |
| `stream` | 0 ms | the ~900 ms answer reveal |
| **top-level** | **28,434 ms** | ~1 second of it animated |

`stageTimeline.ts`'s own docstring already names the defect:

> *"Eleven of those twenty-nine seconds are the two guardrails, and the console spent them showing
> a spinner."*

Live captures agree: a team-of-2 run at **23.7 s / 22,979 tok / $0.0126 / 144 events**
(`docs/teaching/persona-ai-team.md:86-121`); a single-lane client run at **35.4 s**
(`persona-client.md:95-96`); `P95 LATENCY 55.3s` (`persona-client.md:185`).

---

## 2. Dead air, quantified

| Window | Duration | What moves today |
|---|---|---|
| `createSession` round-trip (first question only) | 0.2–0.8 s | **nothing** |
| turn mount → first byte | ~0.3 s | three "not measured" |
| **`guard_input`** | **3.1–7.5 s** | 6 static chips, one number |
| `plan_team` | 1.4 s | one label |
| shared retrieval | 2–4 s | "Retrieval started", then silence |
| lane model calls | 3–8 s each | frozen `thinking · step 2/4` |
| `synthesize` | 3.8 s | one label |
| **`guard_output`** | **7.8 s** | 6 static chips |
| **Total static** | **~25–45 s of a 40–60 s run** | |

---

## 3. The scaffold — the keystone change

`RunPreview.tsx` already draws **the four beats and all twelve named rails**:

```
Input rail   schema · denylist · PII · prompt injection · content safety · topic
Route
Retrieve & answer
Output rail  schema · content filter · denylist · content safety · grounding · PII
```

It reads from the same source the live stage list reads (`INPUT_CHAIN` / `OUTPUT_CHAIN`,
`stageTimeline.ts:72-89`), and its own docstring is careful about what it does not claim:

> *"The middle is deliberately one beat rather than a guess at which of `answer_memory`,
> `retrieve → plan → act → reflect` or a fan-out this particular question will take — that is the
> router's decision and it has not been made yet."*

**Today it is mounted only inside the empty-console block** (`ChatConsole.tsx:350`) as *"the one
thing an empty console can say that is true"*, and disappears the instant a question is sent.

**The change:** promote it to the run's persistent scaffold and light each beat and rail as it
lands. This is the research's *"skeleton that mirrors the shape of the answer"* — measured to make
a wait feel **30–40% shorter** — and it is honest by construction, because those twelve rails are
the real ones the request passes through.

---

## 4. Beat-by-beat choreography

### t = 0 → 0.3 s · dispatch

The scaffold is already on screen from the idle state, so **the transition is a state change, not
a mount.** Beats grey → `Input rail` goes live. Fixes the current three-way "not measured".

Also fix `LaneBoard.tsx:459-462`, which currently says *"This run reported no per-agent identity"*
for the first ~5 s of **every** run — including team runs — purely because `routing` is still null.
It should say *"sizing the run"* while `state.events.length === 0`.

### t = 0.3 → 7.5 s · `guard_input` — the longest dead pocket

> **A correction, and the most important line in this document.** An earlier draft of this plan
> said the six rail chips should "chase in sequence". **That would have been fabricated
> measurement.** The backend emits, verbatim (`aegis/src/aegis/guardrails/pipeline.py:1308-1312`):
>
> ```python
> "per_rail_timing_ms": {"schema": None, "pii": None, "injection": None, "total": timing["total"]}
> ```
>
> Per-rail timings are **null**. Only the total is measured. A sequential chase would draw six
> durations the platform explicitly declines to claim — on the one screen whose entire subject is
> not claiming things. It is the most attractive lie available here, and it is banned.

What actually happens:

- The **beat as a whole** lights and holds for its real duration, with the elapsed clock running.
- The six chips are **present but static** — they name the rails the request passes, which is true,
  and claim nothing about their order or timing.
- The mark (§ `03-SIGNATURE-MARK.md`) is in `screening`: a slow continuous rotation that carries
  **duration, never progress**.
- On the `guardrail` event, `Guardrail.layer` (`lib/stream.ts:172` — *"which rail layer produced
  the verdict"*) names the **deciding** rail, which locks green on pass or red on block. That one
  is measured, so that one may make a claim.

`stageTimeline.ts:58-62` already states the constraint, and this is the sentence to keep quoting:

> *"The wire reports one verdict and at most one deciding `layer` — never per-layer progress — so
> this is the chain, not a progress bar."*

**The presenter's line lands here**, and it converts the dead pocket into the argument:
*"eleven of those twenty-nine seconds are the guardrails. That is what governance costs, and we
show you the bill."*

### t = 7.5 → 9 s · `route` → `plan_team`

Scaffold advances to `Route`. Heading flips to `Team of 2`; the `RoutingReceipt` line appears with
`decided_by`. Lane cards mount with the existing 40 ms Framer stagger.

### t = 9 → 21 s · `run_team` — the fan-out

Already the best-animated surface in the app (`LaneBoard.tsx`), laid out in a container-queried
grid *"because a fan-out is concurrent, and a vertical stack renders four simultaneous agents as a
queue"*. Two additions:

- **Per-lane live elapsed counter.** The browser-clock pattern already exists at
  `RunStages.tsx:343-353`; lift it into `LaneCard`. The `step k/4` fraction is already on the wire
  (`subagent.py:557`).
- **Pace the reasoning.** Today `_sentences(result.content)` is written *after* the call returns
  (`subagent.py:567-568`), so the typing caret fires for a single frame. Run it through the
  existing `useRevealedText` / `revealPace.ts` — the same honest pacing already used for the
  answer.

### t = 21 → 25 s · `synthesize`

Lane cards animate their verdict badges (`in the answer` / `omitted · reason`) as they fold, rather
than all landing in one frame. Framer Motion is installed and barely used.

### t = 25 → 33 s · `guard_output` — the safety claim

The answer exists in full, one node away, and six rails are screening it. The copy already exists
at `stageTimeline.ts:103-105` as a 0.72 rem caption. Make it the loudest thing on screen:

> **The answer is written. Screening six output rails before releasing any of it.**

This is the single best value-per-character change in the plan: the worst dead pocket becomes the
product's central argument.

### t = 33 s · `stream` → settle

All ~60 `token` events land in one frame; `useRevealedText` paces the reveal over ~900 ms at a
floor of 110 chars/s. Unchanged — it already works.

---

## 5. The two switched-off visuals

### 5.1 The live flow graph

`console/FlowCanvas.tsx` (521 L) already animates the compiled 17-node / 23-edge graph — active
node pip at `:127-131`, edges animated **only while running** at `:364`. The topology snapshot
ships locally (`web/src/config/graphTopology.json`), so it draws at t=0 with nothing lit and fills
in as the run advances.

**It is unreachable during a run.** `ChatConsole.tsx:638` calls `setView('run')` inside `send()` —
asking a question actively switches away from it.

Two options, in preference order:
1. Split the running layout so a compact flow spine sits above the run panel — visible without
   costing a tab.
2. Stop forcing the view, and let a running turn default to Flow.

**Risk:** this changes the running layout of a 48-file, 10,412-LOC tree that two of five personas
land on by default. Own commit, own screenshot sweep.

### 5.2 The heartbeat

`beatFromSignal(run.lastSignal)` is computed on **every live turn** (`ChatConsole.tsx:178`) and
passed only into `{settled && <ResultTabs beat={beat}/>}` (`:234`) — a component that mounts after
the run ends. `.animate-beat` is defined at `globals.css:438-450` and `motion.ts:33-35` already
ships `isBeatSignal` for per-chip gating.

Wire `beat` into the running header and the four trust checks: **144 wire events become 144 visible
ticks**, at roughly ten lines of change. `runReducer.ts:203-217` already assigns correct hues to
`routing` / `agent_status` / `synthesis` / `reflection` / `memory`.

---

## 6. The live-data moment — the rail, firing

The structural analogue of the winning trading-chart demo: **adversarial input → real classifier →
real latency → decision landing live.** A firing line, not a fraud chart.

Real adversarial payloads from the shipped red-team battery are fired one at a time at the real
input rail. Each verdict lands on a live strip chart the instant its SSE frame arrives:
x = probe index, y = the **server's own measured milliseconds**, dot colour = the verdict, caption
= the rail that decided and its own sentence. A block counter climbs.

### The endpoint already exists and has never been called

`GET /v1/stream/guardrail-demo?q=<text>` — `backend/src/app/api/routes.py:1523`. It runs a real
`Guardrails().stream_check_input_agui(q, em)` and streams AG-UI SSE frames. The
`CUSTOM(guardrail_verdict)` frame carries `verdict`, `rules: [layer]`, `rationale`, `redactions`,
`redaction_spans` (real character offsets) and `per_rail_timing_ms.total` — **a genuine
server-measured figure.** Unauthenticated by design, so it cannot fail on a stale token mid-demo.

**Verified:** the only reference anywhere in `web/src` is the generated OpenAPI type. Nothing has
ever called it.

### The honesty ladder for payloads

1. **Preferred:** the shipped battery via a stored run — `getRedteamHistory` → newest run →
   `report.attacks[].prompt`. Every payload on screen is then a real probe, and the receipt reads
   `Source: redteam run <id> · suite <id>`.
2. **If no run is stored:** render an `Absence` — *"No red-team run is stored, so there are no
   probes to replay."* **Never invent a payload.** The one surface whose entire subject is
   adversarial honesty is the last place to fabricate data.

Rehearsal task: fire the battery once (offline, no model calls) before the demo so history exists.

### Two caveats that must be printed on the panel

- The y-axis is the **rail's** time (`per_rail_timing_ms.total`), not round-trip latency.
- `routes.py:1556` constructs a bare `Guardrails()` — **platform floor rails, no tenant fold, no
  completer** — so a block rate measured here can differ from the console's.

Stating both is the product's personality. A juror who spots either unstated is a juror lost.

### Where it lives

On the **existing `guardrails` screen**, at the top — not a new route. DESIGN.md §8 requires every
route to be reachable from a real portal with a `test_route_coverage.py` entry.

### Supporting beat, free

`RetrievalStep` *"carries the graph delta so the viz can animate"* (`lib/stream.ts:181`), and
`KnowledgeGraph.tsx` already recolours traversed edges, runs 3 directional particles per touched
edge, pulses a halo on active nodes and scopes to the evidence subgraph. On a clean question this
is the second wow beat and needs **no new code** — only to be on screen while it happens.

---

## 7. What must not be built

| Tempting | Why not |
|---|---|
| Per-layer guardrail timings | The wire reports one verdict and one deciding layer. Inventing six durations would be fabricated measurement on the product's own honesty surface. |
| A progress bar for the run | There is no percentage. `stageTimeline.ts` calls this out explicitly; a bar would be a guess rendered as a fact. |
| Count-up on any governance figure | DESIGN.md §6: *"a spend cap that animates looks approximate."* |
| Idle ambient loops on operator screens | DESIGN.md §6. `.animate-scan` / `.animate-idle-breathe` were built and deliberately deleted from the knowledge graph for exactly this reason — they remain in `globals.css` with zero uses. |
| A synthetic "fraud detection" chart | Aegis has no fraud domain. Borrowing the regional demo's *form* without its data would be the one thing that discredits the rest. |
