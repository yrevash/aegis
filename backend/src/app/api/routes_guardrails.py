"""The guardrail control plane — what this tenant's rails do, and who decided each one.

§7.6(i): *"read the platform defaults first — nearly free, and it is a trust feature."*
``GET /guardrails/policy`` returns the effective rail stack as data — each rail's name,
what it screens, hard-block versus advisory, its threshold, and whether the model-backed
layer is wired — beside the per-tenant controls that decide it. Readable by every
authenticated role, because *"here is exactly what we screen, read it yourself"* is a
stronger enterprise answer than a marketing page and it costs a serialiser.

**Provenance is the point, not decoration.** A rail screen that shows a value without
saying where the value came from cannot be reasoned about: an operator looking at
"blocks off-topic questions" has no way to tell whether that is the platform's floor
(and therefore not theirs to relax) or their own tenant's tightening (and therefore
theirs to review). So every control row carries three things:

``value``
    What the rails enforce for this request's tenant — read off the folded
    :class:`~aegis.guardrails.policy.GuardrailPolicy`, never off the settings table
    alone, because the host wires a floor of its own that no settings row mentions.
``platform_value``
    The floor: what the same rails enforce with no tenant layer at all.
``source``
    ``platform`` when the two agree — the tenant has changed nothing that binds, even if
    they wrote a row that lost — and ``tenant``/``user`` only when the tenant's own
    write is what moved it. Derived by comparing the two policies rather than by
    trusting the settings chain's own badge, because a tenant row that loses to a
    stricter host is *not* what is in force and saying otherwise would be the lie the
    field exists to prevent.

For the two ``UNION`` keys the badge is not enough on its own — the effective set is a
merge, not a winner — so the row also itemises ``added``: the members that are the
tenant's own and are not in the floor.

**There is no write here, deliberately.** Every one of these controls is a catalogue key,
so the write is ``PUT /settings/{key}`` in :mod:`app.api.routes_console`, which already
validates against the spec, refuses a weakening with the resolver's own reason and
audits. A second write path for the same keys would be a second policy that can disagree
with the first — the defect §7.6 is written against.
"""

from __future__ import annotations

import logging
from typing import Any

from aegis.settings.guardrails import GUARDRAIL_SETTING_BINDINGS, fold_resolved
from aegis.settings.resolver import resolve_all
from aegis.settings.spec import setting_controls, spec_for
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

from app.api.routes import AuthContext, require_auth

__all__ = [
    "GuardrailControlRow",
    "GuardrailPolicyResponse",
    "GuardrailRailRow",
    "guardrails_router",
    "mount",
]

logger = logging.getLogger(__name__)

guardrails_router = APIRouter()

#: ``GuardrailPolicy`` field → the catalogue key that governs it. Inverted from the one
#: declaration of that wire rather than restated: a rail describes itself in terms of
#: the policy fields it reads, and only :data:`GUARDRAIL_SETTING_BINDINGS` knows which
#: key each field belongs to. A second copy here is how a screen ends up attributing a
#: value to the wrong control.
_KEY_FOR_FIELD: dict[str, str] = {b.field: b.key for b in GUARDRAIL_SETTING_BINDINGS}


class GuardrailRailRow(BaseModel):
    """One rail in the stack, as the pipeline itself describes it."""

    id: str
    layer: str = Field(description="The verdict label this rail stamps, for the console.")
    name: str
    screens: str
    stage: str = Field(description="input | output | both. Input covers tool results.")
    enforcement: str = Field(description="block | redact | advisory | off.")
    active: bool = Field(description="Whether the rail runs at all as configured.")
    model_backed: bool = Field(
        description="Whether it needs the guardrail completer, which is the platform's "
        "and never a tenant's choice of model (§7.16 row 7)."
    )
    threshold: str | None = None
    settings: list[str] = Field(
        default_factory=list,
        description="The catalogue keys that govern this rail, if any.",
    )


class GuardrailControlRow(BaseModel):
    """One control, its effective value, and where that value came from."""

    key: str
    value: Any = Field(description="What the rails enforce for this tenant.")
    platform_value: Any = Field(description="The floor: the same rails with no tenant layer.")
    source: str = Field(description="platform | tenant | user — who decided the value.")
    added: list[Any] | None = Field(
        default=None,
        description="For a union key, the members this tenant added on top of the floor.",
    )
    writable: bool = Field(description="Whether this caller's role may write the key.")
    control: dict[str, Any] = Field(description="The catalogue's UI descriptor.")


class GuardrailPolicyResponse(BaseModel):
    """Body for ``GET /guardrails/policy``."""

    tenant_id: int | None = None
    resolved: bool = Field(
        description="False when no tenant layer was read — an ungoverned or storeless "
        "deployment, where the platform floor is the whole policy."
    )
    model_layer_wired: bool = Field(
        description="Whether the model-backed rails can run in this process."
    )
    rails: list[GuardrailRailRow]
    controls: list[GuardrailControlRow]


def _tenant_of(auth: AuthContext) -> int | None:
    """Return the caller's sealed tenant scope, never a wire field (§7.16 row 12)."""
    from aegis.retrieval.types import UntenantedPrincipalError, tenant_filter

    try:
        return tenant_filter(auth.tenant_scope())
    except UntenantedPrincipalError:
        return None


