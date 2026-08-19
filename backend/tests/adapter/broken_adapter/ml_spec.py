"""The feature list, spelled the way a first attempt spells it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FeatureSpec:
    """One model feature."""

    name: str
    dtype: str
    description: str


@dataclass(frozen=True)
class TargetSpec:
    """What the spine predicts."""

    name: str
    task: str
    unit: str | None = None


FEATURES: list[FeatureSpec] = [
    FeatureSpec(name="urgency", dtype="categorical", description="How urgent the item is."),
    FeatureSpec(name="backlog", dtype="numeric", description="Queue length when raised."),
    FeatureSpec(name="reopens", dtype="numeric", description="Times the item came back."),
]

TARGET = TargetSpec(name="minutes_to_close", task="regression", unit="minutes")

# THE BREAK: the spine reads FEATURE_NAMES. Spelled this way it finds nothing, falls back
# to four generic noise columns, trains happily, and serves the result as domain evidence.
FEATURE_COLUMNS: list[str] = [f.name for f in FEATURES]


def training_frame(*, num_records: int = 200, seed: int = 7) -> Any:
    """Return the labelled training frame. Never called by the conformance suite."""
    raise NotImplementedError("the broken adapter is read-only fixture data")


def describe_prediction(resp: Any, *, top_k: int = 3) -> str:
    """Render one prediction as this domain's decision-support sentence."""
    return f"Predicted {TARGET.name}: {resp.prediction} {TARGET.unit}"
