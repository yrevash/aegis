"""Regression guards for the spine's statistical-honesty bugs.

Each test here pins one defect that made the spine return a *confident-looking*
answer with no signal behind it — the failure mode that matters most, because the
whole product's trust story rests on this module's "calibrated confidence". Every
test in this file fails against the pre-fix implementation.

Everything trains offline on tiny in-test frames: no network, no domain adapter.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

import aegis.ml as ml
from aegis.ml.dataset import SYNTHETIC_LEVELS, synthesise_frame
from aegis.ml.model import TrustworthyModel, _encoded_parents, _min_calibration_rows
from aegis.ml.spec import ResolvedSpec


# ── Bug 1 · the synthetic fallback must never masquerade as a real model ──────
def test_training_on_synthetic_data_is_never_auto_persisted(tmp_path, caplog):
    """``train(path=...)`` must refuse to write a noise model to disk.

    A persisted artifact is loaded successfully by every later process, so a single
    silent synthetic write turns a one-off fallback into permanent fake evidence
    that nothing ever retrains away.
    """
    spec = ResolvedSpec(features=["x0", "x1"], target="out", task="regression")
    artifact = tmp_path / "spine.joblib"

    with caplog.at_level(logging.WARNING, logger="aegis.ml.model"):
        model = TrustworthyModel.train(spec, frame=None, path=artifact)

    assert model.data_source == "synthetic"
    assert not artifact.exists()  # THE guard
    assert "synthetic" in caplog.text.lower()


def test_a_real_model_is_still_persisted(tmp_path, regression_spec, regression_frame):
    """The refusal is scoped to synthetic data — real models still persist."""
    artifact = tmp_path / "spine.joblib"
    model = TrustworthyModel.train(regression_spec, regression_frame, path=artifact)
    assert model.data_source == "provided"
    assert artifact.exists()


def test_explicit_save_of_a_synthetic_model_still_announces_itself(tmp_path):
    """A deliberately hand-saved synthetic model still labels every prediction."""
    spec = ResolvedSpec(features=["x0", "x1"], target="out", task="regression")
    model = TrustworthyModel.train(spec, frame=None, path=None)
    artifact = tmp_path / "spine.joblib"
    model.save(artifact)

    reloaded = TrustworthyModel.load(artifact)
    resp = reloaded.predict_explain({"x0": 1.0, "x1": 2.0})
    assert resp.data_source == "synthetic"  # machine-readable, on the response
    assert reloaded.model_card().data_source == "synthetic"


def test_get_model_raises_rather_than_serving_noise(monkeypatch, tmp_path):
    """No cached model and no artifact ⇒ a typed error, never a trained fallback."""
    ml._MODEL = None
    monkeypatch.setattr(ml, "DEFAULT_ARTIFACT_PATH", tmp_path / "absent.joblib")
    with pytest.raises(ml.MLModelUnavailableError, match="synthetic noise"):
        ml.get_model()


# ── Bug 2 · binary classification must explain the class it predicted ─────────
def _binary_model() -> tuple[TrustworthyModel, pd.DataFrame]:
    """Fit a binary classifier on a strong, monotone single-feature signal."""
    rng = np.random.default_rng(7)
    x = pd.DataFrame(rng.normal(size=(400, 2)), columns=["f0", "f1"])
    frame = x.copy()
    frame["label"] = (x["f0"] > 0).astype(int)
    spec = ResolvedSpec(
        features=["f0", "f1"], target="label", task="classification"
    )
    return TrustworthyModel.train(spec, frame, path=None), x


def test_binary_drivers_point_at_the_predicted_class():
    """The top driver must push *toward* the label shown beside it, for both classes.

    Both boosters emit a single 2-D margin toward class 1 for binary problems, and
    the old ``values.ndim == 3`` re-index never fired, so a row predicted class 0
    came back with every driver's sign inverted relative to its own prediction.

    Here ``label = f0 > 0``, so a strongly negative ``f0`` is the reason a row is
    class 0 and its contribution to the *predicted* class must be positive.
    """
    model, x = _binary_model()

    for label, row in (("0", {"f0": -2.5, "f1": 0.0}), ("1", {"f0": 2.5, "f1": 0.0})):
        resp = model.predict_explain(row)
        assert resp.prediction == label
        top = resp.shap_attribution[0]
        assert top.feature == "f0"
        assert top.contribution > 0, (
            f"f0={row['f0']} is why this row is class {label}; its contribution to "
            f"the predicted class must be positive, got {top.contribution}"
        )
    _ = x


def test_binary_driver_signs_are_mirror_images_across_the_boundary():
    """Explaining class 0 is exactly explaining "not class 1" — signs must flip."""
    model, _ = _binary_model()
    negative = model.predict_explain({"f0": -2.5, "f1": 0.4})
    positive = model.predict_explain({"f0": 2.5, "f1": 0.4})
    assert negative.prediction == "0"
    assert positive.prediction == "1"
    by_name = {f.feature: f.contribution for f in negative.shap_attribution}
    assert by_name["f0"] > 0  # pre-fix this was the class-1 margin, i.e. negative


# ── Bug 3 · the synthesiser must be compatible with the preprocessor ──────────
def test_synthesised_categoricals_are_real_string_levels():
    """A categorical column must not be synthesised as 600 distinct floats."""
    spec = ResolvedSpec(
        features=["region", "tenure"],
        target="y",
        task="regression",
        categorical_features=["region"],
    )
    frame = synthesise_frame(spec, n_rows=200)

    assert list(frame.columns) == ["region", "tenure", "y"]  # declared order kept
    assert set(frame["region"].unique()) <= set(SYNTHETIC_LEVELS)
    assert frame["region"].nunique() <= len(SYNTHETIC_LEVELS)
    assert frame["tenure"].dtype.kind == "f"


def test_synthetic_fallback_encodes_to_a_sane_width_and_generalises():
    """One-hot must fit a handful of levels, not one degenerate level per row.

    Pre-fix, ``synthesise_frame`` emitted floats for ``region``, the encoder fitted
    600 single-row levels, and *every* inference row hit ``handle_unknown="ignore"``
    → an all-zero block the model never saw, while the conformal interval (calibrated
    on the same degenerate encoding) still advertised 90% coverage.
    """
    spec = ResolvedSpec(
        features=["region", "tenure"],
        target="y",
        task="regression",
        categorical_features=["region"],
    )
    model = TrustworthyModel.train(spec, frame=None, path=None)

    # 4 one-hot columns + 1 numeric passthrough — not one column per training row.
    assert len(model.encoded_names) == len(SYNTHETIC_LEVELS) + 1
    assert model.feature_modes["region"] in SYNTHETIC_LEVELS

    # Levels the model actually trained on produce genuinely distinct answers.
    predictions = {
        level: model.predict_explain({"region": level, "tenure": 0.0}).prediction
        for level in SYNTHETIC_LEVELS
    }
    assert len(set(predictions.values())) > 1


# ── Bug 4 · encoded→original attribution must not misattribute numerics ───────
def test_encoded_parents_does_not_swallow_a_numeric_name_prefixed_by_a_categorical():
    """``plan_age`` is a numeric passthrough, not a one-hot level of ``plan``."""
    frame = pd.DataFrame(
        {"plan": ["a", "b"] * 50, "plan_age": np.arange(100.0), "y": np.arange(100.0)}
    )
    spec = ResolvedSpec(
        features=["plan", "plan_age"],
        target="y",
        task="regression",
        categorical_features=["plan"],
    )
    preprocessor = TrustworthyModel._build_preprocessor(spec).fit(frame[spec.features])
    encoded_names = list(preprocessor.get_feature_names_out())

    parents = _encoded_parents(preprocessor, ["plan"], ["plan_age"], encoded_names)
    assert parents == ["plan", "plan", "plan_age"]  # NOT [..., "plan", "plan_age"]


def test_prefixed_numeric_feature_keeps_its_own_shap_contribution():
    """End-to-end: the numeric driver must not report 0.0 with its mass in ``plan``."""
    rng = np.random.default_rng(3)
    n = 400
    frame = pd.DataFrame(
        {"plan": rng.choice(["a", "b"], size=n), "plan_age": rng.normal(size=n)}
    )
    # plan_age is the dominant driver; plan is a weak one.
    frame["y"] = 5.0 * frame["plan_age"] + (frame["plan"] == "a") * 1.0
    spec = ResolvedSpec(
        features=["plan", "plan_age"],
        target="y",
        task="regression",
        categorical_features=["plan"],
    )
    model = TrustworthyModel.train(spec, frame, path=None)
    assert model.encoded_parents == ["plan", "plan", "plan_age"]

    resp = model.predict_explain({"plan": "a", "plan_age": 2.0})
    by_name = {f.feature: f.contribution for f in resp.shap_attribution}
    assert by_name["plan_age"] != 0.0  # pre-fix: exactly 0.0
    assert abs(by_name["plan_age"]) > abs(by_name["plan"])


# ── Bug 5 · requested coverage is not achieved coverage ───────────────────────
def test_model_card_separates_requested_from_measured_coverage(
    regression_spec, regression_frame
):
    """``conformal_coverage`` is the request; the measurement is its own field."""
    model = TrustworthyModel.train(
        regression_spec, regression_frame, confidence_level=0.9, path=None
    )
    card = model.model_card()

    assert card.conformal_coverage == 0.9  # what was asked for
    assert card.conformal_coverage_empirical is not None  # what was observed
    assert 0.0 <= card.conformal_coverage_empirical <= 1.0
    # The two are measured independently; the report must not just echo the request.
    assert card.test_size > 0


def test_model_card_reports_held_out_accuracy(regression_spec, regression_frame):
    """An R² ≈ 0 model must be distinguishable from a good one on the card alone."""
    good = TrustworthyModel.train(regression_spec, regression_frame, path=None).model_card()
    assert good.metric_name == "r2"
    assert good.metric_value is not None
    assert good.metric_value > 0.5

    # Same features, a target that is pure noise ⇒ the card says so.
    noise = regression_frame.copy()
    noise["y"] = np.random.default_rng(0).normal(size=len(noise))
    bad = TrustworthyModel.train(regression_spec, noise, path=None).model_card()
    assert bad.metric_value is not None
    assert bad.metric_value < 0.5


def test_classification_card_reports_accuracy_and_measured_coverage(
    classification_spec, classification_frame
):
    card = TrustworthyModel.train(
        classification_spec, classification_frame, path=None
    ).model_card()
    assert card.metric_name == "accuracy"
    assert card.metric_value is not None
    assert card.conformal_coverage_empirical is not None


def test_min_calibration_rows_matches_the_conformal_rank():
    """The floor is the smallest n for which ceil((n+1)·c) is a valid rank."""
    for level in (0.5, 0.8, 0.9, 0.95, 0.99):
        n = _min_calibration_rows(level)
        assert np.ceil((n + 1) * level) <= n
        assert n == 1 or np.ceil(n * level) > n - 1


def test_too_few_calibration_rows_is_rejected_not_silently_served(regression_spec):
    """A 20-row frame gives ~4 calibration points: a 0.9 interval is unattainable."""
    rng = np.random.default_rng(0)
    tiny = pd.DataFrame(rng.normal(size=(20, 3)), columns=regression_spec.features)
    tiny["y"] = rng.normal(size=20)
    with pytest.raises(ValueError, match="unattainable"):
        TrustworthyModel.train(regression_spec, tiny, confidence_level=0.9, path=None)


def test_classification_splits_are_stratified_so_rare_classes_reach_calibration(
    monkeypatch,
):
    """A rare class must reach calibration, or its conformal sets carry no guarantee.

    MAPIE calibrates on ``y_cal`` alone, so a class with no calibration residual has
    no conformity score and its prediction sets are invalid — silently. This spies on
    the exact ``y_cal`` handed to :meth:`_build_conformal` and proves every seed sees
    the 3% minority class, then shows that an unstratified split of the same frame
    genuinely drops it for some of those seeds (i.e. the bug was real, not theoretical).
    """
    from sklearn.model_selection import train_test_split as sk_split

    n, seeds = 300, range(12)
    rng = np.random.default_rng(11)
    frame = pd.DataFrame(rng.normal(size=(n, 2)), columns=["a", "b"])
    labels = np.zeros(n, dtype=int)
    labels[:5] = 1  # ~1.7% minority
    frame["label"] = labels
    spec = ResolvedSpec(features=["a", "b"], target="label", task="classification")

    seen: list[set[int]] = []
    original = TrustworthyModel._build_conformal

    def _spy(task, estimator, confidence_level, x_cal, y_cal):
        seen.append(set(y_cal))
        return original(task, estimator, confidence_level, x_cal, y_cal)

    monkeypatch.setattr(TrustworthyModel, "_build_conformal", staticmethod(_spy))
    for random_state in seeds:
        TrustworthyModel.train(spec, frame, random_state=random_state, path=None)

    assert len(seen) == len(seeds)
    assert all(calibrated == {0, 1} for calibrated in seen)  # THE guard

    # The unstratified split this replaced really does lose the minority class.
    dropped = sum(
        1 not in set(sk_split(frame, frame["label"], test_size=0.25, random_state=rs)[3])
        for rs in seeds
    )
    assert dropped > 0


# ── Bug 6 · imputation must be visible in the response ────────────────────────
def test_empty_input_is_reported_as_fully_imputed(regression_spec, regression_frame):
    """``predict_explain({})`` answers about the median row and must say so."""
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    resp = model.predict_explain({})
    assert resp.imputed_features == regression_spec.features
    assert resp.unknown_features == []


def test_typoed_feature_names_are_reported_not_silently_ignored(
    regression_spec, regression_frame
):
    """A caller who mistyped every key must be able to tell from the response."""
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    resp = model.predict_explain({"f0_typo": 1.0, "f1": 2.0})

    assert resp.unknown_features == ["f0_typo"]
    assert set(resp.imputed_features) == {"f0", "f2"}  # f1 was genuinely used
    assert resp.data_source == "provided"


def test_uncoercible_and_none_values_count_as_imputed(regression_spec, regression_frame):
    """A value that cannot become a float falls back to the median — visibly."""
    model = TrustworthyModel.train(regression_spec, regression_frame, path=None)
    resp = model.predict_explain({"f0": "not-a-number", "f1": None, "f2": 0.5})
    assert set(resp.imputed_features) == {"f0", "f1"}


# ── Bug 7 · a categorical driver must name its level ──────────────────────────
def test_categorical_shap_row_names_the_level_that_drove_it():
    """``region = 1.0`` says nothing; the response must carry ``"emea"``."""
    rng = np.random.default_rng(5)
    n = 400
    frame = pd.DataFrame(
        {
            "region": rng.choice(["na", "emea", "apac"], size=n),
            "tenure": rng.normal(size=n),
        }
    )
    frame["y"] = (frame["region"] == "emea") * 4.0 + frame["tenure"]
    spec = ResolvedSpec(
        features=["region", "tenure"],
        target="y",
        task="regression",
        categorical_features=["region"],
    )
    model = TrustworthyModel.train(spec, frame, path=None)

    resp = model.predict_explain({"region": "emea", "tenure": 0.5})
    by_name = {f.feature: f for f in resp.shap_attribution}
    assert by_name["region"].value_label == "emea"
    assert by_name["region"].value == 1.0  # one-hot indicator, unchanged
    assert by_name["tenure"].value_label is None  # numerics report their real value
    assert by_name["tenure"].value == 0.5

    # A different level is reported as a different level, not as another 1.0.
    other = model.predict_explain({"region": "apac", "tenure": 0.5})
    assert {f.feature: f.value_label for f in other.shap_attribution}["region"] == "apac"
