# Swapping the domain

This directory is the **only** thing that changes when the real hackathon problem
is revealed. The core (`agent/`, `retrieval/`, `ml/`, `memory/`, `guardrails/`, `api/`,
`observability/`) imports the domain *exclusively* through `adapter/__init__.py`.
Keep those export names stable and the core keeps working.

The example domain shipped here is a neutral **service-request / case-management**
world — it is illustrative only, so the vertical slice runs before the real domain
is known.

## Retarget checklist (edit these files, in order)

Ten pieces: **eight modules** plus `corpus/` and `skills/`. The numbers are the
piece numbers each module's own docstring carries, and the order is the order to
edit them in. `__init__.py` is not a piece — it is the registry; keep its
`__all__` stable and the core keeps working.

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
5. **`personas.py`** — re-voice the two personas and their data scope.
6. **`prompts.py`** — re-voice the matching system prompts (paired with piece 5).
7. **`memory_spec.py`** — redefine what counts as a *durable fact* in the new
   domain, how it is extracted from a conversation, who it is scoped to, and how
   the profile reads. Nothing in `app/memory/*` changes — this is the only memory
   seam, exactly as `ml_spec.py` is the only ML seam.
8. **`roster.py`** — declare the specialists the supervisor may route to
   (`role`, `keywords`, `description`, exactly one `is_default`). Each `role` must
   match a graph specialist node; the core falls back to a `qa`-only roster if the
   contract is absent, so a wrong `role` degrades silently rather than failing —
   check the `routing` stream event after editing.
9. **`corpus/`** — drop in the new seed `*.md` documents (same frontmatter keys).
10. **`skills/`** — rewrite the procedural how-to-act playbooks (`*.md`). They are
    discovered from `memory_spec.SKILLS_DIR` and chosen per query by
    `memory_spec.select_skills`, so renaming a file means updating that selector.

## ML reshape points (a tenant capability, not an agent step)

**The agent graph runs no ML step.** ML is a capability this deployment *offers* —
served by `POST /ml/explain`, `GET /ml/model-card` and the admin forecast dashboard —
not a stage of the pipeline. It was taken out of the graph because it decorated a
decision it never made: nothing routed, gated or branched on its output. The human
gate is driven by tool **risk** alone (`ToolSpec.risk` vs `AgentConfig.gate_min_risk`).

Retarget it because the tenant's use case needs predicting, not because the agent
needs it. Two day-of reshape points:

- **Features / target / signal** → `adapter/ml_spec.py` (`FEATURES`, `TARGET`,
  the latent signal, `features_for_request`, `describe_prediction`). Adapter edit.
- **Estimators / ensemble members** → `ml/model.py` `_regression_members` /
  `_classification_members` (the soft-voting XGBoost + HistGradientBoosting ensemble;
  add a RandomForest / linear member or stack a meta-learner here). Core edit — the
  MAPIE conformal + SHAP plumbing adapts automatically.

## Invariants to preserve

- The public names re-exported by `__init__.py` (see its `__all__`).
- `generate_synthetic(config, *, complete=None)` returns a fully schema-valid
  `SyntheticDataset`, even with no LLM available (templated fallback).
- Tools call `app.data.record_audit` and honour `ALLOWLIST` via `run_tool`.
- `feature_matrix(dataset)` yields `(X, y)` the ML spine can train on.

Make **no** changes outside `adapter/` — new deps or env vars go in the final
report for the orchestrator to merge.
