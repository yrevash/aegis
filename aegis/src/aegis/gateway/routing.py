"""Config-driven model registry: the fleet, a *role → model* routing table, cost tables.

Heterogeneous model routing means code requests a model by **role**, never by a
hard-coded id, so swapping the underlying fleet is a one-file change
(env-overridable per role). This module owns the routing table and the per-role
cost-per-1k lookup the gateway falls back to when a provider's own cost map has
no entry for a custom deployment id.

It also owns the **allowed-deployment set** — :data:`_FLEET_DECLARATION`, the platform's
statement of which deployments exist and which of them a tenant may select. That set is
what makes ``agent.model`` a control instead of a dropdown: the settings catalogue reads
its legal values from :func:`tenant_model_choices`, so the enum a screen renders is a
projection of the set the server validates against; :func:`check_tenant_may_select`
refuses anything else at the write path *and* at the point of use; and no deployment on
a role the host's own safety layers call is selectable at all
(:func:`guardrail_reserved_roles`), which is §7.16 row 7 enforced at import rather than
by a check somebody remembers to write.

Depends only on :mod:`aegis.core.models` — no litellm, no network.
"""

from __future__ import annotations

import os
import re
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum

from aegis.core.models import ModelRole

__all__ = [
    "PLATFORM_DEFAULT",
    "BillingUnit",
    "Deployment",
    "DeploymentNotAllowedError",
    "allowed_deployments",
    "baseline_role",
    "billable_input_units",
    "billing_unit",
    "check_tenant_may_select",
    "deployment_for_choice",
    "guardrail_reserved_roles",
    "is_routable_role",
    "is_small_model",
    "model_for",
    "role_for_deployment",
    "routing_table",
    "selected_deployment",
    "tenant_model_choices",
    "tenant_selectable_deployments",
    "unit_cost",
]

# Default role → deployment-id map. Override any entry via an env var of the form
# ``MODEL_<ROLE>`` (e.g. ``MODEL_GENERATION=genailab-maas-DeepSeek-V3-0324``).
_DEFAULT_ROUTING: dict[ModelRole, str] = {
    ModelRole.CHEAP: "genailab-maas-gpt-4o-mini",
    ModelRole.REASONING: "genailab-maas-Phi-4-reasoning",
    ModelRole.GENERATION: "genailab-maas-gpt-4o",
    ModelRole.EMBEDDING: "genailab-maas-text-embedding-3-large",
    ModelRole.VISION: "genailab-maas-Llama-3.2-90B-Vision-Instruct",
    ModelRole.VOICE: "genailab-maas-whisper",
}


# ─────────────────────────────────────────────────────────────────────────────
# The fleet — which deployments exist, and which a tenant may select (§7.16 row 6)
# ─────────────────────────────────────────────────────────────────────────────


class DeploymentNotAllowedError(ValueError):
    """A deployment the platform does not run, or does not let a tenant select.

    §7.16 row 6 asks the server to validate a model override against the platform's
    allowed deployments, and adds *"a UI enum is not enforcement"*. This is the refusal
    that makes the sentence true: an unknown id and a real-but-reserved id are both
    refused, and neither is quietly swapped for a default — a tenant who asked for a
    model they may not have must be told so, not served by something else under the
    same name.
    """


@dataclass(frozen=True, slots=True)
class Deployment:
    """One model deployment the platform runs, and what it costs.

    Attributes:
        id: The gateway deployment id, exactly as the provider spells it.
        role: The job this deployment does. It is also the **billing unit** and the
            fallback-chain tier it belongs to, so a deployment cannot be declared for
            one purpose and priced as another.
        input_rate: USD per one billable input unit (see :func:`billing_unit`).
        output_rate: USD per 1k completion tokens.
        tenant_selectable: Whether a tenant may point ``agent.model`` at it. Declared
            per deployment rather than inferred, because "the platform runs it" and
            "a tenant may choose it" are two different decisions and conflating them is
            how the guardrail classifier ends up on a tenant's model.
    """

    id: str
    role: ModelRole
    input_rate: float
    output_rate: float
    tenant_selectable: bool = False


