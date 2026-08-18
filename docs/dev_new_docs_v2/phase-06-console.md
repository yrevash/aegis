# Phase 6 — The unified console

**After phases 3, 4 and 5 — this is what makes them visible.**

Building it earlier means building it twice: the events it renders do not exist until Phase 5,
and the documents it cites do not exist until Phase 4.

Phase 3 gives Aegis real ingestion. Phase 4 gives it real concurrent agents. Neither is
visible today. The console is a single-shot bento panel over one run, with no chat, no
session, no model choice, no sources tab and no budget. On 30 August the jury does not read
`graph.py`; they watch this screen.

---

## What is actually wrong

Five things, verified in source.

### 1. The live wire is `StreamEvent`, not AG-UI

There are two streaming primitives in this repo and only one serves the console.

```python
# backend/src/app/api/schemas.py:448
StreamEvent = Annotated[
    RunStarted | NodeStarted | NodeFinished | Reasoning | Guardrail | ...
    Field(discriminator="type"),
]
```

That union is mirrored by hand in `web/src/lib/stream.ts` and is what `POST /query`
(`routes.py:894`) streams.

The other one is `AegisEmitter` (`aegis/src/aegis/core/stream.py:97`) with its name table in
`aegis/src/aegis/core/stream_names.py`, mirrored in `web/src/lib/streamNames.ts`. Grep both:

- `AegisEmitter` is **constructed** in exactly one production place —
  `routes.py:801`, inside `GET /stream/guardrail-demo`. Everywhere else it is a type
  annotation on an optional `emitter` parameter (`guardrails/pipeline.py`, `memory/stream.py`,
  `voice/stream.py`, `ml/stream.py`, `evals/stream.py`) that is `None` on every production
  call path.
- `web/src/lib/streamNames.ts` is **imported by nothing in `web/src`**. Its only reader is
  `backend/tests/api/test_stream_name_mirror.py`.

So the repo has a mirror test guarding the protocol nobody uses, and no mirror test on the
protocol that serves the product. All console work in this phase goes on `StreamEvent`.

### 2. Three real events are on the wire and thrown away; one dead event is wired

| Event | Backend emits | In the TS union | Handled |
|---|---|---|---|
| `reflection` | yes — `schemas.py:392` | **no** | no |
| `routing` | yes — `schemas.py:417` | **no** | no |
| `memory` | yes — `schemas.py:436` | **no** | no |
| `abstained` | **no — not in the Python union** | yes — `stream.ts:280` | yes — `runReducer.ts:302` |

`runReducer.ts:335` ends `default: return next`, so the three real events land in
`state.events` and are silently discarded. The self-repair loop, the supervisor hand-off and
every memory recall are **already happening and already on the wire, invisible.**

Meanwhile the reducer carries a whole `'abstained'` phase (`runReducer.ts:37`, `128`, `174`,
`302`, `316`) that can never be reached, because no Python variant emits it.

This is free demo value, and it is also proof that additive protocol changes are safe: an
unknown `type` cannot break the client.

### 3. The console never sends `session_id`, so memory is dark in the live product

```ts
// web/src/lib/api/liveTransport.ts:31
body: JSON.stringify({ query, persona }),
```

The backend supports sessions completely. `QueryRequest.session_id` exists
(`schemas.py:595`) and `routes.py:930` threads it into `run_agent`. Both memory nodes then
gate on it:

```python
# aegis/src/aegis/agent/graph.py:648  (recall_memory)
# aegis/src/aegis/agent/graph.py:693  (persist_memory)
if deps.memory is None or state.get("session_id") is None:
    return {}
```

**Every live run today recalls nothing and persists nothing.** Nobody should claim multi-turn
memory on stage until this is fixed.

One honest correction to the plan that flagged this: the fetch body is one line, but the
plumbing is not. `RunTransport.start` takes `(query, persona, token, handlers)`
(`web/src/lib/api/transport.ts`), and so do `useRunStream.start` (`useRunStream.ts:49`), the
mock transport, and the console call site. Five files, not one. Still the largest visible
payoff per hour in this phase.

### 4. There is no chat, and nowhere to keep one

`MoneyShotConsole.tsx` is 233 lines of a three-column bento over exactly one run.
`useRunStream` holds exactly one `RunState`. There is no turn list and no session rail.

There is also no storage. `backend/src/app/data/models.py` declares two tables — `approvals`
and `chunks`. `memory_session` exists (`aegis/src/aegis/memory/stores.py:68`) but it is the
memory subsystem's own thread record: id, subject, persona, turn count, rolling summary. Use
it as the *session identity*; do not overload it as the chat transcript store.

### 5. Four endpoints the console needs do not exist

