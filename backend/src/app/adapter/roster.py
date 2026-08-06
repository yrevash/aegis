"""Agent roster — the domain's declaration of which specialists the supervisor may route to.

This is the **adapter** half of the multi-agent supervisor: the core owns the
router mechanism (classifier + hand-off protocol), while the domain declares *which*
specialists exist, how to recognise them, and what each one does. On the day only
this file changes to add or retune an intent — the core reads it defensively through
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
"""

from __future__ import annotations

from dataclasses import dataclass


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
