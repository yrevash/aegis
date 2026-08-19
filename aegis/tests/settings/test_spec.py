"""The catalogue's own contract: a control per setting, and a coherent declaration.

The bijection test is inherited from :func:`aegis.agent.harness.harness_config`'s, and it
is here for the same reason: it makes a setting **impossible to add without a UI control
appearing**. Phase 7 renders one component from :func:`setting_controls`, so a catalogue
key with no control is a value the resolver enforces and nobody can see or change.

The rest of this module drives the refusals in :meth:`SettingSpec.__post_init__`. They
are refusals at *import* time, which is the strongest place for them — a catalogue that
cannot say which direction of a ``tighten_only`` key is stricter would otherwise ship and
resolve something plausible.
"""

from __future__ import annotations

import pytest

from aegis.governance.config import RBAC_LADDER
from aegis.settings.spec import (
    SETTING_SPECS,
    MergeRule,
    SettingSpec,
    Strictness,
    UnknownSettingError,
    setting_controls,
    setting_keys,
    spec_for,
    strictest,
)

#: Every fine role a spec may name, read off the RBAC ladder rather than restated.
_FINE_ROLES = {tier.fine_role for tier in RBAC_LADDER}


def _spec(**overrides) -> SettingSpec:  # noqa: ANN003
    """Build a minimal valid spec, with fields replaced for the case under test."""
    fields = {
        "key": "test.key",
        "type_": int,
        "default": 1,
        "writable_by": frozenset({"platform_admin"}),
        "readable_by": frozenset({"platform_admin"}),
        "merge": MergeRule.OVERRIDE,
        "description": "A test setting.",
    }
    return SettingSpec(**{**fields, **overrides})


# ── the bijection: catalogue ⇄ controls ──────────────────────────────────────


def test_every_setting_has_a_control_and_every_control_a_setting():
    """The inherited bijection — a key with no control is a hidden enforcement."""
    controls = setting_controls()
    assert [control["key"] for control in controls] == list(setting_keys())
    assert {control["key"] for control in controls} == {spec.key for spec in SETTING_SPECS}
    assert len(controls) == len(SETTING_SPECS)


def test_every_control_carries_what_a_form_needs_to_render_it():
    for control in setting_controls():
        assert control["control"] in {"toggle", "number", "text", "tags", "select"}
        assert control["description"], f"{control['key']} has no help text"
        assert control["merge"] in {rule.value for rule in MergeRule}
        assert control["writable_by"], f"{control['key']} is writable by nobody"
        assert control["readable_by"], f"{control['key']} is readable by nobody"


def test_a_control_declares_its_bounds_and_choices_where_the_spec_has_them():
    by_key = {control["key"]: control for control in setting_controls()}
    assert by_key["agent.max_plan_iterations"]["minimum"] == 1
    assert by_key["agent.max_plan_iterations"]["maximum"] == 10
    assert by_key["agent.gate_min_risk"]["choices"] == ["low", "medium", "high"]
    assert by_key["agent.gate_min_risk"]["control"] == "select"
    assert by_key["guardrails.topical.block"]["control"] == "toggle"
    assert by_key["guardrails.pii.entities"]["control"] == "tags"


def test_every_role_a_spec_names_is_a_real_rbac_tier():
    """A typo in a role name would silently make a key writable by nobody."""
    for spec in SETTING_SPECS:
        assert spec.writable_by <= _FINE_ROLES, f"{spec.key}: {spec.writable_by - _FINE_ROLES}"
        assert spec.readable_by <= _FINE_ROLES, f"{spec.key}: {spec.readable_by - _FINE_ROLES}"


def test_every_tighten_only_key_in_the_catalogue_declares_its_direction():
    for spec in SETTING_SPECS:
        if spec.merge is MergeRule.TIGHTEN_ONLY:
            assert spec.stricter is not None, spec.key


