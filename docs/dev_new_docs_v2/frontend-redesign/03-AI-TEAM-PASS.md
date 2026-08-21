# The `ai_team` portal — the visual pass

> **Status, 2026-08-21.** Wave 1 is **done and pushed**: `evals`, `mlops`, `harness`,
> `guardrails`, `llmops`, `tokenopt`, `rag`, `graph`, `memory` — nine screens, plus an
> audit-and-fix pass over all nine. Wave 2 — `vision`, `voice`, `simulation`, `cache` —
> is **not started**.
>
> Measured after Wave 1, on a clean dev server: **zero horizontal overflow and zero blank
> screens** across all 16 `ai_team` sections at 390 / 834 / 1440 / 1920 (`web/scripts/shoot.mjs`).
> tsc clean · web 231/231 · build 67/67 · lint clean but for three pre-existing
> `dashboard/` warnings.
>
> The 34 console errors the sweep reports are **one pre-existing backend defect, not a
> regression** — see "The RBAC gap" at the end of this file.

Written 2026-08-21, from a full read of all thirteen screens rather than from memory.
Hand this to an implementing agent alongside `00-DESIGN-DIRECTION.md`.

`platform_admin` established the language (`02-REMAINING-PORTALS.md` records what it
finished). This applies it to the second portal. It is **presentation only** — no API,
auth, routing, tenant-scoping or business-logic change.

---

## The brief

> *"more visual, less text, clean — use more and more Storyset images, transform it
> fully visually."*

So the target is not "add a chart to every screen". It is: **every screen leads with
something you can look at**, prose collapses into the primitives that already exist,
and the honest empty states — which this platform produces a great many of by design —
stop reading as brokenness and start reading as composure.

---

## Wave 0 — done, committed, do not redo

- **21 new scenes** recoloured onto the blue ramp and committed to
  `web/public/illustrations/` (67 total).
- **`scripts/recolour-illustrations.mjs`** — the mapping now lives in idempotent code.
  CREDITS.md's table had missed Storyset's current accent (`#407bff`) entirely.
- **`Scene.tsx` gains eleven keys**: `versions`, `retrieval`, `matrix`, `grading`,
  `subjects`, `stores`, `diagnose`, `tuning`, `assistant`, `sealed`, `cost`.
- **`schema.d.ts` regenerated** — it was 42 lines behind `openapi.json`, including the
  whole `SkillAgent` type.

**`Scene.tsx` is owned by Wave 0.** A lane that needs a new key requests it; it does not
edit that file, because four lanes editing one registry is four conflicts.

---

## The text rule — stated by the owner mid-pass, and it overrides the softer version

> *"No text bombs. Only what's needed, minimal text only, nothing extra. A clean UI is a
> must, with visuals."*

`02-REMAINING-PORTALS.md` said **prose → structure, nothing deleted**. That was right for
the `platform_admin` pass and it is now too weak. Relocating an essay into an `InfoTip`
produces a screen that measures clean and still reads heavy — a text bomb with a lid on it.

**The rule now: prefer deleting redundant prose over relocating it.**

- **One line, not three.** `Absence` — one clause of `why`, one of `needed`. `Receipt` —
  origin, then stop. `InfoTip` — one sentence; a second only if it carries a constraint the
  reader cannot infer.
- **No section-intro paragraphs.** A card title plus a chart is complete. Never add a
  sentence explaining the chart underneath it.
- **No restatement.** If a `StatCard` label says "Coverage", nothing below it says "this is
  the coverage". A scene beside a sentence that says the same thing stays silent — which
  `Scene` already is, being `aria-hidden`.
- **If a panel exists only to hold one sentence, delete the panel.**

The target reading order on every screen: **visual → numeral → one line of provenance →
nothing else.** If it is unclear whether a sentence earns its place, it does not.

**This does not weaken the honesty rules.** A stated absence is still stated and no figure
is ever invented. Say it shorter; do not stop saying it.

---

## Responsive fit — the owner reported it as broken, and the shell was half the cause

> *"Screen fit is not right. Ensure it adapts to the screen size of the device being used."*

