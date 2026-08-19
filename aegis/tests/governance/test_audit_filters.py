"""``list_recent_audit`` filters in SQL, and its outcome word has one definition.

Two claims, one file:

* **The filter runs before the limit.** Narrowing a page the ``LIMIT`` already returned
  is not a filter — it answers "no such event" for anything that fell off the page,
  which is the same answer it gives for an event that never happened. The first test
  puts the wanted row far outside a small page and demands it back.
* **The Python classifier and the SQL predicate agree.** ``classify_outcome`` labels the
  row a reader sees and ``_outcome_clause`` selects the rows a filter returns. Two
  spellings of one rule that drift apart would put a different word on the screen than
  the filter was applied by, which is worse than having no filter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aegis.governance import AuditLog
from aegis.governance.audit import (
    AUDIT_OUTCOMES,
    classify_outcome,
    list_recent_audit,
)

from .._seed import seed

pytestmark = pytest.mark.asyncio

_TENANT = 1

#: One action per shape the classifier must get right, mixed so neither the prefix rule
#: nor the substring rule can carry the test on its own.
_ACTIONS = (
    "auth.login",
    "ops.diagnose",
    "tool:create_ticket",
    "guardrail.input",
    "GUARDRAIL.OUTPUT",
    "documents.upload.blocked",
    "tool:transfer_funds denied",
    "report.export",
)


def _row(action: str, *, actor: str, minutes_ago: int, tenant_id: int | None = _TENANT):
    return AuditLog(
        tenant_id=tenant_id,
        action=action,
        actor=actor,
        model=None,
        trace_id=f"trace-{action}",
        payload={},
        ts=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=minutes_ago),
    )


async def test_the_filter_runs_before_the_limit(db):
    """One wanted row, 40 rows newer than it, and a page of 5.

    Client-side narrowing of ``limit=5`` returns nothing here: the row is not on the
    page. Filtering in SQL returns it, which is the entire point of §7.11.
    """
    rows = [_row("noise.event", actor="somebody", minutes_ago=i) for i in range(40)]
    rows.append(_row("ops.diagnose", actor="alice", minutes_ago=500))
    await seed(db, *rows)

    found = await list_recent_audit(5, tenant_id=_TENANT, actor="alice")

    assert [r.action for r in found] == ["ops.diagnose"]


async def test_every_filter_narrows_and_they_compose(db):
    """Each predicate on its own, then together — the operator's actual query."""
    await seed(
        db,
        _row("ops.diagnose", actor="alice", minutes_ago=10),
        _row("ops.release", actor="bob", minutes_ago=20),
        _row("auth.login", actor="alice", minutes_ago=5000),
    )
    now = datetime.now(UTC).replace(tzinfo=None)

    by_actor = await list_recent_audit(50, tenant_id=_TENANT, actor="alice")
    assert {r.action for r in by_actor} == {"ops.diagnose", "auth.login"}

    by_family = await list_recent_audit(50, tenant_id=_TENANT, action_prefix="ops.")
    assert {r.action for r in by_family} == {"ops.diagnose", "ops.release"}

    by_trace = await list_recent_audit(50, tenant_id=_TENANT, trace_id="trace-ops.release")
    assert [r.action for r in by_trace] == ["ops.release"]

    by_window = await list_recent_audit(
        50, tenant_id=_TENANT, since=now - timedelta(hours=1), until=now
    )
    assert {r.action for r in by_window} == {"ops.diagnose", "ops.release"}

    composed = await list_recent_audit(
        50,
        tenant_id=_TENANT,
        actor="alice",
        action_prefix="ops.",
        since=now - timedelta(hours=1),
    )
    assert [r.action for r in composed] == ["ops.diagnose"]


async def test_an_unknown_outcome_word_selects_nothing_rather_than_everything(db):
    """A typo must not silently turn a filtered view into the whole trail."""
    await seed(db, _row("auth.login", actor="alice", minutes_ago=1))

    assert await list_recent_audit(50, tenant_id=_TENANT, outcome="complete") == []
    assert len(await list_recent_audit(50, tenant_id=_TENANT, outcome="completed")) == 1


async def test_a_wildcard_in_action_prefix_is_a_literal(db):
    """``%`` typed into the box searches for ``%``; it does not match everything."""
    await seed(
        db,
        _row("auth.login", actor="alice", minutes_ago=1),
        _row("%weird", actor="alice", minutes_ago=2),
    )

    found = await list_recent_audit(50, tenant_id=_TENANT, action_prefix="%")

    assert [r.action for r in found] == ["%weird"]


@pytest.mark.parametrize("outcome", AUDIT_OUTCOMES)
async def test_the_sql_predicate_and_the_python_classifier_agree(db, outcome):
    """The word on the row is the word the filter selected by, for every shape."""
    await seed(db, *[_row(a, actor="alice", minutes_ago=i) for i, a in enumerate(_ACTIONS)])

    selected = await list_recent_audit(50, tenant_id=_TENANT, outcome=outcome)

    expected = {a for a in _ACTIONS if classify_outcome(a) == outcome}
    assert {r.action for r in selected} == expected
    assert all(r.outcome == outcome for r in selected), (
        "the row carries a classification the query did not select it by"
    )
    assert expected, "the fixture must exercise both outcomes"
