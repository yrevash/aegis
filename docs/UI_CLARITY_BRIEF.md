# Aegis Console — UI Clarity Redesign Brief

**Author:** product-design research pass
**Date:** 2026-08-05
**Scope:** clarity, hierarchy, and restraint *within* the existing light SnowUI system. This is **not** a reskin — same tokens, same palette, same components. We are removing competition for attention, not changing the identity.
**Read this if:** you are implementing the de-clutter. Every recommendation is expressed in terms of the tokens and components that already exist in `frontend/src`.

---

## 0. TL;DR — the diagnosis in one paragraph

Aegis is *information-rich and hierarchy-poor*. Almost every surface presents many objects at **equal visual weight**: the Operations Dashboard shows ~15 competing numbers and four charts with no single hero; the `RoiPanel` is an entire dashboard compressed into one card; the six semantic hues (agent / graph / risk / block / ok / ml) are used **decoratively** — one tint per metric — which trains the eye to ignore color instead of reading it as status; and the nav, tables, and cards all carry a second mono caption line that adds texture without adding signal. The fix is not to delete features. It is to **rank** them: one hero per view, a small band of supporting metrics, one hero chart, and everything else moved behind progressive disclosure. The founder should be able to pass the "5-second test" on the management dashboard.

---

## 1. Clarity principles (with sources)

These are the load-bearing patterns from clarity-first products (Stripe, Linear, Vercel/Geist, Amplitude) and the primary UX/dataviz literature. Each is stated as a rule we will apply to Aegis.

### A. Hierarchy & one-job-per-screen
1. **Inverted pyramid — one job per view.** Most-significant KPIs top row (above the fold), trends/drivers in the middle, granular detail at the bottom. Executive views should show ~3–7 KPIs above the fold, not everything the system can report. — https://alphabytesolutions.com/executive-dashboard-design-best-practices-and-examples/
2. **Group by meaning, separate with whitespace — not borders.** Cluster related metrics into zones and let whitespace (Gestalt proximity) create the separation, so the eye finds the right region before reading any number. — https://www.nngroup.com/videos/the-gestalt-principles-intro/

### B. One primary metric per view
3. **One hero number, supported by input metrics.** Anchor each view to a single "North Star", then present a small set of leading-indicator inputs beneath it that *explain* the hero's movement. One canonical board per objective, not a wall of equal tiles. — https://amplitude.com/blog/product-north-star-metric
4. **Stripe's restraint: five numbers, then stop.** Stripe's home shows exactly five numbers (gross volume, net volume, new customers, successful payments, date-range comparison), each with a small sparkline — "and that is it." Show what the user needs to act, not everything available. — https://www.925studios.co/blog/stripe-dashboard-design-breakdown

### C. Whitespace & rhythm
5. **A 4px spacing scale.** Constrain spacing to a finite ramp (Vercel/Geist: `4, 8, 12, 16, 24, 32, 40, 48, 64`) with a single corner radius. Predictable vertical rhythm; no one-off margins. — https://vercel.com/geist/space
6. **Whitespace signals quality; hairlines over heavy dividers.** Use spacing + typography (not color and chrome) to create calm, especially in financial/admin contexts. Linear separates with 0.5px borders and lets spacing do the work. — https://blog.logrocket.com/ux-design/linear-design/

### D. Progressive disclosure
7. **Two tiers, never more — disclose the frequent, defer the rare.** Everything frequently needed stays on the primary display; advanced/rare detail moves to a clearly-labeled secondary surface (tab, expander, "Show details", row-drilldown). Never exceed two levels; label the trigger with strong information scent. — https://www.nngroup.com/articles/progressive-disclosure/

### E. Restrained color (color = meaning)
8. **Color only for status; neutral by default.** Reserve saturated color for semantic status (green = good/succeeded, amber = attention/pending, red = failed). When *every* data point is colored, color stops meaning anything. Trend charts stay monochrome. — https://www.925studios.co/blog/stripe-dashboard-design-breakdown
9. **A single accent, hierarchy via opacity not hue.** Linear reserves one accent for interactive/status and carries hierarchy through gradations of neutral, not color variation. Vercel builds on near-white surface + near-black ink + a defined gray ramp. — https://designmd.cc/benchmarks/linear

