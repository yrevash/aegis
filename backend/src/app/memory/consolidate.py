"""Memory WRITE path — episodic→semantic consolidation (mem0-style, Zep bitemporal).

This is the deferred, cheap-model distillation that turns raw conversation turns into
durable, bitemporally-versioned facts. It runs OFF the money-shot path (a background
sweep over a durable queue), never on request latency.

Two phases per session (mem0):

1. **EXTRACT** — one cheap call over the running summary + the last ~10 turns using the
   adapter's ``FACT_EXTRACTION_PROMPT`` / ``FactExtraction`` schema. Candidates below
   ``config.tau_extract`` are dropped.
2. **RECONCILE** — each surviving candidate is embedded (batched once), its top-k valid
   neighbors fetched by cosine, and an operation decided:

   * **DEDUP short-circuit** — top neighbor cosine ``>= config.dedup_cos`` AND same
     predicate → NOOP with **no** second LLM call (just bump access stats).
   * otherwise a cheap ``decide_op`` call picks ADD | UPDATE | INVALIDATE | NOOP.

Applied under Zep bitemporal rules (see ``docs/MEMORY_SPEC.md`` HARDENING CORRECTIONS):

* **ADD** — insert a new valid fact.
* **UPDATE** (refinement of the same value) — insert a superseding row and expire the old
  one (``expired_at``) under a concurrency guard.
* **INVALIDATE** (contradiction of the value) — set the old row's ``invalid_at`` +
  ``expired_at`` under the same guard, and insert the contradicting fact. Never a delete.
* **Re-assertion of an already-invalidated fact** falls out naturally as an ADD: the
  valid-only neighbor scan never surfaces the dead row, so it is a brand-new valid fact.

All writes are audited in ``memory_write_log``; the running summary and structured
profile are refreshed at the end. Everything is dependency-injected (``complete`` /
``embed``) so the whole path is offline-testable with scripted fakes.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapter.memory_spec import (
    FACT_EXTRACTION_PROMPT,
    PROFILE_FIELDS,
    FactExtraction,
    FactSchema,
)
from app.core.models import ModelRole
from app.memory.config import MemoryConfig
from app.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryProfile,
    MemorySession,
    MemoryWriteLog,
    WriteOp,
)
from app.memory.tokens import count_tokens
from app.memory.vector_ops import topk_by_cosine

# Injected LLM/embed callables (real bindings: app.core.llm.complete + retrieval embed).
CompleteFn = Callable[..., Awaitable[Any]]
EmbedFn = Callable[[list[str]], Awaitable[list[list[float]]]]

_MODEL_TAG = ModelRole.CHEAP.value

#: How many trailing turns feed the extractor (mem0 last-m window).
_LAST_M_TURNS = 10

#: System prompt for the cheap reconcile (decide-op) call. Detectably distinct from the
#: extraction prompt so a fake ``complete`` can route responses by phase.
_DECIDE_OP_PROMPT = (
    "You reconcile a newly-extracted candidate fact against the customer's existing "
    "known facts. Decide EXACTLY ONE operation:\n"
    '- "add": the candidate is genuinely new information.\n'
    '- "update": the candidate refines/sharpens an existing fact with the SAME value '
    "(more precise wording, added detail) — give its id as target_id.\n"
    '- "invalidate": the candidate CONTRADICTS an existing fact (the value changed) — '
    "give the contradicted fact's id as target_id.\n"
    '- "noop": the candidate adds nothing.\n'
    'Return JSON: {"op": "add|update|invalidate|noop", "target_id": <int|null>, '
    '"reason": "<short>"}.'
)

#: System prompt for the running-summary refresh (plain text, not JSON).
_SUMMARY_PROMPT = (
    "You maintain a running summary of a customer-support conversation. Merge the prior "
    "summary with the new turns into a single concise summary that preserves durable "
    "context (who the customer is, what they want, decisions and commitments). Return "
    "ONLY the updated summary text."
)

#: Non-membership predicate aliases → structured profile fields.
_PROFILE_ALIASES: dict[str, str] = {
    "prefers_channel": "preferred_channel",
    "channel": "preferred_channel",
    "prefers_language": "preferred_language",
    "language": "preferred_language",
    "name": "display_name",
    "customer_tier": "tier",
}


@dataclass
class ConsolidationResult:
    """Counts of the bitemporal operations a single ``consolidate`` run applied."""

    added: int = 0
    updated: int = 0
    invalidated: int = 0
    noop: int = 0


class _DecideOp(BaseModel):
    """Parsed shape of the cheap reconcile call (defensive; unknown op → noop)."""

    op: str = Field(default="noop")
    target_id: int | None = Field(default=None)
    reason: str | None = Field(default=None)


# --------------------------------------------------------------------------- helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _fact_snapshot(fact: MemoryFact) -> dict[str, Any]:
    """A JSON-serialisable before/after snapshot for the write log."""

    def _iso(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None

    return {
        "id": fact.id,
        "predicate": fact.predicate,
        "object": fact.object,
        "text": fact.text,
        "confidence": fact.confidence,
        "importance": fact.importance,
        "valid_at": _iso(fact.valid_at),
        "invalid_at": _iso(fact.invalid_at),
        "expired_at": _iso(fact.expired_at),
        "supersedes_id": fact.supersedes_id,
    }


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Trim ``text`` so ``count_tokens(text) <= max_tokens`` (char-proportional)."""
    if max_tokens <= 0 or not text:
        return ""
    if count_tokens(text) <= max_tokens:
        return text
    truncated = text[: max_tokens * 4]
    while truncated and count_tokens(truncated) > max_tokens:
        truncated = truncated[: int(len(truncated) * 0.9)]
    return truncated


