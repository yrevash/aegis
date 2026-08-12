r"""AG-UI streaming for the full memory lifecycle — recall, cache, add, forget.

Every memory operation emits over the shared :class:`~aegis.core.stream.AegisEmitter` so a
frontend can later render exactly what memory did on a turn:

* **recall / query** — :func:`stream_assemble` brackets one working-memory assembly in a
  ``STEP_STARTED``/``STEP_FINISHED`` pair and emits ``memory_recall`` (what was checked:
  recalled fact/message ids + token cost). When a :class:`~aegis.memory.cache.\
  MemorySemanticCache` is supplied it also emits ``memory_cache`` (hit/miss with backend +
  similarity), serving the assembled block straight from the cache on a hit and writing it
  back on a miss.
* **add / consolidate** — :func:`stream_add` runs the deferred consolidation and emits
  ``memory_write`` with the per-op counts, then invalidates the subject's cache.
* **forget / delete / expire** — :func:`stream_forget` forgets one fact and emits
  ``memory_write`` (op ``delete``), then invalidates the subject's cache.

Cache invalidation always follows a **committed** authoritative SQL write (the derived
cache is never allowed to outlive the rows it summarises), and emits ``memory_cache``
(event ``evict``) so the eviction is visible too. Recall/assembly never calls a model
(deterministic; BLOCKER 3).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Protocol

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.memory.working import AssembledMemory

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from aegis.core.stream import AegisEmitter
    from aegis.memory.cache import MemorySemanticCache
    from aegis.memory.config import MemoryConfig
    from aegis.memory.consolidate import CompleteFn, ConsolidationResult, EmbedFn
    from aegis.memory.spec import MemorySpec

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


def _recall_payload(assembled: AssembledMemory) -> dict[str, Any]:
    """The ``memory_recall`` value — counts plus *what was checked* (recalled ids)."""
    return {
        "recalled_fact_count": len(assembled.recalled_fact_ids),
        "recalled_message_count": len(assembled.recalled_message_ids),
        "recalled_fact_ids": list(assembled.recalled_fact_ids),
        "recalled_message_ids": list(assembled.recalled_message_ids),
        "tokens_used": assembled.tokens_used,
    }


async def stream_assemble(
    assembler: AssembleLike,
    emitter: AegisEmitter,
    *,
    subject_id: str,
    session_id: str,
    persona: str | None = None,
    query: str,
    query_vec: list[float] | None = None,
    tenant_id: int | None = None,
    cache: MemorySemanticCache | None = None,
) -> AssembledMemory:
    """Assemble working memory for one turn, streaming recall + cache evidence.

    Emits ``STEP_STARTED("recall_memory")`` → [``memory_cache``] → ``memory_recall`` →
    ``STEP_FINISHED("recall_memory")``. With a ``cache``: a hit deserialises and returns the
    cached block (the expensive ``assembler.assemble`` never runs); a miss runs assembly and
    writes the result back under the configured TTL. Without a ``cache`` the behaviour is the
    original assemble-and-emit-counts path.

    Args:
        assembler: Anything satisfying :class:`AssembleLike` (typically the host facade).
        emitter: The AG-UI emitter for streaming events.
        subject_id: The memory subject (app-level isolation key).
        session_id: The current conversation thread.
        persona: Optional adapter persona id (gates skill selection).
        query: The user query.
        query_vec: Optional recall-comparable query embedding (also the cache probe vector).
        tenant_id: Optional tenant scope for the cache (the assembler carries its own).
        cache: Optional semantic cache over recall/assembly.

    Returns:
        The assembled :class:`~aegis.memory.working.AssembledMemory`.
    """
    async with emitter.step(_STEP_NAME, SpanKind.CHAIN):
        assembled = await _assemble_with_cache(
            assembler,
            emitter,
            subject_id=subject_id,
            session_id=session_id,
            persona=persona,
            query=query,
            query_vec=query_vec,
            tenant_id=tenant_id,
            cache=cache,
        )
        await emitter.custom(stream_names.MEMORY_RECALL, _recall_payload(assembled))
    return assembled


async def _assemble_with_cache(
    assembler: AssembleLike,
    emitter: AegisEmitter,
    *,
    subject_id: str,
    session_id: str,
    persona: str | None,
    query: str,
    query_vec: list[float] | None,
    tenant_id: int | None,
    cache: MemorySemanticCache | None,
) -> AssembledMemory:
    """Cache-check → (hit: deserialise | miss: assemble + write-back), emitting cache events."""
    if cache is not None:
        hit = await cache.check(
            subject_id=subject_id, query=query, query_vec=query_vec, tenant_id=tenant_id
        )
        if hit is not None:
            await emitter.custom(
                stream_names.MEMORY_CACHE,
                {
                    "event": "hit",
                    "backend": hit.backend,
                    "similarity": round(hit.similarity, 4),
                    "subject_id": subject_id,
                    "cached_query": hit.query,
                },
            )
            return AssembledMemory(**hit.value)
        await emitter.custom(
            stream_names.MEMORY_CACHE,
            {"event": "miss", "backend": cache.backend_label, "subject_id": subject_id},
        )

    assembled = await assembler.assemble(
        subject_id=subject_id,
        session_id=session_id,
        persona=persona,
        query=query,
        query_vec=query_vec,
    )
    if cache is not None:
        await cache.store(
            subject_id=subject_id,
            query=query,
            value=asdict(assembled),
            query_vec=query_vec,
            tenant_id=tenant_id,
        )
    return assembled


async def _emit_evict(
    emitter: AegisEmitter,
    cache: MemorySemanticCache | None,
    *,
    subject_id: str,
    tenant_id: int | None,
) -> None:
    """Invalidate a subject's derived cache after a committed write and emit ``evict``."""
    if cache is None:
        return
    evicted = await cache.invalidate(subject_id=subject_id, tenant_id=tenant_id)
    await emitter.custom(
        stream_names.MEMORY_CACHE,
        {
            "event": "evict",
            "backend": cache.backend_label,
            "count": evicted,
            "subject_id": subject_id,
        },
    )