#: The roles whose deployment a tenant may **never** redirect, because host-side safety
#: machinery calls them and nothing about that call is the tenant's to configure:
#: ``CHEAP`` is the injection / content-safety / topical classifier's completer, and
#: ``VISION`` is the media screen's. §7.16 row 7 is not a check somebody remembers to
#: write — pointing the classifier at a model of the tenant's choosing disables it
#: *without appearing to*, since the rail still runs, still reports, and passes
#: everything — so the reservation is declared here and enforced at import below.
_GUARDRAIL_RESERVED_ROLES: frozenset[ModelRole] = frozenset(
    {ModelRole.CHEAP, ModelRole.VISION}
)


def guardrail_reserved_roles() -> frozenset[ModelRole]:
    """Return the roles a tenant may never redirect (the safety layers' own models)."""
    return _GUARDRAIL_RESERVED_ROLES


#: **The allowed-deployment set.** The platform's whole fleet, declared once, exactly as
#: ``docs/architecture/backend.md`` §2 lists it ("Only these models may be used") — and
#: the ``tenant_selectable`` column is the platform saying which of them a tenant may
#: choose between. Only the main answer-generation tier is selectable: that is what
#: ``agent.model`` is a preference *about*, and everything else in this table is
#: infrastructure a tenant does not pick — the classifier the rails judge them by, the
#: embedding model their index is built with, Whisper, the media screen.
#:
#: The rates are the same kind of figure as :data:`_COST_PER_1K` — approximate, honest
#: and never zero — but they are **per deployment**, which is the point: a tenant who
#: selects DeepSeek-V3 must be ledgered at DeepSeek's price, not at gpt-4o's.
_FLEET_DECLARATION: tuple[Deployment, ...] = (
    Deployment("genailab-maas-gpt-35-turbo", ModelRole.CHEAP, 0.0005, 0.0015),
    Deployment("genailab-maas-gpt-4o-mini", ModelRole.CHEAP, 0.00015, 0.0006),
    Deployment("genailab-maas-Phi-4-reasoning", ModelRole.REASONING, 0.0011, 0.0044),
    Deployment("genailab-maas-DeepSeek-R1", ModelRole.REASONING, 0.00135, 0.0054),
    Deployment(
        "genailab-maas-gpt-4o", ModelRole.GENERATION, 0.0025, 0.01, tenant_selectable=True
    ),
    Deployment(
        "genailab-maas-DeepSeek-V3-0324",
        ModelRole.GENERATION,
        0.00027,
        0.0011,
        tenant_selectable=True,
    ),
    Deployment(
        "genailab-maas-Llama-3.3-70B-Instruct",
        ModelRole.GENERATION,
        0.00071,
        0.00071,
        tenant_selectable=True,
    ),
    Deployment(
        "genailab-maas-Llama-4-Maverick-17B-128E-Instruct-FP8",
        ModelRole.GENERATION,
        0.00035,
        0.00141,
        tenant_selectable=True,
    ),
    Deployment(
        "genailab-maas-text-embedding-3-large", ModelRole.EMBEDDING, 0.00013, 0.0
    ),
    Deployment(
        "genailab-maas-Llama-3.2-90B-Vision-Instruct", ModelRole.VISION, 0.0025, 0.01
    ),
    Deployment(
        "genailab-maas-Phi-3.5-vision-instruct", ModelRole.VISION, 0.0004, 0.0016
    ),
    Deployment("genailab-maas-whisper", ModelRole.VOICE, 0.006, 0.0),
)

_FLEET: dict[str, Deployment] = {entry.id: entry for entry in _FLEET_DECLARATION}
if len(_FLEET) != len(_FLEET_DECLARATION):
    raise ValueError("the fleet declares a deployment id twice")
