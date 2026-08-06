# Frontend Redesign Spec — Aegis Console

> **Status:** contract for the build agents. This document is the source of truth for the
> visual-maturity pass described in `ENTERPRISE_MATURITY_PLAN.md` §5. Build agents implement
> to this spec; they do not re-derive it.
>
> **SCOPE (updated):** **Light theme only** and **desktop web only.** Dark mode and mobile
> responsiveness are OUT OF SCOPE — do not spend effort on `.dark` variants or small-screen
> breakpoints; optimize the desktop light experience. (Existing dark tokens may remain but
> need not be maintained; target a comfortable desktop width, no mobile layout work.)
>
> **The critique we are solving (verbatim from the user):**
> **(a) too much text** · **(b) reads too "AI-ish" (jargon)** · **(c) too linear** (everything
> is a stacked full-width row). Target: a **premium, calm, executive SaaS product** with
> **varied non-linear layouts, real charts, and tasteful motion** — quality bar: the "Saasto"
> Figma template, and the craft of Linear / Vercel / Stripe dashboards.
>
> **Hard constraints (do not break):**
> - Stack is fixed: React 19 + Vite + **Tailwind v4 (CSS-first, tokens in `index.css`)** +
>   **recharts 3** + shadcn-style primitives in `components/ui/` + lucide icons + sonner.
> - **No new animation library.** Motion = CSS keyframes (`tw-animate-css` + `index.css`) +
>   RAF hooks + recharts' built-in `isAnimationActive`. `framer-motion` is **not** installed
>   and must **not** be added.
> - **No fakes.** Every "sample"/illustrative number keeps its `sample` marker. Branding renames
>   the *presentation*; the honest underlying tech is always one hover/subtitle away.
> - Keep the suite green: `pnpm build && pnpm lint && pnpm test` must pass after every surface.
> - Light **and** dark must both work (tokens already dual-mode). Respect
>   `prefers-reduced-motion` (the reset block in `index.css` already neutralises keyframes —
>   new RAF/IntersectionObserver motion must check `prefersReducedMotion()` from
>   `components/console/motion.ts`).

---

## 1. Design principles

The eight rules every surface is measured against. If a change doesn't serve one of these, don't make it.

1. **Numbers and visuals lead; prose follows.** The first thing on any surface is a figure, a
   chart, or a state — never a paragraph. Target ≤ 12 words of body copy visible per card before
   an interaction. (Linear/Stripe "north-star metric up top, detail on demand".)
2. **Prose moves to tooltips and expandables.** Every explanatory sentence currently rendered
   inline becomes either an `InfoTip` (ⓘ hover) or an expandable "Technical detail" disclosure.
   The glass-box depth stays — it just lives **one layer down**, not on the primary surface.
3. **Short, professional labels.** Panel titles are 1–3 words. No sentences as headings, no raw
   API routes (`POST /ops/diagnose`, `GET /metrics · 4s`) on primary surfaces — those move to a
   tooltip/footer or the dev console.
4. **Drop AI jargon from primary surfaces.** Words that must **not** appear as primary labels:
   *conformal, SHAP, RRF, BM25, bitemporal, episodic/semantic/procedural (as bare headings),
   trajectory, deferral signal, chokepoint, principals, dual verdict, rerank, autonomy band.*
   Each has an approved plain-language replacement (§3) with the honest term in its tooltip.
5. **Non-linear bento layouts.** No surface is a single `flex-col` of full-width rows. Every
   surface uses a **12-column bento grid** with tiles of varied span (hero tiles 6–8 cols × 2
   rows; support tiles 3–4 cols) so the eye moves in two dimensions. Max **1–2 hero tiles per
   viewport** (bento best practice: size = importance, not content volume).
6. **Real charts, not CSS meter bars.** Where a trend, share, distribution, or funnel is being
   shown, use a recharts component (we already have `AreaChart`, `BarChart`, `DonutChart`,
   `LineChart`, plus `Sparkline`). Hand-built `<div style={{width}}>` meters are replaced with
   `MiniTrend`, `DonutChart`, or `RadialBar` unless the bar is a genuine single-value gauge.
7. **Tasteful, purposeful motion.** Four sanctioned motion moments only: **count-up** on KPI
   mount, **reveal-on-scroll** (staggered) for bento tiles entering, **section cross-fade** on
   tab switch, **chart draw-in**. Everything else (hover lift, focus ring) is a ≤200ms
   transition. No infinite gratuitous loops on primary surfaces except the live-run pulse.
