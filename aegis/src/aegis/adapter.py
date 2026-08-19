"""The domain seam as **one** executable contract: :class:`DomainAdapter`.

Retargeting Aegis to a new problem means writing one thing — a domain adapter — and
until this module existed there was no way to say what that thing *is*. The pieces were
discoverable only by reading the reference adapter's directory listing, and the prose
disagreed with itself about how many there were (a README saying five, a checklist with
six steps, a module docstring calling itself "piece 6 of 6", and ten pieces on disk).
Phase 3 fixes those sentences; this module fixes the **structure**, so the question
"have I implemented the adapter?" is answered by a type checker and by one ``isinstance``
call instead of by counting files.

**Ten pieces, nine of them members.** The reference adapter is eight Python modules plus
two data directories. This Protocol carries eleven attributes: the nine below, plus
``DOMAIN_ID`` and ``DOMAIN_DESCRIPTION``.

======  ==========================  =========================================================
Piece   Member                      What the platform reaches through it
======  ==========================  =========================================================
1       ``schema``                  The domain's record types and their version.
2       ``ml_spec``                 Features, target and the training frame for the ML spine.
3       ``generator``               Synthetic-world generation (demo + ML training data).
4       ``tools``                   The action-tool registry, its risk tiers and allowlist.
5       ``personas``                Who is asking, and the data each may see.
6       ``prompts``                 The persona system prompt, and the platform floor.
7       ``memory_spec``             What counts as a durable fact — and ``SKILLS_DIR``.
8       ``roster``                  The specialists routed between, and the fan-out team.
9       ``corpus``                  The seed corpus loader (``corpus/``, a data directory).
10      *(no member)*               ``skills/`` — the directory named by ``SKILLS_DIR``.
======  ==========================  =========================================================

``skills/`` is the one piece with no member of its own, deliberately: it is a directory
of Markdown playbooks discovered at call time, and it is already named by member 7's
``SKILLS_DIR``. Giving it a second, top-level spelling would create exactly the kind of
"is it five or six?" ambiguity this module exists to end.

**How to satisfy it.** An adapter is normally a *package*, and a package satisfies this
structurally — no inheritance, no registration, no base class::

    import myapp.adapter
    from aegis.adapter import DomainAdapter

    assert isinstance(myapp.adapter, DomainAdapter)      # every member present
    def wire(adapter: DomainAdapter) -> None: ...        # mypy checks every signature

The two checks are complementary and both are needed. ``isinstance`` is
:func:`~typing.runtime_checkable`, which means it verifies that every member **exists**
— it does not look at a single signature. A type checker is what catches the member that
exists with the wrong shape. An adapter that passes only the first is exactly the
"working-looking but wrong" state this phase exists to remove.

**Members are attributes of the adapter package, so they must be imported.** A submodule
becomes an attribute of its parent package only once something imports it; an adapter
whose ``__init__.py`` never touches ``memory_spec`` does not have a ``memory_spec``
member, however present the file is on disk. This bit the reference adapter itself.

Nothing here is domain-specific, and nothing here imports a heavy dependency: the
sub-Protocols describe only what the platform actually reads, so a domain is free to
carry any amount of structure the platform never sees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover - typing-only imports, never a runtime dependency
    from collections.abc import Mapping, Sequence

    import pandas as pd

    from aegis.core.types import RiskLevel

__all__ = [
    "AgentRosterLike",
    "CorpusModule",
    "adapter_members",
    "missing_members",
    "DomainAdapter",
    "GeneratorModule",
    "MLSpecModule",
    "MemorySpecModule",
    "PersonasModule",
    "PromptsModule",
    "RosterModule",
    "SchemaModule",
    "ToolSpecLike",
    "ToolsModule",
]


# ─────────────────────────────────────────────────────────── piece 1: schema ──
@runtime_checkable
class SchemaModule(Protocol):
    """Piece 1 — the domain's record types, and the version they are stamped with.

    The platform passes domain records around **opaquely**: it never introspects a
    field, which is why only the version is a member here. ``SCHEMA_VERSION`` is not
    decoration — it is written onto generated datasets, so a corpus or a trained model
    can be told apart from one produced by a different shape of the same domain.

    Consumers: :mod:`aegis.ml` (through ``ml_spec``), the synthetic generator (piece 3),
    and any host route that serialises a domain record.
    """

    SCHEMA_VERSION: str


# ────────────────────────────────────────────────────────── piece 2: ml_spec ──
@runtime_checkable
class MLSpecModule(Protocol):
    """Piece 2 — the supervised problem: which columns, which target, which frame.

    This is the *module* spelling of :class:`aegis.ml.spec.MLSpec`.
    :func:`aegis.ml.spec.resolve_spec` reads either — ``FEATURE_NAMES``/``TARGET.name``
    (the adapter-module spelling described here) or lowercase ``features``/``target``
    (the object spelling) — and falls back to generic noise when it finds neither. The
    fallback is why this member is worth a Protocol: a misspelled attribute does not
    raise, it silently trains the spine on synthetic noise and serves the result as
    domain evidence.

    Consumers: :func:`aegis.ml.spec.resolve_spec`, the ML spine's train/serve paths, and
    ``describe_prediction`` on the agent's reasoning path.

    Attributes:
        FEATURES: The ordered feature specs; each carries ``.name`` and ``.dtype``,
            from which the categorical subset is derived when
            ``CATEGORICAL_FEATURES`` is absent.
        FEATURE_NAMES: The ordered feature-column names (``[f.name for f in FEATURES]``).
        TARGET: The target spec; ``.name`` is the predicted column and ``.task`` the
            supervised task (``"regression"`` | ``"classification"``).
    """

    FEATURES: list[Any]
    FEATURE_NAMES: list[str]
    TARGET: Any

    def training_frame(self, *, num_records: int = ..., seed: int = ...) -> pd.DataFrame:
        """Return the labelled training frame: one column per feature, plus the target."""
        ...

    def describe_prediction(
        self,
        resp: Any,  # noqa: ANN401 - the host's ML explain response (host type)
        *,
        top_k: int = 3,
    ) -> str:
        """Render one prediction as the domain's own decision-support sentence."""
        ...


