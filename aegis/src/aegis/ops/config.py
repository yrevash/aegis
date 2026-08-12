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
from dataclasses import asdict, dataclass, field
from typing import Any

_RenderFloor = Callable[[str], str]
_SetTenantScope = Callable[[Any, "int | None"], Awaitable[None]]


# ─────────────────────────────────────────────────────────────────────────────
# Loop parameters — the operator-tunable knobs of the self-improvement loop.
#
# The eval pass margin, the deterministic change-risk classifier thresholds (diff
# size + the safety/guardrail/tool/policy term lists + the tunable-config bounds),
# and the auto-promote risk ceiling all live here as one injectable dataclass. The
# defaults reproduce the loop's historical behaviour exactly, so untouched hosts see
# no change; :func:`aegis.ops.release.classify_change` / :func:`aegis.ops.release.release`
# read the effective params (per-call override → configured default) and
# :func:`get_loop_params` exposes them as data for the LLMOps UI.
# ─────────────────────────────────────────────────────────────────────────────

#: Total order over the risk tiers — used to compare a change's risk against the
#: auto-promote ceiling ("promote when risk <= ceiling").
RISK_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True)
class LoopParams:
    """The tunable knobs of the LLM-Ops self-improvement loop (defaults = historical).

    Attributes:
        eval_margin: How much a draft's regression score must beat the baseline by for
            the eval gate to pass (default ``0.0`` — strictly better).
        high_diff_fraction: Fraction of changed prompt lines at/above which a diff is
            unconditionally HIGH blast-radius.
        low_diff_fraction: Fraction at/below which a diff counts as a small wording nudge
            (a precondition for LOW risk).
        safety_terms: Terms whose occurrence-count changing between prompts forces HIGH
            risk (guardrail/tool/approval/policy wording is never "low risk").
        critical_config_markers: Substrings of a config key that make any change to that
            key HIGH (model/tool/permission surface).
        tunable_config_keys: Config keys that may be tweaked within bounds and still be LOW.
        tunable_max_delta: Per-key maximum delta for a "bounded tweak" of a tunable key.
        auto_promote_ceiling: The highest risk tier auto-promoted in ``tiered`` mode
            (``"low"`` by default — medium/high escalate to human approval).
    """

    eval_margin: float = 0.0
    high_diff_fraction: float = 0.40
    low_diff_fraction: float = 0.15
    safety_terms: tuple[str, ...] = field(
        default_factory=lambda: (
            "ignore",
            "guardrail",
            "safety",
            "tool",
            "approval",
            "never",
            "policy",
            "system prompt",
        )
    )
    critical_config_markers: tuple[str, ...] = field(
        default_factory=lambda: ("model", "tool", "permission", "role", "scope")
    )
    tunable_config_keys: tuple[str, ...] = field(
        default_factory=lambda: ("temperature", "top_k", "top_p")
    )
    tunable_max_delta: dict[str, float] = field(
        default_factory=lambda: {"temperature": 0.5, "top_k": 5, "top_p": 0.3}
    )
    auto_promote_ceiling: str = "low"

    def as_dict(self) -> dict[str, Any]:
        """Return the effective params as a plain JSON-friendly dict (for the UI/API)."""
        return asdict(self)


#: The process-wide default loop params (a host may replace via ``configure_ops`` or pass
#: a per-call override to the release gate). Kept module-level so the accessor is cheap.
_loop_params: LoopParams = LoopParams()


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
    loop_params: LoopParams | None = None,
) -> None:
    """Wire the injected seams for the LLM-Ops loop (call once at host startup).

    Only the provided arguments are (re)bound; omitted ones keep their current value, so a
    host can configure the floor renderer early and the approvals seams later. See the
    module docstring for each seam's contract. ``loop_params`` tunes the self-improvement
    loop's knobs (eval margin, risk-classifier thresholds/term lists, auto-promote ceiling);
    omitted, the historical defaults stand.
    """
    global _render_floor_prompt, _session_factory, _set_tenant_scope
    global _enqueue_approval, _approval_model, _approval_status, _loop_params
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
    if loop_params is not None:
        _loop_params = loop_params


def get_loop_params() -> LoopParams:
    """Return the effective loop params (the configured default, historical if untouched).

    The single source the release gate reads when no per-call ``params`` override is given,
    and the accessor the LLMOps API/UI surfaces so an operator can see (and tune) the knobs.
    """
    return _loop_params


def reset_loop_params() -> None:
    """Reset the loop params to the historical defaults (test isolation / operator revert)."""
    global _loop_params
    _loop_params = LoopParams()


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
    "RISK_ORDER",
    "LoopParams",
    "apply_tenant_scope",
    "configure_ops",
    "get_approval_model",
    "get_approval_status",
    "get_enqueue_approval",
    "get_loop_params",
    "get_session_factory",
    "render_floor_prompt",
    "reset_loop_params",
]