def test_the_catalogue_stays_small_enough_that_every_key_is_tested():
    """A soft ceiling, deliberately: every key is a control, a permission and a test.

    Not an arbitrary number — it is the point at which "seed it with what phases 6 and 7
    actually need" would have stopped being true. Raising it is fine; doing so without
    noticing is not.

    Raised from 15 to 18 by §7.6, which added the three per-tenant rail controls the
    task's closed template list names: ``guardrails.denylist.patterns``
    (``matches_pattern``, over the vetted library), ``guardrails.input.max_chars``
    (``max_length``) and ``guardrails.pii.block``. Each one is bound in
    :data:`aegis.settings.guardrails.GUARDRAIL_SETTING_BINDINGS`, read by a rail, and
    covered by the tighten-only sweep in ``test_forbidden_controls`` — which is the
    price this ceiling exists to make somebody pay before adding a key.

    Raised from 18 to 24 by §7.8, which cut tenant sub-roles and spent the difference
    on six ``seat.*`` keys — one label and five revoke-only capability toggles — rather
    than on a ``tenant_roles`` table, a permission catalogue, a ``require_permission``
    dependency and Alembic. The price was paid in the same change: each toggle is named
    in :data:`aegis.settings.seats.SEAT_CAPABILITIES` beside the guard that reads it,
    each is driven by a request with the seat revoked in
    ``backend/tests/api/test_seats.py``, and all five are in ``_STRICTER_END``. Six keys
    is the *whole* mechanism, which is the argument for the ceiling being a soft one:
    the alternative was six days of new schema, not six fewer keys.
    """
    assert len(SETTING_SPECS) <= 24


# ── the refusals, driven ──────────────────────────────────────────────────────


def test_a_setting_whose_type_no_control_renders_is_refused_at_import():
    with pytest.raises(ValueError, match="no UI control renders"):
        _spec(type_=dict, default={})


def test_a_setting_with_no_help_text_is_refused():
    with pytest.raises(ValueError, match="no description"):
        _spec(description="")


def test_a_tighten_only_setting_with_no_direction_is_refused():
    """Without a direction the resolver could not tell tightening from weakening."""
    with pytest.raises(ValueError, match="which direction is stricter"):
        _spec(merge=MergeRule.TIGHTEN_ONLY, bounds=(1, 10))


def test_a_tighten_only_setting_with_no_domain_to_rank_over_is_refused():
    """Phase 7 §7.16 rows 1 and 3: the fold has to be able to compare two values.

    A ``tighten_only`` key with neither choices nor bounds is not a rule the resolver
    can apply — :meth:`SettingSpec.rank` has nothing to turn the value into, so the fold
    would raise on the first tenant write, and :func:`strictest_legal` would have no
    value to fail closed to during the outage in which it is the only thing standing
    between a tenant and a control nobody can read.
    """
    with pytest.raises(ValueError, match="neither choices nor bounds"):
        _spec(
            merge=MergeRule.TIGHTEN_ONLY,
            stricter=Strictness.LOWER,
            type_=str,
            default="anything",
        )


def test_a_union_setting_that_is_not_a_list_is_refused():
    with pytest.raises(ValueError, match="union"):
        _spec(merge=MergeRule.UNION, type_=int, default=1)


def test_a_default_outside_its_own_bounds_is_refused():
    with pytest.raises(ValueError, match="outside"):
        _spec(default=99, bounds=(1, 10))


# ── validation of a candidate value ───────────────────────────────────────────


def test_a_boolean_is_not_accepted_for_an_integer_key():
    """``isinstance(True, int)`` is True, which is how a toggle ends up in a number."""
    with pytest.raises(ValueError, match="expected an integer"):
        _spec().validate(True)


def test_a_value_outside_the_bounds_is_refused_not_clamped():
    """Clamping silently is the same defect class as ignoring a write."""
    spec = _spec(bounds=(1, 10))
    with pytest.raises(ValueError, match="outside"):
        spec.validate(11)


