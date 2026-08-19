"""The HTTP contract, pinned: ``backend/openapi.json`` **is** what the server serves.

Why a snapshot and not a set of hand-written assertions: the same reason
``web/src/config/graphTopology.json`` and ``docs/module/PIPELINES.md`` are snapshots in
this repo. A hand-written assertion pins the thing somebody thought to pin; a committed
document pins *everything*, so removing a field, renaming a response model, dropping a
route or loosening a request model all fail here, in review, instead of surprising an
integrator at runtime. The remedy is always one command, printed in the failure::

    backend/.venv/bin/python scripts/build_openapi.py

The other three tests are the properties the snapshot alone would let you *review* but
not *enforce* — a stale reviewer is exactly how the four ``extra="ignore"`` incidents
got in, so each is asserted rather than left to a reading of the diff.

Nothing here touches the network or the database: ``app.openapi()`` is derived from the
route table.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_args

import pytest
from jsonschema import Draft202012Validator, ValidationError

import app.main
from app.api.openapi import API_PREFIX, INFRA_PATHS, STREAM_EVENT_SCHEMA_NAME
from app.api.schemas import RunStarted, StreamEvent

#: Repo root, from ``backend/tests/api/`` → ``backend/`` → repo.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_SNAPSHOT = _REPO_ROOT / "backend" / "openapi.json"
_BUILDER = _REPO_ROOT / "scripts" / "build_openapi.py"

#: FastAPI synthesises a body model per multipart endpoint (file uploads). They are the
#: framework's, not ours, and it does not mark them ``additionalProperties: false``.
_FRAMEWORK_BODY_PREFIX = "Body_"


def _served() -> dict:
    """The document the app actually serves."""
    return app.main.app.openapi()


def _committed() -> dict:
    """The document committed to the repo."""
    return json.loads(_SNAPSHOT.read_text(encoding="utf-8"))


def test_the_committed_snapshot_is_the_served_schema() -> None:
    """Any unreviewed change to the HTTP contract fails here."""
    assert _SNAPSHOT.exists(), f"{_SNAPSHOT} is missing; run {_BUILDER}"
    served, committed = _served(), _committed()

    if served != committed:
        served_paths, committed_paths = set(served["paths"]), set(committed["paths"])
        added = sorted(served_paths - committed_paths)
        removed = sorted(committed_paths - served_paths)
        pytest.fail(
            "the served OpenAPI document has drifted from backend/openapi.json.\n"
            f"  routes added:   {added or 'none'}\n"
            f"  routes removed: {removed or 'none'}\n"
            "  (a difference with neither is a changed schema — a model, a field or a "
            "response)\n"
            "Review the change, then regenerate:\n"
            "  backend/.venv/bin/python scripts/build_openapi.py\n"
            "  cd web && npm run gen:api"
        )


def test_every_route_is_versioned_except_the_infrastructure_probes() -> None:
    """``/v1`` is the version boundary, and only the probes sit outside it.

    The probes are outside on purpose: a load balancer, an orchestrator and the
    console's boot probe dial them, they answer the same question in every version, and
    a probe URL that moves during a version rollout is a liveness check that starts
    404-ing. They are served at exactly one path each — there is no ``/v1/health``.
    """
    paths = set(_served()["paths"])
    unversioned = sorted(p for p in paths if not p.startswith(f"{API_PREFIX}/"))
    assert unversioned == sorted(INFRA_PATHS), (
        f"these routes are served outside {API_PREFIX}: {unversioned}. Only the "
        f"infrastructure probes {sorted(INFRA_PATHS)} may be."
    )
    aliased = sorted(
        p for p in paths if p.startswith(API_PREFIX) and p[len(API_PREFIX) :] in INFRA_PATHS
    )
    assert aliased == [], (
        f"a probe is served twice: {aliased}. One path per probe, or an operator has "
        f"two answers to 'which URL do I configure'."
    )
    assert len(paths) > 90, f"the route table is implausibly small: {len(paths)}"


def test_every_request_body_forbids_a_field_it_does_not_carry() -> None:
    """``extra="forbid"`` is published as ``additionalProperties: false`` — enforce it.

    This is the check that would have caught the four incidents. ``agent_id``,
    ``actions``, ``depth_mode`` and ``session_id`` were each added to the wire, proven
    to reach the model, and rendered nowhere, because pydantic's default drops an
    unrecognised field in silence and answers 200.

    ``tests/api/test_request_models_forbid_extras.py`` asserts the rule over classes
    whose name ends in ``Request``; it therefore never saw ``BrowseIn`` or
    ``InspectionIn``, which really were permissive. The published document has no
    naming convention to be fooled by: it lists the schema of every body the API
    accepts.
    """
    served = _served()
    components = served["components"]["schemas"]
    permissive: list[str] = []
    checked = 0
    for path, operations in served["paths"].items():
        for method, operation in operations.items():
            body = operation.get("requestBody")
            if body is None:
                continue
            for media in body["content"].values():
                ref = media["schema"].get("$ref")
                if ref is None:
                    continue
                name = ref.rsplit("/", 1)[-1]
                if name.startswith(_FRAMEWORK_BODY_PREFIX):
                    continue  # FastAPI's own multipart body model
                checked += 1
                if components[name].get("additionalProperties") is not False:
                    permissive.append(f"{method.upper()} {path} → {name}")
    assert checked >= 25, f"too few request bodies found to trust this: {checked}"
    assert permissive == [], (
        "these request bodies silently drop a field they do not recognise and answer "
        f"200 — the exact failure that hid session_id and depth_mode: {permissive}"
    )


def test_the_stream_event_union_is_published_and_validates_a_real_frame() -> None:
    """§8.8: the product's primary interface is machine-readable, and it is correct.

    Three things, because publishing a schema that describes something other than what
    the wire carries would be worse than publishing none:

    1. the union exists in ``components`` with every Python variant in it;
    2. it is discriminated on ``type`` — the field inside the frame's ``data`` payload,
       which is what the client parses (the ``event:`` line duplicates it);
    3. a real serialised event validates against the published document, and one
       carrying an unknown ``type`` does not.
    """
    served = _served()
    components = served["components"]["schemas"]
    union = components[STREAM_EVENT_SCHEMA_NAME]

    assert union["discriminator"]["propertyName"] == "type"
    published = set(union["discriminator"]["mapping"])
    python = {
        member.model_fields["type"].default for member in get_args(get_args(StreamEvent)[0])
    }
    assert published == python, (
        f"the published union and app.api.schemas.StreamEvent disagree: "
        f"missing {sorted(python - published)}, extra {sorted(published - python)}"
    )

    # The SSE response is declared as what it actually is, not as JSON.
    response = served["paths"][f"{API_PREFIX}/query"]["post"]["responses"]["200"]
    schema = response["content"]["text/event-stream"]["schema"]
    assert schema["$ref"].endswith(f"/{STREAM_EVENT_SCHEMA_NAME}")

    # The document's own ``components`` travel with the reference, so every ``$ref``
    # the union reaches for resolves inside the published schema and nowhere else.
    validator = Draft202012Validator(
        {
            "$ref": f"#/components/schemas/{STREAM_EVENT_SCHEMA_NAME}",
            "components": served["components"],
        }
    )
    frame = RunStarted(run_id="r-1", seq=1, trace_id="t-1").model_dump(mode="json")
    validator.validate(frame)
    with pytest.raises(ValidationError):
        validator.validate({**frame, "type": "not_a_variant"})


def test_the_snapshot_builder_agrees_with_this_test() -> None:
    """Guard the guard: ``--check`` must fail on the same drift this file fails on.

    Two copies of "is the snapshot current" that could disagree would let one of them
    pass for the wrong reason — so the builder's own check is exercised here rather
    than trusted.
    """
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    import build_openapi

    assert build_openapi.SNAPSHOT == _SNAPSHOT
    assert build_openapi.render() == _SNAPSHOT.read_text(encoding="utf-8")
