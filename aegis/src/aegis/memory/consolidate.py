"""Memory WRITE path — episodic→semantic consolidation (mem0-style, Zep bitemporal).

This is the deferred, cheap-model distillation that turns raw conversation turns into
durable, bitemporally-versioned facts. It runs OFF the money-shot path (a background
sweep over a durable queue), never on request latency.

Two phases per session (mem0):

1. **EXTRACT** — one cheap call over the running summary + the last ~10 turns using the
   injected :class:`~aegis.memory.spec.MemorySpec`'s ``FACT_EXTRACTION_PROMPT`` /
   ``FactExtraction`` schema. Candidates below ``config.tau_extract`` are dropped.
2. **RECONCILE** — each surviving candidate is embedded (batched once), its top-k valid
   neighbors fetched by cosine, and an operation decided:

   * **DEDUP short-circuit** — top neighbor cosine ``>= config.dedup_cos`` AND same
     predicate → NOOP with **no** second LLM call (just bump access stats).
   * otherwise a cheap ``decide_op`` call picks ADD | UPDATE | INVALIDATE | NOOP.

A mutating decision whose ``target_id`` cannot be resolved to a fact the model was
actually shown is **refused**, never retargeted onto the nearest neighbor — see
:func:`_resolve_target`. The refusal is audited and counted as
``ConsolidationResult.rejected``.

Applied under Zep bitemporal rules (see ``docs/architecture/memory-spec.md`` HARDENING CORRECTIONS):

* **ADD** — insert a new valid fact.
* **UPDATE** (refinement of the same value) — insert a superseding row and expire the old
  one (``expired_at``) under a concurrency guard.
* **INVALIDATE** (contradiction of the value) — set the old row's ``invalid_at`` +
  ``expired_at`` under the same guard, and insert the contradicting fact. Never a delete.
* **Re-assertion of an already-invalidated fact** falls out naturally as an ADD: the
  valid-only neighbor scan never surfaces the dead row, so it is a brand-new valid fact.

All writes are audited in ``memory_write_log``; the running summary and structured
profile are refreshed at the end. Everything is dependency-injected (``complete`` /
``embed`` / ``spec``) so the whole path is offline-testable with scripted fakes.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.core.models import ModelRole
from aegis.memory.config import MemoryConfig
from aegis.memory.scope import bind_memory_scope
from aegis.memory.scoring import ForgetPolicy
from aegis.memory.spec import FactSchemaLike, MemorySpec, resolve_spec
from aegis.memory.stores import (
    ConsolidationStatus,
    MemoryConsolidationJob,
    MemoryFact,
    MemoryMessage,
    MemoryProfile,
    MemorySession,
    MemoryWriteLog,
    WriteOp,
)
from aegis.memory.tokens import count_tokens
from aegis.memory.vector_ops import topk_by_cosine

# Injected LLM/embed callables (real bindings: a chat completer + a retrieval embedder).
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
    "You maintain a running summary of a conversation between a user and an assistant. "
    "Merge the prior summary with the new turns into a single concise summary that "
    "preserves durable context (who the user is, what they want, decisions and "
    "commitments). Return ONLY the updated summary text."
)

logger = logging.getLogger(__name__)



@dataclass
class ConsolidationResult:
    """Counts of the bitemporal operations a single ``consolidate`` run applied.

    Attributes:
        added: New valid facts inserted.
        updated: Refinements applied (old row expired + superseding row inserted).
        invalidated: Contradictions applied (old row invalidated + contradicting row).
        noop: Decisions that legitimately changed nothing (dedup or an explicit noop).
        rejected: Decisions **refused** because the model's ``target_id`` could not be
            resolved to a fact it was actually shown. Surfaced as its own count rather
            than folded into ``noop`` so a caller can see model failures, not just infer
            them from the write log.
    """

    added: int = 0
    updated: int = 0
    invalidated: int = 0
    noop: int = 0
    rejected: int = 0


class _DecideOp(BaseModel):
    """Parsed shape of the cheap reconcile call (defensive; unknown op → noop)."""

    op: str = Field(default="noop")
    target_id: int | None = Field(default=None)
    reason: str | None = Field(default=None)


# --------------------------------------------------------------------------- helpers


def _now() -> datetime:
    return datetime.now(UTC)


def _tenant_clause(model, tenant_id: int | None):  # noqa: ANN001, ANN202 - mapped class
    """The NULL-symmetric tenant predicate for ``model`` (``IS NULL`` for the null tenant).

    Mirrors :func:`aegis.memory.recall._tenant_clause` so the read and write paths agree on
    what "no tenant" scopes to; ``if tenant_id is not None`` silently means "any tenant".
    """
    if tenant_id is None:
        return model.tenant_id.is_(None)
    return model.tenant_id == tenant_id


def _resolve_target(
    decided: _DecideOp, neighbors: list[tuple[MemoryFact, float]]
) -> tuple[MemoryFact | None, str | None]:
    """Resolve a decide-op's ``target_id`` to a real neighbor, or explain why it cannot be.

    A ``target_id`` naming a fact the model was never shown is a **model failure**, not a
    hint. Retargeting it onto the cosine-nearest neighbor (the previous behaviour) writes
    ``invalid_at``/``expired_at`` onto an unrelated memory and inserts the candidate as its
    successor — permanently wrong bitemporal history, audited as a legitimate
    contradiction. Such a decision is refused here and the caller records it instead.

    An omitted ``target_id`` is defaulted only when the neighbor set has exactly one
    member, where the referent is unambiguous; with several plausible neighbors an omitted
    id is just as unresolvable as an invented one and is refused the same way.

    Args:
        decided: The parsed decide-op response.
        neighbors: The ``(fact, similarity)`` neighbors the model was shown, best first.

    Returns:
        ``(target, None)`` when the referent is resolved, else ``(None, reason)`` where
        ``reason`` names the failure for the write log.
    """
    if decided.target_id is not None:
        for fact, _ in neighbors:
            if fact.id == decided.target_id:
                return fact, None
        return None, (
            f"decide-op refused: target_id={decided.target_id} is not one of the "
            f"{len(neighbors)} existing fact(s) shown to the model"
        )
    if len(neighbors) == 1:
        return neighbors[0][0], None
    if not neighbors:
        return None, "decide-op refused: no target_id and no existing facts to target"
    return None, (
        f"decide-op refused: no target_id and {len(neighbors)} candidate neighbors — "
        "the referent is ambiguous"
    )


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


def _profile_field_for(predicate: str, spec: MemorySpec) -> str | None:
    """Map a fact predicate onto a structured-profile field, if one applies.

    The alias table is **the domain's**, read from the spec's optional
    ``PROFILE_ALIASES``. It used to be a module constant here, mapping spellings like
    one domain's own feature names onto that same domain's profile fields — core
    knowledge of one domain's vocabulary, and therefore an alias set that silently
    stopped matching anything the moment the domain changed. A domain that declares no
    aliases simply gets none; nothing degrades, because an unaliased predicate that is
    not already a profile field was never meant to write to the profile.

    Args:
        predicate: The extracted fact's predicate.
        spec: The domain memory spec.

    Returns:
        The profile field to write, or ``None`` when the predicate maps to none.
    """
    if predicate in spec.PROFILE_FIELDS:
        return predicate
    aliases: Mapping[str, str] = getattr(spec, "PROFILE_ALIASES", {}) or {}
    return aliases.get(predicate)


async def _load_session_and_turns(
    session: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    tenant_id: int | None,
) -> tuple[MemorySession | None, list[MemoryMessage]]:
    """Fetch the session row (subject/tenant-scoped) and its last ~10 turns, chronological.

    The tenant predicate is NULL-symmetric — ``tenant_id=None`` is the null-tenant scope,
    not "any tenant" — so a null-tenant consolidation can never distil another tenant's
    turns into this subject's facts.
    """
    sess_stmt = select(MemorySession).where(
        MemorySession.id == session_id,
        MemorySession.subject_id == subject_id,
        _tenant_clause(MemorySession, tenant_id),
    )
    sess = (await session.execute(sess_stmt)).scalar_one_or_none()

    msg_stmt = (
        select(MemoryMessage)
        .where(
            MemoryMessage.subject_id == subject_id,
            MemoryMessage.session_id == session_id,
            _tenant_clause(MemoryMessage, tenant_id),
        )
        .order_by(MemoryMessage.turn_index.desc(), MemoryMessage.id.desc())
        .limit(_LAST_M_TURNS)
    )
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


#: Subjects a durable "fact about the user" may never actually be about. The extraction
#: prompt already asks the model not to record the assistant's own statements, and the
#: model does it anyway: a real deployment stored *"The assistant lacks historical
#: reporting tools and cannot query historical data"* as an ``entity_attr`` about the
#: user, at confidence 1.0 and with no expiry.
#:
#: That single row is worse than noise, because memory is recalled into the next turn's
#: context. A sentence asserting the assistant cannot do something is a **standing
#: instruction not to try** — it outlives the gap it described, so the day the tool is
#: added the agent keeps declining, citing a memory of its own past limitation. The
#: subject of the sentence is the system, and the system is not what this table is for.
_NON_SUBJECTS: frozenset[str] = frozenset(
    {"assistant", "the assistant", "ai", "the ai", "agent", "the agent",
     "system", "the system", "aegis", "model", "the model", "bot", "the bot",
     "chatbot", "the chatbot"}
)

#: Phrasings that make a sentence a claim about the *tooling* rather than about a person.
#: Matched only in combination with a system subject or an explicit self-reference, so a
#: user's own genuine constraint ("I cannot access the finance dashboard") still records.
_CAPABILITY_CLAIM = (
    "lacks ", "lack ", "cannot query", "cannot access", "has no tool", "have no tool",
    "no tools", "does not have the tool", "is unable to", "cannot retrieve",
    "not available in this", "does not support",
)


def _is_about_the_system(candidate: FactSchemaLike) -> bool:
    """Whether this candidate records the assistant's own state rather than the user's.

    Two independent signals, because the model reaches the same bad row by two routes:
    naming the assistant as the ``subject``, or writing a subject-less ``text`` that is
    plainly a statement about tooling. Either is enough to drop it.

    Deliberately conservative about the second: a capability phrase only disqualifies a
    fact when the sentence is *about* the assistant, so a real standing constraint on the
    person — the thing this table exists to remember — is never mistaken for one.
    """
    subject = (getattr(candidate, "subject", "") or "").strip().lower()
    if subject in _NON_SUBJECTS:
        return True
    text = (getattr(candidate, "text", "") or "").strip().lower()
    names_system = any(
        f"the {n}" in text or text.startswith(n)
        for n in ("assistant", "system", "agent", "model")
    )
    return names_system and any(phrase in text for phrase in _CAPABILITY_CLAIM)


async def _extract_candidates(
    *,
    summary: str | None,
    turns: Sequence[MemoryMessage],
    config: MemoryConfig,
    complete: CompleteFn,
    spec: MemorySpec,
) -> list[FactSchemaLike]:
    """Phase 1: one cheap call → confidence-gated candidate facts (defensive parse)."""
    user_content = _render_turns(summary, turns)
    if not user_content.strip():
        return []
    result = await complete(
        ModelRole.CHEAP,
        [
            {"role": "system", "content": spec.FACT_EXTRACTION_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
    )
    raw = getattr(result, "content", "") or ""
    try:
        extraction = spec.FactExtraction.model_validate_json(raw)
    except (ValueError, ValidationError):
        return []
    # Two gates, and the second is not a duplicate of the prompt. The prompt asks the
    # model not to record the assistant's own statements; this enforces it. A rule that
    # only exists as a sentence in a prompt is a rule the model is free to ignore, and
    # this one it demonstrably did.
    kept: list[FactSchemaLike] = []
    for candidate in extraction.facts:
        if candidate.confidence < config.tau_extract:
            continue
        if _is_about_the_system(candidate):
            logger.info(
                "memory: dropped a candidate fact about the assistant rather than the "
                "subject: %r",
                (getattr(candidate, "text", "") or "")[:160],
            )
            continue
        kept.append(candidate)
    return kept


async def _decide_op(
    *,
    candidate: FactSchemaLike,
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
    candidate: FactSchemaLike,
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
    model: str | None = None,
) -> None:
    """Append one row to the fact-write audit trail.

    ``model`` names **who decided the write** and defaults to the consolidation
    model role, because until the memory control plane existed every write here was
    a model's. An explicit value is what an operator- or subject-initiated write
    passes (``"operator:<username>"``), so ``GET /memory/writes`` can say *a person
    wrote this* rather than attributing a human correction to the cheap model.
    """
    session.add(
        MemoryWriteLog(
            subject_id=subject_id,
            tenant_id=tenant_id,
            op=op,
            fact_id=fact_id,
            before=before,
            after=after,
            reason=reason,
            model=model or _MODEL_TAG,
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
    candidate: FactSchemaLike,
    target: MemoryFact,
    subject_id: str,
    tenant_id: int | None,
    embedding: list[float] | None,
    source_turn_ids: list[int],
    trace_id: str | None,
    reason: str | None,
    result: ConsolidationResult,
) -> bool:
    """Refinement of the same value: expire the old row (guarded) + insert superseding.

    Returns:
        Whether the refinement was actually applied — ``False`` when the concurrency guard
        found the row already moved, so the caller does not credit the candidate with a
        write that never happened (e.g. when refreshing the structured profile).
    """
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
        return False
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
    return True


async def _apply_invalidate(
    session: AsyncSession,
    *,
    candidate: FactSchemaLike,
    target: MemoryFact,
    subject_id: str,
    tenant_id: int | None,
    embedding: list[float] | None,
    source_turn_ids: list[int],
    trace_id: str | None,
    reason: str | None,
    result: ConsolidationResult,
) -> bool:
    """Contradiction: invalidate the old row (guarded) + insert the contradicting fact.

    Returns:
        Whether the contradiction was actually applied — ``False`` when the concurrency
        guard found the row already moved (same rationale as :func:`_apply_update`).
    """
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
        return False
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
    return True


async def _apply_add(
    session: AsyncSession,
    *,
    candidate: FactSchemaLike,
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
    candidates: list[FactSchemaLike],
    embeddings: list[list[float] | None],
    subject_id: str,
    tenant_id: int | None,
    source_turn_ids: list[int],
    config: MemoryConfig,
    complete: CompleteFn,
    trace_id: str | None,
    result: ConsolidationResult,
) -> list[FactSchemaLike]:
    """Phase 2: per-candidate dedup short-circuit → decide-op → bitemporal apply.

    Returns:
        The candidates whose op genuinely reached the store (ADD / applied UPDATE /
        applied INVALIDATE), in application order. Candidates that deduped, were an
        explicit noop, lost the concurrency guard, or whose decide-op was refused are
        **not** returned — so downstream derived state (the structured profile) is built
        from what was actually written, not from the raw extractor output.
    """
    applied: list[FactSchemaLike] = []
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
        target, target_error = _resolve_target(decided, neighbors)

        if decided.op in {"update", "invalidate"} and target is None and neighbors:
            # The model named a fact it was never shown (or named none while several were
            # plausible). Falling back to the cosine-nearest neighbor would corrupt an
            # unrelated memory's bitemporal history and audit it as a real contradiction,
            # so the decision is REFUSED: nothing is written, the refusal is audited with
            # its reason, and the caller sees it as ``ConsolidationResult.rejected``.
            _write_log(
                session,
                subject_id=subject_id,
                tenant_id=tenant_id,
                op=WriteOp.NOOP,
                fact_id=None,
                before={},
                after={},
                reason=target_error,
                trace_id=trace_id,
            )
            result.rejected += 1
            continue

        if decided.op == "add" or (decided.op in {"update", "invalidate"} and target is None):
            # ``target is None`` here means there were no neighbors at all — there is
            # nothing to supersede, so the candidate is simply new.
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
            applied.append(candidate)
        elif decided.op == "update":
            if await _apply_update(
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
            ):
                applied.append(candidate)
        elif decided.op == "invalidate":
            if await _apply_invalidate(
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
            ):
                applied.append(candidate)
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
                reason=decided.reason or target_error or "decide-op: noop",
                trace_id=trace_id,
            )
            result.noop += 1
    return applied


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
    candidates: list[FactSchemaLike],
    subject_id: str,
    tenant_id: int | None,
    spec: MemorySpec,
) -> None:
    """Merge mapped stable attributes from the APPLIED facts into the structured profile.

    ``candidates`` must be the ops that actually reached the store (see
    :func:`_reconcile`), never the raw extractor output: a candidate the reconcile ruled a
    duplicate/low-value noop — or one whose invalidate lost the concurrency guard — wrote
    no fact, so letting it rewrite the profile would put the prompt's human block out of
    sync with the bitemporal truth it is supposed to summarise.

    Within a batch, several applied facts can map onto the same profile field. They are
    merged in ascending confidence so the **most confident** value wins rather than
    whichever happened to sit last in the extractor's list; equal confidences fall back to
    application order (``sorted`` is stable), i.e. the later write wins.
    """
    updates: dict[str, Any] = {}
    for candidate in sorted(candidates, key=lambda c: c.confidence):
        field = _profile_field_for(candidate.predicate, spec)
        if field is not None and candidate.object:
            updates[field] = candidate.object
    if not updates:
        return

    prof_stmt = select(MemoryProfile).where(
        MemoryProfile.subject_id == subject_id,
        _tenant_clause(MemoryProfile, tenant_id),
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
    # This commits, and the request path keeps using the session afterwards. Bound to the
    # session rather than to this transaction so what follows the commit stays scoped —
    # see :mod:`aegis.memory.scope`.
    await bind_memory_scope(session, tenant_id)
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
    spec: MemorySpec | None = None,
) -> ConsolidationResult:
    """Distil the session's episodic turns into bitemporal semantic facts (mem0 2-phase).

    Extracts confidence-gated candidate facts, reconciles each against its valid
    neighbors (dedup short-circuit or a cheap decide-op call), applies the Zep
    bitemporal write rules, then refreshes the running summary and structured profile.

    Args:
        session: Async DB session.
        subject_id: The memory subject (app-level isolation key).
        session_id: The conversation thread to consolidate.
        config: Consolidation cadence, thresholds, and token caps.
        complete: Injected chat completer (cheap-model extract/decide/summary calls).
        embed: Injected batched embedder for candidate facts.
        tenant_id: Optional tenant scope.
        trace_id: Optional trace id recorded in the write log.
        spec: The domain :class:`~aegis.memory.spec.MemorySpec`; defaults to the configured
            process-wide spec when ``None``.

    Returns:
        A :class:`ConsolidationResult` with per-op counts. Does not commit — the caller
        (test or :func:`sweep_pending`) owns the transaction boundary.
    """
    spec = resolve_spec(spec)
    result = ConsolidationResult()

    # Consolidation does not commit, but it is *reached* after one: ``sweep_pending``
    # commits each job's claim before calling this, and ``stream_add`` commits right
    # after. Binding here means every statement below — the session/turn loads, the
    # neighbour searches in ``vector_ops``, the bitemporal writes, the profile and the
    # summary — carries this job's tenant, whichever transaction it lands in.
    await bind_memory_scope(session, tenant_id)

    sess, turns = await _load_session_and_turns(
        session, subject_id=subject_id, session_id=session_id, tenant_id=tenant_id
    )
    if sess is None:  # create a shell so the summary has somewhere to live
        sess = MemorySession(id=session_id, subject_id=subject_id, tenant_id=tenant_id)
        session.add(sess)
        await session.flush()

    candidates = await _extract_candidates(
        summary=sess.summary, turns=turns, config=config, complete=complete, spec=spec
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
        applied = await _reconcile(
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
        # Derived state follows the applied ops, not the raw candidates: a noop'd or
        # refused candidate must never move the structured profile.
        await _update_profile(
            session,
            candidates=applied,
            subject_id=subject_id,
            tenant_id=tenant_id,
            spec=spec,
        )

    await _refresh_summary(
        session, sess=sess, turns=turns, config=config, complete=complete
    )
    return result


async def prune_forgotten(
    session: AsyncSession,
    *,
    config: MemoryConfig,
    limit: int = 500,
    trace_id: str | None = None,
) -> int:
    """Soft-archive stale, low-value, aged-out facts out of hot recall (the forget sweep).

    The Generative-Agents-style forgetting pass (``ForgetPolicy``): for each currently-live
    fact whose confidence-weighted recency has decayed below ``config.forget_floor`` while
    it has never been recalled and is older than ``config.forget_min_age_days``, close it in
    transaction-time (``expired_at = now``) so it drops out of hot recall
    (``invalid_at IS NULL AND expired_at IS NULL``). This is a soft-archival, **never a
    hard delete** — the row is retained for audit and the belief timeline exactly like a
    supersession — and each archival is logged to ``memory_write_log`` as a ``PRUNE`` op.

    The SQL prefilter (live + never-recalled) bounds the scan cheaply; the exponential
    decay/floor test is applied in Python because it is not portable SQL across the SQLite
    test DB and Postgres. Does **not** commit — the caller owns the transaction boundary.

    **Cross-tenant by construction**, which is why it takes no ``tenant_id``: it scans
    every live fact on the platform. The caller must therefore have bound the *platform*
    scope (``bind_memory_scope(session, None)``, as :func:`sweep_pending` does) — under
    ``RLS_FAIL_CLOSED=true`` a session left bound to one tenant archives only that
    tenant's facts and an unbound one archives none, both silently.

    Returns:
        The number of facts archived.
    """
    policy = ForgetPolicy(
        forget_floor=config.forget_floor,
        forget_min_age_days=config.forget_min_age_days,
        half_life_days=config.half_life_days_fact,
    )
    stmt = (
        select(MemoryFact)
        .where(
            MemoryFact.expired_at.is_(None),
            MemoryFact.access_count == 0,
        )
        .order_by(MemoryFact.valid_at)
        .limit(limit)
    )
    rows = list((await session.execute(stmt)).scalars().all())

    now = _now()
    archived = 0
    for fact in rows:
        valid_at = fact.valid_at
        if valid_at.tzinfo is None:
            valid_at = valid_at.replace(tzinfo=UTC)
        age_days = max(0.0, (now - valid_at).total_seconds() / 86400.0)
        if not policy.is_archivable(
            confidence=fact.confidence,
            age_days=age_days,
            access_count=fact.access_count or 0,
            invalidated=fact.invalid_at is not None,
        ):
            continue
        before = _fact_snapshot(fact)
        fact.expired_at = now
        await session.flush()
        _write_log(
            session,
            subject_id=fact.subject_id,
            tenant_id=fact.tenant_id,
            op=WriteOp.PRUNE,
            fact_id=fact.id,
            before=before,
            after=_fact_snapshot(fact),
            reason="prune: decayed below forget_floor, never recalled, aged past floor",
            trace_id=trace_id,
        )
        archived += 1
    return archived


async def sweep_pending(
    session: AsyncSession,
    *,
    config: MemoryConfig,
    complete: CompleteFn,
    embed: EmbedFn,
    limit: int = 10,
    spec: MemorySpec | None = None,
) -> int:
    """Run up to ``limit`` PENDING consolidation jobs; flip each to DONE or ERROR.

    Each job is claimed with a guarded ``PENDING → RUNNING`` update (so a second sweeper
    cannot double-run it), consolidated, then marked terminal. Returns the number of jobs
    successfully consolidated.
    """
    # Local: keeps the governance package (and its pyjwt/argon2 dependencies) off
    # ``aegis.memory``'s import graph, the same way ``aegis.jobs.scope`` does. Resolved
    # once here rather than inside the loop's ``try``, where an ImportError would be
    # swallowed as a per-job failure and read as "consolidation is broken".
    from aegis.governance.context import governed  # noqa: PLC0415 - see above

    # **This sweeper's authority spans every tenant, and it has to say so.**
    #
    # The queue read below is deliberately cross-tenant: one sweeper drains the
    # pending consolidation jobs for the whole platform. But it bound no scope, so
    # `install_scope_auditor` logged it as an UNSCOPED READ on every sweep — and the
    # consequence is worse than the warning suggests. Under `RLS_FAIL_CLOSED=true`,
    # which is the production posture, an unbound session is not "sees everything";
    # it is "sees nothing", because the closed predicate has no scope to match. The
    # sweep would find zero PENDING jobs, process zero, report success, and memory
    # consolidation would silently stop for every tenant at once.
    #
    # A scope of ``None`` is not "clear the scope" — per ``set_tenant_scope``'s
    # contract it is the platform assertion, written to a second GUC precisely so
    # that "a caller whose authority spans every tenant" and "nobody bound a scope"
    # stop being spelled identically. That distinction is what this buys.
    #
    # ``bind_memory_scope`` rather than a one-shot ``set_tenant_scope`` because this
    # function **commits per job, deliberately** — one poisoned job must not roll back
    # the batch — and a transaction-local GUC does not survive a commit. The one-shot
    # form scoped the queue read and nothing after it: every claim, every statement of
    # every consolidation, every terminal status update and the whole forget sweep ran
    # unscoped. Under the fail-closed posture that is a sweep which claims nothing,
    # consolidates nothing and marks nothing DONE, while reporting success.
    await bind_memory_scope(session, None)

    stmt = (
        select(MemoryConsolidationJob)
        .where(MemoryConsolidationJob.status == ConsolidationStatus.PENDING)
        .order_by(MemoryConsolidationJob.created_at, MemoryConsolidationJob.id)
        .limit(limit)
    )
    # Read every job's identity ONCE, here, while the instances are live — and work from
    # the plain tuples afterwards. The loop below spans commits and, on the error path, a
    # ``rollback``, and a rollback expires every instance in the session whatever
    # ``expire_on_commit`` says. A later ``job.id`` is then a lazy refresh, issued from
    # whatever context happens to be current: inside the ``except`` block it raised
    # ``MissingGreenlet``, escaped the handler whose entire purpose is that one bad job
    # cannot break the batch, and took down the sweep, the request that fired it and
    # every background sweeper in the process with it.
    jobs = [
        (job.id, job.tenant_id, job.subject_id, job.session_id)
        for job in (await session.execute(stmt)).scalars().all()
    ]

    processed = 0
    for job_id, job_tenant, job_subject, job_session in jobs:
        # Back to the platform scope for the claim: the queue spans tenants, and the
        # previous iteration left the session bound to *its* job's tenant, under which
        # this job's row may be invisible. Re-binding is a no-op on the first pass.
        await bind_memory_scope(session, None)
        claim = (
            update(MemoryConsolidationJob)
            .where(
                MemoryConsolidationJob.id == job_id,
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

        # The claim is durable, so this unit of work now has an owner — and the scope
        # narrows from the platform to that owner for everything the job does, including
        # its terminal status update. Bound *here* rather than relying on ``consolidate``
        # to do it, because the ERROR branch below must also run under the job's own
        # tenant: a failure before ``consolidate`` bound anything would otherwise write
        # the error under the previous job's tenant and, fail-closed, update no row at
        # all — leaving the job stuck in RUNNING with nothing to say why.
        await bind_memory_scope(session, job_tenant)

        try:
            # The job row is where this unit of work acquires an owner, so it is where
            # the billing scope is bound. Consolidation makes live model calls —
            # summarisation plus an embedding per fact — and the gateway can only cap
            # and ledger what a bound governance context names. Without this the whole
            # queue spent a tenant's money against no cap and left no ledger row, on a
            # sixty-second timer (task 9.2).
            #
            # Bound per job and not per sweep, because one drain covers several tenants
            # and the wrong tenant's cap is not better than none.
            with governed(tenant_id=job_tenant):
                await consolidate(
                    session,
                    subject_id=job_subject,
                    session_id=job_session,
                    config=config,
                    complete=complete,
                    embed=embed,
                    tenant_id=job_tenant,
                    spec=spec,
                )
        except Exception as exc:  # noqa: BLE001 - queue must never crash the sweep
            await session.rollback()
            # The rollback discarded the transaction-local GUCs; the session-level
            # binding re-applies this job's scope to the transaction the UPDATE opens,
            # which is the only reason this row is still writable here.
            await session.execute(
                update(MemoryConsolidationJob)
                .where(MemoryConsolidationJob.id == job_id)
                .values(status=ConsolidationStatus.ERROR, error=str(exc)[:2000])
            )
            await session.commit()
            continue

        await session.execute(
            update(MemoryConsolidationJob)
            .where(MemoryConsolidationJob.id == job_id)
            .values(status=ConsolidationStatus.DONE, error=None)
        )
        await session.commit()
        processed += 1

    # Forgetting pass: every drain cycle also soft-archives stale, low-value, aged-out
    # facts out of hot recall (ForgetPolicy). Isolated from the queue work and committed on
    # its own — a prune failure must never wedge consolidation, and it does not affect the
    # returned processed-jobs count.
    try:
        # Platform scope again: the forget sweep is cross-tenant by construction (it
        # takes no tenant argument), and the session is still bound to the last job's.
        await bind_memory_scope(session, None)
        pruned = await prune_forgotten(session, config=config)
        if pruned:
            await session.commit()
    except Exception:  # noqa: BLE001 - prune is best-effort; never break the sweep
        await session.rollback()

    return processed


__all__ = [
    "ConsolidationResult",
    "consolidate",
    "enqueue_consolidation",
    "prune_forgotten",
    "sweep_pending",
]
