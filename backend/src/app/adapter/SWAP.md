# Swapping the domain

This directory is the **only** thing that changes when the real hackathon problem
is revealed. The core (`agent/`, `retrieval/`, `ml/`, `guardrails/`, `api/`,
`observability/`) imports the domain *exclusively* through `adapter/__init__.py`.
Keep those export names stable and the core keeps working.

The example domain shipped here is a neutral **service-request / case-management**
world — it is illustrative only, so the vertical slice runs before the real domain
is known.

## Retarget checklist (edit these files, in order)

1. **`schema.py`** — redefine the entities and enums for the new world. This is
   the vocabulary everything else shares.
2. **`ml_spec.py`** — declare the new `FEATURES` and `TARGET`, and rewrite
   `latent_resolution_hours` (rename it) to be the ground-truth signal for the new
   target. This module is the single source of truth for the predictable signal;
   the generator imports it, so the labels stay consistent by construction. Also
   re-voice `describe_prediction` so the injected evidence names the new target/unit.
3. **`generator.py`** — adjust the procedural draws and the LLM prompts in
   `_fabricate_request_text` / `_fabricate_documents` to the new records. Keep the
   hybrid pattern: seeded structure + LLM text + templated fallback.
4. **`tools.py`** — replace the action tools with the new domain's real actions.
   Keep them typed, idempotent, reversible, audited, and registered in
   `TOOL_REGISTRY`; update `ALLOWLIST`.
5. **`personas.py` + `prompts.py`** — re-voice the two personas and their data
   scope + system prompts.
6. **`corpus/`** — drop in the new seed `*.md` documents (same frontmatter keys).

## ML reshape points (predict-then-answer, non-gating)

ML is a **solution signal, not a flow decider**: the agent injects the prediction,
calibrated conformal interval and top SHAP drivers into its answer as supporting
evidence. It **never** gates, defers, or terminates a run — the human gate is driven
by tool **risk** only. There are three day-of reshape points:

- **Features / target / signal** → `adapter/ml_spec.py` (`FEATURES`, `TARGET`,
  the latent signal, `features_for_request`, `describe_prediction`). Adapter edit.
- **Estimators / ensemble members** → `ml/model.py` `_regression_members` /
  `_classification_members` (the soft-voting XGBoost + HistGradientBoosting ensemble;
  add a RandomForest / linear member or stack a meta-learner here). Core edit — the
  MAPIE conformal + SHAP plumbing adapts automatically.
- **When ML runs** → the agent calls it best-effort whenever `features_for_request`
  resolves a subject; return an empty feature dict to opt a query out of ML entirely.

## Invariants to preserve

- The public names re-exported by `__init__.py` (see its `__all__`).
- `generate_synthetic(config, *, complete=None)` returns a fully schema-valid
  `SyntheticDataset`, even with no LLM available (templated fallback).
- Tools call `app.data.record_audit` and honour `ALLOWLIST` via `run_tool`.
- `feature_matrix(dataset)` yields `(X, y)` the ML spine can train on.

Make **no** changes outside `adapter/` — new deps or env vars go in the final
report for the orchestrator to merge.
