"""Memory READ path — per-tier recall (semantic / profile / episodic / procedural).

This is the first half of context engineering (``docs/MEMORY_SPEC.md`` §B): gather the
raw material the working-memory assembler (:mod:`aegis.memory.working`) later budgets and
orders. It does **selection**, not layout — no token budget, no spotlighting here.

**Isolation is app-level first (BLOCKER 2).** Every query filters ``subject_id`` (and
``tenant_id`` when given) in its ``WHERE`` clause — the primary, NULL-safe, dialect-
independent isolator. Postgres RLS is only an additive belt and is never relied upon.

**Dual-path vectors (BLOCKER 1).** All cosine search goes through
:func:`aegis.memory.vector_ops.topk_by_cosine`, which is portable across the SQLite test
DB and Postgres. When ``query_vec`` is ``None`` (e.g. an exact-cache hit never computed
one, or a lite 256-dim vector is not recall-comparable) facts fall back to recency-only SQL.

The domain seam (profile rendering, skill selection) is the injected
:class:`~aegis.memory.spec.MemorySpec`; pass ``spec=`` or configure a process-wide default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.memory.config import MemoryConfig
from aegis.memory.scoring import RecallCandidate, rank_top
from aegis.memory.spec import MemorySpec, resolve_spec
from aegis.memory.stores import MemoryFact, MemoryMessage, MemoryProfile, MemorySession
from aegis.memory.vector_ops import topk_by_cosine
from aegis.retrieval.fusion import RankedList, reciprocal_rank_fusion
from aegis.retrieval.models import Candidate
from aegis.retrieval.types import RetrievalOrigin


@dataclass
class RecallBundle:
    """The raw recalled material for one turn, before budgeting/ordering.

    Consumed by :func:`aegis.memory.working.assemble_working_memory`. Every field is
    already subject/tenant-scoped; the assembler only budgets, dedups, and orders it.

    Attributes:
        profile_text: The rendered "human block" (``spec.render_profile``), or "".
        facts: Ranked durable facts (Generative-Agents composite, or recency-only).
        episodic: RRF-fused earlier turns, EXCLUDING those already in the raw window.
        skills: Selected procedural skills as ``(name, markdown_text)`` pairs.
        running_summary: The session's rolling summary (``MemorySession.summary``), or "".
    """

    profile_text: str = ""
    facts: list[RecallCandidate] = field(default_factory=list)
    episodic: list[RecallCandidate] = field(default_factory=list)
    skills: list[tuple[str, str]] = field(default_factory=list)
    running_summary: str = ""


def _age_days(ts: datetime | None) -> float:
    """Non-negative age in days of ``ts`` (treats naive timestamps as UTC).

    SQLite ``server_default=func.now()`` yields naive datetimes; Postgres may yield
    aware ones. Normalising both to UTC keeps recency decay correct on either dialect.
    """
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return max(0.0, (datetime.now(UTC) - ts).total_seconds() / 86400.0)


async def load_raw_window(
    session: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    config: MemoryConfig,
    tenant_id: int | None = None,
) -> list[MemoryMessage]:
    """Return the last ``raw_window_turns`` messages of this session, oldest-first.

    This is the verbatim "raw window" — the bottom tier of the assembled context and the
    dedup set that episodic recall is filtered against. Shared by :func:`recall` (for the
    dedup set) and the assembler (for the actual bottom section) so the definition of
    "recent turns" is single-sourced.
    """
    if config.raw_window_turns <= 0:
        return []
    stmt = select(MemoryMessage).where(MemoryMessage.subject_id == subject_id)
    if tenant_id is not None:
        stmt = stmt.where(MemoryMessage.tenant_id == tenant_id)
    stmt = (
        stmt.where(MemoryMessage.session_id == session_id)
        .order_by(MemoryMessage.turn_index.desc())
        .limit(config.raw_window_turns)
    )
    rows = list((await session.execute(stmt)).scalars().all())
    rows.reverse()  # oldest-first for natural reading order
    return rows


async def _recall_facts(
    session: AsyncSession,
    *,
    subject_id: str,
    query_vec: list[float] | None,
    config: MemoryConfig,
    tenant_id: int | None,
) -> list[RecallCandidate]:
    """Semantic facts: pgvector/Python top-k over VALID facts → composite → top-n.

    Falls back to recency-only (``ORDER BY valid_at DESC``) when there is no comparable
    query vector, so recall still serves under the degradation ladder.
    """
    if query_vec:
        hits = await topk_by_cosine(
            session,
            MemoryFact,
            subject_id=subject_id,
            query_vec=query_vec,
            k=config.k_fact,
            tenant_id=tenant_id,
            valid_only=True,
        )
        candidates = [
            RecallCandidate(
                key=f"{f.subject}|{f.predicate}",
                text=f.text,
                relevance=sim,
                age_days=_age_days(f.valid_at),
                importance=f.importance,
                access_count=f.access_count,
                payload=f,
            )
            for f, sim in hits
        ]
        return rank_top(
            candidates, config, half_life_days=config.half_life_days_fact, n=config.n_fact
        )

    # Recency-only fallback: newest valid facts, no scoring.
    stmt = select(MemoryFact).where(MemoryFact.subject_id == subject_id)
    if tenant_id is not None:
        stmt = stmt.where(MemoryFact.tenant_id == tenant_id)
    stmt = (
        stmt.where(MemoryFact.invalid_at.is_(None), MemoryFact.expired_at.is_(None))
        .order_by(MemoryFact.valid_at.desc())
        .limit(config.n_fact)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return [
        RecallCandidate(
            key=f"{f.subject}|{f.predicate}",
            text=f.text,
            relevance=0.0,
            age_days=_age_days(f.valid_at),
            importance=f.importance,
            access_count=f.access_count,
            payload=f,
        )
        for f in rows
    ]


async def _recall_profile(
    session: AsyncSession,
    *,
    subject_id: str,
    tenant_id: int | None,
    spec: MemorySpec,
) -> str:
    """Load the subject's profile row and render it as the human block (or "")."""
    stmt = select(MemoryProfile).where(MemoryProfile.subject_id == subject_id)
    if tenant_id is not None:
        stmt = stmt.where(MemoryProfile.tenant_id == tenant_id)
    profile = (await session.execute(stmt)).scalars().first()
    if profile is None:
        return ""
    return spec.render_profile(profile.data or {})


