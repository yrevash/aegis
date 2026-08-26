# Aegis — implementing the wow pass

## Context

Aegis is technically finished. Reviewers said the app shows **"too much data, too complex, not good
UX"**; a previous winner said the tech is done and what is missing are **visual moments people
remember**. The owner's regional-finals evidence agrees: what won was *the system working, visibly,
in real time*.

An audit found the problem is not what it looked like. **The wow factors are already built and
switched off.** A run takes 40–60 s and roughly **one second of it is animated**.
`stageTimeline.ts` says so itself: *"eleven of those twenty-nine seconds are the two guardrails, and
the console spent them showing a spinner."*

The plan documents live on this branch at `docs/dev_new_docs_v2/planVisual/`. This is the
implementation plan for them.

### Decisions taken by the owner

| | Decision |
|---|---|
| **Scope** | All four waves |
| **The human gate** | **Cut from the demo path.** Two turns: a normal run, then the injection block. No agent time hunting a phrasing that reaches the gate. The mark's `gated` state is still built so it works if one fires |
| **Density** | Primitive fixes app-wide, 40-word InfoTip ceiling on the demo screens only |
| **Identity** | An abstract mark that **replaces** `AssistantBot` |
| **Branch** | `docs/wow-pass-plan`. Nothing on `main` |

### On the design skills

Both were read. The anti-slop taste skill **excludes this work by its own Section 13** — *"NOT for
dashboards, dense product UI, admin panels"* — and several of its rules contradict Aegis's
test-enforced `DESIGN.md`: it mandates dark mode (Aegis is light-only, `lightThemeOnly.test.mjs`),
discourages `lucide-react` (40+ files), and bans hand-rolled SVG (the mark must be one).
`DESIGN.md` §10 settles it: *existing functionality > this file > taste principles*.

