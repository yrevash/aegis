# HARNESS + LLM-OPS PLAN — embody (and later show) both reference architectures

Goal: make TAIF S2 genuinely **have** two reference architectures — the *Production
Principles* building blocks and the *Harness + LLM-Ops closed loop* (memory taxonomy +
consolidation, and the trace→eval→diagnose→release→**improved-prompt-fed-back** loop) —
then **show both clearly** in a later frontend pass. Core work first.

Guiding rule: **all new machinery is domain-agnostic core (`app/*`); every place where
"what counts as a fact / skill / durable action / good answer" is domain-specific is
read through a thin adapter contract.** On the day, only `app/adapter/*` changes.

**Scope (decided): build ALL of it — maximal SOTA, nothing cut as "over-engineering,"
including the multi-agent router.** Self-improvement is **hybrid** (autonomous optimizer +
eval gate + tiered human approval; autonomous where low-risk, human-gated where impactful;
always reversible). Every capability must ladder up to the **management value story the
frontend will later show: money made · money saved · top security · top performance at
least cost · audit trails.** Each feature below notes the metric it feeds:
- memory + cheap-model consolidation + routing-to-cheap → **money saved** (fewer/cheaper
  tokens) + **performance at least cost**;
- self-improving prompt + per-step evals → **performance / quality** (and cost per solved
  task → **money made/saved**);
- guardrails + approvals + RLS → **top security**;
- versioned prompts + memory write-back + audit log + trace tree → **audit trails**.

---

## 1. Coverage today (grounded, file:line-verified)

**Already reference-matching — keep, later just surface:** model routing (`core/models.py`
`ModelRole`), tool contracts + risk tiers + allowlist (`adapter/tools.py`), LangGraph
orchestration + bounded Reflexion self-repair loop (`agent/graph.py`), HITL approvals +
RBAC/budgets/RLS (`data/`, `api/routes.py`), hybrid RAG + RRF + rerank + spotlighting
(`retrieval/*`), real OTel span tree + audit (`observability/*`, `AuditLog`), reliability
fallbacks, cost/semantic-cache/streaming.

**Confirmed gaps (ranked by distance from the docs):**
1. 🔴 **Entire memory subsystem absent** — no procedural / semantic / episodic memory, no
   write-back, no consolidation. Only static persona prompts + knowledge-RAG.
2. 🔴 **No multi-turn chat history** — `/query` takes only `query`+`persona`; `run_agent`
   seeds `messages: []`. Zero cross-turn continuity.
3. 🔴 **Open loop / no self-improvement** — `EvalResult` table defined but never written;
   nothing feeds an improved prompt/config back into the harness.
4. **MCP absent** — tools are already MCP-shaped but no MCP server/client exists.
5. **Evals retrieval-centric** — no per-step grading, no error/edge scenarios, no diagnose.
6. **No agent-to-agent routing** (specialized LLM callers exist, no router).
7. **Call-safety hardening** — `json_object` only (no schema-enforced output), no LLM-call
   timeout, no re-ask on bad JSON, no per-call `max_tokens`.

---

## 2. Design per gap (domain-agnostic core + adapter seam)

### GAP 1 — Memory taxonomy + multi-turn working memory  *(Effort L · Demo High)*
First-class **session** + three long-term stores, assembled into an ephemeral **working
memory** per run and written back after each reply.

| Tier | Store | Notes |
|---|---|---|
| Working (ephemeral) | `AgentState` + rolling window in **Redis** (`session_id`, TTL) | bounded, degrades to in-mem |
| Episodic (past chats) | **Postgres `chat_message`** (dated, ordered) **+ pgvector** embedding | "SQL + vector" |
| Semantic (durable facts / user profile) | **pgvector `semantic_fact`** (tenant+user scoped) | similarity recall |
| Procedural (how to act) | **files** `app/adapter/skills/*.md` (optionally embedded) | domain behaviour → adapter-owned |

Neo4j/LightRAG stays as domain **knowledge** only — memory is separate.

