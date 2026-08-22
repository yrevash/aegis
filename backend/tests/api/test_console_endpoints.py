"""The four console endpoints, and the boundary the chat tables must not leak across.

Deliberately few tests. Each one stands on a claim that fails a real regression:

* **Isolation.** A conversation belongs to one tenant *and* one person. The test drives
  the HTTP surface for the app-level half and then reads ``chat_sessions`` directly with
  another tenant's scope bound, which is the only way to prove the **database** is
  filtering rather than the ``WHERE`` clause above it. Removing ``chat_sessions`` from
  ``aegis.governance.rls._TENANT_SCOPED_TABLES`` fails that half; deleting the
  ``user_id`` predicate in ``app.data.chat`` fails the other.
* **The transcript is written by the run.** ``POST /query`` with a ``session_id`` must
  produce the user turn and the assistant turn, the latter carrying the ``run_id``.
  This is the end of the thread task 6.2 exists to close: the id has to reach the
  backend and something has to happen when it does.
* **A client can read its own budget**, and an ungoverned account is told so instead of
  shown a zero.
* **``GET /models`` is a projection**, not a copied list — proved by moving the routing
  table underneath it.
* **A refused attachment is a 200 carrying the verdict**, because a blocked image is the
  injection screen working, not an error.
"""

from __future__ import annotations

import base64
import json

import pgsupport
import pytest
from sqlalchemy import select

from app.api.schemas import Role
from app.core.security import create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope, upsert_budget
from app.data.models import ChatSession

pytestmark = pytest.mark.asyncio

#: A real 1×1 PNG — valid magic bytes and a readable IHDR, so hygiene clears it and the
#: request reaches the injection screen, which is what the attachment test is about.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)


def _headers(*, tenant_id: int, user_id: int, username: str) -> dict[str, str]:
    """A ``client``-role bearer for one tenant's user."""
    token = create_access_token(
        user_id=user_id, username=username, role="client", tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_tenants() -> None:
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=1, name="Tenant A"),
            Tenant(id=2, name="Tenant B"),
            User(id=11, username="a-user", role=Role.CLIENT, tenant_id=1),
            User(id=12, username="a-other", role=Role.CLIENT, tenant_id=1),
            User(id=22, username="b-user", role=Role.CLIENT, tenant_id=2),
        )
        await session.commit()


# ── Isolation: the tenant boundary and the owner boundary ────────────────────


async def test_a_conversation_is_visible_only_to_its_tenant_and_its_owner(client, db):
    """Neither another tenant nor a colleague may see, rename or delete a chat.

    The last assertion is the one that proves **Postgres** is enforcing it: the app
    predicate is deliberately not used there, so the only thing that can return zero
    rows is the ``tenant_isolation`` policy engaging for the bound scope. Drop
    ``chat_sessions`` from the RLS registry and that assertion fails while every HTTP
    assertion above it still passes — which is exactly the silence the registry exists
    to prevent.
    """
    await _seed_two_tenants()
    owner = _headers(tenant_id=1, user_id=11, username="a-user")
    colleague = _headers(tenant_id=1, user_id=12, username="a-other")
    stranger = _headers(tenant_id=2, user_id=22, username="b-user")

    created = await client.post("/sessions", headers=owner, json={"title": "Q3 refunds"})
    assert created.status_code == 201, created.text
    session_id = created.json()["id"]

    assert [r["id"] for r in (await client.get("/sessions", headers=owner)).json()["rows"]] == [
        session_id
    ]
    # Same tenant, different person: a chat is personal, not tenant-wide.
    assert (await client.get("/sessions", headers=colleague)).json()["rows"] == []
    assert (await client.get("/sessions", headers=stranger)).json()["rows"] == []

    for headers in (colleague, stranger):
        reads = await client.get(f"/sessions/{session_id}/messages", headers=headers)
        renames = await client.patch(
            f"/sessions/{session_id}", headers=headers, json={"title": "mine"}
        )
        removes = await client.delete(f"/sessions/{session_id}", headers=headers)
        assert (reads.status_code, renames.status_code, removes.status_code) == (404, 404, 404)

    # The row is untouched, and the owner still owns it.
    assert (await client.get("/sessions", headers=owner)).json()["rows"][0]["title"] == "Q3 refunds"

    # …and the database refuses it too, with no app-level predicate in the query.
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, 2)
        rows = (await session.execute(select(ChatSession.id))).scalars().all()
        assert rows == [], (
            "chat_sessions is readable across tenants with tenant 2's scope bound — the "
            "tenant_isolation policy is missing, so the row-level boundary is only the "
            "WHERE clause the application happens to write."
        )


