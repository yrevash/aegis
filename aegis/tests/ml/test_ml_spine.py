"""Offline unit tests for the trustworthy-ML spine (domain-agnostic, spine-generic).

Everything trains on a tiny in-test synthetic dataset. No network, no live infra,
no domain adapter — the spine's lenient spec normalisation and built-in
synthesiser make it fully self-contained. Domain-specific parity tests (real
adapter spec, distinct predictions on real categorical features) live in the
backend's ``tests/ml`` and run through the strangler shim.
"""

from __future__ import annotations

import numpy as np

import aegis.ml as ml
from aegis.ml import predict_explain
from aegis.ml.model import TrustworthyModel
from aegis.ml.spec import FALLBACK_SPEC, ResolvedSpec, resolve_spec
from aegis.ml.types import MLExplainResponse, ShapFeature


# ── spec resolution ──────────────────────────────────────────────────────────
def test_resolve_spec_falls_back_when_none():
    """No spec injected ⇒ the self-contained fallback, no probing anywhere."""
    assert resolve_spec(None) is FALLBACK_SPEC
    assert resolve_spec() is FALLBACK_SPEC


def test_resolve_spec_normalises_explicit_object():
    """An adapter-shaped object is read leniently into a concrete ResolvedSpec."""

    class _Spec:
        features = ["a", "b"]
        target = "t"
        task = "classify"

    resolved = resolve_spec(_Spec())
    assert resolved.features == ["a", "b"]
    assert resolved.target == "t"
    assert resolved.task == "classification"


def test_resolve_spec_is_idempotent_on_a_resolved_spec(regression_spec):
    """A ResolvedSpec passed back in is returned unchanged (not misread)."""
    assert resolve_spec(regression_spec) is regression_spec


# ── regression: conformal interval + SHAP shapes ─────────────────────────────
def test_regression_conformal_interval_and_shap(regression_spec, regression_frame):
    model = TrustworthyModel.train(
        regression_spec, regression_frame, confidence_level=0.9, path=None
    )

    resp = model.predict_explain({"f0": 1.0, "f1": -1.0, "f2": 0.5})
    assert isinstance(resp, MLExplainResponse)

    # Conformal interval: present, correctly ordered, brackets the point.
    assert resp.conformal_interval is not None
    lower, upper = resp.conformal_interval
    assert lower <= upper
    assert lower <= resp.prediction <= upper
    assert resp.conformal_confidence == 0.9

    # SHAP: one signed attribution per feature.
    assert len(resp.shap_attribution) == len(regression_spec.features)
    assert all(isinstance(f, ShapFeature) for f in resp.shap_attribution)
    assert {f.feature for f in resp.shap_attribution} == set(regression_spec.features)
    # Sorted by descending absolute contribution.
    contribs = [abs(f.contribution) for f in resp.shap_attribution]
    assert contribs == sorted(contribs, reverse=True)


def test_regression_coverage_guarantee_holds(regression_spec, regression_frame):
    """The calibrated interval should cover held-out targets near the target rate."""
    train_df = regression_frame.iloc[:300]
    test_df = regression_frame.iloc[300:]
    model = TrustworthyModel.train(regression_spec, train_df, confidence_level=0.9, path=None)

    covered = 0
    for _, row in test_df.iterrows():
        resp = model.predict_explain({f: row[f] for f in regression_spec.features})
        lo, hi = resp.conformal_interval
        covered += lo <= row["y"] <= hi
    coverage = covered / len(test_df)
    # Marginal coverage is a statistical guarantee; allow finite-sample slack.
    assert coverage >= 0.80


# ── classification: prediction set path ──────────────────────────────────────
def test_classification_predict_explain(classification_spec, classification_frame):
    model = TrustworthyModel.train(
        classification_spec, classification_frame, confidence_level=0.9, path=None
    )
    resp = model.predict_explain({"f0": 1.5, "f1": -1.2, "f2": 0.3})

    assert isinstance(resp, MLExplainResponse)
    assert isinstance(resp.prediction, str)  # class label as string
    assert resp.conformal_interval is None  # no interval for classification
    assert resp.conformal_confidence == 0.9
    assert len(resp.shap_attribution) == len(classification_spec.features)


# ── inference robustness ─────────────────────────────────────────────────────
def test_missing_features_are_imputed(regression_spec, regression_frame):
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    # Empty dict → every feature imputed from training medians; must not raise.
    resp = model.predict_explain({})
    assert isinstance(resp, MLExplainResponse)
    assert len(resp.shap_attribution) == len(regression_spec.features)


# ── persistence round-trip ───────────────────────────────────────────────────
def test_save_and_load_roundtrip(regression_spec, regression_frame, tmp_path):
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    artifact = tmp_path / "spine.joblib"
    model.save(artifact)
    assert artifact.exists()

    reloaded = TrustworthyModel.load(artifact)
    inputs = {"f0": 0.7, "f1": -0.3, "f2": 0.1}
    a = model.predict_explain(inputs)
    b = reloaded.predict_explain(inputs)
    assert np.isclose(a.prediction, b.prediction)
    assert np.allclose(a.conformal_interval, b.conformal_interval)


# ── module-level contract (default synthetic spec) ───────────────────────────
def test_module_level_predict_explain_trains_fallback():
    ml._MODEL = None  # force cold start with no artifact injected
    resp = predict_explain({"feature_0": 0.5})
    assert isinstance(resp, MLExplainResponse)
    assert resp.conformal_confidence == 0.9
    assert resp.conformal_interval is not None


# ── synthesiser sanity ───────────────────────────────────────────────────────
def test_train_synthesises_when_no_frame():
    spec = ResolvedSpec(features=["x0", "x1"], target="out", task="regression")
    model = TrustworthyModel.train(spec, frame=None, path=None)
    resp = model.predict_explain({"x0": 1.0, "x1": 2.0})
    assert isinstance(resp.prediction, float)
