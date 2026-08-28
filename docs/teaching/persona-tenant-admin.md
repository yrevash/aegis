# Demo walkthrough — the Tenant admin portal

> **Who this is for.** You, standing in front of the jury with `northwind.admin`
> (or `vertex.admin`) signed in. Every screen in this portal, what is on it, where
> each number comes from, the sentence that lands the point, and the honest answer
> to the question you will get back.
>
> **Verified against the running system on 2026-08-23.** Figures quoted below are
> from that moment and are **illustrative** — they will differ on the day. The
> *sources* (endpoint, table, role) will not.
>
> **Refreshed against the code on 2026-08-28** for the changes that landed since:
> the audit trail's hash chain and its verify button, the per-tenant record store,
> and the compliance totals. Sections marked *"read from the code, not re-walked"*
> were checked against the component and the endpoint rather than by looking at a
> running box.

---

## Before you start

| | |
|---|---|
| Sign in | `northwind.admin` / `demo` (tenant 1) or `vertex.admin` / `demo` (tenant 2) |
| Lands on | `/app/tenant_admin/dashboard` |
| Frontend | http://localhost:3001 |
| Backend | http://127.0.0.1:8110 |

**The one fact that shapes this whole portal: a tenant admin is *pinned* to
exactly one tenant.** `northwind.admin` logs in with `tenant_id: 1`,
`fine_role: tenant_admin`. That pin is inside the JWT, and every backend read
re-resolves the scope from it (`_scope_tenant`) — the browser cannot widen it by
sending a different tenant id.

**Why this portal exists at all.** Aegis used to have one coarse `admin` role that
collapsed the person who operates *Aegis* and the person who administers *one
customer* into a single value. They landed on the same URL, the same nav, the same
screens. That is the defect this portal fixes: a tenant admin had no portal, it was
borrowing the platform's. Worth saying out loud — it is a governance story, not a
routing story.

### The nav, in order

The rail groups the thirteen sections under two headings.

**Workspace** — Overview · Analytics · Documents · Forecast · Console · Memory
**Governance** — Approvals · Governance · Roles & Access · Jobs · Audit · LLMOps · Settings

*(Source: `web/src/lib/portal.ts` → `ROLE_SECTIONS.tenant_admin`, grouped by
`web/src/components/layout/navGroups.ts`.)*

### What is deliberately **not** here

| Missing | Why |
|---|---|
| **Database** | `require_db_console` is `require_platform_admin`, never `require_admin`. Any database browse is platform-tier. |
| **MCP** | Declaring an external tool peer and lowering its risk tier is a platform decision. |
| **Compliance** | It lives on the DevOps portal. |
| **Cache** | Cache hit rate is one figure across every tenant that shared the worker. `require_infra_reader` refuses a tenant-pinned principal outright — there is no filter that would make the figure safe. |

The rule behind all four is written down in `portal.ts`: *a section belongs on a
portal only if that role can act on it. A read-only copy of someone else's screen
is a gap wearing a menu entry.*

### Listings open closed — know this before you click

*(DESIGN.md §4; read from the code, not re-walked.)* Every panel whose body is a
list of rows — documents, approvals, audit entries, jobs, prompts, seats — opens as
**one bar** carrying its title, its row count and one key figure:

    Documents                                    10 rows · 7 ingested

**Hover expands it; click pins it open**, and the pin survives the pointer leaving.
One surface per page stays open — the thing the page is named after. The forcing
function is the demo itself: a reviewer gets a few seconds to decide what a screen
is, and spending them scrolling past forty rows is a failed screen however correct
the table was.

If a juror asks: the rows are collapsed, **never `display:none`** — they stay in
the accessibility tree and reachable by keyboard, and each trigger is a real
`<button>` carrying `aria-expanded`.

---

## 1 · Overview

*Route: `/app/tenant_admin/dashboard` · Component: `dashboard/AdminCommandCenter.tsx`*

### What this screen is for

Your tenant at a glance — what it cost, what it saved, what needs attention. It is
the **same component** the platform operator's overview uses, and every figure on
it is narrowed to your tenant by the backend rather than by the browser.

### What is on it

| Panel | What it shows | Where the number comes from |
|---|---|---|
| **Total spend, 30 days** | Your tenant's metered spend | `GET /admin/usage?window=month` — `usage_ledger`, scoped server-side |
| **Cost saved vs frontier** | What this workload would have cost on the frontier model, minus what it did | `GET /gateway/optimization` → `summary` |
| **Queries served** | Metered model calls | same |
| **Small-model share** · **Cache hit rate** · **Quality score** | Routing and quality figures | `GET /metrics` |
| **p95 latency** | 95th-percentile whole-run duration | `GET /latency` — an in-process rolling window that resets on restart |
| **Alerts** | Derived: your tenant at ≥80% / ≥100% of cap | `GET /governance/dashboard` budgets |
| **Customers & budgets** | Here it is **your tenant only**, with calls, spend and a meter against your cap | `GET /governance/dashboard` |
| **Daily spend, by who it was billed to** | The same stacked area — with one band, yours | `GET /admin/usage` |
| **Model mix** | Donut of spend per deployment | `by_model` |
| **Model routing** | Which deployment each role lands on | `GET /metrics.routing` |
| **Latency** | p50 / p95 / max and the run count | `GET /latency` |

### What to say when demoing it

> "This is the same code the platform operator sees. Nothing about the *component*
> knows I am a tenant admin — the backend narrows every row from the tenant pin in
> my token. That is the design: one screen, one scoping rule, enforced in one
> place."

The strongest move here is a **side-by-side**. Open Northwind in one window and
Vertex in another:

> "Same screen, same code. $5.88 against $0.39, three thousand calls against four
> hundred and sixty-five, and neither one can see the other's number. Ask Vertex a
> question and Northwind's figure does not move."

*(Illustrative, verified 2026-08-23: Northwind $5.88 / Vertex $0.39 over 30 days.
The platform total, $6.43, is the sum plus untenanted platform work.)*

### What a jury might ask

**"Is p95 latency yours or everyone's?"**
Everyone's, and this is worth being straight about. `/latency` measures the
*process*, not the tenant. Per-tenant latency does not exist at all, and the
Forecast screen states why as one of five recorded absences: a ledger row records
tokens, units and cost, never duration.

