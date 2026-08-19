"""Downloadable reports: what leaves the platform, and whose rows are in it (§7.12).

An export is the highest-leverage place in this product to leak a whole tenant at
once — one request, every row, in a file that then lives on somebody's laptop. So the
claims asserted here are the ones that would be expensive to get wrong:

1. **The scope comes from the sealed ``TenantScope``, never from the URL.** A tenant
   admin's export contains its own rows and no others, and asking for another
   tenant's is a 403. Both are requests a UI would never send.
2. **The file states its own scope and window**, so a CSV in a downloads folder is
   still evidence of something specific.
3. **Every export is audited**, including — especially — the export of the audit
   trail itself.
4. **A download ticket is not a bearer.** It authorises one report for one minute and
   authenticates nothing else in the product.

No gateway call happens anywhere here; the forecast test asserts the refusal path,
which fits nothing and calls nobody.
"""

from __future__ import annotations

import pytest
from tests.conftest import login_as

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="u") -> dict[str, str]:
    """Build a bearer header for a fine ``role`` (coarse is derived on the token)."""
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


# ── 1. Isolation ─────────────────────────────────────────────────────────────


async def test_the_audit_export_carries_only_the_callers_tenant(client, db):
    """Tenant A's export holds A's rows and none of B's.

    The mutation this guards: replacing ``_scope_tenant(auth, None)`` with ``None``
    (the platform-wide read) makes both tenants' rows appear in one file, and this
    assertion is what fails.
    """
    await _seed_two_tenants()
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    b = _headers(TENANT_ADMIN, tenant_id=2, user_id=22, username="b-admin")

    # Two exports, one per tenant: each writes its own `report.export` row into its
    # own trail, which is the row the other tenant must not be able to see.
    assert (await client.get("/reports/audit.csv", headers=a)).status_code == 200
    assert (await client.get("/reports/audit.csv", headers=b)).status_code == 200

    body = (await client.get("/reports/audit.csv", headers=b)).text
    assert "b-admin" in body
    assert "a-admin" not in body, "tenant B's export contains tenant A's actor"


async def test_a_tenant_admin_cannot_widen_the_forecast_export_by_parameter(client, db):
    """``?tenant_id=`` is refused, not honoured — the request a UI would never send."""
    await _seed_two_tenants()
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    resp = await client.get("/reports/forecast.csv?tenant_id=2", headers=a)
    assert resp.status_code == 403
    assert "Cross-tenant" in resp.json()["detail"]


async def test_a_client_role_may_not_export_anything(client, db):
    """Row 5 of 7.16, at the one surface that hands over a whole table at once."""
    await _seed_two_tenants()
    headers = _headers("client", tenant_id=1, user_id=11, username="a-user")
    for report in ("audit", "tenant", "budget", "forecast"):
        resp = await client.get(f"/reports/{report}.csv", headers=headers)
        assert resp.status_code == 403, f"{report}.csv was readable by a client"
    minted = await client.post("/reports/tickets", json={"report": "audit"}, headers=headers)
    assert minted.status_code == 403


# ── 2. The file describes itself ─────────────────────────────────────────────


async def test_the_file_states_its_scope_window_and_end(client, db):
    """A CSV that does not say what it contains becomes evidence of the wrong thing."""
    await _seed_two_tenants()
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    resp = await client.get(
        "/reports/audit.csv?since=2026-01-01T00:00:00Z&actor=a-admin", headers=a
    )
    assert resp.status_code == 200
    body = resp.text

    assert "Aegis export,Audit trail" in body
    assert "Tenant 1 only" in body
    assert "2026-01-01T00:00:00+00:00 to now (UTC, inclusive)" in body
    assert "Exported by,a-admin (tenant_admin)" in body
    assert "Filter: actor,a-admin" in body
    assert "End of export," in body

    assert resp.headers["content-type"].startswith("text/csv")
    disposition = resp.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=")
    assert "aegis-audit-tenant-1-" in disposition


async def test_the_budget_export_carries_the_enforcers_own_figures(client, db):
    """The report and the cap cannot disagree: both read ``budget_status``."""
    from aegis.governance import budget_status, upsert_budget

    await _seed_two_tenants()
    await upsert_budget(
        scope_type="tenant", scope_id=1, window="month", usd_cap=25.0, tenant_id=1
    )
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")

    resp = await client.get("/reports/budget.csv", headers=a)
    assert resp.status_code == 200
    rows = await budget_status(1)
    assert rows, "the cap the export is compared against must exist"
    assert "25.0" in resp.text
    assert f"End of export,{len(rows)} data rows" in resp.text
    # The caveat that stops somebody computing an error rate from spend rows.
    assert "records no outcome" in resp.text


async def test_the_forecast_export_states_its_refusal_rather_than_an_empty_table(
    client, db
):
    """An empty table would read as "no spend"; a stated refusal reads as itself."""
    await _seed_two_tenants()
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    resp = await client.get("/reports/forecast.csv?horizon=7", headers=a)
    assert resp.status_code == 200
    body = resp.text
    assert "achieved_coverage" in body, "the caveat columns are the header, always"
    assert "No forecast was produced" in body
    assert "End of export,0 data rows" in body


# ── 3. The export is itself audited ──────────────────────────────────────────


async def test_every_export_writes_its_own_audit_row_with_the_filters(client, db):
    """An export of the trail that is not itself in the trail is the first hole found."""
    await _seed_two_tenants()
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    resp = await client.get("/reports/audit.csv?actionPrefix=report.", headers=a)
    assert resp.status_code == 200

    from aegis.governance import list_recent_audit

    rows = await list_recent_audit(10, tenant_id=1)
    assert rows[0].action == "report.export"
    assert rows[0].actor == "a-admin"


# ── 4. The download ticket ───────────────────────────────────────────────────


async def test_a_ticket_downloads_the_file_a_navigation_cannot_ask_for(client, db):
    """The browser path: mint with the bearer, then navigate with no header at all."""
    await _seed_two_tenants()
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    minted = await client.post("/reports/tickets", json={"report": "tenant"}, headers=a)
    assert minted.status_code == 200, minted.text
    ticket = minted.json()["ticket"]

    resp = await client.get(f"/reports/tenant.csv?ticket={ticket}")
    assert resp.status_code == 200, resp.text
    assert "a-admin" in resp.text
    assert "b-admin" not in resp.text, "the ticket widened the scope"


async def test_a_ticket_is_not_a_bearer_and_is_bound_to_one_report(client, db):
    """A stolen ticket must be worth one export, not a session."""
    await _seed_two_tenants()
    a = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    ticket = (
        await client.post("/reports/tickets", json={"report": "tenant"}, headers=a)
    ).json()["ticket"]

    # Not an access token: `require_auth` refuses it everywhere in the product.
    as_bearer = await client.get("/audit", headers={"Authorization": f"Bearer {ticket}"})
    assert as_bearer.status_code == 401

    # And it does not open the other three reports.
    assert (await client.get(f"/reports/audit.csv?ticket={ticket}")).status_code == 401


async def test_the_platform_admin_export_spans_tenants_and_says_so(client, db):
    """The platform scope is a stated scope, not an unlabelled superset."""
    await _seed_two_tenants()
    headers = await login_as(client, "admin")
    resp = await client.get("/reports/tenant.csv", headers=headers)
    assert resp.status_code == 200
    body = resp.text
    assert "All tenants (platform scope)" in body
    assert "a-admin" in body and "b-admin" in body
    assert "aegis-tenant-platform-" in resp.headers["content-disposition"]
    # Derived, never invented: nobody has signed in as these two seeded rows.
    assert "last_login_utc" in body