# ── The transcript is written by the run ─────────────────────────────────────


async def test_query_with_a_session_id_writes_both_turns_of_the_transcript(
    client, db, make_deps, parse_sse
):
    """The id the console sends reaches the backend and the turns land in Postgres.

    Without the ``session_id`` in the request body — the state this repo shipped in —
    nothing is written and the assistant's answer exists only in the browser's memory.
    """
    from app.api import routes as api_routes
    from app.main import app

    await _seed_two_tenants()
    owner = _headers(tenant_id=1, user_id=11, username="a-user")
    session_id = (
        await client.post("/sessions", headers=owner, json={"title": "Refunds"})
    ).json()["id"]

    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(propose_tool=False)
    try:
        run = await client.post(
            "/query",
            headers=owner,
            json={"query": "Why was R1 escalated?", "session_id": session_id},
        )
        assert run.status_code == 200, run.text
        finished = [e for e in parse_sse(run.text) if e["event"] == "run_finished"]
        assert finished, run.text
        run_id = json.loads(finished[0]["data"])["run_id"]
    finally:
        app.dependency_overrides.pop(api_routes.get_agent_deps, None)

    turns = (await client.get(f"/sessions/{session_id}/messages", headers=owner)).json()["rows"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "Why was R1 escalated?"
    assert turns[1]["content"] != ""
    assert turns[1]["run_id"] == run_id


async def test_a_colleague_cannot_write_into_another_users_transcript(
    client, db, make_deps, parse_sse
):
    """``POST /query`` with somebody else's ``session_id`` writes nothing of theirs.

    ``append_chat_turn`` was the one function in ``app.data.chat`` without the owner
    predicate — it checked only that the session existed *in the tenant* — so a
    ``session_id`` was a write capability for anyone who could learn one. And they are
    learnable, not guessable: ``chat_sessions.id`` is deliberately the same string as
    ``memory_session.id``, and a tenant-admin may list a colleague's memory sessions.
    Both turns landed in the victim's transcript, the assistant turn carrying a
    ``run_id`` from a run they never made, with ``last_active_at`` bumped so their
    session rail re-ordered under them.

    Restore the tenant-only ``exists`` check and every assertion below fails.
    """
    from app.api import routes as api_routes
    from app.data.models import ChatMessage
    from app.main import app

    await _seed_two_tenants()
    owner = _headers(tenant_id=1, user_id=11, username="a-user")
    colleague = _headers(tenant_id=1, user_id=12, username="a-other")
    session_id = (
        await client.post("/sessions", headers=owner, json={"title": "Q3 refunds"})
    ).json()["id"]

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, 1)
        before = (
            await session.execute(
                select(ChatSession.last_active_at).where(ChatSession.id == session_id)
            )
        ).scalar_one()

    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps(propose_tool=False)
    try:
        run = await client.post(
            "/query",
            headers=colleague,
            json={"query": "What is in their transcript?", "session_id": session_id},
        )
        assert run.status_code == 200, run.text
        # The run itself is fine — it is the *transcript* the colleague may not touch.
        assert [e for e in parse_sse(run.text) if e["event"] == "run_finished"], run.text
    finally:
        app.dependency_overrides.pop(api_routes.get_agent_deps, None)

    assert (await client.get(f"/sessions/{session_id}/messages", headers=owner)).json()[
        "rows"
    ] == []
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, 1)
        turns = (
            await session.execute(
                select(ChatMessage.id).where(ChatMessage.session_id == session_id)
            )
        ).scalars().all()
        assert turns == [], "a colleague's run wrote turns into somebody else's transcript"
        after = (
            await session.execute(
                select(ChatSession.last_active_at).where(ChatSession.id == session_id)
            )
        ).scalar_one()
        assert after == before, "the victim's session rail was re-ordered by a stranger's run"


async def test_concurrent_turns_on_one_session_get_distinct_indexes(db):
    """Four appenders racing on one conversation number their turns 0..3, not all 0.

    ``turn_index`` is a ``SELECT count(*)`` and ``ix_chat_messages_session_turn`` is not
    unique, so two writers that counted before either inserted both wrote index 0 and
    the transcript rendered them in an arbitrary order for ever. The fix is ordering,
    not DDL: the ``UPDATE`` that bumps ``last_active_at`` runs *first* and takes the
    session row's write lock, so the second appender waits there instead of counting
    stale. Move that ``UPDATE`` back below the count and this fails.
    """
    import asyncio

    from app.data.chat import append_chat_turn, create_chat_session

    await _seed_two_tenants()
    await create_chat_session("race-1", tenant_id=1, user_id=11, title="Race")

    indexes = await asyncio.gather(
        *(
            append_chat_turn(
                "race-1", role="user", content=f"turn {n}", tenant_id=1, user_id=11
            )
            for n in range(4)
        )
    )
    assert sorted(indexes) == [0, 1, 2, 3], (
        f"concurrent appends produced duplicate turn_index values: {indexes}"
    )


