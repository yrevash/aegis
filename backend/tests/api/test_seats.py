"""Named seats (§7.8): a grant that can only ever take capability away.

Tenant sub-roles were cut. §7.8's replacement is a closed set of catalogue keys, and
the whole reason that substitution is safe rests on three claims. Each one is a defect
if it is false, so each one is driven here with a request a UI would never send, over a
real PostgreSQL served by a ``NOSUPERUSER NOBYPASSRLS`` role.

1. **A revoked seat actually stops the action.** A toggle that saves, audits and
   changes nothing is the exact defect ``inert_reason`` exists to end — four
   ``guardrails.*`` keys shipped that way. The catalogue-completeness check in
   ``aegis/tests/settings/test_forbidden_controls.py`` proves each key is *named* by
   live code; only a driven request proves the naming is a guard.

2. **Nothing a tenant admin can write grants anything.** Not because this route checks
   for it — because ``TIGHTEN_ONLY`` + ``default=True`` + ``stricter=LOWER`` leaves no
   value that widens. The seat is read *after* the coarse guard, so the effective
   permission is an ``AND``, and the user-scope-over-tenant-scope case is the one that
   would break it if the fold were wrong (7.16 row 15).

3. **The target is the server's to decide** (7.16 row 12). The tenant comes from the
   sealed scope; a cross-tenant target and a platform-staff target are both refused.

No gateway call happens anywhere here.
"""

from __future__ import annotations

import pytest
from aegis.settings.seats import SEAT_CAPABILITIES, SEAT_LABEL_KEY
from tests.conftest import login_as

from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="u") -> dict[str, str]:
    """Build a bearer header for a fine ``role`` (coarse is derived on the token)."""
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed() -> None:
    """Two tenants, an admin in each, and a member of tenant 1 to hold a seat."""
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Tenant A"),
                Tenant(id=2, name="Tenant B"),
                User(id=11, username="a-admin", role=Role.ADMIN, tenant_id=1),
                User(id=12, username="a-analyst", role=Role.ADMIN, tenant_id=1),
                User(id=22, username="b-admin", role=Role.ADMIN, tenant_id=2),
            ]
        )
        await session.commit()


def _a_admin() -> dict[str, str]:
    return _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")


def _analyst() -> dict[str, str]:
    """The seated principal: an admin of tenant 1, so every coarse guard admits it."""
    return _headers(TENANT_ADMIN, tenant_id=1, user_id=12, username="a-analyst")


# ── 1. A seat is a name plus what it may do, and the name is the point ────────


async def test_a_seat_is_named_and_says_who_granted_each_capability(client, db):
    """The demo sentence, driven: a Support Lead that may approve but may not upload."""
    await _seed()
    r = await client.put(
        "/admin/seats/12",
        json={
            "label": "Support Lead",
            "capabilities": {"seat.can_upload_documents": False},
        },
        headers=_a_admin(),
    )
    assert r.status_code == 200, r.text
    seat = r.json()
    assert seat["label"] == "Support Lead"
    by_key = {row["key"]: row for row in seat["capabilities"]}
    assert by_key["seat.can_upload_documents"]["allowed"] is False
    assert by_key["seat.can_upload_documents"]["source"] == "user"
    # Everything else is untouched and still the platform's own answer — a seat takes
    # capability away, so an unmentioned toggle must not read as revoked.
    assert by_key["seat.can_approve"]["allowed"] is True
    assert by_key["seat.can_approve"]["source"] == "platform"

    listed = await client.get("/admin/seats", headers=_a_admin())
    assert listed.status_code == 200
    rows = {row["userId"]: row for row in listed.json()["rows"]}
    assert rows[12]["label"] == "Support Lead"
    assert rows[11]["label"] == "", "seating one user must not seat the whole tenant"


# ── 2. Every capability actually gates its action ────────────────────────────
#
# Parametrised over the catalogue's own closed set rather than written out five times:
# a sixth capability added without a driven refusal fails here on the day it lands,
# which is the property that stops a seat toggle from becoming decoration.

