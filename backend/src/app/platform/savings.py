"""Baseline-vs-actual spend for ``GET /savings``.

Derived from the persisted, **tenant-scoped** ``usage_ledger`` table — never from the
gateway's since-boot process-global tally, which every tenant sharing a worker would
see the others' spend through. Each ledger row carries the **actual** cost and the
units needed to price a **baseline**: what those same tokens would have cost had the
call gone to the frontier generation model.

**The headline number is only a saving if a cheaper model actually answered.**
``baseline − actual`` is arithmetic; calling the difference *small-model routing*
is a claim about mechanism, and the two come apart. The router picks a role per
turn — that decision is real and logged — but a role is priced from its own band,
so pointing every routable role at one deployment makes the subtraction compare two
price bands for the **same model**. Nothing cheaper served anything; no money moved.
On a single-deployment gateway that is not a corner case, it is the configuration.

So this module resolves the question from the ledger rather than asserting it:

* :func:`~aegis.governance.enforcement.token_usage_by_model` names the deployment
  that answered each group of calls, and
  :func:`~aegis.gateway.routing.routing_realisation` decides — from those observed
  deployments, not from the routing table — whether any of them differs from the one
  pricing the baseline.
* When one does, the saving is **realised** and reported as money.
* When none does, ``saved_usd`` is **zero** and the same figure is reported separately
  as ``projected_usd``: what these role assignments would save on a fleet that has
  the models to route between. A projection is worth showing; it is not worth
  showing under the word "saved".

Restoring a multi-deployment fleet flips this back with no code change, because both
sides are read at request time.

**Honesty note (no fabricated precision).** Semantic-retrieval-cache and answer-cache
hits also save money — but a cache hit *skips generation entirely*, so it never
reaches this ledger. Its win shows up as cache-hit rate, not as dollars here. Rather
than invent a per-hit price and double-count, this endpoint reports the two cache
sources at ``$0`` **in this figure** with an explanation that they save by bypassing
the model and are measured elsewhere.

Embeddings and transcription are excluded from the baseline for the same reason audio
and images always were: a frontier *chat* model is not an alternative way to embed a
chunk, so pricing those tokens at the chat rate books a saving against a choice nobody
could have made. Their spend still counts in ``actual_cost_usd``, because it is real
money; it just contributes nothing to the gap.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.gateway.llm import baseline_token_cost
from aegis.gateway.routing import nonroutable_deployments, routing_realisation
from aegis.governance.enforcement import savings_buckets, token_usage_by_model
from aegis.retrieval.types import AllTenants

from app.api.schemas import SavingsBreakdownRow, SavingsResponse

_ZERO_BUCKET = {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0}

_REALISED_NOTE = (
    "saved_usd is the measured gap between an all-frontier-model baseline and actual "
    "gateway spend, taken straight from the usage ledger. It is reported as realised "
    "because the ledger shows calls answered by a deployment other than the one "
    "pricing the baseline — a cheaper model genuinely served work. Semantic- and "
    "answer-cache hits also save, but a cache hit bypasses the model and never enters "
    "this ledger, so they are shown at $0 here (real, but not estimated with false "
    "precision or double-counted). This makes the headline figure conservative."
)


def _projected_note(realisation: object, served: str) -> str:
    """Explain, in the endpoint's own body, why the saving is projected and not banked."""
    return (
        f"saved_usd is $0 because no saving was realised on this fleet. Every priced "
        f"call was answered by {served}, which is also the deployment the frontier "
        f"baseline is priced from — so the gap below compares two price bands for the "
        f"same model, not a cheaper model against a dearer one. The router's per-turn "
        f"role assignments are real and logged; what is missing is a second deployment "
        f"to route to. projected_usd is what those same assignments would save on a "
        f"fleet that has one, priced at the published bands. Cache hits are excluded "
        f"here for the separate reason that they never reach this ledger."
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
        buckets = {"token": dict(_ZERO_BUCKET), "other": dict(_ZERO_BUCKET)}
        by_model: dict[str, dict[str, float]] = {}
    else:
        tenant = None if isinstance(scope, AllTenants) else scope
        buckets = await savings_buckets(tenant)
        by_model = await token_usage_by_model(tenant)

    # Token work splits again, by whether a frontier chat model was ever an
    # alternative. Embeddings and transcription were never routable, so they are
    # priced like audio and images: floored at their own cost, booking no saving.
    excluded = nonroutable_deployments()
    routable = {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0}
    unroutable = {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0}
    for model, usage in by_model.items():
        target = unroutable if model in excluded else routable
        for key in target:
            target[key] += usage[key]

    # With no per-model detail (an untenanted principal, or a ledger written before
    # the model column carried a value) fall back to the whole token bucket, which is
    # what this endpoint priced before the split existed.
    if not by_model:
        routable = dict(buckets["token"])

    other = buckets["other"]
    served = sorted(m for m in by_model if m not in excluded)
    realisation = routing_realisation(served)

    # Token work is priced against the frontier rate. Work with no frontier
    # alternative — audio, images, embeddings — is baselined at its own actual cost,
    # so it books a zero gap in either direction.
    #
    # Its own cost, and specifically *not* ``max(frontier_value, cost)``, which is
    # what this line used to say. That max reads as a safe floor and is not one: an
    # embedding pass is token-billed, so ``baseline_token_cost`` happily values half
    # a million embedding tokens at the chat rate, the max picks that far larger
    # number, and the endpoint books a saving for not having embedded with a chat
    # model — a choice that was never available. Having no alternative means the
    # baseline *is* the actual, which is the whole reason this bucket is separate.
    baseline = (
        baseline_token_cost(
            int(routable["prompt_tokens"]), int(routable["completion_tokens"])
        )
        + unroutable["cost_usd"]
        + other["cost_usd"]
    )
    actual = routable["cost_usd"] + unroutable["cost_usd"] + other["cost_usd"]
    gap = max(0.0, baseline - actual)

    realised = realisation.realised
    saved = gap if realised else 0.0
    projected = 0.0 if realised else gap
    saved_pct = (saved / baseline) if baseline > 0 else 0.0

    if realised:
        note = _REALISED_NOTE
        routing_explanation = (
            "Measured exactly at the gateway ledger: low-risk turns are routed to a "
            "small/cheap model instead of the frontier model. saved_usd is the gap "
            "between what those calls would have cost at the frontier rate "
            "(baseline) and what they actually cost. Deployments observed serving "
            f"this work: {', '.join(served) or 'none'}."
        )
    else:
        one = served[0] if served else realisation.baseline_deployment
        note = _projected_note(realisation, one)
        routing_explanation = (
            f"Not realised on this fleet, so booked at $0. Every priced call was "
            f"answered by {one}, the same deployment the baseline is priced from, so "
            f"the gap is two price bands for one model rather than a cheaper model "
            f"doing the work. The routing decision itself runs on every turn; it "
            f"currently has one deployment to choose between. See projected_usd."
        )

    breakdown = [
        SavingsBreakdownRow(
            source="Small-model routing",
            saved_usd=round(saved, 6),
            explanation=routing_explanation,
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
        projected_usd=round(projected, 6),
        routing_realised=realised,
        models_observed=served,
        baseline_model=realisation.baseline_deployment,
        note=note,
        breakdown=breakdown,
    )
