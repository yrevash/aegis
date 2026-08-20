"""The domain contract for long-term memory — injected, never hard-coded.

:mod:`aegis.memory` is domain-agnostic: *how* to persist, score, recall, budget and
consolidate is mechanism (core); *what counts as a durable fact*, *how to extract it*,
*how the profile reads*, and *which procedural skills apply* are domain meaning and live
behind this :class:`MemorySpec` Protocol. A host application supplies an object (commonly
a module) that structurally satisfies it — no inheritance required.

Recall/consolidate accept an optional ``spec`` argument and otherwise fall back to a
process-wide default configured once via :func:`set_default_spec` (the host wires its
adapter spec at startup). This keeps the public recall/consolidate signatures stable
while remaining fully injectable for tests and multi-domain embeds.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "FactExtractionLike",
    "FactSchemaLike",
    "MemorySpec",
    "get_default_spec",
    "resolve_spec",
    "set_default_spec",
]


class FactSchemaLike(Protocol):
    """Structural view of one durable fact the extractor emits.

    Mirrors the adapter's ``FactSchema`` (a pydantic model) without importing it, so the
    core reads candidate fields without coupling to a domain type.
    """

    fact_type: str
    subject: str
    predicate: str
    object: str
    text: str
    confidence: float
    importance: int
    valid_at: datetime | None


class FactExtractionLike(Protocol):
    """Structural view of the extractor's container object (a ``facts`` list)."""

    facts: list[FactSchemaLike]


@runtime_checkable
class MemorySpec(Protocol):
    """The domain seam for memory: extraction prompt, profile shape, and skill selection.

    An object satisfies this structurally. The reference adapter is a *module* whose
    module-level attributes and functions match these members (module functions bind no
    ``self``, so ``render_profile(self, profile)`` matches a module ``render_profile(profile)``).

    Attributes:
        FACT_EXTRACTION_PROMPT: System prompt driving the cheap-model fact extractor.
        IMPORTANCE_HINTS: Domain guidance for the 1..10 importance (poignancy) rating.
        PROFILE_FIELDS: Ordered structured-profile fields (the always-injected human block).
        PROFILE_ALIASES: *Optional.* Predicate spellings the extractor emits mapped onto
            ``PROFILE_FIELDS`` entries, for the case where the model phrases an
            attribute differently from the field that stores it. Absent means no
            aliases, which is a domain statement and not a degraded control: an
            unaliased predicate that is not already a profile field was never meant to
            write to the profile. Not declared as a member below because it is
            genuinely optional and a Protocol member is a requirement.
        FACT_TYPES: The typed kinds of durable fact the domain distils.
        SKILLS_DIR: Filesystem directory holding procedural skill markdown files.
        FactSchema: The pydantic model class for one extracted fact.
        FactExtraction: The pydantic container class (has a ``facts`` list; supports
            ``model_validate_json``).
    """

    FACT_EXTRACTION_PROMPT: str
    IMPORTANCE_HINTS: str
    PROFILE_FIELDS: list[str]
    FACT_TYPES: list[str]
    SKILLS_DIR: str
    FactSchema: type[Any]
    FactExtraction: type[Any]

    def render_profile(self, profile: dict[str, Any]) -> str:
        """Render the structured profile JSON as a compact prompt "human block"."""
        ...

    def select_skills(
        self, query: str, persona: str | None, available: list[str]
    ) -> list[str] | None:
        """Select procedural skill names for a query (subset of ``available``), or None.

        **No longer on the recall path as of §10.1.** Skills are rows in ``agent_skills``
        now, resolved ``platform u tenant u user`` through the settings resolver and
        offered to the model as name-and-description cards, with the body fetched by a
        ``load_skill`` tool call. A trigger is data on the row rather than a keyword
        table compiled into a function, which is what lets a tenant admin write one.

        The member stays because the ``skills/`` directory it selects from is still the
        adapter's shipped starter content and the conformance suite still checks the two
        are in step; it is not read by :mod:`aegis.memory.recall`. See the §10.1 report
        for the conformance change this asks for.
        """
        ...


_default_spec: MemorySpec | None = None


def set_default_spec(spec: MemorySpec) -> None:
    """Configure the process-wide default :class:`MemorySpec` (host wiring, called once).

    Args:
        spec: The domain contract object recall/consolidate use when no ``spec`` is passed.
    """
    global _default_spec  # noqa: PLW0603 - a single deliberate injection seam
    _default_spec = spec


def get_default_spec() -> MemorySpec:
    """Return the configured default :class:`MemorySpec`.

    Raises:
        RuntimeError: If no default has been configured via :func:`set_default_spec`.
    """
    if _default_spec is None:
        msg = (
            "aegis.memory has no MemorySpec configured; call "
            "aegis.memory.set_default_spec(...) or pass spec=... explicitly."
        )
        raise RuntimeError(msg)
    return _default_spec


def resolve_spec(spec: MemorySpec | None) -> MemorySpec:
    """Return ``spec`` if given, else the configured process-wide default."""
    return spec if spec is not None else get_default_spec()
