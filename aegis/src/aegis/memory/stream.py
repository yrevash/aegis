"""AG-UI streaming for memory recall — emits its work à la carte over the emitter.

Wraps one working-memory assembly (`recall_memory`) in a `STEP_STARTED`/`STEP_FINISHED`
bracket, emitting the `MEMORY_RECALL` custom event in between so a frontend can render how
much durable context was injected — recalled fact/message counts and the token cost — as
soon as recall finishes. Never calls a model (assembly is deterministic; BLOCKER 3).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.memory.working import AssembledMemory

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter

_STEP_NAME = "recall_memory"


class AssembleLike(Protocol):
    """Structural type of the assembler `stream_assemble` drives.

    Satisfied by the host's memory facade (e.g. a ``MemoryDeps`` whose ``assemble`` opens
    its own tenant-scoped session), so the streaming seam never threads a DB session.
    """

    async def assemble(
        self,
        *,
        subject_id: str,
        session_id: str,
        persona: str | None,
        query: str,
        query_vec: list[float] | None,
    ) -> AssembledMemory:
        """Recall + assemble the working-memory block for one turn."""
        ...


async def stream_assemble(
    assembler: AssembleLike,
    emitter: AegisEmitter,
    *,
    subject_id: str,
    session_id: str,
    persona: str | None = None,
    query: str,
    query_vec: list[float] | None = None,
) -> AssembledMemory:
    """Assemble working memory for one turn, streaming the recall evidence over `emitter`.

    Emits `STEP_STARTED("recall_memory")` → `CUSTOM(memory_recall)` →
    `STEP_FINISHED("recall_memory")`, bracketing one call to `assembler.assemble`.

    Args:
        assembler: Anything satisfying :class:`AssembleLike` (typically the host's memory
            facade).
        emitter: The AG-UI emitter for streaming events.
        subject_id: The memory subject (app-level isolation key).
        session_id: The current conversation thread.
        persona: Optional adapter persona id (gates skill selection).
        query: The user query.
        query_vec: Optional recall-comparable query embedding.

    Returns:
        The assembled :class:`~aegis.memory.working.AssembledMemory`.
    """
    async with emitter.step(_STEP_NAME, SpanKind.CHAIN):
        assembled = await assembler.assemble(
            subject_id=subject_id,
            session_id=session_id,
            persona=persona,
            query=query,
            query_vec=query_vec,
        )
        await emitter.custom(
            stream_names.MEMORY_RECALL,
            {
                "recalled_fact_count": len(assembled.recalled_fact_ids),
                "recalled_message_count": len(assembled.recalled_message_ids),
                "tokens_used": assembled.tokens_used,
            },
        )
    return assembled


__all__ = ["AssembleLike", "stream_assemble"]