for _entry in _FLEET_DECLARATION:
    # §7.16 row 7, structurally: a selectable deployment on a role the safety layers
    # call would hand a tenant the classifier that judges them. Refused at import, so
    # the mistake is a failed test run rather than a rail that silently passes
    # everything.
    if _entry.tenant_selectable and _entry.role in _GUARDRAIL_RESERVED_ROLES:
        raise ValueError(
            f"{_entry.id} is declared tenant-selectable on {_entry.role.value}, which is "
            "reserved for the host's own safety layers; a tenant selecting it would "
            "choose the model their own guardrails are run on"
        )
del _entry

#: The value ``agent.model`` carries when a tenant has expressed no preference — "use
#: whatever the platform routes". A sentinel rather than an empty string so the stored
#: value, the UI option and the audit record all say the same readable thing.
PLATFORM_DEFAULT = "default"

#: The deployment this call context has selected, or ``None`` for the platform's own
#: routing. A ContextVar because the selection belongs to **one run** — the gateway is
#: process-wide and shared by every tenant, and a module-level "current model" is a
#: cross-tenant leak with a shorter name.
_selection: ContextVar[str | None] = ContextVar("aegis_selected_deployment", default=None)


def allowed_deployments() -> dict[str, ModelRole]:
    """Return every deployment the platform runs, mapped to the role it serves."""
    return {entry.id: entry.role for entry in _FLEET.values()}


def tenant_selectable_deployments() -> tuple[str, ...]:
    """Return the deployment ids a tenant may point ``agent.model`` at, in fleet order.

    Read off :data:`_FLEET` rather than the declaration tuple so there is one runtime
    view of the set: the list an operator is offered and the check their write goes
    through can never be answering from different copies of it.
    """
    return tuple(entry.id for entry in _FLEET.values() if entry.tenant_selectable)


def tenant_model_choices() -> tuple[str, ...]:
    """Return the legal values of ``agent.model`` — the sentinel, then the fleet.

    The settings catalogue reads its choices from **this** function rather than
    restating them, so the enum a screen renders is a projection of the set the server
    validates against and cannot drift from it. That is the whole of "a UI enum is not
    enforcement": the enum is not the enforcement, it is a view of it.
    """
    return (PLATFORM_DEFAULT, *tenant_selectable_deployments())


def role_for_deployment(deployment: str) -> ModelRole:
    """Return the role ``deployment`` serves.

    Raises:
        DeploymentNotAllowedError: If the platform does not run it.
    """
    entry = _FLEET.get(deployment)
    if entry is None:
        raise DeploymentNotAllowedError(
            f"{deployment!r} is not a deployment this platform runs; the fleet is "
            f"{sorted(_FLEET)}"
        )
    return entry.role


def check_tenant_may_select(deployment: str) -> Deployment:
    """Return the fleet entry for ``deployment``, or refuse a tenant's choice of it.

    The single server-side gate for §7.16 row 6, called from the settings write path
    (through the catalogue) and again at the point of use, so a value that became
    unpermitted after it was stored cannot quietly take effect.

    Raises:
        DeploymentNotAllowedError: If the platform does not run the deployment, or runs
            it but does not offer it to tenants.
    """
    entry = _FLEET.get(deployment)
    if entry is None:
        raise DeploymentNotAllowedError(
            f"{deployment!r} is not a deployment this platform runs. Choose one of "
            f"{list(tenant_model_choices())}."
        )
    if not entry.tenant_selectable:
        raise DeploymentNotAllowedError(
            f"{deployment!r} is a real deployment but is not offered to tenants: it "
            f"serves the {entry.role.value} role, which the platform keeps for its own "
            "use. Choose one of "
            f"{list(tenant_model_choices())}."
        )
    return entry


def deployment_for_choice(choice: str | None) -> str | None:
    """Return the deployment an ``agent.model`` value names, or ``None`` for the default.

    Args:
        choice: The resolved value of ``agent.model``.

    Returns:
        The validated deployment id, or ``None`` when the tenant expressed no preference.

    Raises:
        DeploymentNotAllowedError: If the value names something a tenant may not select.
    """
    if choice is None or choice == PLATFORM_DEFAULT:
        return None
    check_tenant_may_select(choice)
    return choice


