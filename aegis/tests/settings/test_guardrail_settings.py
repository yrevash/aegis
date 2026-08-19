"""Do the ``guardrails.*`` settings actually reach a rail?

The defect these cover is not "the value is wrong", it is **"nothing reads it"**. The
first four keys were in the catalogue, writable by a tenant admin, saved, audited and
badged "Your setting" on the settings screen, while:

* ``guardrails.grounding.block`` and ``guardrails.topical.block`` were read from the
  *host's* environment (``app.config.Settings.grounding_block``), which no tenant can
  see or change;
* ``guardrails.denylist.terms`` and ``guardrails.pii.entities`` were mentioned only in
  docstrings — no rail had ever been given a denied term or an entity kind to screen
  for. Two modules under ``aegis/websearch`` already *documented* their tenant-scoped
  behaviour as the reason the search cache may not hold a verdict, describing a
  mechanism that did not exist.

So every claim here is about what the *resolved policy* makes the rails do. The
arithmetic is the same as :mod:`aegis.settings.agent`'s and is proved the same way:

* a tenant's tightening reaches the rail, and a **different** tenant's request never
  sees it (a memoised policy would be one tenant's denylist applied to another's
  question);
* a host that wired something stricter is never loosened back to the platform default,
  and a ``UNION`` key can only ever grow;
* an unreadable policy fails **closed**, out loud, and never to the platform default —
  the loosest configuration the tenant could have chosen.

The last section covers §7.6's three additions — ``guardrails.denylist.patterns``
(``matches_pattern``, drawn from the vetted library), ``guardrails.input.max_chars``
(``max_length``) and ``guardrails.pii.block`` — which were written with their wire
rather than after it, and whose claims are the same three.
"""

from __future__ import annotations

import logging

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.core.types import GuardVerdict
from aegis.governance.rls import set_tenant_scope
from aegis.guardrails import Guardrails
from aegis.guardrails.policy import GuardrailPolicy
from aegis.settings import SettingScope, write_setting
from aegis.settings.guardrails import (
    resolve_guardrail_policy,
    strictest_guardrail_policy,
)

from .._seed import ensure_tenants

_TENANT = 621
_OTHER_TENANT = 622


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker with the tenants the settings FKs need."""
    await ensure_tenants(pg_sessionmaker, _TENANT, _OTHER_TENANT)
    return pg_sessionmaker


async def _write(db, key, value, *, tenant_id=_TENANT):  # noqa: ANN001, ANN202
    """Write a tenant-scoped guardrail setting the way a tenant admin's request would."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        await write_setting(
            session,
            key,
            value,
            scope=SettingScope.TENANT,
            actor_role="tenant_admin",
            tenant_id=tenant_id,
        )
        await session.commit()


