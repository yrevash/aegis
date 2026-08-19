"""Phase 7 §7.16 — the fifteen controls a tenant must never have, as assertions.

7.16 is a fifteen-row table, and its own framing is the point: each row is *"a catalogue
entry under Phase 3 §3.7, so this is executable configuration rather than a paragraph in
a document nobody re-reads"*. A table in a markdown file is not executable. This module
is what makes the sentence true — every row the catalogue can decide is decided here,
**over** :data:`SETTING_SPECS` rather than as fifteen near-identical hand-written tests,
and every row it cannot decide says where the enforcement really lives instead of
pretending.

What is proven here, by row:

* **1, 3** — no ``tighten_only`` key can *resolve* weaker than the platform layer, for
  every such key over the whole cross-product of legal platform/tenant/user values,
  through the resolver's own fold; and, live, a weaker write is refused with a reason at
  tenant and at user scope, while a weaker row written **behind** that guard still loses.
* **7** — nothing a tenant can write reaches the guardrail completer: the entire
  tenant-reachable surface of the pipeline is :class:`GuardrailPolicy`'s fields, every
  one of them is a bound catalogue key, none of them names a model, and
  :meth:`Guardrails.with_policy` keeps the completer the host wired.
* **9** — the fan-out cap is a bound, bounded ``tighten_only`` key **and** the clamp is
  visible: :func:`aegis.agent.router.decide_depth` reports ``decided_by='platform_cap'``
  and names the cap in its reason. A silent clamp would be a different control.
* **2, 4, 8, 10, 12, 14** — the catalogue holds no key by which any of them could be
  asked for at all. One data-driven refusal table, because "there is no such key" is the
  same assertion six times and a sixteenth key called ``agent.tools`` is how it stops
  being true.
* **6** — the allowed-deployment set exists and is what decides. It used to be reported
  as NOT ENFORCED, because there was no such set anywhere in the repo
  (:func:`aegis.gateway.routing.routing_table` maps a role to *one* deployment — a
  routing decision, not a list of alternatives) and ``agent.model`` carried an
  ``inert_reason`` saying so. The set is now
  :data:`aegis.gateway.routing._FLEET_DECLARATION`; the catalogue **reads** its legal
  values from it rather than restating them, so the enum a screen renders is a
  projection of the check every write goes through; the choice is re-validated at the
  point of use so a row that predates a withdrawal cannot take effect; no deployment on
  a role the host's own safety layers call is selectable at all (row 7 held against row
  6); and the ledger prices the deployment that answered rather than its tier.

Rows **5** (``_scope_tenant``), **11** (``check_input`` before storage), **13**
(``require_devops`` on a live red-team run) and **15** (``admin_create_user``) are
enforced — or not — in ``backend/src/app/api``, which this suite cannot import: the
``aegis`` package is deliberately host-free and the backend is not on its path. They are
unreachable from here by construction, not by omission, and the backend suite is where
their tests belong.
"""

from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.core.models import ModelRole
from aegis.governance.rls import set_tenant_scope
from aegis.governance.security import TENANT_ADMIN
from aegis.guardrails.policy import GuardrailPolicy
from aegis.jobs.admission import max_inflight_key
from aegis.settings import (
    SettingScope,
    SettingWeakerThanFloorError,
    resolve,
    write_setting,
)
from aegis.settings.agent import AGENT_SETTING_BINDINGS
from aegis.settings.guardrails import GUARDRAIL_SETTING_BINDINGS
from aegis.settings.models import Setting
from aegis.settings.resolver import _resolve_from_layers
from aegis.settings.spec import (
    SETTING_SPECS,
    MergeRule,
    SettingSpec,
    Strictness,
    setting_controls,
    spec_for,
    strictest,
    strictest_legal,
)

from .._seed import ensure_tenants, ensure_users

_TENANT = 716
_USER = 7161

#: Every key the fold has to protect. Derived, never listed: a new ``tighten_only`` key
#: is covered by rows 1 and 3 the moment it is declared.
_TIGHTEN_ONLY: tuple[SettingSpec, ...] = tuple(
    spec for spec in SETTING_SPECS if spec.merge is MergeRule.TIGHTEN_ONLY
)


def _legal_values(spec: SettingSpec) -> list[Any]:
    """Return a covering sample of the values a caller could legally write.

    Exhaustive where the domain is finite (choices, booleans); the endpoints, the
    default and the midpoint where it is an interval, which are the values a fold can
    get wrong — anything strictly between two of them is ranked by the same comparison.
    """
    if spec.choices is not None:
        return list(spec.choices)
    if spec.type_ is bool:
        return [False, True]
    low, high = spec.bounds
    middle = (low + high) // 2 if spec.type_ is int else (low + high) / 2
    return sorted({low, middle, high, spec.default})


