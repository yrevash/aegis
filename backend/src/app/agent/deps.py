"""Backend composition root: wire ``aegis.agent``'s DI contract to the real modules.

The DI *contract* — :class:`AgentConfig`, the :class:`AgentDeps` dataclass, the
:class:`MemoryDeps`/``AnswerCache`` Protocols and the risk-ordering helpers — lives
in the standalone ``aegis.agent.deps``. This module is the strangler shim + the
composition root: it re-exports that contract by identity, provides the concrete,
DB-backed :class:`MemoryDeps` implementation (which opens tenant-scoped sessions and
writes the memory stores — the host coupling that could not move), and binds
:meth:`AgentDeps.default` to the live gateway, retrieval, guardrails, domain
adapter and durable data layer — mirroring the ``gateway.configure(...)``
pattern every other module proved.

Nothing about the graph's behaviour changes: every existing
``from app.agent.deps import ...`` / ``AgentDeps.default()`` call site is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aegis.agent.deps import (
    AgentConfig,
    ToolOutcome,
    risk_at_least,
    risk_rank,
)
from aegis.agent.deps import (
    AgentDeps as _AegisAgentDeps,
)
from aegis.agent.deps import (
    MemoryDeps as MemoryDepsProtocol,
)
from aegis.agent.router import load_roster as _core_load_roster

from app.api.schemas import RiskLevel

if TYPE_CHECKING:
    from app.memory.config import MemoryConfig
    from app.memory.working import AssembledMemory
    from app.retrieval.answer_cache import AnswerCache

logger = logging.getLogger(__name__)

__all__ = [
    "AgentConfig",
    "AgentDeps",
    "MemoryDeps",
    "MemoryDepsProtocol",
    "ToolOutcome",
    "risk_at_least",
    "risk_rank",
]


# ─────────────────────────────────────────────────────────────────────────────
# Long-term memory capability (the concrete, DB-backed impl behind the Protocol)
# ─────────────────────────────────────────────────────────────────────────────

# Live background consolidation tasks, kept referenced so the event loop cannot GC
# one mid-flight; the done-callback logs any exception (honest durability over a
# bare fire-and-forget ``create_task``). The durable ``memory_consolidation_job`` row
# enqueued synchronously in ``persist`` is the real recovery seam behind these.
_CONSOLIDATION_TASKS: set[asyncio.Task[Any]] = set()


def _on_consolidation_done(task: asyncio.Task[Any]) -> None:
    """Discard a finished consolidation task and surface any swallowed exception."""
    _CONSOLIDATION_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:  # pragma: no cover - defensive logging of a background failure
        logger.warning("Background memory consolidation failed", exc_info=exc)


def _current_tenant_id() -> int | None:
    """Return the request's tenant id from the governance context (``None`` if unset)."""
    try:
        from app.core.governance import get_governance_context

        gov = get_governance_context()
        return gov.tenant_id if gov is not None else None
    except Exception:  # noqa: BLE001 - governance is optional at this seam
        return None