# ── The caller's own budget ──────────────────────────────────────────────────


async def test_a_client_reads_its_own_cap_and_an_ungoverned_one_is_told_so(client, db):
    """`GET /me/budget` answers the role every other budget read refuses.

    ``measured`` is the load-bearing field: with no cap there is nothing to draw, and a
    pill that rendered ``$0.00 of $0`` would be a fabricated measurement.
    """
    await _seed_two_tenants()
    owner = _headers(tenant_id=1, user_id=11, username="a-user")

    ungoverned = await client.get("/me/budget", headers=owner)
    assert ungoverned.status_code == 200, ungoverned.text
    assert ungoverned.json()["measured"] is False
    assert ungoverned.json()["usd_cap"] is None

    await upsert_budget(scope_type="tenant", scope_id=1, window="day", usd_cap=50.0, tenant_id=1)
    await upsert_budget(scope_type="user", scope_id=11, window="day", usd_cap=5.0, tenant_id=1)

    mine = (await client.get("/me/budget", headers=owner)).json()
    assert mine["measured"] is True
    # Nearest-binding wins: the user's own cap, not the tenant's roomier one.
    assert mine["usd_cap"] == pytest.approx(5.0)
    assert {r["budget"]["scope_type"] for r in mine["rows"]} == {"tenant", "user"}

    # Another tenant's caps are never in the answer.
    other = _headers(tenant_id=2, user_id=22, username="b-user")
    assert (await client.get("/me/budget", headers=other)).json()["rows"] == []


# ── /models is a projection ──────────────────────────────────────────────────


async def test_models_reflects_the_gateway_routing_table(client, user_headers, monkeypatch):
    """Move the routing table and the endpoint moves with it.

    A hand-maintained model list would pass a test that only checked the shape; this one
    can only pass if the rows are read from :func:`aegis.gateway.routing.routing_table`.
    """
    monkeypatch.setenv("MODEL_GENERATION", "some-other-deployment")
    payload = (await client.get("/models", headers=user_headers)).json()

    by_role = {row["role"]: row for row in payload["rows"]}
    assert by_role["generation"]["model"] == "some-other-deployment"
    assert payload["default_role"] == "generation"
    # The price is per the role's own billing unit, not a blanket per-1k-tokens.
    assert by_role["voice"]["billing_unit"] == "audio_minutes"
    assert by_role["generation"]["input_cost_usd"] > 0


# ── /attachments ─────────────────────────────────────────────────────────────


async def test_a_refused_attachment_is_a_two_hundred_carrying_the_verdict(
    client, user_headers, monkeypatch
):
    """An unscreenable image is blocked, and the block is the response body.

    With no vision completer the injection screen **fails closed** — there is no offline
    signature backstop for pixels — so this also pins that the composer never receives a
    500 it would have to render as "something went wrong".
    """

    async def _no_gateway(role, messages, **kwargs):  # noqa: ANN001, ARG001
        raise RuntimeError("no vision credential in this environment")

    monkeypatch.setattr("app.core.llm.complete", _no_gateway)

    resp = await client.post(
        "/attachments",
        headers=user_headers,
        json={
            "image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
            "mime_type": "image/png",
            "question": "What is this?",
            "filename": "invoice.png",
        },
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["blocked"] is True
    assert payload["summary"] == ""
    # The sniffed type, never the declaration — the one lie that is a whole rail bypass.
    assert payload["mime_type"] == "image/png"
    assert payload["coverage"] != ""
    # The refusal says WHICH refusal it is. ``aegis.vision.pipeline`` computes the
    # distinction its own docstring says must never be blurred — "blocked by the injection
    # screen" (this image carries an instruction) versus "blocked because the injection
    # screen could not run" (fail-closed, nobody looked at it) — and this response used to
    # drop the sentence entirely, leaving the composer with "No description was produced."
    # for both. The screen cannot run here, so it must be the second one.
    assert "could not run" in payload["blocked_reason"], payload["blocked_reason"]
    assert "injection screen" in payload["blocked_reason"]