### F. Typography
10. **Few sizes, few weights.** Cap to a handful of sizes and 2–3 weights (Geist: 400 read / 500 interact / 600 announce, capped at semibold). Hierarchy from size + weight, not color. — https://vercel.com/geist/space
11. **Tabular numerals for all data.** `font-variant-numeric: tabular-nums` on every number in a row, KPI, or live counter so digits share one grid and don't "jump" on update. — https://developer.mozilla.org/en-US/docs/Web/CSS/font-variant-numeric

### G. KPI cards & tables
12. **KPI anatomy: label · big number · delta · sparkline.** Quiet label, one large high-contrast value (largest size + heaviest weight), a comparison delta vs. prior period, a small trend sparkline. The sparkline matters because the trend changes the meaning of the delta. — https://www.925studios.co/blog/stripe-dashboard-design-breakdown
13. **Tables: alignment over gridlines; right-align numbers.** Left-align text, right-align numbers (so digit places line up), match header alignment to its column, drop vertical gridlines, keep only faint horizontal rules, stay monochrome. Zebra striping is usually harmful — use whitespace + alignment. — https://medium.com/mission-log/design-better-data-tables-430a30a00d8c

### H. Empty states & the 5-second test
14. **Design the blank slate — three jobs.** Every empty state must (1) state status honestly ("No records for this range" — never "no content" while still loading), (2) teach what belongs here, (3) offer the next action. Differentiate first-run / no-results / cleared; pair with skeletons so empty ≠ loading. — https://www.nngroup.com/articles/empty-state-interface-design/
15. **The 5-second rule.** A viewer should grasp the primary message in five seconds: identify the main KPI, judge good/bad, know if action is needed. This is the acceptance test for hierarchy. — https://nastengraph.substack.com/p/the-complete-guide-to-dashboard-testing
16. **Maximize the data-ink ratio (Tufte).** Erase non-data-ink: gridlines, boxes, drop shadows, meaningless color, redundant labels. The theoretical backbone under every rule above. — https://www.holistics.io/blog/data-ink-ratio/

---

## 2. What our system already gives us (use these, don't invent)

From `frontend/src/index.css` and the component library — the redesign stays inside this vocabulary:

- **Neutrals:** `--background #f9f9fa`, `--foreground #101828`, `--surface #fff`, `--surface-2 #f2f4f7` (inset track), `--border #e4e7ec` (hairline), `--muted-foreground #667085`. Radius `--radius: 0.75rem` (12px).
- **Six semantic hues**, each with an `-ink` variant: `agent` (mint), `graph` (blue), `risk` (amber), `block` (rose), `ok` (green), `ml` (purple). **These are meant to be *signals*.** Today they're used as *categories* (one hue per KPI) — the single biggest color-clarity regression to fix.
- **Utilities:** `.eyebrow` (uppercase micro-label), `.tabular` (tabular-nums — already available, apply it everywhere numbers live), `font-display`, `bg-tint-blue` / `bg-tint-purple` (soft KPI tints), `shadow-card`.
- **Type sizes in play today:** KPI value `1.75rem`, deck value `2rem`, ROI hero `2.75rem`, plus a lot of `0.62–0.76rem` mono captions. The mono micro-caption is overused — it's the "texture" that reads as clutter.

