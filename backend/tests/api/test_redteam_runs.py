"""The red-team control plane: parameters reach the runner, and the report survives.

Three claims, and each of them is a defect that shipped:

1. **The parameters are not thrown away.** ``POST /redteam/run`` calls
   ``await run_redteam()`` — no completer, no battery, no thresholds — while the
   runner has been fully parameterised the whole time. The test here drives
   ``POST /redteam/runs`` with a suite and both thresholds and asserts the run that
   comes back is *that* battery judged against *those* numbers, not the defaults.

2. **A run leaves durable evidence, and the evidence is tenant-scoped.** The old
   endpoint kept three summary numbers on an audit row and dropped the report. A run
   against tenant A must be readable by tenant A's admin and invisible to tenant B's —
   over a real PostgreSQL served by a ``NOSUPERUSER NOBYPASSRLS`` role, so the
   ``tenant_isolation`` policy is genuinely in force underneath the app-level scoping.

3. **A tenant admin cannot fire the weapon.** 7.16 row 13: a live-model run is
   platform staff only. Enforced on the server, with a request no UI would send.

No gateway call happens anywhere here: every run in this file is ``mode="offline"``,
which wires **no** completer, and the one live-mode test asserts the 403 that lands
before any pipeline is built.
"""

from __future__ import annotations

import pytest
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


# ── 1. The parameters actually reach the runner ──────────────────────────────