# ──────────────────────────────────────────────────────── piece 3: generator ──
@runtime_checkable
class GeneratorModule(Protocol):
    """Piece 3 — a synthetic world: the demo's data, and the ML spine's training set.

    Both entry points must produce the **same** structure and the same labels; the sync
    one exists because it is called from inside a running event loop (where
    ``asyncio.run`` raises) and from offline training, and it must therefore need no
    model call.

    It also names the **client-facing demand series** ``/forecast`` charts, because the
    series is the domain's records counted over time and its axis label is a sentence a
    client reads. Both used to live in the host's forecast module, which reached into
    the shipped domain's record collection by attribute name and owned its chart title
    as a constant — so a retarget got an ``AttributeError`` from one and, from the
    other, the shipped domain's sentence printed over its own data forever.

    Consumers: the host's seed/demo routes, ``ml_spec.training_frame``, and the host's
    ``/forecast`` domain series.

    Attributes:
        DOMAIN_SERIES_LABEL: What the demand series measures, in the client's language.
        DOMAIN_SERIES_UNIT: The unit of the series' values (the chart's y-axis).
    """

    DOMAIN_SERIES_LABEL: str
    DOMAIN_SERIES_UNIT: str

    def domain_series_events(
        self, *, num_records: int = ..., seed: int = ...
    ) -> Sequence[tuple[Any, float]]:
        """Return ``(timestamp, value)`` events for the client-facing demand series."""
        ...

    def generate_synthetic_sync(
        self,
        config: Any | None = None,  # noqa: ANN401 - the domain's own config model
    ) -> Any:  # noqa: ANN401 - the domain's own dataset type
        """Fabricate a schema-valid synthetic dataset with no LLM and no ``await``."""
        ...

    async def generate_synthetic(
        self,
        config: Any | None = None,  # noqa: ANN401 - the domain's own config model
    ) -> Any:  # noqa: ANN401 - the domain's own dataset type
        """Fabricate a synthetic dataset, optionally with LLM-written record text."""
        ...