**Shell, fixed in this pass.** `app/app/[role]/layout.tsx` capped content at `max-w-7xl`
(**1280px**) while DESIGN.md §4 specifies **1440px** — which is why `McpConsoleView`
declares its own `max-w-[1440px]` that the shell was silently overriding. A screen asking
for a width the shell would not grant, and two different effective content widths
depending on which screen you were on. Now `max-w-[1440px]`, with `min-w-0` on the
scroller.

The shell only guarantees the frame. A screen can still blow it out from the inside:

- **`min-w-0` on every flex/grid child that can hold wide content.** A grid child defaults
  to `min-width:auto`, so one long table row, one unbroken id, one long model name sets the
  track width and the *page body* scrolls sideways instead of the table. **This exact
  defect has landed four separate times in this codebase.** Invisible at 1440; the whole
  experience at 390.
- **Wide tables live in `DataPanel`**, never a bare `CardBody`.
- **Charts are fluid** — `ResponsiveContainer`, percentage width, fixed `height`. Never a
  hardcoded pixel width.
- **Container queries inside a narrow column.** `lg:grid-cols-2` on a component that
  renders inside a half-width card is wrong: the viewport is wide while the column is not.
- **No fixed heights that strand content.** `h-[380px]` on a panel that may hold more gives
  dead space on one device and clipping on another.
- **Long strings get `truncate` or `break-words`.**

**The check, at 390 / 834 / 1440 / 1920:**
`document.documentElement.scrollWidth === window.innerWidth`. A horizontal body scrollbar
anywhere is a failure. Note the fix is never `overflow-x:hidden` on the body — that hides
the defect rather than removing it.

---

## The constraint that shapes every lane: the chart ceiling

Surveying all thirteen screens produced one finding that matters more than any layout
decision: **most of these screens have no time-series in their payload.** Adding a trend
line would mean inventing one, on a product whose entire pitch is that it does not.

| has a real series | screen | the series |
|---|---|---|
| yes | `llmops` | `OpsEvalRow.score` by `ts`, 200 rows — already drawn by `EvalTrend` |
| yes | `memory` | `getMemoryWrites` — `op` + `ts`, a genuine event stream |
| within one run | `harness` | `nodeLedger[]` — duration / tokens / cost per node, ordered |
| within one clip | `voice` | `segments[]` — `start`/`end`, time-aligned |

Everything else is **snapshot or categorical**: composition, comparison and distribution
marks only — donut, ranked bars, stacked bar, gauge, bullet. Three screens (`graph`,
`vision`, `rag`) are close to chart-complete already and their lever is illustration and
layout, not new charts.

**`cache` deliberately gets no new chart.** Its API returns counters with no history; a
series would have to be accumulated client-side, which is a new claim about what the page
measures. It already draws two honest bar visualisations.

**Rules, non-negotiable:**
- No fabricated trend lines. No dual axis. No rainbow.
- **Three categorical series is the ceiling** — `#175cd3 ↔ #1570ef` measures ΔE 6.4
  against a floor of 15. A fourth folds into `Other (n)`.
- `--blue-600` is a fill/border/ring step, **not** a small-text step. Small blue text is
  `--blue-700`.
- Colour comes from `chartHex(signal)` / `rampHex(i,n)` in `charts/palette.ts`.
- Status hues always ship with an **icon and a word**.
- No progress-bar lists standing in for charts — rejected twice already. A chart has an
  axis you can read values off.
- Container queries, not viewport breakpoints, inside a narrow column — that has caused
  four separate defects here.

---

## The composition recipe, from the finished screens

`PageHeader` → **hero visual or KPI band** → the thing you act on → charts in an
asymmetric grid → tables. Page shell is `flex flex-col gap-4` (JobsView) or
`space-y-6` (AdminCommandCenter).

- **`DataPanel` for every wide table.** Its scroll container cannot widen the page; a bare
  `CardBody` lets a 900px table scroll the *document* sideways — invisible at 1440 and the
  whole experience at 390.
- **`StatCard`** carries icon, tone, `trend` sparkline and `source` receipt. Guard the
  sparkline: a constant or <2-point series passes `undefined` and draws nothing.
- **`Figure`** for every numeral. One `display` size per screen.
- **`Receipt`** closes every chart and figure card.
- **`InfoTip`** carries mechanism prose — including inside table headers.
- **Three-tier honesty in a chart card:** data → chart; partial → one-line `<Empty>`;
  none → `<Absence figure/why/needed>`, never a spinner that never resolves.
