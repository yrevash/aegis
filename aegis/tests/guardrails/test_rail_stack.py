"""``Guardrails.rail_stack()`` — the honest read half of §7.6.

*"Here is exactly what we screen, read it yourself"* is only a trust feature while the
table is read off the pipeline that runs. Two claims, and they fail differently:

* every field a tenant can write is attributed to a rail, so the guardrails screen can
  never show a control without the rail it governs (driven from the bindings, so a
  fourth control added tomorrow is covered the moment it is declared);
* the description follows the **folded** policy, not the host's — otherwise a tenant who
  tightened a rail would read the platform's posture back and believe it was theirs.
"""

from __future__ import annotations

from aegis.guardrails import Guardrails
from aegis.guardrails.policy import GuardrailPolicy
from aegis.settings.guardrails import GUARDRAIL_SETTING_BINDINGS


def _by_id(guard: Guardrails) -> dict[str, object]:
    return {rail.id: rail for rail in guard.rail_stack()}


def test_every_tenant_writable_field_is_attributed_to_a_rail():
    """A control with no rail on the screen is a control nobody can reason about."""
    claimed = {
        field
        for rail in Guardrails(completer=None, injection_cache=None).rail_stack()
        for field in rail.policy_fields
    }
    bound = {binding.field for binding in GUARDRAIL_SETTING_BINDINGS}
    assert bound <= claimed, (
        f"{sorted(bound - claimed)} are tenant-writable but no rail claims them; the "
        "screen would show a setting with nothing to attach it to"
    )
    assert claimed <= bound, (
        f"{sorted(claimed - bound)} are attributed to a rail but are not tenant "
        "controls at all"
    )


def test_the_stack_describes_the_folded_policy_not_the_hosts():
    """The tenant's tightening is what the tenant reads back."""
    host = Guardrails(completer=None, injection_cache=None, allowed_topics="insurance")
    assert _by_id(host)["topical"].enforcement == "advisory"
    assert _by_id(host)["pii"].enforcement == "redact"
    assert _by_id(host)["denylist"].active is False

    tenant = host.with_policy(
        GuardrailPolicy(
            topical_block=True,
            pii_block=True,
            input_max_chars=500,
            denylist_patterns=("jwt",),
        )
    )
    rails = _by_id(tenant)
    assert rails["topical"].enforcement == "block"
    assert rails["pii"].enforcement == "block"
    assert rails["denylist"].active is True
    assert rails["input_schema"].threshold == "500 characters"
    # And the host's own object is unchanged: the stack is per policy, never global.
    assert _by_id(host)["input_schema"].threshold == "8000 characters"


def test_a_rail_that_cannot_run_says_so_rather_than_disappearing():
    """"We do not screen topics here" is an answer; a missing row is not."""
    offline = Guardrails(completer=None, injection_cache=None)
    rails = _by_id(offline)
    assert rails["topical"].active is False, "no topics wired, so the rail cannot run"
    assert rails["content_safety"].active is False, "no completer, so it cannot run"
    assert rails["content_safety"].model_backed is True
    assert rails["injection"].active is True, (
        "the injection rail keeps its deterministic signatures with no completer"
    )
