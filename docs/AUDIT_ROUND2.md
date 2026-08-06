# AUDIT_ROUND2.md — post-build adversarial audit (memory + LLM-Ops + router + MCP + gateway)

## Round 3 — enterprise-maturity pass audit (branding + full frontend rework + cleanup)

After the Aegis branding/capabilities manifest, the zero-knowledge learning docs, the
repo cleanup, and the full 9-surface frontend redesign, two read-only auditors swept the
result (backend+docs drift; frontend honesty). **Verdict: the maturity pass is honest and
holds up** — 469 backend tests pass, ruff clean, all 12 `AEGIS_MODULES` `module_path`s
import (self-enforced by `tests/test_capabilities.py`), the learn/00–60 docs carry **zero**
file/function/path drift, and every redesigned surface preserved its live-vs-mock honesty
markers (`sample` badges, `—` on null, jargon demoted to InfoTips not deleted into false
claims). Real findings were small and are all **FIXED** below.

**FIXED (Round 3):**
- **MED (honesty) — Audit surface mislabelled** as "Aegis Trace · OpenTelemetry → Phoenix"
  in the nav, but it renders the Postgres `/audit` append-only trail (which belongs to
  **Aegis Governance** per the module map, not the OTel/Phoenix tracing module). Relabelled
  to "Aegis Governance — append-only audit trail · Postgres (RLS), with trace links to Aegis
  Trace" (`routes/Portal.tsx`).
- **MED (honesty) — stale ADR 0007 supersede note** claimed the Python autonomy-band engine
  (`classify_autonomy`/`assess_uncertainty`) was "retained but inert." It was in fact deleted
  in the cleanup pass; only the `AutonomyBand` wire-enum survives. Corrected the ADR status +
  note (`docs/adr/0007-conformal-autonomy-bands.md`).
- **LOW — stale code refs** to the deleted autonomy-band engine in two historical docs:
  `IMPROVEMENTS.md` listed `agent/deps.py::classify_autonomy` as a shipped feature (struck);
  `ARCHITECTURE_REVIEW.md` referenced `assess_uncertainty` + old line numbers (marked
  superseded, pointing to ADR 0007).
- **LOW — dead code deleted:** `frontend/src/components/metrics/MetricsDeck.tsx` (orphaned by
  the Overview redesign, imported nowhere) and `frontend/src/components/ui/progress.tsx`
  (unused shadcn primitive). Frontend stayed green (build ✓, 167 tests, lint clean).

**Informational (no action):** `capabilities.module_count()` is test-only (the route uses
`len(AEGIS_MODULES)`) — harmless, tested, left as-is. The `app.core.governance` manifest
`module_path` points at the `GovernanceContext` contextvar while the RBAC/RLS/budget/audit
code lives across `data/*`+`core/*` — the LEARNING_GUIDE map already states "`data/*`,
`core/*`" honestly, so the representative single path is not a fabrication.

---


After the memory subsystem, the LLM-Ops closed loop, the router, MCP, and call-safety
hardening were built, five read-only auditors swept the code (claim-vs-reality, bugs,
hardcoding, dead/unused, SOTA, wiring). Two auditors (memory, whole-repo cross-cutting)
**failed mid-run on a session usage limit** and must be re-run (see "Not yet audited").
The other three completed; their real findings and disposition are below.

> Verdict from the completed auditors: the core is **genuinely real and honest** — the
> LLM-Ops loop is truly closed (real prompt-dependent eval_fn, real active-prompt cache
> feedback, real per-run EvalResult writes), the qa money-shot + risk gate + bounded
> self-repair + durable exactly-once resume are correct, the MCP facade is a real
> governance-preserving wrapper (not a bypass), and the NeMo Colang policy actually
> executes with a shared deterministic injection backstop. The findings are refinements,
> a few decorative claims, and dead weight — not fabricated functionality.

## FIXED this round (committed)
- **Gateway `embed()` had no timeout** → unbounded hang on the hot retrieval path. Added
  per-call `timeout` + `asyncio.wait_for` backstop. (`core/llm.py`)
- **Governance fail-open on import** → a bare `except Exception` around the governance
  import silently disabled *all* budget enforcement on any error. Narrowed to `ImportError`.
- **`rollback` could resurrect an eval-REJECTED draft** (also `ARCHIVED`, usually higher
  version) → now only reverts to a version that was actually live (`activated_at` set).