# ──────────────────────────────────────────────────────────── piece 4: tools ──
@runtime_checkable
class ToolSpecLike(Protocol):
    """Structural view of one registered action tool.

    ``risk`` is the load-bearing field: it is the **only** signal that decides whether a
    proposed action routes to the human approval gate (compared against
    ``AgentConfig.gate_min_risk``). A tool registered without one is not a mild
    annotation gap — it is an ungated action.

    Two optional booleans are read when present and are not members here because a
    domain may legitimately declare neither: ``destructive`` and ``idempotent``, the
    MCP hints a client acts on. They belong on the tool because risk does not imply
    idempotency, and the host's MCP surface kept them in a table keyed by the shipped
    domain's tool names until a retarget renamed every key and silently lost them.
    """

    @property
    def name(self) -> str:
        """The tool's registered name (the name the model calls)."""
        ...

    @property
    def description(self) -> str:
        """What the tool does, as the model reads it."""
        ...

    @property
    def risk(self) -> RiskLevel:
        """The declared risk tier — the only input to the human-gate decision."""
        ...

    def definition(self) -> dict[str, Any]:
        """Return the OpenAI/MCP ``{"type": "function", ...}`` schema for this tool."""
        ...


@runtime_checkable
class ToolsModule(Protocol):
    """Piece 4 — what the agent can *do*, at what risk, and who may ask for it.

    Consumers: ``AgentDeps.tool_definitions_for`` / ``.tool_risk`` / ``.run_tool``
    (:mod:`aegis.agent`), the host's MCP server, and the persona prompt's tool clause.

    Attributes:
        TOOL_REGISTRY: Tool name → its spec. The registry is the whole vocabulary; a
            name absent from it is treated as HIGH risk by the platform, so a
            hallucinated tool can never slip under the gate.
        ALLOWLIST: Persona id → the tool names that persona may call. Checked
            **before** any side effect.
    """

    @property
    def TOOL_REGISTRY(self) -> Mapping[str, ToolSpecLike]:  # noqa: N802 - a module CONSTANT
        """Tool name → its spec."""
        ...

    @property
    def ALLOWLIST(self) -> Mapping[str, frozenset[str]]:  # noqa: N802 - a module CONSTANT
        """Persona id → the tool names that persona may call."""
        ...

    def is_allowed(self, persona_id: str, tool_name: str) -> bool:
        """Return whether ``persona_id`` is authorised to call ``tool_name``."""
        ...

    def tools_for(self, persona_id: str) -> Sequence[ToolSpecLike]:
        """Return the allowlist-filtered tool specs for a persona."""
        ...

    def tool_definitions_for(self, persona_id: str) -> list[dict[str, Any]]:
        """Return the ``tools=`` payload offered to the model for a persona."""
        ...

    async def run_tool(
        self,
        persona_id: str,
        tool_name: str,
        args: dict[str, Any],
        ctx: Any,  # noqa: ANN401 - the domain's own tool-execution context
    ) -> Any:  # noqa: ANN401 - structural ToolOutcome (ok/summary); see aegis.agent
        """Authorise and execute one tool, returning an object with ``ok``/``summary``."""
        ...


