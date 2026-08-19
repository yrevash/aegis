"""The host half of the guardrail settings seam: resolve this request's tenant policy.

The mirror of :func:`app.agent.deps.resolve_run_config`, and the reason
``guardrails.grounding.block``, ``guardrails.topical.block``,
``guardrails.denylist.terms`` and ``guardrails.pii.entities`` stopped being four
catalogue keys that saved, wrote an audit row, badged themselves "Your setting" and
reached nothing at all.

:data:`app.guardrails._guard` is a process-wide :class:`~aegis.guardrails.Guardrails`
built once at import from environment configuration. The four keys are per tenant and
resolved asynchronously, and the tenant is not known until a request arrives. So the
policy is folded on **per request**, here, and the result is a *new* pipeline object
that lives exactly as long as the rail call that asked for it. Nothing is memoised: a
cached policy is one tenant's denylist applied to another tenant's next question.

**Fail closed, loudly.** Anything that goes wrong returns
:func:`~aegis.settings.guardrails.strictest_guardrail_policy` — both rails hard-block,
the collections fall back to the platform floor — never the host's policy, because the
host's policy is the loosest configuration the tenant could have chosen and quietly
handing back something looser than what they set is the exact defect this seam removes.
"""

from __future__ import annotations

import logging

from aegis.guardrails.policy import GuardrailPolicy

logger = logging.getLogger(__name__)

__all__ = ["resolve_request_policy"]


def _current_tenant_id() -> int | None:
    """Return the request's tenant id from the governance context (``None`` if unset)."""
    try:
        from app.core.governance import get_governance_context

        gov = get_governance_context()
        return gov.tenant_id if gov is not None else None
    except Exception:  # noqa: BLE001 - governance is optional at this seam
        return None


def _current_user_id() -> int | None:
    """Return the request's user id from the governance context (``None`` if unset)."""
    try:
        from app.core.governance import get_governance_context

        gov = get_governance_context()
        return gov.user_id if gov is not None else None
    except Exception:  # noqa: BLE001 - governance is optional at this seam
        return None


async def resolve_request_policy(policy: GuardrailPolicy) -> GuardrailPolicy:
    """Return ``policy`` tightened to the rails **this request's tenant** resolved.

    Two paths return ``policy`` unchanged, and neither is a failure:

    * **No tenant** — an ungoverned/offline call (a unit test, a CLI, a startup probe)
      has no tenant layer to resolve.
    * **No durable store** — with ``stores_enabled`` off there is no ``settings`` table
      and no governance at all, so there is nothing to read. A tenant bound in that mode
      is incoherent and is said out loud rather than assumed.

    Anything else that goes wrong fails closed to
    :func:`~aegis.settings.guardrails.strictest_guardrail_policy`.

    Args:
        policy: The process-wide policy the composition root wired.

    Returns:
        The policy the rails must enforce for this request.
    """
    tenant_id = _current_tenant_id()
    if tenant_id is None:
        return policy

    from app.config import get_settings

    if not get_settings().stores_enabled:
        logger.warning(
            "Tenant %s is bound but no durable store is configured, so its guardrail "
            "settings cannot be read; the platform defaults are the only rails that "
            "exist in this mode.",
            tenant_id,
        )
        return policy

    from aegis.settings.guardrails import (
        resolve_guardrail_policy,
        strictest_guardrail_policy,
    )

    from app.data.session import get_sessionmaker, set_tenant_scope

    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            resolved = await resolve_guardrail_policy(
                session, policy, tenant_id=tenant_id, user_id=_current_user_id()
            )
            await session.rollback()
    except Exception:  # noqa: BLE001 - an unreadable rail fails closed, loudly
        logger.error(
            "Could not open a session to resolve tenant %s's guardrail settings; "
            "failing closed to the strictest configuration rather than to the platform "
            "default.",
            tenant_id,
            exc_info=True,
        )
        return strictest_guardrail_policy(policy)
    return resolved