**"Why does the security posture card say 'Posture unavailable'?"**
Because it is not yours to read. Security posture describes the **deployment** —
one reading across every tenant that shared the worker — so `require_infra_reader`
refuses a tenant-pinned principal. The dashboard does not even send the request
when a tenant is pinned. See *Known rough edges*: the panel's wording is currently
weaker than the reason, and you should say the reason out loud.

### Anything deliberately absent

- **Sparklines appear only where a real series exists.** A tile with one sample
  gets none — a flat rule under a number reads as a chart that failed to load.
- **The stacked chart refuses to draw with one bucket** and states what is
  missing instead.

---

## 2 · Analytics

*Route: `/app/tenant_admin/analytics` · Component: `analytics/AnalyticsView.tsx`*

### What this screen is for

Two halves. The top half is Aegis drawing your metered usage ledger. The bottom
half is **Apache Superset** rendering your boards inside this page.

### What is on it

**Top half — from `GET /admin/usage`** (a tenant-administrator reading; it is
refused to a plain client, who reads their own costs on *Savings* instead)

Window switch (30 days / 24 hours) · Metered spend · Tokens · Models in play ·
Days with traffic · Spend per day (or per hour) · Spend by model · **Cost per 1k
tokens** (the rate actually paid, not list price) · Weekday rhythm · Hour of day.

**Bottom half — `Insight boards`**

Verified live: Superset answering, and this role is offered **19 boards** —
spend / runs / human gates / background jobs / governed actions / token volume /
run latency over time; spend by model; token volume by model; runs by outcome;
run latency by outcome; human gates by risk; gate decisions; job throughput; job
outcomes; governance trail; red-team defence; red-team block rate; and the
**Tenant insights** dashboard, embedded.

### What to say when demoing it

> "Nineteen boards for me. The platform operator gets twenty — the platform-wide
> Operations dashboard is not mine, and I cannot enumerate it either: a board I am
> not an audience for returns the same 404 as a board that does not exist."

Then the mechanism:

> "The backend builds the query, mints a short-lived guest token, and the tenant
> filter is a `WHERE` clause signed into that token. It is not a browser-side
> filter and there is nothing in this page I could edit to widen it."

### What a jury might ask

**"What if Superset is off?"**
The top half is unaffected — it reads the ledger directly. The Superset section
then states the backend's own sentence, the command that fixes it, and the three
things the add-on brings that the ledger charts genuinely cannot (governed
dashboards, datasets beyond the ledger, ad-hoc SQL). **No greyed-out chart
skeleton** — a placeholder chart on an analytics page is indistinguishable from a
real one at a glance.

### Anything deliberately absent

- **Cost per 1k tokens excludes audio and image deployments** — they bill by
  second and by frame.
- **An hour with no ledger row is absent, not zero.** Buckets are summed, never
  averaged.

---

## 3 · Documents

*Route: `/app/tenant_admin/documents` · Component: `documents/DocumentsView.tsx`*

### What this screen is for

Everything the agent can ground an answer in for *your* tenant, and the one place
to add more. This is the act a tenant performs more often than any other.

### What is on it

| Panel | What it shows | Source |
|---|---|---|
| **Documents** | Row count | `GET /documents` — your tenant's own rows |
| **Ingested and searchable** | `N of M` | `documents.status = succeeded` |
| **Waiting to be ingested** | Count | `documents.status = pending or running` |
| **Searchable passages** | Sum of chunk counts | `sum(documents.chunk_count)` |
| **Upload panel** | The front door: file, plus **doc type** and **date** | `POST /documents` |
| **What the corpus is made of** | Status composition and the `doc_type` set | `documents.status` / `documents.doc_type` |
| **Searchable passages per document** | Distribution | `documents.chunk_count` |
| **Parse confidence** | Five fixed bands | `documents.parse_confidence` |
| **When they arrived** | Arrivals per day | `documents.created_at` |
| **Corpus shelf** | Every document, newest first, click to open its log | `GET /documents` |
| **Ingest log** | The six stages, live | `documents.completed_stage` + the `run_events` row each stage wrote inside its own transaction |

### What to say when demoing it

Upload a PDF live and open the log:

> "Six stages — parse, chunk, enrich, embed, index, graph — and each one commits
> inside its own transaction. Nothing on this screen is accumulated in the
> browser. That is why a refresh mid-ingest resumes the view instead of losing it,
> and why a worker killed halfway and restarted cannot make this screen claim a
> stage that never committed."

On the two fields you have to fill in:

> "Type and date are yours to give, and left blank they are recorded as
> **unknown** rather than guessed. Nothing in a PDF's bytes states either, and they
> are two of the four fields every chunk's retrieval prefix carries. And the date
> is the date the document is *from*, never the date you uploaded it."

### What a jury might ask

**"What if I upload the same file twice?"**
Nothing starts, and the panel says so — because "nothing happened" and "it worked"
look identical otherwise.

**"What if my tenant is over budget?"**
Admission control refuses the ingest with its reason on screen — the tenant cannot
afford it, or has no free slot. It is not swallowed into "upload failed", because
that would recreate in the browser exactly the invisible backpressure the gate
exists to remove.

**"Can another tenant see these?"**
No. `chunks.tenant_id` is NOT NULL and row-level security is on the table. That is
also why a platform admin — who has no owning tenant — **cannot upload at all**.

### Anything deliberately absent

- **Six of the seeded rows are 0-chunk placeholders sitting at `pending`.** They
  are counted as pending and never as knowledge.
- **"Searchable passages" is stated as an absence, not a zero,** when nothing has
  parsed: `chunk_count` is `null` before a parse runs, and a null is not a zero.
- **None of the four charts is a metric trend.** `GET /documents` is a snapshot of
  the table with no history in it. "When they arrived" is a *volume* — it says how
  many documents landed, never whether ingest got better — and the screen says so
  in the receipt under the chart.
- **Fewer than three days of arrivals prints the counts as a sentence**, not a
  two-bar histogram. *"A third day makes this a histogram."*

---

## 4 · Forecast

*Route: `/app/tenant_admin/forecast` · Component: `forecast/ForecastView.tsx`*