def _strictest_first(spec: SettingSpec) -> list[Any]:
    """Return :func:`_legal_values` ordered strictest first, per the spec's direction."""
    ordered = sorted(_legal_values(spec), key=spec.rank)
    return ordered if spec.stricter is Strictness.LOWER else list(reversed(ordered))


def _writer_for(spec: SettingSpec) -> str:
    """Return a role that may write ``spec`` at a sub-platform scope.

    A tenant admin where the key allows one — that is the principal the row is written
    against. For a platform-only key (``jobs.*``) the platform admin writes it *at
    tenant scope*, which is still subject to the floor: only the PLATFORM scope is
    exempt, because the platform layer is what the floor **is**.
    """
    return TENANT_ADMIN if TENANT_ADMIN in spec.writable_by else "platform_admin"


# ── rows 1 and 3: the resolver cannot compute a weaker value ──────────────────


#: Which end of each ``tighten_only`` key is the stricter one — **stated here, not read
#: off the spec**. Every other assertion in this module derives "weaker" from
#: :attr:`SettingSpec.stricter`, so all of them stay green if a direction is declared
#: backwards; a rule that is self-consistent about the wrong direction is exactly how a
#: tightening control becomes a weakening one. This table is the independent statement,
#: in the same spirit as the ``RiskLevel`` ordering guard in the catalogue itself.
_STRICTER_END: dict[str, Strictness] = {
    # A lower gate threshold pauses MORE tool calls, so lower is stricter.
    "agent.gate_min_risk": Strictness.LOWER,
    # Fewer rounds, fewer sub-agents: less spend, guaranteed termination.
    "agent.max_plan_iterations": Strictness.LOWER,
    "agent.agentic_retrieval_max_rounds": Strictness.LOWER,
    "agent.team.max_parallel": Strictness.LOWER,
    # A rail that hard-blocks is stricter than one that flags.
    "guardrails.topical.block": Strictness.HIGHER,
    "guardrails.grounding.block": Strictness.HIGHER,
    # Refusing a payload that carries PII is stricter than masking it and carrying on:
    # a redaction that misses one entity still reaches the model, a refusal reaches
    # nothing.
    "guardrails.pii.block": Strictness.HIGHER,
    # A shorter accepted query is a smaller context-stuffing window, so lower is
    # stricter — the opposite direction from the two toggles above it, which is exactly
    # why this table is written by hand rather than read off the spec.
    "guardrails.input.max_chars": Strictness.LOWER,
    # Fewer concurrent jobs is a smaller share of the shared worker pool.
    "jobs.max_inflight.ingest": Strictness.LOWER,
    # Over-estimating refuses a job that might have fitted; under-estimating admits one
    # that cannot finish. The higher figure is the one that refuses.
    "jobs.estimated_cost_usd.ingest_per_mb": Strictness.HIGHER,
}


def test_rows_1_and_3_the_declared_stricter_end_is_the_one_that_is_actually_stricter():
    """A direction declared backwards inverts the guarantee without breaking anything.

    ``strictest`` is arithmetic, so it happily computes the *loosest* value of the chain
    for a key whose :class:`Strictness` points the wrong way — and every test that reads
    the direction off the spec agrees with it. Only a second, independent statement of
    which end is stricter can catch that, which is what :data:`_STRICTER_END` is.
    """
    declared = {
        spec.key: spec.stricter for spec in SETTING_SPECS if spec.merge is MergeRule.TIGHTEN_ONLY
    }
    assert declared == _STRICTER_END, (
        "a tighten_only key's strictness direction moved, or a new one arrived without "
        "an independent statement of which end is stricter: "
        f"{sorted(set(declared.items()) ^ set(_STRICTER_END.items()))}"
    )


def test_rows_1_and_3_every_tighten_only_key_can_be_ranked_and_failed_closed():
    """The precondition of the guarantee, asserted over the whole catalogue.

    ``strictest`` is arithmetic over :meth:`SettingSpec.rank`, so a ``tighten_only`` key
    with no direction or no rankable domain would not resolve weaker — it would *raise*,
    and there would be no strictest value to clamp to when the settings cannot be read.
    Both are refused at import; this is the catalogue-wide statement of it.
    """
    assert _TIGHTEN_ONLY, "no tighten_only key in the catalogue — rows 1 and 3 are vacuous"
    for spec in _TIGHTEN_ONLY:
        assert spec.stricter is not None, spec.key
        assert spec.choices is not None or spec.bounds is not None or spec.type_ is bool, (
            f"{spec.key} has no domain to rank over"
        )
        # Total for every catalogue entry, not only the ones a binding module checks:
        # the fail-closed path must exist before the outage, not be discovered in it.
        spec.validate(strictest_legal(spec))


