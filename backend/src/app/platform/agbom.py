"""The Agent Bill of Materials — what this agent is made of, as one document.

## Why this exists

In March 2026 an actor published backdoored `litellm` releases to PyPI. They were live for
roughly forty minutes and reached on the order of 2,500 organisations. Aegis is clean — its
dependency pins are explicit and a fresh resolve honours them — but "clean" was a fact
nobody could check from outside, because the thing a buyer asks for is an inventory, and
the answer was a `pyproject.toml` and a promise.

Aegis already serves a dependency SBOM. What it did not serve is the *agent's* inventory:
which tools exist and at what risk tier, which model deployments this platform declares
and which of them each role currently routes to, which rails run, and what the agent can
read from. Those are the components that decide what an agent can do, and none of them
appear in a package manifest.

## The shape, and one deliberate divergence

CycloneDX 1.6, because the OWASP Agent Observability Standard's Inspect layer extends
CycloneDX rather than inventing a fourth format, and because a buyer's scanner already
reads it.

**Tools are emitted as `type: "application"`, not `type: "tool"`.** The AOS example
document uses `"tool"`, and `"tool"` is not a member of the CycloneDX 1.6 `component.type`
enum — that enum is `application | framework | library | container | platform |
operating-system | device | device-driver | firmware | file | machine-learning-model |
data | cryptographic-asset`. CycloneDX does have a `metadata.tools` field, which is a
different thing: the tools that *produced* the document, not components of the system.
Emitting `"tool"` would produce a document that fails schema validation, which defeats the
entire reason for using a standard format. The divergence from the AOS example is recorded
here rather than left for a reader to discover from a validator error.

## What it will not claim

Only what is actually resolvable at runtime. A model deployment that is configured is
listed as configured, not as verified reachable. The knowledge sources are the collections
this deployment holds, not a promise about their contents.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

__all__ = ["build_agbom"]

#: The CycloneDX revision this document claims.
SPEC_VERSION = "1.6"


def _tool_components() -> list[dict[str, Any]]:
    """Every tool the agent can call, with the risk tier that decides its gate.

    The risk tier is the point. A tool inventory that lists names tells a reader what the
    agent *can* do; the tier tells them which of those stop for a human, which is the
    property anyone assessing blast radius actually needs.
    """
    from app.adapter.tools import ALLOWLIST, TOOL_REGISTRY

    out: list[dict[str, Any]] = []
    for name, spec in sorted(TOOL_REGISTRY.items()):
        personas = sorted(p for p, allowed in ALLOWLIST.items() if name in allowed)
        risk = getattr(getattr(spec, "risk", None), "value", "unknown")
        out.append(
            {
                # See the module docstring: "tool" is not a CycloneDX component type.
                "type": "application",
                "bom-ref": f"tool/{name}",
                "name": name,
                "description": (getattr(spec, "description", "") or "")[:300],
                "properties": [
                    {"name": "aegis:kind", "value": "tool"},
                    {"name": "aegis:riskTier", "value": str(risk)},
                    {
                        "name": "aegis:readOnly",
                        "value": str(bool(getattr(spec, "read_only", False))).lower(),
                    },
                    {"name": "aegis:personas", "value": ",".join(personas) or "none"},
                    {"name": "aegis:scheme", "value": "local"},
                ],
            }
        )
    return out


def _model_components() -> list[dict[str, Any]]:
    """Every model deployment this platform declares, and which one each role routes to.

    ``machine-learning-model`` is a real CycloneDX 1.6 component type, so these need no
    divergence.

    **This function previously carried an explanation that was false, and the code it
    justified produced a different answer every few minutes.** The claim was that the
    router reads ``MODEL_<ROLE>`` from ``os.environ`` while ``.env`` is loaded only into
    pydantic settings, so "requested" and "observed" necessarily diverge. Six lines
    disprove it::

        >>> import os; os.environ.get("MODEL_GENERATION")     # None
        >>> import litellm                                    # calls load_dotenv()
        >>> os.environ.get("MODEL_GENERATION")                # 'DeepSeek-V4-Flash'

    ``import litellm`` calls ``load_dotenv()``. So ``.env`` **is** in the environment, the
    router does find it, and the id it requests is the same one the ledger records. There
    was no divergence to be honest about.

    The real defect the false explanation hid: because the environment changes when
    ``litellm`` is first imported, an inventory built by asking the router *"what would
    you pick right now"* is non-deterministic **within a single process**. Measured on
    the live server: the same pid served 6 models at 10:20 and 4 different ones at 10:35.
    A buyer diffing two AgBOMs from one deployment would see a fleet change that never
    happened — which is precisely the thing an SBOM exists to make impossible.

    So this enumerates ``_FLEET_DECLARATION``: the platform's own statement of what it
    runs. Twelve deployments, fixed at import, identical on every call. Whichever one a
    role currently routes to is marked with ``aegis:routing:inForce`` rather than being
    the only thing listed — "declared" and "in force" are different facts and the
    document now carries both instead of silently collapsing them into the second.

    Enumerating the declaration also fixes an inventory that was wrong in both
    directions: it omitted five deployments with thousands of recorded answers each in
    ``usage_ledger``, and it emitted two ids the fleet does not declare and the pricing
    table cannot even look up.
    """
    from aegis.gateway.routing import _FLEET_DECLARATION, model_for

    # Resolved once, here, so the property is a snapshot of routing rather than a second
    # source of truth. It may legitimately name an id outside the declaration — a gateway
    # override, for instance — and when it does, that id appears as its own component
    # below rather than being silently dropped or silently substituted.
    in_force: dict[str, str] = {}
    for entry in _FLEET_DECLARATION:
        routed = model_for(entry.role)
        if routed:
            in_force[str(routed)] = entry.role.value

    declared = {entry.id for entry in _FLEET_DECLARATION}
    out: list[dict[str, Any]] = []

    for entry in sorted(_FLEET_DECLARATION, key=lambda e: e.id):
        props = [
            {"name": "aegis:kind", "value": "model"},
            {"name": "aegis:model:role", "value": entry.role.value},
            {"name": "aegis:state", "value": "declared"},
            {
                "name": "aegis:model:tenant-selectable",
                "value": str(bool(entry.tenant_selectable)).lower(),
            },
            {
                "name": "aegis:observedIn",
                "value": "usage_ledger.model — the deployment that actually replied",
            },
        ]
        if entry.id in in_force:
            props.append({"name": "aegis:routing:inForce", "value": "true"})
        out.append(
            {
                "type": "machine-learning-model",
                "bom-ref": f"model/{entry.id}",
                "name": entry.id,
                "properties": props,
            }
        )

    # An id the router resolves to that the fleet does not declare. Listing it as
    # `undeclared` is the honest move: hiding it would mean the inventory omits a model
    # that answers requests, and pretending it is declared would mean the pricing table
    # can be asked for a rate it does not have.
    for routed, role in sorted(in_force.items()):
        if routed in declared:
            continue
        out.append(
            {
                "type": "machine-learning-model",
                "bom-ref": f"model/{routed}",
                "name": routed,
                "properties": [
                    {"name": "aegis:kind", "value": "model"},
                    {"name": "aegis:model:role", "value": role},
                    {"name": "aegis:state", "value": "undeclared"},
                    {"name": "aegis:routing:inForce", "value": "true"},
                    {
                        "name": "aegis:note",
                        "value": (
                            "routed to by configuration but absent from the platform's "
                            "fleet declaration; no pricing entry exists for it"
                        ),
                    },
                ],
            }
        )
    return out


def _rail_components() -> list[dict[str, Any]]:
    """The guard stages that screen this agent's traffic."""
    from aegis.core.types import GuardStage

    return [
        {
            "type": "application",
            "bom-ref": f"rail/{stage.value}",
            "name": f"guardrail:{stage.value}",
            "properties": [
                {"name": "aegis:kind", "value": "guardrail"},
                {"name": "aegis:stage", "value": stage.value},
            ],
        }
        for stage in GuardStage
    ]