### What this screen is for

Your tenant's spend projected forward, with the interval coverage that was
actually *measured*.

### What is on it

- **Controls**: measure (spend / calls), horizon, refresh. **There is no tenant
  selector** — that renders only for a platform admin, because the server would
  refuse a cross-tenant request anyway.
- **Four figures**: next step, projected total, held-out error (sMAPE), coverage
  achieved vs coverage requested.
- **Card 1 — the band.** Every projected point is a **band**, never a line.
- **Card 2 — burn-down against your cap**, headlined by *the date it runs out*.
- **Card 3 — how the model was chosen** (rolling-origin held-out backtest).
- **Explainability** — SHAP over the supervised spine, which is a *different*
  model, with a sentence saying so.
- **Exports** — four server-streamed, audited CSVs.
- **"What this page cannot tell you"** — 5 stated absences.

### ⚠ On this deployment, right now, the forecast refuses

Verified live for `northwind.admin`:

```
{"available": false, "forecast": null, "burndown": null,
 "refusal": {"code": "insufficient_history",
   "reason": "Forecasting 14 step(s) of 'D' data needs 3 held-out backtest window(s)
     plus enough history before the earliest cutoff to fit two seasonal cycles
     (season=7) and calibrate 3 conformal window(s)",
   "have": 2, "need": 71}}
```

The demo data was seeded 2026-08-22, so the ledger holds **2 days** where the fit
needs **71**. The screen renders that refusal verbatim.

**Demo it as the point, not as a gap:**

> "Two days of history against a requirement of seventy-one. So it refuses, and it
> tells you exactly what it would need and why — two seasonal cycles to fit, three
> conformal windows to calibrate. Most dashboards would draw you a line through two
> points and put a confidence interval on it."

If you want a live forecast on the day you need roughly ten weeks of daily ledger
buckets. That is a seeding job — decide before the demo.

### What a jury might ask

**"How accurate has this forecast been?"**
Unknown, and it is one of the five stated absences: nothing stores a forecast at
the moment it is made, so none has ever been scored against the days that
followed. The backtest is evidence about the *method*, on windows held out from
the fit.

**"Why is the SHAP panel here if it does not explain the forecast?"**
Because "how does the forecast look without feature X" is a real question with a
real answer that belongs to a different model. The spend forecast is univariate —
its only input is its own history, so there is nothing to attribute. Drawing both
on one visual would make the product dishonest, so they are two panels with two
sources and a sentence between them.

### Anything deliberately absent

The five, verbatim: error rate of model calls (no outcome column on
`usage_ledger`) · per-tenant latency at any percentile (no duration column) · how
accurate this forecast turned out (forecasts are not persisted) · which features
drive spend (univariate) · what the spine would predict without a feature
(`POST /ml/experiment` is **not built**).

---

## 5 · Console

*Route: `/app/tenant_admin/console` · Component: `console/ChatConsole.tsx`*

### What this screen is for

Where you actually ask the agent something, and watch every step it takes get
named, sourced and priced as it happens.

### What is on it

| Element | What it shows |
|---|---|
| Chats control | Your own `GET /sessions` list — real `chat_sessions` rows under your tenant's RLS policy |
| Composer | The question box, plus Mode, Model and Image pickers |
| **Budget line** | `$x of $y` — reads `GET /me/budget`, the same `BudgetStatusRow` set the gateway compares every call against. If no cap governs you it says so rather than printing `$0.00 of $50`. |
| Seed questions | The adapter's own configured questions, not invented examples |
| "What happens to a question" | Input rail (6) → route → retrieve & answer → output rail (6) |
| **Run panel** (live) | The lane board (the fan-out), a live feed, run figures, four trust checks |
| Approval spotlight | When the run hits the gate the screen scrims and blurs behind the decision |
| **Answer** | Streamed, badged `output checked`, with the sources it *stands on* |
| **Sources tab** | The passages behind the answer |
| **Trace tab** | Routing and self-repair decisions, guardrail glass box, per-node timing and cost, the raw event log, graph traversal, the trace id, and the **checkpoint timeline** |

### The demo that wins this section — the durable interrupt

The human gate is a real `langgraph.types.interrupt`. With
`AGENT_CHECKPOINTER=postgres` the pause is written to Postgres.

1. Ask for something consequential — `update_request_status` is HIGH risk, and the
   gate floor is `high`, so it parks.
2. **Restart the backend.**
3. Approve it from the Approvals screen.
4. The run resumes, and the Trace tab's **checkpoint timeline** shows one tick per
   persisted checkpoint, the gate marked where it parked, and the continuation
   hanging off that same tick.

> "That is the question a reviewer actually asks — did the resume continue from the
> gate, or quietly re-run the graph from the top? This is the answer, drawn from
> the checkpoint rows themselves rather than asserted."

### What a jury might ask

**"Are the per-node costs real?"**
Yes — each node emits a `node_finished` event with its model, tokens, duration and
USD. A local node (guardrails, routing, gate, verify, reflect) shows `—` rather
than `$0.00`, because it never called a model.

**"Show me one that went wrong."**
Better material than a clean run. The event log shows tool errors verbatim, the
verify node's verdict on the round, the reflect node re-planning, and the iteration
budget being exhausted with *"finalising with the best available result"*. Nothing
is smoothed over.

**"What is the `verify` node?"**
The node that decides whether the round worked, sitting between `act` and
`reflect`. It judges against something outside the model — the rows already in
hand, or a read-only read-back proving the write landed — rather than trusting a
tool's own report that it succeeded. *(Read from `aegis/agent/graph.py`, not
re-walked.)*

### Anything deliberately absent

- **A turn read back from stored history has no trace and no agent cards**, and
  says so — `run_events` is not persisted for replay, and an empty trace would be
  worse than a sentence.

---

## 6 · Memory

*Route: `/app/tenant_admin/memory` · Component: `memoryctl/MemoryControlView.tsx`*

### What this screen is for

See what the platform holds about someone, correct it, delete it, and stop keeping
it. **A memory screen you cannot correct is a report** — this one has the three
missing verbs attached.

### What is on it

