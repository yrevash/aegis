"""Supply chain (OWASP LLM03): a vulnerability verdict and a standard SBOM export.

The LLM03 row used to carry two named absences: no CVE/OSV advisory feed, so what the
platform had was patch *freshness* rather than a vulnerability verdict; and no
CycloneDX/SPDX export, so the inventory was readable only by this platform's own
console. This module is what makes both claims checkable.

**Every test here is offline.** The one network call — OSV.dev — is replaced by a fake
transport, so this file never reaches the wire; the point of the tests is the
*behaviour around* the feed, and the behaviour that matters most is what happens when
the feed cannot be reached.

The load-bearing property, and the one to break first if you want to see these fail:
:func:`app.platform.advisories.audit_dependencies` may never report ``passed`` without a
real answer for every package. Delete the ``unknown`` branch from ``AdvisoryAudit.passed``
and ``test_an_audit_that_could_not_run_does_not_pass`` fails — which is precisely the
defect that turns an air-gapped build into a green supply-chain badge.
"""

from __future__ import annotations

import json

from tests.conftest import login_as

import app.platform.advisories as advisories
from app.platform.advisories import AdvisoryAudit, audit_dependencies
from app.platform.sbom import (
    CYCLONEDX_SPEC_VERSION,
    SPDX_VERSION,
    Component,
    build_cyclonedx,
    build_spdx,
    lockfile_digest_count,
    resolve_inventory,
)

_FIXTURE = [
    Component(
        name="jinja2", version="3.1.2", license_id="BSD-3-Clause",
        purl="pkg:pypi/jinja2@3.1.2",
    ),
    Component(
        name="fastapi", version="0.141.1", license_id="MIT",
        purl="pkg:pypi/fastapi@0.141.1",
    ),
]

_ADVISORY_DETAIL = {
    "id": "GHSA-q2x7-8rv6-6q7h",
    "summary": "Jinja has a sandbox breakout through indirect reference to format method",
    "aliases": ["CVE-2024-56326"],
    "database_specific": {"severity": "MODERATE"},
}


def _feed(*, batch, detail=_ADVISORY_DETAIL):
    """Install a fake OSV transport and return nothing — monkeypatched by the caller."""

    def post(url, payload):  # noqa: ANN001, ANN202 - test double
        if callable(batch):
            return batch(url, payload)
        return batch

    def get(url):  # noqa: ANN001, ANN202 - test double
        return detail

    return post, get


# ─────────────────────────────────────────────────────────────────────────────
# The advisory feed — a vulnerability verdict, not freshness
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_published_advisory_is_reported_against_the_installed_version(monkeypatch):
    """The verdict LLM03 was missing: this exact version, this published CVE."""
    post, get = _feed(
        batch={"results": [{"vulns": [{"id": "GHSA-q2x7-8rv6-6q7h"}]}, {}]}
    )
    monkeypatch.setattr(advisories, "_post_json", post)
    monkeypatch.setattr(advisories, "_get_json", get)

    audit = audit_dependencies(components=list(_FIXTURE))

    assert audit.online is True
    assert audit.passed is False, "a vulnerable dependency must never pass"
    vulnerable = {p.name for p in audit.vulnerable}
    assert vulnerable == {"jinja2"}
    row = next(p for p in audit.packages if p.name == "jinja2")
    assert row.status == "vulnerable"
    assert row.worst_severity == "moderate"
    # The identifier a reader recognises is the CVE, and it is carried, not dropped.
    assert "CVE-2024-56326" in row.vulnerabilities[0].aliases
    # The package OSV answered "no advisories" for is clean — and only that one.
    clean = next(p for p in audit.packages if p.name == "fastapi")
    assert clean.status == "clean"
    assert clean.worst_severity == "none"


async def test_an_audit_that_could_not_run_does_not_pass(monkeypatch):
    """The load-bearing honesty rule. Break ``passed`` and this is what fails.

    An air-gapped build, a proxy that eats the request, OSV down — each of them
    produces zero findings, and reading zero findings as "no vulnerabilities" is the
    exact defect that makes a supply-chain badge worthless. Offline, every package is
    ``unknown``, ``online`` is ``False``, and ``passed`` is ``False``.
    """

    def refuse(url, payload):  # noqa: ANN001, ANN202
        raise advisories.AdvisoryFeedUnreachableError("no network")

    monkeypatch.setattr(advisories, "_post_json", refuse)

    audit = audit_dependencies(components=list(_FIXTURE))

    assert audit.online is False
    assert audit.passed is False
    assert {p.status for p in audit.packages} == {"unknown"}
    assert "not a clean bill of health" in audit.note
    # Nothing was invented: the installed versions are still reported, so a reader can
    # see *what* could not be checked.
    assert {p.version for p in audit.packages} == {"3.1.2", "0.141.1"}


async def test_a_clean_answer_from_the_feed_passes(monkeypatch):
    """The good case must actually be reachable, or the gate is just a red light."""
    post, get = _feed(batch={"results": [{}, {}]})
    monkeypatch.setattr(advisories, "_post_json", post)
    monkeypatch.setattr(advisories, "_get_json", get)

    audit = audit_dependencies(components=list(_FIXTURE))

    assert audit.online is True
    assert audit.passed is True
    assert {p.status for p in audit.packages} == {"clean"}