async def _recall_episodic(
    session: AsyncSession,
    *,
    subject_id: str,
    query_vec: list[float] | None,
    config: MemoryConfig,
    tenant_id: int | None,
    raw_window: list[MemoryMessage],
) -> list[RecallCandidate]:
    """Hybrid episodic recall: RRF-fuse (recency window ∪ vector top-k), minus the window.

    The recency list is the raw window tagged ``BM25`` (``RetrievalOrigin`` has no
    "recency"); the vector list is the subject-wide semantic top-k tagged ``VECTOR``. The
    fused survivors are then filtered to those NOT already in the raw window, so episodic
    recall only ever contributes older, relevant turns the bottom tier would miss.
    """
    raw_ids = {m.id for m in raw_window}
    msg_by_id: dict[int, MemoryMessage] = {m.id: m for m in raw_window}

    # Recency list (newest first) — the raw window as a ranked list.
    recency_list = RankedList(
        origins=(RetrievalOrigin.BM25,),
        candidates=[
            Candidate(id=str(m.id), text=m.content)
            for m in sorted(raw_window, key=lambda m: m.turn_index, reverse=True)
        ],
    )

    # Vector list (most similar first) across the whole subject.
    vec_hits = await topk_by_cosine(
        session,
        MemoryMessage,
        subject_id=subject_id,
        query_vec=query_vec,
        k=config.k_epi,
        tenant_id=tenant_id,
    )
    vec_sim: dict[int, float] = {}
    vector_candidates: list[Candidate] = []
    for msg, sim in vec_hits:
        msg_by_id[msg.id] = msg
        vec_sim[msg.id] = sim
        vector_candidates.append(Candidate(id=str(msg.id), text=msg.content))
    vector_list = RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=vector_candidates)

    fused = reciprocal_rank_fusion([recency_list, vector_list])

    out: list[RecallCandidate] = []
    for cand in fused:
        mid = int(cand.id)
        if mid in raw_ids:
            continue  # already in the bottom raw-window tier — never double-inject
        msg = msg_by_id.get(mid)
        if msg is None:
            continue
        out.append(
            RecallCandidate(
                key=f"msg:{mid}",
                text=msg.content,
                relevance=vec_sim.get(mid, 0.0),
                age_days=_age_days(msg.created_at),
                importance=msg.importance,
                access_count=msg.access_count,
                payload=msg,
            )
        )
        if len(out) >= config.n_epi:
            break
    return out


def _recall_skills(
    query: str, persona: str | None, config: MemoryConfig, spec: MemorySpec
) -> list[tuple[str, str]]:
    """Select procedural skills for the query and read their markdown bodies."""
    try:
        available = [
            f[:-3] for f in os.listdir(spec.SKILLS_DIR) if f.endswith(".md")
        ]
    except OSError:
        return []
    selected = spec.select_skills(query, persona, available) or []
    skills: list[tuple[str, str]] = []
    for name in selected[: config.n_skill]:
        path = os.path.join(spec.SKILLS_DIR, f"{name}.md")
        try:
            with open(path, encoding="utf-8") as fh:
                skills.append((name, fh.read()))
        except OSError:
            continue
    return skills