| Panel | What it shows | Source |
|---|---|---|
| Facts held · Sessions · Last active · **Subjects in reach** | The chosen subject's record size | `GET /memory/subjects` |
| **Whose memory** | The subject picker — a list the *server* built | `GET /memory/subjects` |
| **Scope ladder** | Who can reach this record, as three rungs | same response |
| **Fact manager** | Write · Correct · Delete | `POST /memory/facts` and friends |
| **Retention** | The horizon, what is already past it, and the two ways to remove it — with a `(value, source)` badge on the horizon | `memory.retention_days` from the settings catalogue |
| **Chat threads** | Your own threads and their transcripts | `GET /sessions` |
| **The full record** | Facts with belief history, structured profile, sessions, write log, recall trace | the four `/memory/*` reads |

Verified live for `northwind.admin`: 3 subjects — `northwind.admin` (7 facts, 27
sessions), `northwind.analyst` (5 facts, 56), `northwind.client` (0 facts, 2) —
with `may_manage_others: true` and `self_subject: user:6`.

### What to say when demoing it

Open the **scope ladder** first:

> "Three rungs, and it is deliberately not a pyramid of shared data. A memory is
> private to one person by construction — `recall()` filters `subject_id` **and**
> `tenant_id` on every arm. There is no tenant-wide memory bucket. What widens with
> rank is the **reach over those records**: a person manages one, I manage every
> subject inside my tenant, and nobody reaches across a tenant at all."

Then the picker:

> "This used to be a free-text box, and the subject key is an arbitrary string. So
> an admin could not discover whose records existed and a client had to know the
> internal `user:<id>` shape to look at their own. `GET /memory/subjects` is the
> missing route and it is also the isolation boundary — every row was derived
> server-side from my sealed tenant scope. The browser only ever echoes back a
> subject it was handed, so there is no string anyone could type here that widens
> it."

Then write a fact and watch the rail:

> "A write goes through the **full input rail before anything is stored**. That is
> not a formality: a stored fact is replayed into every future prompt for this
> subject as trusted context, so an unscreened write is a prompt injection with a
> delay fuse. It costs the attacker nothing today and arrives inside the model's
> context tomorrow."

### What a jury might ask

**"If I correct a fact, is the old one gone?"**
No — **correct supersedes rather than overwrites**. The replaced row stays in the
belief timeline in the record below, so *"what did it think last week, and who
changed it"* keeps an answer. An edit box that quietly rewrote history would make
the audit trail decorative.

**"Can I actually delete? GDPR / DPDP."**
Yes. `POST /v1/memory/forget` deletes, and that route is the reason
`memory_write_log` is deliberately *excluded* from the append-only revoke list —
erasure has to be able to delete from it, and it runs in a request handler.

**"Can I edit a chat transcript?"**
No, by design. `POST /query` is the only writer of `chat_messages` — a client that
could post its own turns could post turns that never happened. You may rename a
thread and delete one, nothing else.

### Anything deliberately absent

- **The horizon carries its provenance.** A value with no source is a value nobody
  can reason about — and that rule applies to a number that is merely displayed
  exactly as much as to one that is edited.
- **The forgetting sweep archives rather than deletes**; erasure is the separate,
  explicit route.

---

## 7 · Approvals

*Route: `/app/tenant_admin/approvals` · Component: `approval/ApprovalInbox.tsx`*

### What this screen is for

Every action the agent proposed for **your tenant** and did not take. Nothing
high-risk executes without this decision.

### What is on it

**The board** — queue switch (Waiting / Decided / Everything), lookback (24h / 7d
/ since the beginning), the waiting figure as the screen's one display numeral, the
**urgency ladder** (five bands, worst first, icon + word + count), and the decided
split (approved / rejected / expired).

> **Note:** there is **no "Whose gate" selector** on this portal. It renders only
> for a platform admin. Every row here is yours already.

**Each waiting gate** carries: what runs if approved (every call with its args),
why a person is required (the `rationale`), the SLA rail showing how much of the
deadline is spent, a gate receipt, the run id, and a two-step Approve / Reject.

### The isolation demo — and this portal is where it pays off

There is one pending gate in the system: `seed-gate-vertex`, `cancel_shipment`,
HIGH risk, belonging to **tenant 2 (Vertex Logistics)**. Verified live:

| Signed in as | Sees it? | `decidable` |
|---|---|---|
| `vertex.admin` | Yes | **`true`** — Approve / Reject are live |
| `northwind.admin` | **No — the row does not exist for them** | — |
| `admin` (platform) | Yes | `false`, with the server's reason on the card |

### What to say when demoing it

Sign in as `northwind.admin` first:

> "Empty. Not filtered, not hidden — the query returned no rows, because that gate
> is not mine."

Then `vertex.admin`:

> "Same screen, same URL. There it is, and the buttons are live. And if you look at
> the platform operator's copy of this screen, the buttons are *drawn and disabled*
> with the server's own sentence underneath: 'This gate belongs to a tenant. A
> tenant's own admin decides it — the platform operator sees it, and does not vote
> on it.' Three different answers to one row, decided in one place."

Then the rationale on the card:

> "Cancelling a shipment already in transit is not reversible from the agent's
> side. That sentence is on the gate, not in a policy document."

### What a jury might ask

**"What if nobody decides?"**
Not deciding is itself a decision, and the screen says so. A sweeper marks a
past-deadline gate `expired` and **auto-rejects a HIGH-risk one**. That is why the
waiting list is ordered by how little SLA is left rather than by arrival.

**"Can I approve by accident?"**
No. Both decisions are two-step: a sentence naming the specific calls, a coloured
commit, and "Keep waiting". Approving executes a real tool action; rejecting ends a
parked run. Neither is reversible from this screen.

**"Who decides what counts as high risk?"**
`agent.gate_min_risk` on the Settings screen. It is `tighten_only` — you may lower
it, which gates *more*, and you can never raise it above the platform floor.

### Anything deliberately absent

- **The half of the board this query did not load prints "Not counted in this
  queue"**, not `0`. A query that asked only for decided gates has not been told
  nothing is waiting — it has been told nothing at all.

---

## 8 · Governance

*Route: `/app/tenant_admin/governance` · Component: `governance/GovernanceView.tsx`*

### What this screen is for

Are you about to run out of what you are allowed to spend? Everything else on the
screen is the ledger underneath that answer.

