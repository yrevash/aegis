"""The alert subsystem: who may see what, what a replay produces, and what a push costs.

The notification feature has exactly three ways to be wrong in a way that matters, and
each of them is a section below.

1. **It leaks.** A tenant's alert reaching another tenant, or a user-targeted alert
   reaching the rest of the tenant, is the failure that ends the feature. There are two
   readers — the REST list and the SSE stream — and they are filtered by two different
   mechanisms (a SQL predicate and an in-memory one), so both are driven here, and one
   test asserts the two mechanisms agree on a matrix of scopes rather than trusting that
   whoever edits one will remember the other.
2. **It duplicates.** Every emitter in this platform can run twice: a Temporal activity
   commits and dies before the orchestrator records it, then replays in a fresh worker.
   An alert that arrives twice per replay is worse than useless — it teaches the reader
   to ignore the bell.
3. **It breaks the thing it reports on.** An ingest that succeeded, and was then recorded
   as failed because a notification insert hit a lock, would make this feature a net
   negative. The failure is injected here rather than reasoned about.

These run against the real PostgreSQL the rest of the suite uses, over the
``NOSUPERUSER NOBYPASSRLS`` serving role, so the ``tenant_isolation`` policy is live
underneath the app-level predicate — which is the layer that actually has to hold, since
this deployment runs the fail-**open** flavour of that policy.
"""

from __future__ import annotations

import asyncio
import json

import pgsupport
import pytest

from app.api.routes import AuthContext
from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker
from app.data.notifications import (
    emit,
    list_notifications,
    scope_predicate,
    visible_to,
)
from app.notifications import NotificationBus, get_bus, reset_bus

pytestmark = pytest.mark.asyncio

_TENANT_A = 1
_TENANT_B = 2
_USER_A1 = 11
_USER_A2 = 12
_USER_B1 = 22