@contextmanager
def selected_deployment(deployment: str | None) -> Iterator[None]:
    """Bind ``deployment`` as the model answering its role, for this context only.

    Validated on entry rather than trusted: this is the second of the two places row 6
    is enforced, and it is the one that catches a value that was legal when it was
    written and is not any more.

    Args:
        deployment: A tenant-selectable deployment id, or ``None`` for no selection.

    Yields:
        Nothing; the selection is in force for the body.

    Raises:
        DeploymentNotAllowedError: If ``deployment`` is not one a tenant may select.
    """
    if deployment is not None:
        check_tenant_may_select(deployment)
    token = _selection.set(deployment)
    try:
        yield
    finally:
        _selection.reset(token)


def _routed_default(role: ModelRole) -> str:
    """Return the deployment ``role`` routes to when no tenant has chosen one.

    The platform's own answer — the table above, or the ``MODEL_<ROLE>`` environment
    override. Kept separate from :func:`model_for` because it is also the yardstick the
    pricing uses: a call answered by *this* deployment is priced at the role's own
    (env-overridable) rate exactly as it always was, and only a deployment other than
    this one is repriced from the fleet.
    """
    return os.environ.get(f"MODEL_{role.name}", _DEFAULT_ROUTING[role])


def model_for(role: ModelRole) -> str:
    """Return the deployment id in force for ``role`` on this call.

    The tenant's validated selection (:func:`selected_deployment`) wins for the **one**
    role that selection belongs to; every other role keeps the platform's routing. That
    asymmetry is the point: a tenant choosing their answer model must not, as a side
    effect, choose the model the injection classifier is judged by — see
    :func:`guardrail_reserved_roles`.
    """
    chosen = _selection.get()
    if chosen is not None and _FLEET[chosen].role is role:
        return chosen
    return _routed_default(role)


def routing_table() -> dict[str, str]:
    """Return the effective role → model map (for a dashboard / docs)."""
    return {role.value: model_for(role) for role in ModelRole}


#: The role whose model prices the *frontier baseline* the savings calc compares
#: actual spend against — i.e. "what every call would have cost at the frontier".
#: Defaults to ``GENERATION`` (the main answer-generation model); override with
#: ``GATEWAY_BASELINE_ROLE`` (e.g. ``REASONING``) to reprice savings against a
#: different tier without touching code.
_DEFAULT_BASELINE_ROLE: ModelRole = ModelRole.GENERATION


def baseline_role() -> ModelRole:
    """Return the frontier-baseline role for the savings calc (env-overridable).

    Read from ``GATEWAY_BASELINE_ROLE`` (case-insensitive role *name*, e.g.
    ``GENERATION`` / ``REASONING``); an unset or unrecognised value falls back to
    the default (``GENERATION``) rather than raising, so a typo can never break a
    live call — it just leaves the baseline at its safe default.
    """
    raw = os.environ.get("GATEWAY_BASELINE_ROLE", "").strip().upper()
    if not raw:
        return _DEFAULT_BASELINE_ROLE
    try:
        return ModelRole[raw]
    except KeyError:
        return _DEFAULT_BASELINE_ROLE


# Substrings that mark a deployment id as a small/cheap model, used by the
# measured small-model-share efficiency metric (single source of truth).
_SMALL_MODEL_MARKERS: tuple[str, ...] = ("mini", "3.5", "3-5", "llama-3.2", "phi-3.5")

# A parameter count spelled in the deployment id (``…-90B-…``, ``…-70b``) is
# AUTHORITATIVE and vetoes every generation marker above. Without this veto the
# ``llama-3.2`` marker matched ``genailab-maas-Llama-3.2-90B-Vision-Instruct`` — a
# 90-billion-parameter vision model — and counted it as a "small model", inflating
# ``small_model_share`` and the headline savings story in the favourable
# direction. The Llama 3.2 family spans 1B/3B (genuinely small) and 11B/90B
# (vision, not small), so the generation alone can never decide it.
_SMALL_MODEL_MAX_PARAM_B: float = 10.0
_PARAM_COUNT_RE = re.compile(r"(?<![a-z0-9.])(\d+(?:\.\d+)?)b(?![a-z0-9])")


