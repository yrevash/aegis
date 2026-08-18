"""The `/forecast/...` HTTP surface: RBAC, tenant isolation and honest refusals.

The expensive assertions (a real AutoARIMA/AutoETS backtest takes seconds) are
concentrated in two tests that seed a full history once and check everything the
fitted response must carry. The rest of the file is fast: refusals, guards and
argument validation never reach the forecasting stack at all.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pgsupport
import pytest
from aegis.governance.models import (
    Budget,
    BudgetScope,
    BudgetWindow,
    Tenant,
    UsageLedger,
)
from tests.conftest import login_as

from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="u") -> dict[str, str]:
    """Build a bearer header for a fine ``role`` (coarse is derived on the token)."""
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_history(sessionmaker, tenant_id: int, days: int) -> None:
    """Seed the owning tenant plus ``days`` daily ledger rows with trend, cycle and noise.

    The ``Tenant`` row is not decoration: ``usage_ledger.tenant_id`` is a foreign key to
    ``tenants.id``, and the suite's former SQLite binding simply did not enforce it (SQLite
    ignores foreign keys unless ``PRAGMA foreign_keys=ON``). The history therefore used to
    be attributed to a tenant that did not exist, which is not a state the forecast surface
    should ever be asked to reason about.
    """
    import math
    import random

    from app.forecast.service import clear_cache

    clear_cache()
    rng = random.Random(7)
    start = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    async with sessionmaker() as session:
        await pgsupport.seed(
            session,
            Tenant(id=tenant_id, name=f"tenant-{tenant_id}"),
            *(
                UsageLedger(
                    tenant_id=tenant_id,
                    ts=start + timedelta(days=i, hours=9),
                    cost_usd=max(
                        4.0
                        + 0.05 * i
                        + 1.5 * math.sin(i * 2 * math.pi / 7)
                        + rng.gauss(0, 0.3),
                        0.1,
                    ),
                    model="gpt-small",
                    prompt_tokens=100,
                    completion_tokens=50,
                )
                for i in range(days)
            ),
        )
        await session.commit()


# ── Refusals (no fitting involved) ───────────────────────────────────────────


async def test_an_empty_ledger_refuses_with_the_arithmetic_not_an_error(client, db):
    r = await client.get("/forecast/usage", headers=await login_as(client, "admin"))
    assert r.status_code == 200, "a refusal is a result, not an HTTP failure"
    body = r.json()
    assert body["available"] is False
    assert body["forecast"] is None
    assert body["refusal"]["code"] == "insufficient_history"
    assert body["refusal"]["have"] < body["refusal"]["need"]
    assert body["refusal"]["reason"]


async def test_a_tenant_with_a_little_history_is_told_how_much_it_needs(client, db):
    await _seed_history(db, 1, days=9)
    r = await client.get(
        "/forecast/usage?tenant_id=1", headers=_headers(PLATFORM_ADMIN, username="a")
    )
    body = r.json()
    assert body["available"] is False
    assert body["refusal"]["code"] == "insufficient_history"
    assert body["refusal"]["have"] == 9
    assert body["refusal"]["need"] == 71  # 3 windows x h=14, + 29 to fit and calibrate


# ── RBAC + tenant isolation ──────────────────────────────────────────────────


async def test_usage_forecast_rejects_a_non_admin_role(client, db):
    r = await client.get("/forecast/usage", headers=await login_as(client, "client"))
    assert r.status_code == 403


async def test_budget_forecast_rejects_a_non_admin_role(client, db):
    r = await client.get("/forecast/budget", headers=await login_as(client, "client"))
    assert r.status_code == 403


async def test_forecast_requires_authentication(client, db):
    assert (await client.get("/forecast/usage")).status_code == 401
    assert (await client.get("/forecast/domain")).status_code == 401


async def test_a_tenant_admin_cannot_forecast_another_tenants_spend(client, db):
    headers = _headers(TENANT_ADMIN, tenant_id=1, user_id=1, username="ta")
    r = await client.get("/forecast/usage?tenant_id=2", headers=headers)
    assert r.status_code == 403
    r = await client.get("/forecast/budget?tenant_id=2", headers=headers)
    assert r.status_code == 403


async def test_an_unknown_metric_or_window_is_rejected(client, db):
    headers = await login_as(client, "admin")
    assert (await client.get("/forecast/usage?metric=vibes", headers=headers)).status_code == 400
    assert (await client.get("/forecast/budget?window=year", headers=headers)).status_code == 400


# ── The fitted path ──────────────────────────────────────────────────────────


async def test_a_seeded_tenant_gets_a_forecast_whose_coverage_is_measured(client, db):
    await _seed_history(db, 1, days=150)
    r = await client.get(
        "/forecast/usage?tenant_id=1&horizon=14",
        headers=_headers(PLATFORM_ADMIN, username="a"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True, body.get("refusal")
    fc = body["forecast"]

    assert fc["data_source"] == "usage_ledger"
    assert fc["freq"] == "D"
    assert len(fc["points"]) == 14
    assert [p["step"] for p in fc["points"]] == list(range(1, 15))
    assert all(p["lo"] <= p["point"] <= p["hi"] for p in fc["points"])

    # The band is labelled for what it is, and the coverage reported is the achieved
    # one — a separate field from the level that was requested.
    assert fc["interval_method"] == "conformal"
    assert "ConformalIntervals" in fc["interval_method_detail"]
    bt = fc["backtest"]
    assert bt["requested_coverage"] == 0.9
    assert 0.0 <= bt["empirical_coverage"] <= 1.0
    assert bt["n_points"] == bt["windows"] * bt["horizon"]
    assert bt["coverage_meets_request"] == (bt["empirical_coverage"] >= 0.9)
    assert bt["interval_method"] == "conformal"

    # The losing candidates are published so the selection is auditable.
    assert {c["model"] for c in fc["candidates"]} >= {"SeasonalNaive"}
    assert sum(c["selected"] for c in fc["candidates"]) == 1


async def test_the_budget_route_projects_the_burn_down_against_a_real_cap(client, db):
    await _seed_history(db, 1, days=150)
    async with db() as session:
        session.add(
            Budget(
                tenant_id=1,
                scope_type=BudgetScope.TENANT,
                scope_id=1,
                window=BudgetWindow.MONTH,
                usd_cap=25.0,
            )
        )
        await session.commit()

    r = await client.get(
        "/forecast/budget?tenant_id=1&window=month&horizon=14",
        headers=_headers(PLATFORM_ADMIN, username="a"),
    )
    body = r.json()
    assert body["available"] is True, body.get("refusal")
    burn = body["burndown"]
    assert burn["limit_usd"] == 25.0
    assert burn["window"] == "month"
    assert burn["scope"] == "tenant"
    assert burn["spent_usd"] > 0.0, "a month of seeded ledger has real spend in it"
    assert len(burn["points"]) == 14
    # Cumulative is monotone and starts from what was already spent.
    cumulative = [p["cumulative"] for p in burn["points"]]
    assert cumulative == sorted(cumulative)
    assert cumulative[0] > burn["spent_usd"]
    # A 25 USD monthly cap against ~5 USD/day is blown inside the horizon, with a date.
    assert burn["exhausted_within_horizon"] is True
    assert burn["exhaustion_ts"] is not None
    assert burn["headroom_usd"] < 0
    # The envelope on the cumulative total is explicitly NOT a coverage claim.
    assert burn["cumulative_bounds_are_calibrated"] is False


async def test_the_domain_forecast_reads_through_the_adapter_seam(client, db):
    r = await client.get("/forecast/domain?horizon=7", headers=await login_as(client, "client"))
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True, body.get("refusal")
    fc = body["forecast"]
    # `adapter` is the provenance signal: a synthetic domain series must never be
    # mistaken for live client data.
    assert fc["data_source"] == "adapter"
    assert fc["unit"] == "requests"
    assert len(fc["points"]) == 7
    assert fc["backtest"]["requested_coverage"] == 0.9


async def test_the_horizon_is_clamped_rather_than_allowed_to_run_away(client, db):
    await _seed_history(db, 1, days=9)
    r = await client.get(
        "/forecast/usage?tenant_id=1&horizon=100000",
        headers=_headers(PLATFORM_ADMIN, username="a"),
    )
    # Clamped to MAX_HORIZON, then refused for history — never a multi-minute fit.
    assert r.status_code == 200
    assert r.json()["refusal"]["code"] == "insufficient_history"
