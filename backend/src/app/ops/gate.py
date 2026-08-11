"""Strangler shim: ``app.ops.gate`` delegates to :mod:`aegis.ops.gate`.

The release-gate wiring (the real prompt-dependent ``eval_fn`` + the durable
``prompt_release`` approval seams + the staged-release inbox read/decide path) now lives in
the standalone ``aegis.ops`` package; re-exported here under its historical names. The
host-owned pieces — the durable ``enqueue_approval`` writer, the session factory +
``set_tenant_scope`` binder, and the ``Approval`` ORM class + status enum — are injected
into ``aegis.ops`` once at :mod:`app.ops` import via ``configure_ops``.
"""

from __future__ import annotations

from aegis.ops.gate import (
    DEFAULT_EVAL_SUBSET,
    RELEASE_ACTION,
    PendingRelease,
    ReleaseDecision,
    decide_release,
    enqueue_release_approval,
    list_pending_releases,
    make_eval_fn,
)

__all__ = [
    "DEFAULT_EVAL_SUBSET",
    "RELEASE_ACTION",
    "PendingRelease",
    "ReleaseDecision",
    "decide_release",
    "enqueue_release_approval",
    "list_pending_releases",
    "make_eval_fn",
]