Grep every `@router` in `routes.py`: there is no `/models`, no `/sessions`, no
`/attachments`, no `/me/budget`. The only budget reads are `/admin/budgets`
(`routes.py:1355`) and `/governance/dashboard` (`routes.py:2545`), both behind
`require_tenant_admin` — **a `client`-role user cannot see their own budget anywhere.**

What already exists and should be reused rather than rebuilt:

- `routing_table()` (`aegis/src/aegis/gateway/routing.py:47`) already returns the effective
  role → deployment map, with `unit_cost` beside it. `GET /models` is a projection, not a
  subsystem.
- Image upload is already built: `POST /vision/analyse` (`routes.py:2787`), `analyseImage`
  (`web/src/lib/api/client.ts:724`), and `web/src/components/vision/ImageDropzone.tsx`.
- `set_governance_context` is already bound per request inside the `/query` streaming task
  (`routes.py`, in the `event_source` closure). That contextvar is the seam for a per-run
  model override with zero signature churn through the graph.

---

## What we are fixing now, and what waits

| | |
|---|---|
| **Now** | The three dropped events render; the dead one is deleted. |
| **Now** | `session_id` is sent, chat sessions live in Postgres, and memory is visibly alive. |
| **Now** | A chat shell: clean before a query, live agent logs and RAG/graph activity during, tabs after. |
| **Now** | Model selection, image upload, a measured budget pill. |
| **Waits** | The durable `run_events` log, replay, and the harness rebuilt as a projection over it. |
| **Waits** | Skills authoring UI, per-tenant LLMOps surfaces, the MCP admin console, a cancel endpoint. |
| **Waits** | Persisting model defaults per tenant and per user — that needs the `tenant_settings` table. |

The split is about what the demo needs to *show*. Replay and the harness are the right
architecture and they are in [`backlog-post-hackathon.md`](backlog-post-hackathon.md).

---

## Tasks

### 6.1 — Repair the protocol drift (0.25d)

The cheapest work in the whole plan.

- Add `Reflection`, `RoutingEvent` and `MemoryEvent` to the union in `web/src/lib/stream.ts`
  and give each a branch in `runReducer.ts`. The self-repair loop and the routing decision go
  in the trace; memory recall gets its own visible line.
- Delete `Abstained` from `stream.ts:280`, its branch at `runReducer.ts:302`, and the
  `'abstained'` phase at `runReducer.ts:37`, `128`, `174`, `316`.
- Add a parity test for the **`StreamEvent` union** modelled on
  `backend/tests/api/test_stream_name_mirror.py` — parse `stream.ts`, diff against the Python
  variants, fail on drift. The existing test guards `streamNames.ts`, which nothing imports.

### 6.2 — Chat sessions in Postgres, and actually send the id (0.5d)

```
chat_sessions  (id uuid pk, tenant_id, user_id, title, created_at, last_active_at)
chat_messages  (session_id fk, turn_index, role, content, run_id, created_at)
```

Both carry `tenant_id` and get the RLS policy from Phase 1 — new tenant-scoped tables added
after Phase 1 must not silently arrive without one.

- `GET/POST/PATCH/DELETE /sessions`, `GET /sessions/{id}/messages`.
- Thread the id: `RunTransport.start` → `liveTransport.ts:31` body → the mock transport →
  `useRunStream.start` → the console call site.
- Use the same id as `memory_session.id` so the transcript and the recall agree on what a
  conversation is.

**Verify this the honest way: ask a second question in the same chat and watch a `memory`
event arrive on screen.** That event is only visible because of 5.1. If it does not arrive,
memory is still dark and you have not finished this task.

### 6.3 — The chat shell and the live run surface (0.75d)

Clean before a query. Alive during it. Structured after it.

- New `web/src/components/console/ChatConsole.tsx` — session rail | thread | composer.
  `MoneyShotConsole` is retired **as a page**, not deleted: `DecisionStrip`, `AnswerPanel`,
  `RerankScoreboard`, `GuardrailReveal`, `NodeGantt` and `ConfidenceCard` all survive as tab
  contents in 5.4. Re-home them; do not rewrite them.
- One turn owns one `RunState`. Add a thread reducer over turns; `runReducer`'s own shape does
  not change.
- **The agent panel.** One card per agent, allocated when Phase 4 announces its plan and never
  reflowed — reflowing cards are unreadable on a projector. Each card shows a status word and
  one current-action line, not a scrolling wall. Tool calls render as discrete chips
  (`web_search("…") → 5 results · 820 ms`), collapsed by default. Exactly one card expanded
  and streaming; the rest collapsed to two lines. A finished card dims and gains a duration
  and cost badge; a failed one gets a *designed* terminal state, not a stuck spinner.
