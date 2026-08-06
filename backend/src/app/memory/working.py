"""Memory READ path — working-memory assembly under a hard token budget.

The second half of context engineering (``docs/MEMORY_SPEC.md`` §B). Given the recalled
material from :func:`app.memory.recall.recall`, this lays it out as one **extra system
context block** — the graph adds the real system prompt and the user query separately, so
neither is in this block. Two properties are load-bearing:

* **Lost-in-the-middle ordering:** durable, high-value material at the TOP (profile +
  facts, skills, summary), the bulky/lossy episodic tier in the tolerant MIDDLE, and the
  verbatim recent turns at the BOTTOM (nearest the query the graph appends).
* **Deterministic budget (BLOCKER 3 — no LLM on the read path):** greedy-fill each tier
  up to ``per_tier_caps[tier] * budget`` in priority order, cross-tier dedup via
  ``seen_keys``, and when still over budget **evict** the lowest-priority / oldest raw
  turns. Never a model call — regenerating a summary is a background consolidation job.

The untrusted episodic tier is wrapped with Azure spotlighting so recalled turns are
marked as data, not instructions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.memory.config import MemoryConfig
from app.memory.recall import RecallBundle, load_raw_window, recall
from app.memory.scoring import RecallCandidate
from app.memory.stores import MemoryMessage
from app.memory.tokens import count_tokens
from app.retrieval.spotlight import spotlight, spotlight_system_instruction

_PROFILE_HEADER = "## Known profile"
_FACTS_HEADER = "## Durable facts"
_SKILLS_HEADER = "## Applicable skills"
_SUMMARY_HEADER = "## Conversation summary so far"
_EPISODIC_HEADER = "## Relevant earlier context (reference data)"
_RAW_HEADER = "## Recent conversation"

#: Layout order (top → bottom): high value at the START and END, bulk in the MIDDLE.
_LAYOUT = ("profile", "facts", "skills", "summary", "episodic", "raw")

#: Eviction order (first dropped → last): shed the cheap/recoverable bottom first.
_EVICT_ORDER = ("raw", "episodic", "summary", "skills", "facts", "profile")


@dataclass
class AssembledMemory:
    """The assembled working-memory block and what actually made it in.

    Attributes:
        text: The context block to inject (``""`` when nothing was recalled/fit).
        recalled_fact_ids: Ids of facts injected (for access-count bump + audit).
        recalled_message_ids: Ids of episodic + raw messages injected.
        tokens_used: Token count of ``text`` (always ``<= budget``).
    """

    text: str = ""
    recalled_fact_ids: list[int] = field(default_factory=list)
    recalled_message_ids: list[int] = field(default_factory=list)
    tokens_used: int = 0


@dataclass
class _Section:
    """One assembled tier: a header plus kept ``(key, rendered_text, source_id)`` items."""

    header: str
    items: list[tuple[str, str, int | None]] = field(default_factory=list)
    evict_from_front: bool = False  # raw turns: drop the OLDEST (front) first


def _render(sections: dict[str, _Section]) -> str:
    """Join non-empty sections in layout order into the final block."""
    blocks: list[str] = []
    for name in _LAYOUT:
        sec = sections[name]
        if not sec.items:
            continue
        body = "\n".join(text for _, text, _ in sec.items)
        blocks.append(f"{sec.header}\n{body}")
    return "\n\n".join(blocks)


def _fill(
    section: _Section,
    items: list[tuple[str, str, int | None]],
    *,
    tier_cap_tokens: int,
    budget: int,
    running_tokens: int,
    seen_keys: set[str],
) -> int:
    """Greedily add ``items`` to ``section`` within its tier cap and the global budget.

    Cross-tier dedup: an item whose ``key`` is already in ``seen_keys`` is skipped (never
    re-injected). Returns the token cost of the resulting section block (0 if empty).
    """
    for key, text, source_id in items:
        if key in seen_keys:
            continue
        trial = section.items + [(key, text, source_id)]
        body = "\n".join(t for _, t, _ in trial)
        block_tokens = count_tokens(f"{section.header}\n{body}")
        if block_tokens > tier_cap_tokens:
            continue  # this item overflows the tier cap — try later (smaller) items
        if running_tokens + block_tokens > budget:
            continue  # would blow the global budget — try later items
        section.items = trial
        seen_keys.add(key)
    if not section.items:
        return 0
    body = "\n".join(t for _, t, _ in section.items)
    return count_tokens(f"{section.header}\n{body}")


def build_working_text(
    bundle: RecallBundle,
    raw_turns: list[MemoryMessage],
    *,
    query: str,
    config: MemoryConfig,
) -> AssembledMemory:
    """Pure assembler: turn recalled material into the budgeted, ordered context block.

    Separated from :func:`assemble_working_memory` (which does the I/O) so the budget and
    ordering logic is deterministic and unit-testable without a database.

    Args:
        bundle: The recalled tiers (facts, profile, episodic, skills, summary).
        raw_turns: The verbatim recent turns (oldest-first) — the bottom tier.
        query: The user query (its tokens are subtracted from the budget; it is NOT in
            this block — the graph appends it separately).
        config: Budget cap, answer reserve, and per-tier fraction ceilings.

    Returns:
        The :class:`AssembledMemory` (``text`` guaranteed ``<= budget`` tokens).
    """
    budget = config.ctx_token_cap - config.answer_reserve - count_tokens(query)
    if budget <= 0:
        return AssembledMemory()

    caps = config.per_tier_caps

    def cap_tokens(tier: str) -> int:
        return int(caps.get(tier, 1.0) * budget)

    # The episodic tier is untrusted → its header carries the spotlight instruction, so
    # the instruction is BUDGETED with the tier and disappears if the tier is evicted.
    episodic_header = f"{spotlight_system_instruction()}\n{_EPISODIC_HEADER}"
    sections = {name: _Section(header) for name, header in (
        ("profile", _PROFILE_HEADER),
        ("facts", _FACTS_HEADER),
        ("skills", _SKILLS_HEADER),
        ("summary", _SUMMARY_HEADER),
        ("episodic", episodic_header),
        ("raw", _RAW_HEADER),
    )}
    sections["raw"].evict_from_front = True

    seen_keys: set[str] = set()
    running = 0

    # 1a. Profile (pinned human block — no per-item key, injected whole if it fits).
    if bundle.profile_text.strip():
        running += _fill(
            sections["profile"],
            [("profile:block", bundle.profile_text.strip(), None)],
            tier_cap_tokens=cap_tokens("profile"),
            budget=budget,
            running_tokens=running,
            seen_keys=seen_keys,
        )

    # 1b. Durable facts (keyed by subject|predicate for cross-tier dedup).
    running += _fill(
        sections["facts"],
        [_fact_item(c) for c in bundle.facts],
        tier_cap_tokens=cap_tokens("facts"),
        budget=budget,
        running_tokens=running,
        seen_keys=seen_keys,
    )

    # 2. Skills.
    running += _fill(
        sections["skills"],
        [(f"skill:{name}", f"### {name}\n{text.strip()}", None) for name, text in bundle.skills],
        tier_cap_tokens=cap_tokens("skills"),
        budget=budget,
        running_tokens=running,
        seen_keys=seen_keys,
    )

    # 3. Running summary.
    if bundle.running_summary.strip():
        running += _fill(
            sections["summary"],
            [("summary:block", bundle.running_summary.strip(), None)],
            tier_cap_tokens=cap_tokens("summary"),
            budget=budget,
            running_tokens=running,
            seen_keys=seen_keys,
        )

    # 4. Episodic recall (MIDDLE, untrusted → spotlit; keyed by message id).
    running += _fill(
        sections["episodic"],
        [_episodic_item(c) for c in bundle.episodic],
        tier_cap_tokens=cap_tokens("episodic"),
        budget=budget,
        running_tokens=running,
        seen_keys=seen_keys,
    )

    # 5. Raw window (BOTTOM, verbatim; keyed by message id so it dedups vs episodic).
    running += _fill(
        sections["raw"],
        [_raw_item(m) for m in raw_turns],
        tier_cap_tokens=cap_tokens("raw"),
        budget=budget,
        running_tokens=running,
        seen_keys=seen_keys,
    )

    # Final safety valve (non-LLM): join separators can nudge the total over budget —
    # evict the lowest-priority / oldest items until the assembled text fits.
    text = _render(sections)
    tokens_used = count_tokens(text)
    while tokens_used > budget and _evict_one(sections):
        text = _render(sections)
        tokens_used = count_tokens(text)

    if not text:
        return AssembledMemory()

    fact_ids = [sid for _, _, sid in sections["facts"].items if sid is not None]
    message_ids = [
        sid
        for name in ("episodic", "raw")
        for _, _, sid in sections[name].items
        if sid is not None
    ]
    return AssembledMemory(
        text=text,
        recalled_fact_ids=fact_ids,
        recalled_message_ids=message_ids,
        tokens_used=tokens_used,
    )


def _evict_one(sections: dict[str, _Section]) -> bool:
    """Drop one lowest-priority item (oldest raw turn first). Returns False if empty."""
    for name in _EVICT_ORDER:
        sec = sections[name]
        if sec.items:
            if sec.evict_from_front:
                sec.items.pop(0)  # oldest raw turn
            else:
                sec.items.pop()  # lowest-ranked item of this tier
            return True
    return False


def _fact_item(c: RecallCandidate) -> tuple[str, str, int | None]:
    fid = getattr(c.payload, "id", None)
    return (c.key, f"- {c.text}", fid)


def _episodic_item(c: RecallCandidate) -> tuple[str, str, int | None]:
    mid = getattr(c.payload, "id", None)
    return (c.key, spotlight(c.text), mid)


def _raw_item(m: MemoryMessage) -> tuple[str, str, int | None]:
    return (f"msg:{m.id}", f"{m.role}: {m.content}", m.id)


async def assemble_working_memory(
    session: AsyncSession,
    *,
    subject_id: str,
    session_id: str,
    persona: str | None,
    query: str,
    query_vec: list[float] | None,
    config: MemoryConfig,
    tenant_id: int | None = None,
) -> AssembledMemory:
    """Recall + assemble the working-memory block for one turn (the graph's entry point).

    Runs :func:`app.memory.recall.recall`, fetches the verbatim raw window, then delegates
    to the pure :func:`build_working_text`. Never calls a model (BLOCKER 3). An empty
    recall yields ``text == ""`` so the single-shot path injects nothing.

    Args:
        session: Async DB session.
        subject_id: Memory subject (app-level isolation key).
        session_id: Current conversation thread.
        persona: Active persona (gates skills).
        query: The user query (budget input; not injected here).
        query_vec: Recall-comparable query embedding, or ``None``.
        config: Recall + budget parameters.
        tenant_id: Optional tenant scope.

    Returns:
        The assembled :class:`AssembledMemory`.
    """
    bundle = await recall(
        session,
        subject_id=subject_id,
        session_id=session_id,
        persona=persona,
        query=query,
        query_vec=query_vec,
        config=config,
        tenant_id=tenant_id,
    )
    raw_turns = await load_raw_window(
        session,
        subject_id=subject_id,
        session_id=session_id,
        config=config,
        tenant_id=tenant_id,
    )
    return build_working_text(bundle, raw_turns, query=query, config=config)


__all__ = ["AssembledMemory", "assemble_working_memory", "build_working_text"]