def _param_count_b(model_id: str) -> float | None:
    """Return the parameter count in billions named in ``model_id``, if any."""
    match = _PARAM_COUNT_RE.search(model_id.lower())
    return float(match.group(1)) if match else None


def is_small_model(model_id: str) -> bool:
    """Return whether ``model_id`` names a small/cheap model deployment.

    A parameter count in the id wins over the generation markers: a 90B model is
    never "small", however small its base generation's other members are.
    """
    lowered = model_id.lower()
    params_b = _param_count_b(lowered)
    if params_b is not None and params_b >= _SMALL_MODEL_MAX_PARAM_B:
        return False
    return any(marker in lowered for marker in _SMALL_MODEL_MARKERS)


# The roles small-model routing actually chooses BETWEEN — i.e. the calls whose
# ``small_model_share`` is a meaningful efficiency signal. An embedding or a
# transcription has exactly one deployment in the fleet and no cheaper tier, so
# counting it would silently dilute the denominator of a routing metric with
# calls that were never routable.
_ROUTABLE_ROLES: frozenset[ModelRole] = frozenset(
    {ModelRole.CHEAP, ModelRole.GENERATION, ModelRole.REASONING, ModelRole.VISION}
)


def is_routable_role(role: ModelRole | None) -> bool:
    """Return whether ``role``'s calls count toward ``small_model_share``.

    A legacy caller that passes no role is treated as routable, preserving the
    pre-existing metric exactly for every chat call site.
    """
    return role is None or role in _ROUTABLE_ROLES


@dataclass(frozen=True, slots=True)
class RoutingRealisation:
    """Whether small-model routing is *realised* on the configured fleet, or projected.

    The savings figure is ``baseline − actual``, where the baseline prices a call's
    tokens at :func:`baseline_role`'s band and the actual prices them at the band of
    the role the router chose. That subtraction describes a **cheaper model serving
    the call** only while the roles resolve to different deployments. Point every
    routable role at one deployment — which is what a single-deployment gateway
    forces — and the two bands are two prices for the *same* model: the router's
    choice is still real and still logged, but no call was answered by anything
    cheaper, and reporting the difference as money saved would be claiming a
    mechanism that did not run.

    So the condition is measured rather than assumed, and the endpoint says which
    of the two it is. Restoring a multi-deployment fleet flips it back with no code
    change, because the answer is read from the environment on every call.
    """

    #: Routable role *name* → the deployment id it resolves to right now. Configuration:
    #: what the router *could* choose between, which is not evidence that it did.
    role_deployments: Mapping[str, str]
    #: The deployment the frontier baseline is priced from.
    baseline_deployment: str
    #: Routable deployments carrying no :data:`_FLEET` price declaration, so their
    #: ledger cost comes from the role band rather than from the model's own rate.
    undeclared_deployments: tuple[str, ...]
    #: The deployments that **actually answered** the calls being priced, from the
    #: ledger. Empty when the caller has no observations (nothing has been spent, or
    #: the caller is asking about configuration alone).
    observed_deployments: tuple[str, ...] = ()

    @property
    def distinct_deployments(self) -> tuple[str, ...]:
        """The distinct deployments the routable roles resolve to, sorted."""
        return tuple(sorted(set(self.role_deployments.values())))

    @property
    def realised(self) -> bool:
        """Whether a model other than the baseline's actually served the priced calls.

        **Measured from the ledger, not from the routing table.** A role may point at
        a deployment that is configured but unreachable — a fleet half-migrated, a
        deployment deleted upstream — and every call still falls back to the baseline
        model. Config says what *could* have happened; only the ledger says what did,
        so an observation always wins over the table when there is one.

        With no observations the answer falls back to configuration, which is the
        right default for an empty ledger: nothing has been claimed yet either way.
        """
        if self.observed_deployments:
            return any(
                deployment != self.baseline_deployment
                for deployment in self.observed_deployments
            )
        return any(
            deployment != self.baseline_deployment
            for deployment in self.role_deployments.values()
        )