async def test_suite_and_thresholds_reach_the_runner(client, db):
    """The battery that ran is the suite asked for, judged against the bar asked for."""
    headers = await login_as(client, "devops")
    r = await client.post(
        "/redteam/runs",
        json={
            "suite": "excessive-agency",
            "mode": "offline",
            "minBlockRate": 0.5,
            "maxFalsePositiveRate": 0.25,
        },
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # The battery: only the excessive-agency probes, plus the benign controls that
    # are the false-positive denominator every suite carries.
    categories = {row["category"] for row in body["report"]["categories"]}
    assert categories == {"excessive_agency", "benign_control"}
    assert body["run"]["attacksTotal"] == 3

    # The thresholds: stored on the run, and the verdict computed against them.
    assert body["run"]["minBlockRate"] == 0.5
    assert body["run"]["maxFalsePositiveRate"] == 0.25
    assert body["report"]["thresholds"] == {
        "minBlockRate": 0.5,
        "maxFalsePositiveRate": 0.25,
    }


async def test_a_different_suite_runs_a_different_battery(client, db):
    """Mutation guard on the claim above: the suite is not decoration.

    If ``suite`` were dropped on the floor — the defect — both requests would return
    the full battery and these two counts would be equal.
    """
    headers = await login_as(client, "devops")
    small = await client.post(
        "/redteam/runs", json={"suite": "excessive-agency"}, headers=headers
    )
    full = await client.post("/redteam/runs", json={"suite": "owasp-full"}, headers=headers)
    assert small.status_code == 200 and full.status_code == 200
    assert small.json()["run"]["attacksTotal"] < full.json()["run"]["attacksTotal"]


async def test_the_report_names_the_rail_that_caught_each_attack(client, db):
    """A block is evidence only when it names a rail and carries the rail's own words."""
    headers = await login_as(client, "devops")
    r = await client.post(
        "/redteam/runs", json={"suite": "prompt-injection"}, headers=headers
    )
    assert r.status_code == 200, r.text
    report = r.json()["report"]
    blocked = report["blocked"]
    assert blocked, "the injection suite must actually block something"
    for row in blocked:
        assert row["layer"], f"{row['id']} was blocked by an unnamed rail"
        assert row["reason"], f"{row['id']} was blocked with no rationale"
    # The indirect probes are screened at the TOOL_RESULT stage, not the input rail.
    stages = {row["stage"] for row in report["attacks"]}
    assert "tool_result" in stages


async def test_an_unknown_suite_is_refused_rather_than_silently_defaulted(client, db):
    r = await client.post(
        "/redteam/runs", json={"suite": "no-such-suite"}, headers=await login_as(client, "devops")
    )
    assert r.status_code == 400
    assert "no-such-suite" in r.json()["detail"]


# ── 2. The report persists, and it is tenant-scoped ──────────────────────────


async def test_a_run_persists_and_is_readable_by_id(client, db):
    headers = await login_as(client, "devops")
    started = await client.post("/redteam/runs", json={"suite": "content-safety"}, headers=headers)
    assert started.status_code == 200, started.text
    run_id = started.json()["run"]["runId"]

    fetched = await client.get(f"/redteam/runs/{run_id}", headers=headers)
    assert fetched.status_code == 200
    stored = fetched.json()
    # The whole report survived the round trip, not a summary of it.
    assert stored["report"]["attacks"] == started.json()["report"]["attacks"]
    assert stored["run"]["blockRate"] == started.json()["run"]["blockRate"]


async def test_history_shows_the_previous_run_of_the_same_suite(client, db):
    headers = await login_as(client, "devops")
    first = await client.post("/redteam/runs", json={"suite": "disclosure"}, headers=headers)
    second = await client.post("/redteam/runs", json={"suite": "disclosure"}, headers=headers)
    assert first.status_code == 200 and second.status_code == 200

    assert first.json()["previous"] is None, "the first run of a suite has nothing to compare to"
    assert second.json()["previous"]["runId"] == first.json()["run"]["runId"]

    history = await client.get("/redteam/runs?suite=disclosure", headers=headers)
    assert history.status_code == 200
    ids = [row["runId"] for row in history.json()["rows"]]
    assert ids == [second.json()["run"]["runId"], first.json()["run"]["runId"]]


async def test_a_tenant_admin_reads_only_its_own_tenants_runs(client, db):
    """A run against tenant A is invisible to tenant B's admin — row and detail."""
    await _seed_two_tenants()
    platform = _headers(PLATFORM_ADMIN, username="root")
    started = await client.post(
        "/redteam/runs", json={"suite": "content-safety", "tenantId": 1}, headers=platform
    )
    assert started.status_code == 200, started.text
    run_id = started.json()["run"]["runId"]
    assert started.json()["run"]["tenantId"] == 1

    a_admin = _headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin")
    b_admin = _headers(TENANT_ADMIN, tenant_id=2, user_id=22, username="b-admin")

    mine = await client.get("/redteam/runs", headers=a_admin)
    assert mine.status_code == 200
    assert [row["runId"] for row in mine.json()["rows"]] == [run_id]

    theirs = await client.get("/redteam/runs", headers=b_admin)
    assert theirs.status_code == 200
    assert theirs.json()["rows"] == []

    # And the id is not an oracle: a 404, not a 403 that confirms it exists.
    assert (await client.get(f"/redteam/runs/{run_id}", headers=b_admin)).status_code == 404
    assert (await client.get(f"/redteam/runs/{run_id}", headers=a_admin)).status_code == 200


# ── 3. A tenant admin cannot start a run, live least of all ──────────────────


async def test_a_tenant_admin_cannot_start_a_live_run(client, db):
    """7.16 row 13, enforced on the server with a request no UI would send."""
    await _seed_two_tenants()
    r = await client.post(
        "/redteam/runs",
        json={"suite": "owasp-full", "mode": "live"},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert r.status_code == 403
    assert "platform" in r.json()["detail"].lower()


async def test_a_tenant_admin_cannot_start_an_offline_run_either(client, db):
    """The trigger is platform staff only; only the *reports* are tenant-readable."""
    await _seed_two_tenants()
    r = await client.post(
        "/redteam/runs",
        json={"suite": "owasp-full"},
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert r.status_code == 403


async def test_a_client_cannot_read_reports_at_all(client, db):
    r = await client.get("/redteam/runs", headers=await login_as(client, "client"))
    assert r.status_code == 403


async def test_the_catalogue_tells_a_tenant_admin_it_may_not_run(client, db):
    """The screen's disabled button has a server-side reason behind it, not a guess."""
    await _seed_two_tenants()
    r = await client.get(
        "/redteam/suites",
        headers=_headers(TENANT_ADMIN, tenant_id=1, user_id=11, username="a-admin"),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["mayRun"] is False
    assert body["mayRunLive"] is False
    assert body["refusal"]

    devops = await client.get("/redteam/suites", headers=await login_as(client, "devops"))
    assert devops.json()["mayRun"] is True


# ── The cost estimate is shown before the run, not after ─────────────────────


async def test_a_live_run_is_priced_before_the_button_and_offline_is_free(client, db):
    """The estimate is the gateway's own unit_cost, and offline genuinely costs zero."""
    r = await client.get("/redteam/suites", headers=await login_as(client, "devops"))
    assert r.status_code == 200
    full = next(row for row in r.json()["suites"] if row["id"] == "owasp-full")
    assert full["offline"]["modelCalls"] == 0
    assert full["offline"]["costUsd"] == 0.0
    assert full["live"]["modelCalls"] > 0
    assert full["live"]["costUsd"] > 0.0
    assert full["live"]["model"], "the estimate names the deployment it priced"