- **`release()` didn't check the version is a DRAFT** → could re-release an active/staged
  version. Now rejects non-drafts.
- **MCP HIGH-risk proposal was silent + overclaimed** ("routed to the approval inbox" but
  recorded nothing) → now writes a real audit row (`mcp.high_risk_proposal:<tool>`,
  `executed=false`) and the message matches reality. No side effect still occurs.
- **`/ops/evals?prompt_key=` was a dead filter** (always `[]`) and **diagnose clustered
  unscoped failures** → added real indexed `EvalResult.prompt_key` + `tenant_id` columns
  (trace-eval writes them; diagnose + the endpoint filter by `prompt_key`).
- **Live-gate approval left the durable row stuck `RESUMING`** → `decide_approval` now
  finalizes to `APPROVED` when a live socket executes the approved gate.
- **Router memory keywords too broad** ("my preferences" hijacked "update my preferences to
  X" away from the tool path) → tightened to intent-bearing recall phrases (know/remember/
  told).
- **Output content-filter false positive** — dropped `"you are chatgpt"` (blocked any
  legitimate answer mentioning ChatGPT); kept the specific system-prompt-leak markers.

## DEFERRED (lower severity or larger — tracked for follow-up)
- **Full multi-tenant isolation in the ops path (HIGH, partial):** `EvalResult` now carries
  `tenant_id`, but `registry.promote` still archives every tenant's ACTIVE row for a key,
  the active-prompt cache is keyed by `prompt_key` only (last-writer-wins across tenants),
  and `/ops/release|rollback` don't verify the draft belongs to `auth.tenant_id`. Real gap
  **iff the demo runs multi-tenant on a shared `prompt_key`**; single-tenant is unaffected.
  Fix: key the cache by `(tenant_id, prompt_key)`, tenant-filter `promote`/`get_active`, and
  add ownership checks on the mutation endpoints.
- **Budget check-then-act TOCTOU (MED):** the pre-spend budget check reads prior usage; N
  concurrent governed calls can pass the same stale check. Needs an atomic reservation.
- **`litellm.ssl_verify` disabled process-wide** (LOW/MED): scope it per-call to the TCS
  gateway rather than the module global.
