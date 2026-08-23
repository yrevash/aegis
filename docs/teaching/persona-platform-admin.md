# Demo walkthrough — the Platform admin portal

> **Who this is for.** You, standing in front of the jury with `admin` signed in.
> Every screen in this portal, what is on it, where each number comes from, the
> sentence that lands the point, and the honest answer to the question you will
> get back.
>
> **Verified against the running system on 2026-08-23.** Figures quoted below are
> from that moment and are **illustrative** — they will differ on the day. The
> *sources* (endpoint, table, role) will not.

---

## Before you start

| | |
|---|---|
| Sign in | `admin` / `demo` — or the **Platform admin** button under DEMO ACCESS |
| Lands on | `/app/platform_admin/dashboard` |
| Frontend | http://localhost:3001 |
| Backend | http://127.0.0.1:8110 |

**The one fact that shapes this whole portal: `platform_admin` has no owning
tenant.** The login response for `admin` carries `tenant_id: null`. Aegis has two
admin tiers — the *platform* operator who runs Aegis, and a *tenant's own* admin
who runs one customer — and they are different roles with different portals.
Several screens in this portal behave differently because of that null, and each
time it is deliberate. Say so before a jury asks.

### The nav, in order

The rail groups the twelve sections under two headings. This is the order the
screens appear in and the order this document walks them.

**Workspace** — Overview · Analytics · Forecast · Console
**Governance** — Approvals · Governance · Roles & Access · Jobs · Audit · Database · MCP · Settings

*(Source: `web/src/lib/portal.ts` → `ROLE_SECTIONS.platform_admin`, grouped by
`web/src/components/layout/navGroups.ts`. There is no second list anywhere; a
test reads that file.)*

**Compliance is not on this portal.** Aegis has a Compliance screen — 114 controls
across 12 frameworks — but it lives on the **DevOps** portal, not here. If a juror
asks about compliance, say where it is rather than improvising.

### Chrome that is on every screen

| Element | What it is |
|---|---|
| Breadcrumb | *Platform admin portal / <section>* |
| Text-size control | Steps the whole console's type scale. It is in the top bar *and* on Settings on purpose — the person who needs it is already on a page they cannot read. |
| Alerts bell | Real `GET /notifications` rows (ingest finished, approval raised…), each linking to the screen that shows the thing. |
| Signed in as | `admin`, Platform admin portal |

---

## 1 · Overview

*Route: `/app/platform_admin/dashboard` · Component: `dashboard/AdminCommandCenter.tsx`*

### What this screen is for

The single pane of glass for the person who runs Aegis itself. It answers, in
order: what is this costing, what needs attention, which customers are close to
their limits, where the money went, and is anything unhealthy.

### What is on it

| Panel | What it shows | Where the number comes from |
|---|---|---|
| **Total spend, 30 days** (double-width tile) | Metered spend across every tenant | `GET /admin/usage?window=month` — the `usage_ledger` table. The sparkline is the ledger's own daily buckets. |
| **Cost saved vs frontier** | What the workload would have cost on the frontier model, minus what it did cost | `GET /gateway/optimization` → `summary.cost_saved_usd` / `baseline_cost_usd`. The delta pill is the ratio. |
| **Queries served** | Metered model calls | `summary.total_calls` from the same call |
| **Small-model share** | Share of calls that landed on a cheaper deployment | `summary.small_model_share` |
| **Cache hit rate** | Process-wide cache hits | `GET /metrics` |
| **Quality score** | The rolling quality figure | `GET /metrics` |
| **p95 latency** | 95th-percentile whole-run duration | `GET /latency` — an **in-process rolling window** that resets when the backend restarts |
| **Alerts** | Derived, not stored: tenants at ≥80% and ≥100% of cap, plus any security control that is `partial` or `not_covered` | Composed in the browser from `/governance/dashboard` budgets and `/security/posture` |
| **Customers & budgets** | Top 5 tenants by spend, each with calls, spend and a meter against its cap | `GET /governance/dashboard` → `tenants` + `budgets`. The join is on `scope_type`/`scope_id`, not `tenant_id`. |
| **Daily spend, by who it was billed to** | A stacked area, one band per tenant plus an *untenanted* band | `GET /admin/usage` called once per tenant over the same window |
| **Model mix** | Donut of spend per deployment | `GET /admin/usage` → `by_model` |
| **Model routing** | Which deployment each role (cheap / reasoning / generation / embedding / vision / voice) lands on | `GET /metrics.routing`, falling back to `/gateway/optimization` → `config.routing` |
| **Security posture** | % enforced, counts per state, and the top gap named | `GET /security/posture` — 12 OWASP-Agentic threats, status derived from live wiring at call time |
| **Latency** | p50 / p95 / max and the run count | `GET /latency` |

### What to say when demoing it

> "Every tile on this screen carries its source under it. Total spend is not a
> guess — it is the `usage_ledger`, the same table the budget enforcer reads
> before it refuses a call. And the stacked chart underneath is that same ledger
> asked once per tenant, so the bands sum to the platform total with nothing
> dropped."

Then point at the **untenanted** band:

> "That band is platform work that belongs to no customer. We show it rather than
> silently attributing it to someone."

### What a jury might ask

**"Is p95 latency across all time?"**
No. It is a per-process rolling window and it resets when the backend restarts.
The tile says so in its source line. We do not have per-tenant latency at all —
the ledger records tokens, units and cost, never duration. That gap is written
down on the Forecast screen as a stated absence.

