"""Compliance readiness — every claim on the page must resolve to something real.

This is the module that makes the Compliance surface worth showing. A compliance
table is the easiest thing in a product to fake: it is prose in a data structure,
nothing executes it, and a plausible-sounding cell looks exactly like a true one. So
each evidence reference is **resolved against the real artefact** on every run:

* a ``file`` / ``doc`` reference must exist on disk at that path;
* a ``route`` reference must be in the **served route table**, with that method;
* a ``test`` reference must name a test function that actually exists in that file.

A claim that names a file which was renamed, a route which was removed, or a test
which was deleted fails here rather than reaching a jury or a buyer's reviewer.

Three more rules are enforced, and each one closes a specific way a table like this
goes bad:

* **``enforced`` is expensive.** It needs a file *and* a test. A state cannot be
  typed into existence with a sentence.
* **Every non-enforced state must say what is missing.** ``partial`` without a named
  missing layer is the word "partially" doing no work.
* **The disclaimer travels with the payload.** A readiness claim detached from
  "readiness, not certification" is the one defect this whole surface exists to
  avoid, so it is asserted on the wire and not merely in a docstring.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.conftest import login_as

# Imported for its side effect: the control planes that live in their own modules
# attach themselves to ``router`` from the composition root, so importing ``routes``
# alone yields a table missing every one of them — including this module's own route.
import app.main  # noqa: F401
from app.api.routes import router
from app.platform.compliance import (
    DISCLAIMER,
    DOC_REF,
    JURISDICTION_INDIA,
    JURISDICTION_INTERNATIONAL,
    ControlState,
    EvidenceKind,
    build_compliance,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]

#: Every ``METHOD /path`` pair the application actually serves.
_SERVED: set[str] = {
    f"{method} {route.path}"
    for route in router.routes
    for method in (getattr(route, "methods", ()) or ())
}

_MAP = build_compliance()

#: (framework id, control id, evidence) for every evidence item on the page — the
#: parametrisation that turns "a claim" into "a test that names the claim it failed".
_EVIDENCE = [
    pytest.param(
        framework.id, control.id, evidence, id=f"{framework.id}-{control.id}-{evidence.ref}"
    )
    for framework in _MAP.frameworks
    for control in framework.controls
    for evidence in control.evidence
]

_CONTROLS = [
    pytest.param(framework.id, control, id=f"{framework.id}-{control.id}")
    for framework in _MAP.frameworks
    for control in framework.controls
]


# ─────────────────────────────────────────────────────────────────────────────
# Every reference resolves
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("framework_id", "control_id", "evidence"), _EVIDENCE)
def test_every_evidence_reference_resolves(framework_id, control_id, evidence) -> None:
    """A file exists, a route is served, a test function is really in that file."""
    match evidence.kind:
        case EvidenceKind.FILE | EvidenceKind.DOC:
            assert (_REPO_ROOT / evidence.ref).exists(), (
                f"{framework_id}/{control_id} cites {evidence.ref!r}, which does not exist."
            )
        case EvidenceKind.ROUTE:
            assert evidence.ref in _SERVED, (
                f"{framework_id}/{control_id} cites route {evidence.ref!r}, "
                "which the application does not serve."
            )
        case EvidenceKind.TEST:
            path, _, name = evidence.ref.partition("::")
            test_file = _REPO_ROOT / path
            assert test_file.exists(), (
                f"{framework_id}/{control_id} cites test file {path!r}, which does not exist."
            )
            assert name, f"{framework_id}/{control_id} cites {evidence.ref!r} with no test name."
            assert re.search(
                rf"^\s*(async\s+)?def\s+{re.escape(name)}\s*\(", test_file.read_text(), re.MULTILINE
            ), f"{framework_id}/{control_id} cites {evidence.ref!r}; {name!r} is not in that file."


@pytest.mark.parametrize(("framework_id", "control"), _CONTROLS)
def test_enforced_needs_a_file_and_a_test(framework_id, control) -> None:
    """``enforced`` is the expensive state: code that runs, and a test that proves it.

    Without this, ``enforced`` costs one word. With it, the only way to reach that
    state is to point at a mechanism and at the test that fails when the mechanism
    stops working.
    """
    if control.state is not ControlState.ENFORCED:
        return
    kinds = {evidence.kind for evidence in control.evidence}
    assert EvidenceKind.FILE in kinds, (
        f"{framework_id}/{control.id} claims 'enforced' with no file backing it."
    )
    assert EvidenceKind.TEST in kinds, (
        f"{framework_id}/{control.id} claims 'enforced' with no test backing it."
    )


@pytest.mark.parametrize(("framework_id", "control"), _CONTROLS)
def test_anything_short_of_enforced_names_what_is_missing(framework_id, control) -> None:
    """``partial`` / ``not_implemented`` / ``not_applicable`` must say what is absent."""
    if control.state is ControlState.ENFORCED:
        assert control.gap == "", (
            f"{framework_id}/{control.id} is enforced but still carries a gap sentence; "
            "either the gap is real and the state is partial, or the sentence is stale."
        )
        return
    assert len(control.gap) >= 20, (
        f"{framework_id}/{control.id} is {control.state.value!r} without naming what is "
        "missing. 'partially' is not an answer."
    )


@pytest.mark.parametrize(("framework_id", "control"), _CONTROLS)
def test_every_control_says_something_concrete(framework_id, control) -> None:
    """No empty titles, ids or summaries — a blank cell reads as a covered one."""
    assert control.id.strip(), f"{framework_id} has a control with no id."
    assert control.title.strip(), f"{framework_id}/{control.id} has no title."
    assert len(control.summary) >= 20, f"{framework_id}/{control.id} has no real summary."


# ─────────────────────────────────────────────────────────────────────────────
# Shape, counts and the disclaimer
# ─────────────────────────────────────────────────────────────────────────────


def test_control_ids_are_unique_within_a_framework() -> None:
    """A duplicate id would double-count coverage and hide one of the two rows."""
    for framework in _MAP.frameworks:
        ids = [control.id for control in framework.controls]
        assert len(ids) == len(set(ids)), f"{framework.id} has duplicate control ids."


def test_framework_ids_are_unique() -> None:
    """The screen keys its sections on these."""
    ids = [framework.id for framework in _MAP.frameworks]
    assert len(ids) == len(set(ids))


def test_coverage_counts_are_derived_not_authored() -> None:
    """Per framework and in total, the counts equal the states actually present.

    Hand-authored totals drift the moment a state changes, and a stale total on a
    compliance page is a false claim with a number attached.
    """
    grand = {state: 0 for state in ControlState}
    for framework in _MAP.frameworks:
        counted = {state: 0 for state in ControlState}
        for control in framework.controls:
            counted[control.state] += 1
            grand[control.state] += 1
        assert framework.coverage.enforced == counted[ControlState.ENFORCED]
        assert framework.coverage.partial == counted[ControlState.PARTIAL]
        assert framework.coverage.not_implemented == counted[ControlState.NOT_IMPLEMENTED]
        assert framework.coverage.not_applicable == counted[ControlState.NOT_APPLICABLE]
        assert framework.coverage.total == len(framework.controls)
    assert _MAP.coverage.enforced == grand[ControlState.ENFORCED]
    assert _MAP.coverage.partial == grand[ControlState.PARTIAL]
    assert _MAP.coverage.not_implemented == grand[ControlState.NOT_IMPLEMENTED]
    assert _MAP.coverage.not_applicable == grand[ControlState.NOT_APPLICABLE]
    assert _MAP.coverage.total == sum(len(f.controls) for f in _MAP.frameworks)


def test_all_four_states_are_actually_used() -> None:
    """A four-state vocabulary that only ever emits two is a two-state one in disguise."""
    used = {control.state for framework in _MAP.frameworks for control in framework.controls}
    assert used == set(ControlState), f"unused states: {set(ControlState) - used}"


def test_the_disclaimer_refuses_to_claim_certification() -> None:
    """Readiness, not certification — on the payload, not only in a docstring."""
    assert "not certification" in DISCLAIMER
    assert _MAP.disclaimer == DISCLAIMER
    lowered = DISCLAIMER.lower()
    assert "iso 27001" in lowered and "soc 2" in lowered


def test_the_written_authority_exists() -> None:
    """The document the payload projects is in the repository and is not a stub."""
    doc = _REPO_ROOT / DOC_REF
    assert doc.exists(), f"{DOC_REF} — the authority behind this surface — is missing."
    text = doc.read_text()
    assert "not certification" in text
    for framework in _MAP.frameworks:
        assert framework.name.split(" —")[0] in text, (
            f"{framework.name!r} is served but is not in {DOC_REF}; the screen would be "
            "asserting something the written position does not."
        )


# ─────────────────────────────────────────────────────────────────────────────
# India — the home market, and the three ways this section could become dishonest
# ─────────────────────────────────────────────────────────────────────────────


def test_india_is_served_first() -> None:
    """Jurisdiction ordering is a decision, so a reorder should be a deliberate act.

    For a deployment in India the DPDP Act and the CERT-In Directions are law while the
    ISO and OWASP frameworks are practice. The picker's order is the reviewer's first
    impression of what this platform thinks it answers to, and it is not incidental.
    """
    jurisdictions = [framework.jurisdiction for framework in _MAP.frameworks]
    india = [i for i, j in enumerate(jurisdictions) if j == JURISDICTION_INDIA]
    assert india == list(range(len(india))), (
        f"India's frameworks are at positions {india}; they lead the list on purpose."
    )
    assert len(india) >= 3
    assert set(jurisdictions) == {JURISDICTION_INDIA, JURISDICTION_INTERNATIONAL}


def test_no_bfsi_compliance_is_claimed() -> None:
    """RBI and SEBI must never read better than 'not applicable'.

    This is the single most tempting overclaim on the page — "compliant with RBI" sells,
    and Aegis has not earned one word of it. The rows exist to say what a regulated
    deployment would inherit and what it would still owe, and if either ever moves to
    partial or enforced, this test is the thing that should stop it.
    """
    sectoral = next(f for f in _MAP.frameworks if f.id == "india-sectoral")
    for control_id in ("RBI ITGRCA", "SEBI CSCRF"):
        row = next(c for c in sectoral.controls if c.id == control_id)
        assert row.state is ControlState.NOT_APPLICABLE, (
            f"{control_id} claims {row.state.value!r}. Aegis is not a regulated entity and "
            "has been audited by nobody; this row may only ever be not_applicable."
        )
        assert "owe" in row.gap or "still" in row.gap, (
            f"{control_id} is not_applicable without saying what a deployment would still owe."
        )


def test_the_dpdp_paperwork_obligations_stay_unimplemented() -> None:
    """Consent, breach notification, DPIA and grievance do not exist. Say so.

    Each of these has a plausible-looking near-miss in the codebase — a notification
    inbox that is not grievance redressal, a risk map that is not a DPIA, an audit trail
    that is not a consent record. Those are exactly the substitutions a compliance table
    makes when nobody is checking.
    """
    dpdp = next(f for f in _MAP.frameworks if f.id == "dpdp")
    for control_id in ("s.5 / s.6", "s.8(6)", "s.10", "s.13"):
        row = next(c for c in dpdp.controls if c.id == control_id)
        assert row.state is ControlState.NOT_IMPLEMENTED, (
            f"DPDP {control_id} claims {row.state.value!r}; nothing in this repository "
            "implements it."
        )


def test_indias_rights_are_not_double_counted_under_gdpr() -> None:
    """The same erasure endpoint must not be counted once for India and once for the EU."""
    privacy = next(f for f in _MAP.frameworks if f.id == "privacy")
    assert privacy.jurisdiction == JURISDICTION_INTERNATIONAL
    for control in privacy.controls:
        assert "DPDP" not in control.id and "DPDP" not in control.title, (
            f"{control.id} still carries a DPDP reference; India has its own framework and "
            "counting the evidence in both inflates the coverage totals."
        )


def test_the_residency_inventory_travels_with_the_map() -> None:
    """Two India rows depend on it, so it ships in the same payload they do.

    DPDP s.16 and CERT-In Dir. (iv) are both questions about where data goes, and both
    cite ``residency`` as evidence. A reviewer reading either row must not have to fetch
    a second surface to check it.
    """
    residency = _MAP.residency
    assert residency.channels, "the inventory is empty"
    assert residency.stores_local + residency.stores_external > 0
    assert "does not geolocate" in residency.note
    cited = {
        evidence.ref
        for framework in _MAP.frameworks
        for control in framework.controls
        for evidence in control.evidence
    }
    assert "backend/src/app/platform/residency.py" in cited


def test_the_supply_chain_row_exists_and_is_honest() -> None:
    """LLM03 had zero references anywhere before this surface; it must not regress.

    The SBOM, the hash-pinned lockfiles and the registry patch-check were all real and
    none of them was labelled as supply-chain assurance. Labelling it is the whole
    point — and it must stay ``partial``, because inventory plus freshness is not
    vulnerability management, and an ``enforced`` here would be exactly the unearned
    green this page exists to refuse.
    """
    llm = next(f for f in _MAP.frameworks if f.id == "owasp-llm")
    row = next(c for c in llm.controls if c.id == "LLM03")
    assert row.state is ControlState.PARTIAL
    assert "CVE" in row.gap or "advisory" in row.gap
    refs = {evidence.ref for evidence in row.evidence}
    assert "backend/uv.lock" in refs
    assert "POST /stack/patch-check" in refs


# ─────────────────────────────────────────────────────────────────────────────
# The route
# ─────────────────────────────────────────────────────────────────────────────


async def test_compliance_route_serves_the_map_to_devops(client, db) -> None:
    """The devops portal's principal can read it, and the body carries the disclaimer."""
    headers = await login_as(client, "devops")
    response = await client.get("/compliance", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "not certification" in body["disclaimer"]
    assert body["doc_ref"] == DOC_REF
    assert len(body["frameworks"]) == len(_MAP.frameworks)
    assert body["coverage"]["total"] == _MAP.coverage.total


async def test_compliance_route_refuses_a_business_role(client, db) -> None:
    """It is a fact about the deployment, not about a tenant, so the end-user is refused."""
    headers = await login_as(client, "client")
    response = await client.get("/compliance", headers=headers)
    assert response.status_code == 403, response.text


async def test_compliance_route_requires_authentication(client, db) -> None:
    """No bearer, no map."""
    response = await client.get("/compliance")
    assert response.status_code == 401
