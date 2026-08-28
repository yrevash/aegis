# Demo walkthrough — the Client portal

> Written by walking the real screens on **2026-08-23** against the running stack
> (frontend `localhost:3001`, backend `127.0.0.1:8110`), signed in as **both**
> `northwind.client` and `vertex.client`. Every figure quoted is what those two accounts
> showed on that day. Where a claim could not be checked by looking, it says "not
> verified".
>
> **Refreshed against the code on 2026-08-28** for the changes that landed since: the
> closed-by-default listings, the two new agent settings, and the compliance totals.
> Passages marked *"read from the code, not re-walked"* were checked against the
> component or the endpoint rather than by looking at a running box.

**Who this portal is for.** The tenant's end user. Not the person who runs Aegis, not
the person who administers the tenant, not the person who tunes the agent. Somebody who
wants to ask the system a question and then wants four things answered about it: *what
did it cost me, what is it doing, what needs my decision, and what does it know about
me.*

**It is the narrowest portal in the product, and that is the pitch.** Ten sections —
and it is the only portal that did *not* grow one when **Interop** was added, because a
client does not publish this deployment's standards to anybody. Every section is either
the client's own question, the client's own money, the client's own documents, or the
client's own governance record. Nothing here is somebody else's screen shown read-only.
Say that out loud before you click anything.

---

## Before you open a laptop lid

### Two accounts, and you need both

| Account | Password | Tenant | Seat cap |
|---|---|---|---|
| `northwind.client` | `demo` | 1 — Northwind Trading | `$7.50` |
| `vertex.client` | `demo` | 2 — Vertex Logistics | `$100.00` |

The isolation demo is: open the same section as each, side by side. It works properly on
**Savings** and **Documents**. It does **not** work on **Overview** — see the warning
below, because a jury doing exactly this comparison will land on Overview first.

**Never demo this portal as the plain `client` account.** It is a real seed row, and it
carries `tenant_id: NULL`: it signs in fine and then every tenant-scoped screen is
correctly empty, because there is no tenant to scope to. It owns no documents and no
ledger rows. That is why `client` and `ai` were removed from the login page's quick-in
buttons — the client overview showed a dash for *"Your spend"* while `northwind.client`
had **2,653** ledger rows behind it, and a reviewer reads an empty screen as broken
software rather than as the wrong account. Both tenant-bound clients are on the button
list precisely so the side-by-side comparison is one click each. *(Read from
`web/src/app/login/page.tsx` and `backend/src/app/seed.py`, not re-walked.)*

### The nav, as rendered

The portal groups its ten sections into two headings:

- **Workspace** — Console, Overview, Documents, Analytics, Savings, Forecast, Memory
- **Governance** — Approvals, Risk Map, Settings

(The catalogue order in `web/src/lib/portal.ts` is `console, dashboard, documents,
analytics, approvals, savings, forecast, risk, memory, settings`; the nav
regroups it. Either order is fine to walk — this guide follows the catalogue.)

### Two things you must know before a jury finds them