def nonroutable_deployments() -> frozenset[str]:
    """Return the deployments bound to roles small-model routing never chooses between.

    Embeddings and transcription have one deployment and no cheaper tier (see
    :data:`_ROUTABLE_ROLES`), so their spend has no frontier alternative to be priced
    against. A caller pricing a baseline uses this to keep that work out of the
    comparison instead of booking a saving against a choice nobody could have made.

    Resolved through :func:`model_for`, so an env override is reflected; a deployment
    that a routable role *also* points at is excluded, because on a single-deployment
    fleet every role collapses onto one id and treating it as non-routable would
    silently empty the comparison.
    """
    routable = {model_for(role) for role in _ROUTABLE_ROLES}
    return frozenset(
        model_for(role)
        for role in ModelRole
        if role not in _ROUTABLE_ROLES and model_for(role) not in routable
    )


def routing_realisation(observed: Collection[str] = ()) -> RoutingRealisation:
    """Return whether small-model routing was realised on the calls being priced.

    Reads the live environment (``MODEL_<ROLE>`` / ``GATEWAY_BASELINE_ROLE``), so a
    caller never caches the answer across a fleet change.

    Args:
        observed: The deployment ids that actually served the calls in question,
            typically the distinct ``usage_ledger.model`` values for the rows being
            priced. Supplying them is what turns this from a statement about the
            config into a statement about the spend.
    """
    role_deployments = {
        role.name: model_for(role)
        for role in sorted(_ROUTABLE_ROLES, key=lambda r: r.name)
    }
    candidates = set(role_deployments.values()) | set(observed)
    return RoutingRealisation(
        role_deployments=role_deployments,
        baseline_deployment=model_for(baseline_role()),
        undeclared_deployments=tuple(sorted(d for d in candidates if d not in _FLEET)),
        observed_deployments=tuple(sorted(set(observed))),
    )


# Per-role pricing, ``(input_rate, output_rate)``, used ONLY as a fallback when a
# provider's cost map has no entry for a model — the case for custom/self-hosted
# deployment ids. Approximate but honest and non-zero; override any role via
# ``COST_<ROLE>_IN`` / ``COST_<ROLE>_OUT``.
#
# The OUTPUT rate is always USD per 1k completion tokens. The INPUT rate is USD
# per one *billable input unit* — see :class:`BillingUnit` / :func:`billing_unit`:
# per 1k prompt tokens for a text model, per audio-MINUTE for ``VOICE`` (Whisper
# bills per minute of audio and produces no billable output tokens, which is
# exactly what ``(0.006, 0.0)`` says), per image for an image-billed role.
_COST_PER_1K: dict[ModelRole, tuple[float, float]] = {
    ModelRole.CHEAP: (0.00015, 0.0006),
    ModelRole.REASONING: (0.0011, 0.0044),
    ModelRole.GENERATION: (0.0025, 0.01),
    ModelRole.EMBEDDING: (0.00013, 0.0),
    ModelRole.VISION: (0.0025, 0.01),
    ModelRole.VOICE: (0.006, 0.0),
}


class BillingUnit(StrEnum):
    """The unit a role's *input* rate is charged per.

    Not every model bills per token. Whisper bills per minute of audio; an
    image-generation/understanding deployment may bill per image. Carrying the
    unit explicitly is what stops a per-minute call from being ledgered as
    ``prompt_tokens=0`` → ``$0.00``, which would let a tenant with a USD cap
    transcribe without limit.
    """

    TOKENS = "tokens"  # input rate is USD per 1k prompt tokens
    AUDIO_MINUTES = "audio_minutes"  # input rate is USD per minute of audio
    IMAGES = "images"  # input rate is USD per image