@dataclass
class MemoryDeps:
    """Long-term-memory capability consumed by the ``recall_memory``/``persist_memory`` nodes.

    The concrete implementation behind ``aegis.agent``'s :class:`MemoryDeps` Protocol:
    it holds the memory :class:`~app.memory.config.MemoryConfig` plus the injected
    ``complete`` (LLM) and ``embed`` (retrieval embedding) callables, and opens its own
    tenant-scoped DB session per call so the agent graph never threads a session. Test
    fakes leave ``AgentDeps.memory = None`` so the nodes stay silent no-ops.
    """

    config: MemoryConfig
    complete: Any  # CompleteFn: (role, messages, ...) -> Awaitable[LLMResult]
    embed: Any  # retrieval EmbedFn: (list[str]) -> Awaitable[list[list[float]]]

    async def assemble(
        self,
        *,
        subject_id: str,
        session_id: str,
        persona: str | None,
        query: str,
        query_vec: list[float] | None,
    ) -> AssembledMemory:
        """Recall + assemble the working-memory block for one turn (READ path)."""
        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.working import assemble_working_memory

        # ``recall_memory`` runs BEFORE ``retrieve``, so the query embedding retrieval
        # later computes is not available yet — without embedding it here, semantic
        # fact/episodic recall would silently fall back to recency-only. Embed the
        # query now so the Generative-Agents composite actually runs against a vector.
        if query_vec is None:
            query_vec = await self._embed_query(query)

        tenant_id = _current_tenant_id()
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            return await assemble_working_memory(
                session,
                subject_id=subject_id,
                session_id=session_id,
                persona=persona,
                query=query,
                query_vec=query_vec,
                config=self.config,
                tenant_id=tenant_id,
            )

    async def persist(
        self,
        *,
        subject_id: str,
        session_id: str,
        turn_index: int,
        user_text: str,
        assistant_text: str,
        query_vec: list[float] | None,
        run_id: str | None,
        trace_id: str | None,
    ) -> None:
        """Persist the user + assistant turns and cadence-fire consolidation (WRITE path).

        The user turn reuses the retrieval query embedding **only** when it is a real
        gateway vector of dim ``EMBED_DIM``; a lite/256-dim vector is recorded by its
        dimension but never stored as a recall-comparable embedding (it would corrupt
        cosine recall). Every ``consolidation_every_n`` turns a durable job row is
        enqueued synchronously and a background consolidation task is fired off the hot
        path. Never blocks the stream.
        """
        from sqlalchemy import select

        from app.data.models import EMBED_DIM
        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.consolidate import enqueue_consolidation
        from app.memory.stores import MemoryMessage, MemoryOrigin, MemorySession

        tenant_id = _current_tenant_id()
        vec_dim = len(query_vec) if query_vec is not None else None
        user_embedding = query_vec if vec_dim == EMBED_DIM else None

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            sess = (
                await session.execute(
                    select(MemorySession).where(
                        MemorySession.id == session_id,
                        MemorySession.subject_id == subject_id,
                    )
                )
            ).scalar_one_or_none()
            if sess is None:
                sess = MemorySession(
                    id=session_id, subject_id=subject_id, tenant_id=tenant_id
                )
                session.add(sess)
                await session.flush()

            idx = turn_index if turn_index else (sess.turn_count or 0)
            session.add(
                MemoryMessage(
                    subject_id=subject_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    turn_index=idx,
                    role="user",
                    origin=MemoryOrigin.USER,
                    content=user_text,
                    embedding=user_embedding,
                    embedding_dim=vec_dim,
                    run_id=run_id,
                    trace_id=trace_id,
                )
            )
            session.add(
                MemoryMessage(
                    subject_id=subject_id,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    turn_index=idx,
                    role="assistant",
                    origin=MemoryOrigin.ASSISTANT,
                    content=assistant_text,
                    run_id=run_id,
                    trace_id=trace_id,
                )
            )
            sess.turn_count = (sess.turn_count or 0) + 1
            new_count = sess.turn_count
            await session.flush()

            every = self.config.consolidation_every_n
            if every > 0 and new_count % every == 0:
                # Durable enqueue commits the turns + the PENDING job together.
                await enqueue_consolidation(
                    session,
                    subject_id=subject_id,
                    session_id=session_id,
                    tenant_id=tenant_id,
                )
                self._fire_consolidation(
                    subject_id=subject_id, session_id=session_id, tenant_id=tenant_id
                )
            else:
                await session.commit()

    def _fire_consolidation(
        self, *, subject_id: str, session_id: str, tenant_id: int | None
    ) -> None:
        """Schedule a background consolidation, tracked so it is neither GC'd nor silent."""
        task = asyncio.create_task(
            self._run_consolidation(subject_id, session_id, tenant_id)
        )
        _CONSOLIDATION_TASKS.add(task)
        task.add_done_callback(_on_consolidation_done)

    async def _embed_query(self, query: str) -> list[float] | None:
        """Embed the query for semantic recall; ``None`` (→ recency fallback) on failure."""
        try:
            vecs = await self.embed([query])
        except Exception:  # noqa: BLE001 - recall must degrade, never crash a run
            logger.warning("memory: query embed failed; recall→recency", exc_info=True)
            return None
        return vecs[0] if vecs else None

    async def _run_consolidation(
        self, subject_id: str, session_id: str, tenant_id: int | None
    ) -> None:
        """Drain the durable consolidation queue off the hot path (marks jobs DONE).

        This runs ``sweep_pending`` — which CLAIMS the PENDING job ``persist`` just
        enqueued (``PENDING→RUNNING``), consolidates, and marks it ``DONE`` — rather than
        a raw ``consolidate`` that would leave the job PENDING for the interval sweeper to
        re-run (a duplicate extract/decide/summary pass). ``subject_id``/``session_id``
        identify the trigger; the sweep processes this tenant's queue.
        """
        from app.data.session import get_sessionmaker, set_tenant_scope
        from app.memory.consolidate import sweep_pending

        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            await sweep_pending(
                session,
                config=self.config,
                complete=self.complete,
                embed=self.embed,
                limit=8,
            )

    @classmethod
    def default(cls) -> MemoryDeps:
        """Build production memory deps: real config + live LLM/embed callables (lazy)."""
        from app.core.llm import complete
        from app.memory.config import MemoryConfig
        from app.retrieval.gateway import default_embed

        return cls(config=MemoryConfig(), complete=complete, embed=default_embed())


