"""Binding the agent's tighten-only controls to the catalogue — per tenant, per run.

**The gap this closes.** ``agent.gate_min_risk`` was in the catalogue, was
``TIGHTEN_ONLY``, was writable by a tenant admin and renderable on a settings screen —
and *nothing read it*. :class:`~aegis.agent.deps.AgentConfig` carried a hardcoded
``RiskLevel.HIGH`` that no host ever overrode, so a tenant admin who asked for **more**
oversight over their own agents ("gate everything MEDIUM and above") got exactly
nothing. A control that is present, writable, displayable and binds nothing is the same
defect as a budget pill that lies.

Why it was left is worth stating, because it is what this module is for:
:class:`~aegis.agent.deps.AgentConfig` is built once, **synchronously**, in a host's
composition root, while settings resolution is **per tenant and async** — it needs a
database read, and the tenant is not known until the request. Honouring the setting
therefore means resolving it *per run*, which is what :func:`resolve_agent_config` does:
it takes the process-wide config, folds the resolved value of every bound key onto it,
and returns a **new** config object. Nothing here is cached, module-level or otherwise —
one tenant's floor reused for another tenant's run would turn a safety control into a
cross-tenant leak, which is the failure this codebase has already paid for five times.

Two properties are load-bearing:

* **The fold is** :func:`~aegis.settings.spec.strictest`, never an assignment. The
  process config is one more layer in the chain, so a tenant can only ever *tighten*
  what the host wired — a host that pinned ``gate_min_risk=LOW`` for a run is not
  loosened to ``high`` by a tenant who never wrote anything.
* **A resolution failure fails closed, loudly.** If the tenant is known but their
  settings cannot be read, we do not know what they asked for, and the platform default
  is by construction the *loosest* value they could have chosen. So every bound key
  clamps to the strictest value its spec allows — the gate fires on everything, the
  fan-out narrows to one — and an ``ERROR`` names the tenant. Degrading to the platform
  default instead would silently discard a tenant's tightening, which is precisely the
  defect this module exists to remove.

Requires the ``aegis[data]`` extra (the resolver's session). Deliberately does **not**
import :mod:`aegis.agent`: the config object is handled structurally through
:func:`dataclasses.replace`, so the settings package stays free of the agent package and
of anything the agent package pulls in.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aegis.core.types import RiskLevel
from aegis.settings.resolver import resolve_all
from aegis.settings.spec import MergeRule, spec_for, strictest, strictest_legal

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from sqlalchemy.ext.asyncio import AsyncSession

    from aegis.agent.deps import AgentConfig

__all__ = [
    "AGENT_SETTING_BINDINGS",
    "resolve_agent_config",
    "strictest_agent_config",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Binding:
    """One catalogue key wired to one :class:`~aegis.agent.deps.AgentConfig` field.

    Attributes:
        key: The catalogue key, e.g. ``agent.gate_min_risk``.
        field: The ``AgentConfig`` attribute it governs.
        to_setting: Convert the config's Python value to the catalogue's stored form
            (``RiskLevel.HIGH`` → ``"high"``), so the two are comparable at all.
        from_setting: The inverse, applied to whichever value won the fold.
    """

    key: str
    field: str
    to_setting: Callable[[Any], Any] = lambda value: value
    from_setting: Callable[[Any], Any] = lambda value: value


#: **The wire.** Every entry is a control a tenant can already write that now binds
#: something. Keys are deliberately only the ``TIGHTEN_ONLY`` ones: "the tenant's value
#: wins if it is stricter" is the only merge rule that is safe to apply to a config the
#: host built for its own reasons, and an ``OVERRIDE`` key (``agent.model``,
#: ``agent.mode``) belongs to the request, not to the process config.
#:
#: Note what this means for a host that also reads one of these from its environment
#: (``AgentDeps.default()`` builds ``agentic_retrieval_max_rounds`` from settings): for a
#: **governed** run the catalogue is the authority, so an environment value looser than
#: the platform's catalogue default loses the fold. That is the rule working, not a
#: surprise — one number, one place, and the stricter layer wins — but it is written down
#: here because two sources for one figure is how a control stops meaning what it says.
AGENT_SETTING_BINDINGS: tuple[_Binding, ...] = (
    _Binding(
        key="agent.gate_min_risk",
        field="gate_min_risk",
        to_setting=lambda risk: RiskLevel(risk).value,
        from_setting=RiskLevel,
    ),
    _Binding(key="agent.max_plan_iterations", field="max_plan_iterations"),
    _Binding(
        key="agent.agentic_retrieval_max_rounds", field="agentic_retrieval_max_rounds"
    ),
    _Binding(key="agent.team.max_parallel", field="max_parallel_agents"),
)


# Import-time coherence, in the style of the catalogue itself: a binding to a key that
# is not tighten_only, or to one with no clampable domain, is a programming error whose
# only symptom would otherwise be a fail-open during a database outage.
for _binding in AGENT_SETTING_BINDINGS:
    _spec = spec_for(_binding.key)
    if _spec.inert_reason is not None:
        raise ValueError(
            f"{_binding.key} is bound to AgentConfig here but the catalogue still "
            f"declares it inert: {_spec.inert_reason!r}. One of the two is lying; "
            "clear inert_reason when the key gains a consumer."
        )
    if _spec.merge is not MergeRule.TIGHTEN_ONLY:
        raise ValueError(
            f"{_binding.key} merges by {_spec.merge.value}; only a tighten_only key may "
            "be folded onto a host's AgentConfig, because only tightening is safe to "
            "apply to a value the host chose"
        )
    strictest_legal(_spec)
del _binding, _spec


def strictest_agent_config(config: AgentConfig) -> AgentConfig:
    """Return ``config`` clamped to the strictest value every bound key allows.

    The fail-closed value. Used when a tenant is known but their settings could not be
    read: the tenant may have tightened any of these keys, we cannot tell which, and the
    platform default is the loosest thing they could have chosen — so we take the
    strictest thing they *could* have. The gate fires on every tool, and the fan-out
    narrows to a single agent.

    Args:
        config: The process-wide configuration.

    Returns:
        A new configuration; ``config`` is never mutated.
    """
    updates: dict[str, Any] = {}
    for binding in AGENT_SETTING_BINDINGS:
        spec = spec_for(binding.key)
        updates[binding.field] = binding.from_setting(strictest_legal(spec))
    return dataclasses.replace(config, **updates)


def _fold(binding: _Binding, config: AgentConfig, resolved: Any) -> Any:  # noqa: ANN401
    """Return the value of ``binding.field`` after folding ``resolved`` onto ``config``.

    Args:
        binding: The key/field wire.
        config: The configuration being resolved for this run.
        resolved: What the settings chain resolved for this tenant.

    Returns:
        The stricter of the two, in the config's own type.
    """
    spec = spec_for(binding.key)
    current = binding.to_setting(getattr(config, binding.field))
    try:
        winner = strictest(spec, resolved, current)
    except ValueError:
        # The host wired a value outside the catalogue's declared domain, so the two are
        # not comparable. The catalogue is the authority for these keys and its value is
        # at least legal, so it wins — and it is said out loud, because a host config the
        # catalogue cannot rank is a bug in the host.
        logger.warning(
            "%s: the process config holds %r, which is not a legal value for the "
            "catalogue key; using the resolved %r instead",
            binding.key,
            current,
            resolved,
        )
        winner = resolved
    return binding.from_setting(winner)


async def resolve_agent_config(
    session: AsyncSession,
    config: AgentConfig,
    *,
    tenant_id: int | None,
    user_id: int | None = None,
) -> AgentConfig:
    """Return ``config`` with every bound control tightened to this tenant's floor.

    Called **per run**, by a host, once the request's tenant is known. It returns a new
    object and holds no state: nothing here may outlive the run it was resolved for.

    Args:
        session: An async session. The caller is expected to have bound the tenant scope
            (:func:`aegis.governance.rls.set_tenant_scope`), as every governed read does.
        config: The process-wide configuration the host built.
        tenant_id: The tenant this run belongs to. ``None`` means an ungoverned run —
            there is no tenant layer to resolve, and ``config`` is returned unchanged.
        user_id: The acting user, so a user-scoped tightening is honoured too.

    Returns:
        A new configuration, never weaker than ``config`` in any bound key. On a
        resolution failure, :func:`strictest_agent_config` — never ``config``.
    """
    if tenant_id is None:
        # Ungoverned / offline: there is no tenant layer, and the host's config already
        # *is* the platform layer. Not a failure, so not a fail-closed clamp.
        return config
    try:
        resolved = await resolve_all(session, tenant_id=tenant_id, user_id=user_id)
        updates = {
            binding.field: _fold(binding, config, resolved[binding.key][0])
            for binding in AGENT_SETTING_BINDINGS
        }
    except Exception:  # noqa: BLE001 - a safety floor we cannot read fails closed, loudly
        logger.error(
            "Could not resolve the agent settings for tenant %s; failing closed to the "
            "strictest floor every bound key allows (the human gate fires on every "
            "tool, the team narrows to one agent) rather than to the platform default, "
            "which is the loosest value this tenant could have chosen.",
            tenant_id,
            exc_info=True,
        )
        return strictest_agent_config(config)
    return dataclasses.replace(config, **updates)