**Clarity budget rule for this system:** on any given view, **one** hue may be "loud" (the hero's status color). Everything else is neutral ink + hairline. Color returns only to flag a threshold breach (over budget = `block`, awaiting approval = `risk`, healthy = `ok`).

---

## 3. Per-surface clutter audit + concrete redesign

Priority order matches the founder's ask: **Management/Operations Dashboard → Admin settings → money-shot Console**, then the chrome.

---

### 3.1 Operations / Management Dashboard  ★ primary focus
`components/dashboard/Dashboard.tsx`, `MetricsDeck.tsx`, `RoiPanel.tsx`, `DashboardCharts.tsx`

#### What's cluttered
- **No hero.** The view opens with `MetricsDeck` = four equal-weight cells (cache-hit, small-model share, cost/1k, quality) each with its own hue, icon, delta, sparkline and sub-caption. Four co-equal instruments, no ranking. Then `RoiPanel` (which *has* a real hero — the ticking `cost_saved_usd`) is demoted to a 2/3 column beside two illustrative tiles.
- **`RoiPanel` is a dashboard inside a card.** It stacks a hero, a 2-up "cost at scale" projection, a 3-up "manual vs agent" comparison, and **two** "sample assumptions" notes — ~7 numbers and two disclaimers in one card. It out-weighs everything around it and buries its own headline.
- **Measured and illustrative are interleaved.** "Queries today 2,870" and "Actions approved 41" (both `illustrative`) sit at equal weight beside real telemetry; the charts carry three "sample data" badges. The eye can't tell the trustworthy signal from the placeholder, and the repeated badges are visual noise.
- **Color as decoration.** Cost is amber (`risk`), quality is purple (`ml`), cache is green (`ok`), small-model is mint (`agent`) — purely categorical. A first-time viewer reads four "important, different" colors and learns to ignore all of them (violates §1.8).
- **Object count:** 4 deck cells + ROI hero + 4 ROI sub-tiles + 2 illustrative KPIs + 4 charts ≈ **15 competing objects** before any scroll.

#### Redesign — the target (detail in §4)
Keep: the four live telemetry numbers, the ROI hero, one trend chart, one breakdown. Regroup into a strict inverted pyramid; demote ROI's math and the illustrative projections behind disclosure; neutralize decorative color.

**Current (schematic):**
```
┌───────────────────────────────────────────────────────────┐
│ Live telemetry  [cache 84%]│[small 61%]│[cost $3.2]│[qual .91]│  4 equal hues
├───────────────────────────────────────────────────────────┤
│ ┌── ROI (2/3) ─────────────────────┐ ┌ Queries 2,870 (illus)┐│
│ │ HERO $ saved  + cost@scale (2)   │ ├ Approved 41   (illus)┤│
│ │ + manual-vs-agent (3) + 2 notes  │ └──────────────────────┘│
│ └──────────────────────────────────┘                        │
├───────────────────────────────────────────────────────────┤
│ [Cost area (sample)] [Donut] │ [Volume bar (sample)][table] │
└───────────────────────────────────────────────────────────┘
```

**Target (schematic — full spec in §4):**
```
┌───────────────────────────────────────────────────────────┐
│  Operations            This month ▾        ● live /metrics  │  one control row
├───────────────────────────────────────────────────────────┤
│  HERO — [primary persona metric slot]                       │
│  $ 128,400  ▲ 12%      one plain-English line of context    │  1 loud number
│  ▁▂▃▄▅▆▇  (sparkline)                                        │
├───────────────────────────────────────────────────────────┤
│  Cache 84% ▲ │ Small-model 61% ▲ │ Cost/1k $3.20 ▼ │ Qual .91 │  4 neutral inputs
│  ▁▂▃ spark   │ ▁▂▃ spark        │ ▁▂▃ spark        │ ▁▂▃      │
├───────────────────────────────────────────────────────────┤
│  Cost & quality over time  ──────────────────────  [hero]   │  1 full-width chart
│                                                             │
├───────────────────────────────────────────────────────────┤
│  Model mix / routing  (clean table)      ▸ How we save $    │  1 table + disclosure
└───────────────────────────────────────────────────────────┘
```

**Concrete moves**
- **Promote the ROI hero to *the* page hero.** `cost_saved_usd` (ticking) becomes the single top number — full-width band, `2.75rem` value, one plain caption ("42% cheaper than frontier-only — from caching + small-model routing"), one sparkline. Keep the `.tabular` count-up.
- **Demote ROI's math.** Move "Cost at scale" and "Manual vs agent" (and their two sample notes) into a `▸ How we calculate savings` expander (`components/ui` — a `<details>`/disclosure), collapsed by default. This is exactly §1.7: frequent (the saving) stays; rare (the derivation) defers.
- **Turn the deck into supporting inputs.** Keep the four `MetricsDeck` cells but **neutralize them:** icon chips become neutral (`text-muted-foreground` on `bg-surface-2`), sparklines monochrome (neutral), and color appears **only** on the delta when it crosses a threshold. They are now clearly the second tier under the hero.
- **Cut the illustrative KPIs from the exec view.** "Queries today / Actions approved" are placeholders — remove them from the management dashboard, or move them into the "Details" disclosure clearly separated under a "Projections (illustrative)" heading. One honest hero beats two placeholders.
- **One hero chart, one breakdown.** Collapse the 2×2 chart grid to a single full-width trend (cost *or* quality over time — the founder's persona will pick which; see §4) plus one breakdown (model-mix donut *or* routing table). The remaining sample charts move into the disclosure.
- **Sample badges:** with placeholders removed from the primary view, the repeated "sample data" tags largely disappear from the fold — the remaining ones live inside the disclosure where the "illustrative" framing is set once.

---

### 3.2 Admin settings (tenants / users / budgets / usage)
`components/admin/AdminSettings.tsx` + `TenantsView` / `UsersView` / `BudgetsView` / `UsageView`

#### What's cluttered
- Structure is already good — a four-tab well is the right progressive-disclosure move ("who / capped how / spending what"). The clutter is *within* tabs:
- **Budgets tab shows the create-form permanently beside the table** (`grid-cols-[1.4fr_1fr]`). Creating a cap is occasional; the form competes with the data every visit and adds ~8 inputs of visual weight.
- **Tables are legible but noisy in the details:** everything is `0.72rem` mono; numeric columns (Tokens / USD / RPM / TPM, Created) are **not right-aligned** in Tenants/Users, so digit places don't line up (violates §1.13). The Tenants `ID`/`#id` column is low-signal chrome.
- **UsageView** is fine and close to the target pattern (3 KPIs → spend-by-model bars → trend) — use it as the model for the others.
- **Empty states** are honest but terminal ("No users in this scope.", "No budgets set. Create one on the right.") — they state status but don't offer the action inline (§1.14).

#### Redesign
```
Admin  ┌ Tenants ┬ Users ┬ Budgets ┬ Usage ┐   ← keep the tab well
       └─────────┴───────┴─────────┴───────┘

BUDGETS (target):
┌───────────────────────────────────────────── [ + New cap ] ┐  ← primary action, top-right
│  Scope        Window   Tokens    USD     RPM     TPM        │
│  tenant #2    month   5.0M      $1,200    600    200k       │  ← numbers RIGHT-aligned, tabular
│  user #7      day      500k      $80       60     20k       │
│  … clean hairline rows, no vertical rules …                 │
└─────────────────────────────────────────────────────────────┘
   (create form opens in a Dialog from "+ New cap" — components/ui/dialog)

   Empty:  "No caps yet — spend is currently unbounded.  [ Create the first cap ]"
```
- **Move the budget create-form into the existing `Dialog`** triggered by a `+ New cap` button in the card header. The table becomes the whole tab; the form appears on demand (§1.7). Reuse the exact `Field`/form JSX — just relocate it.
- **Right-align every numeric column** and add `.tabular` (Budgets already tabular on cells; add alignment). Match headers to column alignment. Drop the Tenants `ID` column (or fold `#id` into a muted suffix on the name). Keep the single hairline row border you already use (`border-border/40`) — that's correct; don't add more.
- **Upgrade empty states** to carry the next action (the `Dialog` trigger), per §1.14.
- **Usage stays as-is structurally** — it's the reference. Consider bumping the 3 KPIs to the shared KPI component so all admin numbers share one anatomy.

---

### 3.3 The money-shot Console
`components/console/MoneyShotConsole.tsx` (+ its 9 panels)

#### What's cluttered
- This is an **intentional** high-density demo ("every element on screen is a rubric axis being scored") — it should stay dense. But even here, clarity is lost in one specific way: the **answer** — the actual output a viewer cares about — is `AnswerPanel`, buried in the *middle* of the center column beneath the knowledge graph, rerank scoreboard and reasoning lane, competing with 8 sibling panels at equal weight (trace, graph, rerank, reasoning, answer, guardrail, dual-verdict, shap, efficiency).
- Nine co-equal panels means no focal point; the eye has nowhere to land first.

#### Redesign (surgical — preserve the "wow", add a focal point)
- **Give the answer primacy in the center column.** Order the center stack **Answer → Reasoning → Guardrail → Graph/Rerank**, or visually lift `AnswerPanel` (slightly stronger card, a hair more padding) so it reads as the destination of the flow. The graph can move below the answer or share the top with it. The trace (left) and ML rails (right) stay as the "instruments" framing the result.
- **Keep the spotlight pattern** — `ApprovalSpotlight` scrimming the console for a decision is exactly right (a forced single focus); it's the one place the console already nails hierarchy. Use it as the model for the rest.
- **One active hue at a time** is already the design intent (`beatFromSignal` pulses one subsystem) — lean into it: idle panels should sit more neutral so the pulsing subsystem is unmistakably the live one.
- **Present mode** (`F`) is a strong clarity asset — it already strips chrome and enlarges. No change; just ensure the answer-primacy ordering carries into it.
- Do **not** try to reduce panel count here — the density is the pitch. Only add the focal point.

---

### 3.4 Chrome — Sidebar & Topbar
`components/layout/Sidebar.tsx`, `Topbar.tsx`

#### What's cluttered
- **Every nav row carries a mono `hint` sub-caption** ("the money-shot", "KPIs & cost", "two users, one system", "async human gate", "tenants · budgets · usage", "traceability"). Six two-line rows turn a nav into a paragraph. The hints are personality, not wayfinding.
- **Topbar** is reasonable, but the global **Search is a non-functional placeholder** occupying prime real estate, and the notification dot implies unread state that isn't wired.

#### Redesign
- **Drop the per-row `hint` by default** — single-line nav rows (label + icon + active accent bar, which you already have). Keep the group headings ("Workspace" / "Governance") — those *are* useful grouping (§1.2). If a hint is truly needed, make it a `title`/tooltip, not persistent ink.
- **Either wire or hide** the topbar search; a dead search field erodes trust. If it must stay for the demo, make it obviously a command hint (`⌘K`) rather than a full input, or gate it behind an icon.
- Keep breadcrumb, theme toggle, present affordance, and user chip — those are all earning their place.

---

## 4. Management dashboard — the target (executive-clear)

**Goal:** any user, including non-technical leadership, reads it in **5 seconds** — what's the headline, is it good, do I act? Structure below is final; **which** metric is the hero depends on the founder's "most important persona" story — see the marked slot.

### Layout spec

```
╔════════════════════════════════════════════════════════════════════╗
║ ROW 0 — CONTEXT BAR                                                 ║
║  Operations                         [ This month ▾ ]   ● live       ║
║  (h1, semibold)                     (single range control)  (pip)   ║
╠════════════════════════════════════════════════════════════════════╣
║ ROW 1 — THE HERO  (full width, generous padding, one loud color)   ║
║                                                                    ║
║   ▸▸ PERSONA METRIC SLOT ◂◂                                         ║
║   Cost saved vs frontier-only              ▲ 12% vs last month      ║
║   $128,400                                                         ║
║   (font-display 2.75rem, .tabular, count-up)                       ║
║   "42% cheaper than running every query on the frontier model —    ║
║    from caching + small-model routing."   (one plain sentence)     ║
║   ▁▂▃▄▅▆▇▇  (single sparkline, neutral or ok-tinted)               ║
╠════════════════════════════════════════════════════════════════════╣
║ ROW 2 — SUPPORTING INPUTS  (4 neutral tiles, equal, tabular)       ║
║  ┌────────────┬────────────┬────────────┬────────────┐            ║
║  │ Cache-hit  │ Small-model│ Cost / 1k  │ Quality    │            ║
║  │ 84%   ▲2pt │ 61%   ▲3pt │ $3.20 ▼$.1 │ 0.91  ▲.01 │            ║
║  │ ▁▂▃ (mono) │ ▁▂▃ (mono) │ ▁▂▃ (mono) │ ▁▂▃ (mono) │            ║
║  └────────────┴────────────┴────────────┴────────────┘            ║
║   neutral icon chips; color ONLY on a delta that crosses threshold  ║
╠════════════════════════════════════════════════════════════════════╣
║ ROW 3 — ONE HERO CHART  (full width)                               ║
║   Cost & quality over time            [ Cost | Quality ] toggle    ║
║   ────────────────────────────────────────────────────────        ║
║   (single trend; monochrome line, one accent; no gridline clutter) ║
╠════════════════════════════════════════════════════════════════════╣
║ ROW 4 — ONE BREAKDOWN + DISCLOSURE                                 ║
║   Model mix / routing (clean table, numbers right-aligned)         ║
║   ────────────────────────────────────────────────────────        ║
║   ▸ How we calculate savings   (cost-at-scale + manual-vs-agent +  ║
║                                  illustrative projections live here)║
╚════════════════════════════════════════════════════════════════════╝
```

### Rules for this view
- **Exactly one loud number** (Row 1). Everything below is neutral ink until a threshold makes color meaningful.
- **≤ 4 supporting KPIs** (Row 2), all sharing one KPI anatomy (label · number · delta · sparkline), all `.tabular`.
- **One hero chart** (Row 3), with a segmented toggle instead of two side-by-side charts.
- **One breakdown table** (Row 4), plus **one disclosure** that absorbs all the ROI math and the illustrative projections — nothing illustrative appears above the disclosure.
- **Whitespace does the grouping** (§1.2): the four rows are separated by generous vertical gap (`gap-6`/`gap-8` on the existing 4px scale), not by heavy card borders.
- **Empty/first-run state** (before the first `/metrics` sample): the hero reads a skeleton with "Awaiting first query" (you already do this), not a fabricated number — honest per §1.14.

### ▶ PERSONA METRIC SLOT (leave for the founder)
> **This is the one decision the brief intentionally defers.** The founder will supply the "most important persona" user-story + chart. It determines:
> 1. **Which metric is the hero** (Row 1). Default placeholder: `cost_saved_usd`. Candidates the backend already exposes: `cost_saved_usd`, `cost_per_1k_queries_usd`, `quality_score`, `cache_hit_rate`, `small_model_share`.
> 2. **Which trend the hero chart defaults to** (Row 3) — align it to the hero (e.g. hero = savings → chart = cost over time).
> 3. **Which 3–4 inputs** best *explain* the hero's movement (Row 2).
>
> Build the **structure now** (four rows + disclosure + neutralized color); wire the specific metric IDs when the persona story lands. Nothing above blocks on it.

---

## 5. Prioritized punch-list (implementer-ready)

Ordered by clarity-return per unit effort. Each item is scoped to existing files/components.

### P0 — the management dashboard (biggest win, do first)
1. **Restructure `Dashboard.tsx` into the four-row inverted pyramid** (§4): hero band → 4 supporting tiles → one hero chart → one breakdown + disclosure. Use `gap-6`/`gap-8` vertical rhythm.
2. **Promote the ROI hero out of `RoiPanel` into the page hero band**; keep the ticking `useCountUp` and one sparkline; one plain-English caption.
3. **Move ROI math ("Cost at scale", "Manual vs agent", both sample notes) into a `▸ How we calculate savings` disclosure** (collapsed). Move the illustrative KPIs and the two sample charts into the same disclosure under a "Projections (illustrative)" heading.
4. **Neutralize `MetricsDeck` color:** neutral icon chips + monochrome sparklines; color returns only on threshold-crossing deltas. This alone fixes the "color means nothing" problem (§1.8).
5. **Collapse the 2×2 chart grid to one full-width trend + segmented Cost|Quality toggle** (`components/ui/tabs`), plus one breakdown (model-mix donut or routing table).

### P1 — admin clarity
6. **Budgets: move the create-form into a `Dialog`** behind a `+ New cap` header button; the table becomes the whole tab.
7. **Right-align + `.tabular` all numeric table columns** (Tenants, Users, Budgets); match header alignment; drop the low-signal Tenants `ID` column (or mute it into the name).
8. **Upgrade admin empty states** to carry the next action inline (open the create Dialog) per §1.14.

### P2 — console focal point
9. **Give `AnswerPanel` primacy** in the console center column (reorder Answer→Reasoning→Guardrail→Graph, and/or lift its card weight). Keep everything else; the density is the pitch.
10. **Neutralize idle console panels** so the `beatFromSignal` pulse unmistakably marks the one live subsystem.

### P3 — chrome & global polish
11. **Drop the persistent nav `hint` sub-captions** (single-line rows; move any essential hint to a `title` tooltip). Keep group headings.
12. **Wire or hide the Topbar search** (or convert to a `⌘K` affordance); only show the notification dot when there's real unread state.
13. **Global: apply `.tabular` to every number** that isn't already (KPIs, table cells, deltas) so live updates don't jitter (§1.11).
14. **Audit color usage repo-wide against the "one loud hue per view" rule** — every remaining hue should encode status, not category.

### Definition of done (per surface)
- Passes the **5-second test**: hero, its good/bad status, and "do I act?" are legible at a glance.
- **≤ 7 objects** above the fold on the management dashboard; **exactly one** loud color.
- No illustrative/placeholder number appears above a disclosure on an exec surface.
- Every table: text left, numbers right, one hairline rule, tabular figures.
- No functional identity/token change — same SnowUI light system throughout.
