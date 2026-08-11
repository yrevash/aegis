"""Aegis ops — the importable LLM-Ops self-improvement loop.

Trace → Eval → Observe → Diagnose → Gate → Release, where Release writes a versioned,
reversible system prompt/config back into the harness. Every stage is domain-agnostic:
what "good" means for a domain comes from the eval corpus (:mod:`aegis.evals`) + the
injected prompt *floor* (the adapter/persona baseline the registry builds on but never
goes below).

The loop carries its own ORM — :class:`EvalResult` / :class:`PromptVersion` /
:class:`PromptStatus` on the shared :class:`aegis.data.AegisBase` — and a process-wide
active-prompt cache read synchronously on the hot path. **Ops depends on
:mod:`aegis.evals`** (one-directional) and injects everything host-specific via
:func:`configure_ops`: the prompt-floor renderer, the session factory + ``set_tenant_scope``
binder, the durable ``enqueue_approval`` writer, and the host-owned ``Approval`` ORM class
+ status enum. Importing this package pulls ``sqlalchemy`` (``aegis[data]``) but no
``fastapi`` / ``litellm`` (see ``tests/ops/test_isolation.py``).
"""

from __future__ import annotations

from aegis.ops.config import configure_ops
from aegis.ops.diagnose import DiagnoseResult, diagnose
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
from aegis.ops.models import EvalResult, PromptStatus, PromptVersion
from aegis.ops.release import (
    ChangeRisk,
    ReleaseResult,
    apply_release_decision,
    classify_change,
    release,
)
from aegis.ops.trace_eval import RunEval, evaluate_run

__all__ = [
    "DEFAULT_EVAL_SUBSET",
    "RELEASE_ACTION",
    "ChangeRisk",
    "DiagnoseResult",
    "EvalResult",
    "PendingRelease",
    "PromptStatus",
    "PromptVersion",
    "ReleaseDecision",
    "ReleaseResult",
    "RunEval",
    "apply_release_decision",
    "classify_change",
    "configure_ops",
    "decide_release",
    "diagnose",
    "enqueue_release_approval",
    "evaluate_run",
    "list_pending_releases",
    "make_eval_fn",
    "release",
]