1. **Overview's top figures are platform-wide, not this tenant's.** Verified by calling
   `GET /v1/metrics` as both clients: the responses are byte-identical. Details and the
   honest answer are in [§2](#2-overview).
2. **There is no Access demo on this portal any more**, and that is the fix, not a gap —
   a client cannot drive the operator lane it compares against. Details in
   [§10](#10-access-demo--not-on-this-portal-on-purpose).

The first is not fatal, and is much worse if a jury finds it first.

### Listings open closed — know this before you click

*(DESIGN.md §4; read from the code, not re-walked.)* Every panel whose body is a
list of rows — documents, approvals, memory facts — opens as **one bar** carrying
its title, its row count and one key figure:

    Documents                                    10 rows · 7 ingested

**Hover expands it; click pins it open**, and the pin survives the pointer leaving.
One surface per page stays open — the thing the page is named after; on Documents
that is the upload control and the corpus figures, and the document list closes.
The rows are collapsed, **never `display:none`**: they stay in the accessibility
tree and reachable by keyboard, and each trigger is a real `<button>` carrying
`aria-expanded`.

---

## 1. Console

`/app/client/console` · nav label **Console** · hint `LangGraph`

### What this screen is for

The client asks the system a question and watches it work. It is the reason the portal
exists — without it the tenant's end user would have a set of reports and no way to ask
anything.

### What is on it

The same console surface as every other portal, narrowed by who is asking.

**The composer.** A question box, and:

- **Mode** — `Auto` / `Single` / `Team`.
- **Model** — `DeepSeek-V4-Flash`.
- **Image** — attaches an image (routes through the Vision pipeline; see the caveat in
  §11 of this file's sibling guide — the hosted vision deployment is not answering on
  this box).
- **The budget line** — `$0.04 of $7.50` for `northwind.client`, `$0.02 of $100` for
  `vertex.client`. This is the *seat's* spend against its cap, read from the usage
  ledger, and it is the first per-tenant figure on the screen.

**The path strip.** `Input rail 6 · Route · Retrieve & answer · Output rail 6`.

**Seeded prompts**, and note that they are client-shaped, not operator-shaped:

- *What is the status of my open request?*
- *Add a note to my request: the issue is still not resolved*
- *What is the resolution SLA for my request, and when does it escalate?*

**A real run**, captured as `northwind.client` on the third prompt:

```
Run finished in 35.4 s.
RUN · 11 STAGES · 35.4 s · 15,629 tok
SINGLE LANE   qa · single lane · chosen by auto
  "Sized for one agent — auto chose a single lane for this question, and it ran
   the whole turn."
GUARDRAILS 1 fired of 3   SOURCES 6 documents   COST $0.0464   WIDTH Single lane
Decided by: auto
ACTION  load_skill  LOW RISK
Measured from: run 3f9c93a6e114 · guardrail · retrieval · usage events
```

The answer carried an `output redacted` badge, and inside it the escalation contact
appeared as `[REDACTED_PERSON]` — the outbound PII rail firing on a real name that came
out of a retrieved policy document.

The answer also does something worth pausing on. It says, in its own words:

> *"I don't have a tool available to search for your requests (the `find_requests` tool
> isn't currently enabled for me). Without being able to look up your specific request,
> I can't tell you which priority or category it is, and therefore can't give you the
> exact SLA that applies to it."*

It then gives the general SLA framework from the retrieved documents and asks for a
request id. It did not invent a request.

**Sources tab.** `hybrid · 51 recalled → 6 ranked → 3 used`, with each ranked passage's
score and chunk id. **Every id begins `t1:`** — `t1:cc5eb9b969ad8ce0`,
`t1:104af63cd02885d7`, and so on. That prefix is the tenant, and it is on the screen.

**Run / Flow / Trace tabs.** Same as everywhere: the Flow tab draws the compiled LangGraph
topology read live from the running backend (`GET /v1/agent/topology`); the Trace tab
carries the decisions, the before/after of every rail that changed the text, the
per-node cost and latency, and the run's durable Postgres checkpoints.

**Left rail.** *Chats* (14 durable threads for this seat), *New chat*, *What I know*,
*Add a skill*.

### What to say when demoing it

> "This is the client's own console. Same engine, same rails, same trace — narrowed to
> what this person is allowed to do. Watch the source ids: every one starts `t1`, which
> is the tenant. And read what the answer says about itself — it tells the user that the
> request-lookup tool is not enabled for their role, gives them what it *could* ground,
> and asks for an id. It did not make up a ticket."

### What a jury might ask

**"Why does the client get a console at all?"** Because a portal with every report and no
way to ask a question is a dashboard, not a product. `POST /query` has always admitted
every authenticated role; it was the portal catalogue that used to withhold the surface,
and a route-coverage test now stops that regressing.

**"Why did it call `load_skill` and not the request lookup?"** Because `find_requests` is
not in the `client` persona's allowlist — see [Settings](#11-settings), which shows
`Tools 1 of 4 available`. The model reached for a skill loader and was refused by name.
That refusal is the allowlist working, and it is visible in the trace.

**"$0.0464 for one question is expensive."** Yes, and it is on the screen rather than
hidden. Two model-backed input rails, agentic retrieval over 51 candidates, a rerank, a
plan, a generate and two model-backed output rails. The seat cap ($7.50) is what stops it
running away, and it is enforced at the gateway chokepoint before the first call.

### Deliberately absent

- `COST not measured` / `TOKENS not measured` in the first seconds of a run — before any
  model has been called it refuses to print a zero.
- Under the Sources tab: *"Page, position and verbatim check — Neither was reported, so
  every quote here is the model's claim."* Aegis will show which document an answer stood
  on; it will not claim it verified the quotation against a page and an offset.

---

## 2. Overview

`/app/client/dashboard` · nav label **Overview** · hint `value at a glance`

### What this screen is for

The one-screen summary: what the platform is costing, how fast it is, how much work it
has done, and where the value is coming from.

### What is on it

**The KPI strip.**

```
COST SAVED  $0.30  ↑ 58% vs frontier
LLM CALLS   170
ACTIONS APPROVED  15
QUALITY     86%
P95 LATENCY 55.3s
YOUR SPEND  $0.09 of $8      ← this one is yours
CACHE HIT   0%
```

**Three trend charts**: Cost trend, Model mix (`SMALL MODEL 0% / Large model 100%`),
Query volume.

**Four value cards**, and two of them are absences:

| Card | Reading |
|---|---|
| **Value · Savings** | `58% cheaper per 1k vs frontier` — Source: `GET /metrics · cost_per_1k_queries_usd vs baseline_cost_usd` |
| **Security** | *"Share of requests the guardrails cleared — No counter records how many requests reached the rails or what they decided, so any share computed here would be 100 % by construction."* |
| **Performance** | `$27.49 cost / 1k queries` — Source: `GET /metrics · cost_per_1k_queries_usd` |
| **Audit** | *"Share of actions traced end to end — The audit trail is append-only and has no denominator: nothing counts the actions that were never written to it, so the share can only ever come out at 100 %."* |

Then a **Cost breakdown** panel.

### What to say when demoing it

Lead with the two empty cards, not the numbers.

> "Two of these four cards are empty, and they are the most important two. A security
> dashboard that shows '100 % of requests cleared the guardrails' is showing you an
> arithmetic artefact — nothing counts the requests that never reached the rails, so the
> denominator is the numerator. Same for the audit card: the trail is append-only, so
> the share of actions traced can only ever come out at 100 %. Rather than print a
> reassuring number that means nothing, it prints the reason it would mean nothing."

### The one you must not be surprised by

**`COST SAVED`, `LLM CALLS`, `ACTIONS APPROVED`, `QUALITY`, `P95 LATENCY`, `CACHE HIT`,
`Savings 58%` and `cost / 1k $27.49` are identical for Northwind and Vertex.** Verified
by calling the endpoint as both:

```
GET /v1/metrics  as northwind.client  →  cost_saved_usd 0.30075974,
                                          total_calls 170, quality_score 0.857,
                                          p95_latency_ms 55316.2
GET /v1/metrics  as vertex.client     →  byte-for-byte the same response
```

This is **deliberate at the API**, and the handler says so in its own docstring: these
are *"aggregate efficiency figures (cache-hit rate, small-model share, cost-per-1k,
measured savings) — not per-tenant spend, tenant listings or budget mutation, which stay
admin-gated."* The endpoint was relaxed from platform-admin to any authenticated
principal precisely so every portal's landing page would work.

Only **`YOUR SPEND`** on this strip is tenant-scoped — `$0.09 of $8` for Northwind,
`$0.02 of $100` for Vertex.

**The honest answer to give:**

> "These top figures are the platform's own efficiency posture — cache hit rate,
> small-model share, cost per thousand queries — and they are the same number for every
> tenant on this deployment, on purpose. The per-tenant money is `YOUR SPEND` here, and
> Savings on the next screen, which is ledger-backed and genuinely different per tenant.
> The strip should say 'platform' on its face, and it does not. That is a labelling gap,
> and I would fix it."

Then go straight to Savings and show a figure that *does* move.

### What a jury might ask

**"Small-model share 0 % — so no routing is happening?"** Routing is happening; a
*smaller* model is not, because this deployment's fleet offers one text deployment and
every role resolves to it. The screen prints 0 % rather than dressing it up. The saving
comes from role price bands, not from a smaller model.

**"Cache hit 0 %."** True on this box and the screen says so. Distinct questions,
semantic caches deliberately tight (cosine ≥ 0.99 and ≥ 0.97). A false cache hit on an
agent answer is worse than a miss.

**"P95 latency 55 seconds?"** Yes. Model-backed rails on both paths plus agentic
retrieval over a hosted fleet. It is the honest measured p95 across the runs this process
has seen, not a marketing number.

### Deliberately absent

- The Security and Audit cards, above. Both name the *reason* the metric cannot exist,
  which is the pattern used everywhere in this product.

---

## 3. Documents

`/app/client/documents` · hint `corpus · ingest`

### What this screen is for

What the agent can ground an answer in, and the one place a client can add more. This is
the second-best isolation demo on the platform after Savings, because the two tenants'
corpora look nothing alike.

### What is on it

**Four header figures**, each with its own source:

| | Northwind | Vertex | Source |
|---|---|---|---|
| Documents | 34 | 3 | `GET /documents · this tenant's own rows` |
| Ingested and searchable | 30 of 34 | **0 of 3** | `documents.status = succeeded` |
| Waiting to be ingested | 3 | 3 | `documents.status = pending or running` |
| Searchable passages | 74 | *absence* | `sum of documents.chunk_count · null on anything unparsed` |

Vertex's fourth figure is an absence rather than a zero:

> *Not one document has finished parsing, so no row carries a chunk count yet.*
> **TO MEASURE IT** — an ingest that reaches the chunk stage — the count is written with it

**Upload form.** Document, optional Type, optional Document date, `Upload and ingest`.
Two labels worth reading aloud:

- Type: *"Recorded as untyped if left blank — never inferred."*
- Date: *"The date the document is from, not today."*

**What the corpus is made of.** A donut — Northwind `succeeded 30 / pending 3 / failed 1`
with types `audit-probe` and `31 untyped`; Vertex `3 documents`, types
`none declared · 3 untyped`.

**Searchable passages per document.** Northwind: `REVO: A Quadruped Robotic Platform…`
(32), `§1026.13 Billing error resolution.` (12), `CRL cum Undertaking by the Company` (2),
then a long tail of 1s and `25 others`. Vertex: an absence.

**How well the parser read them.** A histogram of `documents.parse_confidence` — the
parser's own score in [0,1], `31 of the corpus scored` for Northwind. Vertex: *"Not one
document carries a parser score, so there is no distribution to draw."*

**When they arrived.** `documents.created_at`, and a note that is a small masterpiece of
restraint:

> *34 arrived across 2 days — most on 22 Aug. A third day makes this a histogram.*
> Source: `documents.created_at · a count of arrivals, not a quality trend`

**This tenant's corpus.** The full table — document, status, stage, pages, chunks, size,
uploaded, detail — with a per-row link to the ingest log. Northwind's failed row carries
its reason inline:

```
audit-probe-B  #19  failed  enrich  1 page  1 chunk  690 B  22 Aug 2026
   the embed stage failed: BudgetExceededError:
   tenant token_cap exceeded: used 2002971.0 of 2000000.0.
```

Vertex's three rows are `vertex-service-report-q3.pdf`, `vertex-incident-postmortem.md`,
`vertex-supplier-agreement.pdf`, all `pending · not started · not parsed · not chunked ·
no workflow`.

Source line: `GET /documents · the documents table, not the job queue`.

### What to say when demoing it

> "Thirty-four documents for Northwind, three for Vertex, and they are different files
> with different names. Nothing here is filtered in the browser — the endpoint returns
> this tenant's rows and no others. Now look at Vertex's passage count: it is not zero,
> it says no document has finished parsing so no row carries a count, and it names what
> would make the figure real."

And on the failed row:

> "That document did not fail on a corrupt PDF. It failed because the tenant hit its
> token cap partway through embedding. The budget ceiling is enforced at the same
> chokepoint that governs a live query, so it stops a background job the same way."

### What a jury might ask

**"Could I see another tenant's document by guessing an id?"** The rows come from a query
narrowed server-side to the caller's tenant, and the tenant-scoped tables carry
`FORCE ROW LEVEL SECURITY` with a serving role that is `NOSUPERUSER NOBYPASSRLS` — 25
RLS-enforced tables, per the security posture signals. The database enforces it, not the
prompt.

**"Why is a robotics paper in a support-desk corpus?"** Because it was uploaded during
development. Be straight about it — the corpus has accumulated test uploads
(`notif-live-*`, `audit-probe-*`, `zz-markall-*`, `dl`, `dl2`, `singleread`) and they
show up in source lists and in the knowledge graph. It is a housekeeping problem, not a
correctness one.

**"Why is Vertex stuck?"** Its three documents show `no workflow` — nothing has picked
them up. Do not improvise a cause; say the ingest workflow was never started for them and
that the Jobs surface in the operator portals is where that would be visible.

### Deliberately absent

- Vertex's passage count, parse-confidence histogram and per-document chunk chart — three
  separate absences, each naming what would fill it.
- `never inferred` on the document type. If you did not declare a type, it is `untyped`,
  not a guess.

---

## 4. Analytics

`/app/client/analytics` · hint `Apache Superset`

### What this screen is for

The tenant's own business board — Superset's charts, rendered inside Aegis, over data
already narrowed to this tenant.

### What is on it

**Metered usage** — and it is withheld:

> *The spend ledger is a tenant-administrator reading. Your own costs are on Savings.*
> **TO MEASURE IT** — A reachable Aegis backend and a session that may read
> `/admin/usage`.

**Insight boards.** `14 for your role · Superset answering`, a window selector (Last 7
days / 30 days / quarter / year / No filter), and `13 of 13 drawn`. The first is a live
embedded Superset dashboard — *"drawn by Superset itself, inside this page"* — with
source `embedded Superset dashboard · http://localhost:8088`.

**The rest are one query each**, grouped:

*Broken down by day* — Spend over time (`spend_usd by day`, 2.81), Runs over time
(`runs_total 171`), Human gates over time (`gates_total 15`), Background jobs over time
(21 rows), Governed actions over time (30 rows).

*Broken down by status* — Runs by outcome (`COMPLETED / in_flight / BLOCKED / REJECTED /
ERROR`), Gate decisions (`15 total — REJECTED 10, APPROVED 5`, plus average decision
seconds by status), Job outcomes (`43 total — SUCCEEDED 37, FAILED 6`).

*One board each* — Spend by model (`DeepSeek-V4-Flash 2.81`, `text-embedding-3-large
0.00`), Human gates by risk (`HIGH 15`), Governance trail (`events_total by action` —
`query.start`, `auth.login`, `ml.explain`, `router:route`, `analytics.embed_token`,
`documents.upload`, `find_requests`, and 13 others), Red-team defence
(`attacks_total / attacks_blocked by suite` — `owasp-full`, `prompt-injection`), Red-team
block rate (`block_rate_avg 0.82`).

Every board has a **"The rows behind the chart"** control.

### What to say when demoing it

> "These are Superset charts rendered inside Aegis, never in another tool and never in
> another portal. The backend builds the query, mints a short-lived guest token, and the
> token carries this tenant's row-level-security rule — so the `WHERE` clause is not
> something the browser could remove even if it tried. And every chart has 'the rows
> behind the chart', so a figure you disbelieve is one click from its own data."

### What a jury might ask

**"Is Superset actually running, or is this a mock?"** Running, at `localhost:8088`, and
the first board is a genuine embedded dashboard rendered by Superset in an iframe. The
other twelve are Aegis-drawn charts over Superset chart-data queries, which is why they
each carry their own row count.

**"'14 for your role' but '13 of 13 drawn'."** The embedded dashboard is the fourteenth
and is counted separately from the twelve query-backed boards plus itself. If pushed, say
you would tighten that copy.

**"Why can a client see red-team block rate here but not on Guardrails?"** Fair catch.
The red-team board is a stored analytics dataset; the Guardrails posture panel is a live
platform-only endpoint. Different sources, different guards. It is worth acknowledging as
an inconsistency rather than defending.

### Deliberately absent

- **Metered usage**, withheld with a reason and a redirect. A client does not get the
  tenant's spend ledger; they get their own costs on Savings. That refusal — and the
  pointer to where the right answer lives — is better than a 403 page.

---

## 5. Approvals

`/app/client/approvals` · group **Governance** · hint `the human gate`

### What this screen is for

What happened to the actions your questions asked for. When the agent proposes something
above the tenant's risk floor, it does not do it — it parks, and the run waits.

### What is on it

**Filters.** `Waiting / Decided / Everything`, and `RAISED: Last 24 hours / Last 7 days /
Since the beginning`, plus `Refresh`.

**Waiting on a decision.** `0 gates are parked · Past deadline 0 · On track 0` for
`northwind.client` on the day of writing.

**Already decided** — an absence that explains the query rather than the data:

> *Not counted in this queue. This query asked the server for waiting gates only, so
> nothing here counts what has already been decided. The Decided tab loads them.*

Source: `aegis.approvals · last 7 days`.

**The empty state:**

> **Nothing is waiting on you** — *When the agent proposes an action above the risk floor
> your tenant set, it parks here instead of running it — and the run waits until you
> decide.*

### The scoping rule, which is the interesting part

`GET /approvals` narrows by *what the principal is*, never by a query parameter the
browser chose. From the handler's own docstring:

- **Platform staff** see every tenant's gates and may target one, but may decide only the
  gates that carry no tenant; every other row comes back `decidable=false` with the
  reason.
- **A tenant admin** sees and decides its own tenant's gates. Naming another tenant is a
  403, not a wider read.
- **Every other authenticated principal** — *"the client the gate was raised for, above
  all"* — sees **the gates they raised** and no others, read-only. That scope is the
  requester's own user id, which is strictly tighter than their tenant's.

A client with a tenant-admin's view of the queue would be a leak; a client with no view
at all was the gap this section closed.

### What to say when demoing it

> "This is the client's side of the human gate. They do not decide it — that is their
> administrator's job — but a user whose question tripped a gate used to have no screen
> that told them what became of it. And the scope is the tightest in the product: not
> 'your tenant's gates', *your* gates. The server derives that from the token; there is
> no parameter the browser could widen."

### What a jury might ask — and the honest answer

**"It's empty. Show me one."** You probably cannot, from this account, and you should say
why rather than fumbling. Look at [Settings](#11-settings): the `client` persona holds
exactly one tool, `add_case_note`, at **low** risk. The gate fires at `high` and above.
So a client seat, in this configuration, **cannot raise a gate through its own console**.
The queue is correctly empty because nothing this seat can do reaches the floor.

The gates that exist on the box today belong to other principals: 15 approved, 15
rejected, 8 expired, 1 pending (seeded, tenant 2) and 1 resuming. To *show* a live gate,
demo it from an operator account whose persona holds `update_request_status` (risk high)
— that is the AI-team portal's console, or a tenant admin's.

**"So the section is decoration?"** No — it is the client's record of a decision made
about their run, and it is the correct place for it. But be honest that with a
one-low-risk-tool persona it will usually be empty, and say what would fill it.

**"What does 'past deadline' mean?"** Every parked gate carries an SLA deadline; the
queue sorts pending gates soonest-deadline-first, and an undecided gate can expire
(8 have). Expiry is a decision too, and it is recorded.

### Deliberately absent

- The "Already decided" note above — it tells you the *query* excluded them, not that
  there are none.

---

## 6. Savings

`/app/client/savings` · hint `baseline vs actual`

### What this screen is for

The single question a paying customer has: what did this cost me, and what would it have
cost without Aegis? **This is the cleanest tenant-isolation demo on the platform.** Do
not skip it.

### What is on it

**The headline**, and it is different per tenant. Verified live, both accounts, same
minute:

| | Northwind (tenant 1) | Vertex (tenant 2) |
|---|---|---|
| **Projected** vs frontier | **$8.61** | **$0.52** |
| Saved (banked) | **$0.00** | **$0.00** |
| Share of baseline | 59 % | 56 % |
| Frontier baseline | $14.70 (`14.697900`) | $0.93 (`0.925400`) |
| Actual spend | $6.08 (`6.083300`) | $0.41 (`0.408000`) |

**Read the tile label.** It says *Projected vs frontier*, not *Saved*, and the banked
figure is zero. That is deliberate and it is the interesting part of this screen — see
"what a jury might ask" below.

**What this figure leaves out.** `2 of 3 sources at $0` — *"Reported at zero rather than
estimated — each row carries its own reason."*

**Where the savings come from**, marked *Adds up to the total*:

| Source | Northwind | Why |
|---|---|---|
| Small-model routing | $0.00 | Not realised on this fleet — see below |
| Semantic retrieval cache | $0.00 | A cache hit never reaches the ledger |
| Answer cache | $0.00 | Same |

All three rows are $0, and each says why on its own line. The card beside the table reads
*3 of 3 sources at $0*.

Source line: `gateway usage ledger · computed 23 Aug 2026, 18:51`.

The API carries the reason for the two zeros:

> *`saved_usd` is $0 because no saving was realised on this fleet. Every priced call was
> answered by DeepSeek-V4-Flash, which is also the deployment the frontier baseline is
> priced from — so the gap below compares two price bands for the same model, not a
> cheaper model against a dearer one. The router's per-turn role assignments are real and
> logged; what is missing is a second deployment to route to. `projected_usd` is what
> those same assignments would save on a fleet that has one.*

### What to say when demoing it

Do it as a side-by-side. Two browser windows, two accounts, same screen.

> "Northwind projects eight dollars sixty against a fourteen-seventy frontier baseline.
> Vertex projects fifty-two cents against ninety-three. Same platform, same minute, same
> endpoint — and a query I run as Northwind does not move Vertex's number by a cent. This
> is not a filter in the browser: `GET /savings` reads the usage ledger narrowed to the
> caller's tenant, and the tables underneath carry `FORCE ROW LEVEL SECURITY` with a
> serving role that cannot bypass it."

Then the two zeros:

> "And read the breakdown — all three rows are zero, each for its own reason. Two are
> cache hits, which bypass the model entirely and so never enter the ledger this figure
> is computed from. The third is the one worth your attention: our router assigns a cheap
> tier or a frontier tier per turn, and it really does — but this gateway has exactly one
> text deployment, so both tiers resolve to the same model. Subtracting one price band
> from the other would have booked nine dollars of savings that nobody banked. The
> endpoint checks the ledger for which deployments actually answered, finds one, and
> refuses to call it a saving. It shows the projection instead, labelled."

### What a jury might ask

**"Is 'frontier baseline' just a bigger number you chose?"** It is every call in the
tenant's ledger repriced at the baseline model and role — `generation` /
DeepSeek-V4-Flash, named on the AI-team portal's Token opt screen along with the unit
costs for every role. Same ledger, one substitution.

**"You said small-model routing but the model mix says 0 % small model."** Correct — and
the endpoint says so before you do. This gateway offers one text deployment, so
`MODEL_CHEAP`, `MODEL_GENERATION` and `MODEL_REASONING` all resolve to DeepSeek-V4-Flash.
The router still assigns a role per turn and those assignments are logged, but a role is
priced from its own band, so subtracting one band from the other prices *the same model*
two ways. `GET /savings` reads `usage_ledger` for the deployments that actually answered,
sees one, and books `saved_usd = 0` with the figure moved to `projected_usd`. Restore a
multi-deployment fleet and it flips back with no code change, because both sides are read
at request time. The honest sentence is: *the mechanism runs, the saving is not yet
banked, and the platform is the one telling you that.*

**"Then why show the projection at all?"** Because it is the measured value of a real
decision under a fleet that has more than one model — which is the fleet this runs on at
the hackathon. Showing it under the word "saved" would be the lie; showing it labelled is
just a forecast with its assumption named.

**"This was a real bug?"** Yes — cross-tenant figures on this surface was a defect that
was found and fixed, which is exactly why it is now the demo to run. Say so; a fixed bug
you can reproduce the fix for is more convincing than a feature that was always right.

### Deliberately absent

- Two of three savings sources at $0, each with its reason. See above.

---

## 7. Forecast

`/app/client/forecast` · hint `statsforecast · conformal`

### What this screen is for

Demand and spend projected forward — with the interval coverage that was *actually
measured*, not the coverage that was requested.

### What is on it

**Header.** `adapter (synthetic domain)`, horizon selector `7 / 14 / 30`, `Refresh`.

**Four figures:**

```
Next day                    9.23 requests
   Source: 121 observations · adapter
Projected, next 14 days     156.33 requests
   Source: Sum of the 14 projected points · band 4.73–13.72 requests at step 1
Held-out error · smape      25.1%
   Source: AutoARIMA selected from 3 candidates
Coverage achieved           67%
   Source: 90% requested · 42 held-out points
```

**The chart.** *Service requests opened per day — the domain demand series*, with the
observed history, the AutoARIMA forecast, the 90 % conformal band (`widest 15.16 requests
at step 2`), and a marker for the last observed point. `121 × D history · horizon 14`.

**Interval coverage · measured.** Marked `below request`:

```
66.7% achieved, against 90.0% requested
28 of 42 held-out actuals inside the band · 3 rolling-origin windows
```

**How the model was chosen.** Three rolling-origin windows, 42 points:
`SMAPE 25.11% · MAPE 29.27% · MAE 2.886`, and the candidate table:

| Model | sMAPE | MAE | Coverage |
|---|---|---|---|
| **AutoARIMA** *(selected)* | 25.11 % | 2.886 | 66.7 % |
| AutoETS | 25.53 % | 2.938 | 66.7 % |
| SeasonalNaive | 32.14 % | 3.571 | 69.0 % |

Source: `adapter (the domain records, through the swap seam) · univariate · statsforecast`.

### What to say when demoing it

This is the screen where the platform's honesty costs it something, so use it.

> "It asked for a ninety percent interval and it got sixty-seven. Twenty-eight of
> forty-two held-out actuals fell inside the band. Most forecasting screens would print
> the ninety, because ninety is what you configured. This one prints what it measured,
> labels it 'below request', and shows you the three candidate models it chose from
> including the one that scored *better* on coverage and worse on error. If you only
> believe one number on this platform, believe this one — it had every opportunity to
> flatter itself and didn't."

### What a jury might ask

**"So your forecast is unreliable?"** The point estimate is a 25 % sMAPE on 121 daily
observations of a synthetic series — modest. The interval is under-covering, which means
the band is too narrow, which the screen states. What matters for the pitch is that the
number is *measured on held-out data across three rolling-origin windows*, not asserted
from the configuration.

**"Why did you pick AutoARIMA when SeasonalNaive had better coverage?"** Because selection
is on sMAPE (stated: `CANDIDATES · SELECTED ON SMAPE`), and SeasonalNaive is 7 points
worse on error. Both facts are in the table; the selection criterion is named.

**"Is this real data?"** `adapter (synthetic domain)` — it says so in the header and in
the source line. The series comes through the domain-swap seam, so on a real deployment
this is the customer's own demand series with the same machinery.

### Deliberately absent

- Nothing is hidden on this screen. Its distinguishing feature is that the *unflattering*
  number is the headline.

---

## 8. Risk Map

`/app/client/risk` · group **Governance** · hint `OWASP-Agentic`

### What this screen is for

How an autonomous agent can go wrong, and which control in this codebase holds each risk
down — expressed as a before/after, so a non-technical reader can see the movement.

### What is on it

**Headline.** `RISK REMOVED BY AEGIS — 50% less agent-risk exposure across all 9 risks`,
`BEFORE AEGIS 109`, `AFTER AEGIS 54`, `RISKS MOVED 9 of 9`.

Breakdown: `Low residual · 6 risks, 25 still carried` / `Medium residual · 3 risks, 29
still carried` / `Removed by Aegis · 55`.

**Likelihood × impact.** A 5×5 grid plotting before and after positions, banded high /
medium / low.

**Risks by category, worst residual first.** Output integrity (Medium, 2), Reliability
(Medium, 2), Input integrity (Medium, 1), Accountability (Low, 1), Autonomy (Low, 1),
Governance (Low, 1), Tools (Low, 1).

**How far each risk moved, biggest reduction first** — nine rows, each with the risk, its
category, the before → after score, the percentage, the residual band, the **control**,
and the **files that control lives in**:

| | Risk | Move | Control | Files |
|---|---|---|---|---|
| AA-01 | Excessive agency / autonomy | 15 → 5, −67 %, Low | Human approval gate | `agent/graph.py` (gate/approval nodes), `agent/deps.py` (`gate_min_risk`) |
| AA-04 | Sensitive-information disclosure | 15 → 5, −67 %, Low | PII redaction, both directions | `guardrails/pii.py`, `guardrails/rails.py` |
| AA-02 | Tool misuse / hijacking | 12 → 3, −75 %, Low | Tool allowlist + reversible actions | `adapter/tools.py` (`ALLOWLIST`, `run_tool`, `is_allowed`, `InverseAction`) |
| AA-08 | Cascading failures / unbounded consumption | 9 → 3, −67 %, Low | Hard loop cap + spend ceiling | `agent/graph.py` (reflect), `data/governance.py` |
| AA-06 | Identity / privilege abuse across tenants | 10 → 5, −50 %, Low | Tenant isolation (roles + database-enforced) | `core/governance.py`, `data/session.py` |
| AA-03 | **Prompt injection / jailbreak** | 16 → 12, **−25 %, Medium** | Fail-closed injection screening | `guardrails/classifier.py`, `guardrails/rails.py` |
| AA-05 | Insecure output handling | 12 → 8, −33 %, Medium | Output rail | `guardrails/rails.py`, `guardrails/schema.py` |
| AA-07 | Untraceable / unaccountable actions | 8 → 4, −50 %, Low | Immutable audit log | `data/audit.py`, `observability/otel.py` |
| AA-09 | Hallucination / ungrounded answer | 12 → 9, **−25 %, Medium** | Grounded answers + abstention | `retrieval/pipeline.py`, `ml/model.py` |

Source: `docs/security/owasp-agentic.md · generated 23 Aug 2026, 18:52`.

That file exists in the repository and opens with a paragraph the screen is downstream of:

> *No one has "solved" prompt injection. Reported attack-success rates against frontier
> models remain in the ~50–84 % range even with best-effort defenses, so Aegis does not
> claim to block injection — it claims layers … and — the decisive layer — never let the
> model take a consequential action without a human.*

### What to say when demoing it

Point at the *worst* rows, not the best.

> "Nine risks, all nine moved, and the two that moved least are the two everybody in this
> room already knows are unsolved: prompt injection, down twenty-five percent and still
> carrying medium residual, and hallucination, the same. Nobody has solved prompt
> injection — published attack success rates against frontier models are still fifty to
> eighty-four percent. What Aegis claims is layers, and one decisive layer: the model
> never takes a consequential action without a human. That is why excessive agency is the
> row that moved sixty-seven percent."

### What a jury might ask — and this is the important one

**"Where do the numbers 109 and 54 come from?"** Be direct: **these are authored
likelihood × impact scores from `docs/security/owasp-agentic.md`, not measurements.**
Every other money or latency figure in this portal is metered; this one is an assessment.
What is checkable is the right-hand column — every control names a real file, and a test
resolves those references against the repository. Do not let a jury walk away thinking
"50 % less risk" was measured. Say the number is a structured judgement and the file
paths are the evidence.

**"Is this OWASP official?"** The source document is explicit that the OWASP GenAI
Security Project's agentic work is evolving, that the *themes* are stable but the exact
`ASI0x` numbering should be confirmed against the current publication before quoting it.
Aegis uses its own `AA-0x` identifiers here for that reason.

### Deliberately absent

- Nothing claims certification. The related Compliance surface (DevOps portal) is
  explicit: *aligned with 13 published frameworks, certified against none — no
  certificate held, no independent audit, no attestation.* That page now leads with what
  is **enforced** — 38 of its 124 controls — and puts the rest one click away, for the
  same reason this page leads with the rows that moved least.

---

## 9. Memory

`/app/client/memory` · hint `Qdrant`

### What this screen is for

What the agent has kept about *this user*, who else can reach it, how long it is kept, and
the controls to teach it, correct it or erase it.

### What is on it (as `northwind.client`, a fresh-ish seat)

**Header.** `Facts held 0 · Sessions 3 · Last active 2m ago`. Source
`GET /memory/subjects`.

**Your record** — `northwind.client · 0 facts · 3 sessions`, and **Who can reach this
record**:

- `northwind.client` — the record itself. *"0 durable facts, 3 sessions — nobody else's
  are in here"*
- Tenant 1's administrator — read and correct, inside the tenant
- The other people in this tenant — *"This sign-in manages one subject, so it was served
  exactly one row — the isolation working, not a gap."*
- The platform operator — *"Aegis itself, which is refused nothing by tenancy — every
  read of it lands on the audit trail."*

**Teach it something.** A 2000-character box and `Save fact`, with *"Screened before it
is stored"* — the guardrails run at write time, so a body the rails refuse never reaches
the store. Empty state: *"Nothing believed yet — write the first fact above."*

**How long this is kept.** `Aegis default — CONVERSATION TURNS 90 days · SUPERSEDED FACTS
30 days`, `PAST THE HORIZON NOW: Nothing has aged past the horizon`, and **Erase this
record**. Source: `GET /memory/retention · tenant scope · write log never swept`.

**Chats.** 14 durable threads for this seat.

**Subject.** `user:8 · last seen 2m ago`, `TURNS 3 · SUPERSEDED 0 · RECALLS 0`.

**Writes per day.** *"Nothing written yet — Every learn, update and retirement lands
here."*

**What we know / Profile / Sessions / Recent updates.** All honest empties: *"No facts
recorded for this subject yet"*, *"No profile has been consolidated for this subject
yet"*, three sessions each *"No running summary yet"*, *"No updates yet. Changes appear
here, newest first."*

**Why did it recall this?** A box that asks what the agent *would* recall for a given
question without running a turn. *"Nothing traced yet."*

### What a populated one looks like

The `northwind.analyst` seat on the same box holds 5 facts, 56 sessions, 145 turns, 16
superseded and 255 recalls, with a per-fact confidence, a recall count, and a readable
supersession chain (*"The user prefers short replies"* → *"The customer prefers short
replies"* → *"The customer requests short replies"*). If you want to demo a full memory
screen, use that account; if you want to demo the client's control over it, use this one.

### What to say when demoing it

> "This is the user's own record and the four rows at the top say exactly who can reach
> it — them, their tenant's administrator, nobody else in the tenant, and the platform
> operator whose every read lands on the audit trail. There is a box to teach it
> something, and the fact is screened by the guardrails when it is *saved*, not when it
> is used. And there is an erase button. A memory screen you cannot correct is a report,
> and this is not one."

### What a jury might ask

**"Nine empty panels is a bad look."** It is a fresh seat. Every one of those empties says
what would fill it and none of them shows a zero pretending to be a measurement. If you
want a populated screen, sign in as the analyst. Consider warming this seat with two or
three turns before a demo.

**"Does erase actually erase?"** It removes the subject's record. Note the retention
source line: *"write log never swept"* — the append-only write log is not deleted by the
retention horizon. Say that; it is the honest boundary between "your facts are gone" and
"there is no record anything ever happened".

**"Can the tenant admin read my memory?"** Yes — row two says so, plainly, on the user's
own screen. That is the design: a tenant's administrator can read and correct inside
their tenant. Telling the user is the point.

### Deliberately absent

- Every empty panel names what would fill it.
- `write log never swept` on the retention source line.

---

## 10. Access demo — not on this portal, on purpose

`web/src/lib/portal.ts` no longer lists `simulation` for `client`, so there is no such
section and no such nav entry. This heading is kept because the screen *was* here, and
because why it left is a better answer than the screen ever was.

The Access demo runs one question as two roles at once and compares what each was allowed
to retrieve and to do. A `client` principal cannot drive the operations-lead lane:
`_resolve_persona` (`backend/src/app/api/routes.py`) refuses an operator-scoped persona to
the self-scoped `client` role, and returns 403 before the stream opens. That refusal is
the platform working exactly as designed. What did not work was the screen: half of a
two-lane comparison 403s every time, so the section drew an error where it promised a
control — the shape `portal.ts` calls out in its own doctrine and already refuses for
`jobs` on `devops`.

**Where to demo it:** the AI-team portal (`/app/ai_team/simulation`), whose principal can
drive both lanes. See `persona-ai-team.md`.

**If a jury asks why a client cannot see it:** because the comparison needs an operator,
and this account is not one. The isolation the demo illustrates is the same isolation that
stops this account from running it — which is a better sentence than any screen.

## 11. Settings

`/app/client/settings` · group **Governance** · hint `platform → tenant → you`

### What this screen is for

Every control that applies to this seat, with **who decided its current value**, and —
where the client may not change it — who may.

### What is on it

**Text size.** 90 / 100 / 110 / 125 %, with the scope selector *Just me / Everyone in my
tenant / Every tenant*.

**Categories.** How the agent answers (8), Guardrails (7), Memory and retention (2), Seat
(6), Skills (1). Note what is **not** in a client's catalogue that is in the AI team's:
*Ingestion jobs* — the `jobs.*` keys are not readable by a client at all, and an
unreadable key is **omitted** from the list rather than refused, so it cannot blank the
screen. *(Counts read from `SETTING_SPECS`, not re-walked: two token ceilings joined the
`agent.*` family.)*

**How the agent answers** — eight controls. Six of them read `Read only` for a client,
with the sentence *"Only a platform admin, a tenant admin and the AI team may change
this."*

| Control | In force | Decided by | Client may change |
|---|---|---|---|
| `agent.agentic_retrieval_max_rounds` | 2 | platform default | no · cannot be weakened |
| `agent.gate_min_risk` | high | platform default | no · cannot be weakened |
| `agent.max_plan_iterations` | 4 | platform default | no · cannot be weakened |
| `agent.max_trajectory_tokens` | 36,000 | platform default | no · cannot be weakened |
| `agent.max_tool_result_tokens` | 4,000 | platform default | no · cannot be weakened |
| `agent.mode` | standard | platform default | **Not wired up — "Nothing reads this yet."** |
| `agent.model` | default | platform default | yes (a preference, not a permission) |
| `agent.team.max_parallel` | 4 | platform default | no · cannot be weakened |

The two token ceilings are readable by a client and writable only by the admin tiers and
the AI team. They bound one lane's whole trajectory and one tool result's contribution to
it — Aegis has no trajectory compaction, so they stand in for one — and both are
`tighten_only`, which is the same direction rule `gate_min_risk` carries.

`gate_min_risk` carries the sentence worth reading aloud:

> *Minimum tool-risk tier that forces the human approval gate. It is the ONLY gating
> signal, so a tenant may lower it (gating more) and never raise it.*

`agent.model` carries the other one:

> *A preference, not a permission: the server validates the choice against that set on
> write and again at the point of use, and the ledger prices the deployment that actually
> answered.*

**Skills.** `0 in force`, one platform skill listed as `Off · Not yours to change`, and a
**Write one · SKILL.md format** editor whose scope selector offers a client only
*Personal — only me* (a tenant admin also gets *Tenant — everyone here*). With the rule:

> *Screened when you save, not when it is used: a body the guardrails refuse is never
> stored. Only the name and the description reach a prompt — the agent loads the rest with
> a `load_skill` tool call you can watch in the trace.*

**Tools.** And this is the panel that explains the Console:

```
Tools  1 of 4 available     persona client     human gate at high and above

TOOL                   RISK     STATUS
add_case_note          low      Available
assign_request         medium   Not in your persona
find_requests          low      Not in your persona
update_request_status  high     Not in your persona
```

Source: `GET /v1/console/tools`. The AI-team portal's `operations_lead` persona shows
`4 of 4 available` on the identical panel, with `update_request_status` marked *Human
approval required*.

### What to say when demoing it

> "Every value says who decided it. And look at the tools: this seat has one of four.
> That is not a preference the user set — it is the persona's allowlist, checked before
> the handler runs. It is exactly why the console told the user a moment ago that it
> could not look up their requests. The screen and the run agree, because they read the
> same endpoint."

Then, if you have the AI-team portal open:

> "Same panel, operator persona: four of four, and the high-risk one says *human approval
> required*. The gate floor is `high` and a tenant may lower it, never raise it —
> `tighten_only`. That is the sentence that keeps a tenant from configuring their way out
> of the human gate."

### What a jury might ask

**"`agent.mode` says 'Not wired up'."** It does, and it is right to. Nothing reads that
key yet; the width control that *is* wired is the composer's Mode chip, which reports
`decided_by` on every run. A catalogue that lists a key nothing reads, and labels it, is
better than one that quietly does nothing.

**"Can a client weaken a guardrail?"** No. Every merge rule is `tighten_only` (a tenant
may make it stricter) or `union` (a tenant's denied terms are added to the platform's,
never subtracted). Those rules are printed per-control on the Guardrails screen in the
operator portals.

**"Can a client write a skill that changes behaviour?"** Personal scope only, screened at
save time, and the body only enters a prompt via a `load_skill` tool call you can watch
in the trace. It is a visible tool call, not an invisible prompt injection point.

### Deliberately absent

- `Not wired up` on `agent.mode`.
- `0 in force` for skills, with the one platform skill shown as `Off · Not yours to
  change` — you can see it exists and see that you are not the layer that owns it.

---

## Why this portal is the narrowest, and how to say it

A jury will notice that Client has ten sections where Platform admin has twelve and AI
team sixteen, and that several of the eleven are read-only. Get in front of it.

> "The client portal is deliberately the narrowest surface in the product. Its job is
> four questions: what is this costing me, what is it doing, what needs my decision, and
> what does it know about me. So it gets a console, its own spend, its own documents, its
> own analytics, its own memory, its own approvals record and its own risk register — and
> nothing else. The rule the catalogue is built on is that a role only gets a section it
> can *act* on; a read-only copy of somebody else's screen is a tab on their screen, not a
> section on this one. The two exceptions are deliberate and they are the role's own
> record: their approvals and their memory are theirs to read even where the write belongs
> to their administrator."

That rule is written at the top of `web/src/lib/portal.ts` and a route-coverage test in
`backend/tests/api/test_route_coverage.py` reads that file and asserts every listed
section renders a live surface.

### The isolation demo, in the order that works

1. **Savings** — two windows, two accounts. $8.70 vs $0.51. Ledger-backed, genuinely
   different, computed the same minute.
2. **Documents** — 34 documents vs 3, different filenames, and Vertex's absences named
   rather than zeroed.
3. **Console** — every source id prefixed `t1:` / `t2:`.
4. **Settings → Tools** — `1 of 4` here, `4 of 4` on an operator persona.

Skip Overview for this comparison. It will show you the same numbers twice.

---

## Things that are wrong or fragile on this box — read before demo day

Reported, not fixed.

1. **Overview's KPI strip is platform-wide and does not say so.** `GET /v1/metrics`
   returns byte-identical responses to `northwind.client` and `vertex.client`
   (`cost_saved_usd 0.30075974`, `total_calls 170`, `quality_score 0.857`,
   `p95_latency_ms 55316.2`). This is deliberate at the API — the handler's docstring
   calls them *"aggregate efficiency figures … not per-tenant spend"* — but the client's
   Overview presents them without a scope label, so `COST SAVED`, `LLM CALLS`, `QUALITY`,
   `P95` and `CACHE HIT` read as the client's own. Only `YOUR SPEND` differs. A jury
   comparing two tenants will land here first.

2. ~~The Access demo's operations-lead lane cannot run from this portal.~~ **Fixed.**
   `simulation` was removed from `client` in `web/src/lib/portal.ts`: a `client` cannot
   drive the operator lane the screen exists to compare against, so half the comparison
   403'd every time. It lives on the AI-team portal, where both lanes run. See
   [§10](#10-access-demo--not-on-this-portal-on-purpose).

3. **Approvals will almost always be empty for a client seat.** The `client` persona holds
   one tool, `add_case_note`, at low risk; the gate floor is `high`. A client seat cannot
   raise a gate through its own console, so the inbox it is scoped to (`gates you
   raised`) has nothing to show. Correct behaviour, confusing demo.

4. **Vertex's three documents are stuck.** All `pending · not started · no workflow`.
   Nothing has picked them up, so Vertex's corpus is unsearchable and any query as
   `vertex.client` retrieves nothing (the Access demo's provenance line came back as
   `· RRF` with no arms). If you plan to demo *anything* retrieval-shaped as Vertex,
   ingest something first.

5. ~~"Small-model routing" overstates what is happening.~~ **Fixed.** The Savings
   breakdown used to attribute 100 % of the gap to small-model routing while the Overview
   said `Small-model share 0%`. `GET /savings` now reads `usage_ledger` for the
   deployments that actually answered; finding only the baseline's own model, it books
   `saved_usd = 0` and reports the figure as `projected_usd` with the tile relabelled
   *Projected vs frontier*. Embeddings were also being priced at the chat baseline — half
   a million embedding tokens valued as if a chat model could have embedded them — and no
   longer are. See [§6](#6-savings).

6. **Northwind's corpus is full of test uploads.** `notif-live-*`, `audit-probe-*`,
   `zz-markall-*`, `singleread`, `dl`, `dl2`, plus a robotics paper. These are the
   top-ranked sources on several client runs — a jury reading the STANDS ON list will see
   `notif-live-1787432237982` where they expect a policy document.

7. **Analytics counts boards inconsistently.** `14 for your role` next to `13 of 13
   drawn`. Cosmetic, but it is the kind of thing this product otherwise gets right.

8. **Voice and Vision are unavailable in this environment.** Both hosted deployments
   return `NotFoundError: The API deployment for this resource does not exist`. Neither
   is a section in this portal, but the console's **Image** attachment routes through
   Vision, so an image attached to a client question will be blocked fail-closed at the
   injection screen.

---

## One-line crib

| Section | The one sentence |
|---|---|
| Console | The client's own question, with every source id stamped with their tenant. |
| Overview | Two of the four value cards are empty on purpose — and the reason is the point. |
| Documents | 34 documents for Northwind, 3 for Vertex, and Vertex's missing counts say so rather than showing zero. |
| Analytics | Superset's own charts, behind a guest token carrying this tenant's `WHERE` clause. |
| Approvals | Not your tenant's gates — *yours*, scoped by the server to your own user id. |
| Savings | $8.70 vs $0.51, same minute, same endpoint, different tenants. |
| Forecast | Asked for 90 % coverage, measured 67 %, printed the 67. |
| Risk Map | Nine risks moved; the two that moved least are the two nobody has solved. |
| Memory | Four rows say exactly who can reach this record, and there is an erase button. |
| Access demo | Not on this portal — a client cannot drive the operator lane. Demo it from AI team. |
| Settings | One tool of four, and that is why the console said what it said. |