- **`GUARDRAILS_ENGINE=nemo` silently downgrades** to programmatic rails if the package is
  absent (enforcement stays real; operator's mental model is wrong) — log a warning.
- **`PromptVersion.config` is advisory-only** (MED): stored + risk-classified but not applied
  at generation. Either wire it (temperature/model in eval_fn + the generate node) or mark it
  advisory and drop it from the promotable risk surface. (Currently document as advisory.)
- **Change-risk classifier is term-count based** (LOW): a small semantically-dangerous edit
  that doesn't alter a listed term's count classes LOW → auto-promotes. Add an LLM-judged
  risk backstop or a broader term set.
- **Equal-score draft auto-promotes** (LOW): `margin=0.0` + strict `<` lets a no-improvement
  prompt tie-and-promote. Consider a default `margin>0`.
- **Colang PII rewrite discarded** (LOW): `nemo_check_*` recompute `pii.redact` rather than
  reading the Colang-rewritten message (functionally identical; the block decision is the
  real Colang signal). Document.
- **PII detector false positives** (LOW): IP regex masks software versions (`1.2.3.4`), phone
  regex masks bare 10-digit numbers — require context / lower priority.
- **Dead / retired surface (LOW, documented-inert): RESOLVED — deleted.** The autonomy-band
  machinery in `agent/deps.py` (`assess_uncertainty`/`relative_width`/`classify_autonomy`/
  `_pick` + the `AutonomyBand` import, the `AutonomyPolicyLike` protocol, the `PolicyFn`
  alias, and the `AgentConfig.uncertainty_*`/`abstain_*`/`autonomy_bands_enabled`/
  `high_risk_never_autonomous` fields), the `autonomy_policy_for` dep field + binding +
  `_default_autonomy_policy_for` (and the now-unreferenced adapter `AutonomyPolicy`/
  `AUTONOMY_POLICIES`/`autonomy_policy_for` surface in `adapter/personas.py`), the
  `band`/`abstained` state keys, the `events.abstained` builder + the `Abstained`
  `StreamEvent` variant, `ml_explanation`'s 4 always-null args, and the duplicate
  `graph._select_checkpointer` (build now compiles with the single
  `data/session.get_agent_checkpointer` path) — all removed. Suite + ruff green.
- **Router LLM-tiebreak unreachable in prod (honesty):** the shipped roster has one named
  specialist (`memory`), so the `used_llm=True` tiebreak never fires live — routing is
  deterministic qa-vs-memory. State this honestly or add a second specialist.
- **Live-gate finalize is untested** (all durable tests park); add a live-branch test.

## Re-audit (memory + whole-repo cross-cutting — the two that failed before)

Both re-ran to completion. **Verdict: the backend is genuinely real and honest** — ML spine,
memory, retrieval, NeMo, MCP, observability all verified real; no fabricated core
functionality; no TODO/stub/`NotImplementedError` anywhere. Findings are dead code, a few
decorative knobs, and doc drift.

**Memory — FIXED (committed):**
- **HIGH — semantic recall was inert live** (`recall_memory` ran before `retrieve`, so
  `query_vec` was always `None` → facts fell to recency-only, episodic recall always empty).
  Fixed: `MemoryDeps.assemble` now embeds the query itself before recall.
- **MED — consolidation ran twice per cadence** (immediate raw run + a PENDING job the
  sweeper re-ran). Fixed: the immediate fire now drains the durable queue (`sweep_pending`),
  closing the job so the sweeper won't re-run it.
- Honest docs: `MemoryBackend` redis tier (not wired), `select_skills` (no vector fallback).
- Solid, verified: isolation (every query filters `subject_id`), bitemporal apply +
  concurrency guard, token-budget assembler (≤ budget, never drops query), endpoint authz.

**Cross-cutting — FIXED (committed):**
- **MED — fabricated telemetry provider** `azure.ai.openai` on every span → corrected to the
  real `tcs.genailab`.
- **MED-HIGH — NeMo silent fail-open on refusal-string drift** → added a coupling test that
  fails CI if the Python refusal constants diverge from the Colang `bot refuse` messages.

**DEFERRED (tracked cleanup — dead code / decorative / doc; no fabricated functionality):**
- **Retired autonomy-band machinery** (~200 LOC, confirmed dead): `classify_autonomy`/
  `assess_uncertainty`/`relative_width`, `AutonomyBand` (in `deps`), `events.abstained`,
  `state.band`/`abstained`, `autonomy_policy_for`, `ml_explanation`'s 4 always-None args, and
  the `AgentConfig.uncertainty_*`/`abstain_*` fields → **RESOLVED — deleted** (see the FIXED
  entry above). `AutonomyBand` remains only as the wire-enum backing `MLExplanation.band`.
- **`graph._select_checkpointer`** duplicates `session.get_agent_checkpointer` and is
  test-only (prod recompiles) → **RESOLVED — collapsed** to the single `get_agent_checkpointer`
  path (`_build_postgres_checkpointer` kept; `session.py` imports it).
- **`retrieval/pgvector_index.py`** (whole module) — never imported; targets a `chunks` table
  the pipeline never writes → **RESOLVED — module + its test deleted** (the retrieval pipeline
  writes `retrieval.models.Chunk` into the LightRAG store, never the DB `chunks` table).
- **Forget/prune sweep** (`ForgetPolicy`) tested but never wired; `MEMORY_SPEC` overstates it
  as running → implement the sweep or correct the spec.
- **Read-path frequency signal** (`w_freq=0`, `access_count` not bumped on recall) — misleading
  "times recalled" framing → wire a read bump + nonzero weight or drop the framing.
- **`log_level` setting** never consumed (INSTALL documents it) → **RESOLVED — applied**:
  `main.create_app` now calls `logging.basicConfig(level=settings.log_level, force=True)`
  (unknown level → INFO fallback). The `chunker.dedup` test-only helper,
  `semconv.GEN_AI_RESPONSE_FINISH_REASONS`, and the unused `openinference-instrumentation-litellm`
  pyproject dependency were also removed in the same pass.
- **Eval-harness groundedness** is tautological (context graded as its own answer) — honestly
  commented, off by default, and the *production* trace-eval grades real answers; low blast radius.
- **NeMo Colang PII rewrite discarded** (Python recomputes `pii.redact`); **router LLM-tiebreak
  unreachable** with a one-specialist roster; **nemo silent downgrade** when the package is
  absent → all honesty/wording items (document or minor wiring).
- **Test hygiene:** sync tests carry `@pytest.mark.asyncio` (23 warnings) — cosmetic.
