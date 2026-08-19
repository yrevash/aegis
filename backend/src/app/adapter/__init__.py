"""Adapter registry — the single clean surface the core imports.

The core (agent, retrieval, ml, memory, guardrails, api) depends on the domain
**only** through the names re-exported here. Everything domain-specific lives in the
**ten** sibling pieces — eight modules plus ``corpus/`` and ``skills/``, listed in
``README.md`` and ``SWAP.md``; this file is not one of them, it is the interface
contract. Swapping the domain means editing those pieces and keeping these exports
stable.

Exposed surface (the domain seam described in ``docs/learn/50-run-and-extend.md``):

* **schema** (piece 1) — the :mod:`app.adapter.schema` module + key record types.
* **ml_spec** (piece 2) — :data:`FEATURES`, :data:`TARGET`, :func:`features_for_request`,
  :func:`feature_matrix`, :func:`latent_resolution_hours`, :func:`describe_prediction`,
  :func:`training_frame`.
* **generation** (piece 3) — :func:`generate_synthetic`, :func:`generate_synthetic_sync`,
  :class:`GeneratorConfig`, :class:`SyntheticDataset`.
* **tool registry** (piece 4) — :data:`TOOL_REGISTRY`, :data:`ALLOWLIST`, :func:`run_tool`,
  :func:`tools_for`, :func:`tool_definitions_for`, :func:`is_allowed`,
  :class:`ToolContext`, :class:`InMemoryRecordStore`.
* **personas** (piece 5) — :data:`PERSONAS`, :data:`DEFAULT_PERSONA_ID`,
  :func:`get_persona`, :class:`Persona`.
* **prompts** (piece 6) — :data:`SYSTEM_PROMPTS`, :func:`render_system_prompt`.
* **roster** (piece 8) — :func:`agent_roster`, :class:`AgentRoster`,
  :class:`RosterSpecialist`, and the fan-out team :func:`sub_agent_roster`.
* **corpus** (piece 9) — :func:`load_seed_corpus`.

Two pieces are deliberately *not* re-exported here, because their consumer binds to
the module itself rather than to individual names:

* **memory_spec** (piece 7) — :mod:`app.memory` installs it as the process-wide
  default spec (``set_default_spec(app.adapter.memory_spec)``), so the module object
  is the contract.
* **skills/** (piece 10) — Markdown playbooks discovered from
  ``memory_spec.SKILLS_DIR`` at call time, never imported.

Current domain: a neutral service-request / case-management world. It is
illustrative only — see :data:`DOMAIN_ID` / :data:`DOMAIN_DESCRIPTION`.
"""

from __future__ import annotations

from app.adapter import ml_spec, schema
from app.adapter.corpus import load_seed_corpus
from app.adapter.generator import (
    GeneratorConfig,
    generate_synthetic,
    generate_synthetic_sync,
)
from app.adapter.ml_spec import (
    FEATURE_NAMES,
    FEATURES,
    TARGET,
    describe_prediction,
    feature_matrix,
    features_for_request,
    latent_resolution_hours,
    training_frame,
)
from app.adapter.personas import (
    DEFAULT_PERSONA_ID,
    PERSONAS,
    Persona,
    get_persona,
)
from app.adapter.prompts import SYSTEM_PROMPTS, render_system_prompt
from app.adapter.roster import (
    AgentRoster,
    RosterSpecialist,
    agent_roster,
    sub_agent_roster,
)
from app.adapter.schema import (
    Customer,
    Document,
    ServiceRequest,
    SupportAgent,
    SyntheticDataset,
)
from app.adapter.tools import (
    ALLOWLIST,
    TOOL_REGISTRY,
    InMemoryRecordStore,
    ToolContext,
    is_allowed,
    run_tool,
    tool_definitions_for,
    tools_for,
)

DOMAIN_ID = "service_request_management"
"""Machine id of the currently-loaded example domain."""

DOMAIN_DESCRIPTION = (
    "Illustrative service-request / case-management domain: customers raise "
    "requests, agents resolve them, and a KB corpus backs retrieval. Swap the "
    "ten adapter pieces — eight modules plus corpus/ and skills/ — to retarget "
    "the platform to the real problem."
)

__all__ = [
    "ALLOWLIST",
    "DEFAULT_PERSONA_ID",
    "DOMAIN_DESCRIPTION",
    "DOMAIN_ID",
    "FEATURES",
    "FEATURE_NAMES",
    "PERSONAS",
    "TARGET",
    "TOOL_REGISTRY",
    "AgentRoster",
    "Customer",
    "Document",
    "GeneratorConfig",
    "InMemoryRecordStore",
    "Persona",
    "RosterSpecialist",
    "ServiceRequest",
    "SupportAgent",
    "SyntheticDataset",
    "ToolContext",
    "agent_roster",
    "describe_prediction",
    "feature_matrix",
    "features_for_request",
    "generate_synthetic",
    "generate_synthetic_sync",
    "get_persona",
    "is_allowed",
    "latent_resolution_hours",
    "load_seed_corpus",
    "ml_spec",
    "render_system_prompt",
    "run_tool",
    "schema",
    "sub_agent_roster",
    "tool_definitions_for",
    "training_frame",
    "tools_for",
    "SYSTEM_PROMPTS",
]
