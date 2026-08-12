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
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import OneHotEncoder
from xgboost import XGBClassifier, XGBRegressor

from aegis.ml.dataset import resolve_training_frame
from aegis.ml.spec import ResolvedSpec, TaskType, resolve_spec
from aegis.ml.types import EnsembleMember, MLExplainResponse, ModelCard, ShapFeature

if TYPE_CHECKING:
    from aegis.ml.spec import MLSpec

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
    encoded_names: list[str], categorical_features: list[str]
) -> list[str]:
    """Map each encoded column to the original feature it derives from.

    A one-hot column ``"<feature>_<level>"`` maps to ``<feature>``; a passthrough
    numeric column maps to itself. Categoricals are matched longest-name-first so a
    feature whose name is a prefix of another can never steal the other's columns.

    Args:
        encoded_names: Column names emitted by the fitted preprocessor.
        categorical_features: The original categorical feature names.

    Returns:
        A list parallel to ``encoded_names`` giving each column's parent feature.
    """
    cats = sorted(categorical_features, key=len, reverse=True)
    parents: list[str] = []
    for col in encoded_names:
        parent = col
        for c in cats:
            if col == c or col.startswith(f"{c}_"):
                parent = c
                break
        parents.append(parent)
    return parents


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

    # ── construction ────────────────────────────────────────────────────────
    @classmethod
    def train(
        cls,
        spec: MLSpec | None = None,
        frame: pd.DataFrame | None = None,
        *,
        confidence_level: float = 0.9,
        calibration_size: float = 0.25,
        random_state: int = 0,
        path: Path | str | None = None,
    ) -> TrustworthyModel:
        """Fit, conformalise and (optionally) persist an ensemble from the spec.

        The data is split into a training set (fits the ensemble) and a disjoint
        calibration set (fed to MAPIE). A dedicated calibration split is what
        makes the coverage guarantee valid — never calibrate on training rows.

        Args:
            spec: Domain spec; resolved via :func:`resolve_spec` when ``None``.
            frame: Explicit training frame; synthesised when ``None``.
            confidence_level: Target marginal coverage (e.g. ``0.9`` → 90%).
            calibration_size: Fraction of rows reserved for calibration.
            random_state: Seed for the split and the estimator.
            path: If given, the fitted model is saved there via :meth:`save`.

        Returns:
            A ready-to-serve :class:`TrustworthyModel`.
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
        encoded_parents = _encoded_parents(encoded_names, resolved.categorical_features)

        x_train, x_cal, y_train, y_cal = train_test_split(
            encoded, y, test_size=calibration_size, random_state=random_state
        )
        estimator = cls._build_estimator(resolved.task, random_state)
        estimator.fit(x_train, y_train)
        conformal = cls._build_conformal(
            resolved.task, estimator, confidence_level, x_cal, y_cal
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
        )
        if path is not None:
            model.save(path)
        return model

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

    def _raw_row(self, features: dict[str, Any]) -> pd.DataFrame:
        """Build one native-typed feature row, imputing anything missing.

        Categorical features keep their string value (imputed with the training
        mode); numeric features are coerced to float (imputed with the training
        median). The result carries the **original** feature columns and is what the
        preprocessor encodes.

        Args:
            features: Raw ``feature name → value`` mapping from the caller.

        Returns:
            A one-row ``DataFrame`` with columns in ``feature_names`` order.
        """
        row: dict[str, Any] = {}
        categorical = set(self.categorical_features)
        for name in self.feature_names:
            if name in categorical:
                raw = features.get(name)
                row[name] = str(raw) if raw is not None else self.feature_modes.get(name)
            else:
                raw = features.get(name, self.feature_medians.get(name, 0.0))
                try:
                    row[name] = float(raw)
                except (TypeError, ValueError):
                    row[name] = self.feature_medians.get(name, 0.0)
        return pd.DataFrame([row], columns=self.feature_names)

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
            the guaranteed coverage rate and the signed per-feature SHAP
            attributions, sorted by descending absolute contribution.
        """
        raw = self._raw_row(features)
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
            shap_attribution=self._attributions(x, raw),
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

    def _attributions(self, x: pd.DataFrame, raw: pd.DataFrame) -> list[ShapFeature]:
        """Compute signed SHAP attributions for a single row, per **original** feature.

        Each ensemble member is explained with an exact tree ``TreeExplainer`` on the
        **encoded** row and the per-encoded-column contributions are averaged with
        the members' (uniform) voting weights. The encoded contributions are then
        **aggregated back to the original features** (summing the one-hot columns of
        each categorical), so the drivers are reported per real feature — exact for
        the averaged regression output; a per-member mean over margin space for
        soft-voting classification (a faithful driver ranking).

        Args:
            x: One-row **encoded** frame (estimator input space).
            raw: One-row **raw** frame (original feature values, for reporting).

        Returns:
            One :class:`~aegis.ml.types.ShapFeature` per original feature, sorted by
            descending absolute contribution.
        """
        class_index: int | None = None
        if self.task == "classification":
            class_index = int(np.argmax(self.estimator.predict_proba(x)[0]))

        weights = self._member_weights()
        per_member: list[np.ndarray] = []
        with warnings.catch_warnings():
            # SHAP calls member.predict on a bare ndarray → sklearn's cosmetic
            # "X does not have valid feature names" warning; the values are correct.
            warnings.simplefilter("ignore", category=UserWarning)
            for name, explainer in self._explainers.items():
                values = np.asarray(explainer.shap_values(x))
                if values.ndim == 3:  # multiclass: (1, n_encoded, n_classes)
                    idx = class_index if class_index is not None else 0
                    values = values[:, :, idx]
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
                # Numeric features report their real value; categoricals report the
                # one-hot active indicator (1.0) — the level is named by `feature`.
                value=(1.0 if name in categorical else float(raw.iloc[0][name])),
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