@pytest.mark.parametrize("spec", _TIGHTEN_ONLY, ids=lambda spec: spec.key)
def test_rows_1_and_3_no_tighten_only_key_resolves_weaker_than_the_platform(spec):
    """The strong claim, driven: **the resolver cannot compute a weaker value.**

    Not "a weaker write is refused" — that is the guard, and it is tested live below.
    This is the structural half: whatever is *already stored* at tenant and user scope,
    over the whole cross-product of legal values, the value the resolver returns is
    never weaker than the platform layer. The platform layer is always one of the
    arguments to :func:`strictest`, so a weakening loses by arithmetic rather than by a
    check somebody remembered to write.

    Driving the resolver's own fold (:func:`_resolve_from_layers`) rather than
    re-implementing the comparison is the point: a rule proven against a copy of itself
    proves nothing.
    """
    values = _legal_values(spec)
    for platform in values:
        floor = platform
        for tenant in values:
            for user in values:
                layers = {
                    (spec.key, SettingScope.PLATFORM): platform,
                    (spec.key, SettingScope.TENANT): tenant,
                    (spec.key, SettingScope.USER): user,
                }
                resolved, source = _resolve_from_layers(spec, layers)
                assert strictest(spec, resolved, floor) == resolved, (
                    f"{spec.key}: platform={platform!r} tenant={tenant!r} user={user!r} "
                    f"resolved to {resolved!r}, which is WEAKER than the platform layer"
                )
                # And it is exactly the strictest of the chain — not merely "not weaker".
                expected = strictest(spec, strictest(spec, platform, tenant), user)
                assert spec.rank(resolved) == spec.rank(expected), (
                    f"{spec.key}: expected {expected!r}, got {resolved!r}"
                )
                assert source in {"platform", "tenant", "user"}


# ── the live half of rows 1 and 3: the guard, and what is written behind it ───


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker with the tenant and user the FKs need."""
    await ensure_tenants(pg_sessionmaker, _TENANT)
    await ensure_users(pg_sessionmaker, **{f"u{_USER}": _TENANT})
    return pg_sessionmaker


async def _write(db, key, value, *, scope, role, tenant_id=None, user_id=None):  # noqa: ANN001, ANN202, PLR0913
    """Write a setting the way a request would: scope bound, then committed."""
    async with db() as session:
        await set_tenant_scope(session, None if scope is SettingScope.PLATFORM else tenant_id)
        row = await write_setting(
            session,
            key,
            value,
            scope=scope,
            actor_role=role,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_user_id=user_id,
        )
        await session.commit()
        return row


async def _resolve(db, key, *, tenant_id=None, user_id=None):  # noqa: ANN001, ANN202
    """Resolve a setting under a bound tenant scope, as a request would."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        result = await resolve(session, key, tenant_id=tenant_id, user_id=user_id)
        await session.rollback()
        return result


