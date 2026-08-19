"""Binding the guardrail controls to the catalogue — per tenant, per request.

**The gap this closes.** ``guardrails.grounding.block``, ``guardrails.topical.block``,
``guardrails.denylist.terms`` and ``guardrails.pii.entities`` were in the catalogue,
were writable by a tenant admin, saved, wrote an audit row, and rendered on the settings
screen badged "Your setting" — and *nothing read any of them*. The grounding and topical
toggles were host-wired from environment variables that no tenant can see; the denylist
and the PII entity set were mentioned only in docstrings. The three §7.6 keys added
since — ``guardrails.denylist.patterns``, ``guardrails.pii.block`` and
``guardrails.input.max_chars`` — were written against this module from their first line,
which is the point of it: a guardrail control and the wire that makes it real arrive
together or not at all. A control that saves, is
audited, is badged as yours, and changes nothing is worse than an absent control,
because the absent one does not lie about it.

This module is the exact analogue of :mod:`aegis.settings.agent`, for the same reason
and with the same shape: :class:`~aegis.guardrails.pipeline.Guardrails` is built once,
**synchronously**, in a host's composition root, while settings resolution is **per
tenant and async**. Honouring the keys therefore means resolving them *per request*
and folding them onto the pipeline the host built —
:func:`resolve_guardrail_policy` returns a **new**
:class:`~aegis.guardrails.policy.GuardrailPolicy`, and the host hands it to
:meth:`~aegis.guardrails.pipeline.Guardrails.with_policy`, which returns a new pipeline.
Nothing here is cached, module-level or otherwise: one tenant's denylist reused for
another tenant's request is a cross-tenant leak wearing a safety control's clothes.

Three properties are load-bearing:

* **The fold is the spec's own merge rule, never an assignment.** The booleans and the
  input-length ceiling fold with :func:`~aegis.settings.spec.strictest`
  (``TIGHTEN_ONLY``) and the collections with a union (``UNION``), so what the host
  wired is one more layer in the chain and a tenant can only ever *tighten*. A host
  that set ``grounding_block=True`` from its environment is not loosened by a tenant who
  never wrote anything, and a tenant naming three PII kinds cannot switch off the six
  the engine already screens.
* **A resolution failure fails closed, loudly.** If the tenant is known but their
  settings cannot be read we do not know what they asked for, and the platform default
  is by construction the loosest value they could have chosen. So the booleans clamp to
  blocking, the collections keep everything already known, and an ``ERROR`` names the
  tenant.
* **Nothing is resolved for an unknown tenant.** ``tenant_id=None`` is an ungoverned run
  — there is no tenant layer to read — and the host's own policy is returned unchanged.
  That is not a failure and so is not a fail-closed clamp.

Requires the ``aegis[data]`` extra (the resolver's session). Deliberately does **not**
import :mod:`aegis.guardrails.pipeline`: the policy object is a stdlib-only leaf
dataclass handled through :func:`dataclasses.replace`, so the settings package stays
free of the guardrails package and of everything it pulls in.
"""

from __future__ import annotations

import dataclasses
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aegis.guardrails.policy import GuardrailPolicy
from aegis.settings.resolver import resolve_all
from aegis.settings.spec import MergeRule, spec_for, strictest, strictest_legal

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "GUARDRAIL_SETTING_BINDINGS",
    "resolve_guardrail_policy",
    "strictest_guardrail_policy",
]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Binding:
    """One catalogue key wired to one :class:`GuardrailPolicy` field.

    Attributes:
        key: The catalogue key, e.g. ``guardrails.denylist.terms``.
        field: The :class:`GuardrailPolicy` attribute it governs.
    """

    key: str
    field: str


#: **The wire.** Every entry is a control a tenant could already write that now reaches
#: a rail. Unlike :data:`aegis.settings.agent.AGENT_SETTING_BINDINGS` this set is not
#: restricted to ``TIGHTEN_ONLY``: ``UNION`` is equally safe to fold onto a host's
#: configuration, because a union of the host's set with the tenant's is a superset of
#: the host's by arithmetic. ``OVERRIDE`` is the rule that is *not* safe here — "the
#: last scope wins" applied to a rail would let a scope turn a guardrail off — and the
#: import-time check below refuses one.
GUARDRAIL_SETTING_BINDINGS: tuple[_Binding, ...] = (
    _Binding(key="guardrails.topical.block", field="topical_block"),
    _Binding(key="guardrails.grounding.block", field="grounding_block"),
    _Binding(key="guardrails.denylist.terms", field="denylist_terms"),
    _Binding(key="guardrails.denylist.patterns", field="denylist_patterns"),
    _Binding(key="guardrails.pii.entities", field="pii_entities"),
    _Binding(key="guardrails.pii.block", field="pii_block"),
    _Binding(key="guardrails.input.max_chars", field="input_max_chars"),
)

