"""Pipeline health: the states that are easy to get wrong, and the figures refused.

Four claims, each of which the obvious implementation gets wrong:

1. **``unknown`` is not ``down``.** A probe that timed out established nothing, and
   ``/readyz`` must not refuse traffic on it. The naive version treats every non-``up``
   verdict as a failure and flaps the load balancer under exactly the load that caused
   the timeout.
2. **A bypassing serving role is red, always.** If ``audit_rls_enforcement`` reports the
   serving role holds ``SUPERUSER``/``BYPASSRLS``, every ``tenant_isolation`` policy is
   enforced against nobody — and every other row of the table still reads green.
3. **The pipeline aggregation is tenant-scoped.** A tenant admin reading another
   tenant's failure counts is a leak, and it is a leak that looks like a working
   dashboard.
4. **A figure nothing records is refused and named.** The gateway has no error rate
   because ``usage_ledger`` has no failed-call row; the response says so instead of
   dividing recorded rows by recorded rows and reporting a perfect 0%.

Everything runs through the real ASGI app against the real scratch PostgreSQL served by
the ``NOSUPERUSER NOBYPASSRLS`` role. No model call is made anywhere here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pgsupport
import pytest
from aegis.governance.rls import RlsEnforcement
from aegis.jobs import JobRun, JobStatus

from app.api import routes_health
from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio

_TENANT_A = 1
_TENANT_B = 2


def _headers(role: str, *, tenant_id: int | None = None, username: str = "op") -> dict[str, str]:
    """Mint a bearer for one principal without touching the password hasher."""
    token = create_access_token(
        user_id=None, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


def _component(key: str, statusword: str, *, required: bool) -> routes_health.ComponentHealth:
    """Build one component row without dialling anything."""
    return routes_health.ComponentHealth(
        key=key,
        name=key,
        category="store",
        status=statusword,  # type: ignore[arg-type]
        detail=None,
        evidence="test double",
        measured_at=routes_health._now(),
        required=required,
    )


# ── 1. unknown ≠ down ────────────────────────────────────────────────────────


async def test_a_probe_that_times_out_is_unknown_and_not_down() -> None:
    """A probe that never answers yields ``unknown`` with a detail saying why."""

    async def _never() -> None:
        import asyncio

        await asyncio.sleep(30)

    original = routes_health._PROBE_TIMEOUT_SECONDS
    routes_health._PROBE_TIMEOUT_SECONDS = 0.01
    try:
        row = await routes_health._timed_probe(
            _never(),
            key="redis",
            name="Redis",
            category="store",
            evidence="probe_redis — PING",
            required=True,
        )
    finally:
        routes_health._PROBE_TIMEOUT_SECONDS = original

    assert row.status == "unknown"
    assert row.status != "down"
    assert "not the same fact" in (row.detail or "")


async def test_readyz_refuses_on_down_and_tolerates_unknown(client, monkeypatch) -> None:
    """503 only when a *required* component actually answered no.

    The second half is the load-bearing one: an ``unknown`` required component must not
    drain the instance, because nothing has established that it is broken.
    """
    monkeypatch.setattr(
        routes_health,
        "_components",
        lambda: _fixed([_component("redis", "unknown", required=True)]),
    )
    ok = await client.get("/readyz")
    assert ok.status_code == 200
    assert ok.json()["status"] == "ready"
    assert ok.json()["failing"] == []

    monkeypatch.setattr(
        routes_health,
        "_components",
        lambda: _fixed(
            [
                _component("redis", "down", required=True),
                _component("neo4j", "down", required=False),
            ]
        ),
    )
    refused = await client.get("/readyz")
    assert refused.status_code == 503
    # The optional dependency being down is reported but never gates: with Neo4j down,
    # hybrid retrieval keeps working on the vector and BM25 arms.
    assert refused.json()["failing"] == ["redis"]


def _fixed(rows: list[routes_health.ComponentHealth]):
    """Return an awaitable yielding ``rows`` — the shape ``_components`` has."""

    async def _run() -> list[routes_health.ComponentHealth]:
        return rows

    return _run()


# ── 2. A bypassing serving role is red ───────────────────────────────────────


async def test_a_bypassing_serving_role_is_reported_down(monkeypatch) -> None:
    """``SUPERUSER``/``BYPASSRLS`` on the serving role is ``down``, never a warning."""
    import aegis.governance.rls as rls

    async def _bypassed(_engine: object) -> RlsEnforcement:
        return RlsEnforcement(dialect="postgresql", role="postgres", is_superuser=True)

    monkeypatch.setattr(rls, "audit_rls_enforcement", _bypassed)
    row = await routes_health._rls_component()
    assert row.status == "down"
    assert row.required is True
    assert "SUPERUSER" in (row.detail or "")

    async def _clean(_engine: object) -> RlsEnforcement:
        return RlsEnforcement(dialect="postgresql", role="aegis_app")

    monkeypatch.setattr(rls, "audit_rls_enforcement", _clean)
    assert (await routes_health._rls_component()).status == "up"


# ── 3. The aggregation is tenant-scoped ──────────────────────────────────────


async def _seed_two_tenants_with_failures() -> None:
    """Give each tenant one failed ingest, with a distinguishable reason."""
    finished = datetime.now(UTC) - timedelta(minutes=5)
    started = finished - timedelta(seconds=2)
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Tenant A"),
            Tenant(id=_TENANT_B, name="Tenant B"),
            User(id=11, username="a-user", role=Role.CLIENT, tenant_id=_TENANT_A),
            User(id=22, username="b-user", role=Role.CLIENT, tenant_id=_TENANT_B),
        )
        for tenant, marker in ((_TENANT_A, "A-ONLY"), (_TENANT_B, "B-ONLY")):
            session.add(
                JobRun(
                    tenant_id=tenant,
                    job_type="ingest",
                    workflow_id=f"wf-{tenant}",
                    status=JobStatus.FAILED,
                    error=f"parse blew up {marker}",
                    started_at=started,
                    finished_at=finished,
                )
            )
        await session.commit()


async def test_pipeline_depth_and_failures_are_scoped_to_the_callers_tenant(
    client, db
) -> None:
    """Tenant A's admin sees one failure and never reads Tenant B's reason."""
    await _seed_two_tenants_with_failures()

    a = await client.get(
        "/platform/pipeline", headers=_headers(TENANT_ADMIN, tenant_id=_TENANT_A)
    )
    assert a.status_code == 200, a.text
    body = a.json()
    assert body["available"] is True
    assert body["tenant_id"] == _TENANT_A
    assert body["failed_in_window"] == 1
    assert sum(row["count"] for row in body["depth"]) == 1
    reasons = " ".join(row["error"] for row in body["recent_failures"])
    assert "A-ONLY" in reasons
    assert "B-ONLY" not in reasons

    # And a widening parameter cannot buy what the role does not have.
    widened = await client.get(
        "/platform/pipeline?tenant_id=2",
        headers=_headers(TENANT_ADMIN, tenant_id=_TENANT_A),
    )
    assert widened.status_code == 403

    # The platform admin is the one principal that reads across tenants.
    everyone = await client.get("/platform/pipeline", headers=_headers(PLATFORM_ADMIN))
    assert everyone.json()["failed_in_window"] == 2


async def test_a_window_with_nothing_finished_reports_no_failure_rate(client, db) -> None:
    """``failure_rate`` is ``null`` — never 0.0 — when nothing finished in the window."""
    resp = await client.get("/platform/pipeline", headers=_headers(PLATFORM_ADMIN))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["finished_in_window"] == 0
    assert body["failure_rate"] is None
    assert body["durations"] is None


# ── 4. The refused figures, and who may read the unscopeable ones ────────────


async def test_the_gateway_refuses_an_error_rate_and_names_what_it_would_need(
    client, db
) -> None:
    """No error-rate figure is served, and the gap says what would have to be emitted."""
    resp = await client.get("/platform/health", headers=_headers(PLATFORM_ADMIN))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    gateway = next(c for c in body["components"] if c["key"] == "llm_gateway")
    # No recorded call in a fresh scratch database: unknown, not down, and not a
    # fabricated 0% error rate.
    assert gateway["status"] == "unknown"
    assert "error_rate" not in gateway
    assert gateway["evidence"].startswith("SELECT max(ts), count(*) FROM usage_ledger")

    gaps = {row["figure"] for row in body["not_recorded"]}
    assert "LLM gateway error rate" in gaps
    needs = next(
        row["needs"] for row in body["not_recorded"] if row["figure"] == "LLM gateway error rate"
    )
    assert "outcome" in needs

    # Every component carries provenance. A verdict with no evidence is the class of
    # claim this surface exists to stop.
    assert all(c["evidence"] for c in body["components"])


async def test_the_unscopeable_surfaces_are_refused_to_a_tenant_admin(client, db) -> None:
    """Process-wide counters have no tenant filter, so the answer is the role gate."""
    tenant = _headers(TENANT_ADMIN, tenant_id=_TENANT_A)
    assert (await client.get("/platform/health", headers=tenant)).status_code == 403
    assert (await client.get("/platform/caches", headers=tenant)).status_code == 403


async def test_cache_counters_are_served_with_their_caveat_and_no_invented_rate(
    client, db
) -> None:
    """Every declared cache is listed; an unread one reports ``null``, never 0%."""
    from aegis.core.cache_stats import CACHE_KEYS, reset_cache_stats

    reset_cache_stats()
    resp = await client.get("/platform/caches", headers=_headers(PLATFORM_ADMIN))
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [row["key"] for row in body["caches"]] == list(CACHE_KEYS)
    for row in body["caches"]:
        assert row["lookups"] == 0
        assert row["hit_rate"] is None, row["key"]
    assert "per-process" in body["caveat"].lower()

    # The counters move: drive a real cache and read the surface again.
    from aegis.websearch.cache import InMemoryWebSearchCache

    cache = InMemoryWebSearchCache(max_entries=1)
    cache.get("miss-me")
    cache.set("a", "1")
    cache.get("a")
    cache.set("b", "2")

    again = await client.get("/platform/caches", headers=_headers(PLATFORM_ADMIN))
    web = next(r for r in again.json()["caches"] if r["key"] == "web_search")
    assert (web["hits"], web["misses"]) == (1, 1)
    assert web["hit_rate"] == pytest.approx(0.5)
    assert web["evictions"] == 1
    assert web["backend"] == "in_memory"

    injection = next(r for r in again.json()["caches"] if r["key"] == "injection")
    assert injection["evictions"] is None
    reset_cache_stats()
