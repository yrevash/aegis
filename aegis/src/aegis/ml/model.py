"""Trustworthy-ML spine: a stacked/soft-voting **ensemble** + MAPIE conformal + SHAP.

This is the statistically-honest prediction core of the platform, and it is a
domain-**agnostic** solution component: *what* to predict (features, target, task)
comes from an injected spec; *how* to predict, calibrate and explain lives here.

A single :class:`TrustworthyModel` bundles three verified components:

* **A soft-voting ensemble** — a genuine ensemble of complementary CPU-only tree
  learners combined by averaging: gradient boosting via **XGBoost** plus a
  histogram gradient-boosting learner (:class:`~sklearn.ensemble.HistGradientBoostingRegressor`
  / ``…Classifier``). Averaging two well-regularised but differently-implemented
  boosters reduces variance versus either alone while staying light (16 GB, no GPU).
  Swapping in / adding members (RandomForest, a linear model, a stacking
  meta-learner) is a one-function edit — see :func:`_regression_members` /
  :func:`_classification_members` (the **estimator reshape point**).
* **MAPIE split conformal prediction** — wraps the *already-fitted* ensemble and,
  using a held-out calibration split, produces prediction intervals (regression)
  or prediction sets (classification) with a **guaranteed marginal coverage**
  equal to ``confidence_level``. This calibrated interval is the honest uncertainty
  the agent surfaces as supporting evidence ("predicts X, 90% coverage").
* **SHAP TreeExplainer (per member)** — exact per-feature attributions computed for
  each tree member and **averaged with the ensemble's member weights**, so the
  drivers explain the ensemble's output (exact for the averaged regression output;
  a per-member mean approximation in classification margin space).

The ensemble is a **solution signal only** — the agent injects the prediction,
calibrated interval and top SHAP drivers into its answer context as evidence. It
never gates, defers, or terminates a run (that decision is risk-tier driven in the
core); a failed or low-confidence prediction is simply omitted, best-effort.

Targeted library versions (introspected & smoke-tested on 2026-08-05):

* xgboost 3.3 — ``XGBRegressor``/``XGBClassifier`` (``.fit``, ``.predict``,
  ``.predict_proba``, ``.classes_``).
* scikit-learn 1.9 — ``VotingRegressor``/``VotingClassifier`` (soft voting;
  ``named_estimators_`` exposes the fitted members for SHAP) and
  ``HistGradientBoosting{Regressor,Classifier}``.
* mapie 1.4 — ``mapie.regression.SplitConformalRegressor`` and
  ``mapie.classification.SplitConformalClassifier``. Both take
  ``estimator``, ``confidence_level`` and ``prefit=True``; the flow is
  ``conformalize(X_cal, y_cal)`` then ``predict_interval(X)`` →
  ``(pred (n,), intervals (n, 2, n_levels))`` or ``predict_set(X)`` →
  ``(pred (n,), sets (n, n_classes, n_levels))``.
* shap 0.52 — ``shap.TreeExplainer(member).shap_values(X)`` → ``(n, n_features)``
  for regression / binary, ``(n, n_features, n_classes)`` for multiclass. Works on
  XGBoost and sklearn histogram / forest boosters alike.
"""

from __future__ import annotations

import logging
import warnings
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

import joblib
import numpy as np
import pandas as pd
import shap
from mapie.classification import SplitConformalClassifier
from mapie.regression import SplitConformalRegressor
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    VotingClassifier,
    VotingRegressor,
)
from sklearn.metrics import accuracy_score, r2_score
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier, XGBRegressor

from aegis.ml.dataset import resolve_training_frame
from aegis.ml.spec import ResolvedSpec, TaskType, resolve_spec
from aegis.ml.types import EnsembleMember, MLExplainResponse, ModelCard, ShapFeature

if TYPE_CHECKING:
    from aegis.ml.spec import MLSpec

logger = logging.getLogger(__name__)

DEFAULT_ARTIFACT_PATH = Path(__file__).resolve().parent / "artifacts" / "ml_spine.joblib"
"""Portable, package-relative default location for the persisted model."""

# Shared, light, CPU-only XGBoost hyper-parameters (no GPU, single-threaded so the
# spine stays deterministic and sub-millisecond on the hot path).
_XGB_PARAMS: dict[str, Any] = {
    "n_estimators": 200,
    "max_depth": 4,
    "learning_rate": 0.1,
    "subsample": 0.9,
    "n_jobs": 1,
    "tree_method": "hist",
}