### What is on it

| Panel | What it shows | Source |
|---|---|---|
| **Scope badge** | *tenant scope · tenant #1 only* | Derived from `fine_role`, not from `role` |
| **Spend against cap** | One gauge — yours. The ring shows whichever of the USD cap and the token cap you are **closer to**; bars beneath show both. | `GET /governance/dashboard` |
| Calls · Tokens · Cost | The window's usage over one shared hairline, with the cost shape beneath | `usage` |
| Tenants & budgets | One row: yours | same |
| Cost by model | The 8 costliest models | `usage.by_model` |
| **Users & roles** | Your tenant's people, their role, their personal cap | `users` |
| Recent audit tail | Actor · action · time | `recent_audit` |

Verified live for `northwind.admin`: one tenant (Northwind Trading), four budgets
(the tenant cap $200/day plus three user caps), three users
(`northwind.admin` admin, `northwind.analyst` ai_team, `northwind.client` client),
3,248 calls / $4.01 on the day window, and 50 audit rows.

### What to say when demoing it

> "One call — `GET /governance/dashboard` — builds this whole screen, and the
> backend pins it to my tenant. If I ask for a *different* tenant it is forbidden,
> not filtered. The badge in the header says which scope this is, because captioning
> both admin tiers identically would present one tenant's figures as the platform's."

On the gauge:

> "Deliberately not an average. A tenant with 4% of its dollars spent and 97% of its
> token allowance burned is not comfortable, and a screen that averaged the two would
> say it was. The ring shows whichever cap is closer — the tighter one is what will
> actually stop work."

### What a jury might ask

**"Can I raise my own cap here?"**
No, and the Roles & Access screen says so in the refusal where the control would
be: *"Aegis sets your tenant's own cap — raising it is not yours to do. You set the
caps on your users."*

**"Where is the cap actually enforced?"**
In the gateway, against the same `usage_ledger` these figures are summed from —
which is why the Console's budget pill reads `GET /me/budget`, the same
`BudgetStatusRow` set, rather than a second figure that could eventually disagree
with a refusal.

### Anything deliberately absent

- **A portal role is not painted like a status.** It is told apart by its word,
  because a colour that means nothing sitting next to reserved status hues is a
  design failure with a governance cost.
- **The cost-shape band refuses to curve through one point:** *"the ledger returned
  one bucket for this window, so there is no shape to draw — a curve through one
  point would be invented."*

---

## 9 · Roles & Access

*Route: `/app/tenant_admin/roles` · Components: `admin/RolesAccess.tsx`, `admin/AdminControls.tsx`, `admin/SeatsPanel.tsx`*

### What this screen is for

Who in your tenant may do what, and what it may cost them. It is where you
provision a seat and cap it.

### What is on it

1. **Delegation map** — each portal role with its head count and the `sees` line
   stating what that grant hands over.
2. **Counting strip** — **two** tiles here, not three: *Users in scope* and *Caps
   in force*. The **Tenants** tile is absent, because `GET /admin/tenants` is
   `require_platform_admin` and you are never sent the request.
3. **Who has access** — the roster: user, scope, holds, and an **Assign portal**
   dropdown per row, with a self-lockout guard.
4. **Named seats** — five revoke-only toggles per user plus a `seat.label`, each
   carrying a `source` badge: `platform` (untouched), `tenant` (off for
   everybody), `user` (set for this person specifically).
5. **Budgets** table (no Tenants table).
6. **Manage access** drawer — three tabs, two of which you may use.

### The three refusals, and why each is shown rather than hidden

| Tab | What you see |
|---|---|
| **New tenant** | *"Aegis onboards tenants. Your admin rights end at your own tenant's users and their caps."* — badged **platform only** |
| **New user** | The tenant picker is replaced by: *"Pinned to tenant #1. Aegis fills this in from your sign-in — a user created here can only be yours."* |
| **Set a cap** | Scope "A tenant" is refused: *"Aegis sets your tenant's own cap — raising it is not yours to do. You set the caps on your users."* Scope "A user" works. |

### What to say when demoing it

> "Every one of those refusals is rendered where the control would have been. We
> could hide the tab — and hide the rule. The browser is mirroring the server's
> guard so the rule is *visible*; the server is still the authority and refuses it
> whoever asks."

On seats:

> "Every toggle here can only take capability **away**, and the screen says so
> rather than pretending otherwise. The server folds a write against the enclosing
> scopes and the strictest value wins — so switching one back on restores what the
> tenant already permits and can never exceed it."

On the roster:

> "And I cannot lock myself out. The option is disabled with the reason on it, and
> the guard mirrors the backend rule rather than reimplementing it."

### What a jury might ask

**"Could I promote one of my users to platform admin?"**
The dropdown offers the four portal roles, and the server is the authority on what
it will accept. Do not promise the outcome from the stage — offer to try it live;
a refusal renders the server's own sentence, which is a better answer than a claim.

**"Does creating a user actually work, or just show a toast?"**
It works, and the screen is built so you can see it: every successful write reloads
all three readings, so a created user appears in the roster below without a
refresh. A console that will not show you what you just did is the defect this
layout exists to remove.

### Anything deliberately absent

- **The Tenants table and the Tenants tile.** Not greyed out — not fetched, because
  a card whose data was fetched only to be refused would render an error that means
  nothing to the person reading it.

---

## 10 · Jobs

*Route: `/app/tenant_admin/jobs` · Component: `jobs/JobsView.tsx`*

### What this screen is for

Durable background work for your tenant, its admission caps, and the controls that
act on it. **Unlike the platform operator's copy, this one has every write.**

### What is on it

| Panel | What it shows | Source |
|---|---|---|
| **Pipeline funnel** | The six ingest stages as isometric solids, height by runs that committed each. Each stage carries a tip naming its queue, timeout and attempt budget. | folded from the `GET /jobs` rows |
| Queue table | Job + type, status, last committed stage, cost, created, detail, log, **Action** | `GET /jobs` |
| Filters | All / In flight / Failed / Succeeded | over the same rows |
| **Action column** | **Cancel** while in flight, **Re-queue** once terminal | `POST /jobs/{id}/cancel` and `/requeue` |
| **Upload panel** | The front door into this queue | `POST /documents` |
| Corpus panel | What came out | `GET /documents` |
| Ingest log | Per document, stage by stage | `documents.completed_stage` + `run_events` |
| Pipeline health | Nine panels behind a disclosure | `GET /health` |

