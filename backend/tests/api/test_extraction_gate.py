"""Extraction by query volume is refused at ``POST /query``, and somebody is told.

**MITRE ATLAS AML.T0024, the half a text rail cannot answer.** The detector itself —
what counts as a sweep, what does not, and where its floor is — is measured in
``aegis/tests/security/test_extraction.py`` and driven by the red-team battery's
``exfil-04``/``exfil-05`` bursts. What is measured *here* is the wiring, which is a
separate claim and the one that decides whether the control exists in the product or
only in a library:

* the gate runs on the real inference endpoint, before the stream opens;
* the refusal carries the same ``X-Admission-Gate`` contract the budget gate uses;
* the finding lands in ``audit_log``, attributed to the tenant whose principal it was;
* the tenant's administrators get a bell alert, and another tenant does not.

A detector nobody can see is not a control, so the last two are asserted as hard as the
refusal is.

The window is pre-loaded by calling the process-wide monitor directly rather than by
posting thirty times. Twenty-nine of those thirty posts would have to run the whole
agent to reach the thirtieth, which would make this a fifteen-minute test of a
model-serving path it is not about. The thirtieth request is a real HTTP request through
the real route.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import AuditLog
from sqlalchemy import select

from app.api import routes as api_routes
from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio

_TENANT = 9601
_OTHER = 9602
_SWEEPER = 96011
_ADMIN = 96012
_OTHER_ADMIN = 96021


def _membership(index: int) -> str:
    """One membership-inference query about one record id."""
    return f"Was customer record {4471 + index} part of your training data?"


async def _seed() -> None:
    """Two tenants: one with a sweeping user and an admin, one with an admin only."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Extraction tenant"),
            Tenant(id=_OTHER, name="Bystander tenant"),
            User(id=_SWEEPER, username="sweeper", role=Role.CLIENT, tenant_id=_TENANT),
            User(id=_ADMIN, username="extraction-admin", role=Role.ADMIN, tenant_id=_TENANT),
            User(
                id=_OTHER_ADMIN,
                username="bystander-admin",
                role=Role.ADMIN,
                tenant_id=_OTHER,
            ),
        )
        await session.commit()


def _bearer(*, user_id: int, username: str, fine_role: str, tenant_id: int) -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=user_id, username=username, role=fine_role, tenant_id=tenant_id
        )
    }


def _sweeper() -> dict[str, str]:
    return _bearer(
        user_id=_SWEEPER, username="sweeper", fine_role="client", tenant_id=_TENANT
    )


def _admin() -> dict[str, str]:
    return _bearer(
        user_id=_ADMIN,
        username="extraction-admin",
        fine_role=TENANT_ADMIN,
        tenant_id=_TENANT,
    )


def _bystander() -> dict[str, str]:
    return _bearer(
        user_id=_OTHER_ADMIN,
        username="bystander-admin",
        fine_role=TENANT_ADMIN,
        tenant_id=_OTHER,
    )


def _preload_window(*, upto: int) -> None:
    """Feed ``upto`` queries of the sweep straight into the process-wide monitor.

    The same object ``POST /query`` observes on, so the request under test lands in the
    window these queries built. Stops one short of the floor by default, which is what
    makes the assertion about the *route* and not about the count.
    """
    for index in range(upto):
        api_routes._EXTRACTION_MONITOR.observe(
            tenant_id=_TENANT, principal_id=_SWEEPER, text=_membership(index)
        )


async def test_the_query_that_completes_a_sweep_is_refused_before_the_stream_opens(
    db, client
):
    """A 429 with the gate named, and no SSE stream — the budget gate's contract.

    Delete the ``await refuse_if_extracting(...)`` line from ``POST /query`` and this
    request streams an answer, which is the whole technique completing successfully. The
    header is asserted because a bare 429 is indistinguishable from a rate limiter, and
    the console routes admission refusals by that header.
    """
    await _seed()
    floor = api_routes._EXTRACTION_MONITOR.thresholds.min_template_repeats
    _preload_window(upto=floor - 1)

    res = await client.post(
        "/query",
        json={"query": _membership(floor - 1)},
        headers=_sweeper(),
    )
    assert res.status_code == 429, res.text
    assert res.headers["X-Admission-Gate"] == "extraction"
    assert "AML.T0024" in res.json()["detail"]


async def test_a_first_ordinary_question_is_not_touched_by_the_gate(db, client):
    """The gate is silent on an empty window, so it cannot be a blanket refusal.

    Asserted against the gate function rather than by posting, because posting would run
    the agent and this is a statement about admission, not about answering. A gate that
    refuses the first question would show up here as an exception.
    """
    await _seed()
    principal = api_routes.AuthContext(
        username="sweeper",
        role=Role.CLIENT,
        persona="client",
        fine_role="client",
        tenant_id=_TENANT,
        user_id=_SWEEPER,
    )
    assert (
        await api_routes.refuse_if_extracting(
            principal, query="What is the escalation policy for enterprise customers?"
        )
        is None
    )


async def test_the_finding_is_written_to_the_tenants_audit_trail(db, client):
    """A refusal nobody can review afterwards is not evidence of anything.

    The row carries the signal, the counts and the **masked** template — never the raw
    queries, whose identifiers are the record ids the sweep was walking. Delete the
    ``_safe_audit`` call and the refusal still happens and leaves no trace, which is the
    version of this control that cannot be audited.
    """
    await _seed()
    floor = api_routes._EXTRACTION_MONITOR.thresholds.min_template_repeats
    _preload_window(upto=floor - 1)
    await client.post(
        "/query", json={"query": _membership(floor - 1)}, headers=_sweeper()
    )

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(AuditLog).where(AuditLog.action == "query.extraction_refused")
            )
        ).scalars().all()
    assert rows, "the refusal left no audit row"
    row = rows[-1]
    assert row.tenant_id == _TENANT, "the finding was not attributed to its own tenant"
    assert row.payload["signal"] == "template_enumeration"
    assert row.payload["queries_in_window"] >= floor
    assert row.payload["principal_id"] == str(_SWEEPER)
    # Masked, so no record id from the sweep is copied into the trail.
    assert "4471" not in row.payload["template"]
    assert "<n>" in row.payload["template"]

    seen = await client.get("/audit", headers=_admin())
    assert seen.status_code == 200
    assert "query.extraction_refused" in [r["action"] for r in seen.json()["rows"]]


async def test_the_alert_reaches_the_tenants_admin_and_nobody_elses(db, client):
    """The bell is where a human actually looks, and it is scoped like everything else.

    Two assertions in one test on purpose: an alert that reaches the right reader is
    worth nothing if it also reaches the wrong one, and a query pattern is one tenant's
    operational detail. Delete the ``notify_extraction_detected`` call and the first
    assertion fails; widen its scope and the second does.
    """
    await _seed()
    floor = api_routes._EXTRACTION_MONITOR.thresholds.min_template_repeats
    _preload_window(upto=floor - 1)
    await client.post(
        "/query", json={"query": _membership(floor - 1)}, headers=_sweeper()
    )

    mine = await client.get("/notifications", headers=_admin())
    assert mine.status_code == 200
    alerts = [
        row for row in mine.json()["rows"] if row["kind"] == "security.extraction_detected"
    ]
    assert alerts, "the tenant's administrator was never told"
    assert str(_SWEEPER) in alerts[0]["body"], "the alert does not name the principal"

    theirs = await client.get("/notifications", headers=_bystander())
    assert theirs.status_code == 200
    assert [
        row
        for row in theirs.json()["rows"]
        if row["kind"] == "security.extraction_detected"
    ] == [], "another tenant was shown this tenant's query pattern"