async def _guard_for(db, guard: Guardrails, *, tenant_id: int) -> Guardrails:
    """Resolve ``tenant_id``'s policy and fold it onto ``guard``, as a host does."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        policy = await resolve_guardrail_policy(
            session, guard.policy, tenant_id=tenant_id
        )
        await session.rollback()
    return guard.with_policy(policy)


# ── the denylist: a term one tenant denies, and only that tenant ─────────────


async def test_a_tenants_denied_term_blocks_only_that_tenants_request(db):
    """Tenant 621 denies ``project-zephyr``; tenant 622 asks the same question freely.

    The first assertion is the wire that did not exist: ``guardrails.denylist.terms``
    had no consumer anywhere, so a tenant admin could add every confidential codename
    they own and every one of them would still sail through the input rail.

    The second is the reason the resolution may not be memoised. The pipeline is a
    process-wide singleton built once from the host's environment; writing 621's terms
    onto it would deny 622's perfectly legitimate question about their own project. That
    is why :meth:`Guardrails.with_policy` returns a *new* object rather than mutating.
    """
    await _write(db, "guardrails.denylist.terms", ["project-zephyr"])
    host = Guardrails(completer=None, injection_cache=None)
    assert host.policy.denylist_terms == (), "the host wired no terms; the fixture lies"

    question = "Summarise the Project-Zephyr launch plan."
    strict = await _guard_for(db, host, tenant_id=_TENANT)
    lax = await _guard_for(db, host, tenant_id=_OTHER_TENANT)

    blocked = await strict.check_input(question)
    assert blocked.verdict is GuardVerdict.BLOCK, blocked
    assert blocked.layer == "denylist", blocked
    assert "project-zephyr" in blocked.reason.lower(), blocked

    allowed = await lax.check_input(question)
    assert allowed.verdict is not GuardVerdict.BLOCK, (
        f"tenant {_OTHER_TENANT} was refused by tenant {_TENANT}'s denylist: {allowed!r}"
    )
    assert (await host.check_input(question)).verdict is not GuardVerdict.BLOCK, (
        "the process-wide pipeline was mutated by one tenant's resolution"
    )


# ── the PII entity set: additive, and the platform's kinds survive ───────────


async def test_a_tenants_extra_pii_entity_is_screened_and_the_platforms_are_kept(db):
    """Tenant 621 adds ``LOCATION``; emails keep being redacted for everyone.

    ``guardrails.pii.entities`` is ``UNION``-merged, and its platform default names
    three of the nine kinds the detection engine already screens. So the effective set
    is the union of the engine's curated allowlist and whatever resolved — which is what
    makes the key honest in both directions: a tenant naming *fewer* kinds cannot switch
    the rest off (the second assertion), and a tenant naming a new one gets it screened
    (the first).
    """
    pytest.importorskip("presidio_analyzer")
    from aegis.guardrails import pii

    if pii.active_engine() != "presidio":  # pragma: no cover - regex fallback host
        pytest.skip("the regex fallback engine has no LOCATION detector, by design")

    text = "Ship it to Paris, and mail ada@example.com."
    await _write(db, "guardrails.pii.entities", ["LOCATION"])
    host = Guardrails(completer=None, injection_cache=None)

    tightened = await _guard_for(db, host, tenant_id=_TENANT)
    assert tightened.policy.pii_entities[:3] == (
        "EMAIL_ADDRESS",
        "PHONE_NUMBER",
        "CREDIT_CARD",
    ), "the platform floor was replaced rather than added to"
    assert "LOCATION" in tightened.policy.pii_entities

    redacted = await tightened.check_output(text)
    assert redacted.verdict is GuardVerdict.REDACT, redacted
    assert redacted.redactions == ["EMAIL", "LOCATION"], redacted
    assert "Paris" not in redacted.text and "ada@example.com" not in redacted.text

    untouched = await (await _guard_for(db, host, tenant_id=_OTHER_TENANT)).check_output(text)
    assert untouched.redactions == ["EMAIL"], (
        f"tenant {_OTHER_TENANT} inherited tenant {_TENANT}'s entity kinds: {untouched!r}"
    )


# ── the two block toggles: tighten-only, in both directions ─────────────────


async def test_a_block_toggle_tightens_but_a_host_that_already_blocks_is_never_loosened(db):
    """The tenant may turn blocking on; nobody's silence turns the host's blocking off.

    Both halves matter. Before this, ``grounding_block`` came from the host's
    environment alone, so a tenant asking for a hard block got an advisory FLAG. After
    it, the fold is :func:`aegis.settings.spec.strictest` and not an assignment — so
    tenant 622, who never wrote anything and therefore resolves the platform default
    ``False``, cannot switch off a host that wired ``True``.
    """
    await _write(db, "guardrails.grounding.block", True)
    lax_host = Guardrails(completer=None, injection_cache=None)
    strict_host = Guardrails(
        completer=None, injection_cache=None, grounding_block=True, topical_block=True
    )

    tightened = await _guard_for(db, lax_host, tenant_id=_TENANT)
    assert tightened.policy.grounding_block is True, (
        "the tenant asked for a hard grounding block and the rail stayed advisory"
    )
    assert tightened.policy.topical_block is False, "an unwritten key was not left alone"

    silent = await _guard_for(db, strict_host, tenant_id=_OTHER_TENANT)
    assert (silent.policy.grounding_block, silent.policy.topical_block) == (True, True), (
        f"a tenant who wrote nothing loosened the host's rails: {silent.policy!r}"
    )


# ── the failure mode: closed, and loud ──────────────────────────────────────


class _UnreachableSettings:
    """A session whose every read fails — the settings database being down."""

    async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG002
        raise RuntimeError("settings database unreachable")


async def test_an_unreadable_policy_fails_closed_to_blocking_and_says_so(caplog):
    """A resolution that raises leaves the rails *stricter*, never at the default.

    The platform default is by construction the loosest configuration a tenant could
    have chosen, so degrading to it would silently discard whatever they tightened —
    the exact defect this seam exists to remove. Both toggles clamp to blocking and the
    two collections keep the platform floor, which is the most that is knowable without
    the read that just failed.
    """
    host = GuardrailPolicy()
    with caplog.at_level(logging.ERROR):
        resolved = await resolve_guardrail_policy(
            _UnreachableSettings(), host, tenant_id=_TENANT
        )

    assert resolved == strictest_guardrail_policy(host)
    assert (resolved.grounding_block, resolved.topical_block) == (True, True)
    assert (resolved.grounding_block, resolved.topical_block) != (
        host.grounding_block,
        host.topical_block,
    )
    assert resolved.pii_entities == ("EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD")
    assert any(
        str(_TENANT) in record.message and record.levelno >= logging.ERROR
        for record in caplog.records
    ), "the tenant whose rails could not be read was not named in any ERROR"


async def test_an_ungoverned_request_reads_nothing_and_changes_nothing(db):
    """``tenant_id=None`` is an offline run, not a failure, so it is not clamped.

    The host's policy already *is* the platform layer when there is no tenant layer to
    fold onto it. Returning the fail-closed clamp here would hard-block every rail in
    every unit test and CLI in the codebase.
    """
    policy = GuardrailPolicy(denylist_terms=("host-term",))
    async with db() as session:
        await session.close()  # proves no read was attempted
        assert await resolve_guardrail_policy(session, policy, tenant_id=None) == policy


# ── §7.6's three new rail controls ──────────────────────────────────────────


def test_the_input_ceiling_is_one_number_stated_in_three_places():
    """The platform's character limit is a floor only while all three agree on it.

    ``guardrails.input.max_chars`` is bounded *by the rail's own constant*, the policy
    object defaults to it, and the rail enforces it. If those three drifted, a tenant
    could be offered a "tightening" above the real limit (which the resolver would
    happily call legal) or the fail-closed clamp would land somewhere nobody chose.
    :mod:`aegis.guardrails.policy` is deliberately a stdlib-only leaf and so cannot
    import the constant; this is the assertion that pays for that.
    """
    from aegis.guardrails.schema import MAX_INPUT_CHARS
    from aegis.settings.spec import spec_for

    spec = spec_for("guardrails.input.max_chars")
    assert spec.bounds == (500, MAX_INPUT_CHARS)
    assert spec.default == MAX_INPUT_CHARS
    assert GuardrailPolicy().input_max_chars == MAX_INPUT_CHARS
    assert Guardrails(completer=None, injection_cache=None).policy.input_max_chars == (
        MAX_INPUT_CHARS
    )


async def test_a_tenant_shortens_the_accepted_query_and_cannot_lengthen_it(db):
    """``max_length``, the third §7.6 template — and it only points one way.

    Three claims, because they fail differently: the shortened ceiling reaches the
    schema rail (the wire); a value above the platform's constant is refused by the
    catalogue on write, which is what a ``curl`` meets rather than a disabled input; and
    a rail handed a longer ceiling than the platform's *still* enforces the platform's,
    so even a caller inside the process cannot widen the window.
    """
    from aegis.guardrails import schema
    from aegis.guardrails.schema import MAX_INPUT_CHARS
    from aegis.settings import SettingValueError

    await _write(db, "guardrails.input.max_chars", 500)
    host = Guardrails(completer=None, injection_cache=None)
    tightened = await _guard_for(db, host, tenant_id=_TENANT)
    assert tightened.policy.input_max_chars == 500

    long_question = "a" * 800
    blocked = await tightened.check_input(long_question)
    assert blocked.verdict is GuardVerdict.BLOCK and blocked.layer == "schema", blocked
    assert "500" in blocked.reason, blocked
    lax = await _guard_for(db, host, tenant_id=_OTHER_TENANT)
    assert (await lax.check_input(long_question)).verdict is not GuardVerdict.BLOCK

    with pytest.raises(SettingValueError, match="outside"):
        await _write(db, "guardrails.input.max_chars", MAX_INPUT_CHARS + 1)

    # And the rail itself refuses to be widened, whatever it is handed.
    assert schema.validate_input_format(
        "b" * (MAX_INPUT_CHARS + 1), max_chars=MAX_INPUT_CHARS * 10
    ).ok is False


async def test_a_tenant_switches_on_a_vetted_pattern_and_writes_an_id_not_a_regex(db):
    """``matches_pattern``, the second §7.6 template, and the reason it is a library.

    The tenant-facing value is an **id**. What proves that is not the screen — it is
    :meth:`SettingSpec.validate` refusing anything else on write, so the catastrophic
    write (a nested-quantifier regex, run by this process on the request path against
    attacker-influenced text) is refused by the same check every setting goes through
    rather than by a form somebody can walk around with a ``curl``.
    """
    from aegis.settings import SettingValueError

    await _write(db, "guardrails.denylist.patterns", ["aws_access_key_id"])
    host = Guardrails(completer=None, injection_cache=None)
    tightened = await _guard_for(db, host, tenant_id=_TENANT)
    assert tightened.policy.denylist_patterns == ("aws_access_key_id",)

    leak = "rotate this for me: AKIAIOSFODNN7EXAMPLE"
    blocked = await tightened.check_input(leak)
    assert blocked.verdict is GuardVerdict.BLOCK and blocked.layer == "denylist", blocked
    assert "AWS access key id" in blocked.reason, blocked
    assert "AKIAIOSFODNN7EXAMPLE" not in blocked.reason, (
        "the verdict quoted the secret it exists to keep out of logs"
    )
    # The same secret on the way *out* is the exfiltration half of the same rail.
    assert (await tightened.check_output(leak)).verdict is GuardVerdict.BLOCK

    lax = await _guard_for(db, host, tenant_id=_OTHER_TENANT)
    assert (await lax.check_input(leak)).verdict is not GuardVerdict.BLOCK

    for refused in ("(a+)+$", "aws_access_key", "../../etc/passwd"):
        with pytest.raises(SettingValueError, match="not among the allowed members"):
            await _write(db, "guardrails.denylist.patterns", [refused])


async def test_an_unvetted_pattern_id_blocks_rather_than_screening_nothing():
    """A stored id the library no longer vets is a rail that screens nothing.

    Which is the failure mode the whole module exists to prevent, so it fails closed:
    the text is refused and the reason names the id, rather than passing while a screen
    reports a configured rail. The catalogue cannot produce this state — the write is
    refused above — but a library entry *withdrawn* after a tenant selected it can, and
    that is precisely when the answer must not be "pass".
    """
    from aegis.guardrails import schema

    check = schema.denied_pattern("nothing to see", ["a_pattern_nobody_vetted"])
    assert check.ok is False
    assert "a_pattern_nobody_vetted" in check.reason
    assert schema.denied_pattern("nothing to see", []).ok is True


async def test_a_tenant_can_refuse_pii_where_the_platform_only_redacts(db):
    """``guardrails.pii.block``: the ordered pair (redact, refuse), tighten-only.

    Refusing is strictly more conservative than masking — a redaction that misses one
    entity still reaches the model, a refusal reaches nothing — which is why the key is
    ``TIGHTEN_ONLY`` with ``HIGHER`` stricter rather than a preference. The tenant who
    asked for it gets a BLOCK; the tenant who did not still gets the platform's
    redaction, on the same text through the same process-wide pipeline.
    """
    await _write(db, "guardrails.pii.block", True)
    host = Guardrails(completer=None, injection_cache=None)
    text = "my email is ada@example.com"

    tightened = await _guard_for(db, host, tenant_id=_TENANT)
    assert tightened.policy.pii_block is True
    blocked = await tightened.check_input(text)
    assert blocked.verdict is GuardVerdict.BLOCK and blocked.layer == "pii", blocked
    assert "ada@example.com" not in blocked.text, "the block handed back the raw PII"
    assert (await tightened.check_output(text)).verdict is GuardVerdict.BLOCK

    lax = await _guard_for(db, host, tenant_id=_OTHER_TENANT)
    assert (await lax.check_input(text)).verdict is GuardVerdict.REDACT
