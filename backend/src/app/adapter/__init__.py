"""Adapter registry — the single clean surface the core imports.

The core (agent, retrieval, ml, memory, guardrails, api) depends on the domain
**only** through the names re-exported here. Everything domain-specific lives in the
**ten** sibling pieces — eight modules plus ``corpus/`` and ``skills/``, mapped in
``README.md`` and retargeted by the root ``SKILL.md`` (the `retarget-aegis` skill,
the one authoritative procedure); this file is not one of them, it is the interface
contract. Swapping the domain means editing those pieces and keeping these exports
stable.

Exposed surface (the domain seam described in ``docs/learn/50-run-and-extend.md``):

* **schema** (piece 1) — the :mod:`app.adapter.schema` module + key record types.
* **ml_spec** (piece 2) — :data:`FEATURES`, :data:`TARGET`, :func:`features_for_request`,
  :func:`feature_matrix`, :func:`latent_resolution_hours`, :func:`describe_prediction`,
  :func:`training_frame`.
* **generation** (piece 3) — :func:`generate_synthetic`, :func:`generate_synthetic_sync`,
  :class:`GeneratorConfig`, :class:`SyntheticDataset`, and the client-facing demand
  series ``/forecast`` reads: :data:`DOMAIN_SERIES_LABEL`, :data:`DOMAIN_SERIES_UNIT`,
  :func:`domain_series_events`.
* **tool registry** (piece 4) — :data:`TOOL_REGISTRY`, :data:`ALLOWLIST`, :func:`run_tool`,
  :func:`tools_for`, :func:`tool_definitions_for`, :func:`is_allowed`,
  :class:`ToolContext`, :class:`InMemoryRecordStore`.
* **personas** (piece 5) — :data:`PERSONAS`, :data:`DEFAULT_PERSONA_ID`,
  :data:`PERSONA_BY_ROLE`, :func:`get_persona`, :func:`persona_for_role`,
  :class:`Persona`.
* **prompts** (piece 6) — :data:`SYSTEM_PROMPTS`, :func:`render_system_prompt`,
  :data:`PLATFORM_FLOOR` / :func:`render_platform_floor` (the half no tenant may edit).
* **memory_spec** (piece 7) — the :mod:`app.adapter.memory_spec` **module** itself, not
  its individual names: :mod:`app.memory` installs it as the process-wide default spec
  (``set_default_spec(app.adapter.memory_spec)``), so the module object is the contract.
  It is imported here rather than only by its consumers, because a submodule is an
  attribute of its package only once something imports it — and until it was, this
  package satisfied nine of the ten pieces of :class:`aegis.adapter.DomainAdapter` with
  the tenth sitting on disk, unreachable.
* **roster** (piece 8) — :func:`agent_roster`, :class:`AgentRoster`,
  :class:`RosterSpecialist`, and the fan-out team :func:`sub_agent_roster`.
* **corpus** (piece 9) — :func:`load_seed_corpus`.
* **skills/** (piece 10) — the one piece with no name here: Markdown playbooks
  discovered from ``memory_spec.SKILLS_DIR`` at call time, never imported.

The whole set is executable as :class:`aegis.adapter.DomainAdapter`::

    from aegis.adapter import DomainAdapter, missing_members
    import app.adapter

    assert not missing_members(app.adapter)      # every piece present
    assert isinstance(app.adapter, DomainAdapter)

Current domain: a neutral service-request / case-management world. It is
illustrative only — see :data:`DOMAIN_ID` / :data:`DOMAIN_DESCRIPTION`.
"""

from __future__ import annotations

from app.adapter import memory_spec, ml_spec, schema
from app.adapter.corpus import load_seed_corpus
from app.adapter.generator import (
    DOMAIN_SERIES_LABEL,
    DOMAIN_SERIES_UNIT,
    GeneratorConfig,
    domain_series_events,
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
    PERSONA_BY_ROLE,
    PERSONAS,
    Persona,
    get_persona,
    persona_for_role,
)
from app.adapter.prompts import (
    PLATFORM_FLOOR,
    SYSTEM_PROMPTS,
    render_platform_floor,
    render_system_prompt,
)
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
    "DOMAIN_SERIES_LABEL",
    "DOMAIN_SERIES_UNIT",
    "FEATURES",
    "FEATURE_NAMES",
    "PERSONAS",
    "PERSONA_BY_ROLE",
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
    "domain_series_events",
    "feature_matrix",
    "features_for_request",
    "generate_synthetic",
    "generate_synthetic_sync",
    "get_persona",
    "is_allowed",
    "latent_resolution_hours",
    "load_seed_corpus",
    "memory_spec",
    "ml_spec",
    "persona_for_role",
    "render_platform_floor",
    "render_system_prompt",
    "run_tool",
    "schema",
    "sub_agent_roster",
    "tool_definitions_for",
    "training_frame",
    "tools_for",
    "PLATFORM_FLOOR",
    "SYSTEM_PROMPTS",
]