def _knowledge_components() -> list[dict[str, Any]]:
    """What the agent can read from.

    The module docstring promised this section and the document did not contain it —
    a reader was told the inventory covered knowledge sources and could grep the served
    JSON to find it did not.

    ``data`` is a real CycloneDX 1.6 component type, so no divergence is needed.

    These are the collections this deployment is **configured** to read, named from the
    same constants the retrieval code uses. Deliberately not a live count: querying the
    vector store here would make the inventory endpoint fail whenever Qdrant is down,
    and it would make the document non-deterministic all over again — which is the
    defect the model section was just repaired for. A configured source is a fact about
    this deployment; a point count is a fact about this minute.
    """
    from aegis.retrieval.chunk_index import DEFAULT_CHUNK_COLLECTION
    from aegis.retrieval.graph_index import (
        DEFAULT_ENTITY_COLLECTION,
        DEFAULT_RELATION_COLLECTION,
    )

    sources = (
        (DEFAULT_CHUNK_COLLECTION, "document chunks retrieved by vector search"),
        (DEFAULT_ENTITY_COLLECTION, "graph entities for multi-hop traversal"),
        (DEFAULT_RELATION_COLLECTION, "graph relations for multi-hop traversal"),
    )
    return [
        {
            "type": "data",
            "bom-ref": f"knowledge/{name}",
            "name": name,
            "description": what,
            "properties": [
                {"name": "aegis:kind", "value": "knowledge"},
                {"name": "aegis:store", "value": "qdrant"},
                # Configured, not verified reachable — the distinction this module's
                # docstring commits to everywhere else.
                {"name": "aegis:state", "value": "configured"},
                {
                    "name": "aegis:tenantScoped",
                    "value": "true",
                    },
            ],
        }
        for name, what in sources
    ]


