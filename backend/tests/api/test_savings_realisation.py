"""``GET /savings`` may only call a gap "saved" when a cheaper model actually served.

The savings figure is ``baseline − actual``. That subtraction describes *small-model
routing* only while the roles resolve to different deployments: a role is priced from
its own band, so pointing ``MODEL_CHEAP`` and ``MODEL_GENERATION`` at one deployment
makes the gap two price bands for the **same model**. Nothing cheaper answered
anything, and reporting the difference as money saved claims a mechanism that did not
run — which on this deployment is not hypothetical, it is the configuration.

These tests pin the two directions of that decision and the boundary between them:

* one observed deployment, and it is the baseline's  → ``saved_usd == 0``, the figure
  moves to ``projected_usd``, and ``routing_realised`` is ``False``;
* a second deployment observed in the ledger        → the same gap is ``saved_usd``,
  and ``projected_usd`` is zero.

The decision is driven from ``usage_ledger`` rather than from the routing table on
purpose, so the third test points a role at a deployment that never serves a call and
asserts the answer still follows the spend. Embeddings are checked too: they are
token-billed and so share a bucket with chat, but no frontier *chat* model is an
alternative way to embed, and pricing their tokens at the chat baseline books a
saving against a choice nobody could have made.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from app.api.schemas import Role
from app.data import Tenant, UsageLedger, User, get_sessionmaker
from app.platform.savings import build_savings

pytestmark = pytest.mark.asyncio

#: The single deployment every routable role points at in the tests below — the
#: shape a one-deployment gateway forces.
ONE_MODEL = "solo-deployment"
#: A genuinely different deployment, to prove the realised branch still fires.
OTHER_MODEL = "cheaper-deployment"


@pytest.fixture(autouse=True)
def one_deployment_fleet(monkeypatch):
    """Pin every routable role to one deployment, and the embedder to another."""
    for role in ("GENERATION", "CHEAP", "REASONING", "VISION"):
        monkeypatch.setenv(f"MODEL_{role}", ONE_MODEL)
    monkeypatch.setenv("MODEL_EMBEDDING", "embedder")
    monkeypatch.delenv("GATEWAY_BASELINE_ROLE", raising=False)


@pytest_asyncio.fixture
async def tenant(db):
    """One tenant and user for the ledger rows to attribute to."""
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Tenant One"),
                User(id=2, username="member", role=Role.CLIENT, tenant_id=1),
            ]
        )
        await session.commit()
    return db


async def _ledger(*rows: UsageLedger) -> None:
    async with get_sessionmaker()() as session:
        session.add_all(rows)
        await session.commit()


def _call(model: str, *, prompt: int = 100_000, completion: int = 10_000, cost: float = 0.05):
    """One token-billed ledger row served by ``model``."""
    return UsageLedger(
        tenant_id=1,
        user_id=2,
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        cost_usd=cost,
    )


async def test_one_deployment_fleet_books_zero_saved_and_reports_a_projection(tenant):
    """The same model on both sides of the subtraction is not a saving."""
    await _ledger(_call(ONE_MODEL), _call(ONE_MODEL))

    result = await build_savings(1)

    assert result.routing_realised is False
    assert result.models_observed == [ONE_MODEL]
    assert result.baseline_model == ONE_MODEL
    # The gap is real arithmetic and is still reported — just never as money saved.
    assert result.saved_usd == 0.0
    assert result.saved_pct == 0.0
    assert result.projected_usd > 0.0
    assert result.baseline_cost_usd > result.actual_cost_usd

    routing_row = next(r for r in result.breakdown if r.source == "Small-model routing")
    assert routing_row.saved_usd == 0.0
    assert "projected_usd" in routing_row.explanation


async def test_a_second_deployment_in_the_ledger_makes_the_gap_a_real_saving(tenant):
    """When a model other than the baseline's served work, the gap is banked."""
    await _ledger(_call(ONE_MODEL), _call(OTHER_MODEL))

    result = await build_savings(1)

    assert result.routing_realised is True
    assert result.models_observed == sorted([ONE_MODEL, OTHER_MODEL])
    assert result.saved_usd > 0.0
    assert result.projected_usd == 0.0
    assert result.saved_usd == pytest.approx(
        result.baseline_cost_usd - result.actual_cost_usd
    )


async def test_the_verdict_follows_the_ledger_not_the_routing_table(tenant, monkeypatch):
    """A role configured to a deployment that never serves a call proves nothing.

    This is the failure this whole split exists to prevent: a fleet mid-migration
    advertises a second model in ``MODEL_*``, every call still falls back to the one
    that works, and a config-derived check would report a saving that never happened.
    """
    monkeypatch.setenv("MODEL_CHEAP", "configured-but-never-used")
    await _ledger(_call(ONE_MODEL), _call(ONE_MODEL))

    result = await build_savings(1)

    assert result.routing_realised is False
    assert result.models_observed == [ONE_MODEL]
    assert result.saved_usd == 0.0


async def test_embedding_spend_is_counted_as_cost_but_never_as_a_saving(tenant):
    """Embedding tokens have no frontier chat alternative, so they book no gap.

    They must still appear in ``actual_cost_usd``: the money was spent. What they may
    not do is inflate the baseline by pretending a chat model could have embedded.
    """
    await _ledger(_call(OTHER_MODEL))
    baseline_without = (await build_savings(1)).baseline_cost_usd

    await _ledger(_call("embedder", prompt=500_000, completion=0, cost=0.01))
    after = await build_savings(1)

    # The embedder never enters the routing verdict …
    assert "embedder" not in after.models_observed
    # … and adds only its own cost to the baseline, not a chat-rate valuation of it.
    assert after.baseline_cost_usd == pytest.approx(baseline_without + 0.01, abs=1e-6)
    assert after.actual_cost_usd == pytest.approx(0.06, abs=1e-6)


async def test_an_untenanted_principal_still_reports_honest_zeros(tenant):
    """No scope means no ledger, and every figure is zero rather than everyone's."""
    await _ledger(_call(ONE_MODEL))

    result = await build_savings(None)

    assert result.saved_usd == 0.0
    assert result.projected_usd == 0.0
    assert result.actual_cost_usd == 0.0
    assert result.models_observed == []