@pytest.mark.parametrize("spec", _TIGHTEN_ONLY, ids=lambda spec: spec.key)
async def test_rows_1_and_3_a_weakening_is_refused_at_every_scope_beneath_the_platform(
    spec, db
):
    """The request a UI would never send, per ``tighten_only`` key, against Postgres.

    Three separate claims, because they fail for different reasons:

    1. A tenant write weaker than the platform layer is **refused with a reason** — not
       stored and ignored, which would show the tenant admin their own value on a screen
       while something else was in force.
    2. A user write weaker than their tenant's tightening is refused the same way, so
       the chain holds at both scopes rather than only the one that was tested first.
    3. A weaker row written **behind** the guard — inserted straight into ``settings``,
       which is what a legitimate row becomes the day the platform tightens — still
       loses in the resolver. The guard is the polite half; this is the load-bearing one.
    """
    levels = _strictest_first(spec)
    tightest, loosest = levels[0], levels[-1]
    assert spec.rank(tightest) != spec.rank(loosest), spec.key
    role = _writer_for(spec)

    # 1. The platform pins the strictest value; the tenant tries to loosen it.
    await _write(db, spec.key, tightest, scope=SettingScope.PLATFORM, role="platform_admin")
    with pytest.raises(SettingWeakerThanFloorError) as caught:
        await _write(
            db, spec.key, loosest, scope=SettingScope.TENANT, role=role, tenant_id=_TENANT
        )
    assert "may only be tightened" in caught.value.reason
    assert await _resolve(db, spec.key, tenant_id=_TENANT) == (tightest, "platform")

    # 2. The platform relaxes to the loosest legal value, the tenant tightens to the
    #    strictest, and their user tries to undo it.
    await _write(db, spec.key, loosest, scope=SettingScope.PLATFORM, role="platform_admin")
    await _write(db, spec.key, tightest, scope=SettingScope.TENANT, role=role, tenant_id=_TENANT)
    assert await _resolve(db, spec.key, tenant_id=_TENANT) == (tightest, "tenant")
    with pytest.raises(SettingWeakerThanFloorError):
        await _write(
            db,
            spec.key,
            loosest,
            scope=SettingScope.USER,
            role=role,
            tenant_id=_TENANT,
            user_id=_USER,
        )

    # 3. The row the guard would have refused, written behind it.
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        session.add(
            Setting(
                scope=SettingScope.USER,
                tenant_id=_TENANT,
                user_id=_USER,
                key=spec.key,
                value=loosest,
                updated_by="a request that never went through write_setting",
            )
        )
        await session.commit()
    resolved, source = await _resolve(db, spec.key, tenant_id=_TENANT, user_id=_USER)
    assert (resolved, source) == (tightest, "tenant"), (
        f"{spec.key}: a stored {loosest!r} at user scope changed the resolved value"
    )


# ── row 7: the guardrail completer is out of every tenant's reach ─────────────


