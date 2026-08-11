"""Injected seams for the LLM-Ops loop — config the host wires in (no host import).

``aegis.ops`` is domain- and host-agnostic: it never imports an application layer. The
few things it cannot own itself are injected here once, at host startup (or at import of a
strangler shim), and read by :mod:`aegis.ops.diagnose` / :mod:`aegis.ops.release` /
:mod:`aegis.ops.gate`:

- ``render_floor_prompt(prompt_key) -> str`` — the prompt *floor* (the adapter/persona
  baseline the registry builds on but never goes below). Defaults to an empty floor.
- ``session_factory() -> AsyncSession`` — the host's session maker (the host owns the
  engine); used by the gate's own short-lived read/decide sessions.
- ``set_tenant_scope(session, tenant_id) -> Awaitable`` — the RLS scope binder (defaults
  to :func:`aegis.governance.rls.set_tenant_scope`).
- ``enqueue_approval(**)`` — the durable approval writer the release gate stages through.
- ``approval_model`` / ``approval_status`` — the host-owned ``Approval`` ORM class + its
  status enum (agent-owned; ops reads/decides them via the injected session, never owning
  the table).

Per-call overrides are always accepted where relevant; these module-level values are the
fallbacks.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

_RenderFloor = Callable[[str], str]
_SetTenantScope = Callable[[Any, "int | None"], Awaitable[None]]


def _empty_floor(prompt_key: str) -> str:  # noqa: ARG001 - stable no-floor default
    """Default floor renderer: an empty baseline (the host injects the real one)."""
    return ""


_render_floor_prompt: _RenderFloor = _empty_floor
_session_factory: Callable[[], Any] | None = None
_set_tenant_scope: _SetTenantScope | None = None
_enqueue_approval: Callable[..., Awaitable[Any]] | None = None
_approval_model: Any = None
_approval_status: Any = None


def configure_ops(
    *,
    render_floor_prompt: _RenderFloor | None = None,
    session_factory: Callable[[], Any] | None = None,
    set_tenant_scope: _SetTenantScope | None = None,
    enqueue_approval: Callable[..., Awaitable[Any]] | None = None,
    approval_model: Any = None,  # noqa: ANN401 - host ORM class, kept loose
    approval_status: Any = None,  # noqa: ANN401 - host status enum, kept loose
) -> None:
    """Wire the injected seams for the LLM-Ops loop (call once at host startup).

    Only the provided arguments are (re)bound; omitted ones keep their current value, so a
    host can configure the floor renderer early and the approvals seams later. See the
    module docstring for each seam's contract.
    """
    global _render_floor_prompt, _session_factory, _set_tenant_scope
    global _enqueue_approval, _approval_model, _approval_status
    if render_floor_prompt is not None:
        _render_floor_prompt = render_floor_prompt
    if session_factory is not None:
        _session_factory = session_factory
    if set_tenant_scope is not None:
        _set_tenant_scope = set_tenant_scope
    if enqueue_approval is not None:
        _enqueue_approval = enqueue_approval
    if approval_model is not None:
        _approval_model = approval_model
    if approval_status is not None:
        _approval_status = approval_status


def render_floor_prompt(prompt_key: str) -> str:
    """Return the injected floor prompt for ``prompt_key`` (empty by default)."""
    return _render_floor_prompt(prompt_key)


def get_session_factory() -> Callable[[], Any]:
    """Return the injected session factory, or raise if the host never configured one."""
    if _session_factory is None:
        raise RuntimeError(
            "aegis.ops session factory is not configured; call "
            "aegis.ops.configure_ops(session_factory=...) at startup."
        )
    return _session_factory


async def apply_tenant_scope(session: Any, tenant_id: int | None) -> None:  # noqa: ANN401
    """Apply the injected ``set_tenant_scope`` if configured, else a no-op."""
    if _set_tenant_scope is not None:
        await _set_tenant_scope(session, tenant_id)


def get_enqueue_approval() -> Callable[..., Awaitable[Any]]:
    """Return the injected durable approval writer, or raise if unconfigured."""
    if _enqueue_approval is None:
        raise RuntimeError(
            "aegis.ops enqueue_approval is not configured; call "
            "aegis.ops.configure_ops(enqueue_approval=...) at startup."
        )
    return _enqueue_approval


def get_approval_model() -> Any:  # noqa: ANN401 - host ORM class
    """Return the injected host ``Approval`` ORM class, or raise if unconfigured."""
    if _approval_model is None:
        raise RuntimeError(
            "aegis.ops approval_model is not configured; call "
            "aegis.ops.configure_ops(approval_model=..., approval_status=...) at startup."
        )
    return _approval_model


def get_approval_status() -> Any:  # noqa: ANN401 - host status enum
    """Return the injected host ``ApprovalStatus`` enum, or raise if unconfigured."""
    if _approval_status is None:
        raise RuntimeError(
            "aegis.ops approval_status is not configured; call "
            "aegis.ops.configure_ops(approval_model=..., approval_status=...) at startup."
        )
    return _approval_status


__all__ = [
    "apply_tenant_scope",
    "configure_ops",
    "get_approval_model",
    "get_approval_status",
    "get_enqueue_approval",
    "get_session_factory",
    "render_floor_prompt",
]
