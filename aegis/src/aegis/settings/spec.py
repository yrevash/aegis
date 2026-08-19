"""The settings catalogue — every per-tenant control declared once, as data.

**This is the mechanism behind "0 code change from the dashboard".** A per-tenant
control is an entry in :data:`SETTING_SPECS`, not a screen: the same catalogue decides
what the resolver computes, who may write it, and what the UI renders. Adding a control
is adding a row here.

It is a **generalisation of** :data:`aegis.agent.harness._KNOB_SPECS`, not a second
mechanism. That catalogue proved the shape — a frozen descriptor per knob, a UI
projection derived from it, and a bijection test that fails the suite if a knob exists
with no control — and this one adds the three things a *tenant-scoped* control needs
that a process-wide one does not: who may write it, who may read it, and **how a
tenant's value combines with the platform's**.

That last one is the load-bearing part:

:attr:`MergeRule.TIGHTEN_ONLY` makes the tenant-safety rules *executable configuration*
rather than prose, because the resolver **structurally cannot compute a value weaker
than the platform default** — it takes the strictest value in the chain, so a weaker one
loses by arithmetic rather than by a check somebody remembered to write. Phase 7's
fifteen forbidden controls are enforced by that property. And because "stricter" is not
a fact about a Python type, every ``TIGHTEN_ONLY`` spec must declare which direction it
runs in (:class:`Strictness`) — a rule that cannot say which way is tighter is not a
rule, so the catalogue refuses to be constructed without it.

Requires the ``aegis[governance]`` extra: the role names a spec is written against are
the fine RBAC tiers, and they are imported rather than restated. The one key whose legal
values the *platform* decides rather than this file — ``agent.model`` — reads them from
:func:`aegis.gateway.routing.tenant_model_choices` for the same reason: a copy here
would be a second statement of the allowed-deployment set, and the whole point of that
set is that there is one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from aegis.core.types import RiskLevel
from aegis.gateway.routing import PLATFORM_DEFAULT, tenant_model_choices
from aegis.governance.security import PLATFORM_ADMIN, TENANT_ADMIN
from aegis.governance.types import Role

__all__ = [
    "SETTING_SPECS",
    "MergeRule",
    "SettingSpec",
    "Strictness",
    "UnknownSettingError",
    "setting_controls",
    "setting_keys",
    "spec_for",
    "strictest",
    "strictest_legal",
]


class MergeRule(StrEnum):
    """How a tenant or user value combines with the platform default.

    ``TIGHTEN_ONLY`` is the load-bearing one: it makes the tenant-safety rules
    *executable configuration* rather than prose, because the resolver structurally
    cannot compute a value weaker than the platform default. That is what turns "a
    tenant may add a guardrail but never weaken one" from a policy somebody has to
    remember into arithmetic.
    """

    OVERRIDE = "override"  # last scope wins  (e.g. preferred model)
    TIGHTEN_ONLY = "tighten_only"  # may only become stricter (e.g. gate_min_risk)
    UNION = "union"  # sets accumulate  (e.g. extra guardrails)


class Strictness(StrEnum):
    """Which direction of a ``TIGHTEN_ONLY`` key's value range is the stricter one.

    Necessary because "stricter" is a property of what a setting *means*, not of its
    type. A lower ``agent.gate_min_risk`` gates **more** actions, so lower is stricter;
    a higher ``guardrails.grounding.min_score`` demands **more** evidence, so higher is.
    Both are integers. Leaving the resolver to guess would make the guarantee depend on
    a coincidence of sign, so the catalogue makes it a declaration and refuses a
    ``TIGHTEN_ONLY`` spec without one.

    For a spec with ordered :attr:`SettingSpec.choices`, the compared quantity is the
    index into that tuple — so choices are declared **strictest first** and the spec
    declares :attr:`LOWER`.
    """

    LOWER = "lower_is_stricter"
    HIGHER = "higher_is_stricter"


#: The control a UI renders for each declared Python type. A spec whose ``type_`` is not
#: a key here cannot be constructed — which is how "a setting cannot be added without a
#: UI control appearing" is enforced at import time rather than only in a test.
_CONTROL_BY_TYPE: dict[type, str] = {
    bool: "toggle",
    int: "number",
    float: "number",
    str: "text",
    list: "tags",
}


class UnknownSettingError(KeyError):
    """A key that is not in the catalogue was asked for.

    A ``KeyError`` subclass so ``except KeyError`` still works, but with a message that
    names the catalogue — an unknown key is almost always a typo in a route or a stale
    UI, and "KeyError: 'agent.mode '" on its own has cost people an afternoon.
    """

    def __init__(self, key: str) -> None:
        """Build the error naming the missing key."""
        super().__init__(
            f"{key!r} is not in the settings catalogue (aegis.settings.spec."
            "SETTING_SPECS). Every readable or writable setting must be declared there."
        )
        self.key = key


@dataclass(frozen=True, slots=True)
class SettingSpec:
    """One per-tenant control, declared once and used by every layer.

    Attributes:
        key: The dotted catalogue key, e.g. ``agent.gate_min_risk``. Also the UI's
            identity for the control and the audit record's subject.
        type_: The Python type of the value. Must be one this module can render a
            control for; see :data:`_CONTROL_BY_TYPE`.
        default: The **platform default** — the value in force when nobody has written
            anything, and the floor a ``TIGHTEN_ONLY`` key can never resolve below.
        writable_by: Fine RBAC roles allowed to write it, at any scope they are
            otherwise entitled to. A role absent from this set is refused by the
            resolver, not merely by a disabled control in a form.
        readable_by: Fine RBAC roles allowed to read it.
        merge: How the scopes combine — see :class:`MergeRule`.
        bounds: Inclusive ``(minimum, maximum)`` for a numeric key, or ``None``.
        choices: The legal values, in order, for an enumerated key. For a
            ``TIGHTEN_ONLY`` key they are ordered **strictest first**, because the
            comparison is the index.
        choices_source: A callable returning the legal values, for a key whose domain
            the *platform* decides rather than this file — ``agent.model``'s is the set
            of deployments the fleet declares tenant-selectable. Declared as a function
            so the catalogue, the validation and the rendered control read the one set
            at the one moment, instead of a copy frozen here going stale against the
            fleet. Mutually exclusive with ``choices``, and refused on a
            ``TIGHTEN_ONLY`` key: ranking is *by index into the domain*, so a domain
            that can change under the resolver would silently move what "stricter"
            means.
        stricter: Which direction is stricter. Required for — and only meaningful
            for — ``TIGHTEN_ONLY``.
        description: Rendered as the control's help text. Required: an unexplained
            control in a tenant-facing form is one nobody dares touch.
        inert_reason: ``None`` for a key some consumer actually reads — the normal case,
            and the only one a control may be rendered as live. A **string** declares
            that nothing reads this key yet and says exactly what would make it live.
            It exists because the alternative was found in the wild: six catalogue keys
            that saved, wrote an audit row, badged themselves "Your setting" and changed
            nothing whatsoever. A control that lies about being in force is worse than an
            absent one, so the catalogue — the single declaration every layer reads —
            is where the admission belongs, and :func:`setting_controls` forwards it to
            the screen. Four of those six now bind; the two that do not say so here.

    Raises:
        ValueError: If the declaration is incoherent — an unrenderable type, a
            ``TIGHTEN_ONLY`` key with no strictness direction or no domain to rank its
            values over, a default outside its own bounds or choices, a missing
            description, or an ``inert_reason`` that does not name what would make the
            key live. Every one of these is a programming error in the catalogue itself,
            so it fails at import.
    """

    key: str
    type_: type
    default: Any
    writable_by: frozenset[str]
    readable_by: frozenset[str]
    merge: MergeRule
    bounds: tuple[Any, Any] | None = None
    choices: tuple[Any, ...] | None = None
    choices_source: Callable[[], tuple[Any, ...]] | None = None
    stricter: Strictness | None = None
    description: str = ""
    inert_reason: str | None = None

    def __post_init__(self) -> None:
        """Refuse a declaration the rest of this package could not honour."""
        if self.type_ not in _CONTROL_BY_TYPE:
            raise ValueError(
                f"setting {self.key!r} declares type {self.type_!r}, which no UI control "
                f"renders; extend aegis.settings.spec._CONTROL_BY_TYPE or use one of "
                f"{sorted(t.__name__ for t in _CONTROL_BY_TYPE)}"
            )
        if not self.description:
            raise ValueError(f"setting {self.key!r} has no description to render as help text")
        if self.inert_reason is not None and not self.inert_reason.strip():
            raise ValueError(
                f"setting {self.key!r} declares itself inert but does not say why or "
                "what would make it live; an unexplained dead control is the defect the "
                "field exists to end, not a lighter version of it"
            )
        if self.choices is not None and self.choices_source is not None:
            raise ValueError(
                f"setting {self.key!r} declares both choices and choices_source; the "
                "legal values have to come from exactly one place or the two will "
                "disagree about what is legal"
            )
        if self.choices_source is not None and self.merge is MergeRule.TIGHTEN_ONLY:
            raise ValueError(
                f"setting {self.key!r} is tighten_only with a runtime choices_source; "
                "a tighten_only key ranks values by their index into the domain, so a "
                "domain that can change under the resolver would move what 'stricter' "
                "means without anything appearing to change"
            )
        if self.merge is MergeRule.TIGHTEN_ONLY and self.stricter is None:
            raise ValueError(
                f"setting {self.key!r} is tighten_only but does not say which direction "
                "is stricter, so the resolver could not tell a tightening from a "
                "weakening; declare stricter=Strictness.LOWER or .HIGHER"
            )
        if (
            self.merge is MergeRule.TIGHTEN_ONLY
            and self.choices is None
            and self.bounds is None
            and self.type_ is not bool
        ):
            raise ValueError(
                f"setting {self.key!r} is tighten_only but declares neither choices nor "
                "bounds, so there is no domain to rank its values over: the fold would "
                "raise on the first tenant write and there is no strictest value to fail "
                "closed to. Give it bounds or choices."
            )
        if self.merge is MergeRule.UNION and self.type_ is not list:
            raise ValueError(
                f"setting {self.key!r} merges by union but is typed {self.type_!r}; "
                "a union key holds a list of members"
            )
        self.validate(self.default)

    @property
    def effective(self) -> bool:
        """Whether anything in the system actually reads this key today."""
        return self.inert_reason is None

    @property
    def legal_choices(self) -> tuple[Any, ...] | None:
        """The values this key accepts right now, or ``None`` when it is not enumerated.

        The single answer every layer asks for: the validation, the rendered control and
        the tests all read this, so a key whose domain is the platform's to decide has
        exactly one domain rather than one per reader.
        """
        if self.choices_source is not None:
            return tuple(self.choices_source())
        return self.choices

    @property
    def control(self) -> str:
        """The kind of control a UI renders for this setting."""
        return "select" if self.legal_choices else _CONTROL_BY_TYPE[self.type_]

    def validate(self, value: Any) -> None:  # noqa: ANN401 - any setting value
        """Check a candidate value against this spec's type, bounds and choices.

        Args:
            value: The value to check.

        Raises:
            ValueError: If the value is of the wrong type, outside the bounds, or not
                one of the declared choices. Raised — never coerced: silently clamping a
                tenant's write to the nearest legal value is the same defect class as
                silently ignoring it.
        """
        choices = self.legal_choices
        if self.type_ is list:
            if not isinstance(value, list | tuple):
                raise ValueError(f"{self.key}: expected a list of members, got {value!r}")
            if choices is not None:
                unknown = [item for item in value if item not in choices]
                if unknown:
                    raise ValueError(
                        f"{self.key}: {unknown!r} not among the allowed members "
                        f"{list(choices)!r}"
                    )
            return
        # bool before int: ``isinstance(True, int)`` is True, so an int key would
        # otherwise accept a boolean and store ``true`` where a number belongs.
        if self.type_ is bool:
            if not isinstance(value, bool):
                raise ValueError(f"{self.key}: expected a boolean, got {value!r}")
        elif self.type_ is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{self.key}: expected an integer, got {value!r}")
        elif self.type_ is float:
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{self.key}: expected a number, got {value!r}")
        elif not isinstance(value, self.type_):
            raise ValueError(f"{self.key}: expected {self.type_.__name__}, got {value!r}")
        if choices is not None and value not in choices:
            raise ValueError(
                f"{self.key}: {value!r} is not one of {list(choices)!r}"
            )
        if self.bounds is not None:
            low, high = self.bounds
            if value < low or value > high:
                raise ValueError(f"{self.key}: {value!r} is outside {self.bounds!r}")

    def rank(self, value: Any) -> float:  # noqa: ANN401 - any setting value
        """Return the comparable strictness coordinate of ``value``.

        The one place a value becomes a number, so that :func:`strictest` is a two-line
        comparison rather than a type switch repeated per merge rule.

        Args:
            value: A legal value for this spec.

        Returns:
            The index into :attr:`choices` for an enumerated key, ``1``/``0`` for a
            boolean (a toggle that is *on* is the guardrail engaged), otherwise the
            number itself.

        Raises:
            ValueError: If the value is not legal for this spec.
        """
        self.validate(value)
        if self.choices is not None:
            return float(self.choices.index(value))
        if self.type_ is bool:
            return 1.0 if value else 0.0
        return float(value)


def strictest_legal(spec: SettingSpec) -> Any:  # noqa: ANN401 - any setting value
    """Return the strictest value ``spec`` permits at all — the **fail-closed** value.

    What a binding clamps to when a tenant is known but their settings cannot be read.
    In that state we do not know what they asked for, and the platform default is by
    construction the *loosest* value they could have chosen — so the honest answer is
    the strictest value they *could* have written, not the default.

    Args:
        spec: A ``TIGHTEN_ONLY`` spec.

    Returns:
        The end of the spec's declared domain that its :class:`Strictness` calls
        stricter: the first (or last) of :attr:`SettingSpec.choices`, the low (or high)
        end of :attr:`SettingSpec.bounds`, or — for a boolean toggle, whose domain is
        not written down because it does not have to be — ``True`` when higher is
        stricter and ``False`` when lower is.

    Raises:
        ValueError: If the spec is not ``TIGHTEN_ONLY`` (there is no "strictest" for an
            override or a union), or declares no domain to clamp into, so the question
            has no answer. The second of those is defence in depth:
            :meth:`SettingSpec.__post_init__` already refuses to construct a
            ``TIGHTEN_ONLY`` spec with no rankable domain, so a catalogue entry cannot
            reach here without one. Callers are expected to raise this at **import**,
            not discover it during the outage in which the fail-closed path is the only
            thing standing between a tenant and a control nobody can read.
    """
    if spec.merge is not MergeRule.TIGHTEN_ONLY:
        raise ValueError(
            f"{spec.key} merges by {spec.merge.value}; the strictest legal value is "
            f"only defined for {MergeRule.TIGHTEN_ONLY.value}"
        )
    lower_is_stricter = spec.stricter is Strictness.LOWER
    if spec.choices is not None:
        return spec.choices[0] if lower_is_stricter else spec.choices[-1]
    if spec.bounds is not None:
        return spec.bounds[0] if lower_is_stricter else spec.bounds[1]
    if spec.type_ is bool:
        return not lower_is_stricter
    raise ValueError(
        f"{spec.key} declares neither choices nor bounds, so there is no strictest "
        "value to fail closed to; give it bounds or drop it from the bindings"
    )


def strictest(spec: SettingSpec, left: Any, right: Any) -> Any:  # noqa: ANN401 - any value
    """Return whichever of two values is the stricter under ``spec``.

    **This function is the guarantee.** The resolver folds it over the scope chain for
    every ``TIGHTEN_ONLY`` key, so the result is a maximum (or minimum) of a set that
    always contains the platform value — which is why no combination of tenant and user
    writes can produce something weaker than the platform default. It is not a check
    that can be forgotten at a call site; it is the only expression the resolver has.

    Args:
        spec: The setting being resolved. Must be ``TIGHTEN_ONLY``.
        left: One candidate value.
        right: The other.

    Returns:
        The stricter of the two, ``left`` on a tie.

    Raises:
        ValueError: If ``spec`` does not merge by tightening — there is no meaningful
            "stricter" for an override or a union, and answering anyway would let a
            caller believe a key was protected when it was not.
    """
    if spec.merge is not MergeRule.TIGHTEN_ONLY:
        raise ValueError(
            f"{spec.key} merges by {spec.merge.value}; 'stricter' is only defined for "
            f"{MergeRule.TIGHTEN_ONLY.value}"
        )
    if spec.stricter is Strictness.LOWER:
        return left if spec.rank(left) <= spec.rank(right) else right
    return left if spec.rank(left) >= spec.rank(right) else right


# ─────────────────────────────────────────────────────────────────────────────
# The catalogue
# ─────────────────────────────────────────────────────────────────────────────

#: Every fine role, for the keys a tenant may read but only the platform may write.
_EVERY_ROLE = frozenset(
    {PLATFORM_ADMIN, TENANT_ADMIN, Role.AI_TEAM.value, Role.DEVOPS.value, Role.CLIENT.value}
)

#: The operator tiers: both admins plus the two peer operational roles.
_OPERATORS = frozenset({PLATFORM_ADMIN, TENANT_ADMIN, Role.AI_TEAM.value, Role.DEVOPS.value})

#: Who may write a tenant-level guardrail or agent control. Deliberately not ``client``:
#: a business user tunes their own preferences, not their tenant's safety posture.
_TENANT_CONTROLS = frozenset({PLATFORM_ADMIN, TENANT_ADMIN, Role.AI_TEAM.value})

#: Risk tiers ordered **strictest first**: a lower gate threshold pauses more actions.
#: Read from :class:`aegis.core.types.RiskLevel` rather than restated, so a tier added
#: to the agent's contract cannot go missing from the control that gates on it. The
#: declaration order of that enum *is* low → high, and the assertion below is what stops
#: that from being an assumption: if it is ever reordered, this catalogue's notion of
#: "stricter" would silently invert.
_RISK_TIERS_STRICTEST_FIRST: tuple[str, ...] = tuple(tier.value for tier in RiskLevel)
if _RISK_TIERS_STRICTEST_FIRST != ("low", "medium", "high"):
    raise ValueError(
        "aegis.core.types.RiskLevel is no longer declared low → high, so "
        f"agent.gate_min_risk's tightening direction is wrong: {_RISK_TIERS_STRICTEST_FIRST}"
    )


#: **The catalogue.** Seeded with the keys phases 6 and 7 actually need and no more:
#: every entry is a UI control, a permission decision and a test, so an aspirational key
#: costs exactly as much as a used one and proves nothing.
#:
#: What is deliberately *not* here: ``agent.tools``. Phase 5 resolves a tool allowlist as
#: ``platform ∩ persona ∩ tenant ∩ user`` through one ``is_allowed`` function, and an
#: intersection is not one of the three merge rules. Adding a fourth rule to model it
#: here would create the second mechanism this catalogue exists to prevent.
SETTING_SPECS: tuple[SettingSpec, ...] = (
    SettingSpec(
        key="agent.gate_min_risk",
        type_=str,
        default="high",
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.TIGHTEN_ONLY,
        choices=_RISK_TIERS_STRICTEST_FIRST,
        stricter=Strictness.LOWER,
        description=(
            "Minimum tool-risk tier that forces the human approval gate. It is the ONLY "
            "gating signal, so a tenant may lower it (gating more) and never raise it."
        ),
    ),
    SettingSpec(
        key="agent.max_plan_iterations",
        type_=int,
        default=2,
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.TIGHTEN_ONLY,
        bounds=(1, 10),
        stricter=Strictness.LOWER,
        description=(
            "Hard cap on planning rounds per run. Fewer rounds is stricter: it bounds "
            "spend and guarantees termination."
        ),
    ),
    SettingSpec(
        key="agent.agentic_retrieval_max_rounds",
        type_=int,
        default=2,
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.TIGHTEN_ONLY,
        bounds=(1, 10),
        stricter=Strictness.LOWER,
        description=(
            "Maximum rounds the agentic-retrieval loop may take before finalising. "
            "Fewer rounds is stricter."
        ),
    ),
    SettingSpec(
        key="agent.team.max_parallel",
        type_=int,
        default=4,
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.TIGHTEN_ONLY,
        bounds=(1, 16),
        stricter=Strictness.LOWER,
        description=(
            "How many sub-agents one turn may fan out across. TIGHTEN_ONLY because the "
            "cap protects two things a tenant does not own — the shared worker pool and "
            "the platform's exposure to that tenant's spend — so a tenant may ask for "
            "fewer agents and never more. Fewer is the safer failure. It is the CEILING "
            "an explicit user width is narrowed to (reported as decided_by="
            "'platform_cap'), never a second reason to shrink a width the user chose."
        ),
    ),
    SettingSpec(
        key="agent.model",
        type_=str,
        default=PLATFORM_DEFAULT,
        writable_by=_EVERY_ROLE,
        readable_by=_EVERY_ROLE,
        # OVERRIDE, and deliberately not TIGHTEN_ONLY: a model choice has no rankable
        # order — Llama-3.3-70B is not "stricter" than DeepSeek-V3, it is a different
        # model — so there is no direction for the fold to take, no strictest value to
        # fail closed to, and ``__post_init__`` refuses such a spec outright. What keeps
        # "last scope wins" safe here is that every value it can win with is already on
        # the platform's own list: the domain is the constraint, not the merge rule.
        merge=MergeRule.OVERRIDE,
        choices_source=tenant_model_choices,
        description=(
            "Preferred model deployment for a run, from the deployments the platform "
            "offers tenants. A preference, not a permission: the server validates the "
            "choice against that set on write and again at the point of use, and the "
            "ledger prices the deployment that actually answered."
        ),
    ),
    SettingSpec(
        key="agent.mode",
        type_=str,
        default="standard",
        writable_by=_EVERY_ROLE,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.OVERRIDE,
        choices=("fast", "standard", "team"),
        description=(
            "Answering depth for a run — fast single pass, standard, or the multi-agent "
            "team. Orthogonal to the model, and charged differently."
        ),
        inert_reason=(
            "Nothing reads this yet. The run's width comes from QueryRequest.depth_mode "
            "-> aegis.agent.router.DepthMode, whose values are auto|single|team, and "
            "this key's are fast|standard|team. The two vocabularies do not line up: "
            "'standard' has no DepthMode, and the composer's own mapping sends both "
            "Fast and Deep to 'single' because Deep tunes depth per lane, not width. "
            "Making it live means one vocabulary, not a mapping invented here — either "
            "re-declare these choices as DepthMode's, or give the router a notion of "
            "'standard'. Guessing a mapping would silently give a tenant who asked for "
            "'standard' a width nobody chose, which is worse than the control being off."
        ),
    ),
    SettingSpec(
        key="guardrails.topical.block",
        type_=bool,
        default=False,
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.TIGHTEN_ONLY,
        stricter=Strictness.HIGHER,
        description=(
            "Hard-block off-topic requests instead of flagging them. A tenant may turn "
            "this on; once the platform has, no tenant can turn it off."
        ),
    ),
    SettingSpec(
        key="guardrails.grounding.block",
        type_=bool,
        default=False,
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.TIGHTEN_ONLY,
        stricter=Strictness.HIGHER,
        description=(
            "Hard-block an answer the grounding rail cannot support from the retrieved "
            "sources, instead of flagging it."
        ),
    ),
    SettingSpec(
        key="guardrails.denylist.terms",
        type_=list,
        default=[],
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.UNION,
        description=(
            "Extra terms this tenant's rails deny. Additive by construction: a tenant's "
            "terms are added to the platform's and the platform's cannot be removed."
        ),
    ),
    SettingSpec(
        key="guardrails.pii.entities",
        type_=list,
        default=["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD"],
        writable_by=_TENANT_CONTROLS,
        readable_by=_EVERY_ROLE,
        merge=MergeRule.UNION,
        description=(
            "PII entity kinds the redaction rail screens for. A tenant may add kinds; "
            "the platform's set is a floor, not a starting point."
        ),
    ),
    SettingSpec(
        key="jobs.max_inflight.ingest",
        type_=int,
        default=4,
        writable_by=frozenset({PLATFORM_ADMIN}),
        readable_by=_OPERATORS,
        merge=MergeRule.TIGHTEN_ONLY,
        bounds=(1, 64),
        stricter=Strictness.LOWER,
        description=(
            "How many ingestion jobs one tenant may have in flight. Admission returns a "
            "visible 429 past it rather than queueing silently."
        ),
    ),
    SettingSpec(
        key="jobs.estimated_cost_usd.ingest_per_mb",
        type_=float,
        default=0.5,
        writable_by=frozenset({PLATFORM_ADMIN}),
        readable_by=_OPERATORS,
        merge=MergeRule.TIGHTEN_ONLY,
        bounds=(0.0, 1000.0),
        stricter=Strictness.HIGHER,
        description=(
            "USD per megabyte used to pre-authorise an ingestion job against the "
            "tenant's remaining budget. An estimate, never a charge: the ledger still "
            "records what the run actually cost. A HIGHER figure is the stricter one "
            "because over-estimating refuses a job that might not fit, while "
            "under-estimating admits one that cannot finish."
        ),
    ),
)

_BY_KEY: dict[str, SettingSpec] = {spec.key: spec for spec in SETTING_SPECS}
if len(_BY_KEY) != len(SETTING_SPECS):
    raise ValueError("the settings catalogue declares a key twice")


def spec_for(key: str) -> SettingSpec:
    """Return the catalogue entry for ``key``.

    Args:
        key: The dotted setting key.

    Returns:
        Its :class:`SettingSpec`.

    Raises:
        UnknownSettingError: If the key is not in the catalogue. Never a default — a
            missing key means a route or a UI is out of step with the catalogue, and
            answering with something plausible would hide that.
    """
    try:
        return _BY_KEY[key]
    except KeyError:
        raise UnknownSettingError(key) from None


def setting_keys() -> tuple[str, ...]:
    """Return every catalogue key, in declaration order."""
    return tuple(spec.key for spec in SETTING_SPECS)


def setting_controls(
    specs: Sequence[SettingSpec] | None = None,
) -> list[dict[str, Any]]:
    """Return the catalogue as the control descriptors a settings UI renders.

    The generalisation of :func:`aegis.agent.harness.harness_config`: pure metadata over
    the same declarations the resolver reads, so the form and the enforcement cannot
    drift. Phase 7 renders **one** component from this list — the first bespoke settings
    form is the moment the catalogue starts sprawling into seventeen screens.

    Args:
        specs: The specs to project; defaults to the whole catalogue.

    Returns:
        One descriptor per spec, carrying ``key``, ``type``, ``control``, ``default``,
        ``merge``, ``writable_by``/``readable_by`` (sorted, so the output is stable),
        the ``description`` to render as help text, and ``effective`` — plus ``bounds``,
        ``choices``, ``stricter`` and ``inert_reason`` where they apply.

        ``effective`` is the one a screen must not ignore: ``False`` means nothing reads
        the key yet, ``inert_reason`` says what would change that, and rendering such a
        control as though a write to it took effect is the defect the field was added to
        stop.
    """
    controls: list[dict[str, Any]] = []
    for spec in specs if specs is not None else SETTING_SPECS:
        control: dict[str, Any] = {
            "key": spec.key,
            "type": spec.type_.__name__,
            "control": spec.control,
            "default": spec.default,
            "merge": spec.merge.value,
            "writable_by": sorted(spec.writable_by),
            "readable_by": sorted(spec.readable_by),
            "description": spec.description,
            "effective": spec.effective,
        }
        if spec.inert_reason is not None:
            control["inert_reason"] = spec.inert_reason
        if spec.bounds is not None:
            control["minimum"], control["maximum"] = spec.bounds
        if spec.legal_choices is not None:
            control["choices"] = list(spec.legal_choices)
        if spec.stricter is not None:
            control["stricter"] = spec.stricter.value
        controls.append(control)
    return controls