**"Why is small-model share 0%?"**
Because on this deployment every text role is routed to the same deployment
(`DeepSeek-V4-Flash`) — the routing table on this very screen shows it. The
savings figure is genuine and comes from a different lever (the baseline role vs
the actual role mix), but "small-model share" is honestly zero here rather than
dressed up.

**"Those customer names look like test data."**
They partly are — see *Known rough edges* at the foot of this document.

### Anything deliberately absent

- **The stacked spend chart refuses to draw with one bucket.** If the ledger has
  fewer than two days it prints an `Absence` naming what is missing and what
  would fix it, rather than a flat line through one point.
- **Sparklines appear only where a real series exists.** A tile with a single
  sample gets no sparkline at all — the component (`movingSeries`) withholds it,
  because a horizontal rule under a number reads as a chart that failed to load.
- **A failed panel resolves into its own empty state**, not a spinner that hangs.
  Every fetch is `Promise.allSettled`.

---

## 2 · Analytics

*Route: `/app/platform_admin/analytics` · Component: `analytics/AnalyticsView.tsx`*

### What this screen is for

Two halves. The top half is Aegis drawing the metered usage ledger itself. The
bottom half is **Apache Superset** — a real BI server — rendering its own boards
inside this page. The split matters: the top half keeps working when Superset is
off.

### What is on it

**Top half — Aegis's own charts, from `GET /admin/usage`**

| Panel | What it shows |
|---|---|
| Window switch | 30 days / 24 hours — the only two windows the endpoint implements |
| Metered spend · Tokens · Models in play · Days with traffic | Four figures the rest of the page decomposes |
| Spend per day / per hour | Area chart over the ledger's own buckets. *Summed, never averaged — an hour with no rows is an hour nobody used.* |
| Spend by model | Donut over `by_model` |
| Cost per 1k tokens | `cost_usd ÷ tokens` per model — **the rate actually paid, not list price** |
| Weekday rhythm | Mean per day of week |
| Hour of day | Total per hour, UTC |

**Bottom half — `Insight boards`**

Verified live: `GET /analytics/status` reports Superset answering at
`http://localhost:8088`, with **20 boards for the platform admin role**. One
board opens as an embedded Superset dashboard (the hero); the rest render as a
gallery of small multiples, each one a query the server compiled.

Boards include: spend / runs / human gates / background jobs / governed actions /
token volume / run latency over time; spend by model; runs by outcome; human
gates by risk; gate decisions; job throughput; job outcomes; governance trail;
red-team defence and block rate; plus two dashboards — **Operations** and
**Tenant insights**.

### What to say when demoing it

> "This is Superset, running for real, embedded here. The backend builds the
> query, mints a short-lived guest token, and every row is narrowed to the
> caller's tenant by a `WHERE` clause the browser cannot remove or edit — it is
> signed into the token. As the platform operator I get 20 boards. A tenant admin
> gets 19: the platform-wide Operations dashboard is not theirs."

### What a jury might ask

**"Is the tenant filter enforced in the browser?"**
No. It is a signed RLS clause inside the guest token Superset receives. Changing
anything client-side cannot widen it.

**"What if Superset is down on the day?"**
The top half of the page is unaffected — it reads the ledger directly. The
Superset section then renders a designed absence: the backend's own sentence, the
command to fix it, and the three capabilities the add-on brings that the ledger
charts genuinely cannot. There is **no greyed-out chart skeleton**, deliberately:
a placeholder chart on an analytics page is indistinguishable from a real one.

**"Can you enumerate the boards you're not allowed to see?"**
No. A board you are not an audience for returns the same 404 as a board that does
not exist.

### Anything deliberately absent

- **Cost per 1k tokens excludes audio and image deployments** — they bill by
  second and by frame, so a token rate for them would be a fabricated number.
- **A board that returns no rows says exactly that**, in the space its chart
  would occupy.

---

## 3 · Forecast

*Route: `/app/platform_admin/forecast` · Component: `forecast/ForecastView.tsx`*

### What this screen is for

Spend and demand projected forward — with the interval coverage that was actually
*measured*, not the coverage that was requested.

### What is on it

- **Controls** (page header): tenant selector (*All tenants (platform)* by
  default — this selector renders **only** for a platform admin), measure
  (spend / calls), horizon, refresh.
- **Four figures**: next step, projected total over the horizon, held-out error
  (sMAPE), coverage achieved vs coverage requested.
- **Card 1 — the band.** Every projected point is drawn as a **band**, never a
  line. `GET /forecast/budget` or `/forecast/usage`, `statsforecast` with a
  conformal interval.
- **Card 2 — burn-down against the cap.** The headline is *the date the budget
  runs out*, and the crossing is marked on the curve.
- **Card 3 — how the model was chosen.** Rolling-origin held-out backtest, the
  candidates, the winner.
- **Explainability panel.** SHAP over a *different* model — the supervised spine
  — with a sentence saying so out loud.
- **Exports panel.** Four CSVs, each streamed from the server, each audited.
- **"What this page cannot tell you"** — a disclosure with **5 stated absences**.

### ⚠ On this deployment, right now, the forecast refuses

Verified live for both platform and tenant scope:

```
{"available": false, "forecast": null, "burndown": null,
 "refusal": {"code": "insufficient_history",
   "reason": "Forecasting 14 step(s) of 'D' data needs 3 held-out backtest window(s)
     plus enough history before the earliest cutoff to fit two seasonal cycles
     (season=7) and calibrate 3 conformal window(s)",
   "have": 2, "need": 71}}
```

The demo data was seeded on 2026-08-22, so the ledger holds **2 days** where the
fit needs **71**. The screen renders that refusal verbatim.

