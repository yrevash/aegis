"""SQLAlchemy ORM for the red-team run record — one table, one run per row.

A red-team run that leaves nothing behind is a toast, not evidence. Before this
table the endpoint returned a report to one browser tab and wrote three summary
numbers to the audit trail; the attacks, the verdicts and the rails that produced
them were gone the moment the tab closed, so "has this got worse?" had no answer
and "show me the run you are quoting" had no artefact.

One row is one run: what was attempted (the suite and the probe count), what got
through, what was blocked and **by which rail**, the verdict text the rail actually
wrote, the pass/fail against the thresholds it was judged on, who started it, when,
and what it cost. The per-probe detail lives in :attr:`RedTeamRun.report`, which is
the lossless :meth:`aegis.redteam.runner.RedTeamReport.as_dict` projection — the same
object the screen renders, so the stored evidence and the rendered screen cannot
disagree about what happened.

**Tenant-scoped, and registered as such in the same change.** ``tenant_id`` is a
plain indexed column (no cross-package foreign key, mirroring
:mod:`aegis.ops.models`) and ``redteam_runs`` is listed in
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES`, so :func:`bootstrap_rls`
installs the ``tenant_isolation`` policy on it at boot. A new tenant-scoped table
arriving without that line is the exact gap the Phase-4 audit found five times: the
app-level ``WHERE tenant_id = :ctx`` looks like isolation until somebody writes the
one query that forgets it.

A **NULL** ``tenant_id`` here means a platform-scoped run — Aegis testing its own
rails, not a tenant's. It is deliberately *not* in
:data:`aegis.governance.rls._PLATFORM_BASELINE_TABLES`: unlike a settings default, a
platform red-team run is not something every tenant is entitled to read, so the
standard predicate (under which ``NULL = <scope>`` is NULL, i.e. invisible) is
exactly right.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from aegis.data import AegisBase, JsonB

__all__ = ["REDTEAM_RUNS_TABLE", "RedTeamRun"]

#: The table name, as one constant shared by the model, the RLS registry and the tests.
REDTEAM_RUNS_TABLE = "redteam_runs"


class RedTeamRun(AegisBase):
    """One completed red-team run, with its whole report attached.

    The scalar columns are the ones history is queried and compared on — suite, mode,
    the two rates, the verdict — so a trend line never has to parse JSON. Everything
    else is in :attr:`report`, whole and unsummarised, because the moment the stored
    evidence is a subset of what was shown, the two can drift and only the screen
    looks right.
    """

    __tablename__ = REDTEAM_RUNS_TABLE

    id: Mapped[int] = mapped_column(primary_key=True)

    #: The public identifier. A run is linked to and compared against from a browser,
    #: and an auto-increment primary key in a URL enumerates every other tenant's runs
    #: for anyone who can subtract one.
    run_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    #: The tenant the run belongs to; NULL for a platform-scoped run. Plain indexed
    #: column (no cross-package FK), isolated by RLS + app-level scoping.
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)

    #: The suite id from :data:`aegis.redteam.battery.SUITES` — the name history is
    #: grouped by. Comparing two runs of *different* suites is comparing two batteries.
    suite: Mapped[str] = mapped_column(String(64), index=True)

    #: ``"offline"`` (deterministic backstops only, free) or ``"live"`` (a completer
    #: wired in, real model calls, real spend). Stored because the two are not the same
    #: measurement and a block rate is meaningless without knowing which one it was.
    mode: Mapped[str] = mapped_column(String(16), index=True)

    started_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    duration_ms: Mapped[int] = mapped_column(default=0)

    #: Who ran it, and in what role. A weapon's trigger has a name on it.
    initiated_by: Mapped[str] = mapped_column(String(128), default="")
    initiated_role: Mapped[str] = mapped_column(String(64), default="")

    attacks_total: Mapped[int] = mapped_column(default=0)
    attacks_blocked: Mapped[int] = mapped_column(default=0)
    controls_total: Mapped[int] = mapped_column(default=0)
    false_positives: Mapped[int] = mapped_column(default=0)
    block_rate: Mapped[float] = mapped_column(default=0.0)
    false_positive_rate: Mapped[float] = mapped_column(default=0.0)

    #: The bar this run was judged against, stored beside the result. A threshold that
    #: lives only in today's configuration turns yesterday's PASS into an unfalsifiable
    #: claim the moment somebody lowers it.
    min_block_rate: Mapped[float] = mapped_column(default=0.0)
    max_false_positive_rate: Mapped[float] = mapped_column(default=0.0)
    passed: Mapped[bool] = mapped_column(default=False)

    #: What the run was *estimated* to cost before it started, in USD. Kept so the
    #: estimate can be checked against the ledger rather than trusted forever.
    estimated_cost_usd: Mapped[float] = mapped_column(default=0.0)

    #: The lossless report — every probe, verdict, rail and rationale.
    report: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)

    __table_args__ = (
        # History is always read as "this tenant's runs of this suite, newest first".
        Index("ix_redteam_runs_scope", "tenant_id", "suite", "started_at"),
    )