async def test_a_detail_fetch_that_fails_keeps_the_advisory_id(monkeypatch):
    """A missing summary must not become a missing finding."""
    post, _ = _feed(batch={"results": [{"vulns": [{"id": "PYSEC-2026-1475"}]}, {}]})
    monkeypatch.setattr(advisories, "_post_json", post)
    monkeypatch.setattr(advisories, "_get_json", lambda url: None)

    audit = audit_dependencies(components=list(_FIXTURE))

    row = next(p for p in audit.packages if p.name == "jinja2")
    assert row.status == "vulnerable"
    assert row.vulnerabilities[0].id == "PYSEC-2026-1475"
    assert row.vulnerabilities[0].detail_fetched is False
    assert audit.passed is False


async def test_the_cli_gate_exits_nonzero_on_a_finding(monkeypatch):
    """What CI runs. A gate that exits 0 on a vulnerable dependency gates nothing."""
    post, get = _feed(batch={"results": [{"vulns": [{"id": "GHSA-q2x7-8rv6-6q7h"}]}, {}]})
    monkeypatch.setattr(advisories, "_post_json", post)
    monkeypatch.setattr(advisories, "_get_json", get)
    monkeypatch.setattr(advisories, "resolve_inventory", lambda: list(_FIXTURE))
    monkeypatch.setattr(advisories, "acknowledged_ids", lambda *_a, **_k: frozenset())

    assert advisories.main() == 1

    post_clean, _ = _feed(batch={"results": [{}, {}]})
    monkeypatch.setattr(advisories, "_post_json", post_clean)
    assert advisories.main() == 0


async def test_the_cli_gate_also_fails_when_it_could_not_ask(monkeypatch):
    """No network, no verdict, no green build."""

    def refuse(url, payload):  # noqa: ANN001, ANN202
        raise advisories.AdvisoryFeedUnreachableError("no network")

    monkeypatch.setattr(advisories, "_post_json", refuse)
    monkeypatch.setattr(advisories, "resolve_inventory", lambda: list(_FIXTURE))
    assert advisories.main() == 1


async def test_an_acknowledged_advisory_unblocks_the_build_and_nothing_else(monkeypatch):
    """The acknowledgement file is a statement about the build, not about the risk.

    This is the property that keeps it from becoming a suppression list: the same
    advisory that stops failing CI is still ``vulnerable`` on the API, still counted,
    and the audit still does not pass.
    """
    post, get = _feed(batch={"results": [{"vulns": [{"id": "GHSA-q2x7-8rv6-6q7h"}]}, {}]})
    monkeypatch.setattr(advisories, "_post_json", post)
    monkeypatch.setattr(advisories, "_get_json", get)
    monkeypatch.setattr(advisories, "resolve_inventory", lambda: list(_FIXTURE))
    monkeypatch.setattr(
        advisories, "acknowledged_ids", lambda *_a, **_k: frozenset({"GHSA-q2x7-8rv6-6q7h"})
    )

    assert advisories.main() == 0, "an acknowledged advisory must not block the build"

    audit = audit_dependencies(components=list(_FIXTURE))
    assert audit.passed is False, "…and must still fail the audit itself"
    assert audit.vulnerable[0].name == "jinja2"