**Do not treat this as a broken screen — demo it as the point.** Say:

> "Two days of history. It needs seventy-one to fit two seasonal cycles and
> calibrate three conformal windows. So it refuses, and it tells you exactly what
> it would need. Most dashboards would draw you a line here. A line through two
> points is a lie with a confidence interval on it."

If you would rather show a live forecast, you need ~10 weeks of daily ledger
buckets — that is a data-seeding job, not a code change. Decide before the demo,
not during it.

### What a jury might ask

**"How accurate has this forecast been?"**
Unknown, and the page says so as one of the five stated absences: nothing stores
a forecast at the moment it is made, so no forecast has ever been scored against
the days that followed. The backtest is evidence about the *method*, on windows
held out from the fit.

**"Which features drive spend?"**
None — the spend forecast is univariate, its only input is its own history. The
SHAP panel below is a different model over a different table, and the page says
that in a sentence between them rather than putting them on one chart.

### Anything deliberately absent (all five, verbatim in the disclosure)

1. Error rate of model calls — `usage_ledger` has no outcome column, so any error
   rate computed from it is 0% by construction.
2. Per-tenant latency at any percentile — a ledger row records tokens, units and
   cost, never duration.
3. How accurate this forecast turned out — see above.
4. Which features drive spend — univariate.
5. What the spine would predict without a given feature — that is a training job,
   and `POST /ml/experiment` is **not built**.

---

## 4 · Console

*Route: `/app/platform_admin/console` · Component: `console/ChatConsole.tsx`*

### What this screen is for

Where you actually ask the agent something. On this portal it is load-bearing
rather than a courtesy: **the only gates a platform admin may decide are gates
carrying no tenant, and only an un-tenanted run raises one.** Without this console
your own Approvals queue could only ever be filled by somebody else.

### What is on it

| Element | What it shows |
|---|---|
| Chats control | The caller's own `GET /sessions` list — real `chat_sessions` rows under RLS — merged with chats started in this tab |
| Composer | The question box, plus Mode, Model and Image pickers |
| Budget line | `$x of $y` — reads `GET /me/budget`, the *same* `BudgetStatusRow` set the gateway compares every call against. When no cap governs you it says so rather than printing `$0.00 of $50`. |
| Seed questions | The adapter's own configured questions — real configuration, not invented examples |
| "What happens to a question" | The path: input rail (6) → route → retrieve & answer → output rail (6) |
| **Run panel** (live) | Lane board (the fan-out), a live feed of what is happening now, run figures and four trust checks |
| Approval spotlight | When a run hits the gate, the screen scrims and blurs behind the decision |
| **Answer** | Streamed, with `output checked` and the sources it *stands on* |
| **Sources tab** | The passages the answer stood on |
| **Trace tab** | Routing and self-repair decisions, guardrail glass box, per-node timing and cost, the raw event log, graph traversal, the trace id, and the **checkpoint timeline** |

### The demo that wins this section — the durable interrupt

The human gate is a real `langgraph.types.interrupt`. With
`AGENT_CHECKPOINTER=postgres` the pause is written to Postgres, so:

1. Ask something consequential. The run parks at the gate.
2. **Restart the backend.**
3. Approve it from the Approvals screen.
4. The run resumes — and the **Checkpoint timeline** in the Trace tab shows one
   tick per persisted checkpoint, the gate marked where it parked, and the
   continuation drawn hanging off that same tick.

> "That is the question a reviewer actually asks — did the resume continue from
> the gate, or quietly re-run the graph from the top? This is the answer, drawn
> from the checkpoint rows themselves."

The `checkpoints` table held ~598 rows on 2026-08-23 (visible on the Database
screen). Quote it as "roughly six hundred and growing", not as a fixed number.

### What a jury might ask

**"Are the per-node costs real?"**
Yes. Each node emits a `node_finished` event carrying its model, tokens, duration
and USD. A local node (guardrails, routing, gate, reflect) shows `—` for cost
rather than `$0.00`, because it never called a model.

**"Can I see a run that failed?"**
Yes, and it is more interesting. The event log shows tool errors verbatim (a
Pydantic enum validation failure, a "no skill named X is in force for you"), the
reflect node re-planning, and the iteration budget being exhausted at round 2/2.
Nothing is smoothed over.

### Anything deliberately absent

- **A turn read back from stored history has no trace and no agent cards**, and
  says so. `run_events` is not persisted for replay, so rather than showing an
  empty trace the console states that the event log for that turn was not stored.
- **Vision verdicts are shown before the answer**, in the order the rails ran, so
  the story is not told backwards.

---

## 5 · Approvals

*Route: `/app/platform_admin/approvals` · Component: `approval/ApprovalInbox.tsx`*

### What this screen is for

Every action the agent paused on rather than took. **On this portal it means two
things at once**: gates from Aegis's own un-tenanted runs are yours to decide, and
every tenant's gates are yours to *see* and not to vote on.

### What is on it

**The board (one instrument, not five boxes)**

| Element | What it shows | Source |
|---|---|---|
| Queue switch | Waiting / Decided / Everything | Server-side filter on `GET /approvals` |
| Raised | Last 24 hours / 7 days / since the beginning | `since` parameter |
| **Whose gate** | Every tenant, or one — **this selector renders only for a platform admin** | `tenant_id` parameter |
| Waiting figure | The screen's one display numeral | Count of the rows this query loaded |
| **Urgency ladder** | Five bands, worst first, each with icon + word + count | How much SLA each waiting gate has left |
| Already decided | Approved / rejected / expired, as a split bar | Same query |

