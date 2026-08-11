# aegis.gateway — LLM Gateway (extraction) Design Spec

- **Date:** 2026-08-12 · **Branch:** `feat/aegis-module-contract` · Module 3 of 8
- **Map:** `.superpowers/sdd/module-gateway-map.md`

## 1. Goal

Extract the LiteLLM chokepoint into a standalone importable **`aegis.gateway`**: `complete`/`embed` by
`ModelRole`, custom OpenAI-compatible provider, heterogeneous routing, role fallbacks, timeouts, cost
accounting + savings tally, structured-output re-ask. **Budget/governance and observability are injected
hooks** (no hard `app.*` coupling); litellm is the single heavy dep under `aegis[gateway]` (lazy). Emits a
per-call `model_call` AG-UI event. `aegis.core` stays heavy-dep-free. Strangler shim keeps the backend green.

## 2. Interface to preserve (verbatim)

- `async complete(role, messages, *, tools=None, temperature=0.0, response_format=None, max_tokens=None) -> LLMResult`
- `async embed(texts) -> list[list[float]]`
- helpers `record_call`, `usage_tally() -> dict`, `last_trace_id()`; `BudgetExceededError`.
- Result types (pydantic): `ToolCallResult{id,name,args}`, `Usage{prompt_tokens,completion_tokens,cost_usd}`, `LLMResult{content,tool_calls,usage,model}`.
- LiteLLM call shape, tool parse, `_ROLE_FALLBACKS`, timeouts, cost/estimate, `_UsageTally`/savings, one JSON re-ask — all preserved byte-identical (see map).

## 3. Design (`aegis/src/aegis/gateway/`)

- **`types.py`** — `LLMResult`, `ToolCallResult`, `Usage`, `BudgetExceededError` (pydantic/stdlib only). `app.core.llm` re-exports these + backend re-exports `BudgetExceededError` (orchestrator catches it).
- **`routing.py`** — `model_for(role)`, `routing_table()`, `is_small_model()`, `_DEFAULT_ROUTING`, cost tables + env overrides (`MODEL_<ROLE>`, `COST_<ROLE>_IN/OUT`). Uses `aegis.core.models.ModelRole` (already created by the retrieval module).
- **`llm.py`** — the gateway (`complete`/`embed`), moved from `app.core.llm`, with the three couplings replaced by **injected hooks**:
  - **config** (`GatewayConfig` protocol/dataclass): `base_url, api_key, ssl_verify, max_output_tokens, timeout_seconds, budget_fail_open`.
  - **governance hook** (protocol, default no-op): `get_context() -> ctx|None`, `async enforce(ctx)` (BEFORE spend; raises `BudgetExceededError`; fail-closed unless `budget_fail_open`), `async record(ctx, *, model, prompt_tokens, completion_tokens, cost_usd, trace_id)` (best-effort ledger). Ungoverned path (no ctx) stays a full no-op.
  - **observability sink** (protocol, default no-op): span open + `set_usage(prompt,completion,cost,model)` + `trace_id()`.
  Module-level defaults: a global `configure(config=, governance=, observability=)` sets the injected hooks; without configuration it uses a fail-open no-op governance + no-op observability + config from env (so `aegis.gateway` is usable standalone with just an api key/base url).
- **`stream.py`** — `MODEL_CALL` custom event. Add `MODEL_CALL = "model_call"` to `aegis.core.stream_names` (+ frontend `streamNames.ts` mirror). Helper `async stream_complete(role, messages, emitter, **kw) -> LLMResult` (à la carte): runs `complete`, emits `emitter.custom(stream_names.MODEL_CALL, {model, role, prompt_tokens, completion_tokens, cost_usd, cost_saved_usd, small_model})` inside `emitter.step("llm", SpanKind.LLM)`. Callers opt in.

## 4. Strangler shim

`backend/src/app/core/llm.py` → delegate to `aegis.gateway`, calling `aegis.gateway.configure(...)` at import
with: config from `app.config`; a governance hook wrapping `app.core.governance.get_governance_context` +
`app.data.governance.enforce_governance`/`record_usage`; an observability sink wrapping `app.observability`
`genai_span`/`set_usage`/`current_trace_id`. Re-export `complete`, `embed`, `record_call`, `usage_tally`,
`last_trace_id`, `BudgetExceededError`, `LLMResult`, `ToolCallResult`, `Usage`. ALL callers (agent/deps,
retrieval gateway, guardrails classifier, memory, ops/eval, platform `usage_tally`) unchanged.

## 5. Extra + honest behavior

`aegis[gateway] = ["litellm>=1.52"]` (lazy). Fail-closed budget by default (unless `budget_fail_open`). The
no-op governance default is EXPLICIT (documented) — standalone `aegis.gateway` does no budget enforcement
unless a governance hook is injected; it does not silently pretend to enforce. Ledger writes are best-effort
(swallow, as today) but that's a ledger, not a control path.

## 6. Testing & proof

Port `backend/tests/core/test_llm.py` (FakeLiteLLM monkeypatched into sys.modules before the lazy import;
inject a fake config + no-op governance/obs): provider string/api_base/ssl-off, tool-parse + bad-JSON `_raw`,
fallbacks, cost token-estimate, tally/savings, max_tokens/timeout forward, bounded TimeoutError, one JSON
re-ask, embed. Add: a streaming test (`stream_complete` emits STEP(llm) → custom(model_call) → STEP_FINISHED);
a governance-hook test (enforce raises BudgetExceededError before spend; record called after). Import-isolation
guard: `import aegis.gateway` pulls no litellm. Backend parity: `test_governance_enforcement`,
`test_governed_budget`, `test_platform_surfaces` (usage_tally) green through the shim; full backend suite green
minus the 2 env failures.

## 7. Definition of done

`aegis.gateway` importable + `aegis[gateway]`-installable, `complete`/`embed` preserved, governance +
observability are injected hooks (no-op standalone, real via backend shim), emits `model_call` over AG-UI,
`aegis.core` heavy-dep-free, backend green through the shim (minus 2 env failures).