- **The `PipelineIso` duality is the pattern to copy** where a visual leads: the picture
  *and* the same facts as an accessible list beside it.

---

## The lanes

Three concurrent agents maximum. Directories are disjoint, so no two lanes touch a file.

**Nobody creates files in `charts/`, `shared/`, `ui/`, `primitives/` or `illustration/`.**
The kit is complete — `AreaChart`, `BarChart`, `StackedArea`, `DonutChart`, `RankedBars`,
`NodeGantt`, `ConformalBand`, `ShapWaterfall`, `Gauge`, `MiniTrend`, `BentoGrid`,
`KpiHero`, `ComparisonCard`. A lane needing something new reports it instead of building it.

### Wave 1

**Lane A — Measurement** · `evals/`, `ml/`, `harness/`
- **evals** — the metric×case grid is the screen. `RankedBars` of value-vs-threshold with
  the threshold as a reference line; the per-case table becomes a real matrix. Scene
  `matrix` on the "Answer relevancy — not computed" card, which today is a near-empty
  dashed box; scene `grading` on the empty per-case state. `EvalCaseResult.passed` and
  `EvalMetricConfig.computed` are in the type and never read.
- **ml** — the find: `conformal_coverage_empirical`, `metric_value`, `metric_name` and
  `test_size` are in `ModelCardResponse` and **nothing renders them**. Target-vs-measured
  coverage is the honest headline; its null case is a textbook `Absence`. Ensemble
  `weight` → `DonutChart`. `imputed_features` / `unknown_features` are the machine-readable
  honesty signal and are invisible today — surface them as a `Receipt`. Replace the
  hand-rolled `<table>` with `DataPanel`.
- **harness** — `nodeLedger` is a real ordered series and `NodeGantt` already exists,
  unused here. Lead with it. `config.effective` is fetched and never rendered. Scene
  `tuning` on the config panel, `empty` on "No run yet" (a full-width dashed box on first
  load — the biggest dead space on the screen).

**Lane B — Safety & cost** · `guardrail/`, `ops/`, `gateway/`
- **guardrails** — richest untapped payload of the three. `RedteamReportResponse.categories`
  → ranked bars (today only `MiniMeter`); `overall.blockRate` → `Gauge` against
  `thresholds.minBlockRate`; **counts by `attacks[].layer` — which rail actually caught it —
  is a real distribution rendered nowhere.** Posture `entries[].status` → an enforced /
  partial / not-covered roll-up. Scenes `security` and `sealed` exist and are unused here.
  Prose is already clean; this is a visuals-only lane.
- **llmops** — already has the one true time-series (`EvalTrend`). Give it the
  `charts/` treatment, `Absence` instead of the bare "No scores yet" div, and
  `OpsParamsResponse` thresholds as reference lines. Prompt versions by status, pending
  releases by risk tier. Scene `versions` — a branching commit graph, which is literally
  the prompt lifecycle — and `diagnose` on the diagnose panel.
- **tokenopt** — prose pass already done and confirmed clean; **this lane is pure
  visuals.** `summary.by_role` is fully chartable and today is two hand-rolled `<table>`s:
  cost by role, calls by role, prompt-vs-completion split, small-vs-frontier share
  (`Gauge` on `small_model_share`), actual-vs-baseline cost. Both tables → `DataPanel`.
  No time-series exists — composition marks only. Scene `cost`.

**Lane C — Knowledge** · `retrieval/`, `graph/`, `memoryctl/`, `memory/`
- **memory** — the richest untapped numeric surface in the portal, and it uses *zero*
  `InfoTip`/`Receipt`/`Absence` today. `fact_count` / `session_count` per subject;
  `MemoryFactRow.confidence` × `importance`; `getMemoryWrites` op-counts over time (a real
  series); `getMemoryRetention.at_risk` breakdown; `getRecallDebug` scores. Scene
  `subjects` on the "choose a subject" state.
- **rag** — already draws `RankedBars` via `ProvenanceDonut` and already refuses to draw
  per-arm counts that do not exist (`arms[].candidates` is hardcoded `0` — leave that
  refusal intact). Zero prose. Lane is: the bespoke dashed empty box becomes
  `SceneState name="retrieval"`, and the rerank scoreboard gets the chart-card treatment.