**Each waiting gate** carries: what would run if approved (every call, with its
args), why a person is required (the `rationale`), whose it is, the SLA rail
showing how much of the deadline is spent, a gate receipt, the run id, and a
two-step Approve / Reject.

### The isolation demo, on one screen

This is verified live and it is the strongest thing on this portal.

There is one pending gate in the system — `seed-gate-vertex`,
`cancel_shipment`, HIGH risk, belonging to **tenant 2 (Vertex Logistics)**.

| Signed in as | Sees it? | `decidable` | What the screen says |
|---|---|---|---|
| `admin` (platform) | **Yes** | `false` | *"This gate belongs to a tenant. A tenant's own admin decides it — the platform operator sees it, and does not vote on it."* |
| `vertex.admin` | Yes | `true` | Approve / Reject are live |
| `northwind.admin` | **No** — the row does not exist for them | — | Empty queue |

### What to say when demoing it

> "Watch what happens to the buttons. They are drawn, and they are disabled, and
> underneath them is the server's own sentence saying why. We could have hidden
> them — but hiding the control hides the rule. We could have enabled them — and
> earned a 403. And we could have worked the rule out in TypeScript, which would
> be a second copy of the policy that can drift from the one the button is about
> to hit. So the server sends `decidable: false` and its own reason, and the
> screen renders it."

Then sign in as `vertex.admin` in a second window and show the same gate live.

### What a jury might ask

**"What happens if nobody decides?"**
Not deciding is itself a decision, and the screen says so. A sweeper marks a
past-deadline gate `expired` and **auto-rejects a HIGH-risk one**. The waiting
list is ordered by how little SLA is left, not by arrival, for exactly that
reason.

**"Can you approve by accident?"**
No. Both decisions are two-step: a sentence naming the specific calls, a coloured
commit button, and "Keep waiting". Approving executes a real tool action against
a real system; rejecting ends a parked run. Neither can be taken back from this
screen.

### Anything deliberately absent

- **The half of the board this query did not load prints "Not counted in this
  queue"**, not `0`. If you asked the server only for decided gates, you have not
  been told nothing is waiting — you have been told nothing at all, and printing
  a zero there would be the dashboard inventing a fact in your favour.

---

## 6 · Governance

*Route: `/app/platform_admin/governance` · Component: `governance/GovernanceView.tsx`*

### What this screen is for

The one question this screen exists to answer: **is any tenant about to run out of
what it is allowed to spend?** Everything else on it is the ledger underneath that
answer.

### What is on it

| Panel | What it shows | Source |
|---|---|---|
| Scope badge | *platform scope · every tenant* | Derived from `fine_role`, not from `role` |
| **Spend against cap** | One gauge per tenant. The ring shows whichever of the USD cap and the token cap it is **closer to**, because the tighter one is what will stop work. Bars beneath show both. | `GET /governance/dashboard` → budgets joined on `scope_type`/`scope_id` |
| Calls · Tokens · Cost | The window's usage, over one shared hairline, with the cost shape as a band beneath | `usage` from the same call |
| Tenants & budgets | Every tenant with its cap, spend and remaining | same |
| Cost by model | The 8 costliest of N models | `usage.by_model` |
| Users & roles | Username, role, tenant (by name with the id beneath), personal cap, id | `users` from the same call |
| Recent audit tail | Actor · action · time, 50 rows | `recent_audit` from the same call |

### What to say when demoing it

> "One call — `GET /governance/dashboard` — builds this entire screen. And the
> gauge is deliberately not an average: a tenant with 4% of its dollars spent and
> 97% of its token allowance burned is not comfortable, and a screen that averaged
> the two would tell you it was. The ring shows whichever cap is closer."

And on over-cap:

> "A tenant at 180% of its token cap prints 180%. The arc clamps at the ring; the
> read-out does not. Rounding an overrun down to a full ring is the one lie this
> screen cannot afford."

### What a jury might ask

**"Is this screen read-only?"**
Yes. Creating tenants, provisioning users and setting caps all live on **Roles &
Access**, behind one drawer. This screen is the state; that screen is the writes.

**"Where do the caps actually get enforced?"**
In the gateway, against the same `usage_ledger` these figures are summed from.
That is why the Console's budget pill reads `GET /me/budget` — the same
`BudgetStatusRow` set — rather than a second figure that could eventually
disagree with a refusal.

### Anything deliberately absent

- **A portal role is not painted like a status.** Four roles used to take four
  badge tones — colour that means nothing sitting next to reserved hues that mean
  a great deal. The role is now told apart by its word.
- **The cost-shape band refuses to draw a curve through one point** and says so:
  *"the ledger returned one bucket for this window, so there is no shape to draw —
  a curve through one point would be invented."*

---

## 7 · Roles & Access

*Route: `/app/platform_admin/roles` · Components: `admin/RolesAccess.tsx`, `admin/AdminControls.tsx`, `admin/SeatsPanel.tsx`*

### What this screen is for

Who may do what, and what it may cost them. It is the **only** screen in this
portal that creates tenants and users.

### What is on it, top to bottom

1. **Delegation map** — the permission model before the roster that assigns it.
   Each portal role with its head count, its share as a bar, and the `sees` line
   that states the delegation contract that grant hands over.
2. **Counting strip** — Tenants (`GET /admin/tenants`), Users in scope
   (`GET /admin/users`, tenant-scoped server-side), Caps in force
   (`GET /admin/budgets`, with how many name a single user).