- **The activity rail.** Retrieval, graph and guardrail events that carry no agent identity —
  the supervisor-level lane.
- **An honest empty state.** No placeholder cards, no sample results, no invented domain
  copy. Keep the `OfflineBanner` (`ConsoleMount.tsx:33`) — a mock run must stay labelled as a
  mock run.

**This task depends on Phase 4 stamping an agent identity on its events.** If it does not, the
cards have nothing to group by and this degrades to a single-lane log. Confirm the event shape
with Phase 4 before starting 5.3, not during it.

### 6.4 — Result tabs (0.5d)

The rule, from the v2 doc: the main tab carries what a user actually wants. Anything they
would only ask for on purpose gets its own tab.

| Tab | Contents |
|---|---|
| **Answer** *(default)* | The answer, inline citations, the **sources list** with rerank scores and provenance, and a one-line trust summary — guardrails passed · confidence · cost. |
| **Agents** | Every agent's full log and tool calls. |
| **Retrieval & graph** | `RerankScoreboard`, the graph delta, provenance, cache lineage. |
| **Guardrails** | `GuardrailReveal` and every rail verdict for this run. |
| **Trace & cost** | `NodeGantt`, the per-node and per-agent token/cost breakdown, the trace id. |

If a panel has no real data for a run, the tab says so. It does not render an empty chart.

### 6.5 — The composer: mode, model, tools (1.0d)

Replaces the old "model selection" task, which was one axis of three.

Grok, ChatGPT and Claude have all converged on the same shape, and it is worth copying because
it is the shape users already understand:

| Product | What it ships | The lesson |
|---|---|---|
| **Grok** | One switch in the input bar: **Auto · Fast · Expert · Heavy** — its own descriptions are "Chooses Fast or Expert", "Quick responses", "Thinks hard", "Team of experts" | The mode names a **strategy**, not a model. "Heavy = team of experts" is our `TEAM`. |
| **ChatGPT** | Auto router with **Instant · Thinking · Pro** | **Automatic escalation does not consume the quota; manually picking it does.** |
| **Claude** | Model, effort and extended-thinking are three independent settings; tools are a separate surface | Depth, model and tools are **orthogonal**. Do not fuse them. |

```
┌─ composer ───────────────────────────────────────────────────────────┐
│  [ Mode: Auto ▾ ]   [ Model: Aegis default ▾ ]   [ Tools: 6 of 9 ▾ ]  │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  Ask anything…                                                 │  │
│  └────────────────────────────────────────────────────────────────┘  │
│  ◷ $2.14 of $50 today                                    [ Send ]    │
└──────────────────────────────────────────────────────────────────────┘
```

**The five modes**, named for what they do to *this* system rather than copied from anyone's
marketing:

| Mode | What it does | Maps to |
|---|---|---|
| **Auto** *(default)* | Phase 5's classifier decides `SINGLE` vs `TEAM` and the fan-out width | `RouterDecision` unchanged |
| **Fast** | Force `SINGLE`, skip the classifier's model tiebreak, pin the cheap deployment, disable the agentic-retrieval loop | `depth=SINGLE`, `agentic_retrieval_enabled=False` |
| **Deep** | Force `SINGLE` with the full retrieval loop and the larger model — "think harder in one lane" | `depth=SINGLE`, `agentic_retrieval_max_rounds` at ceiling |
| **Team** | Force `TEAM` at the platform-capped width. The user is explicitly buying a fan-out | `depth=TEAM`, `fanout=cap` |
| **Custom** | Reveals the pinning panel: choose agents from the roster, tools, and width | `depth=TEAM` with an explicit roster |

**Why five and not three:** Fast and Deep both force `SINGLE` and differ only in retrieval depth
and model tier — but that is the entire "quick answer vs think hard" distinction every
comparable product ships, and `AgentConfig` **already has both knobs**
(`agentic_retrieval_enabled`, `agentic_retrieval_max_rounds`). Collapsing them throws away a
control the code already has.

**Precedence — manual wins, and the screen says who decided:**

```
effective_depth = user_mode if user_mode != AUTO else classifier_decision
```

Phase 5 puts `decided_by` on the `routing` event. This phase renders it, so the trace always
names whether the user or the classifier chose.

**The budget rule worth fighting for:** auto-escalation is free; **manual escalation is charged
and pre-flighted.** Before a `Team` run the composer shows the estimated cost and the remaining
balance. This is the asymmetry ChatGPT ships, and with ~650 total fan-out queries on $100 it is
the only thing between a Team button and a burned balance.

