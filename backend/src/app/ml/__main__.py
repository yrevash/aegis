"""Reproducible offline trainer for the ML spine artifact.

Run once during setup (the ``*.joblib`` artifact is intentionally git-ignored) to
train the trustworthy-ML ensemble on the **real domain** feature frame resolved from
the adapter and persist it to :data:`app.ml.model.DEFAULT_ARTIFACT_PATH` (delegated
to :mod:`aegis.ml`)::

    python -m app.ml

This trains on the adapter's ``ml_spec.training_frame`` (its own categorical and
numeric features against its own genuine label), one-hot-encodes the categoricals,
calibrates the conformal predictor on a held-out split, and reports the empirical
coverage so a bad artifact is obvious immediately.

**The sanity probe is built from the adapter's spec, never from a literal row.** It used
to carry the shipped domain's nine feature keys and print its unit (``h``) inline. After
a retarget both probe rows encoded to the same vector — every key the new spec did not
declare was ignored, and every key it did declare was absent — so the one diagnostic
whose job is to say "your model learned nothing" printed ``distinct=False`` and a figure
in the wrong unit on every *correct* integration. It now takes the two rows from the
extremes of the adapter's own training frame, which is exactly the pair a working model
must separate, and prints the target's own name and unit.
"""

from __future__ import annotations

import warnings
from typing import Any

import numpy as np

# Import the HOST artifact path from ``app.ml``, never from ``app.ml.model``.
# ``app.ml.model`` is a thin shim that re-exports ``aegis.ml.model``'s constant,
# which resolves INSIDE the installed aegis package. Training through that path
# wrote the artifact to the library directory while ``app.ml.get_model()`` loads
# from the host directory — so training appeared to succeed and the endpoints
# still answered 503, with the two paths differing by a directory nobody looks at.
from app.adapter import TARGET, training_frame
from app.ml import DEFAULT_ARTIFACT_PATH, train
from app.ml.spec import resolve_spec

#: Rows to draw the probe pair from. Small: the probe only needs the two extremes of
#: the label, not a second training set.
_PROBE_ROWS = 300


def _probe_pair(features: list[str], target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the lowest- and highest-labelled feature rows the domain can produce.

    Args:
        features: The resolved spec's ordered feature columns.
        target: The resolved spec's target column.

    Returns:
        ``(low, high)`` feature dicts, taken from the adapter's own training frame, so
        every key is a column the model was actually fitted on and the two rows differ
        in the way the label says they should.
    """
    frame = training_frame(num_records=_PROBE_ROWS)
    ordered = frame.sort_values(target)
    low = ordered.iloc[0][features].to_dict()
    high = ordered.iloc[-1][features].to_dict()
    return low, high


def _render(value: Any, unit: str) -> str:  # noqa: ANN401 - the spec's own prediction type
    """Render one prediction with the target's own unit (numeric or class label)."""
    try:
        return f"{float(value):.1f}{unit}"
    except (TypeError, ValueError):
        return f"{value!r}"


def main() -> None:
    """Train, persist, and sanity-check the domain ML spine artifact."""
    warnings.filterwarnings("ignore")
    spec = resolve_spec()
    print(f"Training ML spine on domain spec: target={spec.target!r} task={spec.task}")
    print(f"  categorical: {spec.categorical_features}")
    print(f"  numeric    : {spec.numeric_features}")
    model = train(path=DEFAULT_ARTIFACT_PATH)
    print(f"Saved artifact → {DEFAULT_ARTIFACT_PATH} ({len(model.encoded_names)} encoded cols)")

    # Sanity: distinct inputs must give distinct predictions (never a constant).
    unit = f" {TARGET.unit}" if getattr(TARGET, "unit", None) else ""
    low_row, high_row = _probe_pair(list(spec.features), spec.target)
    low = model.predict_explain(low_row)
    high = model.predict_explain(high_row)
    distinct = not np.isclose(low.prediction, high.prediction)
    print(
        f"  sanity: lowest-labelled row={_render(low.prediction, unit)}  "
        f"highest-labelled row={_render(high.prediction, unit)}  (distinct={distinct})"
    )


if __name__ == "__main__":
    main()
