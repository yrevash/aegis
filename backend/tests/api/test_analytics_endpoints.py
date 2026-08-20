"""Embedded analytics: the tenant filter is derived, and Superset being down is a sentence.

**No test in this file reaches a Superset.** There is not one to reach — Superset runs
on the operator's Windows machine and this suite runs on a box that cannot see it — so
the Superset side is a fake HTTP surface that records what Aegis sent. What is proven
here is therefore precise, and worth stating plainly: *the request Aegis builds, the
credential it puts on that request, and the answer the API gives when Superset is not
there.* Whether Superset 6.1.0 honours those requests is proven by walking
``docs/operations/superset-embedded.md``, and nothing below may be read as evidence of it.

The three claims:

1. **The tenant filter comes from the sealed scope and cannot be influenced by the
   request.** Two tenant admins drive the identical HTTP request against the identical
   board and Superset receives two different ``rls`` clauses. There is no request field
   that changes it — the only field the body has is ``window``, and an unknown one is a
   422 rather than a widened read.
2. **A tenant cannot obtain a token scoped to another tenant**, and a role outside a
   board's audience cannot obtain one at all.
3. **Aegis degrades.** Superset unreachable is a 200 on ``/analytics/status`` carrying
   the command that starts it, and a 503 carrying the same sentence on the data route —
   never a 500, and never an empty chart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from aegis.analytics import (
    AnalyticsService,
    Board,
    BoardCatalogue,
    Metric,
    SupersetClient,
    SupersetConfig,
)

from app.api.routes_analytics import get_analytics_service
from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker
from app.main import app

pytestmark = pytest.mark.asyncio

SERVICE_JWT = "service-jwt-that-owns-the-whole-instance"
GUEST_JWT = "guest-jwt"

LOGIN = "/api/v1/security/login"
GUEST_TOKEN = "/api/v1/security/guest_token/"
CHART_DATA = "/api/v1/chart/data"


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    text: str = ""

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


@dataclass
class FakeSuperset:
    """A Superset-shaped surface that records calls and opens no socket."""

    calls: list[dict[str, Any]] = field(default_factory=list)
    explode: bool = False
    chart_status: int = 200

    async def request(self, method, url, *, json=None, headers=None):
        if self.explode:
            raise ConnectionRefusedError("nothing listening on 8088")
        path = url.split("8088", 1)[-1]
        self.calls.append({"path": path, "json": json, "headers": headers or {}})
        if path == LOGIN:
            return FakeResponse(200, {"access_token": SERVICE_JWT})
        if path == GUEST_TOKEN:
            return FakeResponse(200, {"token": GUEST_JWT})
        if path == CHART_DATA:
            if self.chart_status >= 400:
                return FakeResponse(self.chart_status, {"message": "Forbidden: guest role"})
            rows = [{"model": "x", "spend": 1}]
            return FakeResponse(
                200, {"result": [{"colnames": ["model", "spend"], "data": rows}]}
            )
        return FakeResponse(200, {}, text="OK")

    def body(self, path: str) -> Any:
        for entry in self.calls:
            if entry["path"] == path:
                return entry["json"]
        raise AssertionError(f"{path} was never called")


CONFIG = SupersetConfig(
    base_url="http://localhost:8088",
    username="aegis-service",
    password="s3cret",
    enabled=True,
    embed_enabled=True,
)

SPEND = Board(
    id="spend",
    title="Spend by model",
    summary="What this tenant spent, per model.",
    kinds=frozenset({"chart", "dashboard"}),
    audience=frozenset({"tenant_admin", "platform_admin"}),
    datasource_id=7,
    metrics=(Metric(aggregate="SUM", column="cost_usd", label="spend"),),
    groupby=("model",),
    embedded_uuid="dash-uuid",
    dashboard_id=42,
    time_column="ts",
)


def _install(fake: FakeSuperset, *, config: SupersetConfig = CONFIG) -> None:
    """Point the router's service dependency at ``fake`` for this test."""
    client = SupersetClient(config, fake) if config.configured() else None
    service = AnalyticsService(config, BoardCatalogue([SPEND]), client)
    app.dependency_overrides[get_analytics_service] = lambda: service


@pytest.fixture(autouse=True)
def _clear_override():
    yield
    app.dependency_overrides.pop(get_analytics_service, None)


def _headers(role: str, *, tenant_id=None, user_id=None, username="u") -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_tenants() -> None:
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Tenant A"),
                Tenant(id=2, name="Tenant B"),
                User(id=11, username="a-admin", role=Role.ADMIN, tenant_id=1),
                User(id=22, username="b-admin", role=Role.ADMIN, tenant_id=2),
            ]
        )
        await session.commit()


# ── 1. the filter is derived, not accepted ───────────────────────────────────