def test_a_value_outside_the_choices_is_refused():
    spec = spec_for("agent.mode")
    with pytest.raises(ValueError, match="not one of"):
        spec.validate("turbo")


def test_a_union_member_outside_the_allowed_set_is_refused():
    spec = _spec(merge=MergeRule.UNION, type_=list, default=[], choices=("a", "b"))
    spec.validate(["a"])
    with pytest.raises(ValueError, match="not among the allowed members"):
        spec.validate(["a", "z"])


def test_an_unknown_key_names_the_catalogue_rather_than_just_raising_keyerror():
    with pytest.raises(UnknownSettingError, match="settings catalogue"):
        spec_for("agent.mode ")


# ── the strictness arithmetic itself ──────────────────────────────────────────


def test_lower_is_stricter_picks_the_lower_value():
    spec = spec_for("agent.max_plan_iterations")
    assert spec.stricter is Strictness.LOWER
    assert strictest(spec, 1, 5) == 1
    assert strictest(spec, 5, 1) == 1


def test_higher_is_stricter_picks_the_engaged_toggle():
    spec = spec_for("guardrails.topical.block")
    assert spec.stricter is Strictness.HIGHER
    assert strictest(spec, False, True) is True
    assert strictest(spec, True, False) is True


def test_an_enumerated_key_is_compared_by_its_declared_order():
    """Choices are declared strictest first, so the comparison is the index."""
    spec = spec_for("agent.gate_min_risk")
    assert strictest(spec, "high", "low") == "low"
    assert strictest(spec, "medium", "high") == "medium"


def test_strictness_is_undefined_for_an_override_key_and_says_so():
    """Answering anyway would let a caller believe a key was protected when it is not."""
    with pytest.raises(ValueError, match="only defined for"):
        strictest(spec_for("agent.model"), "a", "b")


# ── the honesty flag: a control may not be badged live unless something reads it ──


def test_a_key_is_declared_inert_exactly_when_nothing_binds_it():
    """The catalogue is where "does this control do anything?" is answered, once.

    Six keys once saved, wrote an audit row and badged themselves "Your setting" while
    reaching nothing. Five of them now bind — four through
    :data:`aegis.settings.agent.AGENT_SETTING_BINDINGS` and
    :data:`aegis.settings.guardrails.GUARDRAIL_SETTING_BINDINGS`, and ``agent.model``
    through the platform's allowed-deployment set (§7.16 row 6), which is why it is
    absent from ``bound`` below and still not inert: an ``OVERRIDE`` request-level
    preference is resolved and applied per run at the host's gateway seam, not folded
    onto the process-wide ``AgentConfig``. The one that remains (``agent.mode``, whose
    vocabulary does not line up with ``DepthMode``'s) says so in the one place every
    layer reads.

    Drop the ``inert_reason`` from it without giving it a consumer and the control goes
    back to claiming it works; add a binding for it without clearing the reason and the
    import-time check in the binding module fails first. The converse — a key that is
    live but read by nothing — is caught in ``test_forbidden_controls.py``, which scans
    the source trees for a reader rather than trusting a list here.
    """
    from aegis.settings.agent import AGENT_SETTING_BINDINGS
    from aegis.settings.guardrails import GUARDRAIL_SETTING_BINDINGS

    bound = {b.key for b in AGENT_SETTING_BINDINGS} | {
        b.key for b in GUARDRAIL_SETTING_BINDINGS
    }
    inert = {spec.key for spec in SETTING_SPECS if not spec.effective}

    assert bound & inert == set(), f"bound and declared inert: {sorted(bound & inert)}"
    assert inert == {"agent.mode"}, (
        "the set of controls that change nothing moved; every member needs an "
        f"inert_reason naming what would make it live: {sorted(inert)}"
    )
    for control in setting_controls():
        if not control["effective"]:
            assert control["inert_reason"], control["key"]
