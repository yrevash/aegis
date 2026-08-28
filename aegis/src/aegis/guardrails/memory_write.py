"""The types the memory-write rail speaks in.

They live in their own module for one reason: ``aegis.memory`` needs to *name* what it
sends to the rail and what it gets back, without importing the pipeline. The screen is
injected as a callable, so the memory package depends on this contract and nothing else —
the same shape ``aegis.agent`` already uses for its tool-result screen.

The subtle part is :class:`MemoryWriteVerdict`, and it is worth stating plainly: a caller
that receives a verdict and then writes **the strings it originally passed in** has not
redacted anything. The rewritten values are the point of the return type. This is the
same warning ``check_tool_result``'s docstring gives, made structural here by handing
back the fields rather than a boolean.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from aegis.core.types import GuardResult

__all__ = [
    "MemoryWriteCandidate",
    "MemoryWriteScreen",
    "MemoryWriteVerdict",
]


@dataclass(frozen=True, slots=True)
class MemoryWriteCandidate:
    """One fact on its way to the durable store, before anything has screened it.

    Attributes:
        subject: The entity the fact is about.
        predicate: The relation.
        object: The value.
        text: The rendered sentence the retriever will later put in front of a model.
            This is the field that matters most: it is what a future turn actually
            reads, so it is what an injection would have to travel in.
        origin: Where the write came from — ``"consolidation"`` for the extractor, or
            ``"operator:<username>"`` for a hand-written one. Matches the vocabulary the
            write log already uses, so a refusal is attributable to the same actor the
            successful writes are.
    """

    subject: str
    predicate: str
    object: str
    text: str
    origin: str = "consolidation"


@dataclass(frozen=True, slots=True)
class MemoryWriteVerdict:
    """What the rail decided, and the values the caller must actually write.

    Attributes:
        result: The rail's own verdict. ``verdict`` is ``BLOCK`` when the fact must not
            be stored at all, ``REDACT`` when it may be stored only in its rewritten
            form, and ``PASS`` when it is unchanged.
        subject: The subject to write. Rewritten if the rail redacted it.
        predicate: The predicate to write.
        object: The object to write.
        text: The sentence to write. **Use this, not the one you sent.**
        redactions: The kinds of thing that were redacted — kinds only, never values,
            because a log of what was redacted must not itself leak it.
    """

    result: GuardResult
    subject: str
    predicate: str
    object: str
    text: str
    redactions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        """Whether this fact must not be written at all."""
        return self.result.verdict.value == "block"


#: The screen, as the memory package sees it. Injected, never imported — so a deployment
#: that has no rail configured simply has no screen, and the write path says so rather
#: than silently behaving as though everything passed.
MemoryWriteScreen = Callable[[MemoryWriteCandidate], Awaitable[MemoryWriteVerdict]]