**Persistence is the settings catalogue** (Phase 3 §3.7), not a bespoke store:
`agent.mode`, `agent.model`, `agent.tools` resolve platform → tenant → user. `resolve()` returns
`(value, source)` so the control can show *"tenant default"* next to the value.

**Tool pinning uses the same `is_allowed` intersection Phase 5 already specifies** —
`platform ∩ persona ∩ tenant ∩ user`. One intersection, not two implementations.

**Do not enforce the caps in the dropdown alone.** The allowed model set and the fan-out cap are
exactly the controls that get "enforced" by a UI control and bypassed by a `curl`. They are
server-side checks; the dropdown only reflects them.

### 6.6 — Image upload (0.25d)

Almost entirely reuse. Mount `ImageDropzone.tsx` in the composer, post through `analyseImage`
(`client.ts:724`) to `POST /vision/analyse` (`routes.py:2787`), and attach the result to the
run. The vision screen already runs as an injection rail — surface its verdict as a guardrail
chip in the turn, before the answer, so the rail is visibly doing work.

No new storage. The attachment lives for the run. A durable `attachments` table is backlog.

### 6.7 — The budget indicator (0.25d)

- `GET /me/budget` returning the caller's own effective caps, spend and remaining, read from
  the same `BudgetStatusRow` the enforcer reads — so the pill and the enforcer cannot disagree.
- A compact pill in the composer that ticks live off the cost deltas already carried on
  `node_finished`.

If a figure is not measured, do not draw it. An empty pill that says "not yet measured" is
worth more than a plausible number, and the jury rewards exactly that distinction.

### 6.8 — The mascot (0.25d) — cut this first

Inline SVG. `pointermove` on `window`, pupil offset clamped to the iris radius, rAF-throttled,
listener detached when off-screen, `prefers-reduced-motion` respected. Idle blink on a timer.
Four expressions driven by `RunState.phase`: idle → thinking → working → done/blocked.

Stated plainly: **this is charm, it is cheap, and it is the first thing to cut.** If the agent
panel in 5.3 or the tabs in 5.4 are not done on the morning of the third day, the mascot does
not get built. A console with a blinking chatbot and no sources tab scores worse than the
reverse, and it takes longer to explain.

---

## Definition of done

- [ ] `reflection`, `routing` and `memory` render in the console; `abstained` is gone from
      the TS union and the reducer.
- [ ] A parity test fails if the Python `StreamEvent` union and `web/src/lib/stream.ts` drift.
- [ ] A second turn in the same chat visibly recalls the first — evidenced by a `memory`
      event on screen, not by a claim.
- [ ] Chat sessions persist across a reload and are scoped to the tenant.
- [ ] The console is empty before a query: no placeholder cards, no sample results.
- [ ] Each agent in a run has its own card, its own log and its own tool-call chips.
- [ ] The Answer tab carries the answer, citations and sources; everything else is one tab away.
- [ ] The composer shows the effective model per role and where that choice came from.
- [ ] The budget pill reads the same rows the budget enforcer reads.
- [ ] `pytest` green; the web build and web tests green.

## Demo at the end of this phase

One screen. An empty console and a cursor the mascot is watching. Type a question; agent cards
appear and fill with their own reasoning and tool-call chips in real time; the RAG and graph
activity runs beside them; the answer resolves into tabs with sources first and the budget
ticks down. Ask a follow-up that only makes sense if the first turn was remembered — and watch
the memory event that proves it was.

## Risks

**Three days is not the honest number for this scope.** The deep research costs this console
at 10–13 days. What makes 3 conceivable is everything that is *not* in it: no harness rebuild,
no replay, no skills UI, no LLMOps surfaces, no MCP console. What remains is a chat shell, a
tab set and three composer controls, and the reducer change is small because agent identity
lives on the base event. It is still the tightest phase in the plan. The cut order **inside**
the phase is 5.8, then 5.6, then 5.5.

**5.3 is blocked on Phase 4's event shape.** Agree the agent-identity field with Phase 4
before day one of this phase. Discovering on day two that events are unattributed costs the
whole agent panel.

**Retiring `MoneyShotConsole` risks losing panels that already work.** Six components in it
are good and measured. Re-home them as tab contents; resist rewriting them while you are
already short on time.

**`chat_sessions` and `chat_messages` are new tables and there is no migration tool.**
`backend/pyproject.toml:36` documents the deliberate absence of Alembic; schema comes from
`create_all` plus an additive reconciler. New tables are fine on a fresh database — verify on
the actual long-lived dev database too, not only on a clean one.

**A budget number that is not measured is worse than no budget number.** The pill must read
the enforcer's rows. If it drifts, someone on the jury will find the gap by asking twice.
