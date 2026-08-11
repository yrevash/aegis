# aegis.evals + aegis.ops — Evaluation & LLM-Ops (extraction) Design Spec

- **Date:** 2026-08-12 · **Branch:** `feat/aegis-module-contract` · Module 6 of 8
- **Map:** `.superpowers/sdd/module-evals-map.md`

## 1. Goal

Extract two related capabilities as standalone importable packages:
- **`aegis.evals`** — a *pure* evaluation library: RAGAS-style deterministic lexical proxies
  (context-precision/recall/groundedness), an optional injected LLM-as-judge, a DeepEval-pattern regression
  gate. **No heavy deps** (ragas/deepeval are hand-rolled), no ORM. Depends on `aegis.retrieval` + an injected
  completer.
- **`aegis.ops`** — the LLM-Ops self-improvement loop (trace→eval→diagnose→gate→tiered-release), carrying the
  `EvalResult`/`PromptVersion` ORM (on `aegis.data`), a versioned prompt registry, and the release gate.
  Depends on `aegis.evals` + `aegis.data` + an injected completer + an injected prompt-floor renderer.

Ops→evals is one-directional. Both keep `aegis.core` minimal. Strangler shims keep the backend green.

## 2. Design

### aegis.evals (`aegis/src/aegis/evals/`)
Move `corpus.py`, `metrics.py`, `judge.py`, `harness.py`, `regression.py`. Sever:
- `app.core.llm` (LLMResult/Usage) → `aegis.gateway`; `app.core.models.ModelRole` → `aegis.core.models`.
- `app.retrieval.*` (Chunk, RetrievalResult, cache, memory, pipeline, Retriever) → `aegis.retrieval`.
- **Drop** the lazy `from app.core.llm import complete` fallback in `judge.py` (inject-only; `judge_answer(..., complete)` where `complete=None` means judge is disabled).
- `regression.py`'s `app.agent.router.{load_roster, route_query}` (the tool-selection case) → **inject** a router (`route_fn`/`roster`); if none injected, the agentic tool-selection case is skipped (optional). Preserve the RAG-path metrics unconditionally.
Preserve: `evaluate`, `score_case`/`aggregate`, `judge_answer`/`judge_enabled`, `run_regression_gate`/`run_tool_selection_eval`, `build_eval_retriever`, DEFAULT_THRESHOLDS, DEFAULT_METRICS, SEED_CASES/SEED_CORPUS, all frozen dataclasses.
Optional AG-UI (greenfield, à la carte): `aegis/src/aegis/evals/stream.py` — add `EVAL_RESULT = "eval_result"` to `aegis.core.stream_names` (+ frontend mirror); helper emits `emitter.step("evaluate", SpanKind.EVALUATOR)` + `emitter.custom(EVAL_RESULT, {overall, passed, metrics})`. (SpanKind.EVALUATOR already exists.)

### aegis.ops (`aegis/src/aegis/ops/`)
Move `trace_eval.py`, `diagnose.py`, `registry.py`, `release.py`, `gate.py`, + a `models.py` (EvalResult, PromptVersion, PromptStatus on `aegis.data.AegisBase`). Sever/inject:
- `app.data.models.{EvalResult, PromptVersion, PromptStatus}` → `aegis.ops.models` on `aegis.data.AegisBase`. `Approval`/`ApprovalStatus` stay app-layer (agent-owned); ops uses an injected `enqueue_approval` (already a seam in `release`).
- `app.core.models.ModelRole` → `aegis.core.models`; `app.api.schemas.RiskLevel` → `aegis.core.types` (governance also moved it).
- `app.adapter.{get_persona, render_system_prompt}` → inject a `render_floor_prompt()` callable (the prompt baseline/floor).
- `app.data.session` → injected session factory + `set_tenant_scope` (reuse `aegis.governance.rls.set_tenant_scope` or inject).
- `app.eval.*` → `aegis.evals.*`.
Preserve: `evaluate_run`, `diagnose`, `registry.*` (get_cached_active/refresh_cache/create_draft/get_active/list_versions/promote/rollback), `classify_change`, `release`/`apply_release_decision`, gate.{make_eval_fn/enqueue_release_approval/list_pending_releases/decide_release}. Keep trace_eval best-effort/off-hot-path semantics.

## 3. Extras

`aegis.evals` needs no new heavy dep (uses aegis.retrieval + aegis.gateway types). `aegis.ops` uses `aegis[data]` (sqlalchemy). No `evals`/`ops` heavy extra required beyond what data provides. `aegis.core` stays minimal.

## 4. Strangler shim

`backend/src/app/eval/` → delegate to `aegis.evals` (inject `app.core.llm.complete`, `app.agent.router` for the tool-selection case). `backend/src/app/ops/` → delegate to `aegis.ops` (inject completer, `app.adapter` floor renderer, `app.data` session + `enqueue_approval`). `app.data.models` re-exports EvalResult/PromptVersion/PromptStatus from `aegis.ops.models` (identity); backend `create_all` covers them (on AegisBase). `app.agent.orchestrator` (trace_eval), `app.agent.deps` (registry), `app.main` (registry startup), `routes.py` `/ops/*` — all unchanged (import through the shims). `python -m app.eval.harness`/`regression` keep working.

## 5. Testing & proof

Port `backend/tests/eval/` (pure) → `aegis/tests/evals/`; `backend/tests/ops/` (ORM+adapter, inject fakes) → `aegis/tests/ops/` (SQLite via aegis.data). Add import-isolation guards (`import aegis.evals` pulls no fastapi/litellm; `import aegis.ops` pulls no fastapi/litellm). Backend parity: full suite green minus the 2 env failures; the `/ops/*` routes + off-hot-path trace_eval work through the shims; eval gate CLIs work.

## 6. Definition of done

`aegis.evals` (pure) + `aegis.ops` importable, completer injected, evals metrics/judge/gate preserved, ops
loop (trace/diagnose/registry/release/gate) preserved with ORM on `aegis.data`, `aegis.core` minimal, backend
green through the shims (minus 2 env failures) with `/ops/*` + trace_eval intact.