async def test_the_real_acknowledgement_file_parses_and_justifies_every_entry():
    """A suppression with no reason is a suppression; every entry must argue itself."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    data = json.loads((root / advisories.ACKNOWLEDGED_PATH).read_text(encoding="utf-8"))
    entries = data["acknowledged"]
    assert entries
    for entry in entries:
        assert entry["ids"], f"{entry['package']} acknowledges nothing"
        assert len(entry["blocked_by"]) > 30, f"{entry['package']}: name what pins it"
        assert len(entry["assessment"]) > 60, f"{entry['package']}: say why it is tolerable"
        assert len(entry["fix"]) > 30, f"{entry['package']}: say what would release it"
    assert advisories.acknowledged_ids() >= {"GHSA-g6cj-pr64-35w5", "GHSA-fr49-mhgj-crfc"}


async def test_an_unreadable_acknowledgement_file_acknowledges_nothing():
    """Fails closed: a missing list means nothing is acknowledged, not everything."""
    assert advisories.acknowledged_ids("backend/no_such_file.json") == frozenset()


# ─────────────────────────────────────────────────────────────────────────────
# The SBOM export — CycloneDX 1.6 and SPDX 2.3
# ─────────────────────────────────────────────────────────────────────────────


def test_the_inventory_is_resolved_from_the_running_interpreter():
    """Not a maintained list: the export must move when the environment does."""
    inventory = resolve_inventory()
    assert len(inventory) > 50, "the real environment has hundreds of distributions"
    names = {c.name.lower() for c in inventory}
    assert {"fastapi", "pydantic"} <= names
    fastapi = next(c for c in inventory if c.name.lower() == "fastapi")
    from importlib import metadata

    assert fastapi.version == metadata.version("fastapi"), "no invented versions"
    assert fastapi.purl == f"pkg:pypi/fastapi@{fastapi.version}"


def test_cyclonedx_is_well_formed_and_every_component_carries_a_purl():
    """A PURL is the key an advisory database joins on; a component without one is inert."""
    document = build_cyclonedx()
    assert document["bomFormat"] == "CycloneDX"
    assert document["specVersion"] == CYCLONEDX_SPEC_VERSION
    components = document["components"]
    assert components
    for component in components:
        assert component["purl"].startswith("pkg:pypi/")
        assert component["name"] and component["version"]
    json.dumps(document)  # a document that will not serialise is not a document


def test_spdx_says_noassertion_rather_than_guessing_a_licence():
    """A wrong SPDX licence identifier in a procurement review is a legal claim."""
    document = build_spdx(
        [Component(name="mystery", version="1.0", license_id="", purl="pkg:pypi/mystery@1.0")]
    )
    assert document["spdxVersion"] == SPDX_VERSION
    package = document["packages"][0]
    assert package["licenseDeclared"] == "NOASSERTION"
    assert package["licenseConcluded"] == "NOASSERTION"
    assert package["externalRefs"][0]["referenceLocator"] == "pkg:pypi/mystery@1.0"
    # Every package is DESCRIBES-related to the document, which is what makes it a
    # graph rather than a list.
    assert document["relationships"][0]["relatedSpdxElement"] == package["SPDXID"]
    json.dumps(document)


def test_both_documents_describe_the_same_machine():
    """Two exports resolved separately could disagree; these come from one pass."""
    inventory = resolve_inventory()
    cyclonedx = build_cyclonedx(inventory)
    spdx = build_spdx(inventory)
    assert len(cyclonedx["components"]) == len(spdx["packages"])
    assert {c["purl"] for c in cyclonedx["components"]} == {
        p["externalRefs"][0]["referenceLocator"] for p in spdx["packages"]
    }


def test_the_lockfile_pin_count_is_counted_not_claimed():
    """The integrity evidence that does exist is a number, and it is read off disk."""
    assert lockfile_digest_count() > 1000
    document = build_cyclonedx(list(_FIXTURE))
    pins = next(
        p for p in document["metadata"]["properties"]
        if p["name"] == "aegis:lockfile_sha256_pins"
    )
    assert int(pins["value"]) == lockfile_digest_count()
    # The absence is stated in the document rather than left for a reader to discover.
    attestation = next(
        p for p in document["metadata"]["properties"] if p["name"] == "aegis:attestation"
    )
    assert "unsigned" in attestation["value"]


# ─────────────────────────────────────────────────────────────────────────────
# The routes
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_advisory_route_serves_the_audit_to_devops(client, db, monkeypatch):
    post, get = _feed(batch={"results": [{"vulns": [{"id": "GHSA-q2x7-8rv6-6q7h"}]}, {}]})
    monkeypatch.setattr(advisories, "_post_json", post)
    monkeypatch.setattr(advisories, "_get_json", get)
    monkeypatch.setattr(advisories, "resolve_inventory", lambda: list(_FIXTURE))

    headers = await login_as(client, "devops")
    response = await client.post("/stack/advisories", json={}, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["passed"] is False
    assert body["packages_vulnerable"] == 1
    assert body["severity_counts"] == {"moderate": 1}
    assert body["source"].startswith("https://api.osv.dev")


async def test_the_advisory_route_refuses_a_business_role(client, db):
    """It is a fact about the deployment's dependencies, not about a tenant."""
    headers = await login_as(client, "client")
    response = await client.post("/stack/advisories", json={}, headers=headers)
    assert response.status_code == 403, response.text


async def test_the_sbom_route_serves_both_formats_with_their_own_media_types(client, db):
    """A scanner reads the media type; serving application/json defeats the point."""
    headers = await login_as(client, "devops")

    cyclonedx = await client.get("/stack/sbom", headers=headers)
    assert cyclonedx.status_code == 200, cyclonedx.text
    assert "cyclonedx" in cyclonedx.headers["content-type"]
    assert cyclonedx.json()["specVersion"] == CYCLONEDX_SPEC_VERSION

    spdx = await client.get("/stack/sbom?format=spdx", headers=headers)
    assert spdx.status_code == 200, spdx.text
    assert "spdx" in spdx.headers["content-type"]
    assert spdx.json()["spdxVersion"] == SPDX_VERSION


async def test_the_sbom_route_refuses_an_unknown_format(client, db):
    headers = await login_as(client, "devops")
    response = await client.get("/stack/sbom?format=csv", headers=headers)
    assert response.status_code == 422, response.text


async def test_the_sbom_route_requires_authentication(client, db):
    response = await client.get("/stack/sbom")
    assert response.status_code == 401


def test_the_audit_dataclass_never_passes_on_an_empty_inventory():
    """Zero packages audited is not zero vulnerabilities found."""
    empty = AdvisoryAudit(
        checked_at="now", online=True, note="", source="osv", packages=()
    )
    assert empty.passed is False