def build_agbom() -> dict[str, Any]:
    """Assemble the AgBOM for this deployment.

    Returns:
        A CycloneDX 1.6 document describing the agent as a ``service`` and its tools,
        models and rails as components, with a ``dependencies`` edge from the agent to
        each one. Plain dicts throughout — the endpoint serialises it unchanged.
    """
    from app.capabilities import PRODUCT_NAME, PRODUCT_VERSION

    tools = _tool_components()
    models = _model_components()
    rails = _rail_components()
    knowledge = _knowledge_components()
    components = tools + models + rails + knowledge
    agent_ref = f"service/{PRODUCT_NAME.lower()}"

    # A `serialNumber` identifies this document; `version` counts revisions of it. The
    # litellm story this module opens with is about DIFFING two inventories, and a
    # document with neither field is one a tool cannot line up against its predecessor.
    #
    # Derived from the content rather than randomly generated, and that is the point:
    # two builds of an unchanged deployment produce the SAME serial, so a changed serial
    # means the agent actually changed. A random uuid4 would make every poll look like a
    # new release.
    digest = hashlib.sha256(
        json.dumps(components, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    serial = f"urn:uuid:{digest[:8]}-{digest[8:12]}-{digest[12:16]}-{digest[16:20]}-{digest[20:32]}"

    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": datetime.now(UTC).isoformat(),
            "component": {
                "type": "application",
                "bom-ref": agent_ref,
                "name": PRODUCT_NAME,
                "version": PRODUCT_VERSION,
                "properties": [
                    {"name": "aegis:kind", "value": "agent"},
                    {"name": "aegis:a2aCardUrl", "value": "/.well-known/agent-card.json"},
                    {"name": "aegis:toolCount", "value": str(len(tools))},
                    {"name": "aegis:railCount", "value": str(len(rails))},
                ],
            },
        },
        "components": components,
        "dependencies": [
            {"ref": agent_ref, "dependsOn": [c["bom-ref"] for c in components]},
            *({"ref": c["bom-ref"], "dependsOn": []} for c in components),
        ],
    }