@dataclass
class AgentDeps(_AegisAgentDeps):
    """The concrete capabilities the graph calls (``aegis.agent`` contract) + host wiring.

    Subclasses the standalone :class:`aegis.agent.deps.AgentDeps` to add the host-side
    :meth:`default` composition root and to bind the ``agent_roster`` default to the
    **domain adapter's** roster (the core's own default is only a ``qa``-only fallback).
    The injected-callable shape is unchanged: a plain ``AgentDeps(...)`` still constructs
    it, so test fakes that omit ``agent_roster`` route through the real adapter roster —
    exactly as before the extraction.
    """

    def __post_init__(self) -> None:
        """Bind the adapter-backed roster when the caller left the core default in place."""
        if self.agent_roster is _core_load_roster:
            self.agent_roster = _default_agent_roster

    @classmethod
    def default(cls, config: AgentConfig | None = None) -> AgentDeps:
        """Build production deps bound to the real modules (lazy imports).

        Args:
            config: Optional bounded-autonomy configuration.

        Returns:
            An :class:`AgentDeps` wired to the live gateway, retrieval, guardrails
            and adapter tools, plus the durable seams (tenant scope + route audit)
            ``aegis.agent`` reaches through injected callables.
        """
        from app.adapter import DEFAULT_PERSONA_ID
        from app.config import get_settings
        from app.core.llm import complete
        from app.guardrails import check_input, check_output
        from app.retrieval import retrieve

        settings = get_settings()
        # When no explicit config is passed, build one from settings so environment
        # overrides of the retrieval-intelligence flags actually take effect.
        if config is None:
            config = AgentConfig(
                default_persona_id=DEFAULT_PERSONA_ID,
                query_rewrite_enabled=settings.query_rewrite_enabled,
                agentic_retrieval_enabled=settings.agentic_retrieval_enabled,
                agentic_retrieval_max_rounds=settings.agentic_retrieval_max_rounds,
                answer_cache_enabled=settings.answer_cache_enabled,
            )

        return cls(
            complete=complete,
            retrieve=retrieve,
            check_input=check_input,
            check_output=check_output,
            tool_definitions_for=_default_tool_definitions_for,
            run_tool=_default_run_tool,
            tool_risk=_default_tool_risk,
            render_system_prompt=_default_render_system_prompt,
            agent_roster=_default_agent_roster,
            config=config,
            memory=MemoryDeps.default(),
            answer_cache=_default_answer_cache(settings),
            current_tenant_id=_current_tenant_id,
            record_audit=_default_record_audit,
            embed_query=_default_embed_query,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Default (production) bindings — imported lazily to keep agent import cheap.
# ─────────────────────────────────────────────────────────────────────────────

_shared_store: Any = None


def _default_answer_cache(settings: Any) -> AnswerCache | None:  # noqa: ANN401 - Settings
    """Build the production answer cache, or ``None`` when disabled/unavailable.

    Only wired when both the store layer and the answer cache are enabled; a failure
    to construct the Redis-backed cache (bad URL, missing driver) degrades to ``None``
    (always-miss) rather than breaking agent construction. Lazy-imports the cache so the
    default memory/lite path never pulls in ``redis``.
    """
    if not (settings.stores_enabled and settings.answer_cache_enabled):
        return None
    try:
        from app.retrieval.answer_cache import AnswerCache

        return AnswerCache.from_url(
            settings.redis_url,
            ttl_seconds=settings.answer_cache_ttl_seconds,
            similarity_threshold=settings.answer_cache_threshold,
        )
    except Exception:  # noqa: BLE001 - answer cache is best-effort; degrade to always-miss
        logger.warning("Answer cache unavailable; continuing without it", exc_info=True)
        return None


def _get_shared_store() -> Any:  # noqa: ANN401 - adapter store type
    """Return a process-wide in-memory record store seeded with synthetic data.

    Uses the **synchronous** deterministic generator: this accessor is called from
    synchronous seams *and* from inside the running agent event loop (where
    ``asyncio.run`` would raise), and the store only needs schema-valid records with
    their real ``resolution_hours`` label — not LLM-written prose. Seeding a real
    store is what gives ``run_tool`` concrete records to act on, so a gated action
    changes an actual row rather than succeeding against nothing.
    """
    global _shared_store
    if _shared_store is None:
        from app.adapter import InMemoryRecordStore, generate_synthetic_sync

        dataset = generate_synthetic_sync()
        _shared_store = InMemoryRecordStore.from_dataset(dataset)
    return _shared_store


def _default_tool_definitions_for(persona_id: str) -> list[dict[str, Any]]:
    """Return the allowlist-filtered tool schemas for ``persona_id``."""
    from app.adapter import tool_definitions_for

    return tool_definitions_for(persona_id)


def _default_tool_risk(tool_name: str) -> RiskLevel:
    """Return the declared risk level for ``tool_name``, HIGH if unregistered (L2).

    Unknown/unregistered tools fail **safe**: an unregistered name (e.g. a
    hallucinated tool the planner invented) is treated as HIGH risk so it can never
    slip under the autonomy ceiling and skip the human gate.
    """
    from app.adapter import TOOL_REGISTRY

    spec = TOOL_REGISTRY.get(tool_name)
    return spec.risk if spec is not None else RiskLevel.HIGH


def _default_render_system_prompt(
    persona_id: str, extra_context: str | None = None
) -> str:
    """Render the persona's system prompt, preferring an LLM-Ops active version.

    Resolution order (the "feed-back-into-the-harness" seam): if the prompt registry has
    an ``active`` version cached for ``persona_id``, its ``system_prompt`` is the base;
    otherwise the **adapter default is the floor**. ``extra_context`` (the assembled
    working-memory block, or ``""``) is appended either way. With an empty registry cache
    and no ``extra_context`` — the default/test path — this renders exactly today's
    prompt, so the single-shot behavior is unchanged.
    """
    from app.adapter import get_persona, render_system_prompt
    from app.ops import registry

    cached = registry.get_cached_active(persona_id)
    if cached is not None:
        base = cached[0]
        if extra_context and extra_context.strip():
            return f"{base}\n\n{extra_context.strip()}"
        return base
    return render_system_prompt(get_persona(persona_id), extra_context=extra_context)


def _default_agent_roster() -> Any:  # noqa: ANN401 - adapter AgentRoster duck-type
    """Return the adapter's supervisor roster (routable specialists), lazily imported."""
    from app.agent.router import load_roster

    return load_roster()


async def _default_embed_query(query: str) -> list[float] | None:
    """Embed one query for memory recall; ``None`` (→ recency fallback) on failure.

    Satisfies ``AgentDeps.embed_query`` so the graph supplies a recall vector at the
    seam instead of relying on the host's ``MemoryDeps`` to notice the gap and
    compensate. ``MemoryDeps.assemble`` still has its own fallback, so this is
    defence in depth, not a behaviour change: exactly one embedding is computed
    either way, because ``assemble`` skips its own call when a vector arrives.
    """
    from app.retrieval.gateway import default_embed

    try:
        vecs = await default_embed()([query])
    except Exception:  # noqa: BLE001 - recall must degrade, never crash a run
        logger.warning("agent: query embed for recall failed; recall→recency", exc_info=True)
        return None
    return vecs[0] if vecs else None


async def _default_record_audit(**kwargs: Any) -> None:  # noqa: ANN401 - audit payload
    """Persist a best-effort supervisor-hand-off audit row (gated on ``stores_enabled``).

    The route-audit seam ``aegis.agent`` reaches through ``deps.record_audit``. Gated on
    ``stores_enabled`` so the offline "lite"/test path skips it silently, matching the
    original in-graph behaviour.
    """
    from app.config import get_settings

    if not get_settings().stores_enabled:
        return
    from app.data import record_audit

    await record_audit(**kwargs)


async def _default_run_tool(
    persona_id: str,
    tool_name: str,
    args: dict[str, Any],
    *,
    actor: str | None,
    model: str | None,
    trace_id: str | None,
    approver: str | None,
) -> ToolOutcome:
    """Execute an adapter tool with an audited, store-backed context."""
    from app.adapter import ToolContext, run_tool
    from app.data import record_audit

    ctx = ToolContext(
        store=_get_shared_store(),
        actor=actor,
        model=model,
        trace_id=trace_id,
        approved_by=approver,
        audit=record_audit,
    )
    return await run_tool(persona_id, tool_name, args, ctx)