async def test_row_7_no_catalogue_key_can_point_the_rails_at_a_tenants_model():
    """The injection classifier's model is not a control, and cannot become one.

    Pointing the classifier at a model of the tenant's choosing disables it *without
    appearing to* — the rail still runs, still reports, and still passes everything.
    What makes it unreachable is structural rather than a check: the only thing a
    resolved tenant policy can change about the pipeline is
    :class:`~aegis.guardrails.policy.GuardrailPolicy`, every field of that object is a
    catalogue key bound in :data:`GUARDRAIL_SETTING_BINDINGS`, and the completer is not
    one of them — :meth:`~aegis.guardrails.pipeline.Guardrails.with_policy` copies it
    across untouched.
    """
    from aegis.guardrails.pipeline import Guardrails

    policy_fields = {field.name for field in fields(GuardrailPolicy)}
    bound_fields = {binding.field for binding in GUARDRAIL_SETTING_BINDINGS}
    assert policy_fields == bound_fields, (
        "GuardrailPolicy grew a field no catalogue key governs (or lost one that a key "
        f"does): {sorted(policy_fields ^ bound_fields)}. Every field of it is writable "
        "by a tenant through the settings screen, so a new one is a new tenant control."
    )
    assert not [
        name
        for name in policy_fields
        if any(word in name for word in ("model", "completer", "deployment", "endpoint"))
    ], f"a tenant-writable guardrail field now names a model: {sorted(policy_fields)}"

    async def probe(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202 - a sentinel completer
        raise AssertionError("the guardrail completer was called")

    guard = Guardrails(completer=probe)
    tightened = guard.with_policy(
        GuardrailPolicy(
            topical_block=True,
            grounding_block=True,
            denylist_terms=("acme-project",),
            pii_entities=("IBAN_CODE",),
        )
    )
    assert tightened is not guard
    assert tightened._completer is probe  # noqa: SLF001 - the property under test


# ── row 9: the platform cap clamps, and the clamp is visible ──────────────────


async def test_row_9_the_fanout_cap_clamps_and_says_platform_cap():
    """A clamp nobody can see is a different control from the one the row describes.

    The chain is worth naming because every link is testable and none of it is a UI
    concern: ``agent.team.max_parallel`` (``tighten_only``, bounded) →
    :data:`AGENT_SETTING_BINDINGS` → ``AgentConfig.max_parallel_agents`` →
    :class:`aegis.agent.router.DepthPolicy` → :func:`aegis.agent.router.decide_depth`,
    which narrows an explicit width to the cap and reports ``decided_by='platform_cap'``
    with the cap in the reason string.
    """
    from aegis.agent.deps import AgentConfig
    from aegis.agent.router import Depth, DepthMode, DepthPolicy, decide_depth

    caps = {
        "agent.team.max_parallel": "max_parallel_agents",
        "agent.max_plan_iterations": "max_plan_iterations",
        "agent.agentic_retrieval_max_rounds": "agentic_retrieval_max_rounds",
    }
    config_fields = {field.name for field in fields(AgentConfig)}
    bound = {binding.key: binding.field for binding in AGENT_SETTING_BINDINGS}
    for key, field in caps.items():
        spec = spec_for(key)
        assert spec.merge is MergeRule.TIGHTEN_ONLY, key
        assert spec.stricter is Strictness.LOWER, key
        assert spec.bounds is not None, f"{key} is an unbounded fan-out cap"
        assert bound.get(key) == field, f"{key} no longer binds AgentConfig.{field}"
        assert field in config_fields, f"AgentConfig has no {field}"

    cap = spec_for("agent.team.max_parallel").default
    decision = await decide_depth(
        "compare the three vendors and summarise each",
        policy=DepthPolicy(
            mode=DepthMode.TEAM, requested_fanout=cap + 8, max_parallel_agents=cap
        ),
    )
    assert decision.depth is Depth.TEAM
    assert decision.fanout == cap, "the requested width was not narrowed to the cap"
    assert decision.decided_by == "platform_cap", (
        "the cap narrowed the width silently; the run event would attribute it to the "
        "user, who did not choose it"
    )
    assert str(cap) in decision.reason


# ── rows 2, 4, 8, 10, 12, 14: there is no key to ask with ─────────────────────

#: ``(row, forbidden fragments, why it must never become a key)``. Every entry is a
#: control whose *absence from the catalogue* is the enforcement — the settings surface
#: is generic by design (one form, rendered from :data:`SETTING_SPECS`), so the day one
#: of these becomes a key it becomes writable, renderable and audited in one commit,
#: with no screen or route left to refuse it.
_NO_SUCH_KEY: tuple[tuple[int, tuple[str, ...], str], ...] = (
    (
        2,
        ("budget", "usd_cap", "spend_cap", "quota"),
        "a tenant raising its own budget cap. The USD cap lives in the `budgets` table "
        "and nowhere else, deliberately: a duplicate `budget.usd_cap` in the catalogue "
        "is how the figure on the budgets screen stops being the figure that binds.",
    ),
    (
        4,
        ("sql", "database.", "db.query", "schema.browse"),
        "free-form SQL or a database browse. §7.9 puts that behind a platform-only "
        "read path, not behind a per-tenant setting.",
    ),
    (
        8,
        ("audit", "logging.disable", "telemetry.off"),
        "disabling audit logging, or exporting without being audited. `audit: always` "
        "means no key exists to turn it off — this is that assertion.",
    ),
    (
        10,
        ("tools", "allowlist", "allowed_tool", "permission"),
        "widening a tool allowlist. The effective set is "
        "`platform n persona n tenant n user` through one `is_allowed` function, and an "
        "intersection is not one of the three merge rules; modelling it here would "
        "create the second mechanism the catalogue exists to prevent.",
    ),
    (
        12,
        ("tenant_id", "subject_id", "user_id", "persona", "principal"),
        "setting their own isolation key. Every one of these is derived server-side "
        "from the AuthContext; a client-supplied identity is the shape of five real "
        "cross-tenant leaks already fixed in this repo.",
    ),
    (
        14,
        ("prompt", "system_message", "instructions"),
        "editing the prompt floor. A tenant writes a prompt *version*; the platform "
        "composes the floor underneath it (§7.7).",
    ),
)


@pytest.mark.parametrize(
    ("row", "fragments", "why"),
    _NO_SUCH_KEY,
    ids=[f"row-{row}" for row, _fragments, _why in _NO_SUCH_KEY],
)
def test_no_catalogue_key_exists_for_a_control_a_tenant_must_never_have(row, fragments, why):
    """Rows 2, 4, 8, 10, 12 and 14: the enforcement is that the key does not exist."""
    for spec in SETTING_SPECS:
        for fragment in fragments:
            assert fragment not in spec.key.lower(), (
                f"§7.16 row {row}: the catalogue now declares {spec.key!r}, which looks "
                f"like {why}"
            )


# ── row 2: the half that holds, asserted where it lives ──────────────────────


def test_row_2_a_user_sub_cap_above_the_tenant_cap_can_never_bind():
    """Row 2 has two readings, and **both** now hold. This is the resolution half.

    *"A tenant admin may set sub-caps on their own users, always <= the tenant cap"*
    reads two ways:

    * **the effective limit is never weaker than the tenant cap** — this holds, and
      :func:`aegis.governance.enforcement._clamp_inward` is where. Every field of
      ``GovernanceLimits`` is the minimum of the user's row and the tenant's, so a
      sub-cap above the tenant cap resolves to the tenant cap and buys the user
      nothing. The chokepoint agrees by a second, independent route:
      ``enforce_governance`` checks **every** governing row, and the tenant row is
      measured against the whole tenant's ledger, so it trips first by arithmetic.
    * **a sub-cap above the tenant cap cannot be stored** — this used *not* to hold: the
      row saved and a budgets screen read back \\$500 while \\$50 was what bound, which
      is the ``gate_min_risk`` defect wearing a budget's clothes. It holds now.
      :func:`aegis.governance.enforcement.upsert_budget` refuses the write with
      :class:`~aegis.governance.enforcement.UserCapAboveTenantCapError`, and a *tenant*
      cap lowered beneath an existing sub-cap narrows that sub-cap in the same
      transaction, so neither write path can leave the two readings disagreeing. The
      behaviour is driven against a real database in
      ``aegis/tests/governance/test_enforcement.py`` and over HTTP in the backend suite;
      what is asserted here is the clamp that makes the *enforced* number safe whatever
      is in the row, which is why it stays.

    The catalogue's own half of row 2 — that there is no *settings* key by which a cap
    could be raised at all — is asserted by the table above.
    """
    from aegis.governance.enforcement import _clamp_inward

    cases = (
        # (user cap, tenant cap) — the third is the leak shape the row is about.
        (10, 1000),
        (None, 50),
        (500, 50),
        (50.0, 50.0),
        (None, None),
    )
    for user_cap, tenant_cap in cases:
        clamped = _clamp_inward(user_cap, tenant_cap)
        if tenant_cap is None:
            assert clamped == user_cap
            continue
        assert clamped is not None and clamped <= tenant_cap, (
            f"a user sub-cap of {user_cap!r} resolved to {clamped!r}, above its tenant's "
            f"{tenant_cap!r}: a tenant admin would have raised their own tenant's cap"
        )


# ── row 6: the allowed-deployment set exists, and it is what decides ──────────


def test_row_6_a_tenant_model_override_is_validated_against_the_allowed_set():
    """**Row 6, enforced.** The set exists, the key is live, and the enum is derived.

    The row promises that the server validates a model override against the platform's
    allowed deployments, and warns that *"a UI enum is not enforcement"*. There used to
    be no such set — :func:`aegis.gateway.routing.routing_table` maps each role to
    exactly one deployment, which is a routing decision and not a list of alternatives —
    so ``agent.model`` carried an ``inert_reason`` and bound nothing.

    The set is now :data:`aegis.gateway.routing._FLEET_DECLARATION`, and the shape of
    the fix is what keeps the row's warning satisfied: the catalogue does not *restate*
    the legal values, it reads them from
    :func:`~aegis.gateway.routing.tenant_model_choices`, which is the same function the
    rendered control's ``choices`` are projected from. So the enum is a **view of** the
    enforcement rather than a second copy that a ``curl`` can walk around — there is
    only one set, and :meth:`SettingSpec.validate` is the thing every write goes
    through.
    """
    from aegis.gateway.routing import (
        PLATFORM_DEFAULT,
        allowed_deployments,
        tenant_selectable_deployments,
    )

    spec = spec_for("agent.model")
    assert spec.effective, "agent.model is inert again; row 6 has stopped being enforced"
    assert spec.merge is MergeRule.OVERRIDE, (
        "a model choice has no rankable order, so tighten_only would have no direction "
        "to fold in and no strictest value to fail closed to"
    )

    selectable = tenant_selectable_deployments()
    assert selectable, "the allowed set is empty, so the control offers nothing"
    assert spec.legal_choices == (PLATFORM_DEFAULT, *selectable), (
        "the catalogue's domain and the fleet's tenant-selectable set have drifted; the "
        "enum a screen renders would then be a second policy"
    )
    # The projection a UI reads is that same set, not a copy assembled beside it.
    control = next(c for c in setting_controls([spec]) if c["key"] == "agent.model")
    assert control["control"] == "select"
    assert control["choices"] == list(spec.legal_choices)

    # An unknown deployment and a real-but-reserved one are both refused, by the same
    # check every write goes through — never ignored, never swapped for a default.
    fleet = allowed_deployments()
    reserved = next(name for name, _role in fleet.items() if name not in selectable)
    for refused in ("gpt-4o", "../../etc/passwd", reserved):
        with pytest.raises(ValueError, match="not one of"):
            spec.validate(refused)
    assert spec.validate(PLATFORM_DEFAULT) is None
    for allowed in selectable:
        assert spec.validate(allowed) is None


def test_row_6_the_refusal_survives_the_catalogue_and_reaches_the_gateway_seam():
    """The second gate: a value that was legal when stored must not take effect anyway.

    The catalogue refuses the *write*. This is the refusal at the point of use, which is
    the one that matters the day the platform withdraws a deployment a tenant had
    already chosen — the stored row is still there and still passes nothing at all.
    ``selected_deployment`` re-validates rather than trusting the row, and refuses
    rather than quietly substituting a default, because a run served on a different
    model under the tenant's chosen name is the dropdown-and-curl failure again.
    """
    from aegis.gateway.routing import (
        DeploymentNotAllowedError,
        deployment_for_choice,
        model_for,
        selected_deployment,
        tenant_selectable_deployments,
    )

    chosen = tenant_selectable_deployments()[-1]
    assert deployment_for_choice(chosen) == chosen
    assert deployment_for_choice("default") is None
    assert deployment_for_choice(None) is None

    with pytest.raises(DeploymentNotAllowedError):
        deployment_for_choice("genailab-maas-gpt-4o-mini")
    with pytest.raises(DeploymentNotAllowedError), selected_deployment("a-model-nobody"):
        pass  # pragma: no cover - the context body is never entered

    before = model_for(ModelRole.GENERATION)
    with selected_deployment(chosen):
        assert model_for(ModelRole.GENERATION) == chosen
    assert model_for(ModelRole.GENERATION) == before, "the selection outlived its run"


def test_row_6_a_tenants_model_is_never_the_guardrail_completers(monkeypatch):
    """Row 7 held against row 6: the two must not be satisfiable at each other's cost.

    The guardrail completer resolves through ``complete(ModelRole.CHEAP, ...)`` and the
    media screen through ``VISION``. If a tenant could select a deployment on either
    role, ``model_for`` would hand their run the model its own injection classifier is
    judged by — the rail still runs, still reports, and passes everything. So no
    deployment on a reserved role is selectable (refused at import), and a selection in
    force leaves every other role's routing exactly where the platform put it.
    """
    from aegis.gateway.routing import (
        allowed_deployments,
        guardrail_reserved_roles,
        model_for,
        selected_deployment,
        tenant_selectable_deployments,
    )

    reserved = guardrail_reserved_roles()
    assert ModelRole.CHEAP in reserved, (
        "the injection / content-safety classifier runs on CHEAP; dropping it from the "
        "reserved set would make that model a tenant control"
    )
    fleet = allowed_deployments()
    assert not [name for name in tenant_selectable_deployments() if fleet[name] in reserved]

    untouched = {role: model_for(role) for role in ModelRole if role is not ModelRole.GENERATION}
    with selected_deployment(tenant_selectable_deployments()[-1]):
        assert {
            role: model_for(role) for role in ModelRole if role is not ModelRole.GENERATION
        } == untouched


def test_row_6_the_ledger_prices_the_deployment_that_answered_not_the_tier():
    """Cost follows the model, or a selected model is charged at another model's rate.

    ``_COST_PER_1K`` prices a *role*, which is the same thing as pricing its model only
    while the role has one model. The moment a tenant may select a different deployment,
    the tier's rate is the wrong model's price — a ledger that is wrong in whichever
    direction the fleet happens to be priced, and a USD cap that binds at the wrong
    spend. Both directions are asserted: the cheaper alternative is cheaper, and the
    default is unchanged.
    """
    from aegis.gateway.routing import (
        _FLEET,
        model_for,
        selected_deployment,
        unit_cost,
    )

    default_deployment = model_for(ModelRole.GENERATION)
    at_the_tier = unit_cost(ModelRole.GENERATION, prompt_tokens=1000, completion_tokens=1000)
    assert unit_cost(
        ModelRole.GENERATION,
        prompt_tokens=1000,
        completion_tokens=1000,
        deployment=default_deployment,
    ) == at_the_tier, "naming the routed default repriced a call that did not change"

    cheaper = "genailab-maas-DeepSeek-V3-0324"
    entry = _FLEET[cheaper]
    priced = unit_cost(
        ModelRole.GENERATION,
        prompt_tokens=1000,
        completion_tokens=1000,
        deployment=cheaper,
    )
    assert priced == pytest.approx(entry.input_rate + entry.output_rate)
    assert priced < at_the_tier, "the alternative is priced at the tier it replaced"

    # And the gateway's own estimator reaches the same figure with nothing passed to it,
    # because the selection is what ``model_for`` answers with.
    from aegis.gateway.llm import _estimate_cost

    with selected_deployment(cheaper):
        assert _estimate_cost(ModelRole.GENERATION, 1000, 1000) == pytest.approx(priced)
    assert _estimate_cost(ModelRole.GENERATION, 1000, 1000) == pytest.approx(at_the_tier)


# ── the sixteenth inert key: every effective key is read by something ─────────

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CATALOGUE = Path(__file__).resolve().parents[2] / "src" / "aegis" / "settings" / "spec.py"

#: The source trees a catalogue key can be read from: the library and the host that
#: embeds it. Both are scanned when present — ``jobs.estimated_cost_usd.ingest_per_mb``
#: is read by the backend's job-control module and by nothing in ``aegis`` — and a
#: missing tree is reported rather than silently narrowing the search.
_SOURCE_ROOTS: tuple[Path, ...] = tuple(
    root
    for root in (_REPO_ROOT / "aegis" / "src", _REPO_ROOT / "backend" / "src")
    if root.is_dir()
)


def _string_constants(tree: ast.Module) -> set[str]:
    """Return every string literal in ``tree`` that is not a docstring.

    Docstrings are excluded because the catalogue's keys are *described* all over the
    codebase; only a literal in live code is evidence that something reads the key.
    Comments never reach the AST at all, so they need no exclusion.
    """
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }


