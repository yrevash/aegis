# `tenant_admin`, `client`, `devops` — the last eight screens

Written 2026-08-21 from a full read of all eight, after the `ai_team` pass shipped.
Hand this to an implementing agent alongside `00-DESIGN-DIRECTION.md` and
`03-AI-TEAM-PASS.md` (whose **text rule** and **responsive fit** sections apply verbatim
and are not repeated here).

These eight are what remains once `ai_team` is done. Everything else in the three portals
is a screen `platform_admin` or `ai_team` already finished and they inherit for free —
`tenant_admin` has **one** unique screen left, `client` three, `devops` five.

---

## Two inherited claims this survey corrected

Both came from `02-REMAINING-PORTALS.md` and both were wrong. Recorded because acting on
them would have wasted a lane.

1. **`redteam` does not have "2 scenes and 7 `DataPanel`s".** Measured: **one**
   `SceneState` and **three** `DataPanel`s. It also draws **zero** `charts/` components.
2. **`security`'s "leave it alone" verdict is right, but its stated reason was not.** The
   reason given was that its blocks are `Receipt`/`Absence` evidence. In fact the screen
   has **no `Absence` at all** and exactly **one `Receipt`**; its prose count is low
   because an earlier pass already moved 17 per-row `entry.detail` paragraphs into
   `InfoTip`s (the file says so at lines 81–86). Same conclusion, different fact — and the
   difference matters, because "already relocated" leaves visual work outstanding while
   "it is evidence" would not.

---

## The chart ceiling, again — and one screen that breaks it

Seven of the eight have **no time-series whatsoever**. `savings`, `risk`, `stack`,
`patch`, `security` and `latency` each carry a single scalar `generated_at` /
`checked_at` stamp or none at all. A trend line on any of them is fabrication.

**`redteam` is the exception, and it is the biggest single win on the sheet.**
`getRedteamHistory` returns `RedteamRun[]`, each carrying `startedAt`, `blockRate` and
`falsePointRate` — a genuine block-rate-over-runs series with `minBlockRate` available as
a reference line. The screen draws no trend, while its own history empty-state body
**literally promises "a trend rather than a snapshot."**

`documents` has a softer case: `DocumentRow.created_at` supports an honest
arrivals-by-day histogram. It is a **count of arrivals**, never a metric trend — the
file's own JSDoc already forbids `StatCard trend` here and that stays true.

---

## The lanes

Three concurrent agents maximum, disjoint directories. Same rules as `ai_team`: nobody
creates files in `charts/`, `shared/`, `ui/`, `primitives/`, `illustration/`; nobody runs
git; presentation only.

### Lane A — `client/` · savings, risk
**`SavingsView.tsx` — the owner's named complaint, and the worst of the eight.**
`getSavings` → `saved_usd`, `baseline_cost_usd`, `actual_cost_usd`, `saved_pct`,
`generated_at`, `note`, `breakdown[] = {source, saved_usd, explanation}`. No time series.
It uses **no `DataPanel`, no `StatCard`, no shared `Figure`, no `Receipt`, no `Absence`,
no `Scene`** — it defines its own local `Figure`.
- **Delete three `InfoTip`s and the intro paragraph.** "Why this matters" restates its own
  label then restates the card below it; "About the breakdown" duplicates `ReconcileChip`;
  "About baseline vs actual" duplicates the bar's own `aria-label` and legend. Keep only
  the `KpiHero` baseline definition — it is the sole definition on the page.
- **The real text bulk is `row.explanation`** — one API paragraph per source, stacked
  under a share bar. Move each to an `InfoTip` on its row label; `SecurityView`'s
  `PostureRow` is the precedent.
- Footer `{note} · Computed {…}` is a hand-rolled mono `<p>` where `Receipt` belongs.
- Empty breakdown is a bespoke `h-[200px]` box with one sentence → `Absence` inside
  `SceneState name="cost"`.
- The per-row `ShareBar` duplicates what the `BarChart` above already encodes. **One of
  the two goes.** A `DonutChart` of `saved_usd` by `source` (share) beside the existing
  `BarChart` (magnitude) is the honest pairing.