_ACTIONS = {
    # Not a PDF, deliberately: without the seat this is a 415 and with it revoked a 403,
    # so the two outcomes are distinguishable and neither is the coarse guard's.
    "seat.can_upload_documents": ("UPLOAD", "/documents", None),
    "seat.can_edit_memory": ("POST", "/memory/forget?subject=user:12", None),
    "seat.can_approve": ("POST", "/approval", {"approval_id": "nope", "decision": "approve"}),
    "seat.can_view_tenant_audit": ("GET", "/audit", None),
    "seat.can_change_agent_mode": (
        "PUT",
        "/settings/agent.max_plan_iterations",
        {"value": 1, "scope": "user"},
    ),
}


@pytest.mark.parametrize("capability", [cap.key for cap in SEAT_CAPABILITIES])
async def test_revoking_a_capability_refuses_the_action_it_gates(client, db, capability):
    """A revoked toggle produces a 403 naming the seat — not a saved value nobody reads.

    The request is made twice against the same principal: once seated (the coarse guard
    admits it, and whatever the endpoint then does is not a 403) and once with the
    capability revoked. Only the second may be a 403, which is what makes this a test of
    the *seat* rather than of the role guard sitting above it.
    """
    await _seed()
    method, path, body = _ACTIONS[capability]

    async def _call():
        if method == "GET":
            return await client.get(path, headers=_analyst())
        if method == "PUT":
            return await client.put(path, json=body, headers=_analyst())
        if method == "UPLOAD":
            return await client.post(
                path,
                files={"file": ("note.txt", b"not a pdf", "text/plain")},
                headers=_analyst(),
            )
        return await client.post(path, json=body, headers=_analyst())

    before = await _call()
    assert before.status_code != 403, (
        f"{capability}: the coarse role guard already refuses {path}, so a seat check "
        f"there would prove nothing — {before.text}"
    )

    revoked = await client.put(
        "/admin/seats/12",
        json={"label": "Restricted", "capabilities": {capability: False}},
        headers=_a_admin(),
    )
    assert revoked.status_code == 200, revoked.text

    after = await _call()
    assert after.status_code == 403, f"{capability} was revoked and {path} still ran"
    detail = after.json()["detail"]
    assert "Restricted" in detail, "the refusal must name the seat, not just refuse"
    assert capability in detail


async def test_revoking_the_audit_seat_does_not_stop_audit_rows_being_written(client, db):
    """§7.16 row 8, and the reason ``seat.can_view_tenant_audit`` is excepted from it.

    Row 8 forbids a key that turns audit *logging* off. This seat gates a **read**, and
    the exception in ``aegis/tests/settings/test_forbidden_controls.py`` says so in
    prose; this is the assertion behind the prose. With the seat revoked, an audited
    action still lands its row — the trail stays complete, this principal just cannot
    see it.
    """
    await _seed()
    await client.put(
        "/admin/seats/12",
        json={"capabilities": {"seat.can_view_tenant_audit": False}},
        headers=_a_admin(),
    )
    assert (await client.get("/audit", headers=_analyst())).status_code == 403

    # An audited action by the blinded principal, then read back by an admin who can see.
    await client.put(
        "/settings/agent.max_plan_iterations",
        json={"value": 1, "scope": "user"},
        headers=_analyst(),
    )
    trail = await client.get("/audit", headers=_a_admin())
    assert trail.status_code == 200
    actors = [row["actor"] for row in trail.json()["rows"]]
    assert "a-analyst" in actors, (
        "the write was audited even though its author cannot read the trail; a seat "
        "that could suppress the row would be exactly what row 8 forbids"
    )


# ── 3. Nothing here can grant, and the target is not the caller's to choose ───