# ───────────────────────────────────────────────────────── piece 5: personas ──
@runtime_checkable
class PersonasModule(Protocol):
    """Piece 5 — who is asking, and what they are allowed to see.

    A persona is not a UI label: its ``data_scope`` becomes a retrieval filter and its
    entry in the allowlist becomes the tool set, so this piece is where a domain says
    what "the client" may read and do.

    Consumers: the host's query routes, ``prompts.render_platform_floor``, and the
    retrieval scope resolver.

    It also owns the **role → persona** mapping. RBAC roles belong to the platform;
    *which persona a role adopts* is a domain statement about who is served, and while
    the host decided it — with two persona ids written into an ``if`` in its login path
    — re-voicing ``PERSONAS`` (step 5 of the documented retargeting procedure) made
    every authenticated request resolve to a persona that no longer existed, and every
    sign-in raised ``KeyError``. No test in the repository went through that path, so
    nothing went red.

    Attributes:
        PERSONAS: Persona id → persona object (``.id``, ``.data_scope``, ``.prompt_key``).
        DEFAULT_PERSONA_ID: The persona a request that names none resolves to.
        PERSONA_BY_ROLE: Coarse RBAC role (:class:`aegis.governance.types.Role`, or its
            string value) → persona id. Every role must appear; every value must be a
            key of ``PERSONAS``. Both are checked by ``aegis.conformance``.
    """

    DEFAULT_PERSONA_ID: str

    @property
    def PERSONAS(self) -> Mapping[str, Any]:  # noqa: N802 - a module CONSTANT
        """Persona id → the persona object."""
        ...

    @property
    def PERSONA_BY_ROLE(self) -> Mapping[Any, str]:  # noqa: N802 - a module CONSTANT
        """Coarse RBAC role → the persona id a principal of that role adopts."""
        ...

    def get_persona(
        self, persona_id: str | None
    ) -> Any:  # noqa: ANN401 - the domain's own persona model
        """Return the persona for ``persona_id`` (the default when ``None``)."""
        ...

    def persona_for_role(
        self,
        role: Any,  # noqa: ANN401 - the host's RBAC role enum or its string value
    ) -> str:
        """Return the persona id a principal holding ``role`` adopts."""
        ...


# ────────────────────────────────────────────────────────── piece 6: prompts ──
@runtime_checkable
class PromptsModule(Protocol):
    """Piece 6 — the system prompt, split into the half a tenant may edit and the half it may not.

    ``render_system_prompt`` is the task half; ``render_platform_floor`` is the boundary
    half — the preamble plus the persona's live data scope and tool allowlist, derived
    from the enforcement tables rather than written by hand. An LLM-Ops prompt version
    replaces the first and is composed *over* the second, never instead of it.

    Consumers: ``AgentDeps.render_system_prompt`` (:mod:`aegis.agent`) and the LLM-Ops
    prompt registry.
    """

    def render_system_prompt(
        self,
        persona: Any,  # noqa: ANN401 - the domain's own persona model
        *,
        extra_context: str | None = None,
    ) -> str:
        """Render the persona's full system prompt, including the platform floor."""
        ...

    def render_platform_floor(
        self,
        persona: Any | None,  # noqa: ANN401 - the domain's own persona model
    ) -> str:
        """Render only the half no tenant prompt version may replace."""
        ...