The six stages, with the reason each is pinned where it is:

| Stage | Queue | Why |
|---|---|---|
| parse | cpu | Docling reads the PDF and scores its own reading order — CPU- and RAM-bound, so it serialises. 30 min, 2 attempts: a parse that fails twice is a document, not a flake. |
| chunk | default | Splits on structure, lifting tables out as their own chunks. 5 min, 3 attempts. |
| enrich | default | Attaches the metadata a retrieval filter needs. 5 min, 3 attempts. |
| embed | io | The one billed network stage, so it runs wide. 15 min, **5 attempts** — a provider 429 is expected, not exceptional. |
| index | default | Writes vectors into this tenant's own collection. 10 min, 3 attempts. |
| graph | cpu | Extracts entities and relations onto `chunks.meta`. 30 min, 2 attempts. |

### What to say when demoing it

> "This is the same screen the platform operator sees, and there it is read-only
> with a lock chip reading *'Read-only: no owning tenant'*. Re-queue and cancel load
> the row as `WHERE id = :id AND tenant_id = :caller_tenant`, and their tenant is
> null — so every button would 403. I have a tenant, so I have the buttons. The
> predicate is the tenant pin, never the role name."

Then re-queue a failed job:

> "That passes admission control on the way back in — the in-flight cap and the
> budget pre-authorisation — and a refusal shows the gate's own reason rather than
> being queued out of sight."

### What a jury might ask

**"Can I see another tenant's jobs?"**
No. `GET /jobs` is tenant-scoped for a pinned caller. The platform operator sees
every tenant's rows — that is the one asymmetry, and it is read-only.

**"What does a blank Stage cell mean?"**
It does not show a blank. It shows **"none committed"**, because a blank cell means
the run never wrote a stage — which is a different fact from starting at the
beginning, and an em dash cannot be told apart from a zero.

### Anything deliberately absent

- **A deep link whose row the filter hides widens the filter and says so** rather
  than silently changing your controls.
- **A background poll that fails leaves the last good rows standing.** The
  "updated at" stamp stops advancing, and that is itself the signal.

---

## 11 · Audit

*Route: `/app/tenant_admin/audit` · Components: `admin/AuditLog.tsx`, `audit/AuditInsights.tsx`*

### What this screen is for

Your tenant's append-only record: every action, its actor, its model, its trace id
and who approved it. It is on this portal under the *record exception* — a tenant's
own audit trail is theirs to read even where a write belongs elsewhere.

### What is on it

**The chain strip** *(read from `admin/AuditLog.tsx`, not re-walked — it was added
after the 2026-08-23 walk)*. One card above the insights with a **Verify the chain**
button, and, before you press it, one line explaining itself: *"Every row is hashed
with its predecessor's hash mixed in, so an edited row breaks itself and a removed
one breaks everything after it."* Pressing it walks `GET /v1/audit/verify` and
returns a badge — **`chain intact`** or **`broken at #<id>`** — the count of rows
verified, and, separately, how many rows predate the chain and are therefore not
covered by it. The second figure is never folded into the verdict.

**The insight layer** — charts lead, the trail sits beneath as the thing they are
derived from.

- **Lens chips**, each a *server* predicate: **Refused** · **Guardrail** ·
  **Queries** · **Approvals** · **Uploads** · **Console reads** · **Last 24h**.
- A completed-vs-refused trend on a window taken from the data, not the wall clock.
- Ranked distributions: **Actions**, **Actors**, **Refusals**.

**Filter bar** — free text, time range, outcome, row limit, actor, action prefix,
model, from/to. *(No tenant selector — that is platform-admin only, and it is a
convenience rather than the control: `_scope_tenant` refuses a cross-tenant request
server-side whoever asks.)*

**The trail** — Time · Action · Actor · Model · Trace · Approved by · Result. Trace
ids are click-to-copy. The result is a dot **paired with its word**, never colour
alone.

**CSV export** — `GET /reports/audit.csv`: streamed with no row limit, scoped
through the sealed `TenantScope`, audited as `report.export` **before the first
byte**, and opening with a preamble naming the scope, window, source and filters.

### What to say when demoing it

> "Append-only here is not a promise in a document — it is a Postgres privilege.
> The serving role, `aegis_app`, has `SELECT` and `INSERT` on `audit_log`. `UPDATE`
> and `DELETE` are revoked; `TRUNCATE` is owner-only and it does not own the table.
> The database refuses to alter this trail, not the application."

From `scripts/sql/aegis-app-role.sql`:
```sql
REVOKE UPDATE, DELETE ON public.audit_log FROM aegis_app;
-- same for run_events (and every month partition) and usage_ledger
```

Then press **Verify the chain**, because the grant is only half the argument:

> "That stops the *application* rewriting this. It does not stop whoever holds the
> owner connection. So every row also carries its predecessor's hash mixed into its
> own, and this button asks the server to re-derive all of them. A per-row hash
> would only prove no row was edited; the chain is what catches a row **removed**,
> which is the quieter attack. And it reports honestly how many rows predate the
> chain and are not covered by it at all."

Then click the **Refused** lens:

> "Every filter runs on the server. Changing one re-runs the query rather than
> hiding rows already on screen — so the figures above always describe exactly the
> set the table shows, and a search reaches the whole trail rather than the page in
> view."

### What a jury might ask

**"Where does the blocked/completed verdict come from?"**
There is **no verdict column on the trail**. It is classified server-side by
`aegis.governance.audit.classify_outcome`, and the lens tip says so.

**"Could someone re-derive the chain after editing a row?"**
With the owner connection, yes. This is tamper *evidence*, not tamper prevention,
and saying so is the honest answer. What it costs an attacker is that one quiet
`UPDATE` or `DELETE` no longer suffices — they must rewrite every row after it, and
a unique index on `(tenant_id, prev_hash)` means a spliced fork fails at insert
time rather than being found months later.

