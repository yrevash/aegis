# aegis.ml — Trustworthy ML Spine (extraction) Design Spec

- **Date:** 2026-08-11
- **Branch:** `feat/aegis-module-contract`
- **Status:** Design (autonomous rollout — building without a review gate per user mandate)
- **Roadmap:** `docs/superpowers/plans/2026-08-11-aegis-module-rollout-roadmap.md` (module 1 of 8)

## 1. Goal

Extract the backend's ML spine into a standalone, importable **`aegis.ml`** — a domain-agnostic
"trustworthy ML" engine: a soft-voting **XGBoost + HistGradientBoosting ensemble**, **MAPIE split-conformal**
calibrated intervals/sets, and **SHAP** attributions aggregated back to original features. LLM-free.
Emits its work over the AG-UI spine (`shap_explanation`, `conformal_interval`). Heavy deps live under an
`aegis[ml]` extra; `aegis.core` stays heavy-dep-free. The legacy backend delegates via a strangler shim.

## 2. What's already true (from the extraction map)

- `app.ml` couples to `app.*` in only two places:
  1. `model.py` (and tests) import `MLExplainResponse` + `ShapFeature` from `app.api.schemas`.
  2. `spec.py:resolve_spec` does a hard `from app.adapter import ml_spec`.
- The spine is invoked purely through `PredictFn = Callable[[dict], MLExplainResponse]` (agent injects it via `AgentDeps`; the REST route calls it directly). So satisfying that callable is the whole contract.
- `dataset.py` is already clean (no `app.*` at runtime). Heavy deps (xgboost, sklearn, mapie, shap, joblib, pandas, numpy) live entirely in `model.py`/`dataset.py`.

## 3. Design

### 3.1 Package layout

```
aegis/src/aegis/ml/
  __init__.py     # train / load / get_model / predict_explain + re-exports
  types.py        # MLExplainResponse, ShapFeature  (pydantic-only; NO heavy deps)
  spec.py         # MLSpec Protocol, ResolvedSpec, TaskType, FALLBACK_SPEC, resolve_spec(spec) — INJECTED
  dataset.py      # synthesise_frame, resolve_training_frame  (moved verbatim)
  model.py        # TrustworthyModel (ensemble + MAPIE + SHAP), rebound imports
  stream.py       # stream_predict_explain(features, emitter, *, describe=None) -> MLExplainResponse
  artifacts/      # ml_spine.joblib default path (package-relative)
```

### 3.2 Sever the two couplings

- **Types → `aegis.ml.types`** (pydantic-only). `MLExplainResponse` (`prediction: float|str`, `conformal_interval: tuple[float,float]|None`, `conformal_confidence: float|None`, `interval_width: float|None`, `prediction_set_size: int|None`, `shap_attribution: list[ShapFeature]`) and `ShapFeature` (`feature: str`, `value: float`, `contribution: float`). `model.py` imports from `aegis.ml.types`. `app.api.schemas` re-exports `MLExplainResponse`/`ShapFeature` from `aegis.ml.types` (identity, so the agent + REST route are unchanged). Importing `aegis.ml.types` pulls NO heavy deps (only pydantic) — so `app.api.schemas` stays light.
- **Spec injection.** `resolve_spec(spec: MLSpec | None = None) -> ResolvedSpec` no longer imports `app.adapter`. When `spec is None` it returns `FALLBACK_SPEC` (no adapter probing). The domain passes its spec object explicitly. The backend shim calls `resolve_spec(app.adapter.ml_spec)` / `train(spec=app.adapter.ml_spec)`. `resolve_spec` reads the injected object leniently via `getattr` exactly as today (`FEATURE_NAMES`/`features`, `TARGET.name`/`target`, `TARGET.task`/`task`, `CATEGORICAL_FEATURES`/`FEATURES[].dtype`, `training_frame`/`frame_provider`).

### 3.3 AG-UI streaming (à la carte)

