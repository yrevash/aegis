# Aegis guardrails pilot — deferred follow-ups

Tracked debt from the `feat/aegis-module-contract` pilot (design spec:
`docs/superpowers/specs/2026-08-11-aegis-module-contract-design.md`). The pilot merged
MERGE-READY; these are honest, non-blocking follow-ons surfaced by the final whole-branch review.
None is a security regression.

## From the final review (all Minor)

1. **Injection cache is unused scaffolding.** `aegis/src/aegis/guardrails/cache.py`
   (`make_injection_cache` / `InjectionCache`) is honest + tested but not yet called by the
   classifier or pipeline. Either wire it into `classify_injection` (key on a text hash) or keep it
   explicitly labelled as contract scaffolding.
2. **`AEGIS_MODE` does not yet govern the backend.** `aegis.core.config.CoreSettings` /
   `require_full_infra` and `aegis.core.health` probes exist and are tested, but the running backend
   still uses its own pre-existing `app.config` honest-infra. `AEGIS_MODE` today governs only the
   guardrails cache factory. Adopt `CoreSettings` in the backend boot path (fail-fast + `/readyz`
   per-dependency + UI banner) in a later component.
3. **Dead `guardrails_engine` config knob.** After the NeMo dispatch was dropped from the backend
   shim (item (a) below), `backend/src/app/config.py`'s `guardrails_engine` field is read by nothing.
   Remove it, or restore the dispatch (item (a)) — a config knob that silently does nothing is
   exactly the dishonesty this platform is trying to eliminate.
4. **Frontend event-shape reconciliation.** `frontend/src/types/stream.ts` types guardrail
   `redactions` as structured `Redaction[]` with camelCase `spanKind`, while
   `aegis.core.events.GuardrailEvent` emits `redactions: list[str]` (kinds) and snake_case
   `span_kind`. Not a live bug (the aegis stream is not yet the wire format — the pilot renders from
   fixtures). Reconcile the shapes when the aegis stream is actually wired to the frontend (the
   "process rail" follow-on).

## Parked NeMo items (triaged DEFER — no security regression)

- **(a) `GUARDRAILS_ENGINE=nemo` dispatch dropped from the backend shim.** The default programmatic
   path (full model classifier + deterministic signatures + PII) is the *stronger* path; dropping
   the dispatch only removes the ability to opt into a weaker one. Restore it alongside (b).
- **(b) NeMo Colang runs deterministic-only injection.** `self_check_injection` passes
   `completer=None` (deterministic backstop + PII still enforced; model layer explicitly logged as
   off). Wiring a real `ChatCompleter` into the NeMo engine (so the Colang path matches the
   programmatic path's model layer) is the follow-on. The model injection layer IS wired on the
   actual production path via the shim's `_gateway_completer`.

## Streaming-spine minors (final review, non-blocking)

5. **Emitter run-level ordering not programmatically enforced** (`aegis/src/aegis/core/stream.py`) — message/tool id bracketing is enforced (raises on delta/end without start), but `run_started/finished/error` and open-step-at-run-end are not state-guarded; ordering relies on caller discipline. Optional: track a run-state flag and raise on out-of-order lifecycle calls.
6. **`redaction_spans` computed regardless of verdict** (`aegis/src/aegis/guardrails/pipeline.py`) — `stream_check_input_agui` runs `pii.scan(text)` even when the input is BLOCKED by injection, so the payload could list PII spans that were never redacted. Fix: only populate spans when `verdict is REDACT`, or label them "detected" vs "redacted". Matters once the UI renders spans.

## aegis.ml minors (module review, non-blocking)

7. **ml artifact absent in fresh clones** (`aegis/src/aegis/ml/artifacts/ml_spine.joblib` is gitignored) — a fresh clone's `get_model()` trains a FALLBACK noise model (feature_0..3) until the domain trains it. Correct-by-design for a domain-agnostic package (it must NOT ship a service-request model), but if shippable defaults are wanted, `git add -f` a generic-but-real artifact or ship as package-data. Backend trains the real domain artifact on first use.
8. **`app/ml/_domain_spec()` masks adapter breakage** (returns None → FALLBACK noise) — inherited verbatim from legacy `resolve_spec`; if `app.adapter` genuinely breaks, backend would silently train a noise model. Pre-existing; consider failing loud in a follow-up.

## Module-rollout cross-cutting findings (surfaced during extraction + docs)

9. **Two leaf-to-leaf import violations** (debt against a future multi-wheel split; harmless in the single `aegis` package today): `aegis.memory` imports `aegis.retrieval.{fusion,vectors,spotlight,models}` (reuses RRF/cosine/spotlighting); `aegis.governance` imports `BudgetExceededError` from `aegis.gateway.types`. The Module Contract says leaves import only `aegis.core`. Clean fix (follow-on): move the shared retrieval primitives (fusion/vectors/spotlight) into `aegis.core` (or a shared `aegis.util`), and move `BudgetExceededError` into `aegis.core.types` so gateway+governance both import from core.
10. **`aegis.guardrails` base pipeline needs no extra** (pure code) — there is no `guardrails` extra; only NeMo is gated (`aegis[nemo]`). Correct-by-design; just document `pip install aegis` suffices for the core rails.
11. **Frontend AG-UI surface is currently a name-registry mirror + SSE decoder only** — no per-event React dispatcher/console yet renders the module CustomEvents (guardrail/shap/conformal/citations/reasoning/model_call/eval_result/memory_recall). The full "process rail" console is the deferred frontend follow-on (streaming-spine design spec §6): build the `event.type → React component` dispatcher + renderers.
12. **retrieval RERANKER span** removed when the package went observability-agnostic — re-add via `aegis.observability` wiring or when `stream_retrieve` is wired into the live path.

## Next component work (from the design spec §6 rollout)

Process rail (frontend "show your work") → rest of Set 1 (token-optimization, evals) → retrieval/ML
→ gateway/memory/governance → agent/trace. Each is its own spec → plan → build.
