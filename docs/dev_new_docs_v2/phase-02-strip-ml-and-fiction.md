# Phase 2 — Strip ML from the graph, and strip the fiction

**1 day. Do it before Phase 4, not after.**

Phase 4 makes the graph concurrent. Every dead node still in the graph on that day is a
node you have to reason about while doing the hardest work in the plan. This phase is
cheap, it is mechanical, and it is the only reason it sits this early.

Two separate jobs share the day because both are deletions:

1. ML comes out of the **agent pipeline**.
2. The invented demo domain comes out of the **copy**.

---

## The distinction that must not be lost

> **`aegis.ml` the module stays. All of it.**

The user's instruction was *"remove ml from our agentic pipeline — ml is just for tenant use
case in hackathon"*. That is a statement about the graph, not about the codebase.

| Stays, untouched | Goes |
|---|---|
| `aegis/src/aegis/ml/**` and `backend/src/app/ml/**` — the whole ensemble spine, conformal intervals, SHAP | The `ml_predict` node in the agent graph |
| `POST /ml/explain` (`routes.py:986`) and `GET /ml/model-card` (`routes.py:2413`) | The `ml_explanation` event on the run stream |
| `backend/src/app/adapter/ml_spec.py` — reframed as *the tenant's ML use case*, not *the agent's predict step* | `AgentConfig.run_ml` and the three ML deps on `AgentDeps` |
| The admin forecast dashboard, which the v2 brief wants to grow **more** ML — SHAP charts and interactive feature selection | The ML injection into the planner and generate prompts |

Anyone who reads "remove ML" and deletes `aegis/ml` has broken the forecast dashboard and
the hackathon use case in one commit. Say it out loud in the PR description.

**Also rewrite the pitch sentence.** `docs/hackathon/brief.md` §4 narrates the trust stack
as *conformal → human gate → SHAP → guardrails → traces*, which implies ML feeds the gate.
It never did. The gate is driven by `ToolSpec.risk` alone (`adapter/tools.py:433` is the one
`RiskLevel.HIGH`), and `graph.py:835`'s own docstring says the ML event carries "**no gating
semantics**". Removing ML from the graph therefore breaks **zero** behaviour — but if the
narrative still claims otherwise, a judge asks a question you cannot answer.

---

## What is actually wrong

### 1. The graph runs a node that changes nothing

```python
# aegis/src/aegis/agent/graph.py:713
async def ml_predict(state: AgentState) -> dict[str, Any]:
```

Wired at `graph.py:1151-1153`, sitting on the hot path at `graph.py:1195-1196`:

```python
builder.add_edge("retrieve", "ml_predict")
builder.add_edge("ml_predict", "plan")
```

Its output goes two places — a string appended to the planner prompt (`graph.py:780-782`)
and to the generate prompt (`graph.py:985-986`) — and one event emitted from `gate`
(`graph.py:848-851`). None of it routes, gates, or decides anything. It is a step in the
money-shot trace that the money shot does not use.

### 2. The removal is wider than plan 01 measured

Plan 01 named 16 sites. Verified against source, there are **four more it missed**, and one
of them is a cross-repo snapshot test that will fail the build:

| Missed site | What it is |
|---|---|
| `web/src/config/graphTopology.json:40,141,172` | The console's offline topology snapshot — a node and two edges |
| `backend/tests/api/test_agent_topology.py:36,91,93` | Asserts `ml_predict` is in `REAL_NODE_IDS`, that its only edge target is `plan`, **and** that the JSON snapshot equals the served topology (`:100`) |
| `web/src/components/console/orchestration.ts:64,93,94` | A `NODE_PRESENTATION` entry plus two `NODE_ALIASES` (`ml`, `score`) |
| `backend/src/app/agent/graph.py:3`, `aegis/src/aegis/agent/graph.py:4,22-27` | Module docstrings that draw the trace with `ml_predict` in it |

And one site plan 01 did not consider at all:

```python
# aegis/src/aegis/agent/orchestrator.py:257
ml_snapshot=_ml_snapshot(graph, config),
```

`approvals.ml_snapshot` is a **Postgres column** (`backend/src/app/data/models.py:128`),
surfaced on the API (`schemas.py:702`) and read by the console
(`ApprovalQueueCard.tsx:55`). We have no migration tool (deferred by the master plan), so
**keep the column and stop populating it.** The card already guards on
`confidence != null`, so an empty snapshot renders cleanly — only the mock fixtures
(`web/src/mock/fixtures.ts:203,221,239`) need cleaning.