3. **Who has access** — the roster table: User (with email), Scope
   (*Platform — no tenant* or *tenant #N*), Holds, and an **Assign portal**
   dropdown per row.
4. **Named seats** — five revoke-only toggles per user plus a `seat.label`. Each
   carries a `source` badge: `platform` (nobody has touched it), `tenant` (off
   for everybody), `user` (set for this person specifically).
5. **Tenants** table and **Budgets** table — the tables the writes change.
6. **Manage access** drawer — three tabs: **New tenant**, **New user**, **Set a cap**.

### What to say when demoing it

> "There is a self-lockout guard in the roster, and it mirrors the backend rule
> rather than reimplementing it: you cannot strip the last admin access, and the
> option is disabled with the reason on it. And every seat toggle can only take
> capability *away* — the server folds a write against the enclosing scopes and
> the strictest value wins, so switching one back on restores what the tenant
> already permits and can never exceed it."

Open the **Manage access** drawer and create a tenant live:

> "A tenant cannot be created without a spend cap. And the moment it exists it is
> selectable in the user form and in the budget form — one loader behind all three
> readings, because a form that posts and shows a toast has not finished. You have
> to *see* the new state."

### What a jury might ask

**"Can a tenant admin do this?"**
Only part of it, and the screen says which part on their own portal:
- **New tenant** is refused: *"Aegis onboards tenants. Your admin rights end at
  your own tenant's users and their caps."* (`GET /admin/tenants` is
  `require_platform_admin` — a tenant admin is never even sent the request.)
- **New user** has no tenant picker: *"Pinned to tenant #N. Aegis fills this in
  from your sign-in — a user created here can only be yours."*
- **Set a cap** refuses the tenant scope: *"Aegis sets your tenant's own cap —
  raising it is not yours to do. You set the caps on your users."*

**"Is the refusal enforced in the browser?"**
No. The browser renders the refusal so the rule is visible; the server enforces
it whoever asks.

### Anything deliberately absent

- **A refusal is rendered where the control would have been**, not hidden. A
  hidden refusal is a hidden rule.

---

## 8 · Jobs

*Route: `/app/platform_admin/jobs` · Components: `jobs/JobsView.tsx`, `jobs/jobsPolicy.ts`*

### What this screen is for

Durable background work — the ingest pipeline and everything else queued on the
substrate. On this portal it is **read-only, and that is the interesting part.**

### What is on it

| Panel | What it shows | Source |
|---|---|---|
| **Pipeline funnel** | The six ingest stages (parse → chunk → enrich → embed → index → graph) as isometric solids, height by how many runs committed each. Each stage has a tip naming its queue, timeout and attempt budget. | Folded from the `GET /jobs` rows |
| Queue table | Job id + type, status, last committed stage, cost, created, detail, log | `GET /jobs` — **every tenant's rows** |
| Filters | All / In flight / Failed / Succeeded | client-side over the same rows |
| Ingest log | Per document, stage by stage, expanded inline | `documents.completed_stage` + the `run_events` row written inside that stage's own transaction |
| Corpus panel | This scope's documents | `GET /documents` |
| Pipeline health | Nine panels behind a disclosure — worker liveness, per-stage timings, dependencies | `GET /health` and friends |

### The banner you must explain

The toolbar carries a lock chip reading **"Read-only: no owning tenant"**, and the
Action column and the Upload panel are **gone**, not disabled.

### What to say when demoing it

> "This is the screen where you can see us following our own rule. Re-queue and
> cancel load the row as `WHERE id = :id AND tenant_id = :caller_tenant`, and my
> tenant is null — so every row I can *see* belongs to some tenant and every one
> of those buttons would return 403. Upload refuses outright, because
> `chunks.tenant_id` is NOT NULL. Neither route takes a tenant argument, so a
> tenant picker here could not supply the missing scope — it would send the
> identical request and collect the identical 403.
>
> So we do not draw the control. We say why, once, and the controls live on the
> tenant's own portal where they work."

Point out that the **Action column itself is removed**, not filled with disabled
buttons: a column of stated absences is still a column promising a control.

### What a jury might ask

**"So this is broken?"**
No — it is the deliberate application of one rule written down in
`web/src/lib/portal.ts`: *a portal must not offer a control the backend guard
makes impossible.* The predicate is the **tenant pin, never the role name**
(`canWriteJobs(tenantId)`), so a `tenant_admin` or an `ai_team` analyst reaching
this screen keeps every control — and if a platform admin were ever given a
tenant, the controls come back with no edit to the frontend.

**"What happens when a re-queue is refused?"**
It shows the admission gate's own reason — *"Refused by the <gate> gate — …"* —
rather than being silently queued out of sight.

### Anything deliberately absent

- **A blank Stage cell reads "none committed"**, not an em dash. A blank means the
  run never wrote a stage — which is different from starting at the beginning.
- **A deep link whose row the filter hides widens the filter and says so.** A
  screen that quietly changes your filter is a screen whose controls appear to
  move on their own.
- **A background poll that fails leaves the last good rows standing.** One dropped
  request is not an outage; the "updated at" stamp stops advancing, and that is
  the signal.

---

## 9 · Audit

*Route: `/app/platform_admin/audit` · Components: `admin/AuditLog.tsx`, `audit/AuditInsights.tsx`*

### What this screen is for

The append-only record of every action the platform took, who took it, under
which trace, and who approved it.

### What is on it

**Top — the insight layer** (`audit/AuditInsights.tsx`)

Charts lead, the trail sits beneath as the thing they are derived from. A
3,000-row log read as a table can only be *searched*, which means you have to
already know what you are looking for.