8. **Calm & premium, dual-theme, responsive, accessible.** Restrained colour (signal hues carry
   *all* semantic weight, à la Vercel's near-monochrome shell); generous whitespace; one
   elevation system. Every tile reflows to 1-col on mobile. All interactive elements keep the
   six microstates (default/hover/focus/active/disabled/loading) the primitives already provide;
   colour is never the only signal (pair hue with icon/label).

---

## 2. Visual system

Refine the existing `index.css` tokens — **do not reinvent them.** Every token below either
already exists or is an *additive* new variable. Signal palette, radii, and dark mode are kept
as-is; we add a type scale, an elevation set, and motion tokens.

### 2.1 Type scale

We already ship four font families via `@fontsource`; only two are wired. **Promote
`Space Grotesk` (already installed) to `--font-display`** for hero numbers and panel titles —
it gives the "premium, non-default" feel the brief asks for, while Inter stays for body/UI and
JetBrains Mono for IDs/raw data. This is a one-line change in the `@theme inline` block.

```
--font-display: 'Space Grotesk', 'Inter', ui-sans-serif, system-ui, sans-serif;  /* was Inter */
--font-sans:    'Inter', ui-sans-serif, system-ui, sans-serif;                    /* unchanged */
--font-mono:    'JetBrains Mono', ui-monospace, monospace;                        /* unchanged */
```

Add a semantic type scale as utility classes in `@layer utilities` (rem, 1.25 ratio, tight
display leading). Numbers use `.tabular` (exists) so they don't jitter.

| Token / class     | Size / line-height        | Weight | Use                                   |
|-------------------|---------------------------|--------|---------------------------------------|
| `.t-hero`         | 3.0rem / 1.0 (`clamp` 2.25→3.5) | 600 (display) | KpiHero number, present-mode figure |
| `.t-metric`       | 1.75rem / 1.0             | 600 (display) | KpiTile value (current size — keep)   |
| `.t-title`        | 1.125rem / 1.4           | 600 (display) | Card / panel title                    |
| `.t-body`         | 0.875rem / 1.5           | 400 (sans) | Body copy (kept short)                |
| `.t-label`        | 0.8125rem / 1.4          | 500 (sans) | Field labels, table headers           |
| `.eyebrow` (exists)| 0.68rem, 0.16em tracked, mono | 500 | Section eyebrow / kicker             |
| `.t-mono`         | 0.72rem / 1.4 mono       | 400    | IDs, trace ids, raw values            |

### 2.2 Spacing & layout scale

4px base (Tailwind default). Bento canon:

- **Grid:** 12 columns, `gap: 1rem` (16px) mobile → `1.25rem` (20px) ≥ lg. `BentoGrid` owns this.
- **Tile padding:** `1.25rem` (20px) standard; `1.5rem` (24px) for hero tiles.
- **Section rhythm:** `2rem` (32px) between major bento blocks; `0.75rem` (12px) title→content.
- **Max content width:** 1440px (present mode 1900px, already set in `AppShell`).
- **Tile spans:** hero = `col-span-12 lg:col-span-8 row-span-2`; primary = `lg:col-span-6`;
  support = `lg:col-span-4`; compact stat = `lg:col-span-3`. Everything collapses to
  `col-span-12` below `lg`.

### 2.3 Elevation & border tokens

Keep `--radius: 0.75rem` and `.shadow-card` (the resting card shadow). Add two states:

```css
/* additive, @layer utilities */
.shadow-hover { box-shadow: 0 2px 4px rgba(16,24,40,.05), 0 16px 40px -16px rgba(16,24,40,.18); }
.shadow-pop   { box-shadow: 0 4px 8px rgba(16,24,40,.06), 0 24px 60px -20px rgba(16,24,40,.22); } /* hero tiles, dialogs */
```
- Resting tile: `border border-border shadow-card` (unchanged Card default).
- Interactive tile hover: `-translate-y-0.5 shadow-hover` over `transition-transform,box-shadow`.
- Hairline dividers inside tiles: `border-border/70` (already the convention).
- Dark mode inherits automatically — shadows are subtle on the dark base; add a `1px` inner
  `border-white/5` top-edge on hero tiles in dark for the "lit glass" look (optional, `.dark` only).

### 2.4 Chart palette

Charts already resolve through `chartHex(signal)` and `--chart-1..5`. **Rule: charts only use
the signal palette** (no new hues), so every visual stays inside the trust taxonomy:

| Series role                | Signal token | Light hex | Notes                                  |
|----------------------------|--------------|-----------|----------------------------------------|
| Primary / totals / spend   | `neutral`→`--chart-1` (`#101828`) | near-black | the dominant line, à la Vercel mono   |
| Retrieval / volume / graph | `graph` (`#1570ef`) | blue | secondary series, bars                 |
| Reasoning / agent          | `agent` (`#0e9488`) | teal | agent-owned series                     |
| ML / prediction            | `ml` (`#7a5af8`) | purple | ML trend / band                        |
| Healthy / pass / savings   | `ok` (`#12b76a`) | green | positive deltas, savings               |
| Risk / gate                | `risk` (`#dc6803`) | amber | pending, warn                          |
| Block / danger             | `block` (`#d92d20`) | rose | blocked, error                         |

Conventions: area charts use a top-down gradient fill at 18%→0% opacity of the series hue
(AreaChart already does this); grids are `--border` at 1px; axes use `--muted-foreground`;
donuts keep the `innerRadius 62% / outer 88%` we ship. Tooltips use the shared `ChartTooltip`.

### 2.5 Motion tokens

Add as CSS variables (consumed by transitions) + document the RAF/keyframe durations. All are
neutralised by the existing `prefers-reduced-motion` block; RAF/observer code must additionally
guard with `prefersReducedMotion()`.

```css
:root {
  --dur-fast: 120ms;   /* hover, focus, small state flips           */
  --dur-base: 200ms;   /* default transition, tab underline         */
  --dur-slow: 320ms;   /* section cross-fade, tile reveal           */
  --dur-count: 900ms;  /* KPI count-up (RAF)                        */
  --ease-out: cubic-bezier(.22,1,.36,1);      /* emphasized decel   */
  --ease-inout: cubic-bezier(.65,0,.35,1);    /* symmetric          */
}
```

| Interaction              | Technique                             | Duration / easing        |
|--------------------------|----------------------------------------|--------------------------|
| KPI number mount         | `CountUp` (RAF, easeOutCubic — exists) | `--dur-count`            |
| Bento tile enter         | `RevealOnScroll` (IntersectionObserver) → `.animate-trace-in`-style fade+rise, **staggered 40ms** by tile index | `--dur-slow` `--ease-out` |
| Tab / section switch     | keyed remount + fade-in (opacity 0→1, translateY 4px→0) | `--dur-slow` `--ease-out` |
| Chart draw-in            | recharts `isAnimationActive` + `animationDuration={700}` | 700ms                    |
| Tile hover lift          | `transform` + `box-shadow`             | `--dur-base` `--ease-out` |
| Live-run event           | `.animate-beat` / `.animate-pip` (exists) on active node | 0.9s one-shot            |
| Live trace row append    | `.animate-trace-in` (exists)           | 0.28s                    |

**Do not** add: parallax, infinite marquees, scroll-jacking, or motion on static text.

---

## 3. Copy & branding

### 3.1 Tone shift

From *AI-lab narration* → *executive product copy*. Concretely:

- **Cut the meta-narration.** Phrases like "the money-shot", "the dramatic in-run gate", "This
  wide band is why I stopped", "one system, two principals", "the glass-box on retrieval" are
  removed from the UI (they can live in code comments / docs).
- **Verbs and outcomes, not mechanisms.** "Conformal deferral signal" → "Confidence"; "the
  harness scoring its own traces" → "Quality checks"; "enforced inward at the model chokepoint"
  → "Enforced on every request".
- **One honest subtitle.** Wherever a branded module name appears, pair it with the real tech in
  a muted subtitle or tooltip (never hide it — the no-fakes bar). Pattern:
  `Title: "Retrieval"  ·  subtitle/tooltip: "Hybrid search — vector + graph + keyword"`.

### 3.2 Aegis module label map (nav + panel titles)

Source: `ENTERPRISE_MATURITY_PLAN.md` §1. Left = what the UI shows; the **honest tech** is the
tooltip/subtitle. Nav labels stay short; module names surface in panel titles.

| Surface / panel (current)          | New label (primary)   | Aegis module    | Honest subtitle / tooltip                          |
|------------------------------------|-----------------------|-----------------|----------------------------------------------------|
| Nav: "Live console"                | **Console**           | Aegis Router    | Multi-agent orchestration · LangGraph              |
| Nav: "Dashboard"                   | **Overview**          | —               | Operations & value at a glance                     |
| Nav: "Memory"                      | **Memory**            | Aegis Memory    | Long-term memory · Postgres + pgvector             |
| Nav: "Simulation"                  | **Access demo**       | Aegis Governance| Same query, two roles · RBAC + retrieval scope     |
| Nav: "Self-improvement"            | **Improvement**       | Aegis Loop      | Self-improving prompts · trace→eval→release        |
| Nav: "Approvals inbox"             | **Approvals**         | Aegis Tools/MCP | Human gate on risky actions                         |
| Nav: "Admin settings"              | **Governance**        | Aegis Governance| Tenants · budgets · usage · RBAC                    |
| Nav: "Audit trail"                 | **Audit**             | Aegis Trace     | End-to-end trace · OpenTelemetry → Phoenix         |
| Console panel: OrchestrationMap    | **Orchestration**     | Aegis Router    | Which agent handled each step · LangGraph          |
| Console panel: RerankScoreboard    | **Sources**           | Aegis Retrieval | Hybrid search + rerank · vector + graph + keyword  |
| Console panel: DualVerdict         | **Decision**          | Aegis Signal    | ML confidence + human gate on tool risk            |
| Console panel: ConformalInterval   | **Confidence**        | Aegis Signal    | Calibrated interval · conformal (MAPIE)            |
| Console panel: ShapPanel           | **Why**               | Aegis Signal    | Top drivers of the score · SHAP                     |
| Console panel: GuardrailReveal     | **Guardrails**        | Aegis Guardrails| Injection · PII · schema checks                     |
| Console panel: AgentTracePanel     | **Activity**          | Aegis Trace     | Step-by-step run log                                |
| Ops panel: EvalTrend               | **Quality trend**     | Aegis Evals     | Answer / retrieval / tool / guardrail scores        |
| Ops panel: PendingReleases         | **Release gate**      | Aegis Loop      | Low-risk auto-ships · high-risk waits for approval  |
| Memory panels (3 tiers)            | **What we know / Sessions / Profile** | Aegis Memory | Facts · past conversations · consolidated profile |
| Dashboard: ValueSpine              | **Value** (hero KPIs) | —               | Savings · security · performance · audit            |

> Naming note: "Overview" (not "Dashboard") reads more executive and avoids the generic; "Access
> demo" replaces "Simulation" so a non-technical viewer knows what they're looking at. Keep the
> `Aegis` brand lockup in the sidebar; add a small module-status dot pattern (see `CapabilityMap`).

---

## 4. Per-surface redesign

Each surface below gives: **hero**, **bento structure**, **charts (specific recharts types)**,
**text to cut/relocate**, **micro-animations**, and an **ASCII wireframe**. Directories in
parentheses are the files a build agent owns for that surface.

---

### 4.1 Console  (`components/console`, `ml`, `guardrail`, `retrieval`, `trace`, `graph`)

The console is the money surface and already uses a 3-rail 12-col grid — but each rail is a
vertical stack of full-width cards, and the right rail is the jargon epicentre (Dual verdict /
Conformal / SHAP). Keep the three-rail spine; **restructure the rails into a bento** and demote
jargon to tooltips.

**Hero:** the center stage — live **Answer** with a compact **Decision strip** directly above it
(one row: `Confidence` gauge · `Guardrails ✓` · `Sources n` · `Cost $` ), so the outcome reads
in one glance before any panel. The orchestration/knowledge graph is the visual anchor top-center.

**Bento structure (≥ lg, 12-col):**
- Top full-width: `QueryBar` (unchanged) + slim `TrustBar` (relabelled chips, §3).
- Left rail (col-span-3): **Activity** (AgentTracePanel) — full height, live.
- Center (col-span-6): **Orchestration** graph (hero, row-span-2) → **Decision strip** →
  **Answer** → **Guardrails** (collapsed to a pass/among-n summary chip that expands).
- Right rail (col-span-3): **Confidence** gauge card → **Sources** (compact funnel) → **Why**
  (top-3 drivers only) → **Efficiency** mini-stats.

**Charts:**
- Confidence: `RadialBarChart` (recharts) showing calibrated confidence %, with the numeric
  interval in a tooltip (replaces the raw `ConformalInterval` band on the primary surface; the
  band stays inside the "Technical detail" expander).
- Sources: keep the horizontal score bars but cap to **top 3 + "n more"**, headline = a 3-stage
  funnel stat (`recalled → ranked → used`) rendered as a tiny 3-segment bar, not a sentence.
- Why (SHAP): keep `ShapBar` diverging bars but **top 3 only**, labelled "raises / lowers score"
  (drop "signed SHAP attribution").

**Text to cut / relocate:**
- Remove "conformal interval", "coverage", "deferral signal", "prediction-set size", "signed
  SHAP attribution", "trajectory", "glass-box ledger", "hybrid retrieval · RRF · bm25" from
  visible labels → all become `InfoTip` content or the "Technical detail" expander.
- "Dual verdict / ML signal (evidence) · human gate on tool risk" → panel title **Decision**,
  subtitle "ML confidence + human approval on risky actions".
- Orchestration ledger chips keep `model · ms · $` (data, not prose) but lose the sentence caption.

**Micro-animations:** live run keeps `.animate-beat`/`.animate-pip`/`.animate-trace-in` (already
built and load-bearing — do not remove). Add: Confidence radial draws in on first result;
Decision strip cross-fades between `thinking → decided` states; count-up on Cost/Sources numbers.

```
┌───────────────────────────────────────────────────────────────────────────┐
│ [ Persona ▾ ] [ Ask the agent…                              ] [ Run ] [↺]  │  QueryBar
│ Trust:  Confidence ─ Explained ─ Guarded ─ Approved ─ Traced               │  TrustBar (relabelled)
├──────────────┬────────────────────────────────────┬────────────────────────┤
│ ACTIVITY     │  ORCHESTRATION            (hero)    │  CONFIDENCE            │
│ ● run start  │  ┌───────── graph / flow-map ─────┐ │  ╭───────╮  92%       │  RadialBar
│ ● retrieve   │  │  supervisor → specialist → …   │ │  ╰───────╯  ⓘ interval│
│ ● reason     │  └────────────────────────────────┘ ├────────────────────────┤
│ ● tool call  │  ┌ Decision strip ────────────────┐ │  SOURCES              │
│ ● approval   │  │ 92% · Guards ✓ · 5 src · $0.004│ │  recalled▸ranked▸used │  funnel bar
│ ● answer     │  └────────────────────────────────┘ │  1. doc… ▓▓▓▓  0.81   │  top-3 bars
│  (live spine)│  ANSWER                             │  2. doc… ▓▓▓   0.64   │
│              │  ┌────────────────────────────────┐ ├────────────────────────┤
│              │  │ streamed answer text…          │ │  WHY                  │
│              │  └────────────────────────────────┘ │  driver A  �──▶ +0.22  │  ShapBar top-3
│              │  Guardrails ✓ 3 checks  ▸ expand    │  driver B  ◀── −0.10  │
│              │                                     ├────────────────────────┤
│              │                                     │  EFFICIENCY  cache 74% │
└──────────────┴────────────────────────────────────┴────────────────────────┘
   col-span-3            col-span-6                        col-span-3
```

---

### 4.2 Overview  (`components/dashboard`, `metrics`, `charts`)

Currently a straight `flex-col`: ValueSpine → MetricsDeck → RoiPanel+KPIs → DashboardCharts.
This is the most "linear" surface and the one that most needs the bento + hero treatment. It is
also the **charts owner** — the `components/charts/*` primitives live with this surface; other
surfaces import them read-only.

**Hero:** a **KpiHero** — the single number that matters (savings vs frontier-only), giant, with
a count-up, a `StatDelta` ▲ chip, and an inline `MiniTrend` sparkline of the last N periods.

**Bento structure (12-col):**
- Row 1: `KpiHero` (col-span-8, row-span-2, "Cost saved") + stacked pair (col-span-4):
  `Queries today` and `Actions approved` compact stat tiles.
- Row 2 (fills beside hero): `Quality score` radial + `p95 latency` mini stat.
- Row 3: three equal tiles (col-span-4 each) — **Cost trend** (`AreaChart`), **Model mix**
  (`DonutChart`, live `/metrics`), **Query volume** (`BarChart`).
- Row 4: **Value** strip → convert the prose-heavy `ValueSpine` into a 4-tile `BentoGrid` of
  outcome stats (Savings / Security / Performance / Audit), each = icon + number + one-line +
  `InfoTip`. `RoutingTable` moves into a "Model routing" expandable for admins.

**Charts:** `AreaChart` (cost trend + quality trend — exist), `DonutChart` (model mix — exists,
live), `BarChart` (query volume — exists), `RadialBarChart` (new, quality score gauge),
`MiniTrend` (new, inline in KpiHero and stat tiles). Kill the hand-built CSS meter bars in
`RoiPanel`/`ValueSpine` — replace with `MiniTrend`/`RadialBar`/`DonutChart`.

**Text to cut / relocate:** "Management value spine · money · security · cost · audit", "Cost
saved vs frontier-only — from caching + small-model routing", "sample assumptions", "rubric-scored
efficiency/trust numbers", "live telemetry deck" → titles become **Value**, **Cost saved**,
**Quality**; the explanatory sentences move to `InfoTip`. Keep every `sample` badge.

**Micro-animations:** `CountUp` on KpiHero + all stat numbers (mount); `RevealOnScroll` stagger
as bento rows enter; chart draw-in; hover-lift on tiles. RoiPanel's existing `useCountUp` is
promoted to the shared `CountUp` and reused.

```
┌───────────────────────────────────────────────────┬───────────────────────┐
│  COST SAVED                              (hero)    │  Queries today        │
│                                                    │   2,870   ▲ 8%        │
│    $ 128,400   ▲ 12% vs last month                 ├───────────────────────┤
│    ╱╲    ╱╲   ╱                       (MiniTrend)  │  Actions approved     │
│   ╱  ╲╱╲╱  ╲╱                                       │    41     ● live      │
├─────────────────────────────┬──────────────────────┼───────────────────────┤
│  Quality  ╭──╮ 0.94         │  p95 latency  1.2s   │  (tiles reflow under  │
│           ╰──╯  RadialBar   │               ▲ ok   │   hero on ≥lg)         │
├───────────────────┬─────────────────┬──────────────┴───────────────────────┤
│  COST TREND       │  MODEL MIX      │  QUERY VOLUME                         │
│   AreaChart       │   DonutChart    │   BarChart                            │
│   ╱╲___╱╲___      │     ◐  live     │   ▁▃▅▇▅▃                              │
├───────────────────┴─────────────────┴───────────────────────────────────────┤
│  VALUE:  [ Savings 63% ] [ Security 100% ] [ Perf p95 ] [ Audit ✓ ]  ⓘ each │  4-tile bento
└──────────────────────────────────────────────────────────────────────────────┘
```

---

### 4.3 Memory  (`components/memory`, shares `graph` read-only)

The densest jargon and the most stacked surface (no tabs, full-width rows of scored triples).
Reframe from *three raw memory tiers* to **"what the agent knows about this subject,"** with the
tier machinery demoted behind a toggle.

**Hero:** a **subject header** (who/what) + a **MemorySummary** hero tile — a short, plain
sentence generated from the structured profile ("Prefers X · Y open cases · last seen Zd ago")
plus 3 count-up stats (Facts · Sessions · Turns). The `KnowledgeGraph` is the visual anchor.

**Bento structure (12-col), replacing the current 5 stacked rows:**
- Row 1: **Subject + summary** (col-span-8, hero) + **Knowledge graph** thumbnail (col-span-4,
  expands to full).
- Row 2: **What we know** (col-span-7) — the semantic facts, but as compact rows: statement +
  confidence `MiniMeter` + "recalled n×"; hide subject·predicate·object triples and "bitemporal"
  behind a per-row "detail" popover. **Profile** (col-span-5) — the structured `dl`, cleaned.
- Row 3: **Sessions** (col-span-6, episodic — collapsible transcripts) + **Recent updates**
  (col-span-6 — the write-log timeline, relabelled from "append-only memory write-log").
- **Recall debug** becomes an admin-only **"Why did it recall this?"** expandable under the
  answer, not a primary full-width row. Token-budget/`<pre>` dump lives inside it.

**Charts / viz:** keep `KnowledgeGraph` (force graph — the star). Facts confidence → `MiniMeter`
(small radial or bar). Add a tiny **memory-growth `MiniTrend`** (facts over time) in the summary
hero if data exists. The bitemporal validity strip stays but only inside a fact's detail popover.

**Text to cut / relocate:** headings "Three-tier memory · semantic · episodic · structured",
"Tier 1/2/3 — the semantic/episodic/structured store", "bitemporal", "consolidated",
"relevance × recency × importance", "recall-debug trace — the glass-box on retrieval",
"append-only" → replaced by **What we know / Sessions / Profile / Recent updates** with honest
terms in tooltips.

**Micro-animations:** count-up on the 3 summary stats; `RevealOnScroll` on fact rows (stagger);
graph keeps its idle-breathe/flow-pulse; fact "detail" popover fades in.

```
┌────────────────────────────────────────────────────┬──────────────────────┐
│  SUBJECT: Acme Corp — Premium                       │  KNOWLEDGE GRAPH      │
│  "Prefers email · 2 open cases · last seen 3d ago"  │      ● ─ ● ─ ●        │
│   Facts 24    Sessions 6    Turns 148   (count-up)  │     ╱   graph  ╲      │
├───────────────────────────────────┬────────────────┴──────────────────────┤
│  WHAT WE KNOW                      │  PROFILE                              │
│  • Billing cycle is monthly  92% ▓ │   Plan            Premium             │
│  • Prefers email contact     88% ▓ │   Region          EU                  │
│  • Had a duplicate charge    2×  ⓘ │   Open cases      2                   │
│    ▸ detail (triple, validity)    │   Updated         3d ago              │
├───────────────────────────────────┼───────────────────────────────────────┤
│  SESSIONS                         │  RECENT UPDATES                       │
│  ▸ 2026-08-01  8 turns            │  ● ADD  billing cycle       2d ago    │
│  ▸ 2026-07-28  5 turns            │  ● UPDATE contact pref      3d ago    │
└───────────────────────────────────┴───────────────────────────────────────┘
    ▸ Why did it recall this?  (admin expander → recall debug + working memory)
```

---

### 4.4 Improvement (Self-improvement / Ops)  (`components/ops`)

Currently a `flex-col` of five blocks (LoopStrip, EvalTrend, Timeline+Diff, Diagnose+Releases).
The loop *story* is good; make it a **capability map + trend hero**, not a stack of prose cards.

**Hero:** **Quality trend** (the existing `EvalTrend` multi-line chart) promoted to hero
(col-span-8, row-span-2) with the 4 metric KPI tiles as its header (already clickable). This is
the one real chart on the surface — lead with it.

**Bento structure (12-col):**
- Row 1: **Quality trend** hero (col-span-8) + **Loop** capability strip (col-span-4) — the 4
  loop steps (Watch → Diagnose → Gate → Rollback) as a vertical `CapabilityMap` with live status
  dots, replacing the wordy step captions.
- Row 2: **Release gate** (col-span-5) — `PendingReleases` with a small `DonutChart` of
  auto-shipped vs awaiting-approval instead of the 2-col legend prose; **Diagnose** (col-span-7)
  — latest proposal, `metric_breakdown` as a `BarChart` (recharts) not CSS meters.
- Row 3: **Versions** (col-span-4 timeline) + **Diff** (col-span-8, side-by-side) — kept, but
  under a "Prompt history" section header; diff is inherently 2-col so it stays.

**Charts:** `LineChart` (EvalTrend — exists, hero), `DonutChart` (release split — new usage),
`BarChart` (diagnose metric breakdown — replace CSS meters). 

**Text to cut / relocate:** "The LLM-Ops / Self-improvement view (view #3)", "closed loop ·
human-gated", "the harness scoring its own traces", "tiered gate outcome", "failure mode",
"POST /ops/diagnose", "GET /ops/evals", "risk_reasons" → titles **Improvement / Quality trend /
Release gate / Diagnosis**; API routes to footer tooltips; "tiered gate" → "Low-risk auto-ships,
high-risk waits for approval" as `InfoTip`.

**Micro-animations:** chart draw-in on the trend; count-up on the 4 metric tiles; `CapabilityMap`
dots pulse on the active loop step; reveal-on-scroll for row 2/3.

```
┌────────────────────────────────────────────────────┬──────────────────────┐
│  QUALITY TREND                          (hero)     │  LOOP                 │
│  [Answer .94][Retrieval .91][Tool .88][Guard .97]  │  ● Watch   live       │  CapabilityMap
│   1.0┤        ╱╲___╱‾‾                              │  ● Diagnose  2 open   │
│      │   ___╱‾       (multi-line LineChart)         │  ○ Gate     1 wait    │
│   0.5┤_______________________________               │  ○ Rollback ready     │
├───────────────────────────────┬────────────────────┴──────────────────────┤
│  RELEASE GATE                 │  DIAGNOSIS  (latest proposal)              │
│    ◐ 3 auto  · 1 awaiting     │   failure: retrieval recall               │
│      DonutChart               │   ▏▏▏▏ metric breakdown  (BarChart)       │
│    ▸ Roll back to last-good   │   draft #7 → staged for approval          │
├───────────────────────────────┴────────────────────────────────────────────┤
│  PROMPT HISTORY:   Versions (timeline)  │  Diff  base | proposed  (2-col)   │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

### 4.5 Approvals  (`components/approvals`, `approval`)

Currently a flat `space-y-3` column of full-width rows — the textbook "too linear" case. Turn it
into a **triage board**: summary rail + a responsive card grid.

**Hero:** a compact **queue summary** bar — 3 count-up stats (`Pending` · `Overdue` · `Avg wait`)
+ a slim `DonutChart` of risk mix — so the reviewer reads the queue state before scrolling.

**Bento structure:**
- Top: queue summary (full-width, 3 stat tiles + risk donut, col-span split 8/4).
- Body: **card grid** of approval items — `grid md:grid-cols-2 xl:grid-cols-3`, each item a
  `ComparisonCard`-styled tile (proposed action headline · risk chip · SLA countdown ·
  `Confidence` mini-gauge · Approve/Reject). No more full-width one-per-row list.
- Empty state: designed (icon + "You're all caught up"), not a sentence.

**Charts:** `DonutChart` (risk mix in summary), `RadialBar`/`MiniMeter` (per-card confidence,
replacing the embedded raw `ConformalInterval` on the card face — band goes to a detail popover).

**Text to cut / relocate:** "durable approvals inbox (admin)", "async counterpart to the dramatic
in-run gate", "ML snapshot that routed them here", "conformal band", "This wide band is why I
stopped — driven most by <feature>", "band:", "pred", "CI [x,y]", "% coverage" → card shows
**action · risk · confidence · time left**; the ML internals go behind "Why this needs approval".

**Micro-animations:** count-up on summary stats; card hover-lift; on decision, the card
optimistically fades/collapses out (`--dur-slow`); SLA countdown ticks (existing logic).

```
┌──────────────────────────────────────────────┬────────────────────────────┐
│  Pending 7   Overdue 1   Avg wait 2h13m        │   Risk mix   ◐  DonutChart │
├──────────────────────┬───────────────────────┬─┴──────────────────────────┤
│ ┌ Refund $420 ─────┐ │ ┌ Close case #4821 ─┐ │ ┌ Escalate to tier-2 ────┐ │
│ │ risk ● high      │ │ │ risk ● medium     │ │ │ risk ● low             │ │  card grid
│ │ confidence ╭─╮63%│ │ │ confidence ╭─╮ 81%│ │ │ confidence ╭─╮ 90%     │ │  2–3 cols
│ │ ⏱ 1h left        │ │ │ ⏱ 4h left         │ │ │ ⏱ 6h left              │ │
│ │ [Approve][Reject]│ │ │ [Approve][Reject] │ │ │ [Approve][Reject]      │ │
│ │ ▸ Why approval?  │ │ │ ▸ Why approval?   │ │ │ ▸ Why approval?        │ │
│ └──────────────────┘ │ └───────────────────┘ │ └────────────────────────┘ │
└──────────────────────┴───────────────────────┴────────────────────────────┘
```

---

### 4.6 Governance (Admin)  (`components/admin` except AuditLog)

Already tabbed (Tenants / Users / Budgets / Usage) — that's good; keep the tabs but add a
**governance overview hero above the tabs** so the surface doesn't open cold onto a raw table.

**Hero:** an **overview strip** (above `TabsList`): 4 count-up KPIs — `Tenants` · `Active users`
· `Spend this month` · `% of budget used` (a `RadialBar` gauge) — from live data where available.

**Per-tab bento:**
- **Usage** (already the best): keep `AreaChart` cost trend, promote "Spend by model" from CSS
  share-bars to a horizontal `BarChart`; 3 KpiTiles → bento row. Add a small `DonutChart` spend-
  by-tenant.
- **Budgets:** keep the 2-col (caps table | new-budget form). Add a per-scope **utilisation
  `MiniMeter`** column to the caps table (used/cap) so caps read visually. Relocate the
  "enforced inward at the model chokepoint" sentence → `InfoTip` on the title; "RPM/TPM" keep
  but add tooltips ("requests/min", "tokens/min").
- **Tenants / Users:** stay tables (correct for the data) but gain a header stat + status as a
  coloured dot+label, and zebra/hover rows. No layout revolution — tables are right here.

**Charts:** `AreaChart` (exists), `BarChart` (spend by model), `DonutChart` (spend by tenant),
`RadialBar` (% budget used). 

**Text to cut / relocate:** "the multi-tenant governance surface made visible", "who, capped how,
spending what", "enforced inward at the model chokepoint", "the membership half of multi-tenant
RBAC" → into `InfoTip`s. Titles: **Governance / Tenants / Users / Budgets / Usage**.

**Micro-animations:** count-up on the overview KPIs; tab cross-fade (`--dur-slow`); chart draw-in;
row hover.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Tenants 12   Active users 148   Spend (mo) $4,820   Budget used ╭─╮ 61%   │  overview hero
├───────────────────────────────────────────────────────────────────────────┤
│ [ Tenants ] [ Users ] [ Budgets ] [ Usage ]                                 │  Tabs
├───────────────────────────────────────────────────────────────────────────┤
│  USAGE tab:                                                                  │
│  ┌ Total spend ┐ ┌ Tokens ┐ ┌ Models ┐   │  SPEND BY MODEL   BarChart      │
│  │  $4,820     │ │ 18.2M  │ │   6    │   │  gpt-4o  ▓▓▓▓▓▓                  │
│  └─────────────┘ └────────┘ └────────┘   │  mini    ▓▓▓                     │
│  COST TREND  AreaChart  ╱╲__╱‾            │  SPEND BY TENANT  DonutChart ◐  │
└───────────────────────────────────────────────────────────────────────────┘
```

---

### 4.7 Audit  (`components/admin/AuditLog.tsx`)

A single dense 7-column mono table — correct for an audit log, but it opens cold and reads as a
data dump. Add **scannability + a pulse header**, keep the table.

**Hero:** a thin **audit header strip** — `Events (24h)` count-up · `Blocked` · `Approved` ·
a 24-hour **activity `BarChart`** (events per hour) or `MiniTrend` sparkline — so the reviewer
sees the shape of activity before the rows.

**Body:** the existing table, upgraded: sticky header, filter chips (Result: all/blocked/
completed; Actor), result as a coloured dot+label, trace id as a mono chip with copy affordance.
Rows reveal-on-scroll in small batches. No bento needed — this is legitimately tabular.

**Charts:** `BarChart` (events-per-hour) or `MiniTrend` in the header strip only.

**Text to cut / relocate:** "append-only" stays as a small badge (it's a real, valuable property)
with an `InfoTip` explaining it; empty state designed.

**Micro-animations:** count-up on header stats; header chart draw-in; row `.animate-trace-in` as
batches load.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  Events (24h) 1,284   Blocked 12   Approved 47     ▁▃▅▇▅▃▂  (per-hour bar)  │  header strip
├───────────────────────────────────────────────────────────────────────────┤
│ Filter: [All][Blocked][Completed]   Actor ▾              append-only ⓘ      │
├──────┬─────────┬────────┬───────┬────────┬─────────────┬────────────────────┤
│ Time │ Action  │ Actor  │ Model │ Trace  │ Approved by │ Result             │  sticky header
│ 12:04│ refund  │ alice  │ 4o    │ a1b2 ⧉ │ bob         │ ● completed        │
│ 12:01│ tool     │ system │ mini  │ c3d4 ⧉ │ —           │ ● blocked          │
└──────┴─────────┴────────┴───────┴────────┴─────────────┴────────────────────┘
```

---

### 4.8 Access demo (Simulation)  (`components/sim`)

Already the best-structured non-linear surface (two lanes side-by-side) — keep that, but lead
with the **comparison as the hero** and cut the "principals/divergence" jargon.

**Hero:** a **ComparisonCard** — the `DivergenceStrip` promoted to the top as the headline: the
same query, two roles, and the 4 dimensions (retrieval scope · action tool · human gate · cost)
as a clean side-by-side with clear ✓/✗/differs markers. This *is* the story; make it read first.

**Body:** the two live lanes (`AgentTracePanel` + `AnswerPanel`) stay side-by-side under the
comparison, each headed by a role chip. Add a small **"who can do what" `CapabilityMap`** row.

**Charts:** none required; the `ComparisonCard` + `CapabilityMap` are the visualization. Optional:
a 2-bar `BarChart` of run cost per role.

**Text to cut / relocate:** "Divergence — one system, two principals", "persona ·
operations_lead — full retrieval + human gate", "own-scope/full-scope", "RBAC, retrieval scope
and the tool allowlist" → title **Access demo**, subtitle "Same question, two roles — see what
each is allowed to do." Role labels become human ("Operations lead" / "Client"), scope shown as
✓/✗ not "own-scope".

**Micro-animations:** the two lanes stream with the existing trace animations; the ComparisonCard
cells cross-fade/highlight the row that differs when both runs finish; count-up on run cost.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  ACCESS DEMO  — "Resolve case #4821: duplicate charge on a premium account" │
│  ┌────────────────── ComparisonCard (hero) ──────────────────────────────┐ │
│  │  What differs      │  Operations lead      │  Client                   │ │
│  │  Retrieval scope   │  ✓ full               │  ✓ own account only       │ │
│  │  Action (refund)   │  ✓ executed           │  ✗ proposed only          │ │
│  │  Human gate        │  ● fired              │  — not reached            │ │
│  │  Run cost          │  $0.004               │  $0.002                   │ │
│  └────────────────────────────────────────────────────────────────────────┘ │
├───────────────────────────────────┬───────────────────────────────────────┤
│  OPERATIONS LEAD                  │  CLIENT                               │
│   Activity (trace)                │   Activity (trace)                    │  side-by-side
│   Answer                          │   Answer                              │
└───────────────────────────────────┴───────────────────────────────────────┘
```

---

## 5. New shared components

Add under `src/components/ui/` (primitives) and a new `src/components/shared/` (composed
patterns). All use `cn()`, the design tokens, and `prefersReducedMotion()`. Prop sketches:

```ts
// shared/BentoGrid.tsx — the 12-col bento container + tile
function BentoGrid(props: { children: ReactNode; className?: string }): ReactElement
//   → <div className="grid grid-cols-12 gap-4 lg:gap-5">
function BentoTile(props: {
  span?: 3 | 4 | 5 | 6 | 7 | 8 | 12      // lg column span; always 12 below lg
  rows?: 1 | 2                            // row-span
  hero?: boolean                          // hero padding + shadow-pop + display type
  interactive?: boolean                   // hover-lift + focus ring
  reveal?: boolean                        // wrap in RevealOnScroll (staggered by index)
  index?: number                          // stagger order
  className?: string; children: ReactNode
}): ReactElement                          // composes Card; NOT a new visual box

// shared/KpiHero.tsx — the one-big-number hero tile
function KpiHero(props: {
  label: string                           // e.g. "Cost saved"
  value: number                           // raw number (KpiHero owns the count-up + format)
  format?: (n: number) => string          // default: locale number; pass currency/percent
  delta?: { value: number; direction: 'up' | 'down'; tone?: 'good' | 'bad' | 'neutral' }
  trend?: number[]                         // → inline MiniTrend
  signal?: Signal                         // hue accent (default neutral)
  info?: ReactNode                         // InfoTip content (relocated prose)
  sample?: boolean
}): ReactElement

// shared/StatDelta.tsx — the ▲/▼ delta chip
function StatDelta(props: {
  value: number; direction?: 'up' | 'down'  // auto from sign if omitted
  tone?: 'good' | 'bad' | 'neutral'         // green/rose/muted — decoupled from direction
  suffix?: string                            // "%", "vs last month"
}): ReactElement                             // ▲ 12%  in --success / --danger

// shared/CountUp.tsx — promote RoiPanel's useCountUp to a shared hook + component
function useCountUp(target: number | null, durationMs?: number): number   // guards reduced-motion
function CountUp(props: {
  value: number; durationMs?: number; format?: (n: number) => string; className?: string
}): ReactElement

// shared/MiniTrend.tsx — small inline trend (wraps Sparkline OR a headless recharts Area)
function MiniTrend(props: {
  data: number[]                            // or {value}[]
  color?: Signal                            // default neutral
  height?: number                           // default 40
  variant?: 'line' | 'area'                 // area = filled gradient
}): ReactElement                            // reuses metrics/Sparkline where possible

// shared/ComparisonCard.tsx — side-by-side A vs B (Access demo, Approvals)
function ComparisonCard(props: {
  title: string
  columns: [string, string]                 // ["Operations lead", "Client"]
  rows: Array<{
    label: string
    a: ReactNode; b: ReactNode
    diff?: boolean                           // highlight when a≠b
  }>
}): ReactElement

// shared/CapabilityMap.tsx — Aegis module / step status grid (Loop, Access demo, Sidebar dots)
type Capability = { name: string; tech?: string; status: 'live' | 'idle' | 'pending' | 'off' }
function CapabilityMap(props: {
  items: Capability[]
  layout?: 'row' | 'grid'                    // vertical strip vs bento of module tiles
}): ReactElement                             // name + honest tech subtitle + status dot

// ui/InfoTip.tsx — the standard prose-relocation affordance (wraps existing Tooltip)
function InfoTip(props: { children: ReactNode; label?: string }): ReactElement
//   → a small ⓘ trigger; TooltipProvider is already mounted in App.tsx

// ui/Gauge.tsx — a thin recharts RadialBar wrapper for single-value % (Confidence, Budget)
function Gauge(props: {
  value: number                             // 0..1
  label?: string; centerLabel?: string
  color?: Signal; size?: number
}): ReactElement

// shared/RevealOnScroll.tsx — IntersectionObserver fade+rise, reduced-motion aware
function RevealOnScroll(props: {
  children: ReactNode; delayMs?: number; className?: string
}): ReactElement
```

Reuse (do **not** rebuild): `Card`+parts, `Badge` (trust variants), `Button`, `Tabs`, `Tooltip`,
`Progress`, `KpiTile`, `AreaChart`, `BarChart`, `DonutChart`, `Sparkline`, `ChartTooltip`,
`chartHex`, `cn`, `SIGNALS`. `Gauge`/`RadialBar` is the only genuinely new recharts type.

---

## 6. Build order (parallelizable)

Collision map — the only shared/serialised files:
- `src/index.css` (tokens + motion) — **one owner, first.**
- `src/routes/Portal.tsx` (nav labels) — **one owner.**
- `src/components/shared/*` + new `ui/*` (component library) — **one owner, before surfaces.**
- `src/components/charts/*` — owned by the **Overview** agent; all other surfaces import
  read-only and must not edit them (add `Gauge` in Wave 0 so Console/Approvals/Governance can use
  it without touching charts/).
- `src/api/*`, `src/state/*`, `src/config/signals.ts` — **do not modify** (presentation only).

### Wave 0 — Foundation (serial, 1 agent; blocks all surfaces)
1. `index.css`: add `--font-display: Space Grotesk`, the type-scale utilities, `--dur-*`/`--ease-*`
   motion vars, `.shadow-hover`/`.shadow-pop`. **Additive only** — no existing token values change.
2. Build `src/components/shared/*` and new `ui/InfoTip`, `ui/Gauge` per §5 (promote `useCountUp`
   out of `RoiPanel`). Ship with unit tests + a render smoke test. `pnpm build/lint/test` green.
3. `Portal.tsx` + `Sidebar` brand: apply the §3.2 nav labels and honest subtitles/tooltips.

> Wave 0 is the contract surface. Nothing in Wave 1 starts until §5 components exist and typecheck.

### Wave 1 — Surfaces (parallel, disjoint directories; 6 agents)

| Agent | Surface | Owns (writes)                                              | Imports (read-only)                    |
|-------|---------|------------------------------------------------------------|----------------------------------------|
| **A** | Console | `components/console`, `ml`, `guardrail`, `retrieval`, `trace` | shared/*, ui/*, charts/*, graph/*   |
| **B** | Overview| `components/dashboard`, `metrics`, **`charts`**             | shared/*, ui/*                         |
| **C** | Memory  | `components/memory`                                        | shared/*, ui/*, charts/*, graph/*      |
| **D** | Improvement + Approvals | `components/ops`, `approvals`, `approval`   | shared/*, ui/*, charts/*               |
| **E** | Governance + Audit | `components/admin`                             | shared/*, ui/*, charts/*               |
| **F** | Access demo | `components/sim`                                       | shared/*, ui/*, charts/*, trace/*      |

Rules for Wave 1 agents:
- Edit only your owned directories. If you need a shared change, it belongs in Wave 0 — flag it,
  don't fork it.
- `graph/*` and `trace/*` are read-only for everyone except Console (Console owns their internal
  restyle; Memory/Access-demo consume them as-is).
- Each agent finishes green (`pnpm build && pnpm lint && pnpm test`) before reporting.
- Keep every `sample` badge; move prose to `InfoTip`/expander; use the §4 wireframe for your surface.

### Wave 2 — Integration pass (serial, 1 agent)
Visual QA across surfaces in light + dark + mobile + reduced-motion; verify no surface is a bare
`flex-col` of full-width rows; check tab cross-fades and reveal staggers don't fight; final
`build/lint/test`; screenshot each surface for the PR.

**Parallelism summary:** Wave 0 (1 agent) → Wave 1 (6 disjoint agents in parallel) → Wave 2
(1 agent). A/B/C/D/E/F never touch the same file; the only cross-dependency is `charts/*`, owned
by B and frozen-except-`Gauge` (added in Wave 0), so imports are safe.