**`RiskMap.tsx`** — prose pass landed; body prose is one paragraph. But two `InfoTip`s are
still text bombs with lids on: "How to read this" is **five sentences, ~520 chars, and
opens by restating its own label**. Cut to one sentence each. `RiskEntry` carries
`likelihood`/`impact` and `residual_likelihood`/`residual_impact`, all 1–5 — **a 5×5
likelihood × impact matrix is the one honest undrawn mark**, and `category` is grouped
nowhere. **No empty state exists at all**: an empty `risks[]` renders an empty dumbbell
list silently. Keep `RiskDumbbell` — it is well argued.

### Lane B — `devops/` + `security/` · stack, patch, security
**All three are already prose-clean. This is a pure visual lane.**

**`PatchCheck.tsx` — the biggest win here.** When `online: false` every row is `unknown`,
so the screen renders an all-grey `FreshnessBar` over a table of "registry did not
answer", **with no absence state anywhere**. It reads as broken on the one screen whose
entire subject is honesty about staleness. Give it a stated `Absence` + `Scene diagnose`.
No numerics and no series exist beyond the three-band composition already drawn.

**`StackVersions.tsx`** — `StackComponent` has no numerics at all beyond derived counts.
Honest marks: `DonutChart` by `category`, `RankedBars` by `aegis_module` — the
"modules powered" set is computed inline and **thrown away after `.size`**. Its
`EmptyState` is a bespoke icon-and-sentence box → `SceneState name="versions"`. Two
`InfoTip`s say nearly the same thing ("What this inventory is" / "How this list is
built") — **delete one**.

**`SecurityView.tsx`** — no de-prosing (see the correction above), but real visual work:
`entries[].status` composition and blocks-per-`module` (`module` is printed as text and
never aggregated). `refs[]` is in the type and never read. The `SignalGrid` is ten
uniform boxes each holding a 1–3 word `Badge` — a lot of area for very little. Only three
numbers exist on the whole screen (`max_plan_iterations`, `hazard_categories`,
`rls_tables`). **No timestamp anywhere, not even `generated_at`.** No absence state today.

### Lane C — `redteam/` + `latency/` + `documents/`
Effort is concentrated in the 728-line `redteam`; the other two are near-finished.

**`RedteamView.tsx` — the biggest win of all eight.**
- **Draw the block-rate trend** from `history[].startedAt × blockRate`, with
  `minBlockRate` as a reference line. The empty state already promises it.
- **`CategoryBar` is the rejected progress-bar-as-chart pattern** — replace with
  `RankedBars` against the floor.
- **`report.rails` (`{layer, blocks}[]`) is being rendered as a joined string** in the
  `actions` slot. That is `RankedBars`: blocks per rail.
- `report.unchecked[]` and `falsePositiveDetail[]` are typed and never rendered.
- The leaked-empty `EmptyState` is the money illustration slot (`sealed` or `redteam`).
- **With no run, ~70% of the page is blank** — that state needs a scene.

**`LatencyView.tsx`** — its empty state (`SceneState` wrapping `Absence` + `Receipt`) is
**the model the other seven should copy**; leave it. Real undrawn marks: `total_ms` per
node is a composition (`DonutChart` of where time goes) currently only a table column, and
`run_count` vs `window_capacity` is a bounded ratio → `Gauge`. `NodeRangeBars` is a
genuine bullet chart — keep it, but with 2–3 nodes that card is mostly whitespace.

**`DocumentsView.tsx`** — near prose-clean and already carries two scenes. Undrawn and
computed-but-discarded: status composition, `chunk_count` per doc, `parse_confidence`
distribution, and `shape.types`/`untyped` (computed, never rendered). The empty-corpus
message is a bespoke `<p>` pair that should be a real `Absence`. Two long `InfoTip`s to
trim to one sentence.

---

## Verifying

```bash
cd web && npx tsc --noEmit && npx next lint --dir src && npm test \
  && AEGIS_DIST_DIR=.next-verify npx next build
```
Baselines: **231 tests · 67/67 pages**, tsc clean, lint clean but for three pre-existing
`dashboard/` warnings.

Then the runtime sweep, per portal — this is what caught the real defects last time, and
what two lanes wrongly believed they could not run:

```bash
cd web && node scripts/shoot.mjs --portal client   --user northwind.client
cd web && node scripts/shoot.mjs --portal devops   --user devops
cd web && node scripts/shoot.mjs --portal tenant_admin --user northwind.admin
```

**The dev server is `:3001` and the backend is `:8110` — not `:8000`.** Two `ai_team`
lanes checked 8000, concluded no backend was up, and skipped verification entirely while
both services were running.

**The gate: build → verify on a quiet tree → audit → fix → then push.**
