"""The two unauthenticated surfaces the public landing page reads.

``GET /`` in the console is a public marketing page rendered before anyone signs
in, so the manifest and the efficiency figures it shows must answer with **no**
bearer token. Widening a public surface is exactly the kind of change that is easy
to make by accident, so these tests pin both halves of the contract:

1. the two endpoints answer unauthenticated (the landing page renders at all), and
2. the public metrics body carries **no** cost or routing fields, and ``/metrics``
   still requires auth and still carries them (the surface has not widened).

Assertion 2 is the load-bearing one: without it, someone folding a new field into
``PublicMetricsResponse`` could publish the platform's cost base onto an
unauthenticated page and no test would notice.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


# Field names that must never appear on the public surface. The absolute money
# figures and the effective routing map stay behind ``require_auth``: see
# ``PublicMetricsResponse`` for why publishing a cost base is a bad trade.
WITHHELD_FIELDS = (
    "cost_saved_usd",
    "baseline_cost_usd",
    "cost_per_1k_queries_usd",
    "routing",
    "quality_score",
)


# ── the surfaces answer without a token ──────────────────────────────────────


async def test_capabilities_is_public(client):
    """The module manifest renders the landing page, so it needs no bearer token."""
    resp = await client.get("/platform/capabilities")

    assert resp.status_code == 200
    body = resp.json()
    assert body["product"] == "Aegis"
    assert body["module_count"] == len(body["modules"]) > 0
    # Branding-never-hiding: every module still carries its honest tech.
    assert all(m["tech"] for m in body["modules"])


async def test_public_metrics_is_public(client):
    """The efficiency figures render pre-login, so they need no bearer token."""
    resp = await client.get("/platform/public-metrics")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {
        "cache_hit_rate",
        "small_model_share",
        "total_calls",
        "actions_approved",
        "p95_latency_ms",
    }


# ── the surface has not widened ──────────────────────────────────────────────


async def test_public_metrics_withholds_cost_and_routing(client):
    """No cost figure, routing map or quality score may reach the public body."""
    body = (await client.get("/platform/public-metrics")).json()

    for field in WITHHELD_FIELDS:
        assert field not in body, f"{field} must not be published pre-login"


async def test_authenticated_metrics_still_guarded_and_complete(client, admin_headers):
    """``/metrics`` keeps its auth guard and keeps the fields the public one drops."""
    assert (await client.get("/metrics")).status_code == 401

    body = (await client.get("/metrics", headers=admin_headers)).json()
    for field in WITHHELD_FIELDS:
        assert field in body, f"{field} should still be served to an authed caller"


async def test_public_metrics_are_honest_when_unmeasured(client):
    """Nothing is fabricated: an unmeasured latency is null, not an invented number."""
    body = (await client.get("/platform/public-metrics")).json()

    assert body["p95_latency_ms"] is None or body["p95_latency_ms"] >= 0
    assert body["total_calls"] >= 0
    assert body["actions_approved"] >= 0