async def test_the_rls_clause_superset_receives_is_the_callers_own_tenant(client, db):
    await _seed_two_tenants()
    fake = FakeSuperset()
    _install(fake)

    response = await client.post(
        "/analytics/boards/spend/data",
        json={"window": "last_7_days"},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 200, response.text
    assert fake.body(GUEST_TOKEN)["rls"] == [{"clause": "tenant_id = 1"}]
    assert fake.body(CHART_DATA)["queries"][0]["filters"] == [
        {"col": "tenant_id", "op": "==", "val": 1}
    ]


async def test_the_identical_request_from_another_tenant_gets_another_clause(client, db):
    """The mutation guard: if the clause came from anywhere other than the sealed scope,
    these two byte-identical requests would produce the same filter."""
    await _seed_two_tenants()
    for tenant, user, name in ((1, 11, "a-admin"), (2, 22, "b-admin")):
        fake = FakeSuperset()
        _install(fake)
        response = await client.post(
            "/analytics/boards/spend/data",
            json={"window": "last_7_days"},
            headers=_headers(TENANT_ADMIN, tenant_id=tenant, user_id=user, username=name),
        )
        assert response.status_code == 200, response.text
        assert fake.body(GUEST_TOKEN)["rls"] == [{"clause": f"tenant_id = {tenant}"}]
        assert fake.body(GUEST_TOKEN)["user"]["username"] == f"aegis-tenant-{tenant}"


async def test_a_request_cannot_smuggle_a_tenant_or_a_datasource(client, db):
    """The body has one field. Everything a caller might use to reach another tenant's
    rows is refused by name rather than silently dropped."""
    await _seed_two_tenants()
    fake = FakeSuperset()
    _install(fake)
    response = await client.post(
        "/analytics/boards/spend/data",
        json={"window": "last_7_days", "tenantId": 2, "datasourceId": 99, "filters": []},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 422, response.text
    assert fake.calls == []


async def test_an_unknown_window_is_refused_rather_than_widened(client, db):
    await _seed_two_tenants()
    _install(FakeSuperset())
    response = await client.post(
        "/analytics/boards/spend/data",
        json={"window": "all of time"},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 422, response.text


async def test_a_platform_admin_reads_across_tenants_and_says_so(client, db):
    await _seed_two_tenants()
    fake = FakeSuperset()
    _install(fake)
    response = await client.post(
        "/analytics/boards/spend/data",
        json={"window": None},
        headers=_headers(PLATFORM_ADMIN, username="ops"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["tenantScoped"] is False
    assert fake.body(GUEST_TOKEN)["rls"] == []


# ── 2. who may obtain a token at all ─────────────────────────────────────────


async def test_the_guest_token_reaches_the_browser_and_the_service_credential_does_not(
    client, db
):
    await _seed_two_tenants()
    fake = FakeSuperset()
    _install(fake)
    response = await client.post(
        "/analytics/boards/spend/embed-token",
        json={},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["token"] == GUEST_JWT
    assert SERVICE_JWT not in response.text
    assert "s3cret" not in response.text
    assert "aegis-service" not in response.text


async def test_a_role_outside_the_boards_audience_gets_nothing(client, db):
    """A client must not reach an operator's board — and the 404 is the same answer a
    board that does not exist gets, so it cannot be used to enumerate them."""
    await _seed_two_tenants()
    _install(FakeSuperset())
    headers = _headers("client", tenant_id=1, user_id=11, username="a-user")
    data = await client.post("/analytics/boards/spend/data", json={}, headers=headers)
    token = await client.post("/analytics/boards/spend/embed-token", json={}, headers=headers)
    listing = await client.get("/analytics/boards", headers=headers)
    assert data.status_code == 404
    assert token.status_code == 404
    assert listing.json()["boards"] == []


async def test_an_untenanted_client_is_refused_rather_than_shown_everything(client, db):
    """`tenant_id is None` on a client role is the shape that once read as "may see every
    tenant". Here it is a 403 with a sentence."""
    _install(FakeSuperset())
    response = await client.post(
        "/analytics/boards/spend/data",
        json={},
        headers=_headers("client", tenant_id=None, user_id=99, username="stray"),
    )
    assert response.status_code == 403, response.text
    assert "not bound to a tenant" in response.json()["detail"]


async def test_anonymous_callers_are_refused(client, db):
    for method, path in (
        ("GET", "/analytics/status"),
        ("GET", "/analytics/boards"),
    ):
        response = await client.request(method, path)
        assert response.status_code in (401, 403), (path, response.status_code)


# ── 3. Superset is optional and Aegis degrades ───────────────────────────────


async def test_status_answers_200_with_an_instruction_when_superset_is_down(client, db):
    await _seed_two_tenants()
    _install(FakeSuperset(explode=True))
    response = await client.get(
        "/analytics/status",
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["reachable"] is False
    assert "localhost:8088" in body["detail"]
    assert "superset run" in body["action"]


async def test_reading_a_board_with_superset_down_is_503_with_the_same_instruction(
    client, db
):
    await _seed_two_tenants()
    _install(FakeSuperset(explode=True))
    response = await client.post(
        "/analytics/boards/spend/data",
        json={},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 503, response.text
    assert "superset run" in response.json()["detail"]


async def test_the_feature_being_off_is_a_state_not_a_failure(client, db):
    await _seed_two_tenants()
    _install(FakeSuperset(), config=SupersetConfig())
    response = await client.get(
        "/analytics/status",
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is False
    assert "AEGIS_SUPERSET_ENABLED" in response.json()["action"]


async def test_a_superset_refusal_becomes_a_502_that_keeps_supersets_words(client, db):
    """Not a pass-through of Superset's 403: that would tell a tenant admin to check
    their own access when the real answer is a missing grant on the guest role."""
    await _seed_two_tenants()
    _install(FakeSuperset(chart_status=403))
    response = await client.post(
        "/analytics/boards/spend/data",
        json={},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert response.status_code == 502, response.text
    assert "Forbidden: guest role" in response.json()["detail"]
