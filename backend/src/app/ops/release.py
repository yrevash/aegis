"""Strangler shim: ``app.ops.release`` delegates to :mod:`aegis.ops.release`.

The smart tiered release gate + change-risk classifier now live in the standalone
``aegis.ops`` package; re-exported here under their historical names. The baseline prompt
*floor* is injected into ``aegis.ops`` once at :mod:`app.ops` import via ``configure_ops``;
``eval_fn`` + ``approval_enqueue`` are still passed by the ``/ops/release`` caller.
"""

from __future__ import annotations

from aegis.ops.release import (
    ChangeRisk,
    ReleaseResult,
    apply_release_decision,
    classify_change,
    release,
)

__all__ = [
    "ChangeRisk",
    "ReleaseResult",
    "apply_release_decision",
    "classify_change",
    "release",
]
