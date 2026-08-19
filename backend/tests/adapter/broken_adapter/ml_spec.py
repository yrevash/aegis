"""The feature list, spelled the way a first attempt spells it."""

from __future__ import annotations

from app.adapter.ml_spec import (  # noqa: F401
    FEATURES,
    TARGET,
    describe_prediction,
    training_frame,
)

FEATURE_COLUMNS = [f.name for f in FEATURES]