# §7.16 row 7, at import. Every field of the policy is a tenant control, so a field
# with no binding is a control nobody declared and a binding with no field is a
# control that reaches nothing — and a field NAMING a model would be the row itself:
# pointing the injection classifier at a tenant-selected deployment disables it
# without appearing to, because the rail still runs, still reports and passes
# everything. The suite asserts the same two facts (``test_forbidden_controls``); this
# is the copy that refuses to import, because a guardrail that fails at request time
# has already failed.
_BOUND_FIELDS = {binding.field for binding in GUARDRAIL_SETTING_BINDINGS}
_ALL_POLICY_FIELDS = {field.name for field in dataclasses.fields(GuardrailPolicy)}
if _BOUND_FIELDS != _ALL_POLICY_FIELDS:
    raise ValueError(
        "GuardrailPolicy and the catalogue bindings have drifted: "
        f"{sorted(_BOUND_FIELDS ^ _ALL_POLICY_FIELDS)}. Every field of the policy is "
        "reachable by a tenant write, so an unbound one is a tenant control that no "
        "catalogue key declares, validates or audits."
    )
_MODEL_WORDS = ("model", "completer", "deployment", "endpoint")
if [name for name in _ALL_POLICY_FIELDS if any(w in name for w in _MODEL_WORDS)]:
    raise ValueError(
        "a tenant-writable guardrail field now names a model, a completer or a "
        f"deployment: {sorted(_ALL_POLICY_FIELDS)}. §7.16 row 7 forbids it — the "
        "guardrail completer is deliberately separate from the answer completer, and a "
        "classifier pointed at a tenant's model disables itself without appearing to."
    )

# Import-time coherence, in the style of the catalogue itself: a binding to a key whose
# merge rule could produce something weaker than the host wired is a programming error
# whose only symptom would otherwise be a guardrail that a tenant switched off.
_POLICY_FIELDS = _ALL_POLICY_FIELDS
for _binding in GUARDRAIL_SETTING_BINDINGS:
    if _binding.field not in _POLICY_FIELDS:
        raise ValueError(
            f"{_binding.key} binds GuardrailPolicy.{_binding.field}, which does not "
            f"exist; the policy carries {sorted(_POLICY_FIELDS)}"
        )
    _spec = spec_for(_binding.key)
    if _spec.inert_reason is not None:
        raise ValueError(
            f"{_binding.key} is bound to GuardrailPolicy here but the catalogue still "
            f"declares it inert: {_spec.inert_reason!r}. One of the two is lying; "
            "clear inert_reason when the key gains a consumer."
        )
    if _spec.merge is MergeRule.OVERRIDE:
        raise ValueError(
            f"{_binding.key} merges by {_spec.merge.value}; a guardrail may not be "
            "folded onto a host's pipeline by 'last scope wins', because that is how a "
            "scope turns a rail off. Use tighten_only or union."
        )
    if _spec.merge is MergeRule.TIGHTEN_ONLY:
        strictest_legal(_spec)  # raises now rather than during an outage
del _binding, _spec


def _union(current: Any, resolved: Any) -> tuple[str, ...]:  # noqa: ANN401 - any value
    """Return the host's members followed by any the tenant added, de-duplicated.

    Args:
        current: What the host wired (a sequence of strings).
        resolved: What the settings chain resolved for this tenant.

    Returns:
        The union, host-first and order-stable so a console renders it predictably.
    """
    merged: dict[str, None] = {}
    for source in (current, resolved):
        for value in source or ():
            if isinstance(value, str) and value.strip():
                merged.setdefault(value.strip(), None)
    return tuple(merged)


