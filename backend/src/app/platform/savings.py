"""Baseline-vs-actual spend for ``GET /savings``.

Derived from the real gateway usage ledger (:func:`app.core.llm.usage_tally`).
The ledger accumulates, per real chat completion, both the **actual** cost and a
**baseline** cost — what the same tokens would have cost had every call gone to the
frontier generation model. ``saved_usd`` is exactly that measured gap
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

from app.api.schemas import SavingsBreakdownRow, SavingsResponse
from app.core.llm import usage_tally

_NOTE = (
    "saved_usd is the measured gap between an all-frontier-model baseline and actual "
    "gateway spend, taken straight from the usage ledger — attributable in full to "
    "small-model routing, which is metered exactly. Semantic- and answer-cache hits "
    "also save, but a cache hit bypasses the model and never enters this ledger, so "
    "they are shown at $0 here (real, but not estimated with false precision or "
    "double-counted). This makes the headline figure conservative."
)


def build_savings() -> SavingsResponse:
    """Build the savings roll-up from the live usage ledger."""
    tally = usage_tally()
    baseline = float(tally["baseline_cost_usd"])
    actual = float(tally["total_cost_usd"])
    saved = float(tally["cost_saved_usd"])  # == max(0, baseline - actual)
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
