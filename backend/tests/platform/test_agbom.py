"""The AgBOM is a document a stranger's scanner reads. It has to actually validate.

Three properties, and each one shipped broken at least once.

**It validates as CycloneDX 1.6.** The module diverges from the OWASP AOS example on
purpose — tools are emitted as ``application`` because ``tool`` is not a member of the
1.6 ``component.type`` enum — and that divergence was defended in a docstring with no
test behind it. A docstring is not a validator. The schema is vendored beside this file
rather than fetched, so the answer is the same offline, in CI, and on a hackathon floor
with no network.

**It is deterministic.** It was not. ``import litellm`` calls ``load_dotenv()``, so
``os.environ`` gained every ``.env`` key the first time any request path imported it, and
an inventory built by asking the router "what would you pick right now" changed shape
mid-process — the same pid served six models at 10:20 and four at 10:35. An inventory
that moves is worse than no inventory, because the whole point of one is that you can
diff two of them.

**It is complete.** The old version reported at most one deployment per role: five
deployments with thousands of recorded answers each in ``usage_ledger`` were absent, and
two ids the fleet does not declare were present.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.platform.agbom import build_agbom

_SCHEMA = Path(__file__).parent / "schemas" / "bom-1.6.schema.json"


def _validator() -> object:
    jsonschema = pytest.importorskip("jsonschema")
    return jsonschema.Draft7Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))


def _errors(doc: dict) -> list[str]:
    return [f"{list(e.path)}: {e.message}" for e in _validator().iter_errors(doc)]


def test_the_agbom_validates_against_the_published_cyclonedx_schema() -> None:
    """The claim the whole format choice rests on."""
    assert _errors(build_agbom()) == []


def test_the_validator_is_not_vacuously_passing() -> None:
    """A validator that accepts anything would make the test above meaningless.

    Two negative controls, and the first is the exact divergence the module docstring
    defends: emitting ``type: "tool"`` — the spelling the AOS example uses — must fail,
    or there was never a reason to write ``application`` instead.
    """
    doc = build_agbom()
    doc["components"][0]["type"] = "tool"
    assert any("'tool' is not one of" in e for e in _errors(doc)), (
        "the schema accepted type='tool' — if that is really legal, the module's whole "
        "documented divergence from the AOS example is unnecessary"
    )

    missing = build_agbom()
    del missing["bomFormat"]
    assert _errors(missing), "the schema did not enforce a required key"


def test_two_agboms_from_one_process_are_identical() -> None:
    """The bug that made the document useless, in the order that caused it.

    ``import litellm`` is the mutation. Building before and after it must give the same
    bytes — otherwise a buyer diffing two inventories pulled from one deployment sees a
    fleet change that never happened.
    """
    before = json.dumps(build_agbom()["components"], sort_keys=True)
    import litellm  # noqa: F401 - imported for its load_dotenv() side effect

    after = json.dumps(build_agbom()["components"], sort_keys=True)
    assert before == after, (
        "the AgBOM changed shape inside one process; `.env` reached os.environ late "
        "and the inventory was built from whatever the router happened to see"
    )


def test_every_declared_deployment_is_in_the_inventory() -> None:
    """Under-reporting is the failure mode an SBOM exists to prevent.

    The old version emitted one deployment per role. Five models with thousands of rows
    each in ``usage_ledger`` — the very table this document points the reader at — were
    missing from it.
    """
    from aegis.gateway.routing import _FLEET_DECLARATION

    listed = {
        c["name"]
        for c in build_agbom()["components"]
        if c["type"] == "machine-learning-model"
    }
    missing = {e.id for e in _FLEET_DECLARATION} - listed
    assert not missing, f"declared deployments absent from the AgBOM: {sorted(missing)}"


def test_a_model_outside_the_fleet_is_labelled_rather_than_hidden() -> None:
    """Neither dropped nor quietly promoted.

    Configuration can route to an id the fleet does not declare. Omitting it would mean
    the inventory hides a model that answers requests; listing it as ``declared`` would
    mean the pricing table can be asked for a rate that does not exist. It is listed,
    and it says which it is.
    """
    from aegis.gateway.routing import _FLEET_DECLARATION

    declared = {e.id for e in _FLEET_DECLARATION}
    for c in build_agbom()["components"]:
        if c["type"] != "machine-learning-model":
            continue
        state = next(p["value"] for p in c["properties"] if p["name"] == "aegis:state")
        expected = "declared" if c["name"] in declared else "undeclared"
        assert state == expected, f"{c['name']} is marked {state!r}, expected {expected!r}"


def test_tenant_selectable_deployments_are_marked() -> None:
    """A tenant can pick these. An inventory that does not say so understates the surface."""
    from aegis.gateway.routing import _FLEET_DECLARATION

    expected = {e.id for e in _FLEET_DECLARATION if e.tenant_selectable}
    assert expected, "the fixture is vacuous: no deployment is tenant-selectable"

    marked = {
        c["name"]
        for c in build_agbom()["components"]
        if any(
            p["name"] == "aegis:model:tenant-selectable" and p["value"] == "true"
            for p in c["properties"]
        )
    }
    assert marked == expected
