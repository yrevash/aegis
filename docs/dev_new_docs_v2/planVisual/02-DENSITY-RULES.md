# The density pass — mechanical rules

Seven or eight senior reviewers said the application shows **too much data, too complex, not good
UX**. This document turns that into rules an implementer can apply without judgement calls.

> **The decision that bounds everything here: calm it, don't remove it.** Every screen stays. Every
> number stays. Every `Receipt` stays. What goes is restatement, essays, and prose standing where a
> mark belongs.

---

## 1. This is a compliance gap, not a missing rule

DESIGN.md already forbids what the reviewers complained about.

**§9 Anti-slop:** *"a paragraph where a chart, a badge or a tooltip would do"* · *"a status told in
prose when a strip, a tile or a bar would tell it in one glance"* · *"excess cards"*.

**§4 Density:** *"density is not the same as text"* · *"Prose belongs in an `InfoTip`, not on the
page"* · an `Absence` is *"one line, not three"* · *"A state is drawn before it is described."*

The measured reality:

| | Count | Rule it breaks |
|---|---|---|
| InfoTips | **184** (3,385 words); **56 are ≥25 words**, worst is **95** (`admin/DelegationMap.tsx:203`) | §4 — relocation is not deletion |
| `Absence` boxes | **99**, each rendering **three `<p>`s** (`primitives/Receipt.tsx:106-115`) | §4 — "one line, not three" |
| Nav tooltips | **34**, mean **28.5 words**; `compliance` is **129 words in a `title=` attribute** | invisible by construction |
| Screens with **no chart at all** | **23 of 35** | §9 |
| Card sites | 42 on `documents`, 28 on `jobs`, 24 on `stack`, 15 on `AdminCommandCenter` | §9 "excess cards" |

The repo already diagnosed it, in `docs/dev_new_docs_v2/frontend-redesign/03-AI-TEAM-PASS.md`:

> *"Relocating an essay into an `InfoTip` produces a screen that measures clean and still reads
> heavy — **a text bomb with a lid on it**."*

Previous passes moved 143 prose blocks into 15 on `platform_admin` — but into 184 InfoTips and 99
three-paragraph `Absence` boxes rather than deleting them.

---

## 2. The rules

### R1 — InfoTip ceiling: 25 words

56 currently exceed it. **Over the ceiling → cut, not relocate.** If the mechanism genuinely needs
500 words, it belongs in `docs/`, linked — not in a hover.

Worst offenders: `admin/DelegationMap.tsx:203` (95 w), `db/SchemaMap.tsx:791` (74 w),
`mcp/AegisMcpPanel.tsx:128` (71 w), `mcp/ToolGovernance.tsx:439` (65 w), `db/SchemaMap.tsx:122`
(62 w), `audit/AuditInsights.tsx:174` (55 w).

### R2 — `Absence` is one line — fix the primitive, not the 99 call sites

`primitives/Receipt.tsx:106-115` renders `figure` + `why` + `needed` as three stacked `<p>`s.
DESIGN.md §4 says one line. **Changing the primitive fixes all 99 instances at once** — the single
highest-leverage edit in this document.

Keep `figure` and `why`. `needed` collapses into the same line or a title attribute.

### R3 — Nav tooltips: 12 words

34 average 28.5. A 129-word `title=` attribute is functionally invisible and pure maintenance cost.
Source: `SECTIONS[].tooltip` in `web/src/lib/portal.ts`.

### R4 — No section-intro paragraphs

A card title plus a chart is complete. If a panel exists only to hold one sentence, delete the
panel. `mcp/HowMcpWorks.tsx` is a component whose entire job is explanation.

Delete duplicated prose outright: `skills/SkillsDrawer.tsx` and `skills/SkillsPanel.tsx` carry the
same 43-word paragraph verbatim.

### R5 — Charts before prose, on the demo path only

**Not** all 23 chartless screens. Several are honestly tables and must stay tables — `compliance`,
`approvals`, `settings`, `roles`. Forcing a chart onto them would be decoration, which §9 also
forbids.

---

## 3. What is load-bearing and must survive

This is the rule that stops "calm it" sliding into "gut it". **The honesty text is the product's
thesis and the jury rubric rewards it.**

Keep every `Receipt` — one mono line naming a figure's origin:

```jsx
<Receipt origin="usage_ledger · univariate · statsforecast" detail="n=412" />
```

Keep every stated absence that names a real gap. Examples worth protecting verbatim:

- `latency/LatencyView.tsx:240` — *"The latency window is per-process and resets on restart, and no
  run has completed in this one yet."*
- `cache/CacheView.tsx:375` — *"Cache counters are process-wide — one figure over every tenant that
  shared the worker — so they are not this tenant's to read."* This refusal **is** the isolation
  demo.
- `db/SchemaMap.tsx:319` — a column *"withheld by a Postgres COLUMN grant, not filtered by
  application code on the way out."*
- `forecast/CoverageMeter.tsx:50` — asked 90%, measured 67%.

**Cut restatement, never provenance.** `04-FINAL-PORTALS-PASS.md` already flagged the shape:
*"'Why this matters' restates its own label then restates the card below it"*, and *"Two InfoTips
say nearly the same thing — delete one."*

---

## 4. Priority — the 7 screens a demo actually reaches

A 5–10 minute demo shows **7 of 35** screens:

**Overview · Console · Approvals · Database · Savings/Forecast · Guardrails · Access demo**

Those get the full pass. The other 28 get R2 and R3 **for free** (both are primitive- and
config-level) and nothing else.

Landing screens by portal (`portal.ts:498` `defaultSectionFor`):

| Portal | Lands on |
|---|---|
| platform_admin · tenant_admin | `dashboard` → `AdminCommandCenter.tsx` (15 card sites, 14 panels) |
| **ai_team · client** | **`console`** → the 48-file, 10,412-LOC tree |
| devops | `dashboard` → `OpsOverview` |

---

## 5. Known demo-fatal items — fix before any polish

Found by the persona audits. These are not density problems; they are correctness problems that a
jury will hit first.

1. **`GET /v1/metrics` returns byte-identical responses to `northwind.client` and
   `vertex.client`** and the client Overview presents it unlabelled — so `COST SAVED`, `LLM CALLS`,
   `QUALITY`, `P95` and `CACHE HIT` all read as the client's own. Deliberate at the API; misleading
   on the page. *A jury comparing two tenants lands here first.*
2. **Vertex has no ingestible corpus** — three metadata-only seed stubs, so any retrieval demo as
   `vertex.client` returns nothing.
3. **Northwind's corpus is full of test uploads** (`notif-live-*`, `zz-markall-*`) which rank top on
   client runs — a jury reading the sources sees `notif-live-1787432237982` where a policy document
   belongs.
4. **Forecast currently refuses to draw** — 2 distinct ledger days against the 71 it needs.
5. `layout/TrustBar.tsx` is dead code, mounted nowhere.

---

## 6. Dead space named by previous passes

Worth fixing while in the area, all previously measured:

- **Simulation** — two `AgentTracePanel`s at fixed `h-[380px]` = *"760px of empty vertical space on
  first paint, the worst dead space in the portal."*
- **Redteam** — *"with no run, ~70% of the page is blank"*, and `CategoryBar` is the
  progress-bar-as-chart pattern *"rejected twice already."*
- **Patch** — when `online:false` it renders an all-grey bar over "registry did not answer" with no
  absence state: *"it reads as broken on the one screen whose entire subject is honesty about
  staleness."*
- **Security** — `SignalGrid` is ten uniform boxes each holding a 1–3 word badge, *"a lot of area
  for very little."*
- **Risk** — no empty state exists at all; an empty `risks[]` renders an empty list silently.
- 22 hardcoded `h-[NNNpx]` across 17 files.

---

## 7. Do not create new primitives

`03-AI-TEAM-PASS.md`: *"**Nobody creates files in `charts/`, `shared/`, `ui/`, `primitives/` or
`illustration/`.** The kit is complete."*

Available and already consistent: 9 chart components + `Gauge` (6–16 consumers each),
`palette.ts` (`chartHex`, `rampHex`, `ORDINAL_RAMP`), `KpiHero`, `MiniTrend`, `BentoGrid`,
`StatDelta`, `CountUp`, `Figure`, `Receipt`/`Absence`, `InfoTip`, `PageHeader`, `SectionHeader`,
`States`, `Card`/`DataPanel`/`StatCard`/`Table`/`Badge`, and 67 licensed illustrations behind a
semantic `Scene` router.
