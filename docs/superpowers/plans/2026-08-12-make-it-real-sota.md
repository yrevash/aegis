# Make-It-Real / SOTA Enterprise Program — Implementation Plan

> Execute ONE task at a time via a subagent. Each: plan to the core → **prefer an
> existing SOTA/industry library over homegrown code** → integrate → verify it
> works (tests + lint + actually runs) → commit → next. Everything enterprise-grade.

**Guiding rule (user):** No homegrown 4–10 line stand-ins. If a SOTA implementation
already exists (a library, or already in this repo), take it and wire it in — don't
re-implement. Everything must be real, enforced, used, and streamable to the console.

**Done already this arc:** reality audits · ~240 dup files deleted · MLCommons S1–S13
content-safety rail (real, wired, streamed) · custom-rail extension seam.

## Tasks (ordered)

### T1 — Enforce the NeMo Guardrails engine on the live path + stream it
NeMo Guardrails 0.23 (the SOTA guardrail framework) is installed and a Colang policy
exists (`aegis/guardrails/config/`), but the programmatic pipeline runs instead. Wire
the dead `guardrails_engine` knob so `="nemo"` runs the NeMo engine; map its verdict to
a streamed `GuardResult` (layer-tagged) via the existing guardrail stream events; keep
programmatic as fallback. Verify the engine loads + runs offline in tests (actions
delegate to the real aegis primitives with a stubbed completer).

### T2 — Expand the enterprise Colang policy (OWASP-2025 + MLCommons)
Add rails as Colang flows whose actions delegate to the REAL aegis functions (no
detection logic in Colang): content-safety (S1–S13, `content_safety.screen_content`),
**topical control** (new `topical.py`, LLM self-check), **output grounding/self-check**
(new `grounding.py`, hallucination/fact-check vs retrieved context). Map every rail to
OWASP LLM Top-10 (2025). Enterprise policy artifact + tests.

### T3 — Industry PII: Microsoft Presidio behind the `pii` API
Replace the homegrown regex PII with **Microsoft Presidio** (the industry-standard PII
engine) behind the existing `pii.redact`/`pii.scan` interface — spaCy small model,
CPU-only, offline. Keep the interface + `[REDACTED_x]` contract so nothing downstream
changes. Fallback to regex if Presidio unavailable.

### T4 — Real knowledge graph (offline entity graph)
Replace the fake linear `_graph_slice` chain with a genuine entity+relation graph:
**LLM-cached extractor** (cache to disk, offline after run 1) with a deterministic
NER fallback (spaCy/GLiNER — take the SOTA lib). Typed entity nodes + real relations;
frontend palette/legend for the real `entity_type` kinds. (Design already scoped.)

### T5 — `aegis.ml` to the SDK bar
Confirm/complete: construct with a custom spec, all methods/datatypes present, and a
`stream_*` method to the console. Reuse the existing SOTA stack (XGBoost + MAPIE + SHAP).

### T6 — `aegis.data` to the SDK bar
Custom schema ergonomics + any missing datatypes/helpers; documented usage.

### T7 — Dashboard reality + AEGIS_MODE
Real backend metrics fields for the 5 fabricated tiles (or feed cost-trend from the
real in-session series); adopt `AEGIS_MODE`/`CoreSettings` fail-fast in backend boot.

## Verification gate (every task)
`aegis` suite + `backend` suite green (minus the 2 known-env failures) · ruff clean ·
frontend `tsc`/`oxlint`/`vitest` green where touched · the new capability actually runs.