**"Will the CSV match what I see?"**
Not always, and the screen tells you when it will not: the export takes the actor,
the action prefix and the time range only. Set a model or outcome filter and a bar
appears saying the CSV *"cannot narrow by <those>, so it will hold more rows than
the table below."* A file that quietly holds more than the table it came from is
evidence of the wrong thing.

### Anything deliberately absent

- **The insight figures count only the rows the server returned** — the newest
  `limit` rows matching the filter, never the whole trail — and say so. There is no
  honest way to extrapolate from a newest-first window, so nothing tries.

---

## 12 · LLMOps

*Route: `/app/tenant_admin/llmops` · Component: `ops/LLMOpsView.tsx`*

### What this screen is for

The self-improving prompt loop: **trace → eval → diagnose → release**. It is on
this portal because **the prompt registry is keyed per tenant** — your version of
the task prompt does not touch the platform floor and does not touch another
tenant.

### What is on it

| Row | Panel | Source |
|---|---|---|
| 1 | **Eval trend** — quality over time, as small multiples (one panel per metric family, not four lines on one axis) | `GET /ops/evals` |
| 1 | **Loop** — the four steps live: Watch (N scores) · Diagnose (N drafts open) · Gate (N awaiting sign-off) · Rollback (live vN) | derived from the loaded data |
| 1 | **Version mix** — donut by lifecycle status (active / staged / draft / archived) | `GET /ops/prompts` |
| 2 | **Prompt control** — write a new version of the task prompt, make it live, roll it back, and see which version each recent run was served | `GET/POST /ops/prompts` |
| 3 | **Release gate** — prompt changes awaiting a human, with approve (ship), reject (archive) and one-click rollback | `GET /ops/releases/pending` |
| 3 | **Diagnose** — reads recent quality failures, charts which metric families are failing, and drafts a better prompt | `POST /ops/diagnose` |
| 4 | **Prompt history** — every version with its status, and a unified line diff between any two | `GET /ops/prompts` + `/ops/prompts/active` |
| 5 | **Loop parameters** — read-only | `GET /ops/params` |

Verified live for `northwind.admin` (prompt key `operations_lead`): version 2
active (with rollback notes on it), version 3 draft, 0 releases pending, and eval
rows carrying `answer` (relevance 0.9 / groundedness 0.7), `step:retrieval` and
`step:guardrail` (input `pass`, output `redact`).

Loop parameters, live: `eval_margin 0.0`, `high_diff_fraction 0.4`,
`low_diff_fraction 0.15`, safety terms (`ignore`, `guardrail`, `safety`, `tool`,
`approval`, `never`, `policy`, `system prompt`), critical-config markers (`model`,
`tool`, `permission`, `role`, `scope`), tunable keys (`temperature`, `top_k`,
`top_p`) with max per-release deltas, and `auto_promote_ceiling: low`.

### What to say when demoing it

> "This is the one panel an operator acts on: change the live system prompt without
> a deploy. And two things on this screen are deliberately **not** editable and say
> so rather than being quietly absent."

Then name them:

> "A version is the *task* half only. The safety preamble, the persona's data scope
> and its tool allowlist are composed underneath every version at render time, and no
> version can remove them. It is shown here so it can be read rather than discovered
> by experiment. And the scope line names whose prompt this is — mine."

On the gate:

> "Low-risk improvements that clear the quality margin and touch nothing sensitive
> auto-ship and never land here. What reaches this queue is what the loop decided a
> human has to see — a diff above the high fraction, or one touching a safety term
> or a critical config marker. The ceiling is `low`: nothing above low risk
> auto-promotes, ever."

### What a jury might ask

**"Could this loop rewrite its own guardrails?"**
No. The safety preamble is composed underneath every version and no version can
remove it, and any edit touching a safety term (`guardrail`, `approval`, `never`,
`policy`, `system prompt`…) is forced to the human gate.

**"Are the prompt bodies in the history real?"**
The **active** version's body is the real one, read from `/ops/prompts/active`.
Other versions' bodies are illustrative samples and are **badged as such** on the
panel. Say that before someone finds it — it is stated on the screen, and it is
much worse if a juror discovers it themselves.

**"How much of this is automated?"**
Diagnose drafts, the tiered gate decides whether a human is needed, and only
low-risk changes that clear the margin ship on their own. Everything above the
ceiling waits for you.

### Anything deliberately absent

- **The loop parameters are read-only** on this screen — they are the rules the
  gate runs on, shown so an operator can read the policy that is about to judge
  their change.

---

## 13 · Settings

*Route: `/app/tenant_admin/settings` · Component: `app/[role]/[section]/SettingsView.tsx`*

### What this screen is for

The per-tenant control plane, and the one screen that says **who decided**. On this
portal you are writing your tenant's layer — which can tighten the platform floor
and can never weaken it.

### What is on it

1. **Text size** — the one control that changes nothing on the server and
   everything about whether the rest of the screen is readable.
2. **Settings catalogue** — a rail of namespaces and a panel. **27 controls**
   across `agent` (8), `guardrails` (7), `seat` (6), `jobs` (3), `memory` (2),
   `skills` (1). The rail says how many controls each namespace holds and how many
   are inert or read-only, so what is *not* on screen is still counted. *(Counts
   read from `SETTING_SPECS`, not re-walked — `agent` grew by two when the
   trajectory token ceilings landed: `agent.max_trajectory_tokens` at 36,000 and
   `agent.max_tool_result_tokens` at 4,000. Both are `tighten_only`, so you may
   shrink either for your tenant and never widen one.)*
3. **Skills** — write one, switch it on, and see which layer decided it. Resolved
   `platform ∪ tenant ∪ user`.
4. **Tool roster** — "6 of 9", and why the other three. A read-only projection of
   `agent.gate_min_risk` above it, re-read on every accepted write.

**The scope selector.** Every write targets a layer, and this portal may reach two
of the three:

| Layer | Available to you? | If not, the reason on screen |
|---|---|---|
| **Just me** | ✅ | — |
| **Everyone in my tenant** | ✅ | — |
| **Every tenant** | ❌ | *"The platform default is the platform admin's to set."* |

Every row carries: the description, the merge rule (`tighten_only` vs `override`),
a **provenance receipt naming the deciding scope**, and either a live control or
the reason it is not one.

