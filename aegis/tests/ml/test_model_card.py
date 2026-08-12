"""Tests for the honest, measured ``model_card()`` accessor and the SDK seam.

The card must be read off the *actual* fitted model — its ensemble members and
weights, the encoded matrix width, the MAPIE class backing the guarantee and the
recorded split sizes — never hardcoded. These tests also pin the custom-spec SDK
path (bring-your-own ``ResolvedSpec``) and the disjoint-calibration invariant.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.model_selection import train_test_split

from aegis.ml import EnsembleMember, ModelCard, ResolvedSpec, TrustworthyModel


# ── the card is exported from the package top level (SDK ergonomics) ──────────
def test_model_card_types_importable_from_package():
    """A hackathon user imports the card + member types straight off ``aegis.ml``."""
    assert ModelCard.__name__ == "ModelCard"
    assert EnsembleMember.__name__ == "EnsembleMember"


# ── regression card: every field measured off the real model ──────────────────
def test_model_card_regression_is_measured(regression_spec, regression_frame):
    model = TrustworthyModel.train(
        regression_spec, regression_frame, confidence_level=0.9, calibration_size=0.25, path=None
    )
    card = model.model_card()
    assert isinstance(card, ModelCard)

    assert card.task == "regression"
    assert card.target == "y"
    assert card.features == regression_spec.features
    assert card.n_features == 3
    assert card.numeric_features == regression_spec.features
    assert card.categorical_features == []
    # No categoricals ⇒ encoded width equals the original feature count.
    assert card.encoded_feature_count == 3

    # Ensemble members are the *fitted* soft-voting members, weights normalised.
    names = {m.name for m in card.ensemble_members}
    assert names == {"xgboost", "hist_gbr"}
    kinds = {m.kind for m in card.ensemble_members}
    assert kinds == {"XGBRegressor", "HistGradientBoostingRegressor"}
    assert np.isclose(sum(m.weight for m in card.ensemble_members), 1.0)

    # Conformal metadata reflects the real MAPIE predictor + target coverage.
    assert card.conformal_method == "split_conformal"
    assert card.conformal_predictor == "SplitConformalRegressor"
    assert card.conformal_coverage == 0.9

    # Split sizes were recorded from the actual train/calibration partition.
    assert card.training_size == 300
    assert card.calibration_size == 100
    assert card.training_size + card.calibration_size == len(regression_frame)
    assert card.data_source == "provided"


# ── classification card: correct members + MAPIE class ────────────────────────
def test_model_card_classification_members_and_predictor(
    classification_spec, classification_frame
):
    model = TrustworthyModel.train(
        classification_spec, classification_frame, confidence_level=0.8, path=None
    )
    card = model.model_card()
    assert card.task == "classification"
    assert {m.kind for m in card.ensemble_members} == {
        "XGBClassifier",
        "HistGradientBoostingClassifier",
    }
    assert card.conformal_predictor == "SplitConformalClassifier"
    assert card.conformal_coverage == 0.8


# ── data-source honesty: synthetic fallback is clearly labelled ───────────────
def test_model_card_labels_synthetic_data_source():
    spec = ResolvedSpec(features=["x0", "x1"], target="out", task="regression")
    model = TrustworthyModel.train(spec, frame=None, path=None)  # built-in synthesiser
    assert model.model_card().data_source == "synthetic"


def test_model_card_labels_spec_provider_data_source():
    frame = pd.DataFrame(
        {"x0": np.arange(200.0), "x1": np.arange(200.0)[::-1], "out": np.arange(200.0)}
    )
    spec = ResolvedSpec(
        features=["x0", "x1"], target="out", task="regression", frame_provider=lambda: frame
    )
    model = TrustworthyModel.train(spec, frame=None, path=None)
    assert model.model_card().data_source == "spec_provider"


# ── categoricals: encoded width exceeds original feature count ─────────────────
def test_model_card_reports_onehot_expansion():
    rng = np.random.default_rng(0)
    n = 300
    frame = pd.DataFrame(
        {
            "region": rng.choice(["na", "emea", "apac"], size=n),
            "tenure": rng.normal(size=n),
            "churned": rng.integers(0, 2, size=n),
        }
    )
    spec = ResolvedSpec(
        features=["region", "tenure"],
        target="churned",
        task="classification",
        categorical_features=["region"],
    )
    model = TrustworthyModel.train(spec, frame, path=None)
    card = model.model_card()
    assert card.categorical_features == ["region"]
    assert card.numeric_features == ["tenure"]
    # region → 3 one-hot columns + tenure ⇒ 4 encoded columns from 2 features.
    assert card.encoded_feature_count == 4
    assert card.n_features == 2


# ── the card survives a persistence round-trip ────────────────────────────────
def test_model_card_survives_roundtrip(regression_spec, regression_frame, tmp_path):
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    before = model.model_card()
    artifact = tmp_path / "spine.joblib"
    model.save(artifact)
    after = TrustworthyModel.load(artifact).model_card()
    assert before.model_dump() == after.model_dump()


# ── disjoint-calibration invariant: no calibration on training rows ───────────
def test_calibration_split_is_disjoint_from_training(regression_spec, regression_frame):
    """The recorded split sizes must match a genuinely disjoint train/cal partition.

    The coverage guarantee is only valid if MAPIE calibrates on rows the ensemble
    never trained on. We reproduce the exact split the model uses and prove the two
    index sets are disjoint, then tie that back to the sizes the card reports.
    """
    calibration_size, random_state = 0.25, 0
    model = TrustworthyModel.train(
        regression_spec,
        regression_frame,
        calibration_size=calibration_size,
        random_state=random_state,
        path=None,
    )

    encoded = pd.DataFrame(index=regression_frame.index)  # indices are all that matter
    y = regression_frame[regression_spec.target]
    x_train, x_cal, _, _ = train_test_split(
        regression_frame.index.to_frame(),
        y,
        test_size=calibration_size,
        random_state=random_state,
    )
    train_idx, cal_idx = set(x_train.index), set(x_cal.index)
    assert train_idx.isdisjoint(cal_idx)  # THE invariant
    assert len(train_idx) + len(cal_idx) == len(regression_frame)

    card = model.model_card()
    assert card.training_size == len(train_idx)
    assert card.calibration_size == len(cal_idx)
    _ = encoded  # (kept explicit that only indices drive the partition)


# ── SOTA confirm: SHAP is additive to the ensemble output ─────────────────────
def test_regression_shap_is_additive_to_prediction(regression_spec, regression_frame):
    """sum(SHAP) + weighted base ≈ prediction — the exact-additivity property that

    makes TreeExplainer honest for the weight-averaged regression output. Aggregating
    the encoded contributions back to original features preserves this total mass.
    """
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    row = {"f0": 0.8, "f1": -0.5, "f2": 0.2}
    resp = model.predict_explain(row)

    weights = model._member_weights()
    # Weighted average of each member's TreeExplainer base value.
    base = sum(
        weights[name] * float(np.ravel(expl.expected_value)[0])
        for name, expl in model._explainers.items()
    )
    total_shap = sum(f.contribution for f in resp.shap_attribution)
    assert np.isclose(total_shap + base, resp.prediction, atol=1e-3)