**LangGraph nodes:** `assemble_working_memory` (after `guard_input`) builds `messages`
from active system prompt (from the Ops registry, Gap 2, adapter default as floor) +
selected skills + recalled semantic facts + rolling chat window — replacing `messages:[]`.
`persist_memory` (after `stream`) writes turns to episodic + updates the Redis window.
**Consolidation** (`app/memory/consolidate.py`): after N turns a cheap Summarizer
(`ModelRole.CHEAP`) distills episodic → `semantic_fact` and truncates the window (keeps
working memory lean). Fire-and-forget from `persist_memory`; CLI (`python -m
app.memory.consolidate`) as the manual/offline path.

**Context-bloat control:** hard token cap on the window; last-K-turns + running summary;
semantic-fact top-k cap; skills selected not all-injected — `MemoryConfig` knobs (Ops-tunable).

**Files:** NEW `app/memory/{__init__,stores,working,consolidate}.py`; tables `ChatSession`,
`ChatMessage`, `SemanticFact` in `data/models.py` (reuse RLS pattern); NEW adapter piece
`app/adapter/memory_spec.py` (`fact_extraction_prompt`, `durable_fact_schema`, `skills_dir`,
`select_skills`) + `app/adapter/skills/*.md`. EDIT `agent/{state,graph,deps,orchestrator}.py`,
`api/schemas.py` (+`session_id`), `api/routes.py`.

### GAP 2 — LLM-Ops closed loop with a *safe*, self-improving system prompt  *(Effort M · Demo High — centerpiece)*
Close **Reply→Trace→Eval→Observe→Diagnose→Gate→Release→back into Harness**.

**Smart, tiered (hybrid) safety — this is the key control.** A proposed change is first
**classified by a change-risk classifier** into a *class* (like the tool-risk gate):
- **low-risk classes** (e.g. minor wording/format nudges, a small RAG top-k bump, a
  temperature tweak within bounds) → **autonomous**: auto-promote *iff* it beats the active
  version on the eval regression suite by a margin. The self-agentic loop closes on its own.
- **high-risk / high-blast-radius classes** (rewriting core instructions, changing
  guardrail/safety wording, tool-permission or model-role changes, large diffs) → **HITL**:
  escalate to the existing durable approval inbox; a human promotes.
The model only ever writes a **DRAFT**; the eval gate is always on; the **adapter prompt is
the floor**; every version is **one-click reversible**. Autonomy tier is configurable
(enterprise default = conservative). This is "smart HITL": automate the safe classes,
gate the risky ones — never blind self-modification.

- **Registry** (`app/ops/registry.py` + `PromptVersion` table): `(prompt_key, version,
  system_prompt, config JSON {rag_top_k, temperature, model overrides, memory window},
  status draft|staged|active|archived, parent_version)`. `get_active()` is the seam:
  `agent/deps.py::render_system_prompt` reads it, falling back to `adapter/prompts.py`.
- **Live trace eval** (`app/ops/trace_eval.py`): async judge grades each completed run,
  writes `EvalResult` keyed by `run_id`. Observe = tokens/latency/errors already emitted.
- **Diagnose** (`app/ops/diagnose.py`): cluster failing `EvalResult`s → feed failing
  traces + judge critiques to a Reflexion/DSPy-style **PromptOptimizer** → writes a **draft**
  `PromptVersion` only.
- **Gate + Release** (`app/ops/release.py`): a draft promotes only if it (a) beats the
  active version on the offline `eval/harness.py` regression suite AND (b) a human approves
  via the **existing `Approval` inbox** (`prompt_release` gate, HIGH risk). **Rollback** =
  re-activate the previous version (one call).

**Files:** NEW `app/ops/{registry,trace_eval,diagnose,release}.py`; `PromptVersion` table
(reuse `EvalResult`, `Approval`). EDIT `agent/deps.py`, `agent/orchestrator.py` (kick off
trace_eval post-run, best-effort), `api/routes.py` (`GET /ops/prompts|evals`, `POST
/ops/diagnose|release|rollback`).

