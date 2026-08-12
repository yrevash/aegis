"""LLM-Ops: the closed feedback loop over the agent (see ``docs/learn/40-pipelines.md``).

Trace → Eval → Observe → Diagnose → Gate → Release, where Release writes a versioned,
reversible system prompt/config back into the harness. Every stage is domain-agnostic
core; what "good" means for a domain comes from the eval corpus + adapter prompt (the
floor the registry can only build on, never below).

**Strangler shim.** The loop now lives in the standalone, importable ``aegis.ops`` package
(``registry`` / ``trace_eval`` / ``diagnose`` / ``release`` / ``gate`` + the
``EvalResult`` / ``PromptVersion`` ORM on ``aegis.data``). The submodules here re-export it
under the historical ``app.ops.*`` names; this package wires the host-specific seams into
``aegis.ops`` exactly once, at import, via :func:`aegis.ops.configure_ops`:

- the prompt *floor* renderer (the adapter/persona baseline);
- the session factory + the ``set_tenant_scope`` RLS binder (the host owns the engine);
- the durable ``enqueue_approval`` writer; and
- the host-owned ``Approval`` ORM class + its status enum (agent-owned; the gate reads and
  decides rows through the injected session but never owns the table).
"""

from __future__ import annotations

from aegis.ops import configure_ops as _configure_ops


def _default_render_floor_prompt(prompt_key: str) -> str:
    """The adapter/persona prompt floor for ``prompt_key`` (the pre-extraction default)."""
    from app.adapter import get_persona, render_system_prompt  # noqa: PLC0415

    return render_system_prompt(get_persona(prompt_key))


def _configure() -> None:
    """Wire the backend's host seams into ``aegis.ops`` (idempotent; run at import)."""
    from app.data.approvals import enqueue_approval  # noqa: PLC0415
    from app.data.models import Approval, ApprovalStatus  # noqa: PLC0415
    from app.data.session import get_sessionmaker, set_tenant_scope  # noqa: PLC0415

    _configure_ops(
        render_floor_prompt=_default_render_floor_prompt,
        session_factory=lambda: get_sessionmaker()(),
        set_tenant_scope=set_tenant_scope,
        enqueue_approval=enqueue_approval,
        approval_model=Approval,
        approval_status=ApprovalStatus,
    )


_configure()
