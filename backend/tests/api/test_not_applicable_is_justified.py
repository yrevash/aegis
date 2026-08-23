"""A control may be `not_applicable` only if it says why, in substance.

This test exists because of a rule change on the public landing band. "Enforced in
full" used to mean ``enforced == total``, counting `not_applicable` against a
framework. That is the stricter-sounding rule and it is the wrong one: MITRE ATLAS
maps `AML.T0018 Backdoor ML Model`, and the reason it does not apply here is
structural — the only fitted model is trained in-process from the host's own frame,
so no downloaded artefact exists that could have been backdoored. Counting that as a
shortfall asserts that a risk we cannot have is a gap we failed to close, which is
not a stricter claim but a false one.

So the band now claims completeness on an *applicable-controls* denominator. That
widening is only safe while `not_applicable` is expensive to assert — otherwise the
cheapest route to a headline claim on a public page is to mark the inconvenient
controls inapplicable and let the denominator shrink to meet the numerator.

Three things make it expensive, and this file is the third:

* the band prints the set-aside count beside every claim made on that basis, so a
  reader sees the denominator move;
* the reason for each one is served by ``GET /v1/compliance`` for anyone to read;
* and here, every `not_applicable` control must carry a **substantive** reason — one
  that says something about *this* deployment rather than restating the state.

The bar is deliberately about content, not length alone: a reason must name why the
control cannot bite here. Nothing in this file checks that the reason is *true* —
only a human can do that — but a reason that is present, specific and public is one
somebody can argue with, and an unjustified state is one nobody can.
"""

from __future__ import annotations

import pytest

from app.platform.compliance import ControlState, build_compliance

#: Short enough that a real justification clears it easily, long enough that "n/a",
#: "does not apply" and other restatements of the state do not.
_MIN_REASON_CHARS = 80

#: Phrases that merely restate the verdict. A reason built only from these explains
#: nothing — it is the state spelled out in words.
_RESTATEMENTS = (
    "not applicable",
    "n/a",
    "does not apply",
    "no applicable",
    "out of scope",
)


def _controls():
    """Every ``not_applicable`` control, as ``(framework_id, control, reason)``."""
    table = build_compliance()
    for framework in table.frameworks:
        for control in framework.controls:
            if control.state != ControlState.NOT_APPLICABLE:
                continue
            yield framework.id, control, (control.gap or control.summary or "").strip()


def test_the_table_actually_has_controls_to_check() -> None:
    """Guard against the parametrised tests below passing on an empty collection."""
    table = build_compliance()
    assert sum(len(f.controls) for f in table.frameworks) > 100, "table did not load"


@pytest.mark.parametrize(
    ("framework_id", "control", "reason"),
    list(_controls()),
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_not_applicable_control_states_a_substantive_reason(
    framework_id: str, control: object, reason: str
) -> None:
    """Marking a control inapplicable costs a real sentence about this deployment."""
    control_id = getattr(control, "id", "?")
    assert len(reason) >= _MIN_REASON_CHARS, (
        f"{framework_id} :: {control_id} is not_applicable with a reason of "
        f"{len(reason)} characters. The landing band claims completeness on an "
        "applicable-controls denominator, so a control set aside has to say why it "
        "cannot bite on this deployment — otherwise shrinking the denominator is the "
        f"cheapest way to manufacture a claim. Reason was: {reason!r}"
    )

    stripped = reason.lower()
    for phrase in _RESTATEMENTS:
        stripped = stripped.replace(phrase, "")
    assert len(stripped.strip()) >= _MIN_REASON_CHARS, (
        f"{framework_id} :: {control_id}'s reason is mostly a restatement of the "
        "state. Say what about this deployment puts the control out of reach, not "
        "that it is out of reach."
    )