def _profile_field_for(predicate: str) -> str | None:
    """Map a fact predicate onto a structured-profile field, if one applies."""
    if predicate in PROFILE_FIELDS:
        return predicate
    return _PROFILE_ALIASES.get(predicate)


async def _load_session_and_turns(
    session: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    tenant_id: int | None,
) -> tuple[MemorySession | None, list[MemoryMessage]]:
    """Fetch the session row (subject-scoped) and its last ~10 turns, chronological."""
    sess_stmt = select(MemorySession).where(
        MemorySession.id == session_id, MemorySession.subject_id == subject_id
    )
    sess = (await session.execute(sess_stmt)).scalar_one_or_none()

    msg_stmt = (
        select(MemoryMessage)
        .where(
            MemoryMessage.subject_id == subject_id,
            MemoryMessage.session_id == session_id,
        )
        .order_by(MemoryMessage.turn_index.desc(), MemoryMessage.id.desc())
        .limit(_LAST_M_TURNS)
    )
    if tenant_id is not None:
        msg_stmt = msg_stmt.where(MemoryMessage.tenant_id == tenant_id)
    rows = list((await session.execute(msg_stmt)).scalars().all())
    rows.reverse()  # chronological order for the extractor
    return sess, rows


def _render_turns(summary: str | None, turns: Sequence[MemoryMessage]) -> str:
    parts: list[str] = []
    if summary:
        parts.append(f"Running summary so far:\n{summary}")
    if turns:
        rendered = "\n".join(f"{m.role}: {m.content}" for m in turns)
        parts.append(f"Recent turns:\n{rendered}")
    return "\n\n".join(parts)