# ────────────────────────────────────────────────────── piece 7: memory_spec ──
@runtime_checkable
class MemorySpecModule(Protocol):
    """Piece 7 — what counts as a durable fact, plus the ``skills/`` directory (piece 10).

    Structurally this is :class:`aegis.memory.spec.MemorySpec` — the module a host
    installs with ``aegis.memory.set_default_spec(...)`` — with one addition the memory
    package itself does not need but every host does: :func:`memory_subject_for`, which
    decides **whose** memory a turn reads and writes. That function is the domain's
    answer to "is memory per end-user, per account, or per case?", and getting it wrong
    is a cross-subject data leak rather than a quality regression.

    Consumers: :mod:`aegis.memory`'s recall/consolidate paths (through the installed
    default spec), the host's memory routes, and the skills loader.

    Attributes:
        FACT_TYPES: The typed kinds of durable fact this domain distils.
        PROFILE_FIELDS: Ordered structured-profile fields, always injected.
        FACT_EXTRACTION_PROMPT: The system prompt driving the cheap-model extractor.
        IMPORTANCE_HINTS: Domain guidance for the 1..10 importance rating.
        SKILLS_DIR: Directory of procedural skill Markdown files — **piece 10**.
        FactSchema: Pydantic model for one extracted fact.
        FactExtraction: Pydantic container with a ``facts`` list.
    """

    FACT_TYPES: list[str]
    PROFILE_FIELDS: list[str]
    FACT_EXTRACTION_PROMPT: str
    IMPORTANCE_HINTS: str
    SKILLS_DIR: str
    FactSchema: type[Any]
    FactExtraction: type[Any]

    def memory_subject_for(
        self, user_id: str | int | None, persona_id: str | None = None
    ) -> str | None:
        """Return the memory subject id for a principal, or ``None`` for no memory."""
        ...

    def render_profile(self, profile: dict[str, Any]) -> str:
        """Render the structured profile as the prompt's human block."""
        ...

    def select_skills(
        self, query: str, persona_id: str | None, available: list[str]
    ) -> list[str] | None:
        """Select the procedural skills a query needs (a subset of ``available``)."""
        ...


# ─────────────────────────────────────────────────────────── piece 8: roster ──
@runtime_checkable
class AgentRosterLike(Protocol):
    """Structural view of the supervisor's roster (the read shape the router uses).

    ``default_role`` is the load-bearing member: it is where a turn goes when no
    specialist matched, so a roster without one is a turn with nowhere to land.
    """

    @property
    def specialists(self) -> Sequence[Any]:
        """Every specialist, in declaration order (each carries ``role``/``description``)."""
        ...

    @property
    def default_role(self) -> str:
        """The fall-through role id when no specialist matched."""
        ...

    def roles(self) -> list[str]:
        """Every routable role id, in declaration order."""
        ...

    def named(self) -> list[Any]:
        """The keyword-matchable specialists the classifier may choose between."""
        ...


@runtime_checkable
class RosterModule(Protocol):
    """Piece 8 — which specialists a turn may be handed to, and which team it may fan out across.

    Two rosters, two mechanisms, and they are not interchangeable. ``agent_roster`` is
    the **supervisor's** hand-off set: every role it names must have a handler node in
    the graph, or the turn routes to a specialist that cannot answer.
    ``sub_agent_roster`` is the **fan-out team**: it is deliberately empty-able, and an
    absent one means every turn runs single-lane rather than fanning out to agents the
    domain never declared.

    Consumers: ``AgentDeps.agent_roster`` and ``AgentDeps.subagent_roster``
    (:mod:`aegis.agent`).
    """

    def agent_roster(self) -> AgentRosterLike:
        """Return the specialists the supervisor may route a turn to."""
        ...

    def sub_agent_roster(self) -> Sequence[Any]:
        """Return the ``SubAgentSpec`` team a wide turn fans out across (may be empty)."""
        ...


# ─────────────────────────────────────────────────────────── piece 9: corpus ──
@runtime_checkable
class CorpusModule(Protocol):
    """Piece 9 — the seed knowledge the platform retrieves over before anything is ingested.

    A domain with no corpus is legal (the loader may return an empty list) and honest:
    retrieval then returns no candidates instead of reaching for someone else's
    documents. What is not legal is *absent* — a missing loader is the difference
    between "this domain ships no seed knowledge" and "the seed knowledge silently
    failed to load".

    Consumers: the host's retrieval ingest path and the lite/offline backend.
    """

    def load_seed_corpus(self) -> list[Any]:
        """Load the seed documents as typed domain records (possibly empty)."""
        ...