**Taken from it:** motion must be motivated in one sentence, no decorative status dots, no
fake-precise numbers, real loading/empty/error states, tactile `:active` feedback.
**Not taken:** "Impeccable" (a warm-cream poster system — a different product's identity).

---

## Step 0 — branch

```
git checkout docs/wow-pass-plan
```

Six uncommitted files travel across the checkout (an ML loader guard plus runtime debris). They are
not part of this work and must not be swept into any commit here.

---

## Wave 1 — the switched-off wins

### 1.1 Stop yanking the user off the Flow tab

`ChatConsole.tsx:638`, inside `send()`:

```ts
setView('run')                                    // today
setView((v) => (v === 'flow' ? 'flow' : 'run'))   // keep Flow if they chose it
```

**Do not co-render Flow and Run.** Exploration confirmed five things break: both panels are
`flex-1` so height splits ~50/50; below `lg` the flow panel's `max-lg:h-[68vh]` pushes the thread
off screen; `role="tablist"` + `aria-selected` becomes a lie with two visible panels; `railShown`
(`:751`) would keep the 20 rem memory rail open beside a graph; and the flow panel is
**deliberately mount-gated** (`:885`) because React Flow measures its container once on mount and a
`hidden` panel measures 0×0.

Always-visible flow presence comes from 1.3 instead, not from co-rendering the canvas.

### 1.2 Thread `beat` into the running header

`beat` is already computed at `ChatConsole.tsx:178` and passed only to the settled `ResultTabs`
(`:234`). Add `beat={beat}` at `:223`; widen `RunPanel`'s props (`RunStages.tsx:329-336`) from
`{state, children}` to `{state, beat, children}`. Consume it on the header span at
`RunStages.tsx:390-397`, keyed on `beat.seq`.

**Gate on `state.running`.** `lastSignal` updates on *every* event including `run_finished`, so an
ungated mark pulses forever on a settled run.

### 1.3 The stateful run scaffold — the keystone

**Correction to the planning docs:** `RunPreview` does **not** draw twelve rail chips. It draws
four beats, each rail beat printing a `Figure` of `6`; the twelve individual chips are rendered by
`ActivityRail.tsx` while a guard stage is in flight. Build against what is there.

- Widen `RunPreview.tsx:69` to `RunPreview({ state = null }: { state?: RunState | null })`. The
  idle mount at `ChatConsole.tsx:350` keeps working unchanged.
- Mount it **inside the run card** at `RunStages.tsx:453` — between `</header>` and the
  `{running ? (` grid, as a direct child of `<section aria-label="Run">`. Full width, inherits
  `gap-3`, above the `@[46rem]/turn` split, inside the card border. **Not** at `ChatConsole:222`,
  which makes it a fifth bordered thing above the panel — the shape `TrustChecks`' docblock says was
  already removed once for that reason.
- New pure module `web/src/components/console/runPath.ts`: `BEATS` (moved out of `RunPreview`) and
  `beatStates(state): BeatState[]` returning `pending | running | passed | blocked` per beat, from
  `deriveTiming(state).stages`. Node→beat map: `guard_input`→0; `route`/`plan_team`→1; the middle
  group→2; `guard_output`→3.

**The gap to close first.** `INPUT_CHAIN`/`OUTPUT_CHAIN` are *display labels* (`'PII'`,
`'prompt injection'`), while `Guardrail.layer` is a rail *id* (`'pii'`, `'injection'`). No map
exists. Add one beside the chains in `stageTimeline.ts:72-89` — a predicate in a `.tsx` beside a
`<li>` is a predicate nobody tests.

Verdict handling **must** go through `outputVerdict.ts`. `GuardVerdict` has four members —
`pass | block | redact | flag` — and `outputVerdict.test.mjs` exists precisely to stop them
collapsing into two.

### 1.4 The t=0 copy

`LaneBoard.tsx:459-462` says *"This run reported no per-agent identity"* for the first ~5 s of every
run, including team runs, because `routing` is still null. Say *"sizing the run"* while
`state.events.length === 0`.

---

## Wave 2 — the signature mark

**A six-segment ring around a core.** Six because `INPUT_CHAIN.length === OUTPUT_CHAIN.length === 6`
— the geometry is a fact about the system, not decoration. `viewBox="0 0 48 48"`, six arc paths on
r=18 with a 6° gap, one `<circle r=6>` core. Roughly 70 lines.

New files:
- `console/runMarkState.ts` — **pure**. `markStateOf(state): MarkState` and
  `brokenSegmentOf(state): number | null`. Precedence is the whole content of the file and must be
  explicit: `blocked > gated > fanout > screening > thinking > settled > idle`.
- `console/RunMark.tsx` — `'use client'`, `useReducedMotion`, `aria-hidden`.
- `tests/console/runMarkState.test.mjs`.

| State | Wire fact | Motion | Reduced-motion |
|---|---|---|---|
| `idle` | no run | draws in once, 320 ms. **No loop** (§6) | static |
| `screening` | `timing.current` and `isGuardStage(node)` | slow rotation — carries **duration, never progress** | static |
| `thinking` | open stage, lanes ≤ 1 | core pulse keyed on `beat.seq` | static |
| `fanout` | `state.routing.depth === 'team'`, width from `.fanout` | segments spring into N arcs | spread, no spring |
| `gated` | `state.approval !== null` | **rotation stops dead.** The stillness is the state | identical |
| `blocked` | last guardrail `verdict === 'block'` | one segment vanishes at the deciding layer's index | identical |
| `settled` | `!running && answer !== ''` | segments close, 200 ms | identical |

An unrecognised `layer` yields `null`, not segment 0 — a break drawn at the wrong rail is worse than
none.

**Deletions:** `AssistantBot.tsx`, `botEyes.ts`, `tests/console/assistantBot.test.mjs`, and the
import plus render at `ChatConsole.tsx:924`. Verified: nothing else references them. Net **−1
character, −1 identity element, +1 that does something.**

---

## Wave 3 — the firing line

Real red-team probes fired one at a time at the real input rail; each verdict lands as its SSE frame
arrives. Mounts on the **existing** guardrails screen, after `<PageHeader>` and before
`<RedteamHero>` (`GuardrailsView.tsx` ~862). A new route would need a `test_route_coverage.py` entry
and portal reachability (§8).

### The wire format — verified, do not guess

`GET /v1/stream/guardrail-demo?q=…` is unauthenticated and emits **five** frames: `RUN_STARTED`,
`STEP_STARTED`, `CUSTOM(guardrail_verdict)`, `STEP_FINISHED`, `RUN_FINISHED`. There is **no `event:`
line** — `data:` only. Model fields are camelCase; **the `value` payload stays snake_case.**

```ts
{ verdict: 'pass'|'block'|'redact'|'flag'
  rules: string[]            // 0 or 1 element: the deciding layer
  rationale: string
  redactions: string[]       // PII kinds only, never values
  redaction_spans: { kind: string; start: number; end: number }[]
  per_rail_timing_ms: { schema: null; pii: null; injection: null; total: number }
  spanKind: 'GUARDRAIL' }
```

Three traps:
1. **A second CUSTOM frame** (`guardrail_cache`) can arrive between `STEP_STARTED` and the verdict.
   Narrow on `name === 'guardrail_verdict'`.
2. **There is no `RUN_ERROR` frame.** An error looks like a stream that ends after `STEP_STARTED`.
   "Closed without a verdict" is a distinct state the client must render.
3. **`per_rail_timing_ms` sub-rails are always `null`.** Only `total` is measured. Never draw six
   durations — that is the fabricated measurement the plan docs ban.

### Probes

`getRedteamHistory(token)` → newest run → `getRedteamRun(token, runId)` → `report.attacks[]`, each
carrying real `prompt`, `category`, `owasp`, `stage`. Use `redteam.ts`, **not** the older
`runRedteam` shape `GuardrailsView` already holds — that one has no `stage`.

Filter to `stage === 'input'` and exclude `benign_control`. **Skip `sequence` probes**: their
`prompt` is one query of a burst, so firing it standalone misrepresents the probe.

No stored run → an `Absence`, never an invented payload. Note the second axis this screen already
encodes: a tenant-pinned principal gets a *different* sentence, because `/redteam/runs` is scoped.

### Chart conventions

**`RankedBars` is not SVG** — it is a semantic `<ul>` with div bars. The hand-built-SVG precedent is
`MiniTrend.tsx`: fixed `viewBox` with `preserveAspectRatio="none"`, `useId()` for gradient ids,
`vectorEffect="non-scaling-stroke"`, `chartHex()` for colour, and below two finite points it draws a
dashed baseline rather than inventing a shape. The `<svg>` is `aria-hidden`; the accessible
representation is the labelled DOM beside it.

Colour from `SIGNALS`/`chartHex` only. Every landed probe carries a text label (verdict word +
latency) so colour is never the only channel. An unknown verdict must degrade to a legible marker —
`GuardrailReveal.UNKNOWN_VERDICT` exists because a `Record<GuardVerdict, …>` returned `undefined`
for `flag` and took the console down.

Two caveats printed on the panel: the y-axis is the **rail's** time, not round-trip; and
`routes.py:1556` builds a bare `Guardrails()` — platform floor rails, no tenant fold — so a block
rate here can differ from the console's.

New files: `lib/api/guardrailDemo.ts` (over the existing `readSSEStream`), `guardrail/firingLine.ts`
(pure), `guardrail/RailFiringLine.tsx`, `tests/guardrails/firingLine.test.mjs`.

**Block rate is `blocked / fired`, never `blocked / probes`** — a run stopped halfway must not report
a rate against probes it never fired.

---

## Wave 4 — the density pass

1. **`Absence` becomes one line.** `primitives/Receipt.tsx:106-115` renders three `<p>`s;
   `DESIGN.md` §4 says one. `figure` and `why` join on one line; `needed` moves behind an `InfoTip`.
   **Fixing the primitive corrects 99 call sites with zero call-site churn** — the highest-leverage
   edit in the plan.
2. **Delete `title={item.tooltip}`** from `layout/PortalNav.tsx:98`. 34 native tooltips averaging
   28.5 words, one of them 129 words — functionally unreadable, and they pop as the pointer crosses
   the rail during a demo. Cap `Section.tooltip` at 12 words in `lib/portal.ts`.
3. **40-word InfoTip ceiling** on the demo screens only, with a new
   `tests/design/tipLength.test.mjs` written like `lightThemeOnly.test.mjs` (source scan, floor
   assertion so it cannot pass vacuously).

Cut in this order, stop when under: restatement of the label → advice sentences → keep the sentence
naming a file, route, table, rail or figure (that is the glass box) → what remains is documentation,
move it to `docs/`.

**Never cut a `Receipt`.** Provenance is the product's thesis and the rubric rewards it.

---

## The agent split — non-colliding by directory

| Agent | Owns | Waves |
|---|---|---|
| **A** | `components/console/**`, `state/runReducer.ts` (read-only) | 1 + 2 |
| **B** | `components/guardrail/**`, `lib/api/guardrailDemo.ts` | 3 |
| **C** | `primitives/Receipt.tsx`, `lib/portal.ts`, `layout/PortalNav.tsx`, InfoTips on **redteam, graph, compliance, documents** | 4 |

**C must not touch `components/console/**` or `components/guardrail/**`** — A and B own those, and
their InfoTips are theirs to trim.

---

## Verification

1. `cd web && npx tsc --noEmit && npm test` — baseline **370 passing**. The design suite
   (`lightThemeOnly`, `oneRamp`, `badgeContrast`, `navGroups`, `figureTruncate`) must stay green.
2. **`flowContainment.test.mjs` is the highest structural risk.** It reads `ChatConsole.tsx` as
   *text* and asserts the flow panel's className — sliced from `id="console-panel-flow"` to the
   first `>` — contains `overflow-hidden`, `min-h-0`, `flex-col`. Moving that div, changing its id,
   or reordering classes out of its `cn(` call fails it.
3. `outputVerdict.test.mjs` — a stateful rail chip must use that module, not re-derive
   `passed = verdict === 'pass'`.
4. `stageTimeline.test.mjs` pins `chain.length === 6`; adding the label↔layer map touches that file.
5. `web/scripts/shoot.mjs` — the 390/834/1440/1920 sweep, asserting no horizontal body overflow and
   no console errors.
6. **A real timed run with the console open.** The pass fails if any window longer than 2 s is still
   static.
7. Backend suites untouched — this is presentation only.

---

## What must not be built

| Tempting | Why not |
|---|---|
| The six guardrail chips animating in sequence | `per_rail_timing_ms` sub-rails are `null` on the wire. Six invented durations, on the screen whose subject is not claiming things |
| A run progress bar | There is no percentage. A guess rendered as a fact |
| Count-up on a governance figure | §6: *"a spend cap that animates looks approximate"* |
| Re-enabling `.animate-scan` / `.animate-idle-breathe` | Removed on purpose; §6 bans ambient loops on operator screens. **Delete both keyframes** so they are not rediscovered |
| Co-rendering Flow and Run | Five documented breakages — see 1.1 |
| three.js, 3D hero, 3D graph | §7 priced it: 4.5 kB vs 236 kB; the 3D graph is +305 kB and a full paint-layer rewrite |
| A mascot | Owner's decision. The one that exists is being deleted |
| A new route for the firing line | §8 requires portal reachability and a route-coverage entry |
| Deleting a screen or a number | Wave 4 is relocation and collapse, never removal |