async def _extract_candidates(
    *,
    summary: str | None,
    turns: Sequence[MemoryMessage],
    config: MemoryConfig,
    complete: CompleteFn,
) -> list[FactSchema]:
    """Phase 1: one cheap call → confidence-gated candidate facts (defensive parse)."""
    user_content = _render_turns(summary, turns)
    if not user_content.strip():
        return []
    result = await complete(
        ModelRole.CHEAP,
        [
            {"role": "system", "content": FACT_EXTRACTION_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
    )
    raw = getattr(result, "content", "") or ""
    try:
        extraction = FactExtraction.model_validate_json(raw)
    except (ValueError, ValidationError):
        return []
    return [c for c in extraction.facts if c.confidence >= config.tau_extract]


async def _decide_op(
    *,
    candidate: FactSchema,
    neighbors: list[tuple[MemoryFact, float]],
    complete: CompleteFn,
) -> _DecideOp:
    """The cheap reconcile call over candidate + valid neighbors (defensive parse)."""
    neighbor_lines = [
        {
            "id": fact.id,
            "predicate": fact.predicate,
            "object": fact.object,
            "text": fact.text,
            "similarity": round(sim, 4),
        }
        for fact, sim in neighbors
    ]
    payload = {
        "candidate": {
            "predicate": candidate.predicate,
            "object": candidate.object,
            "text": candidate.text,
        },
        "existing_facts": neighbor_lines,
    }
    result = await complete(
        ModelRole.CHEAP,
        [
            {"role": "system", "content": _DECIDE_OP_PROMPT},
            {"role": "user", "content": json.dumps(payload)},
        ],
        response_format={"type": "json_object"},
    )
    raw = getattr(result, "content", "") or ""
    try:
        decided = _DecideOp.model_validate_json(raw)
    except (ValueError, ValidationError):
        return _DecideOp(op="noop")
    decided.op = (decided.op or "noop").strip().lower()
    return decided


def _new_fact(
    candidate: FactSchema,
    *,
    subject_id: str,
    tenant_id: int | None,
    embedding: list[float] | None,
    source_turn_ids: list[int],
    supersedes_id: int | None = None,
) -> MemoryFact:
    fact = MemoryFact(
        subject_id=subject_id,
        tenant_id=tenant_id,
        fact_type=candidate.fact_type,
        subject=candidate.subject,
        predicate=candidate.predicate,
        object=candidate.object,
        text=candidate.text,
        embedding=embedding,
        confidence=candidate.confidence,
        importance=candidate.importance,
        source_turn_ids=list(source_turn_ids),
        supersedes_id=supersedes_id,
    )
    if candidate.valid_at is not None:
        fact.valid_at = candidate.valid_at
    return fact


def _write_log(
    session: AsyncSession,
    *,
    subject_id: str,
    tenant_id: int | None,
    op: WriteOp,
    fact_id: int | None,
    before: dict[str, Any],
    after: dict[str, Any],
    reason: str | None,
    trace_id: str | None,
) -> None:
    session.add(
        MemoryWriteLog(
            subject_id=subject_id,
            tenant_id=tenant_id,
            op=op,
            fact_id=fact_id,
            before=before,
            after=after,
            reason=reason,
            model=_MODEL_TAG,
            trace_id=trace_id,
        )
    )


async def _bump_access(session: AsyncSession, fact: MemoryFact) -> None:
    fact.access_count = (fact.access_count or 0) + 1
    fact.last_access_at = _now()
    await session.flush()


# --------------------------------------------------------------------------- apply


async def _apply_update(
    session: AsyncSession,
    *,
    candidate: FactSchema,
    target: MemoryFact,
    subject_id: str,
    tenant_id: int | None,
    embedding: list[float] | None,
    source_turn_ids: list[int],
    trace_id: str | None,
    reason: str | None,
    result: ConsolidationResult,
) -> None:
    """Refinement of the same value: expire the old row (guarded) + insert superseding."""
    now = _now()
    guard = (
        update(MemoryFact)
        .where(
            MemoryFact.id == target.id,
            MemoryFact.invalid_at.is_(None),
            MemoryFact.expired_at.is_(None),
        )
        .values(expired_at=now)
    )
    res = await session.execute(guard)
    if (res.rowcount or 0) == 0:  # a concurrent writer already moved it → no-op
        result.noop += 1
        return
    before = _fact_snapshot(target)
    fact = _new_fact(
        candidate,
        subject_id=subject_id,
        tenant_id=tenant_id,
        embedding=embedding,
        source_turn_ids=source_turn_ids,
        supersedes_id=target.id,
    )
    session.add(fact)
    await session.flush()
    _write_log(
        session,
        subject_id=subject_id,
        tenant_id=tenant_id,
        op=WriteOp.UPDATE,
        fact_id=fact.id,
        before=before,
        after=_fact_snapshot(fact),
        reason=reason,
        trace_id=trace_id,
    )
    result.updated += 1


async def _apply_invalidate(
    session: AsyncSession,
    *,
    candidate: FactSchema,
    target: MemoryFact,
    subject_id: str,
    tenant_id: int | None,
    embedding: list[float] | None,
    source_turn_ids: list[int],
    trace_id: str | None,
    reason: str | None,
    result: ConsolidationResult,
) -> None:
    """Contradiction: invalidate the old row (guarded) + insert the contradicting fact."""
    now = _now()
    invalid_at = candidate.valid_at or now
    guard = (
        update(MemoryFact)
        .where(
            MemoryFact.id == target.id,
            MemoryFact.invalid_at.is_(None),
            MemoryFact.expired_at.is_(None),
        )
        .values(invalid_at=invalid_at, expired_at=now)
    )
    res = await session.execute(guard)
    if (res.rowcount or 0) == 0:
        result.noop += 1
        return
    before = _fact_snapshot(target)
    fact = _new_fact(
        candidate,
        subject_id=subject_id,
        tenant_id=tenant_id,
        embedding=embedding,
        source_turn_ids=source_turn_ids,
        supersedes_id=target.id,
    )
    session.add(fact)
    await session.flush()
    _write_log(
        session,
        subject_id=subject_id,
        tenant_id=tenant_id,
        op=WriteOp.INVALIDATE,
        fact_id=fact.id,
        before=before,
        after=_fact_snapshot(fact),
        reason=reason,
        trace_id=trace_id,
    )
    result.invalidated += 1


async def _apply_add(
    session: AsyncSession,
    *,
    candidate: FactSchema,
    subject_id: str,
    tenant_id: int | None,
    embedding: list[float] | None,
    source_turn_ids: list[int],
    trace_id: str | None,
    reason: str | None,
    result: ConsolidationResult,
) -> None:
    fact = _new_fact(
        candidate,
        subject_id=subject_id,
        tenant_id=tenant_id,
        embedding=embedding,
        source_turn_ids=source_turn_ids,
    )
    session.add(fact)
    await session.flush()
    _write_log(
        session,
        subject_id=subject_id,
        tenant_id=tenant_id,
        op=WriteOp.ADD,
        fact_id=fact.id,
        before={},
        after=_fact_snapshot(fact),
        reason=reason,
        trace_id=trace_id,
    )
    result.added += 1


async def _reconcile(
    session: AsyncSession,
    *,
    candidates: list[FactSchema],
    embeddings: list[list[float] | None],
    subject_id: str,
    tenant_id: int | None,
    source_turn_ids: list[int],
    config: MemoryConfig,
    complete: CompleteFn,
    trace_id: str | None,
    result: ConsolidationResult,
) -> None:
    """Phase 2: per-candidate dedup short-circuit → decide-op → bitemporal apply."""
    for candidate, embedding in zip(candidates, embeddings, strict=False):
        neighbors = await topk_by_cosine(
            session,
            MemoryFact,
            subject_id=subject_id,
            query_vec=embedding,
            k=10,
            tenant_id=tenant_id,
            valid_only=True,
        )

        # DEDUP short-circuit — no second LLM call.
        if neighbors:
            top_fact, top_sim = neighbors[0]
            if top_sim >= config.dedup_cos and top_fact.predicate == candidate.predicate:
                await _bump_access(session, top_fact)
                _write_log(
                    session,
                    subject_id=subject_id,
                    tenant_id=tenant_id,
                    op=WriteOp.NOOP,
                    fact_id=top_fact.id,
                    before=_fact_snapshot(top_fact),
                    after={},
                    reason="dedup: cosine >= dedup_cos, same predicate",
                    trace_id=trace_id,
                )
                result.noop += 1
                continue

        decided = await _decide_op(
            candidate=candidate, neighbors=neighbors, complete=complete
        )
        neighbor_by_id = {fact.id: fact for fact, _ in neighbors}
        target = neighbor_by_id.get(decided.target_id)
        if target is None and neighbors and decided.op in {"update", "invalidate", "noop"}:
            target = neighbors[0][0]  # sensible default when the model omits the id

        if decided.op == "add" or (decided.op in {"update", "invalidate"} and target is None):
            await _apply_add(
                session,
                candidate=candidate,
                subject_id=subject_id,
                tenant_id=tenant_id,
                embedding=embedding,
                source_turn_ids=source_turn_ids,
                trace_id=trace_id,
                reason=decided.reason,
                result=result,
            )
        elif decided.op == "update":
            await _apply_update(
                session,
                candidate=candidate,
                target=target,
                subject_id=subject_id,
                tenant_id=tenant_id,
                embedding=embedding,
                source_turn_ids=source_turn_ids,
                trace_id=trace_id,
                reason=decided.reason,
                result=result,
            )
        elif decided.op == "invalidate":
            await _apply_invalidate(
                session,
                candidate=candidate,
                target=target,
                subject_id=subject_id,
                tenant_id=tenant_id,
                embedding=embedding,
                source_turn_ids=source_turn_ids,
                trace_id=trace_id,
                reason=decided.reason,
                result=result,
            )
        else:  # noop (explicit)
            if target is not None:
                await _bump_access(session, target)
            _write_log(
                session,
                subject_id=subject_id,
                tenant_id=tenant_id,
                op=WriteOp.NOOP,
                fact_id=target.id if target is not None else None,
                before=_fact_snapshot(target) if target is not None else {},
                after={},
                reason=decided.reason or "decide-op: noop",
                trace_id=trace_id,
            )
            result.noop += 1


async def _refresh_summary(
    session: AsyncSession,
    *,
    sess: MemorySession,
    turns: Sequence[MemoryMessage],
    config: MemoryConfig,
    complete: CompleteFn,
) -> None:
    """Map-reduce the running summary over the new turns, truncated to the token cap."""
    if not turns:
        return
    rendered = "\n".join(f"{m.role}: {m.content}" for m in turns)
    user_content = f"Prior summary:\n{sess.summary or '(none)'}\n\nNew turns:\n{rendered}"
    result = await complete(
        ModelRole.CHEAP,
        [
            {"role": "system", "content": _SUMMARY_PROMPT},
            {"role": "user", "content": user_content},
        ],
    )
    new_summary = (getattr(result, "content", "") or "").strip()
    if new_summary:
        sess.summary = _truncate_to_tokens(new_summary, config.summary_max_tokens)
        await session.flush()


async def _update_profile(
    session: AsyncSession,
    *,
    candidates: list[FactSchema],
    subject_id: str,
    tenant_id: int | None,
) -> None:
    """Merge mapped stable attributes from the candidates into the structured profile."""
    updates: dict[str, Any] = {}
    for candidate in candidates:
        field = _profile_field_for(candidate.predicate)
        if field is not None and candidate.object:
            updates[field] = candidate.object
    if not updates:
        return

    prof_stmt = select(MemoryProfile).where(MemoryProfile.subject_id == subject_id)
    prof_stmt = prof_stmt.where(
        MemoryProfile.tenant_id == tenant_id
        if tenant_id is not None
        else MemoryProfile.tenant_id.is_(None)
    )
    prof = (await session.execute(prof_stmt)).scalar_one_or_none()
    if prof is None:
        session.add(
            MemoryProfile(subject_id=subject_id, tenant_id=tenant_id, data=dict(updates))
        )
    else:
        prof.data = {**(prof.data or {}), **updates}  # new dict → change detection
    await session.flush()


# --------------------------------------------------------------------------- public API


async def enqueue_consolidation(
    session: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    tenant_id: int | None = None,
) -> MemoryConsolidationJob:
    """Insert a PENDING consolidation job synchronously (called from the request path).

    Durability seam: the job row is committed before the request returns, so a crash or
    redeploy cannot lose the work — the background :func:`sweep_pending` re-runs it.

    Returns:
        The persisted :class:`MemoryConsolidationJob` (status ``PENDING``).
    """
    job = MemoryConsolidationJob(
        subject_id=subject_id,
        session_id=session_id,
        tenant_id=tenant_id,
        status=ConsolidationStatus.PENDING,
    )
    session.add(job)
    await session.flush()
    await session.commit()
    return job


async def consolidate(
    session: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    config: MemoryConfig,
    complete: CompleteFn,
    embed: EmbedFn,
    tenant_id: int | None = None,
    trace_id: str | None = None,
) -> ConsolidationResult:
    """Distil the session's episodic turns into bitemporal semantic facts (mem0 2-phase).

    Extracts confidence-gated candidate facts, reconciles each against its valid
    neighbors (dedup short-circuit or a cheap decide-op call), applies the Zep
    bitemporal write rules, then refreshes the running summary and structured profile.

    Returns:
        A :class:`ConsolidationResult` with per-op counts. Does not commit — the caller
        (test or :func:`sweep_pending`) owns the transaction boundary.
    """
    result = ConsolidationResult()

    sess, turns = await _load_session_and_turns(
        session, subject_id=subject_id, session_id=session_id, tenant_id=tenant_id
    )
    if sess is None:  # create a shell so the summary has somewhere to live
        sess = MemorySession(id=session_id, subject_id=subject_id, tenant_id=tenant_id)
        session.add(sess)
        await session.flush()

    candidates = await _extract_candidates(
        summary=sess.summary, turns=turns, config=config, complete=complete
    )
    source_turn_ids = [m.id for m in turns]

    if candidates:
        texts = [c.text for c in candidates]
        raw_embeddings = await embed(texts)
        # Fact embeddings use whatever the injected embedder returns (real gateway =
        # EMBED_DIM; lite = a consistent smaller dim). Recall's cosine skips any
        # dim-mismatched neighbor, so mixing embedder dims across a subject's lifetime
        # (a mode switch) would just degrade those facts to recency recall — an accepted
        # lite-mode edge case, not a correctness bug within a single mode.
        embeddings: list[list[float] | None] = list(raw_embeddings) + [None] * (
            len(candidates) - len(raw_embeddings)
        )
        await _reconcile(
            session,
            candidates=candidates,
            embeddings=embeddings,
            subject_id=subject_id,
            tenant_id=tenant_id,
            source_turn_ids=source_turn_ids,
            config=config,
            complete=complete,
            trace_id=trace_id,
            result=result,
        )
        await _update_profile(
            session,
            candidates=candidates,
            subject_id=subject_id,
            tenant_id=tenant_id,
        )

    await _refresh_summary(
        session, sess=sess, turns=turns, config=config, complete=complete
    )
    return result


async def sweep_pending(
    session: AsyncSession,
    *,
    config: MemoryConfig,
    complete: CompleteFn,
    embed: EmbedFn,
    limit: int = 10,
) -> int:
    """Run up to ``limit`` PENDING consolidation jobs; flip each to DONE or ERROR.

    Each job is claimed with a guarded ``PENDING → RUNNING`` update (so a second sweeper
    cannot double-run it), consolidated, then marked terminal. Returns the number of jobs
    successfully consolidated.
    """
    stmt = (
        select(MemoryConsolidationJob)
        .where(MemoryConsolidationJob.status == ConsolidationStatus.PENDING)
        .order_by(MemoryConsolidationJob.created_at, MemoryConsolidationJob.id)
        .limit(limit)
    )
    jobs = list((await session.execute(stmt)).scalars().all())

    processed = 0
    for job in jobs:
        claim = (
            update(MemoryConsolidationJob)
            .where(
                MemoryConsolidationJob.id == job.id,
                MemoryConsolidationJob.status == ConsolidationStatus.PENDING,
            )
            .values(
                status=ConsolidationStatus.RUNNING,
                attempts=MemoryConsolidationJob.attempts + 1,
            )
        )
        res = await session.execute(claim)
        if (res.rowcount or 0) == 0:  # lost the race to another sweeper
            continue
        await session.commit()

        try:
            await consolidate(
                session,
                subject_id=job.subject_id,
                session_id=job.session_id,
                config=config,
                complete=complete,
                embed=embed,
                tenant_id=job.tenant_id,
            )
        except Exception as exc:  # noqa: BLE001 - queue must never crash the sweep
            await session.rollback()
            await session.execute(
                update(MemoryConsolidationJob)
                .where(MemoryConsolidationJob.id == job.id)
                .values(status=ConsolidationStatus.ERROR, error=str(exc)[:2000])
            )
            await session.commit()
            continue

        await session.execute(
            update(MemoryConsolidationJob)
            .where(MemoryConsolidationJob.id == job.id)
            .values(status=ConsolidationStatus.DONE, error=None)
        )
        await session.commit()
        processed += 1

    return processed


__all__ = [
    "ConsolidationResult",
    "consolidate",
    "enqueue_consolidation",
    "sweep_pending",
]