- **Lens chips** — one click each, and every one is a **server** predicate, the
  same `GET /audit` parameters the filter bar writes: **Refused** ·
  **Guardrail** · **Queries** · **Approvals** · **Uploads** · **Console reads** ·
  **Last 24h**.
- A completed-vs-refused trend on a window taken from the data, not the wall clock.
- Three ranked distributions: **Actions**, **Actors**, **Refusals**.

**Filter bar** — free text, time range, outcome (any / completed / blocked), row
limit, actor, action prefix, model, **tenant** (platform admin only), from/to.

**The trail** — Time · Action · Actor · Model · Trace · Approved by · Result.
Trace ids are click-to-copy. The result is a coloured dot **paired with its word**,
never colour alone.

**CSV export** — `GET /reports/audit.csv`: streamed with no row limit, scoped
through the sealed `TenantScope`, audited as `report.export` *before the first
byte*, opening with a preamble naming the scope, window, source and filters.

### What to say when demoing it

> "Append-only here is not a promise in a doc — it is a Postgres privilege. The
> serving role is `aegis_app`. It has `SELECT` and `INSERT` on `audit_log`;
> `UPDATE` and `DELETE` are revoked, and `TRUNCATE` is owner-only and it does not
> own the table. So the database refuses to alter this trail, not the application.
> I can show you in psql."

The proof, from `scripts/sql/aegis-app-role.sql`:
```sql
REVOKE UPDATE, DELETE ON public.audit_log FROM aegis_app;
-- same for run_events (and every month partition) and usage_ledger
```

Then the filter demo:

> "Every filter runs on the *server*. Changing one re-runs the query rather than
> hiding rows already on screen — so the figures above always describe exactly the
> set the table shows, and a search reaches the whole trail rather than the page
> in view."

### What a jury might ask

**"Where does the `blocked` outcome come from?"**
There is **no verdict column on the trail**. The outcome is classified
server-side by `aegis.governance.audit.classify_outcome`. The lens tip says so.

**"Does the CSV match what I see?"**
Not always, and the screen tells you when it will not: the export route takes the
actor, the action prefix and the time range, and nothing else. If you have set a
model or outcome filter, a bar appears saying the CSV *"cannot narrow by <those>,
so it will hold more rows than the table below."* A file that quietly holds more
than the table it came from is evidence of the wrong thing.

**"Why is `memory_write_log` not append-only?"**
Deliberately excluded, and written down: the DPDP/GDPR erasure route
(`POST /v1/memory/forget`) must be able to delete from it, and it runs in a
request handler — so the alternative would be an RLS-bypassing connection in the
request path.

### Anything deliberately absent

- **The insight figures count only the rows the server returned** — the newest
  `limit` rows matching the filter, never the whole trail — and the receipt says
  so. There is no honest way to extrapolate one to the other from a newest-first
  window, so nothing tries.

---

## 10 · Database

*Route: `/app/platform_admin/database` · Components: `db/DatabaseView.tsx`, backend `api/routes_db.py`*

### What this screen is for

Looking at the data without dropping out of the product into `psql`. **Platform
admin only** — `require_db_console` is `require_platform_admin`, never
`require_admin`, because the latter would admit the tenant-admin tier.

### What is on it

**Posture strip — the console's own connection, measured before every request**

Verified live from `GET /database/overview`:

| Fact | Value | Why it is here |
|---|---|---|
| Role | `aegis_readonly` — badged **read-only** | Re-verified over this very connection before every request, not trusted from config |
| Tables it can write | **none** | *Measured, not declared.* A non-empty list is a refusal and the console stops. |
| Statement timeout | `10s` | Cancelled by the database, not by the browser |
| Result ceiling | 1,000 rows · 5 MB | |

The role is `NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION
NOINHERIT`, holds `SELECT` and nothing else, and `users.password_hash` is
withheld by a **column-level** grant — the schema browser shows it as a withheld
column on `users`.

**Then:** an ER diagram, a schema browser of **41 relations** split into
tenant-scoped and platform tables with a row-estimate bar each, and a scope
selector reading *"Read as: Every tenant / <tenant>"*.

**Saved questions — 8 parameterised reads**, no typed SQL:
`spend_by_tenant` · `spend_by_model` · `recent_audit` · `audit_by_actor` ·
`failed_jobs` · `documents_by_status` · `pending_approvals` · `users_by_tenant`.

Each result prints **the statement the server built**, its duration and plan
summary, and a chip for anything it did not show and which bound decided that.

### What to say when demoing it

> "Bind a tenant here and run the same read twice. Same query, one clause
> different, and the clause is written by the server — the browser sends a tenant
> id, not a `WHERE`. It can narrow a read and it can never widen one; the server
> refuses any selection that exceeds my own authority."

On the missing SQL box:

> "There is no free-form SQL box, and that is a decision with a reason, not a
> backlog item. Metabase disables native SQL for any database with row or column
> security because it cannot parse SQL well enough to know which tables a query
> touches. We have `tenant_isolation` on nineteen relations. So every read is
> assembled by the server with the tenant filter welded into the `WHERE`."

On auditing:

> "Two audit rows per execution, not one. `db.query.execute` before the statement
> runs, carrying the query, the parameters, the resolved scope and me;
> `db.query.result` after, carrying the row count, the bytes, the duration and the
> verdict. Two rows because `audit_log` is append-only — and because a query that
> kills the process leaves the first row standing alone, which is itself the
> signal. A read whose audit row cannot be written does not run."

### What a jury might ask

