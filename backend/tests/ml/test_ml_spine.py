"""Offline unit tests for the trustworthy-ML spine.

Everything trains on a tiny in-test synthetic dataset. No network, no live
infra, no API keys, no adapter — the spine's defensive spec resolution and
built-in synthesiser make it fully self-contained.
"""

from __future__ import annotations

import sys

import numpy as np

from app.api.schemas import MLExplainResponse, ShapFeature
from app.ml import predict_explain
from app.ml.model import TrustworthyModel
from app.ml.spec import FALLBACK_SPEC, ResolvedSpec, resolve_spec


# ── spec resolution ──────────────────────────────────────────────────────────
def test_resolve_spec_reads_the_real_domain_spec_from_adapter():
    """Regression guard: the adapter's real contract must NOT be silently dropped.

    Previously ``resolve_spec`` read a lowercase ``features``/``target`` the adapter
    never exposed, so it silently fell back to generic-noise ``FALLBACK_SPEC`` and
    the spine trained on the wrong data. It must now resolve the domain spec.
    """
    resolved = resolve_spec()
    assert resolved is not FALLBACK_SPEC
    assert resolved.target == "resolution_hours"
    assert resolved.task == "regression"
    # The domain's categorical features are recognised (so they get one-hot encoded).
    assert set(resolved.categorical_features) == {
        "priority",
        "category",
        "channel",
        "region",
        "customer_tier",
    }
    assert "agent_tenure_months" in resolved.numeric_features
    assert resolved.frame_provider is not None  # trains on the real domain frame


def test_resolve_spec_falls_back_when_adapter_absent(monkeypatch):
    # Simulate a build where the adapter's ml_spec cannot be imported at all.
    monkeypatch.setitem(sys.modules, "app.adapter", None)
    assert resolve_spec() is FALLBACK_SPEC


def test_resolve_spec_normalises_explicit_object():
    class _Spec:
        features = ["a", "b"]
        target = "t"
        task = "classify"

    resolved = resolve_spec(_Spec())
    assert resolved.features == ["a", "b"]
    assert resolved.target == "t"
    assert resolved.task == "classification"


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
    assert resp.conformal_interval is None   # no interval for classification
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
    import app.ml as ml

    ml._MODEL = None  # force cold start with no artifact/adapter
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


# ── domain wiring: the spine trains on the REAL domain data (not generic noise) ──
def test_domain_spine_predicts_distinctly_with_categoricals():
    """End-to-end guard on the CRITICAL honesty fix.

    Trains the spine on the adapter's real (categorical + numeric) feature frame and
    proves it (a) one-hot-encodes the categoricals, (b) reports SHAP over the 9
    ORIGINAL features (not the encoded columns), and (c) returns genuinely DISTINCT
    predictions for a clearly-easy vs clearly-hard request — the exact failure the
    audit caught, where the model returned a constant because it trained on the
    wrong (generic-noise) data.
    """
    from app.adapter import training_frame

    spec = resolve_spec()  # the real adapter spec
    frame = training_frame(num_requests=400)
    model = TrustworthyModel.train(spec, frame, confidence_level=0.9, path=None)

    easy = model.predict_explain(
        {
            "priority": "urgent", "category": "general", "channel": "chat",
            "region": "na", "customer_tier": "enterprise", "agent_tenure_months": 60,
            "queue_depth_at_open": 0, "reopened_count": 0, "description_length": 50,
        }
    )
    hard = model.predict_explain(
        {
            "priority": "low", "category": "technical", "channel": "email",
            "region": "apac", "customer_tier": "standard", "agent_tenure_months": 1,
            "queue_depth_at_open": 40, "reopened_count": 2, "description_length": 1200,
        }
    )

    # SHAP is reported per ORIGINAL feature, not the ~24 encoded columns.
    assert {f.feature for f in easy.shap_attribution} == set(spec.features)
    assert len(easy.shap_attribution) == 9
    # A harder request must take materially longer than an easy one — not a constant.
    assert hard.prediction - easy.prediction > 10.0
    # Calibrated interval present and ordered.
    lo, hi = hard.conformal_interval
    assert lo <= hard.prediction <= hi