#: Role → billing unit for the role's input rate. Anything absent bills per token
#: (the overwhelming default), so adding a role never silently changes pricing.
#: ``VISION`` stays on tokens because the fleet's vision deployment charges image
#: content as input *tokens*; the image COUNT is still carried end to end as a
#: measured unit, and becomes billable the moment ``COST_VISION_UNIT=images`` is
#: set. Override any role with ``COST_<ROLE>_UNIT``.
_BILLING_UNIT: dict[ModelRole, BillingUnit] = {
    ModelRole.VOICE: BillingUnit.AUDIO_MINUTES,
}


def billing_unit(role: ModelRole) -> BillingUnit:
    """Return the billing unit ``role``'s input rate is charged per.

    Read from ``COST_<ROLE>_UNIT`` (e.g. ``COST_VOICE_UNIT=audio_minutes``); an
    unset or unrecognised value falls back to the table default rather than
    raising, so a typo can never break a live call.
    """
    default = _BILLING_UNIT.get(role, BillingUnit.TOKENS)
    raw = os.environ.get(f"COST_{role.name}_UNIT", "").strip().lower()
    if not raw:
        return default
    try:
        return BillingUnit(raw)
    except ValueError:
        return default


def _rate_for(role: ModelRole, deployment: str | None = None) -> tuple[float, float]:
    """Return the (input, output) rate for a call, in the role's billing unit.

    The output rate is always $/1k completion tokens; the input rate is per one
    unit of :func:`billing_unit` for the role.

    **Cost follows the model, not the tier.** ``_COST_PER_1K`` prices a *role*, which is
    the same thing as pricing its model only while the role has one model. The moment a
    tenant may select a different deployment for a role, charging their run at the
    tier's rate is charging one model at another model's price — a ledger that is wrong
    in whichever direction the fleet happens to be priced, and a budget cap that binds
    at the wrong spend. So a deployment that is **not** the one this role routes to by
    default is priced from its own declaration in :data:`_FLEET`. When nothing has been
    selected, ``deployment`` is the routed default and this reduces exactly to the
    original per-role lookup, env overrides and all.
    """
    if deployment is not None and deployment != _routed_default(role):
        entry = _FLEET.get(deployment)
        if entry is not None:
            return entry.input_rate, entry.output_rate
    default_in, default_out = _COST_PER_1K.get(role, (0.0, 0.0))
    rate_in = float(os.environ.get(f"COST_{role.name}_IN", default_in))
    rate_out = float(os.environ.get(f"COST_{role.name}_OUT", default_out))
    return rate_in, rate_out


def billable_input_units(
    role: ModelRole,
    *,
    prompt_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
) -> float:
    """Return how many billable input units ``role`` consumed on one call.

    The count is expressed in the role's own :func:`billing_unit`: thousands of
    prompt tokens, minutes of audio, or images.
    """
    unit = billing_unit(role)
    if unit is BillingUnit.AUDIO_MINUTES:
        return max(0.0, audio_seconds) / 60.0
    if unit is BillingUnit.IMAGES:
        return float(max(0, images))
    return max(0, prompt_tokens) / 1000.0


def unit_cost(
    role: ModelRole,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
    deployment: str | None = None,
) -> float:
    """Return the fallback USD cost of one ``role`` call from its billable units.

    ``input_units × input_rate + completion_tokens/1000 × output_rate``, where the
    input unit is whatever :func:`billing_unit` says for the role. A token-only
    call reduces exactly to the original per-1k-token formula, so existing
    behaviour is unchanged.

    Args:
        role: The role that was called.
        prompt_tokens: Prompt tokens consumed.
        completion_tokens: Completion tokens produced.
        audio_seconds: Seconds of audio, for an audio-billed role.
        images: Image parts sent, for an image-billed role.
        deployment: The deployment that actually answered. Omitted means "whatever
            this role routes to", which is the only answer there was before a tenant
            could select one; naming it is what makes a selected model cost what *it*
            costs rather than what its tier costs.
    """
    rate_in, rate_out = _rate_for(role, deployment)
    units_in = billable_input_units(
        role, prompt_tokens=prompt_tokens, audio_seconds=audio_seconds, images=images
    )
    return units_in * rate_in + (max(0, completion_tokens) / 1000) * rate_out