### What to say when demoing it

Open `agent.gate_min_risk` and read its description aloud:

> "*Minimum tool-risk tier that forces the human approval gate. It is the ONLY
> gating signal, so a tenant may lower it — gating more — and never raise it.* That
> is a `tighten_only` key. If I try to weaken it the server refuses with its own
> sentence, and the row re-renders from the **PUT response**, not from what I typed
> — with a line saying what the fold decided when it differs."

Then the generated-form point:

> "There is no list of keys in this file. `GET /settings` returns every control I
> may read with its own descriptor, already resolved, and the screen draws whatever
> arrives — the control type, the help text, the legal values. A key added to the
> spec next month appears here with nothing in the web app edited. That is the
> mechanism behind 'operating this platform never requires touching code'; the first
> bespoke settings form is the moment that claim stops being true."

### What a jury might ask

**"What is that row that has no input?"**
It is not disabled — it is drawn as a statement. `agent.mode` reports
`effective: false`, and where a live row puts a control this one puts the
catalogue's own `inert_reason`, verbatim, on a marked surface. The reason currently
says: nothing consumes this key yet; the run's width comes from
`QueryRequest.depth_mode`, whose values are `auto|single|team` while this key's are
`fast|standard|team`, and the two vocabularies do not line up.

> "A control that binds to nothing is a real defect we hit — an operator changed a
> value that reached no run. So a key that is not wired says so on its face rather
> than accepting a write into the void."

**"If I add one PII entity, do I lose the platform's three?"**
No. `guardrails.pii.entities` merges as a union, and the write-outcome sentence
says which value ended up in force and which scope decided it.

### Anything deliberately absent

- **A control that is not yours** shows the value and a sentence naming who may
  change it — not a greyed-out box that posts and then 403s.
- **The platform safety skill** appears in your list, marked, with **no control
  beside it**. There is no value a tenant could send that would switch it off, and
  rendering a disabled toggle would imply there was.
- **The tool roster is read-only.** Pinning a subset for one run needs a per-run
  field the query request does not carry, and a pin control that changed nothing
  would be the exact defect this screen exists to remove.

---

## The three claims worth rehearsing

### 1 · Tenant isolation is testable, not asserted

Run it in this order, with two browser windows:

| Step | What it proves |
|---|---|
| Overview as `northwind.admin` vs `vertex.admin` | Different spend, different call counts, from the same component |
| Approvals as `northwind.admin` | The Vertex gate is **absent**, not filtered |
| Approvals as `vertex.admin` | The same gate, decidable |
| Governance as either | The scope badge names the tenant; the roster is that tenant's people only |
| Memory as either | `GET /memory/subjects` returns only that tenant's subjects, server-built |
| Ask a question as one, watch the other's Overview | The other tenant's figures do not move |
| Ask *"which requests are open?"* as each | Two different sets of request ids — the demo desk is per tenant, not one shared |

Underneath: Postgres row-level security with `FORCE` on **twenty-five** relations,
and a serving role (`aegis_app`) that is `NOSUPERUSER NOBYPASSRLS`. `/readyz`
reports it as a health component with its own evidence line.

**The last row of that table is the newest, and it was a real hole.** The
adapter's synthetic record store — the desk the demo tools act on — used to be one
process-wide global. Measured: `northwind.admin` and `vertex.admin` each asked for
open requests and both received the **identical** 25 ids out of the identical "40
matching requests" set, and two independent auditors reached it from two different
surfaces (A2A and MCP). Nothing real leaked, because those records are synthetic —
but *"the isolation holds except in the data we demonstrate it with"* is not a
sentence that survives a jury trying two logins side by side. The store is now
keyed by tenant. The invariant that mattered is preserved and narrowed: there is
still exactly **one** store per tenant, so a note added over MCP is still visible
to the agent looking at that same request.

### 2 · The audit trail is append-only by privilege, and checkable by hash

`UPDATE` and `DELETE` are revoked from the serving role on `audit_log`,
`run_events` (and every month partition) and `usage_ledger`. Postgres refuses, not
the application. Demonstrable in psql in ten seconds.

The grant is only half of it, because it does not bind whoever holds the owner
connection. So every `audit_log` row also carries its predecessor's hash mixed
into its own, and the **Verify the chain** button on the Audit screen walks
`GET /v1/audit/verify`: the server re-derives every hash and reports `chain intact`
or the first break. A per-row hash would only prove no row was *edited*; the chain
is what catches a row **removed**, because everything after it stops verifying.
Chains are per tenant, which is what makes the answer reachable without handing
anybody another tenant's rows — and rows written before the chain existed are
reported separately as `predate the chain, not covered`, never folded into the
verdict.

### 3 · The human gate survives a restart

Park a run at the gate, restart the backend, approve, and it resumes from the
checkpoint it paused on — with the Trace tab's checkpoint timeline showing the
continuation hanging off the interrupt tick. `AGENT_CHECKPOINTER=postgres`,
`langgraph.types.interrupt`, roughly six hundred checkpoint rows persisted and
growing.

---

## Known rough edges — read before you demo

1. **The Overview's security-posture card says "Posture unavailable."** The reason
   is good — posture describes the deployment, not a tenant, so `require_infra_reader`
   refuses a tenant-pinned principal and the dashboard does not send the request at
   all. But the *wording* on screen reads like a failure rather than a rule. Say the
   reason out loud before a juror reads the panel: "that is a platform-wide reading,
   and it is deliberately not mine."
2. **Forecast currently refuses** — 2 days of ledger history against 71 required.
   Covered in §4. Demo it as honesty, or seed history first.
3. **Cache hit rate and small-model share both read 0%.** Both are true (a freshly
   restarted process; single-model routing). Do not let them be read as broken
   instrumentation.
4. **Prompt history bodies other than the active one are illustrative**, and badged
   so on the panel. Name it before someone finds it.
5. **Grammar nit.** The Overview latency receipt reads *"1 runs in the window"*.
   Cosmetic.

---

*Companion guides: `persona-platform-admin.md` (the operator of Aegis itself),
`persona-ai-team.md`, `persona-client.md`. Module-level reference for any subsystem
named above is in the 29 files listed in `docs/teaching/README.md`.*
