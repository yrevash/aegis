"""Agent roster — the domain's declaration of which specialists the supervisor may route to.

This is **piece 8 of 10** of the adapter, and the adapter half of the multi-agent
supervisor: the core owns the router mechanism (classifier + hand-off protocol),
while the domain declares *which* specialists exist, how to recognise them, and
what each one does. On the day only this file changes to add or retune an intent —
the core reads it defensively through
:func:`agent_roster` and falls back to a ``qa``-only roster if the contract is absent.

Each :class:`RosterSpecialist` carries:

* ``role`` — the stable id the core writes into ``state["agent_role"]`` and the
  ``routing`` stream event; it must match a graph specialist node (``qa`` → the full
  RAG+tools pipeline, ``memory`` → the memory specialist).
* ``keywords`` — deterministic phrase hints the core's classifier matches against the
  (lower-cased) query. First-match-wins keeps the trace clean and offline-testable.
* ``description`` — a one-line summary used both for the glass-box hand-off reason and
  as the menu handed to the cheap-LLM tiebreak when two specialists tie.
* ``is_default`` — exactly one specialist is the fall-through when nothing matches.

This file also declares the **sub-agent roster** (:func:`sub_agent_roster`) the adaptive
fan-out draws its team from. The two rosters answer different questions and are both
domain content, which is why they live together: ``agent_roster`` says *which specialist
handles this turn*, ``sub_agent_roster`` says *which agents a wide turn is split across*.
The core owns the fan-out mechanism and deliberately ships no roster of its own — a core
default would make every host fan out to agents it never declared — so until this
declaration existed, ``AgentDeps.default()`` left ``subagent_roster=None`` and **every
turn in the shipped application resolved SINGLE**, whatever the classifier or the user
asked for. Phase 5 was reachable only from its own tests.
"""

from __future__ import annotations

from dataclasses import dataclass

from aegis.agent import SubAgentSpec


@dataclass(frozen=True)
class RosterSpecialist:
    """One routable specialist the supervisor may hand a turn to.

    Attributes:
        role: Stable role id (matches a graph specialist node and the wire event).
        description: One-line summary of what this specialist answers.
        keywords: Lower-case phrase hints the deterministic classifier looks for in
            the query; longer/more specific phrases are the honest signal.
        is_default: Whether this is the fall-through specialist (the money-shot ``qa``
            pipeline) when no keyword matched.
    """

    role: str
    description: str
    keywords: tuple[str, ...] = ()
    is_default: bool = False


@dataclass(frozen=True)
class AgentRoster:
    """The set of specialists the supervisor may route between.

    Exactly one specialist should carry ``is_default=True`` (the ``qa`` money-shot);
    the core treats it as the fall-through and never routes to an unknown role.
    """

    specialists: tuple[RosterSpecialist, ...]

    @property
    def default_role(self) -> str:
        """Return the fall-through role id (the first ``is_default`` specialist)."""
        for spec in self.specialists:
            if spec.is_default:
                return spec.role
        # Defensive: an unmarked roster falls through to its first entry.
        return self.specialists[0].role if self.specialists else "qa"

    def roles(self) -> list[str]:
        """Return every routable role id in declaration order."""
        return [spec.role for spec in self.specialists]

    def named(self) -> list[RosterSpecialist]:
        """Return the non-default (keyword-matchable) specialists."""
        return [spec for spec in self.specialists if not spec.is_default]


# ── The example domain's roster ───────────────────────────────────────────────
# ``qa`` is the DEFAULT money-shot: the full recall → retrieve → ml → plan → gate →
# act → reflect → generate pipeline. ``memory`` is a genuinely distinct specialist
# that answers "what do you know about me / my preferences / my past" DIRECTLY from
# the long-term-memory subsystem, skipping RAG and tools entirely.
_ROSTER = AgentRoster(
    specialists=(
        RosterSpecialist(
            role="qa",
            description=(
                "General question answering over the knowledge base and case data, "
                "with tools and the human-approval gate. The default specialist."
            ),
            is_default=True,
        ),
        RosterSpecialist(
            role="memory",
            description=(
                "Answers questions about the user themselves — what the assistant "
                "knows or remembers about them, their stated preferences, and their "
                "past interactions — directly from long-term memory."
            ),
            keywords=(
                "what do you know about me",
                "what do you remember",
                "do you remember",
                "you remember about me",
                "know about me",
                "remember about me",
                "what have i told you",
                "what did i tell you",
                "my past interactions",
                "our past conversations",
                "remember about my",
                "what you know about me",
            ),
        ),
    )
)