### 3. There is no refund domain to delete

The adapter's real tools, verified at `backend/src/app/adapter/tools.py:426-447`:

| Tool | Risk |
|---|---|
| `update_request_status` | HIGH |
| `assign_request` | MEDIUM |
| `add_case_note` | LOW |

It is a service-request / case-management world. "Refund" is **example prose**, and the
distribution matters — measured, not recalled:

| Where | Files | Occurrences |
|---|---|---|
| `aegis/src/aegis/evals/corpus.py` | 1 | 13 (an eval gold-corpus fixture) |
| Other production Python | 9 | 15 |
| `web/src/mock/**` | 6 | 79 |
| `web/src/components/**` | 8 | 28 |
| `docs/**` | 45 | prose |
| `aegis/tests` + `backend/tests` | 47 | string literals in assertions |

**Two corrections to the brief.**

*First:* the risk-map "refund" mentions are **not** in client-facing copy. They are a module
docstring (`backend/src/app/platform/risk_map.py:13`), one code comment (`:54`), and a
Pydantic class docstring (`backend/src/app/api/schemas.py:1218`). No `RiskEntry.mitigation`
string contains the word. They still want rewording — a judge reading the repo reads
docstrings — but nothing rendered changes.

*Second:* the fiction is **not confined to `web/src/mock/`**. Eight real components carry it,
and two of them are worse than mock data because they look like product:

- `web/src/components/harness/HarnessView.tsx:184,188` shows a tool named **`issue_refund`**
  at `risk: 'high'`. That tool does not exist. A judge who greps for it finds nothing.
- `web/src/components/ops/opsShared.ts:16-43` embeds five versions of a
  `payments_ops_agent` system prompt with a `$2,000 refund ceiling`. There is no such
  ceiling in `adapter/tools.py`, and the real prompt keys are `operations_lead` and `client`
  (`adapter/personas.py:84,93`). The file is honestly badged "sample" — but it is sample
  content for a product that does not exist.

**The human gate is a real platform capability and it stays exactly as it is.** `RiskLevel`
on a `ToolSpec` → `gate` node → durable `approvals` row → inbox. Nothing in that chain
references any domain. It is the strongest thing on the demo and it was never the fiction.

---

## What we are fixing now, and what waits