`aegis/src/aegis/ml/stream.py`:
```python
async def stream_predict_explain(features, emitter, *, model=None, describe=None) -> MLExplainResponse:
    async with emitter.step("ml_predict", SpanKind.CHAIN):
        resp = (model or get_model()).predict_explain(features)
        await emitter.custom(stream_names.CONFORMAL_INTERVAL, {
            "prediction": resp.prediction, "lower": lo, "upper": hi,
            "confidence": resp.conformal_confidence, "interval_width": resp.interval_width,
            "prediction_set_size": resp.prediction_set_size})
        await emitter.custom(stream_names.SHAP_EXPLANATION, {
            "prediction": resp.prediction,
            "features": [{"feature": f.feature, "value": f.value, "contribution": f.contribution}
                         for f in resp.shap_attribution]})
    return resp
```
`SHAP_EXPLANATION` + `CONFORMAL_INTERVAL` already exist in `aegis.core.stream_names`. No new names needed.

### 3.4 Honest infra

N/A — ml is pure compute. `get_model()` resolves singleton → `load(artifact)` → `train(fallback)`; the artifact
path is package-relative (`aegis/ml/artifacts/ml_spine.joblib`). No Redis/Postgres, so no silent-fallback risk.
The committed domain artifact is copied into the package; standalone users get a fallback-trained model or train their own.

### 3.5 Strangler shim

- `backend/src/app/ml/` becomes a thin shim: `app.ml.predict_explain` / `get_model` / `train` / `load` delegate to `aegis.ml`, wiring the domain spec (`app.adapter.ml_spec`) into `get_model`/`train`. `app.ml.spec`, `app.ml.model` re-export from `aegis.ml` for any importer.
- `app.api.schemas.MLExplainResponse` / `ShapFeature` re-export from `aegis.ml.types`.
- `python -m app.ml` (offline trainer) keeps working (delegates to `aegis.ml.train` with the domain spec).
- The backend's `AgentDeps` wiring is unchanged (still `from app.ml import predict_explain`).

## 4. Extras

Add to `aegis/pyproject.toml`: `ml = ["xgboost>=2.1", "scikit-learn>=1.5", "mapie>=1.4", "shap>=0.46", "pandas>=2.2,<2.4", "numpy>=1.26", "joblib>=1.4"]`; include in `all`. `require("aegis[ml]", ...)` is not needed at import (the package assumes its own extra installed); the dep-free guard ensures `aegis.core` never pulls these.

## 5. Testing & proof

1. **Spine-generic tests** (move to `aegis/tests/ml/`, fixtures-based, no adapter): resolve_spec normalises explicit object + falls back when spec None; regression conformal interval ordered + brackets prediction + confidence 0.9; coverage ≥ 0.80 held-out; classification returns str + no interval; missing features imputed; save/load round-trip; module-level predict_explain cold-start trains fallback; train synthesises when no frame.
2. **Streaming test:** `stream_predict_explain` emits `STEP_STARTED("ml_predict")` → `CUSTOM(conformal_interval)` → `CUSTOM(shap_explanation)` → `STEP_FINISHED`; payload fields asserted.
3. **Dep-free guard unchanged:** `aegis.core` still pulls no heavy deps; add a check that `aegis.ml.types` alone imports without xgboost/shap.
4. **Backend parity (shim):** the domain-specific tests (`test_resolve_spec_reads_the_real_domain_spec_from_adapter`, `test_domain_spine_predicts_distinctly_with_categoricals`) pass through the shim with the real adapter injected; full backend suite stays green (except the 2 known env failures).
5. **Live end-to-end:** the ml stream decodes on the frontend (shap_explanation + conformal_interval routed by name).

## 6. Definition of done

`aegis.ml` is importable and installable via `aegis[ml]`, produces `MLExplainResponse` from a features dict,
streams SHAP + conformal over the AG-UI spine, keeps `aegis.core` heavy-dep-free, and the legacy backend works
through the shim with the domain spec injected — full backend suite green (minus the 2 env failures).