### GAP 3 — MCP  *(Effort S · Demo Med)*
Tools are already MCP-shaped. **Recommendation: expose the adapter registry over a thin MCP
*server* (stdio) facade over `run_tool`/`tool_definitions_for` — preserving risk tiers,
allowlist, audit. Do NOT consume external MCP tools** (network/process cost fights the
no-Docker/offline constraint, buys nothing for a known toolset). NEW `app/mcp/server.py`.

### GAP 4 — Per-step / trace-level evals  *(Effort M · Demo High)*
`StepEvaluator` (in `app/ops/trace_eval.py`) reads the per-run event/state trajectory
(offline-friendly vs querying Phoenix) and grades per step: classification (guardrail
verdict), retrieval (context relevance), tool selection (appropriateness) → `EvalResult`
with `metric="step:*"` keyed by `run_id`+node. Feeds Diagnose at step granularity.

### GAP 5 — Multi-agent routing  *(BUILD — decided in scope; Effort M · Demo High)*
A real, **auditable** agent-to-agent router (LangGraph supervisor pattern): a cheap
classifier `route` node dispatches a turn to the right specialist sub-agent — **Q&A agent**
(default RAG+tools), **Summarizer/Consolidation agent**, **Diagnose/Optimizer agent**,
and future domain sub-agents — with explicit hand-offs and typed output expectations, all
under the existing guardrail/approval/trace envelope. Routing is a **deterministic-first
classifier** (not free negotiation) so the money-shot trace stays clean and every hand-off
is a visible span + audit row. Core owns the supervisor + hand-off protocol; the adapter
declares the available specialists + routing hints. Files: `app/agents/{router,registry}.py`
(supervisor + sub-agent registry), adapter `agent_roster()` contract.

### GAP 6 — Call-safety hardening  *(Effort S · Demo Low, correctness High)*
Add per-call `max_tokens` + a timeout wrapper in `core/llm.py`; one JSON re-ask on invalid
structured output before the graceful fallback. Cheap, strengthens R2/C2.

---

## 3. Phased plan + hackathon cut line

- **Phase 1 — Memory + multi-turn (foundational, highest demo).** Ship fully: sessions,
  `chat_message`+`semantic_fact`, Redis window, the two nodes, `memory_spec` + `skills/*.md`,
  `session_id` through the API. Everything else rests on sessions existing.
- **Phase 2 — LLM-Ops closed loop (centerpiece).** `PromptVersion` registry + harness reads
  active → live `trace_eval` → `diagnose`/optimizer draft → **Release via the existing
  approval gate** + rollback. Mostly wiring over existing eval + approvals.
- **Phase 3 — Depth.** Per-step trace evals (extends Phase 2) + consolidation background job
  (extends Phase 1; CLI first).
- **Phase 4 — Facades.** MCP server facade (S) + call-safety hardening. Reject the router.

**Recommended cut line:** Phase 1 complete + Phase 2 through Release-via-approval + rollback.
First cuts if tight: consolidation → manual CLI trigger; per-step evals → answer-level only;
MCP → only with a spare hour. Hard reject: multi-agent router.

**The story this buys:** *"It remembers you across turns and distills what it learns into
durable facts and skills (Harness), and it watches its own traces, diagnoses failures, and
proposes a better prompt that a human approves and can roll back (closed LLM-Ops loop) — all
domain-agnostic, reshaped on the day by editing only `app/adapter/`."*

---

## 4. Frontend "show both" (LATER — after core)
- **Harness/Memory view:** session concept; a Memory panel (episodic timeline · semantic
  facts · active skills); a live "consolidation happened" stream event. Needs
  `GET /memory/{session_id}`, `GET /memory/facts`.
- **LLM-Ops view:** prompt-version timeline with diffs + per-version eval scores + live eval
  trend + a "Diagnose" button; release approval surfaced in the same approvals inbox. Needs
  `GET /ops/prompts|evals`, the diagnose/release/rollback endpoints.
- **Per-step evals:** pass/fail chip on each node of the existing trace viz (`GET
  /ops/evals?run_id=`). **MCP:** a "server: N tools exposed" badge.
