"""The public standards summary — the counts must be *counted*, and nothing else may leak.

``GET /platform/standards`` exists so a public, unauthenticated landing page can name
the frameworks Aegis is built to and say how far each one actually goes. That puts two
loads on it, and each is one of the two things this module asserts.

**The number a visitor reads must be the number the repository can defend.** A
landing page is the single worst place in a codebase to type a figure: nobody reruns
the compliance table when they edit a marketing section, and "27 enforced" outliving
the twenty-seventh control is a lie told at the top of the funnel by a product whose
entire pitch is that it does not do that. So the tests below do not check the counts
against expected literals — pinning ``29`` here would just move the hardcode into the
test suite, and the India frameworks are being extended right now. They check the
summary against :func:`app.platform.compliance.build_compliance`, framework by
framework and total by total, and they re-count the control entries independently. A
summary that stopped deriving fails here.

**A public gap map is a target list.** ``GET /compliance`` is guarded, and the reason
is its body: every control's missing layer, with the file that would have implemented
it. This route summarises the same table for anonymous readers, so the second test
walks the served JSON and asserts that no control id, no gap sentence, no evidence
reference and no residency detail came with it.
"""

from __future__ import annotations

import pytest

# Imported for its side effect: the control planes that live in their own modules
# attach themselves to ``router`` from the composition root, so importing ``routes``
# alone yields a table missing this module's own route.
import app.main  # noqa: F401
from app.api.routes_standards import build_standards
from app.platform.compliance import DISCLAIMER, DOC_REF, ControlState, build_compliance

pytestmark = pytest.mark.anyio


def test_every_count_is_derived_from_the_real_control_table() -> None:
    """The totals equal a fresh count of the control entries, not a stored figure.

    Two independent derivations must agree: the summary's ``coverage``, and a count
    taken here by walking every framework's controls and tallying the four states. If
    somebody replaces the projection with literals, the two stop agreeing the first
    time a control changes state — which is exactly when it matters.
    """
    full = build_compliance()
    summary = build_standards()

    counted = {state: 0 for state in ControlState}
    total = 0
    for framework in full.frameworks:
        for control in framework.controls:
            counted[control.state] += 1
            total += 1

    assert summary.coverage.total == total
    assert summary.coverage.enforced == counted[ControlState.ENFORCED]
    assert summary.coverage.partial == counted[ControlState.PARTIAL]
    assert summary.coverage.not_implemented == counted[ControlState.NOT_IMPLEMENTED]
    assert summary.coverage.not_applicable == counted[ControlState.NOT_APPLICABLE]

    # And the totals are the sum of the parts, so a framework cannot be dropped from
    # the public band while still being counted in the headline figure.
    assert sum(f.coverage.total for f in summary.frameworks) == summary.coverage.total


def test_every_mapped_framework_reaches_the_public_band() -> None:
    """A framework added to the authority appears here without editing this module.

    The short-mark map in ``routes_standards`` is keyed by framework id and falls back
    to the full name. That fallback is the load-bearing part while another agent is
    adding frameworks: an id the map has not met must still be served — long label and
    all — rather than vanish from a grid whose argument is that it is complete.
    """
    full = build_compliance()
    summary = build_standards()

    assert [f.id for f in summary.frameworks] == [f.id for f in full.frameworks]
    for served, source in zip(summary.frameworks, full.frameworks, strict=True):
        assert served.name == source.name
        assert served.version == source.version
        assert served.jurisdiction == source.jurisdiction
        assert served.coverage == source.coverage
        assert served.mark, "a framework with no mark would render as an empty cell"


async def test_the_public_route_answers_without_a_token(client) -> None:
    """Anonymous, because the page that renders it is anonymous."""
    response = await client.get("/platform/standards")

    assert response.status_code == 200
    body = response.json()
    assert body["disclaimer"] == DISCLAIMER
    assert body["doc_ref"] == DOC_REF
    assert body["certified"] is False
    assert body["coverage"]["total"] == build_compliance().coverage.total


async def test_no_control_detail_travels_to_an_anonymous_reader(client) -> None:
    """Names, jurisdictions and counts — never a gap, an evidence ref or a residency row.

    ``GET /compliance`` stays guarded because "this control is not implemented, and
    here is the file that would have implemented it" is a map of where to push. The
    summary must not carry it by accident, so the whole payload is walked rather than
    the top-level keys inspected.
    """
    response = await client.get("/platform/standards")
    assert response.status_code == 200
    body = response.json()

    forbidden = {"controls", "control", "evidence", "gap", "summary", "residency", "state"}
    seen: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            seen.update(node.keys())
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(body)
    assert not (seen & forbidden), f"control detail leaked into the public band: {seen & forbidden}"