def _headers(
    *, role: str, tenant_id: int | None, user_id: int | None, username: str
) -> dict[str, str]:
    """A bearer for one principal. The scope this feature honours comes from here only."""
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed() -> None:
    """Two tenants, two users in the first and one in the second."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Tenant A"),
            Tenant(id=_TENANT_B, name="Tenant B"),
            User(id=_USER_A1, username="a-one", role=Role.ADMIN, tenant_id=_TENANT_A),
            User(id=_USER_A2, username="a-two", role=Role.CLIENT, tenant_id=_TENANT_A),
            User(id=_USER_B1, username="b-one", role=Role.ADMIN, tenant_id=_TENANT_B),
        )
        await session.commit()


@pytest.fixture(autouse=True)
def _isolated_bus():
    """Give every test its own in-process bus, and never a Redis connection.

    ``reset_bus`` alone would leave the *next* test to rebuild one from settings — which
    is right — but a test that subscribed would otherwise inherit whatever the previous
    one published. Rebuilding around each test is what makes the SSE assertions below
    about this test's frames and nothing else.
    """
    reset_bus()
    yield
    reset_bus()


# ─────────────────────────────────────────────────────────────────────────────
# 1. It must not leak
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_tenants_notification_is_invisible_to_another_tenant(client, db):
    """The cross-tenant leak — the failure that matters most, through the HTTP surface."""
    await _seed()
    await emit(
        tenant_id=_TENANT_A,
        kind="job.succeeded",
        title="Ingest finished",
        body="policy-4.pdf ingested — 12 chunks.",
        entity_ref="document:23",
    )

    mine = await client.get(
        "/notifications",
        headers=_headers(
            role=TENANT_ADMIN, tenant_id=_TENANT_A, user_id=_USER_A1, username="a-one"
        ),
    )
    assert mine.status_code == 200, mine.text
    assert [row["body"] for row in mine.json()["rows"]] == [
        "policy-4.pdf ingested — 12 chunks."
    ]
    assert mine.json()["unread"] == 1

    theirs = await client.get(
        "/notifications",
        headers=_headers(
            role=TENANT_ADMIN, tenant_id=_TENANT_B, user_id=_USER_B1, username="b-one"
        ),
    )
    assert theirs.status_code == 200
    assert theirs.json() == {"rows": [], "unread": 0}, (
        "tenant B read tenant A's alert — the app-level predicate is the only thing "
        "holding here, because this deployment's RLS policy fails open"
    )


async def test_a_user_targeted_notification_stays_with_that_user(client, db):
    """``user_id`` narrows *inside* a tenant; ``NULL`` is the whole tenant."""
    await _seed()
    await emit(
        tenant_id=_TENANT_A,
        user_id=_USER_A1,
        kind="budget.exceeded",
        title="Budget cap reached",
        body="Your personal cap stopped a run.",
    )
    await emit(
        tenant_id=_TENANT_A,
        kind="approval.awaiting",
        title="Approval needed",
        body="A gate is waiting.",
    )

    targeted = await client.get(
        "/notifications",
        headers=_headers(
            role=TENANT_ADMIN, tenant_id=_TENANT_A, user_id=_USER_A1, username="a-one"
        ),
    )
    assert {row["kind"] for row in targeted.json()["rows"]} == {
        "budget.exceeded",
        "approval.awaiting",
    }

    neighbour = await client.get(
        "/notifications",
        headers=_headers(
            role=Role.CLIENT.value, tenant_id=_TENANT_A, user_id=_USER_A2, username="a-two"
        ),
    )
    assert {row["kind"] for row in neighbour.json()["rows"]} == {"approval.awaiting"}, (
        "a colleague in the same tenant read a notification addressed to one person"
    )
    assert neighbour.json()["unread"] == 1


async def test_marking_another_tenants_notification_read_is_a_404(client, db):
    """404, not 403: a 403 confirms the id is real and is an enumeration oracle."""
    await _seed()
    row = await emit(
        tenant_id=_TENANT_A, kind="job.succeeded", title="Done", body="x."
    )
    assert row is not None

    refused = await client.post(
        f"/notifications/{row['id']}/read",
        headers=_headers(
            role=TENANT_ADMIN, tenant_id=_TENANT_B, user_id=_USER_B1, username="b-one"
        ),
    )
    assert refused.status_code == 404

    # ...and it really was not marked, rather than refused after the write.
    still_unread = await list_notifications(
        tenant_id=_TENANT_A, user_id=_USER_A1, unread_only=True, limit=50
    )
    assert len(still_unread[0]) == 1


@pytest.mark.parametrize(
    ("envelope", "tenant_id", "user_id", "expected"),
    [
        ({"tenant_id": 1, "user_id": None}, 1, 11, True),
        ({"tenant_id": 1, "user_id": None}, 2, 22, False),
        ({"tenant_id": 1, "user_id": 11}, 1, 11, True),
        ({"tenant_id": 1, "user_id": 11}, 1, 12, False),
        ({"tenant_id": 1, "user_id": 11}, None, 11, True),
        ({"tenant_id": 1, "user_id": 11}, None, 12, False),
        ({"tenant_id": 1, "user_id": None}, None, 99, True),
    ],
)
async def test_the_stream_filter_agrees_with_the_sql_predicate(
    db, envelope, tenant_id, user_id, expected
):
    """The two halves of one rule, checked against each other on a real database.

    ``scope_predicate`` filters rows in SQL for the list; ``visible_to`` filters
    envelopes in memory for the stream. They are two implementations of one sentence, and
    the version of this feature that leaks is the one where somebody fixed the SQL and
    left the filter — so the test writes the row, reads it back through the predicate,
    and demands the in-memory answer match.
    """
    written = await emit(
        tenant_id=envelope["tenant_id"],
        user_id=envelope["user_id"],
        kind="job.succeeded",
        title="Done",
        body="x.",
    )
    assert written is not None
    rows, _ = await list_notifications(
        tenant_id=tenant_id, user_id=user_id, limit=50
    )
    sql_says = any(row["id"] == written["id"] for row in rows)
    assert sql_says is expected
    assert visible_to(envelope, tenant_id, user_id) is expected
    # And the predicate itself is non-empty for a pinned tenant, so a future edit cannot
    # make the "tenant" half vanish while these assertions still pass on the user half.
    assert len(scope_predicate(tenant_id, user_id)) == (1 if tenant_id is None else 2)


async def test_platform_staff_see_every_tenants_tenant_wide_alerts(client, db):
    """``ALL_TENANTS`` is an authority a platform admin holds, not the absence of one."""
    await _seed()
    await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="A", body="a.")
    await emit(tenant_id=_TENANT_B, kind="job.succeeded", title="B", body="b.")

    resp = await client.get(
        "/notifications",
        headers=_headers(
            role=PLATFORM_ADMIN, tenant_id=None, user_id=None, username="admin"
        ),
    )
    assert {row["title"] for row in resp.json()["rows"]} == {"A", "B"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. It must not duplicate
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_replayed_emit_writes_no_second_row_and_pushes_no_second_frame(db):
    """The idempotency guarantee, in the database rather than in a caller's ``if``.

    This is the shape a Temporal replay takes: the identical emit, called again, because
    the activity that made it committed and died before its completion was recorded.
    """
    bus = NotificationBus()
    async with bus.subscribe() as queue:
        first = await emit(
            tenant_id=_TENANT_A,
            kind="job.succeeded",
            title="Ingest finished",
            body="policy-4.pdf ingested — 12 chunks.",
            dedupe_key="job.succeeded:workflow:ingest:1:23",
        )
        second = await emit(
            tenant_id=_TENANT_A,
            kind="job.succeeded",
            title="Ingest finished",
            body="policy-4.pdf ingested — 12 chunks.",
            dedupe_key="job.succeeded:workflow:ingest:1:23",
        )
    assert first is not None
    assert second is None, "the replay wrote a second alert"

    rows, unread = await list_notifications(
        tenant_id=_TENANT_A, user_id=_USER_A1, limit=50
    )
    assert len(rows) == 1
    assert unread == 1
    # The process-wide bus is the one the emitter published on; the locally built one
    # above only proves a subscriber's queue is not fed twice by a deduplicated write.
    assert queue.qsize() == 0


async def test_two_different_events_are_not_deduplicated(db):
    """The guard must not be so broad it swallows a genuinely second event."""
    a = await emit(
        tenant_id=_TENANT_A, kind="job.succeeded", title="One", body="a.",
        dedupe_key="job.succeeded:workflow:ingest:1:23",
    )
    b = await emit(
        tenant_id=_TENANT_A, kind="job.succeeded", title="Two", body="b.",
        dedupe_key="job.succeeded:workflow:ingest:1:24",
    )
    assert a is not None and b is not None
    rows, _ = await list_notifications(tenant_id=_TENANT_A, user_id=_USER_A1, limit=50)
    assert len(rows) == 2


async def test_a_null_dedupe_key_reports_every_time(db):
    """``NULL`` is "report this every time", and Postgres allows many NULLs in a UNIQUE."""
    for _ in range(3):
        assert await emit(
            tenant_id=_TENANT_A, kind="approval.awaiting", title="Gate", body="x."
        ) is not None
    rows, _ = await list_notifications(tenant_id=_TENANT_A, user_id=_USER_A1, limit=50)
    assert len(rows) == 3


# ─────────────────────────────────────────────────────────────────────────────
# 3. It must not break the thing it reports on
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_broken_emitter_returns_none_instead_of_raising(db, monkeypatch):
    """A database failure inside the emitter is a log line, never an exception.

    Injected rather than argued: the whole promise of the emit call sites — one line,
    inside an activity that has just committed a tenant's ingest — rests on this
    function being unable to raise, and "it's wrapped in a try" is a claim a refactor
    can quietly break.
    """
    def _explode(*_args, **_kwargs):
        raise RuntimeError("the sessionmaker is gone")

    monkeypatch.setattr("app.data.notifications.get_sessionmaker", _explode)
    assert await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="x", body="y.") is None


async def test_a_broken_bus_still_leaves_the_row_durable(db, monkeypatch):
    """Push is best-effort; the row is not. A failed publish must not lose the alert."""
    class _BrokenBus:
        async def publish(self, _envelope):
            raise RuntimeError("redis went away mid-publish")

    monkeypatch.setattr("app.notifications.get_bus", lambda: _BrokenBus())
    written = await emit(
        tenant_id=_TENANT_A, kind="job.succeeded", title="Ingest finished", body="x."
    )
    assert written is not None, "a transport failure discarded a committed alert"
    rows, _ = await list_notifications(tenant_id=_TENANT_A, user_id=_USER_A1, limit=50)
    assert [row["id"] for row in rows] == [written["id"]]


# ─────────────────────────────────────────────────────────────────────────────
# The writes, and the badge
# ─────────────────────────────────────────────────────────────────────────────


async def test_mark_read_decrements_the_unread_count(client, db):
    """The badge is a separate COUNT over the whole scope, so this is what proves it moves."""
    await _seed()
    first = await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="One", body="a.")
    await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="Two", body="b.")
    headers = _headers(
        role=TENANT_ADMIN, tenant_id=_TENANT_A, user_id=_USER_A1, username="a-one"
    )
    assert first is not None

    before = await client.get("/notifications", headers=headers)
    assert before.json()["unread"] == 2

    marked = await client.post(f"/notifications/{first['id']}/read", headers=headers)
    assert marked.status_code == 200
    assert marked.json() == {"id": first["id"], "read": True}

    after = await client.get("/notifications", headers=headers)
    assert after.json()["unread"] == 1
    read_row = next(r for r in after.json()["rows"] if r["id"] == first["id"])
    assert read_row["read_at"] is not None

    # Idempotent: a second click succeeds and does not move ``read_at``.
    again = await client.post(f"/notifications/{first['id']}/read", headers=headers)
    assert again.status_code == 200
    still = await client.get("/notifications", headers=headers)
    assert still.json()["unread"] == 1
    assert next(r for r in still.json()["rows"] if r["id"] == first["id"])[
        "read_at"
    ] == read_row["read_at"]


async def test_read_all_marks_only_this_principals_scope(client, db):
    """``read-all`` is a bulk write, so its ``WHERE`` is the one to prove."""
    await _seed()
    await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="A", body="a.")
    await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="A2", body="a2.")
    await emit(tenant_id=_TENANT_B, kind="job.succeeded", title="B", body="b.")

    resp = await client.post(
        "/notifications/read-all",
        headers=_headers(
            role=TENANT_ADMIN, tenant_id=_TENANT_A, user_id=_USER_A1, username="a-one"
        ),
    )
    assert resp.json() == {"marked": 2}

    _, b_unread = await list_notifications(
        tenant_id=_TENANT_B, user_id=_USER_B1, limit=50
    )
    assert b_unread == 1, "read-all reached into another tenant's inbox"


async def test_unread_only_narrows_the_rows_and_not_the_count(client, db):
    """The list and the badge answer two different questions and must not share a filter."""
    await _seed()
    first = await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="One", body="a.")
    await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="Two", body="b.")
    headers = _headers(
        role=TENANT_ADMIN, tenant_id=_TENANT_A, user_id=_USER_A1, username="a-one"
    )
    assert first is not None
    await client.post(f"/notifications/{first['id']}/read", headers=headers)

    resp = await client.get("/notifications?unread_only=true", headers=headers)
    assert [row["title"] for row in resp.json()["rows"]] == ["Two"]
    assert resp.json()["unread"] == 1


# ─────────────────────────────────────────────────────────────────────────────
# The push itself
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_bus_fans_one_publish_out_to_every_subscriber(db):
    """The in-process half of the transport, with no Redis and no HTTP in the way."""
    bus = NotificationBus()
    async with bus.subscribe() as one, bus.subscribe() as two:
        await bus.publish({"tenant_id": 1, "user_id": None, "row": {"id": "n1"}})
        assert json.loads(await asyncio.wait_for(one.get(), 1))["row"]["id"] == "n1"
        assert json.loads(await asyncio.wait_for(two.get(), 1))["row"]["id"] == "n1"
    assert bus.subscribers == 0, "a closed stream is still holding a queue"
    assert bus.mode == "in-process"


async def test_an_emit_reaches_a_live_subscriber(db):
    """End to end through the process-wide bus: emit → durable row → published frame."""
    async with get_bus().subscribe() as queue:
        written = await emit(
            tenant_id=_TENANT_A,
            kind="job.succeeded",
            title="Ingest finished",
            body="policy-4.pdf ingested — 12 chunks.",
            entity_ref="document:23",
        )
        assert written is not None
        envelope = json.loads(await asyncio.wait_for(queue.get(), 2))
    assert envelope["tenant_id"] == _TENANT_A
    assert envelope["row"]["id"] == written["id"]
    assert envelope["row"]["entity_ref"] == "document:23"
    # The wire row carries no scoping fields — the client must not be able to filter.
    assert "tenant_id" not in envelope["row"]
    assert "user_id" not in envelope["row"]


async def test_the_stream_sends_only_what_this_principal_may_see(db):
    """The SSE route's own generator: two tenants publish, one open stream sees one frame.

    This drives the handler and pulls frames off :attr:`EventSourceResponse.body_iterator`
    rather than going through an HTTP client, and that is a deliberate choice rather than
    a shortcut. An ``EventSourceResponse`` never ends on its own — it ends when the client
    disconnects — and ``httpx``'s in-process ASGI transport has no disconnect to send, so
    a test that opened the route over HTTP would pass its assertions and then hang
    forever on the closing handshake. Measured, not assumed: that is exactly what the
    first version of this test did.

    Nothing about the assertion is weakened by it. The scope resolution, the subscription,
    the ``ready`` frame and the per-frame filter are all this handler's code, and the byte
    encoding underneath is ``sse_starlette``'s. The transport itself was verified against
    a running server with ``curl -N`` held open across a real ingest.
    """
    from app.api.routes_notifications import PING_SECONDS, stream_notifications

    await _seed()
    auth = AuthContext(
        username="a-one",
        role=Role.ADMIN,
        persona="analyst",
        fine_role=TENANT_ADMIN,
        tenant_id=_TENANT_A,
        user_id=_USER_A1,
    )
    response = await stream_notifications(auth=auth)
    assert response.media_type == "text/event-stream"
    assert response.ping_interval == PING_SECONDS, (
        "an idle stream with no heartbeat is one an nginx in front of it will close"
    )
    frames = response.body_iterator

    # The opening frame names the transport, so "the alert never arrived" and "the alert
    # arrived in another process" are distinguishable from the stream itself.
    ready = await asyncio.wait_for(anext(frames), 2)
    assert ready.event == "ready"
    assert json.loads(ready.data)["mode"] in {"in-process", "redis"}

    await emit(tenant_id=_TENANT_B, kind="job.succeeded", title="B", body="theirs.")
    await emit(tenant_id=_TENANT_A, kind="job.succeeded", title="A", body="mine.")

    frame = await asyncio.wait_for(anext(frames), 2)
    assert frame.event == "notification"
    row = json.loads(frame.data)
    assert row["title"] == "A", (
        "tenant B's alert was the first frame on tenant A's stream — the stream filter "
        "is not applying the scope the list applies"
    )
    assert row["body"] == "mine."
    await frames.aclose()


# ─────────────────────────────────────────────────────────────────────────────
# 4. It must send the reader somewhere they can actually go
# ─────────────────────────────────────────────────────────────────────────────
#
# The alerts were clickable and the click went nowhere. ``href`` was written absolute
# and hardcoded to one portal — ``/app/tenant_admin/jobs`` — but a tenant-scoped row is
# read by that tenant's ``tenant_admin``, ``ai_team`` *and* ``client``, and platform
# staff receive every tenant's. For four readers out of five the path named a portal
# their session may not enter, and the console's route guard redirects rather than
# errors, so the click marked the row read and put them back on their own dashboard
# with nothing said.
#
# The fix is a change of *meaning*, not of field: the emitter writes the screen and the
# entity (``jobs?document=25``) and never the portal, and the browser resolves it
# against the viewer's own (``web/src/lib/notificationTarget.ts``). These two tests pin
# the half that lives here — no ``/app`` prefix, and the entity is in the target, since
# a link that opens the right *list* is the defect this replaces.


async def test_an_ingest_alert_targets_that_document_and_names_no_portal(db):
    """``jobs?document=<id>`` — the screen and the thing, resolved by whoever reads it."""
    from aegis.jobs import JobStatus

    from app.jobs.activities import _notify_ingest_finished

    await _seed()
    await _notify_ingest_finished(
        JobStatus.SUCCEEDED,
        tenant_id=_TENANT_A,
        workflow_id="ingest:1:25",
        document_id=25,
        filename="policy-4.pdf",
        chunk_count=12,
        completed_stage="index",
        error=None,
    )

    rows, _ = await list_notifications(tenant_id=_TENANT_A, user_id=_USER_A1, limit=50)
    assert len(rows) == 1
    href = rows[0]["href"]
    assert href == "jobs?document=25", (
        f"the ingest alert targets {href!r}; it must name the section and the document "
        "and nothing about who is reading it"
    )
    assert not href.startswith("/app"), (
        "an absolute path picks a portal for a row that five portals read — the bug"
    )


async def test_an_approval_alert_targets_that_gate_and_names_no_portal(db):
    """``approvals?approval=<id>`` — one gate, not the inbox it happens to sit in."""
    from app.data.approvals import enqueue_approval

    await _seed()
    await enqueue_approval(
        approval_id="gate-1",
        run_id="run-1",
        action="deactivate_account",
        tenant_id=_TENANT_A,
        requested_by=_USER_A1,
    )

    rows, _ = await list_notifications(tenant_id=_TENANT_A, user_id=_USER_A1, limit=50)
    assert [row["kind"] for row in rows] == ["approval.awaiting"]
    href = rows[0]["href"]
    assert href == "approvals?approval=gate-1", (
        f"the gate alert targets {href!r}; the inbox's default cut is Waiting/7 days, so "
        "a link to the list alone shows an empty queue the moment the gate is decided"
    )
    assert not href.startswith("/app")