**"Could a bug in the query builder leak another tenant's rows?"**
Two independent layers would have to fail. The tenant predicate is welded into
the statement, *and* the serving path runs under Postgres row-level security with
`FORCE` on a role that is `NOBYPASSRLS`. The console's own role additionally holds
no write privilege at all.

**"Why is there a rate limit only here?"**
Because read-only is not the same as harmless: a loop over this endpoint is a
self-inflicted outage on the cluster the product runs on. It is the only
rate-limited surface in the backend, and the module says so.

**"Can I type a table name that isn't in the catalog?"**
No. Every identifier — the table, the ordering column, the filter column — is
matched against the catalog *this connection can read*, never escaped. A column a
grant withholds is not in the catalog, so it cannot be named at all.

### Anything deliberately absent

- **The free-form SQL box.** Stated on the page as `freeFormReason`, verbatim,
  rather than left as a missing feature.
- **`users.password_hash`.** Not filtered in the query — revoked at the column
  level, so the connection cannot select it.
- **Nothing is truncated silently.** Every result states what it did not show and
  which bound (rows / bytes / time / plan cost) decided that.

---

## 11 · MCP

*Route: `/app/platform_admin/mcp` · Component: `mcp/McpConsoleView.tsx`*

### What this screen is for

Three questions on one page: which external Model Context Protocol servers our
agents may reach, what each of their tools may do and to whom, and what this
deployment offers *back* over the protocol.

### What is on it

| Panel | What it shows |
|---|---|
| **How MCP works** | The four steps a peer passes through to become one, each with its own live count and marked done only when this deployment has actually reached it. On an empty deployment it reads as a to-do list. |
| **Posture** | What each risk tier *does* here, in the present tense, derived from `agent.gate_min_risk` (currently `high`) — so the sentences stay true when a tenant tightens the floor |
| **Connections** | Declare a peer, prove it answers (Test), turn it off, forget it |
| **Aegis's own MCP server** | A real MCP client — `@modelcontextprotocol/sdk`'s `Client` over `StreamableHTTPClientTransport` — connecting to this deployment's own endpoint (`/mcp/mcp`) and listing the tools it offers *this caller* |
| **Tool governance** | The tier every tool is gated at, **per named tool**, with the decision trail underneath. Aegis's own tools appear read-only. |

Live on 2026-08-23: **0 external servers declared**, 4 Aegis tools
(`find_requests` low, `add_case_note` low, `assign_request` medium,
`update_request_status` high), gate floor `high`, and one prior decision in the
trail (an admin lowering `mcp__self-loop__assign_request` from high to medium).

### What to say when demoing it

> "An external tool is HIGH risk by default, which means it stops at the human
> gate until a platform admin lowers it **for a named tool**. There is
> deliberately no 'trust everything from this peer' control — a peer can add a
> tool tomorrow and it would inherit a decision nobody made about it. A tool that
> appears later starts at high like every other one."

Then the consequence:

> "Choose a tier below the gate floor and the sentence under the dropdown says, at
> the moment of the change, that this now runs without a human seeing it first.
> The button stops saying `Apply` and starts saying `Lower to LOW · runs
> unattended`."

And on the self-connection:

> "That panel is not a simulation of an MCP conversation, it is one — the official
> SDK, the same client Claude Desktop would use, carrying my bearer token. The
> tool list is a function of who is asking, so an admin and a tenant user
> connecting to the same URL see different lists."

### What a jury might ask

**"Can I call a tool from here?"**
No, and that is the point. Nothing on this page executes an external tool — that
path runs through the agent, behind the human gate. A button here would be
exactly the side door this screen exists to close. There is no "call it" control
on the self-connection panel either.

**"What happens to what a peer sends back?"**
Whatever a peer returns passes the `TOOL_RESULT` rail before it reaches a prompt.
You can see that rail firing in the Console's event log — *"Tool result guardrail
· pass"* on every tool call.

**"You have no servers declared."**
Correct, and the screen says so as a to-do list rather than pretending. Declare
Aegis's own server as a peer live if you want to show the loop closing — the
endpoint is on the page.

### Anything deliberately absent

- **A disabled server's tools leave the agent's payload entirely** — they are not
  merely hidden.
- **When the self-endpoint is not configured** the panel says so and offers
  nothing. A guessed URL would render as a live address for a server that is not
  there.

---

## 12 · Settings

*Route: `/app/platform_admin/settings` · Component: `app/[role]/[section]/SettingsView.tsx`*

### What this screen is for

The per-tenant control plane, and the one screen that says **who decided**. On
this portal you are writing the *platform floor* — the value every tenant inherits
and can only tighten.

### What is on it

1. **Text size** — the one control that changes nothing on the server and
   everything about whether the rest of the screen is readable.
2. **Settings catalogue** — a rail of key namespaces and a panel. Verified live:
   **25 controls** across 6 namespaces — `guardrails` (7), `agent` (6), `seat` (6),
   `jobs` (3), `memory` (2), `skills` (1). The rail says how many controls each
   namespace holds and how many are inert or read-only, so what is *not* on screen
   is still counted.
3. **Skills** — write a skill, switch it on, and see which layer decided it.
   Resolved `platform ∪ tenant ∪ user`.
4. **Tool roster** — "6 of 9", and why the other three. A read-only projection of
   `agent.gate_min_risk` from the catalogue above, re-read on every accepted write.

Every row carries: the description, the merge rule (`tighten_only` vs `override`),
a **provenance receipt naming the deciding scope**, and either a live control or
the reason it is not one.

**The scope selector.** A write targets a layer, and this portal reaches exactly
one of the three — the mirror image of the tenant admin's:

| Layer | Available to you? | If not, the reason on screen |
|---|---|---|
| **Just me** | ❌ | *"A preference is stored inside a tenant, and this sign-in is not bound to one."* |
| **Everyone in my tenant** | ❌ | *"This sign-in spans every tenant, so it has no single tenant to set a default for."* |
| **Every tenant** | ✅ | — |

> "The null tenant again. I set the floor for everybody and I have no personal
> preference here, because a preference is stored inside a tenant and I am not in
> one. A tenant admin sees the exact opposite: the first two, not the third."

### What to say when demoing it

> "There is no list of keys in this file. `GET /settings` returns every control
> this caller may read — the catalogue's own descriptor per key, already resolved
> — and the screen draws whatever arrives: the control type, the help text, the
> legal values. A key added to the spec next month appears here with nothing in
> the web app edited. That is the mechanism behind 'operating this platform never
> requires touching code' — the first bespoke settings form is the moment that
> claim stops being true."

Then open `agent.gate_min_risk`:

> "This one is `tighten_only`. A tenant may lower it — gating *more* — and can
> never raise it. It is the only gating signal there is, so the merge rule is the
> policy."

### What a jury might ask

**"What is that greyed-out one?"**
It is not greyed out — it is drawn differently on purpose. `agent.mode` reports
`effective: false`, and where a live row puts an input this row puts the
catalogue's own `inert_reason`, verbatim, in a bordered block. The reason
currently reads: nothing consumes this key yet; the run's width comes from
`QueryRequest.depth_mode` and the two vocabularies do not line up.

> "A control that binds to nothing is a real defect we hit once — an operator
> changed a value that reached no run. So a key that is not wired says so on its
> face rather than accepting a write into the void."

**"What happens if my write is folded to something else?"**
The row re-renders from the **PUT response**, not from what you typed, and a
sentence says so when the fold decided something other than what was submitted.

### Anything deliberately absent

- **A control that is not yours** shows the value and the sentence naming who may
  change it — not a greyed-out box that posts and 403s.
- **The platform safety skill** appears in every tenant's list, marked, with no
  control beside it. There is no value a tenant could send that would switch it
  off, and rendering a disabled toggle would imply there was.
- **The tool roster is read-only.** Pinning a subset for one run needs a per-run
  field the query request does not carry, and a pin control that changed nothing
  would be the exact defect this screen exists to remove.

---

## The three claims worth rehearsing

If you get five minutes, these three are the ones that hold up under a hostile
question.

### 1 · Tenant isolation is testable, not asserted

Do it live, in this order:

| Step | Screen | What it proves |
|---|---|---|
| Overview → *Daily spend by who it was billed to* | platform_admin | Northwind and Vertex are different bands, and they sum to the platform total |
| Approvals | platform_admin | The pending gate belongs to Vertex; the buttons are disabled with the server's reason |
| Sign in as `northwind.admin` → Approvals | tenant_admin | The gate is **not there** — not hidden, absent |
| Sign in as `vertex.admin` → Approvals | tenant_admin | The same gate, decidable |
| Database → *Read as: Northwind / Vertex* | platform_admin | Same query, one clause, different rows |

Underneath all of it: Postgres row-level security with `FORCE`, and a serving role
(`aegis_app`) that is `NOSUPERUSER NOBYPASSRLS`. `/readyz` reports it as a health
component: *"Serving as 'aegis_app': no SUPERUSER, no BYPASSRLS."*

Verified spend on 2026-08-23 (illustrative): Northwind $5.88, Vertex $0.39,
platform total $6.43 over 30 days.

### 2 · The audit trail is append-only by privilege

Not by convention, not by an ORM that avoids `DELETE`. `UPDATE` and `DELETE` are
revoked from the serving role on `audit_log`, `run_events` (and every month
partition) and `usage_ledger`. Postgres refuses. Demonstrable in psql in ten
seconds.

### 3 · The human gate survives a restart

Park a run at the gate, **restart the backend**, approve, and it resumes from the
checkpoint it paused on — with the Trace tab's checkpoint timeline showing the
continuation hanging off the interrupt tick. `AGENT_CHECKPOINTER=postgres`,
`langgraph.types.interrupt`, ~598 checkpoint rows persisted.

---

## Known rough edges — read before you demo

These are things a juror may notice. None is a lie; some are untidy.

1. **Test-residue tenants are visible.** The customer list on Overview and the
   Database scope selector show `AuditCo Ltd` and `ZZ Audit Probe 956665`
   alongside Northwind and Vertex. They are real rows created by an audit probe
   run, both with 0 calls. Either clean them up before the demo or have a sentence
   ready ("those are from an automated audit run — zero calls, and you can see
   that in the row").
2. **Forecast currently refuses.** 2 days of ledger history against a requirement
   of 71. Covered in §3 — demo it as honesty, or seed history first.
3. **Small-model share and cache hit rate both read 0%.** Both are true. Do not
   let a juror read them as broken instrumentation; say why (single-model routing;
   a freshly restarted process).
4. **Grammar nit.** The Overview latency receipt reads *"1 runs in the window"* —
   the count is not pluralised. Cosmetic; do not fix mid-demo.
5. **`web/src/components/layout/TrustBar.tsx` is not mounted anywhere.** It is a
   dead component. Nothing on screen is affected — just do not go looking for a
   trust bar that no longer renders.

---

*Companion guides: `persona-tenant-admin.md` (the customer's own administrator),
`persona-ai-team.md`, `persona-client.md`. Module-level reference for any subsystem
named above is in the 29 files listed in `docs/teaching/README.md`.*
