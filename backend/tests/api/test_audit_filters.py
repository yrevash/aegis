"""``GET /audit`` filters server-side, and tells nobody what another tenant holds.

§7.11. The console used to fetch a page and narrow it in the browser, so every filter
answered questions only about the page. These filters are ``WHERE`` clauses, ANDed
underneath the caller's sealed tenant scope.

**The enumeration hazard this project has hit twice.** A filter that distinguishes "no
such row" from "not yours" is an existence oracle: point it at a guessed actor, action or
trace and read the difference. So the tests below do not check that a cross-tenant filter
returns *nothing* — they check that it returns **byte-identically what a filter naming
something that exists nowhere returns**, and that a cross-tenant ``tenant_id`` is refused
the same way whether that tenant exists or not.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import AuditLog

from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio

_A = 9411
_B = 9412
_NO_SUCH_TENANT = 9499
_A_ADMIN = 94111
_B_ADMIN = 94121
_PLATFORM = 94199

#: What tenant B holds. A real actor, a real action family and a real trace — the three
#: things an attacker with tenant A's credentials would guess at.
_B_ACTOR = "b-operator"
_B_ACTION = "tool:transfer_funds"
_B_TRACE = "b0b0b0b0b0b0b0b0"

#: What nobody holds.
_NOWHERE_ACTOR = "nobody-at-all"
_NOWHERE_ACTION = "tool:no_such_tool"
_NOWHERE_TRACE = "ffffffffffffffff"


async def _seed() -> None:
    """Two tenants with an admin each, an un-tenanted platform admin, and B's trail."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_A, name="Filter tenant A"),
            Tenant(id=_B, name="Filter tenant B"),
            User(id=_A_ADMIN, username="filter-a-admin", role=Role.ADMIN, tenant_id=_A),
            User(id=_B_ADMIN, username="filter-b-admin", role=Role.ADMIN, tenant_id=_B),
            User(id=_PLATFORM, username="filter-platform", role=Role.ADMIN, tenant_id=None),
            AuditLog(
                tenant_id=_B,
                action=_B_ACTION,
                actor=_B_ACTOR,
                trace_id=_B_TRACE,
                payload={},
            ),
            AuditLog(tenant_id=_A, action="auth.login", actor="a-operator", payload={}),
            AuditLog(
                tenant_id=_A, action="guardrail.input", actor="a-operator", payload={}
            ),
        )
        await session.commit()


def _headers(*, user_id: int, username: str, fine_role: str, tenant_id: int | None):
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=user_id, username=username, role=fine_role, tenant_id=tenant_id
        )
    }


def _a_admin():
    return _headers(
        user_id=_A_ADMIN, username="filter-a-admin", fine_role=TENANT_ADMIN, tenant_id=_A
    )


def _platform_admin():
    return _headers(
        user_id=_PLATFORM,
        username="filter-platform",
        fine_role=PLATFORM_ADMIN,
        tenant_id=None,
    )


@pytest.mark.parametrize(
    ("param", "real", "absent"),
    [
        ("actor", _B_ACTOR, _NOWHERE_ACTOR),
        ("action_prefix", _B_ACTION, _NOWHERE_ACTION),
        ("trace_id", _B_TRACE, _NOWHERE_TRACE),
    ],
)
async def test_another_tenants_row_is_indistinguishable_from_a_row_that_does_not_exist(
    db, client, param, real, absent
):
    """Filtering on something tenant B really holds looks exactly like filtering on nothing.

    Status, headers-worth-comparing and body are compared as a whole rather than just
    asserting emptiness: a count, an error string or a 404 would each be a signal, and
    any of them turns this endpoint into a directory of another tenant's operators,
    tools and runs.
    """
    await _seed()

    hit = await client.get("/audit", params={param: real}, headers=_a_admin())
    miss = await client.get("/audit", params={param: absent}, headers=_a_admin())

    assert hit.status_code == miss.status_code == 200
    assert hit.json() == miss.json() == {"rows": []}


async def test_naming_another_tenant_is_refused_the_same_way_as_naming_no_tenant(
    db, client
):
    """``?tenant_id=`` cannot widen a scope, and cannot be used to probe for one either.

    Tenant A's admin gets the identical 403 for tenant B (which exists, holds rows and
    is not theirs) and for a tenant id that was never issued.
    """
    await _seed()

    real = await client.get("/audit", params={"tenant_id": _B}, headers=_a_admin())
    fake = await client.get(
        "/audit", params={"tenant_id": _NO_SUCH_TENANT}, headers=_a_admin()
    )

    assert real.status_code == fake.status_code == 403
    assert real.json() == fake.json()


async def test_the_platform_admins_tenant_selector_works(db, client):
    """The one principal whose authority already spans tenants may still name one."""
    await _seed()

    scoped = await client.get("/audit", params={"tenant_id": _B}, headers=_platform_admin())

    assert scoped.status_code == 200
    assert [r["action"] for r in scoped.json()["rows"]] == [_B_ACTION]


async def test_the_filters_reach_sql_and_the_outcome_word_is_the_servers(db, client):
    """The route's parameters narrow the real query, and the row states its own outcome."""
    await _seed()

    blocked = await client.get(
        "/audit", params={"outcome": "blocked"}, headers=_a_admin()
    )
    assert blocked.status_code == 200
    rows = blocked.json()["rows"]
    assert [r["action"] for r in rows] == ["guardrail.input"]
    assert rows[0]["outcome"] == "blocked"

    by_family = await client.get(
        "/audit", params={"action_prefix": "auth."}, headers=_a_admin()
    )
    assert [r["action"] for r in by_family.json()["rows"]] == ["auth.login"]