def agent_roster() -> AgentRoster:
    """Return the domain's :class:`AgentRoster` (the specialists the core may route to).

    The single adapter contract the supervisor consumes. Swapping the domain means
    editing the roster above and keeping this function's shape stable.
    """
    return _ROSTER


# ── The example domain's SUB-agent roster (the fan-out team) ──────────────────
# Four lanes, because ``AgentConfig.max_parallel_agents`` is 4 and a roster shorter than
# the cap is the cap. Each carries its OWN tool allowlist, which the core intersects
# with the persona's — a lane can only ever narrow what its persona could already reach.
# Three of the four carry none at all: a research, knowledge or policy lane reads and
# reports, and giving a read-only remit a write tool is how a fan-out acquires four
# routes to a consequential action instead of one.
#
# ``model_role`` is left at the core default (``CHEAP``) for every lane. That choice is
# most of why fanning out four ways is affordable at all; the expensive model is spent
# once, on the synthesis.
_SUB_AGENTS: tuple[SubAgentSpec, ...] = (
    SubAgentSpec(
        agent_id="research",
        role="research",
        label="Research agent",
        system_prompt=(
            "You are the research agent on a support-operations team. Establish what is "
            "externally true about your sub-task — current policy, recent regulatory or "
            "vendor changes, and anything the internal corpus would not know. Report "
            "what you found and, just as plainly, what you could not establish. Never "
            "present an assumption as a finding."
        ),
    ),
    SubAgentSpec(
        agent_id="knowledge",
        role="knowledge",
        label="Knowledge agent",
        system_prompt=(
            "You are the knowledge agent on a support-operations team. Answer your "
            "sub-task from the shared retrieved context you were given: quote or "
            "paraphrase what the corpus actually says, name the passage it came from, "
            "and say explicitly when the corpus does not cover the question rather "
            "than filling the gap from general knowledge."
        ),
    ),
    SubAgentSpec(
        agent_id="data",
        role="data",
        label="Data agent",
        system_prompt=(
            "You are the data agent on a support-operations team. Answer your sub-task "
            "from the concrete service-request records, using your tools to read and "
            "annotate them. Any change to a request's status or ownership is a "
            "consequential action: request it as a tool call and it will be routed to a "
            "human for approval — you never take one yourself."
        ),
        # The only lane with a write remit, and the two writes it may REQUEST are still
        # gated: ``update_request_status`` is HIGH-risk, so the lane can propose it and
        # nothing else. ``add_case_note`` is LOW and is the one thing it may just do.
        tool_allowlist=frozenset({"update_request_status", "add_case_note"}),
    ),
    SubAgentSpec(
        agent_id="policy",
        role="policy",
        label="Policy agent",
        system_prompt=(
            "You are the policy agent on a support-operations team. Judge your sub-task "
            "against the organisation's escalation, SLA and approval rules: state which "
            "rule applies, what it requires, and where the situation in front of you "
            "departs from it. Recommend; never act."
        ),
    ),
)


def sub_agent_roster() -> tuple[SubAgentSpec, ...]:
    """Return the domain's sub-agent roster — the team a wide turn fans out across.

    The second adapter contract the supervisor consumes, bound to
    ``AgentDeps.subagent_roster``. The core reads it defensively (an absent hook, a
    raising hook or an empty roster all mean "no team, run single-pass"), clamps every
    entry's bounds down to the tenant's :class:`~aegis.agent.AgentConfig`, and takes the
    first ``width`` entries — so roster ORDER is the priority order for a narrow team.
    Swapping the domain means rewriting the four entries above and keeping this
    function's shape stable.
    """
    return _SUB_AGENTS