async def test_a_user_scope_grant_cannot_re_admit_what_the_tenant_revoked(client, db):
    """§7.16 row 15 as arithmetic: the narrower scope cannot widen the broader one.

    The mutation this guards is the tempting one — modelling seats as ``OVERRIDE`` so a
    per-user row simply wins. Under ``OVERRIDE`` this test's second write would restore
    the capability and a tenant admin would have a mechanism that *grants*. Under
    ``TIGHTEN_ONLY`` the fold refuses it outright, with a reason.
    """
    await _seed()
    # The tenant turns approval off for everyone.
    tenant_wide = await client.put(
        "/settings/seat.can_approve",
        json={"value": False, "scope": "tenant"},
        headers=_a_admin(),
    )
    assert tenant_wide.status_code == 200, tenant_wide.text

    # Now try to hand it back to one user. This is the request no UI would send.
    restore = await client.put(
        "/admin/seats/12",
        json={"capabilities": {"seat.can_approve": True}},
        headers=_a_admin(),
    )
    assert restore.status_code == 409, restore.text
    assert "tightened" in restore.json()["detail"].lower()

    # And the capability really is still gone for that user.
    denied = await client.post(
        "/approval",
        json={"approval_id": "nope", "decision": "approve"},
        headers=_analyst(),
    )
    assert denied.status_code == 403


async def test_a_tenant_admin_cannot_seat_a_user_in_another_tenant(client, db):
    """§7.16 row 12: the tenant is the sealed scope's, never the request's."""
    await _seed()
    r = await client.put(
        "/admin/seats/22",  # tenant B's admin
        json={"label": "Mine now"},
        headers=_a_admin(),
    )
    assert r.status_code == 403
    assert "own tenant" in r.json()["detail"]


async def test_a_platform_operator_cannot_be_given_a_seat(client, db):
    """A seat divides a tenant's authority; an untenanted operator holds none of it.

    A tenant-scoped row against a platform account would be a tenant narrowing a
    platform action. ``seat_allows`` ignores it, so storing one would be a control that
    reads as in force and is not — refused instead.
    """
    await _seed()
    devops = await login_as(client, "devops")
    me = await client.get("/auth/me", headers=devops)
    user_id = me.json()["user_id"] if me.status_code == 200 else None
    if user_id is None:  # the endpoint's shape is not what this test is about
        async with get_sessionmaker()() as session:
            from sqlalchemy import select

            row = (
                await session.execute(select(User).where(User.username == "devops"))
            ).scalars().first()
            user_id = row.id
    r = await client.put(
        f"/admin/seats/{user_id}",
        json={"capabilities": {"seat.can_approve": False}},
        headers=_headers(PLATFORM_ADMIN, username="root"),
    )
    assert r.status_code == 404, r.text


async def test_a_seat_write_accepts_only_seat_keys(client, db):
    """The route is not a second, unguarded door into the settings table."""
    await _seed()
    r = await client.put(
        "/admin/seats/12",
        json={"capabilities": {"guardrails.pii.block": False}},
        headers=_a_admin(),
    )
    assert r.status_code == 422
    assert "guardrails.pii.block" in r.json()["detail"]


async def test_a_seat_body_cannot_name_its_own_tenant_or_user(client, db):
    """Row 12 again, at the schema: the identity fields do not exist on the wire."""
    await _seed()
    r = await client.put(
        "/admin/seats/12",
        json={"label": "x", "tenantId": 2, "userId": 22},
        headers=_a_admin(),
    )
    assert r.status_code == 422, r.text


async def test_platform_staff_hold_no_seat_so_their_actions_are_never_narrowed(client, db):
    """A tenant-scoped row must not be able to narrow a platform operator's action.

    ``seat_allows`` returns True before it queries for an untenanted principal. The
    tenant here revokes audit reading for everyone; devops, who belongs to no tenant,
    is unaffected.
    """
    await _seed()
    await client.put(
        "/settings/seat.can_view_tenant_audit",
        json={"value": False, "scope": "tenant"},
        headers=_a_admin(),
    )
    assert (await client.get("/audit", headers=_analyst())).status_code == 403
    devops = await client.get("/audit", headers=await login_as(client, "devops"))
    assert devops.status_code == 200


async def test_the_label_is_readable_by_the_seat_holder(client, db):
    """A capability you cannot see is one you report as a bug — so the seat is readable."""
    await _seed()
    await client.put(
        "/admin/seats/12", json={"label": "Support Lead"}, headers=_a_admin()
    )
    mine = await client.get(f"/settings/{SEAT_LABEL_KEY}", headers=_analyst())
    assert mine.status_code == 200, mine.text
    assert mine.json()["value"] == "Support Lead"