- **graph** — `GraphResponse` has **no numeric fields at all**; every number is computed
  client-side. Honest charts: degree distribution, node-count-per-kind. Both empty states
  (entities, relations) become scenes. Prose is already correctly in `InfoTip`s.

### Wave 2

**Lane D — Multimodal** · `vision/`, `voice/`
- **vision** — single-run snapshot. Truthful: prompt-vs-completion token split, PII region
  confidence scores and areas. The trailing footnote splits into `Receipt` (mime, size,
  provenance) + `InfoTip` (the attacker-controlled-declared-type mechanism). Scene `upload`
  before an image, `empty` after. `PIIOverlay` already draws real bounding boxes — leave it.
- **voice** — `segments[]` is the one true within-clip series and **is not drawn anywhere**;
  `Waveform` already has an unused `marks` prop for chunk boundaries. Note the fleet's
  Whisper reports no confidence (`has_confidence`), so that stays an `Absence`. Already has
  one scene; the "rails blocked the transcript" state is the second slot.

**Lane E — Runtime** · `sim/`, `cache/`
- **simulation** — two `AgentTracePanel`s at a fixed `h-[380px]` mean **760px of empty
  vertical space on first paint**, the worst dead space in the portal. Scene `exercising`
  (literally "a run being exercised against a scenario") pre-run. The two lanes produce
  structurally identical series, so a genuine A/B comparison — node duration or cumulative
  cost, ops vs client — is available and offered by no other screen.
- **cache** — **prose judgement confirmed independently: leave it.** Every long block is
  already inside `InfoTip`, `Absence`, `Receipt` or `EmptyState`; there is no bare `<p>` of
  relocatable prose in the file. No new chart (no history in the payload). This lane is
  narrow: `DataPanel` where a table wants it, scene `stores` on the pre-first-lookup
  `Absence`, and nothing else.

---

## Verifying

```bash
cd web && npx tsc --noEmit && npx next lint --dir src && npm test \
  && AEGIS_DIST_DIR=.next-verify npx next build
```

Baselines as of 2026-08-21: **231 tests · 67/67 pages**, tsc and lint clean.

Then load every screen at **1440 and 390** signed in as `northwind.analyst` (the
un-tenanted `ai` account has no tenant, so every tenant-scoped screen is correctly empty
and looks broken), and confirm `documentElement.scrollWidth === innerWidth` on each.

`/web-interface-guidelines` is run by the orchestrator, not a lane — it is a user-level
command subagents cannot invoke, and every previous lane applied its rule list by hand
instead.

**The gate: build → verify on a quiet tree → audit → fix → then push.** Never push
before the audit.

---

## The RBAC gap — found by the sweep, not fixed, and it is the owner's call

Four endpoints refuse the `ai_team` role that `ROLE_SECTIONS.ai_team` offers a nav entry
for. The backend is untouched by this pass, so this predates it.

| endpoint | screen | un-tenanted `ai` | tenant-bound `northwind.analyst` |
|---|---|---|---|
| `/v1/security/posture` | guardrails | **403** | **403** |
| `/v1/platform/caches` | cache | 200 | **403** |
| `/v1/llmops/prompts`, `/v1/llmops/runs` | llmops | 200 | **403** |

Two separate faults:

1. **`require_admin_or_devops` on `/security/posture` excludes `ai_team` entirely**, so
   half the guardrails screen is dead for *every* account in the portal.
2. `require_infra_reader` and `require_llmops_operator` admit `ai_team` **only via
   `is_platform_staff()`** — an un-tenanted account. So the tenant-bound analyst, the
   account with data, is refused; and the un-tenanted account that is admitted shows
   empty tenant-scoped screens everywhere else.

**No single `ai_team` login demonstrates the portal.** That is the thing to weigh before a
jury demo.

This is exactly the "gap wearing a menu entry" that `web/src/lib/portal.ts`'s own docstring
warns against, and `backend/tests/api/test_route_coverage.py` — which reads that file and
asserts every section renders a live surface — does not catch it, because it checks that a
route exists rather than that this role may call it.

**Not fixed here, deliberately.** Widening those guards changes who may read system
prompts, security posture and cache internals. That is a security boundary and a product
decision, not a presentation fix.
