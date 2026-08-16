"""Reproducible offline trainer for the ML spine artifact.

Run once during setup (the ``*.joblib`` artifact is intentionally git-ignored) to
train the trustworthy-ML ensemble on the **real domain** feature frame resolved from
the adapter and persist it to :data:`app.ml.model.DEFAULT_ARTIFACT_PATH` (delegated
to :mod:`aegis.ml`)::

    python -m app.ml

This trains on ``app.adapter.ml_spec.training_frame`` (categorical + numeric domain
features with the genuine ``resolution_hours`` label), one-hot-encodes the
categoricals, calibrates the conformal predictor on a held-out split, and reports the
empirical coverage so a bad artifact is obvious immediately.
"""

from __future__ import annotations

import warnings

import numpy as np

# Import the HOST artifact path from ``app.ml``, never from ``app.ml.model``.
# ``app.ml.model`` is a thin shim that re-exports ``aegis.ml.model``'s constant,
# which resolves INSIDE the installed aegis package. Training through that path
# wrote the artifact to the library directory while ``app.ml.get_model()`` loads
# from the host directory — so training appeared to succeed and the endpoints
# still answered 503, with the two paths differing by a directory nobody looks at.
from app.ml import DEFAULT_ARTIFACT_PATH, train
from app.ml.spec import resolve_spec


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
    easy = model.predict_explain(
        {"priority": "urgent", "category": "general", "channel": "chat", "region": "na",
         "customer_tier": "enterprise", "agent_tenure_months": 60, "queue_depth_at_open": 0,
         "reopened_count": 0, "description_length": 50}
    )
    hard = model.predict_explain(
        {"priority": "low", "category": "technical", "channel": "email", "region": "apac",
         "customer_tier": "standard", "agent_tenure_months": 1, "queue_depth_at_open": 40,
         "reopened_count": 2, "description_length": 1200}
    )
    print(f"  sanity: easy={float(easy.prediction):.1f}h  hard={float(hard.prediction):.1f}h  "
          f"(distinct={not np.isclose(easy.prediction, hard.prediction)})")


if __name__ == "__main__":
    main()
