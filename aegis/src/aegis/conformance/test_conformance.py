"""Thirteen checks, and every one of them descends from a defect this repository shipped.

Each check's docstring names its scar. The rule is not decoration: a conformance suite
of plausible-sounding checks is a checklist, and a checklist grows forever. A check
earns its place here by pointing at something that has already gone wrong once, silently,
in a real integration — this one.

What every check has in common: the failure it catches produces **no error**. That is
the selection criterion. A wiring mistake that raises will be found in the first minute
by anyone; a wiring mistake that logs a warning and answers as QA will not be found at
all.

Nothing here touches Postgres, Redis, Temporal or a model gateway, and nothing here is
async — an integrator runs this before any of that is configured, which is the point.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import CodeType
from typing import Any

from aegis.conformance._report import fail

Piece = Callable[[str], Any]

#: Attribute (or key) spellings a corpus record may use for its stable identity.
_ID_ATTRS = ("id", "doc_id", "document_id", "uid", "key")
#: Attribute (or key) spellings a corpus record may use for its text.
_TEXT_ATTRS = ("body", "text", "content", "markdown", "raw_text", "chunk_text")


def _get(record: object, names: Sequence[str]) -> tuple[str, Any] | None:
    """Return the first ``(name, value)`` of ``names`` the record carries, if any."""
    for name in names:
        if isinstance(record, Mapping):
            if name in record:
                return name, record[name]
        elif hasattr(record, name):
            return name, getattr(record, name)
    return None


def _protocol_members(protocol: type) -> tuple[str, ...]:
    """Return the member names a ``runtime_checkable`` Protocol requires, sorted."""
    attrs = getattr(protocol, "__protocol_attrs__", None)
    if attrs is None:  # pragma: no cover - Python 3.11 path
        attrs = {
            name
            for name in (*getattr(protocol, "__annotations__", {}), *vars(protocol))
            if not name.startswith("_")
        }
    return tuple(sorted(attrs))


def _string_constants(code: CodeType, *, exclude: object = None) -> set[str]:
    """Return every string constant in ``code`` and its nested code objects.

    Used by the skill-reachability check to see which playbook names a selector can
    actually name. Reading the compiled constants rather than the source works for a
    function whose source is not on disk, and never matches a name that only appears
    in a docstring or a comment.

    Args:
        code: The code object to scan.
        exclude: A string to leave out (the function's own docstring).

    Returns:
        The string constants found.
    """
    found: set[str] = set()
    for const in code.co_consts:
        if isinstance(const, str) and const != exclude:
            found.add(const)
        elif isinstance(const, CodeType):
            found |= _string_constants(const)
        elif isinstance(const, tuple | frozenset):
            found |= {c for c in const if isinstance(c, str) and c != exclude}
    return found


# ─────────────────────────────────────────────────────── 1 · the whole contract ──
def test_every_contract_member_is_present(adapter: object) -> None:
    """Every :class:`aegis.adapter.DomainAdapter` member is reachable on the adapter.

    SCAR: ``missing_members(app.adapter)`` returned ``['memory_spec']`` on this
    repository's own reference adapter. The file was on disk and named in the manifest,
    but nothing imported it, so it was never an attribute of the adapter package — nine
    of ten pieces reachable, and no error anywhere.
    """
    from aegis.adapter import adapter_members, missing_members

    missing = missing_members(adapter)
    if missing:
        fail(
            member="adapter",
            problem=f"{len(missing)} of {len(adapter_members())} contract members are missing",
            what=(
                f"{getattr(adapter, '__name__', adapter)!r} does not expose "
                f"{', '.join(repr(m) for m in missing)}."
            ),
            fix=(
                "Implement each missing piece and import it from the adapter package's "
                "__init__.py. A submodule is an attribute of its package only once "
                "something imports it, so a file on disk that no __init__ touches does "
                "not satisfy the contract. The eleven members are listed in "
                "aegis.adapter, and `from aegis.adapter import missing_members` is this "
                "check in one line."
            ),
            if_not=(
                "The platform reads each piece off the adapter package. A missing one is "
                "not an ImportError at startup — it is a piece of the domain the "
                "platform quietly never consults, so the feature it backs behaves as "
                "though the domain had nothing to say."
            ),
            scar=(
                "missing_members(app.adapter) returned ['memory_spec'] on the reference "
                "adapter the day the Protocol landed — the file was on disk and in the "
                "manifest, and nothing imported it."
            ),
        )


# ────────────────────────────────────────────────────────────── 2 · identity ──
def test_domain_identity_is_a_usable_topical_rail(adapter: object) -> None:
    """``DOMAIN_ID`` and ``DOMAIN_DESCRIPTION`` are declared, and the description is substantive.

    ``DOMAIN_DESCRIPTION`` is not metadata: the host wires it straight into the
    guardrails as ``allowed_topics``, so it is a **control input**. A vague description
    is a loose rail, and an absent one is no rail at all.

    SCAR: the topical rail shipped and never ran, because the composed ``Guardrails``
    was built with no ``allowed_topics`` — ``screen_topic`` returns PASS without calling
    anything when it has nothing to compare against, so the rail's own tests passed
    while the running stack screened no topic at all.
    """
    domain_id = str(getattr(adapter, "DOMAIN_ID", "") or "").strip()
    description = str(getattr(adapter, "DOMAIN_DESCRIPTION", "") or "").strip()
    words = description.split()
    if not domain_id or len(words) < 8:
        fail(
            member="identity",
            problem="the domain does not describe itself well enough to be a guardrail",
            what=(
                f"DOMAIN_ID={domain_id!r}, DOMAIN_DESCRIPTION is {len(words)} words "
                f"(a usable topical rail needs at least 8, and an id is required)."
            ),
            fix=(
                "Set DOMAIN_ID to a stable machine id, and write DOMAIN_DESCRIPTION as a "
                "paragraph that a judge could use to decide whether a question belongs "
                "to this domain — the subject matter, the records involved, and the kind "
                "of question in scope."
            ),
            if_not=(
                "The topical rail is handed this string as its allowed_topics. With "
                "nothing there it does not run at all and every off-topic question is "
                "answered; with two vague words it runs and admits everything."
            ),
            scar=(
                "Four guardrails.* settings keys shipped as controls that bound nothing, "
                "and behind them a topical rail that never ran because the composed "
                "guard had no allowed_topics."
            ),
        )


# ──────────────────────────────────────────────────────────────── 3 · roster ──
def _specialist_nodes() -> dict[str, str]:
    """Return the graph's role → handler-node table, or fail with the install to run."""
    try:
        from aegis.agent.graph import SPECIALIST_NODES
    except ImportError as exc:  # pragma: no cover - depends on the installed extras
        fail(
            member="roster",
            problem="the agent graph could not be imported, so routing cannot be checked",
            what=f"`from aegis.agent.graph import SPECIALIST_NODES` raised: {exc}",
            fix="Install the orchestration extra: pip install 'aegis[agent]'.",
            if_not=(
                "The single highest-value conformance check cannot run, and an "
                "unroutable specialist reaches production undetected."
            ),
            scar="This check exists because an unroutable specialist produces no error.",
        )
    return dict(SPECIALIST_NODES)


def test_every_roster_role_has_a_handler_node(piece: Piece) -> None:
    """Every specialist the roster declares is a role the agent graph can dispatch.

    This is the highest-value check in the suite, because adding a specialist is the
    most natural first thing to do to an adapter and the failure is completely silent.

    SCAR: ``aegis.agent.graph.SPECIALIST_NODES`` maps only ``qa``, ``memory`` and
    ``team``. A roster role outside that set falls back to ``qa`` **with a log warning,
    not an exception** — the new specialist answers as QA, and the ``routing`` stream
    event still names it. The build-time warning meant to catch this could not fire
    either: it iterated ``roster.roles`` (the bound method, not the list), and the
    surrounding ``except Exception`` swallowed the ``TypeError``.
    """
    roster_module = piece("roster")
    roster = roster_module.agent_roster()
    roles = [str(r) for r in roster.roles()]
    nodes = _specialist_nodes()
    unroutable = sorted(set(roles) - nodes.keys())
    if unroutable:
        fail(
            member="roster",
            problem="a declared specialist has no handler node in the graph",
            what=(
                f"agent_roster() declares {', '.join(repr(r) for r in unroutable)}, and "
                f"the graph dispatches only {', '.join(repr(r) for r in sorted(nodes))}."
            ),
            fix=(
                "Either rename the specialist to a role the graph handles, or add a "
                "handler node for it and an entry in aegis.agent.graph.SPECIALIST_NODES "
                "mapping the role to that node. A specialist is a role plus a node; the "
                "roster declares only the first half."
            ),
            if_not=(
                "A turn classified into that role is answered by the 'qa' pipeline. The "
                "only signal is a log warning, the routing event still names your "
                "specialist, and the answer looks entirely normal — so the specialist "
                "you wrote never runs and nothing says so."
            ),
            scar=(
                "SPECIALIST_NODES maps qa, memory and team only, and _route_specialist "
                "falls back to qa with a logger.warning rather than raising. The "
                "build-time warning that was supposed to catch it iterated a bound "
                "method and had its TypeError swallowed, so it could never fire."
            ),
        )


def test_the_roster_default_role_is_declared_and_routable(piece: Piece) -> None:
    """The roster's fall-through role is one of its own specialists, and the graph handles it.

    SCAR: the same ``SPECIALIST_NODES`` seam, one level worse. ``default_role`` is where
    a turn lands when no specialist matched, and ``AgentRoster.default_role`` will hand
    back the first specialist in declaration order when none is marked default — so a
    roster that forgets ``is_default`` silently promotes whichever specialist happens to
    be written first, and if that role has no node, *every unmatched turn* takes the
    ``qa`` fallback path.
    """
    roster = piece("roster").agent_roster()
    roles = [str(r) for r in roster.roles()]
    default = str(roster.default_role)
    nodes = _specialist_nodes()
    if default not in roles or default not in nodes:
        fail(
            member="roster",
            problem="the roster's fall-through role is not a routable declared specialist",
            what=(
                f"default_role is {default!r}; declared roles are "
                f"{', '.join(repr(r) for r in roles) or '(none)'}; routable roles are "
                f"{', '.join(repr(r) for r in sorted(nodes))}."
            ),
            fix=(
                "Mark exactly one declared specialist as the default (is_default=True on "
                "the reference roster's RosterSpecialist), and make sure its role has a "
                "handler node in aegis.agent.graph.SPECIALIST_NODES."
            ),
            if_not=(
                "Every turn that matches no specialist — the majority of them — lands on "
                "a role the graph cannot dispatch and is answered by the 'qa' pipeline "
                "instead, whatever your default was meant to be."
            ),
            scar=(
                "AgentRoster.default_role falls back to the first specialist in "
                "declaration order when none is marked, and _route_specialist falls back "
                "to 'qa' for any role with no node. Neither fallback raises."
            ),
        )


# ───────────────────────────────────────────────────────────────── 4 · tools ──
def test_every_tool_declares_a_risk_tier(piece: Piece) -> None:
    """Every registered tool carries a valid :class:`aegis.core.types.RiskLevel`.

    SCAR: ``risk`` is the **only** input to the human-approval gate — it is compared
    against ``AgentConfig.gate_min_risk`` and nothing else decides whether a proposed
    action stops for a human. A tool registered without one is not a missing annotation,
    it is an ungated action: the registry entry still resolves, the model still calls
    it, and the side effect happens with no approval row anywhere.
    """
    from aegis.core.types import RiskLevel

    tools = piece("tools")
    registry: Mapping[str, Any] = tools.TOOL_REGISTRY
    valid = {level.value for level in RiskLevel}
    offenders = [
        name
        for name, spec in registry.items()
        if str(getattr(spec, "risk", "") or "").lower() not in valid
    ]
    if offenders or not registry:
        fail(
            member="tools",
            problem=(
                "a registered tool has no usable risk tier"
                if registry
                else "the tool registry is empty"
            ),
            what=(
                f"TOOL_REGISTRY entries without a valid RiskLevel: "
                f"{', '.join(repr(n) for n in offenders) or '(the registry is empty)'}. "
                f"Valid tiers: {', '.join(sorted(valid))}."
            ),
            fix=(
                "Give every ToolSpec a risk=RiskLevel.LOW / MEDIUM / HIGH. Pick the tier "
                "by what the tool does to the world, not by how often it is called: HIGH "
                "for anything a human should approve, LOW for a read."
            ),
            if_not=(
                "The gate compares this value with AgentConfig.gate_min_risk and has no "
                "other input. A tool with no tier is executed without ever being offered "
                "to a human, and the run looks exactly like an approved one."
            ),
            scar=(
                "The gate decision is a single comparison against tool risk; an "
                "unregistered tool name is forced to HIGH precisely so a hallucinated "
                "call cannot slip under it — a registered tool with no tier had no such "
                "floor."
            ),
        )


def test_allowlists_name_registered_tools_and_known_personas(piece: Piece) -> None:
    """Every name in ``ALLOWLIST`` and in each sub-agent's allowlist binds to something real.

    SCAR: the allowlist is read with ``.get(persona_id, frozenset())`` and the tool set
    is filtered by membership, so **every** typo here fails open into silence: a
    misspelled persona key gives that persona no tools at all, and a misspelled tool
    name simply never appears in the model's ``tools=`` payload. Neither raises, and the
    agent answers the question anyway — the same shape as the four ``guardrails.*``
    settings keys that shipped as controls bound to nothing.
    """
    tools = piece("tools")
    personas = piece("personas")
    registry = set(tools.TOOL_REGISTRY)
    known = set(personas.PERSONAS)
    problems: list[str] = []
    for persona_id, allowed in tools.ALLOWLIST.items():
        if persona_id not in known:
            problems.append(f"ALLOWLIST key {persona_id!r} is not a declared persona")
        unknown = sorted(set(allowed) - registry)
        if unknown:
            problems.append(
                f"ALLOWLIST[{persona_id!r}] names unregistered tool(s) "
                f"{', '.join(repr(t) for t in unknown)}"
            )
    for spec in piece("roster").sub_agent_roster() or ():
        unknown = sorted(set(getattr(spec, "tool_allowlist", None) or ()) - registry)
        if unknown:
            problems.append(
                f"sub-agent {getattr(spec, 'agent_id', spec)!r} names unregistered "
                f"tool(s) {', '.join(repr(t) for t in unknown)}"
            )
    if problems:
        fail(
            member="tools",
            problem="an allowlist names something that does not exist",
            what="; ".join(problems) + ".",
            fix=(
                "Make every ALLOWLIST key a persona id from personas.PERSONAS, and every "
                "name inside an allowlist (including each SubAgentSpec.tool_allowlist) a "
                "key of TOOL_REGISTRY."
            ),
            if_not=(
                "Nothing raises. A persona keyed by a typo is offered no tools and "
                "answers with none; a tool named by a typo is silently absent from the "
                "model's tool payload. Both read, from the outside, as a model that "
                "chose not to use its tools."
            ),
            scar=(
                "is_allowed / tools_for read the allowlist with .get(persona_id, "
                "frozenset()) and intersect with the registry, so every mismatch here "
                "fails open into an empty set rather than an error."
            ),
        )


# ────────────────────────────────────────────────────────────── 5 · personas ──
def test_every_persona_the_adapter_declares_resolves(piece: Piece) -> None:
    """``get_persona`` resolves the default and every declared id, and returns what it was asked.

    SCAR: "a persona the resolver rejects". ``get_persona`` raises ``KeyError`` for an
    unknown id and ``PERSONAS[DEFAULT_PERSONA_ID]`` is evaluated for *every* request that
    names no persona — so a ``DEFAULT_PERSONA_ID`` that does not appear in ``PERSONAS``
    is not a small mistake, it is every anonymous request 500-ing at the first turn.
    """
    personas = piece("personas")
    registry: Mapping[str, Any] = personas.PERSONAS
    default_id = getattr(personas, "DEFAULT_PERSONA_ID", None)
    problems: list[str] = []
    if not registry:
        problems.append("PERSONAS is empty")
    if default_id not in registry:
        problems.append(f"DEFAULT_PERSONA_ID {default_id!r} is not a key of PERSONAS")
    for persona_id in registry:
        try:
            resolved = personas.get_persona(persona_id)
        except Exception as exc:  # noqa: BLE001 - any raise here is the defect
            problems.append(f"get_persona({persona_id!r}) raised {type(exc).__name__}: {exc}")
            continue
        found = str(getattr(resolved, "id", persona_id))
        if found != persona_id:
            problems.append(f"get_persona({persona_id!r}) returned the persona {found!r}")
    if not problems:
        try:
            fallback = personas.get_persona(None)
        except Exception as exc:  # noqa: BLE001 - any raise here is the defect
            problems.append(f"get_persona(None) raised {type(exc).__name__}: {exc}")
        else:
            if str(getattr(fallback, "id", default_id)) != default_id:
                problems.append("get_persona(None) did not return DEFAULT_PERSONA_ID's persona")
    if problems:
        fail(
            member="personas",
            problem="a persona the adapter declares does not resolve",
            what="; ".join(problems) + ".",
            fix=(
                "Key PERSONAS by each persona's own id, point DEFAULT_PERSONA_ID at one "
                "of those keys, and make get_persona(None) return that default."
            ),
            if_not=(
                "A persona is not a UI label: its data_scope becomes the retrieval filter "
                "and its id is the allowlist key. One that cannot be resolved takes down "
                "every request that names it — and if it is the default, every request "
                "that names none."
            ),
            scar=(
                "get_persona raises KeyError on an unknown id, and the default is "
                "resolved through the same dictionary on every request that omits a "
                "persona."
            ),
        )


# ─────────────────────────────────────────────────────────────── 6 · prompts ──
def test_the_system_prompt_never_drops_the_platform_floor(piece: Piece) -> None:
    """Each persona's rendered system prompt still contains its platform floor, verbatim.

    The floor is the half of the prompt no tenant may replace — the preamble plus the
    persona's live data scope and tool allowlist, derived from the enforcement tables.
    The task half is composed *over* it, never instead of it.

    SCAR: an LLM-Ops prompt version replaced the whole system prompt, floor included, so
    a tenant editing their prompt in the console silently deleted the boundary clauses
    that state what that persona may see and call. Nothing failed; the model simply
    stopped being told its limits.
    """
    prompts = piece("prompts")
    personas = piece("personas")
    problems: list[str] = []
    for persona_id, persona in personas.PERSONAS.items():
        floor = prompts.render_platform_floor(persona)
        rendered = prompts.render_system_prompt(persona)
        if not floor.strip():
            problems.append(f"render_platform_floor({persona_id!r}) is empty")
        elif floor not in rendered:
            problems.append(f"render_system_prompt({persona_id!r}) does not contain its floor")
        elif len(rendered.strip()) <= len(floor.strip()):
            problems.append(f"render_system_prompt({persona_id!r}) is the floor and nothing else")
    if problems:
        fail(
            member="prompts",
            problem="the platform floor is missing from a rendered system prompt",
            what="; ".join(problems) + ".",
            fix=(
                "Render the system prompt as the persona's task prompt composed WITH "
                "render_platform_floor(persona) — join them, never substitute. Derive the "
                "floor's scope and tool clauses from the enforcement tables (data_scope, "
                "ALLOWLIST) rather than restating them by hand."
            ),
            if_not=(
                "The model is never told the boundary it is being held to. The "
                "enforcement still happens, so the visible symptom is not a leak but a "
                "model that keeps attempting things it is not allowed to do — and a "
                "prompt a tenant can edit into having no floor at all."
            ),
            scar=(
                "An active LLM-Ops prompt version replaced the entire system prompt "
                "including the floor, so editing a prompt in the console deleted the "
                "scope and allowlist clauses with no warning."
            ),
        )


# ─────────────────────────────────────────────────────────── 7 · memory_spec ──
def test_memory_spec_satisfies_the_memory_contract(piece: Piece) -> None:
    """The memory piece carries every member :class:`aegis.memory.spec.MemorySpec` requires.

    SCAR: "a memory spec missing a required field". ``set_default_spec`` accepts any
    object at startup and the members are read one at a time, deep inside recall and
    consolidation — so a spec missing ``IMPORTANCE_HINTS`` or ``FactExtraction`` starts
    cleanly, serves queries, and fails (or silently degrades) only the first time a
    conversation is consolidated, which in a demo is never.
    """
    from aegis.memory.spec import MemorySpec

    spec = piece("memory_spec")
    missing = [m for m in _protocol_members(MemorySpec) if not hasattr(spec, m)]
    empty = [
        name
        for name in ("FACT_TYPES", "PROFILE_FIELDS", "FACT_EXTRACTION_PROMPT")
        if not getattr(spec, name, None)
    ]
    if missing or empty:
        fail(
            member="memory_spec",
            problem="the memory contract is incomplete",
            what=(
                f"missing: {', '.join(missing) or '(none)'}; declared but empty: "
                f"{', '.join(empty) or '(none)'}."
            ),
            fix=(
                "Implement every member of aegis.memory.spec.MemorySpec — the fact "
                "types, profile fields, extraction prompt, importance hints, SKILLS_DIR, "
                "the FactSchema/FactExtraction models, render_profile and select_skills — "
                "and install the module with aegis.memory.set_default_spec(...)."
            ),
            if_not=(
                "Nothing checks the spec at install time. A missing member surfaces "
                "inside recall or consolidation, long after startup, and an empty "
                "FACT_TYPES or extraction prompt does not surface at all: the extractor "
                "simply distils nothing and the agent has no long-term memory to show "
                "for a conversation it appeared to remember."
            ),
            scar=(
                "set_default_spec takes any object and the members are read lazily at "
                "recall/consolidate time, so an incomplete spec starts and serves "
                "normally."
            ),
        )


# ──────────────────────────────────────────────────────── 8 · skills (piece 10) ──
def _skill_stems(spec: object) -> tuple[Path, set[str]]:
    """Return the skills directory and the playbook names discovered inside it."""
    directory = Path(str(getattr(spec, "SKILLS_DIR", "")))
    stems = {p.stem for p in directory.glob("*.md")} if directory.is_dir() else set()
    return directory, stems


def test_skills_directory_holds_at_least_one_playbook(piece: Piece) -> None:
    """``SKILLS_DIR`` points at a real directory with at least one ``*.md`` playbook.

    SCAR: ``skills/`` is piece 10 — the piece with no member of its own, discovered from
    a path string at call time and never imported. It is exactly the piece the
    documentation lost: the adapter README said five pieces, a checklist said six, and a
    module docstring called itself "piece 6 of 6" while ten sat on disk. A ``SKILLS_DIR``
    that points nowhere discovers zero playbooks and reports nothing at all.
    """
    spec = piece("memory_spec")
    directory, stems = _skill_stems(spec)
    if not stems:
        fail(
            member="skills",
            problem="no procedural playbook was discovered",
            what=(
                f"memory_spec.SKILLS_DIR is {str(directory)!r}, which "
                f"{'holds no *.md file' if directory.is_dir() else 'is not a directory'}."
            ),
            fix=(
                "Point SKILLS_DIR at a directory inside the adapter package — "
                "str(Path(__file__).parent / 'skills') keeps it correct from a wheel and "
                "from a checkout — and write at least one Markdown playbook in it."
            ),
            if_not=(
                "The loader globs this directory at call time. An empty or wrong path "
                "yields an empty list, select_skills is handed nothing to choose from, "
                "and every turn runs with no procedural guidance — indistinguishable, in "
                "the trace, from a turn that needed none."
            ),
            scar=(
                "skills/ is the piece that appeared in no checklist in this repository "
                "while the running code required it: five pieces in one README, six in "
                "another, 'piece 6 of 6' in a docstring, ten on disk."
            ),
        )


def test_every_playbook_is_reachable_from_select_skills(piece: Piece) -> None:
    """Every playbook on disk can be selected, and the selector names no playbook that is gone.

    SCAR: ``select_skills`` maps keywords to playbook **filenames with a literal dict**.
    Rename ``closing_requests.md`` and the entry still reads ``"closing_requests"``,
    which is then filtered out by ``skill in available`` — so the renamed playbook can
    never be selected again and the stale entry can never fire. Both halves are silent:
    the selector returns its other matches, or nothing, and the turn proceeds.

    Two halves, both cheap and neither guessing. The **always-applicable** half probes
    the selector with its own compiled string constants and asserts it never returns a
    name outside the ``available`` list it was handed. The **literal-mapping** half reads
    those same constants (not the source, and never the docstring) to see which playbooks
    the selector can name at all: if it names some but not all of them, the ones it
    cannot name are unreachable and that is the scar exactly. A selector that names none
    of them literally selects some other way, and only the first half applies to it.
    """
    spec = piece("memory_spec")
    directory, stems = _skill_stems(spec)
    if not stems:
        return  # already reported by the directory check; one failure, not two
    selector = spec.select_skills
    literals = _string_constants(selector.__code__, exclude=selector.__doc__)
    default_persona = getattr(piece("personas"), "DEFAULT_PERSONA_ID", None)

    invented = {
        name
        for probe in [*sorted(literals), ""]
        for name in (selector(probe, default_persona, sorted(stems)) or ())
        if name not in stems
    }
    named = literals & stems
    unreachable = sorted(stems - literals) if named else []
    if invented or unreachable:
        problems = []
        if unreachable:
            problems.append(
                f"playbook(s) {', '.join(repr(s) for s in unreachable)} exist in "
                f"{directory.name}/ but are named nowhere in select_skills, which selects "
                f"{', '.join(repr(s) for s in sorted(named))} by literal name"
            )
        if invented:
            problems.append(
                f"select_skills returned {', '.join(repr(s) for s in sorted(invented))}, "
                f"which was not in the `available` list it was given"
            )
        fail(
            member="memory_spec",
            problem="a playbook and the selector that chooses it have drifted apart",
            what="; ".join(problems) + ".",
            fix=(
                "Keep the selector and the directory in step: every *.md in SKILLS_DIR "
                "must be reachable from select_skills, and every name select_skills can "
                "return must be a file. Return only names from the `available` argument."
            ),
            if_not=(
                "select_skills filters its answer by `skill in available`, so a renamed "
                "playbook is dropped and a stale mapping entry does nothing. The turn "
                "still answers — just without the procedure you wrote for it, and with "
                "no line in the trace to say a skill was wanted and missed."
            ),
            scar=(
                "select_skills selects by a literal keyword→filename dict; renaming a "
                "playbook without editing the dict makes it unselectable forever, in "
                "silence."
            ),
        )


# ────────────────────────────────────────────────────────────── 9 · ml_spec ──
def test_ml_spec_resolves_to_the_domain_not_the_fallback(piece: Piece) -> None:
    """``resolve_spec`` reads the adapter's real features and target, not the generic fallback.

    SCAR: ``aegis.ml.spec.resolve_spec`` reads ``FEATURE_NAMES``/``TARGET.name``
    **leniently** and returns ``FALLBACK_SPEC`` — four columns called ``feature_0…3``
    predicting ``target`` — when it finds neither. A misspelled attribute therefore does
    not raise: the spine trains happily on synthetic noise, the model serves, and the
    agent quotes its prediction as domain evidence.
    """
    from aegis.ml.spec import FALLBACK_SPEC, resolve_spec

    spec = piece("ml_spec")
    resolved = resolve_spec(spec)
    declared_features = [str(f) for f in getattr(spec, "FEATURE_NAMES", []) or []]
    declared_target = str(getattr(getattr(spec, "TARGET", None), "name", "") or "")
    mismatch = (
        resolved is FALLBACK_SPEC
        or resolved.features != declared_features
        or resolved.target != declared_target
    )
    if mismatch or not declared_features or not declared_target:
        fail(
            member="ml_spec",
            problem="the ML spine would not train on this domain's problem",
            what=(
                f"the adapter declares features={declared_features or '(none)'} and "
                f"target={declared_target or '(none)'!r}; resolve_spec produced "
                f"features={resolved.features} target={resolved.target!r}"
                + (" — the generic FALLBACK_SPEC." if resolved is FALLBACK_SPEC else ".")
            ),
            fix=(
                "Declare FEATURE_NAMES (the ordered column names) and TARGET (an object "
                "with .name and .task) on the ml_spec module, spelled exactly like that "
                "— resolve_spec reads those names, and also accepts lowercase "
                "features/target on a spec object."
            ),
            if_not=(
                "resolve_spec never raises. It falls back to four columns named "
                "feature_0..feature_3 predicting 'target', the spine trains on generated "
                "noise, and every prediction the agent reports is a real number computed "
                "from nothing to do with your domain."
            ),
            scar=(
                "The lenient reader plus FALLBACK_SPEC means a misspelled attribute "
                "silently trains the spine on synthetic noise and serves the result as "
                "domain evidence."
            ),
        )


# ─────────────────────────────────────────────────────────────── 10 · corpus ──
def test_seed_corpus_records_carry_identity_and_chunk(piece: Piece) -> None:
    """Seed records have a unique id and text that actually produces chunks.

    A domain with no seed corpus is legal and honest — the loader may return an empty
    list — so this check only constrains the records that exist.

    SCAR: the ingestion chunker shipped producing chunks with no tenant and a ``doc_id``
    that did not join ``documents.id``. Retrieval still returned passages; every
    citation on them resolved to nothing, and the answer looked fully sourced. A corpus
    record with no stable id, or with a body no chunker can split, is the same failure
    one step earlier — text in the index that nothing can be traced back to.
    """
    from aegis.retrieval.chunker import chunk_structured

    records = piece("corpus").load_seed_corpus()
    if not records:
        return  # a domain that ships no seed knowledge is a legal, stated choice
    problems: list[str] = []
    seen: dict[str, int] = {}
    for position, record in enumerate(records):
        identity = _get(record, _ID_ATTRS)
        text = _get(record, _TEXT_ATTRS)
        if identity is None or not str(identity[1] or "").strip():
            problems.append(
                f"record {position} carries no id (looked for {', '.join(_ID_ATTRS)})"
            )
        else:
            key = str(identity[1])
            if key in seen:
                problems.append(f"records {seen[key]} and {position} share the id {key!r}")
            seen[key] = position
        if text is None or not str(text[1] or "").strip():
            problems.append(
                f"record {position} carries no text (looked for {', '.join(_TEXT_ATTRS)})"
            )
        elif not chunk_structured(str(text[1])):
            problems.append(f"record {position} produced no chunks")
    if problems:
        fail(
            member="corpus",
            problem="a seed record cannot be indexed or cited",
            what="; ".join(problems[:6]) + ("; …" if len(problems) > 6 else "."),
            fix=(
                "Give every record returned by load_seed_corpus a unique, stable id and "
                "a non-empty text body, under one of the names the platform reads: "
                f"id ({', '.join(_ID_ATTRS)}) and text ({', '.join(_TEXT_ATTRS)})."
            ),
            if_not=(
                "Chunks are written with the record's id as their doc_id. A record with "
                "no id, or two records sharing one, produces chunks whose citation "
                "resolves to the wrong document or to nothing — and a retrieved passage "
                "with a broken citation reads on screen exactly like a sourced one."
            ),
            scar=(
                "The chunker shipped producing chunks with no tenant and a doc_id that "
                "did not join documents.id; retrieval kept returning passages and every "
                "citation on them was dead."
            ),
        )