# Shared histogram gradient-boosting hyper-parameters — the complementary learner.
_HGB_PARAMS: dict[str, Any] = {
    "max_iter": 200,
    "max_depth": 4,
    "learning_rate": 0.1,
}


# ─────────────────────────────────────────────────────────────────────────────
# Estimator reshape point — swap/add ensemble members here (day-of edit).
# Each member must be a tree model SHAP's TreeExplainer supports (XGBoost, sklearn
# HistGradientBoosting / RandomForest). To add a RandomForest or stack a meta-learner,
# edit only these two functions; the conformal + SHAP plumbing adapts automatically.
# ─────────────────────────────────────────────────────────────────────────────
def _regression_members(random_state: int) -> list[tuple[str, Any]]:
    """Return the ``(name, estimator)`` members of the regression ensemble."""
    return [
        ("xgboost", XGBRegressor(random_state=random_state, **_XGB_PARAMS)),
        (
            "hist_gbr",
            HistGradientBoostingRegressor(random_state=random_state, **_HGB_PARAMS),
        ),
    ]


def _classification_members(random_state: int) -> list[tuple[str, Any]]:
    """Return the ``(name, estimator)`` members of the classification ensemble."""
    return [
        (
            "xgboost",
            XGBClassifier(
                random_state=random_state, eval_metric="logloss", **_XGB_PARAMS
            ),
        ),
        (
            "hist_gbc",
            HistGradientBoostingClassifier(random_state=random_state, **_HGB_PARAMS),
        ),
    ]


def _encoded_parents(
    preprocessor: ColumnTransformer,
    categorical_features: list[str],
    numeric_features: list[str],
    encoded_names: list[str],
) -> list[str]:
    """Map each encoded column to the original feature it derives from.

    The map is derived **structurally** from the fitted preprocessor's column
    layout — the one-hot block emits ``len(categories_[i])`` columns for the
    *i*-th categorical, in declared order, followed by the numeric passthroughs —
    never by matching column-name prefixes. Name matching is ambiguous and
    silently wrong: with a categorical ``"plan"`` and a numeric ``"plan_age"`` the
    passthrough column ``plan_age`` starts with ``"plan_"``, so its whole SHAP
    contribution would be folded into ``plan`` and ``plan_age`` would report 0.0.

    Args:
        preprocessor: The fitted ``ColumnTransformer`` from :meth:`_build_preprocessor`.
        categorical_features: The original categorical feature names, in the order
            they were handed to the one-hot transformer.
        numeric_features: The original numeric (passthrough) feature names, in order.
        encoded_names: Column names emitted by the fitted preprocessor (used only
            to assert the derived layout really matches).

    Returns:
        A list parallel to ``encoded_names`` giving each column's parent feature.

    Raises:
        ValueError: If the derived layout does not match ``encoded_names``, which
            would mean the preprocessor's shape changed and aggregation could no
            longer be trusted.
    """
    parents: list[str] = []
    encoder = preprocessor.named_transformers_.get("cat")
    categories = getattr(encoder, "categories_", None) or []
    for name, levels in zip(categorical_features, categories, strict=True):
        parents.extend([name] * len(levels))
    parents.extend(numeric_features)
    if len(parents) != len(encoded_names):
        msg = (
            f"Encoded layout mismatch: derived {len(parents)} parent columns but the "
            f"preprocessor emitted {len(encoded_names)}; SHAP aggregation would be wrong."
        )
        raise ValueError(msg)
    return parents


def _level_label(value: object) -> str | None:
    """Return a categorical level as a display string (``None`` when absent)."""
    return None if value is None or pd.isna(value) else str(value)


def _min_calibration_rows(confidence_level: float) -> int:
    """Smallest calibration set for which ``confidence_level`` is even attainable.

    Split conformal takes the ``ceil((n + 1) * confidence_level)``-th smallest
    calibration residual as the interval half-width. When that rank exceeds ``n``
    no finite quantile exists, so the requested level is unreachable no matter what
    the data looks like — a 5-row calibration split can never support a 0.9
    interval. This returns the smallest ``n`` for which the rank is in range.

    Args:
        confidence_level: Requested marginal coverage, strictly in ``(0, 1)``.

    Returns:
        The minimum number of calibration rows.

    Raises:
        ValueError: If ``confidence_level`` is not strictly between 0 and 1.
    """
    if not 0.0 < confidence_level < 1.0:
        msg = f"confidence_level must be in (0, 1); got {confidence_level!r}"
        raise ValueError(msg)
    n = 1
    while np.ceil((n + 1) * confidence_level) > n:
        n += 1
    return n