async def _bump_recall_access(
    session: AsyncSession,
    *,
    facts: list[RecallCandidate],
    episodic: list[RecallCandidate],
) -> None:
    """Record that these facts/messages were recalled this turn (read-path frequency).

    Bumps ``access_count`` (+1) and ``last_access_at`` on every fact and episodic turn
    actually recalled into this turn's bundle, then **commits** — the recall session is
    otherwise read-only and its caller never commits, so a bare flush would be rolled back
    on session close and the frequency signal would stay inert. This is the sole write
    recall performs; on the hot path no other pending state shares the session, so the
    commit persists only these bumps.

    The bump feeds the frequency term of the recall composite (``config.w_freq``) on
    FUTURE turns; it never reorders the current turn, which is already ranked. Mirrors the
    write-path bump in :func:`aegis.memory.consolidate._bump_access`.
    """
    fact_ids = [
        f.payload.id
        for f in facts
        if getattr(f.payload, "id", None) is not None
    ]
    message_ids = [
        e.payload.id
        for e in episodic
        if getattr(e.payload, "id", None) is not None
    ]
    if not fact_ids and not message_ids:
        return
    now = datetime.now(UTC)
    if fact_ids:
        await session.execute(
            update(MemoryFact)
            .where(MemoryFact.id.in_(fact_ids))
            .values(access_count=MemoryFact.access_count + 1, last_access_at=now)
        )
    if message_ids:
        await session.execute(
            update(MemoryMessage)
            .where(MemoryMessage.id.in_(message_ids))
            .values(access_count=MemoryMessage.access_count + 1, last_access_at=now)
        )
    await session.commit()
    # Keep the returned candidates consistent with the persisted counts.
    for cand in (*facts, *episodic):
        if getattr(cand.payload, "id", None) is not None:
            cand.access_count += 1


async def recall(
    session: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    persona: str | None,
    query: str,
    query_vec: list[float] | None,
    config: MemoryConfig,
    tenant_id: int | None = None,
    spec: MemorySpec | None = None,
) -> RecallBundle:
    """Gather all recall tiers for one turn (facts, profile, episodic, skills, summary).

    Selection only — the assembler budgets and orders. Every DB query is scoped to
    ``subject_id`` (+ ``tenant_id`` when given); RLS is never relied upon.

    Args:
        session: Async DB session.
        subject_id: The memory subject (app-level isolation key; required).
        session_id: The current conversation thread.
        persona: Active persona (gates skill selection).
        query: The user's query (drives fact/episodic relevance + skill keywords).
        query_vec: The recall-comparable query embedding, or ``None`` (recency-only facts).
        config: Recall fan-outs, weights, and half-lives.
        tenant_id: Optional tenant scope.
        spec: The domain :class:`~aegis.memory.spec.MemorySpec`; defaults to the configured
            process-wide spec when ``None``.

    Returns:
        A :class:`RecallBundle` with each tier's selected items.
    """
    spec = resolve_spec(spec)
    raw_window = await load_raw_window(
        session,
        subject_id=subject_id,
        session_id=session_id,
        config=config,
        tenant_id=tenant_id,
    )
    facts = await _recall_facts(
        session,
        subject_id=subject_id,
        query_vec=query_vec,
        config=config,
        tenant_id=tenant_id,
    )
    profile_text = await _recall_profile(
        session, subject_id=subject_id, tenant_id=tenant_id, spec=spec
    )
    episodic = await _recall_episodic(
        session,
        subject_id=subject_id,
        query_vec=query_vec,
        config=config,
        tenant_id=tenant_id,
        raw_window=raw_window,
    )
    skills = _recall_skills(query, persona, config, spec)

    stmt = select(MemorySession).where(MemorySession.id == session_id)
    stmt = stmt.where(MemorySession.subject_id == subject_id)
    if tenant_id is not None:
        stmt = stmt.where(MemorySession.tenant_id == tenant_id)
    sess = (await session.execute(stmt)).scalars().first()
    running_summary = (sess.summary if sess is not None else None) or ""

    # Read-path frequency: durably record what was recalled so the composite's freq term
    # (config.w_freq) reflects genuinely often-recalled memories on later turns.
    await _bump_recall_access(session, facts=facts, episodic=episodic)

    return RecallBundle(
        profile_text=profile_text,
        facts=facts,
        episodic=episodic,
        skills=skills,
        running_summary=running_summary,
    )


__all__ = ["RecallBundle", "load_raw_window", "recall"]