async def stream_add(
    session: AsyncSession,
    emitter: AegisEmitter,
    *,
    subject_id: str,
    session_id: str,
    config: MemoryConfig,
    complete: CompleteFn,
    embed: EmbedFn,
    tenant_id: int | None = None,
    trace_id: str | None = None,
    spec: MemorySpec | None = None,
    cache: MemorySemanticCache | None = None,
) -> ConsolidationResult:
    """Consolidate a session into durable facts and stream the write, invalidating the cache.

    Runs :func:`aegis.memory.consolidate.consolidate` (mem0 EXTRACT→RECONCILE, Zep
    bitemporal), **commits** the authoritative rows, then invalidates the subject's derived
    cache. Emits ``memory_write`` (op ``consolidate`` with per-op counts) and — when a cache
    is present — ``memory_cache`` (``evict``).

    Returns:
        The :class:`~aegis.memory.consolidate.ConsolidationResult`.
    """
    from aegis.memory.consolidate import consolidate

    result = await consolidate(
        session,
        subject_id=subject_id,
        session_id=session_id,
        config=config,
        complete=complete,
        embed=embed,
        tenant_id=tenant_id,
        trace_id=trace_id,
        spec=spec,
    )
    await session.commit()  # authoritative SQL is durable before the derived cache drops
    await emitter.custom(
        stream_names.MEMORY_WRITE,
        {
            "op": "consolidate",
            "subject_id": subject_id,
            "session_id": session_id,
            "added": result.added,
            "updated": result.updated,
            "invalidated": result.invalidated,
            "noop": result.noop,
        },
    )
    await _emit_evict(emitter, cache, subject_id=subject_id, tenant_id=tenant_id)
    return result


async def stream_forget(
    session: AsyncSession,
    emitter: AegisEmitter,
    *,
    subject_id: str,
    fact_id: int,
    tenant_id: int | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    hard: bool = False,
    cache: MemorySemanticCache | None = None,
) -> bool:
    """Forget one durable fact and stream the delete, invalidating the subject's cache.

    Calls :func:`aegis.memory.crud.forget_fact` (soft by default; ``hard=True`` erases),
    **commits**, then invalidates the derived cache. Emits ``memory_write`` (op ``delete``)
    and — when a cache is present — ``memory_cache`` (``evict``).

    Returns:
        ``True`` if a fact was forgotten, ``False`` if none matched the subject/tenant.
    """
    from aegis.memory.crud import forget_fact

    fact = await forget_fact(
        session,
        fact_id=fact_id,
        subject_id=subject_id,
        tenant_id=tenant_id,
        reason=reason,
        trace_id=trace_id,
        hard=hard,
    )
    await session.commit()
    forgotten = fact is not None
    await emitter.custom(
        stream_names.MEMORY_WRITE,
        {
            "op": "delete",
            "subject_id": subject_id,
            "fact_id": fact_id,
            "hard": hard,
            "forgotten": forgotten,
        },
    )
    if forgotten:
        await _emit_evict(emitter, cache, subject_id=subject_id, tenant_id=tenant_id)
    return forgotten


__all__ = ["AssembleLike", "stream_add", "stream_assemble", "stream_forget"]
