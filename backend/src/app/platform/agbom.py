"""The Agent Bill of Materials — what this agent is made of, as one document.

## Why this exists

In March 2026 an actor published backdoored `litellm` releases to PyPI. They were live for
roughly forty minutes and reached on the order of 2,500 organisations. Aegis is clean — its
dependency pins are explicit and a fresh resolve honours them — but "clean" was a fact
nobody could check from outside, because the thing a buyer asks for is an inventory, and
the answer was a `pyproject.toml` and a promise.

Aegis already serves a dependency SBOM. What it did not serve is the *agent's* inventory:
which tools exist and at what risk tier, which model deployments answer which role, which
rails run, what the agent can read from. Those are the components that decide what an agent
can do, and none of them appear in a package manifest.

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
    """The model deployments this platform will REQUEST, by role.

    ``machine-learning-model`` is a real CycloneDX 1.6 component type, so these need no
    divergence.

    **"requested", not "in use", and the distinction is not pedantry.** Measured on this
    deployment: the router asks for ``genailab-maas-gpt-4o`` while the usage ledger
    records every answer as coming from ``DeepSeek-V4-Flash`` — because the router reads
    ``MODEL_<ROLE>`` from the process environment, ``.env`` is loaded into pydantic
    settings rather than into ``os.environ``, and the gateway endpoint answers with
    whatever it actually serves. Both numbers are honest and they are about different
    things.

    An inventory that picked one and called it "the model" would be wrong half the time.
    This lists what will be requested and points at the ledger for what replied, which is
    the only pair of statements that is true.
    """
    from aegis.core.models import ModelRole
    from aegis.gateway.routing import model_for

    # Asked of the router rather than read off settings, so this reports the deployment
    # a call would ACTUALLY reach. Reading the environment would describe the intended
    # fleet; the router describes the live one, and an inventory that disagrees with the
    # running system is worse than none.
    seen: dict[str, list[str]] = {}
    for role in ModelRole:
        deployment = model_for(role)
        if deployment:
            seen.setdefault(str(deployment), []).append(role.value)

    return [
        {
            "type": "machine-learning-model",
            "bom-ref": f"model/{deployment}",
            "name": deployment,
            "properties": [
                {"name": "aegis:kind", "value": "model"},
                {"name": "aegis:roles", "value": ",".join(sorted(roles_for))},
                {"name": "aegis:state", "value": "requested"},
                {
                    "name": "aegis:observedIn",
                    "value": "usage_ledger.model — the deployment that actually replied",
                },
            ],
        }
        for deployment, roles_for in sorted(seen.items())
    ]


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
    components = tools + models + rails
    agent_ref = f"service/{PRODUCT_NAME.lower()}"

    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
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