| | |
|---|---|
| **Now** | `ml_predict` out of the graph, the event off the wire, the config knob and deps gone, the topology snapshot regenerated. |
| **Now** | The fake tool name, the fake system prompts, and the fake `$4,200 / A-771` story out of anything a judge can see. |
| **Now** | The docstrings that draw a trace with an ML step in it. |
| **Waits** | The `approvals.ml_snapshot` column — needs a migration, and Alembic is deferred. |
| **Waits** | Retiring `adapter/corpus/kb_refund_process.md` and `adapter/skills/handling_refunds.md`. They are real, working corpus and skill assets. Replacing them means writing replacements, and Phase 3 gives us a better way to get corpus in. |
| **Waits** | Generating the web mock fixtures from `adapter/generator.py` at build time (plan 01 §Phase 7's recommendation). Right idea, not a 1-day idea. |
| **Waits** | The `DomainAdapter` protocol and a second swappable adapter. Excellent, and not load-bearing for 30 August. |

Leave `backend/src/app/adapter/generator.py:548,697` alone. `Category.BILLING` producing
"invoices, refunds, double charges" and a case titled "Refund not received" is *legitimate
case-management content*, not fiction. A billing complaint is a real category in a real
support desk.

---

## Tasks

### 2.1 — Cut the node and its wiring (0.25d)

In `aegis/src/aegis/agent/graph.py`:

- Delete the `ml_predict` body, `:713-733`.
- Delete `add_node("ml_predict", …)`, `:1151-1153`.
- Replace the two edges at `:1195-1196` with a single `builder.add_edge("retrieve", "plan")`,
  and delete the `# Predict-then-plan` comment above them.
- Delete `"ml_predict"` from `NODE_LABELS`, `:93`.
- Delete the planner injection `:780-782` and the generate injection `:985-986`.
- In `gate`: delete `:839` (`resp = state.get("ml_response")`), `:847-851` (the `ml_event`
  emission) and the `"ml"` key at `:853`. Rewrite the docstring at `:830-836` — the sentence
  "ML never gates" was true and is now vacuous; say the gate is driven by tool risk, full
  stop.
- Rewrite the module docstring: `:4` (the trace line) and `:22-27` (the whole "ML is a
  solution signal" paragraph). Same for `backend/src/app/agent/graph.py:3`.

### 2.2 — Cut the contract, the config and the state (0.15d)

- `aegis/src/aegis/agent/deps.py:231,236,237` — drop `predict_explain`, `features_for`,
  `describe_prediction` from `AgentDeps`.
- `aegis/src/aegis/agent/deps.py:104,125,153` — drop `AgentConfig.run_ml`.
- `aegis/src/aegis/agent/state.py:81-84,130-132` — drop `ml`, `ml_response`, `ml_summary`.
- `aegis/src/aegis/agent/topology.py:87,92,93` — drop the three `_unreachable` deps.
- `aegis/src/aegis/agent/harness.py:63` — drop the `run_ml` `_KnobSpec`; `:305` — drop the
  `ml_explanation` lookup and `:388`'s `"ml"` key.
- `backend/src/app/agent/deps.py:323,343,348,349` — drop the wiring; delete
  `_default_describe_prediction` (`:453-457`) and `_default_features_for` (`:521-534`); fix
  the docstring at `:397-398`.
- `aegis/src/aegis/agent/orchestrator.py:257,359-364` — drop `_ml_snapshot` and its call
  site. Pass `{}` to keep the approvals row shape.

`aegis/tests/agent/test_harness_config.py:44,55` asserts a bijection between `AgentConfig`
fields and `_KnobSpec` entries in **both** directions. Remove the field and the spec in the
same commit or the suite goes red either way.

### 2.3 — Take the event off the wire and out of the console (0.2d)

- `aegis/src/aegis/agent/events.py:124-140` — delete the `ml_explanation` builder.
- `backend/src/app/agent/events.py:27,52` — drop the re-export.
- `backend/src/app/api/schemas.py:245` — remove the `ml_explanation` variant from the
  `StreamEvent` union. The `autonomy_band` / `min_confidence` fields at `:266,271` go with
  it.
- `web/src/lib/stream.ts:182`, `state/runReducer.ts:275`, `config/signals.ts:59`,
  `components/trace/describeEvent.tsx:107`, `components/console/orchestration.ts:64,93,94`
  — remove the event and the node presentation.
- `web/src/mock/mockTransport.ts:235,528,680` and `web/src/mock/platform.ts:193` — remove
  the emitted event and the `run_ml` knob.

An unknown event `type` is already tolerated by `runReducer.ts`'s
`default: return next`, so the frontend cut can land after the backend cut without a broken
window in between.

### 2.4 — Regenerate the topology snapshot and fix the tests (0.2d)

This is the step that fails the build if skipped.

- Regenerate `web/src/config/graphTopology.json` from `aegis.agent.graph_topology()`.
  `backend/tests/api/test_agent_topology.py:100` asserts the file equals the served
  topology.
- `backend/tests/api/test_agent_topology.py:36,91,93` — drop `ml_predict` from
  `REAL_NODE_IDS`; the `retrieve → plan` edge assertion replaces the `ml_predict → plan`
  one.
- Delete `backend/tests/integration/test_ml_nonblocking.py`,
  `aegis/tests/agent/test_ml_solution_signal.py`,
  `backend/tests/agent/test_ml_solution_signal.py`,
  `backend/tests/agent/test_deps_features.py`.
- Fix the ML references in `aegis/tests/agent/conftest.py`, `backend/tests/conftest.py`,
  `test_telemetry.py`, `test_router.py`, `test_orchestrator.py`,
  `test_self_repair_loop.py`, `test_p0_autonomy_config.py`,
  `backend/tests/integration/test_query_stream.py`.
- Everything under `aegis/tests/ml/` and `backend/tests/ml/` **stays green and untouched**.
  If any of those go red, you deleted too much.

Note while you are here: `aegis/src/aegis/ml/stream.py::stream_predict_explain` has no
caller anywhere in the repo. It is dead, but it is dead *inside `aegis.ml`*, which stays.
Leave it; log it in the backlog.

### 2.5 — Delete the fiction that a judge can see (0.2d)

Ordered by how badly it reads on stage.

1. **`web/src/components/harness/HarnessView.tsx:169-188`** — the offline sample trace names
   a tool `issue_refund` that does not exist and an `ml` node that no longer exists. Rebuild
   it from the three real tools. This is the single worst item: it invites a grep that
   finds nothing.
2. **`web/src/components/ops/opsShared.ts:7-43`** — retarget `PROMPT_KEY` from
   `payments_ops_agent` to the real `operations_lead`, and rewrite the five `SAMPLE_BODIES`
   against the real tool names and the real risk tiers. Keep the "sample" badge; it is the
   honest part and it stays honest.
3. **`web/src/config/personas.ts:45`** — "Should we auto-approve the refund of $4,200 on
   account A-771?" is a sample query for a tool we do not have. Replace with a
   `update_request_status` question, which is genuinely HIGH-risk and genuinely gates.
4. **`web/src/components/sim/{SimulationView,simLogic}.tsx`** — "Refund executed / denied /
   proposed" labels. The lane logic is real and derives from tool results; only the labels
   are fiction. Reword to the gated action's own name.
5. **`web/src/components/memory/RecallDebugPanel.tsx:122,159`**,
   **`web/src/components/guardrail/GuardrailsView.tsx:202-203`**,
   **`web/src/components/ml/MLOpsView.tsx:28`** — placeholder strings and a
   `prior_refunds_90d` SHAP feature name. One-line each.
6. **`web/src/mock/**`** — 79 occurrences across six files. Reword; do not restructure. The
   generate-from-adapter idea is right and it is not today.
7. **The docstrings** — `risk_map.py:13,54`, `api/schemas.py:1218`,
   `aegis/agent/graph.py:1111`, `aegis/guardrails/schema.py:21`,
   `aegis/retrieval/chunker.py:142,334`. Each is one sentence of analogy. Reword to the
   case-management world; the chunker's "Refunds vs Returns" example is genuinely good
   pedagogy and only needs different nouns.

Leave `aegis/src/aegis/evals/corpus.py` alone for now. Its 13 occurrences are a *gold
retrieval corpus with pinned relevance judgements* — the eval's whole point is that the
query/document pairing is fixed. Changing the nouns means re-deriving the gold set, which is
Phase 3 work, not copy work.

---

## Definition of done

- [ ] `grep -rn "ml_predict\|ml_explanation\|run_ml" aegis/src backend/src web/src` returns
      hits **only** under `aegis/src/aegis/ml/` and `backend/src/app/ml/`.
- [ ] `GET /agent/topology` has 14 nodes, and `retrieve`'s only edge target is `plan`.
- [ ] `web/src/config/graphTopology.json` is regenerated and
      `test_web_offline_snapshot_matches_the_real_topology` passes.
- [ ] `POST /ml/explain` and `GET /ml/model-card` still work; every test under
      `aegis/tests/ml/` and `backend/tests/ml/` is still green.
- [ ] No production surface names a tool that is not in `adapter/tools.py`.
- [ ] `pytest` green: 668 backend / 1217 aegis, minus the deleted ML-in-graph tests.
- [ ] `docs/hackathon/brief.md` §4 no longer implies ML feeds the human gate.

## Demo at the end of this phase

Run one query. The trace goes `guard_input → route → retrieve → plan → gate → act →
reflect → generate → guard_output → stream` with no dead step in it, and every noun on the
screen names something that exists in `adapter/tools.py`.

Then open the forecast page and show SHAP there. Same models, honest home. "We took ML out
of the agent because it was decorating a decision it never made — here is where it actually
decides something" is a better answer than most teams will have.

## Risks

**Someone deletes `aegis/ml`.** The whole reason the distinction is written twice in this
document. Guard it in review.

**The topology snapshot bites at the end of the day.** It is a JSON file in `web/` asserted
by a test in `backend/`. It is easy to forget and it fails loudly. Do task 2.4 before 2.5,
not after.

**The fiction rewrite creeps.** Rewording 107 occurrences across 14 web files is a
half-day if you reword and a two-day job if you start restructuring. This phase is a
find-and-replace with judgement, not a refactor. If you find yourself editing component
structure, stop.

**`opsShared.ts` may be feeding a real diff view.** It backs the LLMOps prompt-diff for
versions the API does not expose a body for. Changing `PROMPT_KEY` changes which prompt the
loop dashboard tracks — check `GET /ops/prompts/{prompt_key}` (`routes.py:2040`) still
returns rows for the new key, or the page goes empty and that is worse than fiction.