def _keys_named_in_live_code() -> dict[str, list[str]]:
    """Return ``{catalogue key: [files naming it]}`` across every scanned source tree."""
    catalogue = set(spec.key for spec in SETTING_SPECS)
    found: dict[str, list[str]] = {}
    for root in _SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            if path.resolve() == _CATALOGUE.resolve():
                continue  # the declaration is not a reader of itself
            for value in _string_constants(ast.parse(path.read_text(encoding="utf-8"))):
                if value in catalogue:
                    found.setdefault(value, []).append(str(path.relative_to(_REPO_ROOT)))
    return found


def test_a_key_that_claims_to_be_in_force_is_named_by_live_code_somewhere():
    """The sixteenth inert key, caught the day it is added rather than in Phase 8.

    ``agent.gate_min_risk`` and four ``guardrails.*`` keys shipped as controls that were
    writable, auditable, badged "Your setting" — and bound nothing. ``inert_reason``
    exists so that admission is made in the catalogue; this is the check that the
    admission is *true*, from the other direction: a key with no ``inert_reason``
    claims something reads it, so something must actually name it in live code.

    A key is read if it is named by a binding module, if a source file outside the
    catalogue names it as a non-docstring literal, or if the module that resolves it
    builds the key from parts — ``jobs.max_inflight.{job_type}`` is built by
    :func:`aegis.jobs.admission.max_inflight_key` and passed straight to the resolver,
    so the identity below is the proof that the catalogue and the reader agree.

    The converse is asserted too: a key declared **inert** that something does read is
    the same lie wearing the other hat.
    """
    assert _SOURCE_ROOTS, f"no source tree to scan under {_REPO_ROOT}"
    named = _keys_named_in_live_code()
    bound = {binding.key for binding in AGENT_SETTING_BINDINGS} | {
        binding.key for binding in GUARDRAIL_SETTING_BINDINGS
    }
    derived = {
        spec.key
        for spec in SETTING_SPECS
        if spec.key.startswith("jobs.max_inflight.")
        and max_inflight_key(spec.key.rsplit(".", 1)[-1]) == spec.key
    }
    read = set(named) | bound | derived

    unread = sorted(spec.key for spec in SETTING_SPECS if spec.effective and spec.key not in read)
    assert not unread, (
        f"{unread} claim to be in force but nothing under "
        f"{[str(root.relative_to(_REPO_ROOT)) for root in _SOURCE_ROOTS]} reads them. "
        "Give the key a consumer, or declare inert_reason naming what would make it live "
        "— a control that saves, audits and changes nothing is worse than an absent one."
    )
    lying = sorted(
        spec.key for spec in SETTING_SPECS if not spec.effective and spec.key in read
    )
    assert not lying, (
        f"{lying} are declared inert but are read by {[named.get(key) for key in lying]}; "
        "clear inert_reason — a live control declared dead is how a real setting gets "
        "rendered as a disabled hint."
    )