def _rail_rows(guard: Any) -> list[GuardrailRailRow]:  # noqa: ANN401 - the pipeline
    """Project the pipeline's own rail descriptions onto the wire."""
    return [
        GuardrailRailRow(
            id=rail.id,
            layer=rail.layer,
            name=rail.name,
            screens=rail.screens,
            stage=rail.stage,
            enforcement=rail.enforcement if rail.active else "off",
            active=rail.active,
            model_backed=rail.model_backed,
            threshold=rail.threshold,
            settings=[_KEY_FOR_FIELD[field] for field in rail.policy_fields],
        )
        for rail in guard.rail_stack()
    ]


def _control_rows(effective: Any, floor: Any, *, fine_role: str) -> list[GuardrailControlRow]:  # noqa: ANN401
    """Compare the folded policy against the floor, one row per bound catalogue key.

    The source is *derived from the two policies*, not read off the settings chain: a
    tenant row that lost to a stricter platform value is not in force, and badging it
    "your setting" is precisely the lie this endpoint exists to end.
    """
    rows: list[GuardrailControlRow] = []
    for binding in GUARDRAIL_SETTING_BINDINGS:
        spec = spec_for(binding.key)
        value = getattr(effective, binding.field)
        floor_value = getattr(floor, binding.field)
        added: list[Any] | None = None
        if isinstance(value, tuple | list):
            value, floor_value = list(value), list(floor_value)
            added = [member for member in value if member not in floor_value]
        rows.append(
            GuardrailControlRow(
                key=binding.key,
                value=value,
                platform_value=floor_value,
                source="platform" if value == floor_value else "tenant",
                added=added,
                writable=fine_role in spec.writable_by,
                control=setting_controls([spec])[0],
            )
        )
    return rows


@guardrails_router.get(
    "/guardrails/policy", response_model=GuardrailPolicyResponse, tags=["guardrails"]
)
async def guardrail_policy(
    auth: AuthContext = Depends(require_auth),
) -> GuardrailPolicyResponse:
    """Return the rail stack this caller's tenant enforces, with each value's source.

    The stack is read off the **folded** pipeline — the platform's floor with this
    tenant's tightening applied, exactly as a request would resolve it — so what an
    operator reads here is what a question would meet.

    Raises:
        HTTPException: 503 when the settings store is configured but unreadable. A
            floor reported as the effective policy while a tenant's tightening sits
            unread is the one answer this endpoint must never give.
    """
    from app.config import get_settings
    from app.guardrails import _guard

    floor = _guard.policy
    tenant_id = _tenant_of(auth)
    resolved_tenant = False
    guard = _guard
    if tenant_id is not None and get_settings().stores_enabled:
        from aegis.settings.guardrails import resolve_guardrail_policy

        from app.data.session import get_sessionmaker, set_tenant_scope

        try:
            async with get_sessionmaker()() as session:
                await set_tenant_scope(session, tenant_id)
                policy = await resolve_guardrail_policy(
                    session, floor, tenant_id=tenant_id, user_id=auth.user_id
                )
                # The floor is the platform layer, which includes any platform-scoped
                # settings row — not only what the host wired in code.
                platform_only = await resolve_all(session, tenant_id=None)
                await session.rollback()
        except SQLAlchemyError as exc:
            logger.error("Guardrail policy read failed for tenant %s.", tenant_id, exc_info=True)  # noqa: TRY400
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "The settings store is unreachable, so no rail can be reported as "
                    "in force. The rails themselves fail closed; retry once the "
                    "database is back."
                ),
            ) from exc
        # The floor a tenant is measured against is *the platform layer*: the host's
        # composition root AND whatever a platform admin wrote at platform scope.
        # Folding it with the same function the request path uses is what keeps the
        # number on the screen equal to the number the rails enforce.
        floor = fold_resolved(floor, platform_only)
        guard = _guard.with_policy(policy)
        resolved_tenant = True
    return GuardrailPolicyResponse(
        tenant_id=tenant_id,
        resolved=resolved_tenant,
        model_layer_wired=_model_layer_wired(),
        rails=_rail_rows(guard),
        controls=_control_rows(guard.policy, floor, fine_role=auth.fine_role),
    )


def _model_layer_wired() -> bool:
    """Whether the model-backed rails can run in this process.

    Read off the pipeline rather than off configuration: an operator deciding whether to
    trust a block rate needs the fact, and "the setting says a completer is configured"
    is not the same fact as "there is one".
    """
    from app.guardrails import _guard

    return _guard._completer is not None  # noqa: SLF001 - the honest answer, not a guess


def mount(target: APIRouter) -> None:
    """Attach this module's route to ``target`` as a real ``APIRoute``.

    Idempotent, exactly like :func:`app.api.routes_memory.mount` and for the same
    reason: this is mounted from the composition root while :mod:`app.api.routes` is
    being edited elsewhere, and a second shadowed copy of a handler is invisible at
    runtime and confusing in the one place the route-coverage test reads.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in guardrails_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