@dataclass
class TrustworthyModel:
    """A fitted, conformalised, explainable **ensemble** over one domain spec.

    Instances are produced by :meth:`train` and persisted with :meth:`save` /
    reloaded with :meth:`load`. The heavy SHAP explainers are rebuilt lazily and
    never pickled (see :meth:`__getstate__`), keeping artifacts small and
    portable across machines.

    Attributes:
        estimator: The fitted soft-voting ensemble (``VotingRegressor`` /
            ``VotingClassifier``) whose members are exposed via
            ``named_estimators_``. It is fitted on the **encoded** matrix.
        conformal: The MAPIE conformal predictor calibrated on a held-out split.
        preprocessor: The fitted ``ColumnTransformer`` (one-hot for categoricals,
            passthrough for numerics) that maps a raw feature row to the encoded
            matrix the estimator/conformal/SHAP all operate on.
        feature_names: The **original** feature columns callers pass in (the SHAP
            attribution is reported per original feature, categoricals aggregated).
        categorical_features: Original features one-hot-encoded by the preprocessor.
        numeric_features: Original features passed through as numeric.
        encoded_names: Column names of the encoded matrix (post-preprocessing).
        encoded_parents: For each encoded column, the original feature it derives
            from — the map used to aggregate SHAP back to original features.
        target: Name of the predicted column.
        task: ``"regression"`` or ``"classification"``.
        confidence_level: Guaranteed target coverage of the conformal predictor.
        feature_medians: Per-numeric-feature training medians used to impute missing
            numeric inputs at inference time.
        feature_modes: Per-categorical-feature training mode used to impute missing
            categorical inputs at inference time.
        training_n: Rows the ensemble was fitted on (recorded for the model card).
        calibration_n: Rows in the disjoint calibration split fed to MAPIE.
        data_source: How the training frame was sourced — ``"provided"`` (caller
            frame), ``"spec_provider"`` (the spec's own frame provider) or
            ``"synthetic"`` (the built-in fallback synthesiser). Labels a
            synthetic-fallback model so it is never mistaken for a real one.
        test_n: Rows in the held-out test split — fitted on by nothing and
            calibrated on by nothing, so the metrics below are honest.
        metric_name: ``"r2"`` (regression) or ``"accuracy"`` (classification);
            ``None`` when no test split was held out.
        metric_value: The measured value of ``metric_name`` on the test split.
        empirical_coverage: The **measured** fraction of test rows whose true
            target fell inside the conformal interval / prediction set. This is
            the only coverage number that is an observation rather than a request.
    """

    estimator: VotingRegressor | VotingClassifier
    conformal: SplitConformalRegressor | SplitConformalClassifier
    preprocessor: ColumnTransformer
    feature_names: list[str]
    categorical_features: list[str]
    numeric_features: list[str]
    encoded_names: list[str]
    encoded_parents: list[str]
    target: str
    task: TaskType
    confidence_level: float
    feature_medians: dict[str, float] = field(default_factory=dict)
    feature_modes: dict[str, str] = field(default_factory=dict)
    training_n: int = 0
    calibration_n: int = 0
    data_source: str = "synthetic"
    test_n: int = 0
    metric_name: str | None = None
    metric_value: float | None = None
    empirical_coverage: float | None = None

    # ── construction ────────────────────────────────────────────────────────
    @classmethod
    def train(
        cls,
        spec: MLSpec | None = None,
        frame: pd.DataFrame | None = None,
        *,
        confidence_level: float = 0.9,
        calibration_size: float = 0.25,
        test_size: float = 0.2,
        random_state: int = 0,
        path: Path | str | None = None,
    ) -> TrustworthyModel:
        """Fit, conformalise, **evaluate** and (optionally) persist an ensemble.

        The data is split three ways: a held-out **test** set (touched by neither
        fitting nor calibration, used to measure accuracy and *empirical* coverage),
        then the remainder into a training set (fits the ensemble) and a disjoint
        calibration set (fed to MAPIE). A dedicated calibration split is what makes
        the coverage guarantee valid — never calibrate on training rows — and a
        dedicated test split is what makes the reported numbers *measurements*
        rather than restatements of what was requested.

        Classification splits are **stratified** on the label, so a rare class can
        never be absent from calibration (which would silently invalidate its
        conformal sets).

        Args:
            spec: Domain spec; resolved via :func:`resolve_spec` when ``None``.
            frame: Explicit training frame; synthesised when ``None``.
            confidence_level: Target marginal coverage (e.g. ``0.9`` → 90%).
            calibration_size: Fraction of the non-test rows reserved for calibration.
            test_size: Fraction of all rows held out for measurement; ``0`` skips
                the test split (and leaves the measured metrics ``None``).
            random_state: Seed for the splits and the estimator.
            path: If given, the fitted model is saved there via :meth:`save` —
                **unless** it was trained on synthetic data, which is never
                auto-persisted (see :meth:`save`).

        Returns:
            A ready-to-serve :class:`TrustworthyModel`.

        Raises:
            ValueError: If the calibration split is too small for
                ``confidence_level`` to be attainable (see
                :func:`_min_calibration_rows`).
        """
        resolved: ResolvedSpec = resolve_spec(spec)
        # Label the provenance honestly (mirrors resolve_training_frame's priority):
        # an explicit caller frame, else the spec's own provider, else the synthesiser.
        if frame is not None:
            data_source = "provided"
        elif resolved.frame_provider is not None:
            data_source = "spec_provider"
        else:
            data_source = "synthetic"
        data = resolve_training_frame(resolved, frame, random_state=random_state)

        raw_x = data[resolved.features]
        y = data[resolved.target]

        # Fit the encoder on the full feature vocabulary (categories are not
        # target-dependent, so this leaks no label information) and encode once.
        preprocessor = cls._build_preprocessor(resolved).fit(raw_x)
        encoded_names = list(preprocessor.get_feature_names_out())
        encoded = pd.DataFrame(
            preprocessor.transform(raw_x), columns=encoded_names, index=raw_x.index
        )
        encoded_parents = _encoded_parents(
            preprocessor,
            list(resolved.categorical_features),
            resolved.numeric_features,
            encoded_names,
        )

        stratify = resolved.task == "classification"
        if test_size > 0:
            x_fit, x_test, y_fit, y_test = cls._split(
                encoded, y, test_size, random_state, stratify=stratify
            )
        else:
            x_fit, y_fit = encoded, y
            x_test, y_test = encoded.iloc[:0], y.iloc[:0]
        x_train, x_cal, y_train, y_cal = cls._split(
            x_fit, y_fit, calibration_size, random_state, stratify=stratify
        )

        minimum = _min_calibration_rows(confidence_level)
        if len(x_cal) < minimum:
            msg = (
                f"Calibration split has {len(x_cal)} rows but a "
                f"{confidence_level:.0%} conformal level needs at least {minimum}: "
                "the requested coverage is unattainable. Supply more training rows, "
                "raise calibration_size, or lower confidence_level."
            )
            raise ValueError(msg)

        estimator = cls._build_estimator(resolved.task, random_state)
        estimator.fit(x_train, y_train)
        conformal = cls._build_conformal(
            resolved.task, estimator, confidence_level, x_cal, y_cal
        )
        metric_name, metric_value, empirical_coverage = cls._evaluate(
            resolved.task, estimator, conformal, x_test, y_test
        )

        numeric = resolved.numeric_features
        medians = {
            c: float(pd.to_numeric(raw_x[c], errors="coerce").median()) for c in numeric
        }
        modes = {
            c: str(raw_x[c].mode().iloc[0])
            for c in resolved.categorical_features
            if not raw_x[c].mode().empty
        }

        model = cls(
            estimator=estimator,
            conformal=conformal,
            preprocessor=preprocessor,
            feature_names=list(resolved.features),
            categorical_features=list(resolved.categorical_features),
            numeric_features=numeric,
            encoded_names=encoded_names,
            encoded_parents=encoded_parents,
            target=resolved.target,
            task=resolved.task,
            confidence_level=confidence_level,
            feature_medians=medians,
            feature_modes=modes,
            training_n=int(len(x_train)),
            calibration_n=int(len(x_cal)),
            data_source=data_source,
            test_n=int(len(x_test)),
            metric_name=metric_name,
            metric_value=metric_value,
            empirical_coverage=empirical_coverage,
        )
        if path is not None:
            if data_source == "synthetic":
                # NEVER auto-persist a noise model. A persisted artifact is loaded
                # forever after by every later process, so a silent synthetic write
                # turns a one-off fallback into permanent fake "calibrated evidence".
                logger.warning(
                    "Refusing to auto-persist a synthetic-data model to %s: a "
                    "fallback fitted on generated noise must never be reloaded as a "
                    "real one. Call model.save(path) explicitly if that is intended.",
                    path,
                )
            else:
                model.save(path)
        return model

    @staticmethod
    def _split(
        x: pd.DataFrame,
        y: pd.Series,
        test_size: float,
        random_state: int,
        *,
        stratify: bool,
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
        """Split ``(x, y)``, stratifying on the label for classification.

        Without stratification a rare class can land entirely outside the
        calibration split, at which point its conformal sets carry no guarantee at
        all. Stratification needs ≥ 2 members per class, so a frame too degenerate
        to stratify falls back to a plain split with a logged warning rather than
        failing the whole fit.

        Args:
            x: Feature matrix to split.
            y: Aligned target series.
            test_size: Fraction routed to the second half of the split.
            random_state: Seed for the split.
            stratify: Whether to stratify on ``y`` (classification only).

        Returns:
            ``(x_first, x_second, y_first, y_second)`` as ``train_test_split`` does.
        """
        if stratify:
            try:
                return train_test_split(
                    x, y, test_size=test_size, random_state=random_state, stratify=y
                )
            except ValueError:
                logger.warning(
                    "Cannot stratify the split (a class has fewer than 2 rows); "
                    "falling back to an unstratified split — conformal sets for a "
                    "class absent from calibration carry no coverage guarantee.",
                )
        return train_test_split(x, y, test_size=test_size, random_state=random_state)

    @staticmethod
    def _evaluate(
        task: TaskType,
        estimator: VotingRegressor | VotingClassifier,
        conformal: SplitConformalRegressor | SplitConformalClassifier,
        x_test: pd.DataFrame,
        y_test: pd.Series,
    ) -> tuple[str | None, float | None, float | None]:
        """Measure accuracy and **empirical** conformal coverage on held-out rows.

        This is what makes the model card an audit artifact rather than an echo of
        its own configuration: the coverage returned here is counted from rows the
        ensemble never fitted and MAPIE never calibrated on, so a model with R² ≈ 0
        or a conformal predictor that misses its target is visibly distinguishable
        from a good one.

        Args:
            task: The supervised task type.
            estimator: The fitted ensemble (supplies ``classes_`` for classification).
            conformal: The calibrated MAPIE predictor.
            x_test: Held-out encoded features (may be empty).
            y_test: Held-out targets aligned to ``x_test``.

        Returns:
            ``(metric_name, metric_value, empirical_coverage)``; all ``None`` when
            ``x_test`` is empty or the measurement could not be taken.
        """
        if len(x_test) == 0:
            return None, None, None
        truth = np.asarray(y_test)
        if task == "classification":
            predicted, sets = conformal.predict_set(x_test)
            members = np.asarray(sets)[:, :, 0]
            index_of = {label: i for i, label in enumerate(estimator.classes_)}
            hits = [
                bool(members[row, index_of[label]])
                for row, label in enumerate(truth)
                if label in index_of
            ]
            coverage = float(np.mean(hits)) if hits else None
            return "accuracy", float(accuracy_score(truth, predicted)), coverage
        predicted, intervals = conformal.predict_interval(x_test)
        lower = np.asarray(intervals)[:, 0, 0]
        upper = np.asarray(intervals)[:, 1, 0]
        coverage = float(np.mean((truth >= lower) & (truth <= upper)))
        return "r2", float(r2_score(truth, predicted)), coverage

    @staticmethod
    def _build_preprocessor(spec: ResolvedSpec) -> ColumnTransformer:
        """One-hot the categorical features, pass numerics through unchanged.

        ``handle_unknown="ignore"`` means an unseen category at inference maps to an
        all-zero block (never an error), and ``sparse_output=False`` keeps the
        encoded matrix a dense frame the tree learners + SHAP consume directly.
        """
        return ColumnTransformer(
            transformers=[
                (
                    "cat",
                    OneHotEncoder(handle_unknown="ignore", sparse_output=False),
                    spec.categorical_features,
                ),
                ("num", "passthrough", spec.numeric_features),
            ],
            remainder="drop",
            verbose_feature_names_out=False,
        )

    @staticmethod
    def _build_estimator(
        task: TaskType, random_state: int
    ) -> VotingRegressor | VotingClassifier:
        """Construct a light, CPU-only soft-voting ensemble for ``task``.

        Members come from :func:`_regression_members` / :func:`_classification_members`
        (the estimator reshape point). Regression averages member predictions;
        classification soft-votes their calibrated probabilities.

        Args:
            task: The supervised task type.
            random_state: Seed for determinism.

        Returns:
            An unfitted ``VotingRegressor`` or (soft) ``VotingClassifier``.
        """
        if task == "classification":
            return VotingClassifier(
                estimators=_classification_members(random_state),
                voting="soft",
                n_jobs=1,
            )
        return VotingRegressor(estimators=_regression_members(random_state), n_jobs=1)

    @staticmethod
    def _build_conformal(
        task: TaskType,
        estimator: VotingRegressor | VotingClassifier,
        confidence_level: float,
        x_cal: pd.DataFrame,
        y_cal: pd.Series,
    ) -> SplitConformalRegressor | SplitConformalClassifier:
        """Wrap a fitted ensemble in a calibrated MAPIE conformal predictor.

        Args:
            task: The supervised task type.
            estimator: The already-fitted ensemble (``prefit=True``).
            confidence_level: Target marginal coverage.
            x_cal: Calibration features (disjoint from training).
            y_cal: Calibration targets.

        Returns:
            A conformalised MAPIE predictor ready for interval/set queries.
        """
        if task == "classification":
            clf = SplitConformalClassifier(
                estimator=estimator, confidence_level=confidence_level, prefit=True
            )
            clf.conformalize(x_cal, y_cal)
            return clf
        reg = SplitConformalRegressor(
            estimator=estimator, confidence_level=confidence_level, prefit=True
        )
        reg.conformalize(x_cal, y_cal)
        return reg

    # ── inference ───────────────────────────────────────────────────────────
    @cached_property
    def _explainers(self) -> dict[str, shap.TreeExplainer]:
        """Lazily-built SHAP ``TreeExplainer`` per ensemble member (never pickled)."""
        return {
            name: shap.TreeExplainer(member)
            for name, member in self.estimator.named_estimators_.items()
        }

    def _raw_row(
        self, features: dict[str, Any]
    ) -> tuple[pd.DataFrame, list[str], list[str]]:
        """Build one native-typed feature row, imputing anything missing.

        Categorical features keep their string value (imputed with the training
        mode); numeric features are coerced to float (imputed with the training
        median). The result carries the **original** feature columns and is what the
        preprocessor encodes.

        Imputation is reported back rather than applied silently: a caller who
        mistypes a feature name would otherwise get a fully confident answer about
        the median training row, with nothing in the response saying that none of
        what they sent was used.

        Args:
            features: Raw ``feature name → value`` mapping from the caller.

        Returns:
            ``(row, imputed, unknown)`` — a one-row ``DataFrame`` with columns in
            ``feature_names`` order, the model features that had to be imputed, and
            the caller keys that are not model features (and were ignored).
        """
        row: dict[str, Any] = {}
        imputed: list[str] = []
        categorical = set(self.categorical_features)
        for name in self.feature_names:
            raw = features.get(name)
            if name in categorical:
                if raw is None:
                    imputed.append(name)
                    row[name] = self.feature_modes.get(name)
                else:
                    row[name] = str(raw)
                continue
            if raw is None:
                imputed.append(name)
                row[name] = self.feature_medians.get(name, 0.0)
                continue
            try:
                row[name] = float(raw)
            except (TypeError, ValueError):
                imputed.append(name)
                row[name] = self.feature_medians.get(name, 0.0)
        unknown = [key for key in features if key not in set(self.feature_names)]
        return pd.DataFrame([row], columns=self.feature_names), imputed, unknown

    def _encode(self, raw: pd.DataFrame) -> pd.DataFrame:
        """Map a raw feature row to the encoded matrix the estimator expects."""
        return pd.DataFrame(
            self.preprocessor.transform(raw), columns=self.encoded_names, index=raw.index
        )

    def predict_explain(self, features: dict[str, Any]) -> MLExplainResponse:
        """Predict, conformalise and explain a single input.

        Args:
            features: ``feature name → value`` for one prediction. Missing
                features are imputed with the training median; extras ignored.

        Returns:
            An :class:`~aegis.ml.types.MLExplainResponse` carrying the point
            prediction, the calibrated conformal interval (regression only),
            the requested coverage rate, the signed per-feature SHAP attributions
            sorted by descending absolute contribution, and the honesty signals
            (``data_source``, ``imputed_features``, ``unknown_features``) a caller
            needs to decide how much the evidence is worth.
        """
        raw, imputed, unknown = self._raw_row(features)
        x = self._encode(raw)
        if self.task == "classification":
            prediction, interval, set_size = self._classify(x)
        else:
            prediction, interval, set_size = *self._regress(x), None

        width = (interval[1] - interval[0]) if interval is not None else None
        return MLExplainResponse(
            prediction=prediction,
            conformal_interval=interval,
            conformal_confidence=self.confidence_level,
            interval_width=width,
            prediction_set_size=set_size,
            shap_attribution=self._attributions(x, raw, prediction),
            data_source=getattr(self, "data_source", "synthetic"),
            imputed_features=imputed,
            unknown_features=unknown,
        )

    def _regress(self, x: pd.DataFrame) -> tuple[float, tuple[float, float]]:
        """Point prediction and calibrated interval for a regression row.

        Args:
            x: One-row feature frame.

        Returns:
            ``(prediction, (lower, upper))`` where the interval has guaranteed
            marginal coverage ``confidence_level``.
        """
        point, intervals = self.conformal.predict_interval(x)
        lower = float(intervals[0, 0, 0])
        upper = float(intervals[0, 1, 0])
        return float(point[0]), (lower, upper)

    def _classify(self, x: pd.DataFrame) -> tuple[str, None, int]:
        """Point prediction and conformal set *size* for a classification row.

        The conformal *set* enforces the coverage guarantee; the response schema
        carries no interval for classification (so ``None``), but the set **size**
        is surfaced as informational evidence: a singleton (size 1) is a confident
        call, a non-singleton is ambiguous, and an empty set is degenerate.

        Args:
            x: One-row feature frame.

        Returns:
            ``(label, None, set_size)`` — the predicted label, a ``None`` interval,
            and the number of classes retained in the conformal set for this row.
        """
        point, sets = self.conformal.predict_set(x)
        set_size = int(np.asarray(sets)[0, :, 0].sum())
        return str(point[0]), None, set_size

    def _explained_class(self, prediction: float | str) -> tuple[int, int]:
        """Locate the predicted label among the estimator's classes.

        The explanation must be *of the class shown next to it*, so the index is
        resolved from the label actually returned rather than re-derived from
        ``predict_proba``.

        Args:
            prediction: The label :meth:`_classify` returned (stringified).

        Returns:
            ``(class_index, n_classes)``; ``class_index`` falls back to ``0`` when
            the label cannot be located.
        """
        classes = [str(c) for c in getattr(self.estimator, "classes_", [])]
        if str(prediction) in classes:
            return classes.index(str(prediction)), len(classes)
        return 0, len(classes)

    def _attributions(
        self, x: pd.DataFrame, raw: pd.DataFrame, prediction: float | str
    ) -> list[ShapFeature]:
        """Compute signed SHAP attributions for a single row, per **original** feature.

        Each ensemble member is explained with an exact tree ``TreeExplainer`` on the
        **encoded** row and the per-encoded-column contributions are averaged with
        the members' (uniform) voting weights. The encoded contributions are then
        **aggregated back to the original features** (summing the one-hot columns of
        each categorical), so the drivers are reported per real feature — exact for
        the averaged regression output; a per-member mean over margin space for
        soft-voting classification (a faithful driver ranking).

        Both binary boosters (``XGBClassifier``, ``HistGradientBoostingClassifier``)
        emit a single 2-D ``(n, n_encoded)`` margin, and that margin is always
        *toward class 1*. When the predicted label is class 0 the raw values
        therefore explain the class that was **not** predicted, and every driver's
        sign reads backwards beside the prediction — so they are negated. Only
        genuinely multiclass 3-D values are re-indexed.

        Args:
            x: One-row **encoded** frame (estimator input space).
            raw: One-row **raw** frame (original feature values, for reporting).
            prediction: The predicted value/label this explanation must describe.

        Returns:
            One :class:`~aegis.ml.types.ShapFeature` per original feature, sorted by
            descending absolute contribution.
        """
        class_index, n_classes = (
            self._explained_class(prediction)
            if self.task == "classification"
            else (0, 0)
        )
        flip_binary = self.task == "classification" and n_classes == 2 and class_index == 0

        weights = self._member_weights()
        per_member: list[np.ndarray] = []
        with warnings.catch_warnings():
            # SHAP calls member.predict on a bare ndarray → sklearn's cosmetic
            # "X does not have valid feature names" warning; the values are correct.
            warnings.simplefilter("ignore", category=UserWarning)
            for name, explainer in self._explainers.items():
                values = np.asarray(explainer.shap_values(x))
                if values.ndim == 3:  # multiclass: (1, n_encoded, n_classes)
                    values = values[:, :, class_index]
                elif flip_binary:  # 2-D binary margin is toward class 1, not class 0
                    values = -values
                per_member.append(weights[name] * values[0])
        encoded_contrib = np.sum(per_member, axis=0)

        # Aggregate encoded-column contributions back to their original feature.
        agg: dict[str, float] = dict.fromkeys(self.feature_names, 0.0)
        for parent, contribution in zip(
            self.encoded_parents, encoded_contrib, strict=True
        ):
            agg[parent] = agg.get(parent, 0.0) + float(contribution)

        categorical = set(self.categorical_features)
        features = [
            ShapFeature(
                feature=name,
                # Numeric features report their real value; categoricals have no
                # meaningful float value, so they report the one-hot active
                # indicator (1.0) and name the actual level in `value_label` — a
                # bare "region = 1.0" never says *which* level drove the answer.
                value=(1.0 if name in categorical else float(raw.iloc[0][name])),
                value_label=(
                    _level_label(raw.iloc[0][name]) if name in categorical else None
                ),
                contribution=agg[name],
            )
            for name in self.feature_names
        ]
        features.sort(key=lambda f: abs(f.contribution), reverse=True)
        return features

    def _member_weights(self) -> dict[str, float]:
        """Return normalised per-member voting weights (uniform when unset)."""
        names = list(self.estimator.named_estimators_.keys())
        raw = getattr(self.estimator, "weights", None)
        if raw is None:
            return {name: 1.0 / len(names) for name in names}
        total = float(sum(raw)) or 1.0
        return {name: float(w) / total for name, w in zip(names, raw, strict=False)}

    # ── observability ─────────────────────────────────────────────────────────
    def model_card(self) -> ModelCard:
        """Return honest, **measured** metadata describing this fitted spine.

        Every field is read off the live model — the fitted ensemble members and
        their voting weights, the encoded matrix width, the MAPIE class backing the
        coverage guarantee and the stored split sizes — never hardcoded. This is the
        data the MLOps UI later renders; it is also the audit trail proving *which*
        model (real domain-trained vs synthetic fallback, via ``data_source``) is
        actually serving.

        The two coverage fields are deliberately distinct and must stay that way:
        ``conformal_coverage`` is the level that was **requested**, while
        ``conformal_coverage_empirical`` is the rate **observed** on a held-out test
        split. Reporting the request as the achievement is what makes an audit
        artifact untrustworthy.

        Returns:
            A :class:`~aegis.ml.types.ModelCard` snapshot of this model.
        """
        weights = self._member_weights()
        members = [
            EnsembleMember(
                name=name,
                kind=type(member).__name__,
                weight=weights.get(name, 0.0),
            )
            for name, member in self.estimator.named_estimators_.items()
        ]
        return ModelCard(
            task=self.task,
            target=self.target,
            features=list(self.feature_names),
            n_features=len(self.feature_names),
            categorical_features=list(self.categorical_features),
            numeric_features=list(self.numeric_features),
            encoded_feature_count=len(self.encoded_names),
            ensemble_members=members,
            conformal_method="split_conformal",
            conformal_predictor=type(self.conformal).__name__,
            conformal_coverage=self.confidence_level,
            calibration_size=getattr(self, "calibration_n", 0),
            training_size=getattr(self, "training_n", 0),
            data_source=getattr(self, "data_source", "synthetic"),
            test_size=getattr(self, "test_n", 0),
            conformal_coverage_empirical=getattr(self, "empirical_coverage", None),
            metric_name=getattr(self, "metric_name", None),
            metric_value=getattr(self, "metric_value", None),
        )

    # ── persistence ─────────────────────────────────────────────────────────
    def __getstate__(self) -> dict[str, Any]:
        """Return picklable state, dropping the cached SHAP explainers.

        Returns:
            The instance ``__dict__`` without the non-portable explainers.
        """
        state = self.__dict__.copy()
        state.pop("_explainers", None)
        return state

    def save(self, path: Path | str = DEFAULT_ARTIFACT_PATH) -> Path:
        """Persist the model to ``path`` via joblib.

        Calling this on a ``data_source == "synthetic"`` model is allowed but
        deliberate — :meth:`train` never does it for you, because a persisted noise
        artifact is silently reloaded by every later process. The ``data_source``
        label rides along in the pickle so even a hand-saved synthetic model
        announces itself on every prediction.

        Args:
            path: Destination file; parent directories are created.

        Returns:
            The resolved path written to.
        """
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self, target)
        return target

    @classmethod
    def load(cls, path: Path | str = DEFAULT_ARTIFACT_PATH) -> TrustworthyModel:
        """Load a persisted model from ``path``.

        Args:
            path: Source file produced by :meth:`save`.

        Returns:
            The reconstructed :class:`TrustworthyModel`.

        Raises:
            FileNotFoundError: If ``path`` does not exist.
        """
        target = Path(path)
        if not target.exists():
            raise FileNotFoundError(f"No ML artifact at {target}")
        return joblib.load(target)
