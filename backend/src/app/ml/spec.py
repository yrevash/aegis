"""ML specification contract and defensive resolution of the adapter's spec.

The trustworthy-ML spine is domain-agnostic: *what* to predict (feature columns,
target column, task type) is supplied by the domain adapter via
``app.adapter.ml_spec``. That module is built by another agent and may not exist
yet, so this file imports it **defensively**: it declares a :class:`typing.Protocol`
for static typing and normalises whatever the adapter exposes into a concrete
:class:`ResolvedSpec`. When the adapter is absent, a self-contained
:data:`FALLBACK_SPEC` keeps the spine trainable and testable with no network and
no domain code.

Targeted libraries (verified against the installed versions on 2026-08-05):
pandas 2.3 (capped <2.4 for nemoguardrails co-existence), numpy 2.4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    import pandas as pd

TaskType = Literal["regression", "classification"]
"""Supervised task the spine solves: real-valued target or discrete label."""


@runtime_checkable
class MLSpec(Protocol):
    """Structural contract the adapter's ``ml_spec`` object must satisfy.

    Only ``features`` and ``target`` are required; ``task`` and
    ``training_frame`` are optional and resolved with sensible defaults by
    :func:`resolve_spec`.

    Attributes:
        features: Ordered feature-column names fed to the model.
        target: Name of the column the model predicts.
    """

    features: list[str]
    target: str


@dataclass(frozen=True)
class ResolvedSpec:
    """Concrete, validated ML spec used internally by the spine.

    Attributes:
        features: Ordered feature-column names.
        target: Target-column name.
        task: Supervised task type (``"regression"`` or ``"classification"``).
        categorical_features: Subset of ``features`` that are categorical (string)
            columns; the spine one-hot-encodes exactly these before fitting. The
            remainder are treated as numeric. Empty ⇒ every feature is numeric.
        frame_provider: Optional zero-arg callable returning a training
            ``DataFrame``; when ``None`` the spine synthesises data instead.
    """

    features: list[str]
    target: str
    task: TaskType = "regression"
    categorical_features: list[str] = field(default_factory=list)
    frame_provider: Callable[[], pd.DataFrame] | None = field(default=None, repr=False)

    @property
    def numeric_features(self) -> list[str]:
        """Feature columns not marked categorical (treated as numeric)."""
        cats = set(self.categorical_features)
        return [f for f in self.features if f not in cats]


FALLBACK_SPEC = ResolvedSpec(
    features=["feature_0", "feature_1", "feature_2", "feature_3"],
    target="target",
    task="regression",
)
"""Self-contained default used when ``app.adapter.ml_spec`` is unavailable."""


def _coerce_task(raw: object) -> TaskType:
    """Normalise an arbitrary task value to a valid :data:`TaskType`.

    Args:
        raw: Value read from the adapter spec (may be ``None`` or noise).

    Returns:
        ``"classification"`` if the value clearly denotes classification,
        otherwise ``"regression"``.
    """
    text = str(raw).strip().lower()
    if text in {"classification", "classify", "clf", "categorical", "binary"}:
        return "classification"
    return "regression"


def resolve_spec(spec: MLSpec | None = None) -> ResolvedSpec:
    """Resolve the active ML spec, defensively importing the adapter's version.

    Resolution order:

    1. An explicit ``spec`` argument (used by tests and callers).
    2. ``app.adapter.ml_spec`` if it exists and exposes a feature/target contract.
    3. :data:`FALLBACK_SPEC`.

    The adapter's real contract is read leniently so the domain spec is never
    silently dropped: features come from ``FEATURE_NAMES`` (or a lowercase
    ``features``); the target and task from a ``TARGET`` object with ``.name`` /
    ``.task`` (or lowercase ``target`` / ``task``); the categorical subset from
    ``CATEGORICAL_FEATURES`` (or derived from a ``FEATURES`` list of specs with
    ``.name`` / ``.dtype``); and the training frame from a ``training_frame``
    callable. Any missing piece degrades gracefully rather than falling all the way
    back to generic noise.

    Args:
        spec: Optional explicit spec object satisfying :class:`MLSpec`.

    Returns:
        A validated :class:`ResolvedSpec` ready for training/inference.
    """
    # Idempotent: an already-resolved spec is returned unchanged (callers such as
    # TrustworthyModel.train re-resolve, and the adapter-shaped reader below would
    # otherwise drop a ResolvedSpec's own lowercase fields).
    if isinstance(spec, ResolvedSpec):
        return spec

    candidate: object | None = spec
    if candidate is None:
        try:
            from app.adapter import ml_spec as candidate  # type: ignore[no-redef]
        except (ImportError, AttributeError):
            candidate = None

    if candidate is None:
        return FALLBACK_SPEC

    features = getattr(candidate, "FEATURE_NAMES", None) or getattr(
        candidate, "features", None
    )
    target_obj = getattr(candidate, "TARGET", None)
    target = getattr(target_obj, "name", None) or getattr(candidate, "target", None)
    if not features or not target:
        return FALLBACK_SPEC

    task_raw = getattr(target_obj, "task", None) or getattr(candidate, "task", "regression")

    features = list(features)
    categorical = _resolve_categorical(candidate, features)

    provider = getattr(candidate, "training_frame", None)
    if not callable(provider):
        provider = None

    return ResolvedSpec(
        features=features,
        target=str(target),
        task=_coerce_task(task_raw),
        categorical_features=categorical,
        frame_provider=provider,
    )


def _resolve_categorical(candidate: object, features: list[str]) -> list[str]:
    """Determine which features are categorical, tolerating several adapter shapes.

    Prefers an explicit ``CATEGORICAL_FEATURES`` list; otherwise derives it from a
    ``FEATURES`` sequence of specs carrying ``.name`` and ``.dtype``. Only names that
    actually appear in ``features`` are kept, preserving ``features`` order.
    """
    explicit = getattr(candidate, "CATEGORICAL_FEATURES", None)
    if explicit:
        chosen = set(explicit)
    else:
        specs = getattr(candidate, "FEATURES", None) or []
        chosen = {
            getattr(f, "name", None)
            for f in specs
            if str(getattr(f, "dtype", "")).lower() == "categorical"
        }
    return [f for f in features if f in chosen]