# ───────────────────────────────────────────────────────── the whole contract ──
@runtime_checkable
class DomainAdapter(Protocol):
    """**The** domain contract: one object, eleven members, and nothing else to find.

    Nine of the eleven are the pieces (see the module docstring's table); the other two
    are the domain's identity.

    A host retargets Aegis by writing an object — in practice a package — that satisfies
    this. Every member is a piece the platform genuinely reads; there is no optional
    member, because "optional" is how ``roster`` and ``skills/`` came to be absent from
    every checklist in the repo while being required by the running code.

    Identity first: :attr:`DOMAIN_ID` and :attr:`DOMAIN_DESCRIPTION` are not metadata.
    The description is wired straight into the guardrails' topical rail as the set of
    allowed topics, so it is a *control input* — a vague description is a loose rail.

    Example — the conformance check an integrator runs before anything else::

        import myapp.adapter
        from aegis.adapter import missing_members

        assert not missing_members(myapp.adapter)

    Attributes:
        DOMAIN_ID: Stable machine id of the loaded domain.
        DOMAIN_DESCRIPTION: One-paragraph description of what this domain is about —
            and the guardrails' ``allowed_topics``.
    """

    DOMAIN_ID: str
    DOMAIN_DESCRIPTION: str

    # Read-only on purpose: the platform *reads* every piece and assigns to none, and a
    # mutable protocol member is invariant — which would reject a perfectly good adapter
    # for declaring ``TOOL_REGISTRY: dict[...]`` instead of ``Mapping[...]``. A contract
    # that rejects correct implementations trains people to ignore it.
    @property
    def schema(self) -> SchemaModule:
        """Piece 1 — the domain's record types."""
        ...

    @property
    def ml_spec(self) -> MLSpecModule:
        """Piece 2 — the supervised problem."""
        ...

    @property
    def generator(self) -> GeneratorModule:
        """Piece 3 — the synthetic world."""
        ...

    @property
    def tools(self) -> ToolsModule:
        """Piece 4 — the action tools and their risk tiers."""
        ...

    @property
    def personas(self) -> PersonasModule:
        """Piece 5 — who is asking, and what they may see."""
        ...

    @property
    def prompts(self) -> PromptsModule:
        """Piece 6 — the system prompt and the platform floor."""
        ...

    @property
    def memory_spec(self) -> MemorySpecModule:
        """Piece 7 — the memory contract, and ``SKILLS_DIR`` (piece 10)."""
        ...

    @property
    def roster(self) -> RosterModule:
        """Piece 8 — the specialists and the fan-out team."""
        ...

    @property
    def corpus(self) -> CorpusModule:
        """Piece 9 — the seed corpus loader."""
        ...


def adapter_members() -> tuple[str, ...]:
    """Return every member name :class:`DomainAdapter` requires, sorted.

    Read off the Protocol itself rather than written out a second time, so a member
    added above cannot go missing from the check that enforces it. Python 3.12 exposes
    the set as ``__protocol_attrs__``; on 3.11 it is derived from the class body, which
    is equivalent here and uses no private typing API.
    """
    attrs = getattr(DomainAdapter, "__protocol_attrs__", None)
    if attrs is None:
        # Every member of this Protocol is public, and every name Protocol itself adds
        # to the class body is dunder or ``_is_protocol``-style — so "public names in
        # the annotations or the class dict" is exactly the member set, whether a member
        # is spelled as an annotation, a method, or a read-only property.
        attrs = {
            name
            for name in (*DomainAdapter.__annotations__, *vars(DomainAdapter))
            if not name.startswith("_")
        }
    return tuple(sorted(attrs))


def missing_members(candidate: object) -> list[str]:
    """Return the :class:`DomainAdapter` members ``candidate`` does not have.

    The presence half of conformance, usable before a type checker is involved and from
    a test. An empty list is necessary, not sufficient: it says every member exists, not
    that any of them has the right shape — that is what annotating a parameter
    ``DomainAdapter`` and running a type checker is for.

    Args:
        candidate: The adapter object (normally an imported package module).

    Returns:
        The missing member names, sorted; empty when every member is present.
    """
    return [name for name in adapter_members() if not hasattr(candidate, name)]
