"""Memory READ path — per-tier recall (semantic / profile / episodic / procedural).

This is the first half of context engineering (``docs/architecture/memory-spec.md`` §B): gather the
raw material the working-memory assembler (:mod:`aegis.memory.working`) later budgets and
orders. It does **selection**, not layout — no token budget, no spotlighting here.

**Isolation is app-level first (BLOCKER 2).** Every query filters ``subject_id`` **and**
``tenant_id`` in its ``WHERE`` clause — the primary, NULL-safe, dialect-independent
isolator. Postgres RLS is only an additive belt and is never relied upon. The tenant
predicate is NULL-*symmetric*: ``tenant_id=None`` means the **null-tenant scope**
(``tenant_id IS NULL``), never "any tenant". Emitting the predicate only when a tenant is
supplied would let an unscoped recall return a tenant's row whenever a ``subject_id``
collides across scopes — and recall's output is injected verbatim into the prompt.

**Semantic vectors via the embedded vector store.** All vector recall goes through
:func:`aegis.memory.vector_ops.topk_by_cosine`, now a real ANN search (subject/
tenant payload-filtered, then joined back to the authoritative SQL row) rather than an
in-Python cosine scan. When ``query_vec`` is ``None`` (e.g. an exact-cache hit never
computed one, or a lite 256-dim vector is not recall-comparable) facts fall back to
recency-only SQL.

The domain seam (profile rendering, skill selection) is the injected
:class:`~aegis.memory.spec.MemorySpec`; pass ``spec=`` or configure a process-wide default.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.memory.config import MemoryConfig
from aegis.memory.scope import bind_memory_scope
from aegis.memory.scoring import RecallCandidate, rank_top
from aegis.memory.spec import MemorySpec, resolve_spec
from aegis.memory.stores import MemoryFact, MemoryMessage, MemoryProfile, MemorySession
from aegis.memory.vector_ops import topk_by_cosine
from aegis.retrieval.fusion import RankedList, reciprocal_rank_fusion
from aegis.retrieval.models import Candidate
from aegis.retrieval.types import RetrievalOrigin

logger = logging.getLogger(__name__)


def _tenant_clause(model, tenant_id: int | None):  # noqa: ANN001, ANN202 - mapped class
    """The NULL-symmetric tenant predicate for ``model`` (``IS NULL`` for the null tenant).

    Single-sourced so no recall query can drift back to the leaky
    ``if tenant_id is not None`` form, which silently degrades to "any tenant".
    """
    if tenant_id is None:
        return model.tenant_id.is_(None)
    return model.tenant_id == tenant_id


@dataclass
class RecallBundle:
    """The raw recalled material for one turn, before budgeting/ordering.

    Consumed by :func:`aegis.memory.working.assemble_working_memory`. Every field is
    already subject/tenant-scoped; the assembler only budgets, dedups, and orders it.

    Attributes:
        profile_text: The rendered "human block" (``spec.render_profile``), or "".
        facts: Ranked durable facts (Generative-Agents composite, or recency-only).
        episodic: RRF-fused earlier turns, EXCLUDING those already in the raw window.
        skills: **Tier 1 only** — ``(name, one-line card)`` pairs for the skills in
            force. The body is never here; it arrives through a ``load_skill`` tool
            call, which is what makes the use of a skill visible in the trace.
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
    stmt = select(MemoryMessage).where(
        MemoryMessage.subject_id == subject_id,
        _tenant_clause(MemoryMessage, tenant_id),
    )
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
    """Semantic facts: ANN top-k over VALID facts → composite → top-n.

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
    stmt = select(MemoryFact).where(
        MemoryFact.subject_id == subject_id, _tenant_clause(MemoryFact, tenant_id)
    )
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
    """Load the subject's profile row and render it as the human block (or "").

    The tenant predicate is NULL-symmetric (and matches the write side in
    :func:`aegis.memory.consolidate._update_profile`): the same ``subject_id`` may legally
    hold one profile per tenant *plus* a null-tenant one, so scoping only "when a tenant is
    given" would let a null-tenant recall pick an arbitrary tenant's profile — which is
    rendered straight into the prompt's human block.
    """
    stmt = select(MemoryProfile).where(
        MemoryProfile.subject_id == subject_id,
        _tenant_clause(MemoryProfile, tenant_id),
    )
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
    """Hybrid episodic recall: RRF-fuse (recency ∪ vector top-k) over eligible turns.

    Episodic recall exists to surface turns the raw window would MISS, so both fused lists
    are drawn from the turns outside that window:

    * the **recency list** — the subject's newest turns that the raw window does not
      already carry, tagged ``BM25`` because :class:`RetrievalOrigin` has no "recency"
      member (a known label compromise, not a second signal);
    * the **vector list** — the subject-wide semantic top-k, tagged ``VECTOR``.

    Building the recency list from the raw window itself made the fusion inert: every one
    of its ids is dropped by the dedup filter below, so RRF ranked only items guaranteed to
    be discarded and the surviving order collapsed to pure vector rank — the recency signal
    never reached the output, and a recent-but-unembedded turn could never be recalled at
    all. Ranking the *eligible* turns instead is what makes this genuinely hybrid.
    """
    raw_ids = {m.id for m in raw_window}
    msg_by_id: dict[int, MemoryMessage] = {m.id: m for m in raw_window}

    # Recency list (newest first) — the subject's most recent turns BEYOND the raw window,
    # i.e. exactly the population episodic recall is allowed to contribute from.
    rec_stmt = select(MemoryMessage).where(
        MemoryMessage.subject_id == subject_id,
        _tenant_clause(MemoryMessage, tenant_id),
    )
    if raw_ids:
        rec_stmt = rec_stmt.where(MemoryMessage.id.not_in(raw_ids))
    rec_stmt = rec_stmt.order_by(
        MemoryMessage.created_at.desc(), MemoryMessage.id.desc()
    ).limit(config.k_epi)
    recency_rows = list((await session.execute(rec_stmt)).scalars().all())
    for msg in recency_rows:
        msg_by_id[msg.id] = msg
    recency_list = RankedList(
        origins=(RetrievalOrigin.BM25,),
        candidates=[Candidate(id=str(m.id), text=m.content) for m in recency_rows],
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


async def _recall_skills(
    session: AsyncSession,
    query: str,
    config: MemoryConfig,
    *,
    tenant_id: int | None,
    user_id: int | None,
) -> list[tuple[str, str]]:
    """Return tier 1 of progressive disclosure: one card per skill in force.

    **A body is never returned here.** Until §10.2 this function globbed a directory
    named by ``memory_spec.SKILLS_DIR``, read whole Markdown files and handed them to
    the assembler, which pasted them into the prompt. That burned context on skills the
    query never needed, could not be scoped to a tenant or a user, could not be authored
    from anywhere, and — the part that matters for a glass-box product — left no trace
    that a skill had been used at all: a turn with a skill and a turn without one looked
    identical.

    Now the prompt carries only ``name (scope): description`` for each skill in force,
    and the body arrives, if the model decides it needs one, through a **real
    ``load_skill`` tool call** that appears in the trace like every other action.

    Resolution is :func:`aegis.skills.store.resolve_skills`, which reads the
    ``skills.enabled`` catalogue key through the Phase 3 settings resolver — so the
    scoping is the platform's one scoping mechanism and not a second one, and a tenant
    cannot switch off a platform safety skill because a union has no way to express it.

    Args:
        session: The async session (already tenant-scoped by the caller).
        query: This turn's question — orders the cards, never filters them.
        config: Recall fan-outs; ``n_skill`` caps how many cards are offered.
        tenant_id: The tenant to resolve for.
        user_id: The user to resolve for.

    Returns:
        ``(name, card)`` pairs. Best-effort: a skills store that cannot be read degrades
        to no skills rather than failing the turn, exactly like every other recall tier.
    """
    try:
        from aegis.skills.store import resolve_skills
    except ImportError:  # pragma: no cover - the data extra is not installed
        return []
    try:
        resolved = await resolve_skills(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            query=query,
            limit=config.n_skill,
        )
    except Exception:  # noqa: BLE001 - a skills outage must not fail a turn
        logger.warning("Skills unavailable this turn; continuing without them",
                       exc_info=True)
        return []
    return [(skill.name, skill.card()) for skill in resolved]


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
    user_id: int | None = None,
    spec: MemorySpec | None = None,
) -> RecallBundle:
    """Gather all recall tiers for one turn (facts, profile, episodic, skills, summary).

    Selection only — the assembler budgets and orders. Every DB query is scoped to
    ``subject_id`` **and** ``tenant_id`` (NULL-symmetric — ``None`` is the null-tenant
    scope, not a wildcard); RLS is never relied upon.

    Args:
        session: Async DB session.
        subject_id: The memory subject (app-level isolation key; required).
        session_id: The current conversation thread.
        persona: Active persona (gates profile rendering).
        query: The user's query (drives fact/episodic relevance + skill keywords).
        query_vec: The recall-comparable query embedding, or ``None`` (recency-only facts).
        config: Recall fan-outs, weights, and half-lives.
        tenant_id: Optional tenant scope.
        user_id: The caller's user id, for the **user** layer of skill resolution. A
            skill authored by one person is theirs, so without this the user layer
            cannot be reached and a person's own skills silently never apply.
        spec: The domain :class:`~aegis.memory.spec.MemorySpec`; defaults to the configured
            process-wide spec when ``None``.

    Returns:
        A :class:`RecallBundle` with each tier's selected items.
    """
    spec = resolve_spec(spec)
    # Recall COMMITS, in ``_bump_recall_access`` below, and both this session and its
    # caller keep working afterwards (the assembler re-reads the raw window on it). The
    # tenant GUC is transaction-local, so a one-shot ``set_tenant_scope`` by the caller
    # is gone by then and every later statement runs unscoped — zero rows under
    # ``RLS_FAIL_CLOSED=true``. Binding the *session* makes the scope survive the commit.
    await bind_memory_scope(session, tenant_id)
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
    skills = await _recall_skills(
        session, query, config, tenant_id=tenant_id, user_id=user_id
    )

    stmt = select(MemorySession).where(
        MemorySession.id == session_id,
        MemorySession.subject_id == subject_id,
        _tenant_clause(MemorySession, tenant_id),
    )
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