def strictest_guardrail_policy(policy: GuardrailPolicy) -> GuardrailPolicy:
    """Return ``policy`` clamped to the strictest thing every bound key allows.

    The fail-closed value. Used when a tenant is known but their settings could not be
    read: the tenant may have tightened any of these keys, we cannot tell which, and the
    platform default is the loosest thing they could have chosen — so we take the
    strictest thing they *could* have. Both rails become hard blocks.

    The two ``UNION`` keys are the interesting case, because "the strictest possible
    union" is unbounded — a tenant could have added any term at all, and we cannot
    invent their words. What is knowable is the **platform floor**: the catalogue's own
    default, which is in force for every tenant and needs no database read. So the
    collections keep the host's members plus that floor, and lose only the tenant's own
    additions, which is the smallest honest degradation available. It is still a
    degradation, which is why the caller logs it.

    Args:
        policy: The host's process-wide policy.

    Returns:
        A new policy; ``policy`` is never mutated.
    """
    updates: dict[str, Any] = {}
    for binding in GUARDRAIL_SETTING_BINDINGS:
        spec = spec_for(binding.key)
        current = getattr(policy, binding.field)
        if spec.merge is MergeRule.TIGHTEN_ONLY:
            updates[binding.field] = strictest_legal(spec)
        else:
            updates[binding.field] = _union(current, spec.default)
    return dataclasses.replace(policy, **updates)


def _fold(binding: _Binding, policy: GuardrailPolicy, resolved: Any) -> Any:  # noqa: ANN401
    """Return the value of ``binding.field`` after folding ``resolved`` onto ``policy``.

    Args:
        binding: The key/field wire.
        policy: The policy being resolved for this request.
        resolved: What the settings chain resolved for this tenant.

    Returns:
        The stricter of the two for a ``TIGHTEN_ONLY`` key, their union for a ``UNION``
        one — in the policy's own type.
    """
    spec = spec_for(binding.key)
    current = getattr(policy, binding.field)
    if spec.merge is not MergeRule.TIGHTEN_ONLY:
        return _union(current, resolved)
    try:
        return strictest(spec, resolved, current)
    except ValueError:
        # The host wired a value outside the catalogue's declared domain, so the two are
        # not comparable. The catalogue is the authority for these keys and its value is
        # at least legal, so it wins — said out loud, because a host config the
        # catalogue cannot rank is a bug in the host.
        logger.warning(
            "%s: the process pipeline holds %r, which is not a legal value for the "
            "catalogue key; using the resolved %r instead",
            binding.key,
            current,
            resolved,
        )
        return resolved


async def resolve_guardrail_policy(
    session: AsyncSession,
    policy: GuardrailPolicy,
    *,
    tenant_id: int | None,
    user_id: int | None = None,
) -> GuardrailPolicy:
    """Return ``policy`` with every bound rail tightened to this tenant's floor.

    Called **per request**, by a host, once the tenant is known. It returns a new object
    and holds no state: nothing here may outlive the request it was resolved for.

    Args:
        session: An async session. The caller is expected to have bound the tenant scope
            (:func:`aegis.governance.rls.set_tenant_scope`), as every governed read does.
        policy: The process-wide policy the host wired.
        tenant_id: The tenant this request belongs to. ``None`` means an ungoverned
            request — there is no tenant layer to resolve, and ``policy`` is returned
            unchanged.
        user_id: The acting user, so a user-scoped tightening is honoured too.

    Returns:
        A new policy, never weaker than ``policy`` in any bound key. On a resolution
        failure, :func:`strictest_guardrail_policy` — never ``policy``.
    """
    if tenant_id is None:
        # Ungoverned / offline: there is no tenant layer, and the host's policy already
        # *is* the platform layer. Not a failure, so not a fail-closed clamp.
        return policy
    try:
        resolved = await resolve_all(session, tenant_id=tenant_id, user_id=user_id)
        updates = {
            binding.field: _fold(binding, policy, resolved[binding.key][0])
            for binding in GUARDRAIL_SETTING_BINDINGS
        }
    except Exception:  # noqa: BLE001 - a rail we cannot read fails closed, loudly
        logger.error(
            "Could not resolve the guardrail settings for tenant %s; failing closed to "
            "the strictest configuration every bound key allows (the grounding and "
            "topical rails hard-block, and the denied terms and PII kinds fall back to "
            "the platform floor) rather than to the platform default, which is the "
            "loosest configuration this tenant could have chosen. This tenant's own "
            "additions to the denylist and PII set are NOT in force for this request.",
            tenant_id,
            exc_info=True,
        )
        return strictest_guardrail_policy(policy)
    return dataclasses.replace(policy, **updates)
