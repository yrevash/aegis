"""Baseline-vs-actual spend for ``GET /savings``.

Derived from the persisted, **tenant-scoped** ``usage_ledger`` table — never from the
gateway's since-boot process-global tally, which every tenant sharing a worker would
see the others' spend through. Each ledger row carries the **actual** cost and the
units needed to price a **baseline**: what those same tokens would have cost had the
call gone to the frontier generation model. ``saved_usd`` is exactly that measured gap
(``baseline − actual``), and it is attributable, in full, to **small-model routing**:
those are the only calls the ledger sees.

**Honesty note (no fabricated precision).** Semantic-retrieval-cache and answer-cache
hits also save money — but a cache hit *skips generation entirely*, so it never
reaches this ledger. Its win shows up as cache-hit rate, not as dollars here. Rather
than invent a per-hit price and double-count, this endpoint reports the two cache
sources at ``$0`` **in this figure** with an explanation that they save by bypassing
the model and are measured elsewhere. The headline ``saved_usd`` is therefore the
*conservative* number — the same framing the Overview dashboard already uses.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.gateway.llm import baseline_token_cost
from aegis.governance.enforcement import savings_buckets
from aegis.retrieval.types import AllTenants
from app.api.schemas import SavingsBreakdownRow, SavingsResponse

_NOTE = (
    "saved_usd is the measured gap between an all-frontier-model baseline and actual "
    "gateway spend, taken straight from the usage ledger — attributable in full to "
    "small-model routing, which is metered exactly. Semantic- and answer-cache hits "
    "also save, but a cache hit bypasses the model and never enters this ledger, so "
    "they are shown at $0 here (real, but not estimated with false precision or "
    "double-counted). This makes the headline figure conservative."
)


async def build_savings(scope: int | AllTenants | None = None) -> SavingsResponse:
    """Build the savings roll-up from the persisted usage ledger, for one scope.

    The scope is an :class:`~aegis.retrieval.types.AllTenants` **or** an ``int`` **or**
    ``None`` for exactly the reason that type exists: "a platform admin, so restrict
    nothing" and "this principal is bound to no tenant" are different facts, and
    collapsing both into ``None`` is what turns an unprivileged caller into a
    platform-wide read. The default here is therefore the *closed* one — an omitted
    scope reports zero, not everyone's spend — so forgetting to pass it cannot leak.

    Args:
        scope: ``ALL_TENANTS`` for the platform-wide figure (platform staff only), a
            tenant id for that tenant's own ledger, or ``None`` for an untenanted
            principal, who has no ledger and is honestly shown zeros.
    """
    if scope is None:
        buckets = {
            "token": {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0},
            "other": {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0},
        }
    else:
        buckets = await savings_buckets(
            None if isinstance(scope, AllTenants) else scope
        )
    tok, other = buckets["token"], buckets["other"]

    # Token work is priced against the frontier rate; non-token work (audio, images)
    # has no frontier alternative, so its baseline is floored at its own actual cost
    # and it books a zero saving rather than a fabricated negative one.
    baseline = baseline_token_cost(
        int(tok["prompt_tokens"]), int(tok["completion_tokens"])
    ) + max(
        baseline_token_cost(
            int(other["prompt_tokens"]), int(other["completion_tokens"])
        ),
        other["cost_usd"],
    )
    actual = tok["cost_usd"] + other["cost_usd"]
    saved = max(0.0, baseline - actual)
    saved_pct = (saved / baseline) if baseline > 0 else 0.0

    breakdown = [
        SavingsBreakdownRow(
            source="Small-model routing",
            saved_usd=round(saved, 6),
            explanation=(
                "Measured exactly at the gateway ledger: low-risk turns are routed to a "
                "small/cheap model instead of the frontier model. saved_usd is the gap "
                "between what those calls would have cost at the frontier rate "
                "(baseline) and what they actually cost."
            ),
        ),
        SavingsBreakdownRow(
            source="Semantic retrieval cache",
            saved_usd=0.0,
            explanation=(
                "Estimated separately, not in this dollar figure: a semantic cache hit "
                "reuses a prior retrieval/answer for a near-duplicate query, skipping "
                "generation entirely — so it never reaches the usage ledger this figure "
                "is built from. Its win surfaces as cache-hit rate, not here. Reported "
                "at $0 to avoid double-counting / false precision."
            ),
        ),
        SavingsBreakdownRow(
            source="Answer cache",
            saved_usd=0.0,
            explanation=(
                "Estimated separately, not in this dollar figure: an answer-cache hit "
                "returns a scope-partitioned prior answer with no model call at all — "
                "again invisible to the baseline-vs-actual ledger. Reported at $0 here "
                "for the same honesty reason as the semantic cache."
            ),
        ),
    ]

    return SavingsResponse(
        generated_at=datetime.now(UTC).isoformat(),
        baseline_cost_usd=round(baseline, 6),
        actual_cost_usd=round(actual, 6),
        saved_usd=round(saved, 6),
        saved_pct=round(saved_pct, 6),
        note=_NOTE,
        breakdown=breakdown,
    )
