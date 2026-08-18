"""Admission control — the two gates a tenant's job passes before any work starts.

**Tenant policy, not execution mechanics.** The orchestrator decides how work is retried,
timed out and resumed; this module decides whether the work is allowed to begin at all,
and that is a question about the *tenant* — how much of the shared worker pool it may
hold, and whether it can afford the run. Neither answer belongs in a workflow definition,
and both must be reachable with no orchestrator running.

Two gates, deliberately independent
-----------------------------------

* **The concurrency cap** stops one tenant occupying every worker slot. Ten documents
  dropped by one tenant must not starve the other nine tenants on the box, and the queue's
  own ``max_concurrent_activities`` cannot express that — it is per *worker*, and knows
  nothing about who owns the work it is running.
* **The budget pre-check** stops a job starting that the tenant cannot afford to finish.
  Discovering the cap half way through a 200-page parse means the parse is paid for and
  thrown away; the gateway's mid-run
  :class:`aegis.gateway.types.BudgetExceededError` is the right refusal for a single model
  call and the wrong one for a minutes-long pipeline.

They are independent because they fail for unrelated reasons and are fixed by unrelated
people: a concurrency refusal clears by waiting, a budget refusal needs an administrator.
Collapsing them into one "cannot start" would tell a caller neither.

Both **raise**. Invisible backpressure is the same defect as a silent fallback: a job
quietly parked in a queue nobody surfaces is indistinguishable, from outside, from a job
that was lost — and a 429 a user can see beats a job that never runs for reasons nobody
can name. Every error here carries a ``reason`` that is safe to render.

Where the caps come from
------------------------

The settings catalogue (:mod:`aegis.settings.spec`), not this module and not a deploy:
``jobs.max_inflight.{job_type}`` and ``budget.usd_cap``. Both are ``TIGHTEN_ONLY``, which
for a cap is the only coherent rule — see :func:`admit`.

A job type with no ``jobs.max_inflight.*`` entry raises
:class:`aegis.settings.spec.UnknownSettingError` rather than defaulting to "unlimited".
That is the fail-closed direction: an undeclared job type is a programming error, and the
failure mode of guessing is one tenant with unbounded concurrency.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance.enforcement import WINDOW_SECONDS
from aegis.governance.models import Budget, BudgetScope, BudgetWindow, UsageLedger
from aegis.jobs.models import JobRun, JobStatus
from aegis.settings.resolver import resolve

__all__ = [
    "BUDGET_CAP_KEY",
    "IN_FLIGHT_STATUSES",
    "AdmissionDeniedError",
    "AdmissionError",
    "BudgetExceededError",
    "admit",
    "max_inflight_key",
]

#: The statuses that count against the concurrency cap — every state in which the
#: orchestrator may still be holding a worker slot for this row.
#:
#: :attr:`~aegis.jobs.models.JobStatus.RECONCILING` is in the set on purpose. It means
#: "our row says running and we have not yet established what the orchestrator thinks",
#: and admitting more work against a row whose execution may well be live would be
#: choosing the optimistic reading of an unknown — precisely the reading that leaves a box
#: over-subscribed.
IN_FLIGHT_STATUSES: tuple[JobStatus, ...] = (
    JobStatus.PENDING,
    JobStatus.RUNNING,
    JobStatus.RECONCILING,
)

#: The catalogue key carrying the USD spend cap the budget gate pre-authorises against.
BUDGET_CAP_KEY = "budget.usd_cap"

#: The prefix of the per-job-type concurrency cap keys. Built here rather than written out
#: at the call site so the catalogue and the reader agree by construction.
_MAX_INFLIGHT_PREFIX = "jobs.max_inflight."


def max_inflight_key(job_type: str) -> str:
    """Return the catalogue key holding the in-flight cap for ``job_type``.

    Args:
        job_type: The ``job_runs.job_type`` value, e.g. ``"ingest"``.

    Returns:
        The dotted catalogue key, e.g. ``"jobs.max_inflight.ingest"``.
    """
    return f"{_MAX_INFLIGHT_PREFIX}{job_type}"


class AdmissionError(Exception):
    """Base for both refusals, always carrying a reason a human can be shown.

    Modelled on :class:`aegis.settings.resolver.SettingError`: a refusal with no
    renderable reason becomes a bare 4xx in a browser, and "the job did not start" with
    nothing after it is the silence this module exists to break.

    Attributes:
        reason: One sentence naming what was refused and why.
    """

    def __init__(self, reason: str) -> None:
        """Build the error with the reason it will be reported by."""
        super().__init__(reason)
        self.reason = reason


class AdmissionDeniedError(AdmissionError):
    """The tenant is at its in-flight cap for this job type (a **429**).

    Transient by construction: the same request succeeds once one of the tenant's running
    jobs finishes, which is why the host answers it with 429 (retry) rather than 403.

    Attributes:
        job_type: The job type that was capped.
        in_flight: How many of the tenant's jobs of that type were already in flight.
        cap: The resolved ``jobs.max_inflight.{job_type}`` value.
    """

    def __init__(self, reason: str, *, job_type: str, in_flight: int, cap: int) -> None:
        """Capture the tripped cap so a caller can render it without re-querying."""
        super().__init__(reason)
        self.job_type = job_type
        self.in_flight = in_flight
        self.cap = cap


class BudgetExceededError(AdmissionError):
    """The estimate does not fit in the tenant's remaining budget (a **429**).

    Deliberately **not** :class:`aegis.gateway.types.BudgetExceededError`, which this
    module could have imported. That one is raised *inside* a run when a single model call
    would breach a cap, and a host catches it to end the run gracefully; this one is raised
    before any work exists, and the correct response is to refuse the request. Sharing the
    class would make the two indistinguishable in an ``except`` — and would drag the
    gateway's import graph into a package whose whole discipline is having almost none.

    Attributes:
        cap_usd: The effective cap in force over ``window``.
        spent_usd: Ledger spend already committed inside the window.
        estimated_cost_usd: What this job was estimated to add.
        window: The accounting window the cap and the spend were measured over.
    """

    def __init__(
        self,
        reason: str,
        *,
        cap_usd: float,
        spent_usd: float,
        estimated_cost_usd: float,
        window: BudgetWindow,
    ) -> None:
        """Capture the arithmetic that produced the refusal."""
        super().__init__(reason)
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd
        self.estimated_cost_usd = estimated_cost_usd
        self.window = window


def _tenant_clause(column, tenant_id: int | None):  # noqa: ANN001, ANN202 - SQLA expression
    """Return the equality (or ``IS NULL``) clause matching ``tenant_id`` on ``column``.

    ``column == None`` is a SQL ``= NULL``, which is never true — so a platform-level row
    would be counted as zero and the cap would not bind on exactly the jobs nobody owns.

    Args:
        column: The ``tenant_id`` column to match.
        tenant_id: The tenant, or ``None`` for platform-level rows.

    Returns:
        The SQLAlchemy boolean clause.
    """
    return column.is_(None) if tenant_id is None else column == tenant_id


async def _in_flight(session: AsyncSession, *, tenant_id: int | None, job_type: str) -> int:
    """Count the tenant's jobs of one type that may still be holding a worker slot."""
    return int(
        (
            await session.execute(
                select(func.count(JobRun.id)).where(
                    _tenant_clause(JobRun.tenant_id, tenant_id),
                    JobRun.job_type == job_type,
                    JobRun.status.in_(IN_FLIGHT_STATUSES),
                )
            )
        ).scalar_one()
    )


async def _check_concurrency(
    session: AsyncSession, *, tenant_id: int | None, job_type: str
) -> None:
    """Refuse when the tenant already holds its share of the worker pool.

    Args:
        session: The scoped session.
        tenant_id: The tenant the job would belong to.
        job_type: The ``job_runs.job_type`` value.

    Raises:
        AdmissionDeniedError: When the tenant is at or above the resolved cap.
        UnknownSettingError: When no cap is declared for ``job_type``.
    """
    key = max_inflight_key(job_type)
    cap, _source = await resolve(session, key, tenant_id=tenant_id)
    running = await _in_flight(session, tenant_id=tenant_id, job_type=job_type)
    if running >= cap:
        raise AdmissionDeniedError(
            f"tenant {tenant_id} already has {running} {job_type!r} job(s) in flight and "
            f"the cap ({key}) is {cap}; retry when one finishes",
            job_type=job_type,
            in_flight=running,
            cap=int(cap),
        )


async def _tenant_budget_cap(
    session: AsyncSession, tenant_id: int
) -> tuple[float | None, BudgetWindow]:
    """Return the tightest ``budgets`` USD cap for a tenant and the window it runs over.

    Reads the same rows :func:`aegis.governance.enforcement.enforce_governance` enforces
    at the gateway, rather than a second notion of "the tenant's cap". A tenant whose
    administrator set a $5 daily cap and then watched a $50 ingest be admitted would have
    a budget in name only.

    Args:
        session: The scoped session.
        tenant_id: The tenant.

    Returns:
        ``(cap, window)``. ``cap`` is ``None`` when no tenant-scoped ``budgets`` row
        carries a USD cap; ``window`` is that row's accounting window, defaulting to
        :attr:`~aegis.governance.models.BudgetWindow.DAY` — which is also the column's own
        default, so the two cannot disagree.
    """
    rows = (
        (
            await session.execute(
                select(Budget.usd_cap, Budget.window).where(
                    Budget.scope_type == BudgetScope.TENANT,
                    Budget.scope_id == tenant_id,
                    Budget.usd_cap.is_not(None),
                )
            )
        )
        .tuples()
        .all()
    )
    if not rows:
        return None, BudgetWindow.DAY
    cap, window = min(rows, key=lambda row: float(row[0]))
    return float(cap), window


async def _spent_usd(
    session: AsyncSession, *, tenant_id: int, since: datetime
) -> float:
    """Return the tenant's committed ledger spend since ``since``.

    The :class:`~aegis.governance.models.UsageLedger` is the durable record of what has
    actually been billed, so it — and not ``job_runs.cost_usd`` — is what "already spent"
    means here. Summing both would double-count every job, because a job's model calls
    reach the ledger through the same gateway every other call does.

    Args:
        session: The scoped session.
        tenant_id: The tenant.
        since: The naive-UTC lower bound, matching the ledger's ``ts`` column.

    Returns:
        The summed ``cost_usd``, ``0.0`` when the window is empty.
    """
    total = (
        await session.execute(
            select(func.coalesce(func.sum(UsageLedger.cost_usd), 0.0)).where(
                UsageLedger.tenant_id == tenant_id, UsageLedger.ts >= since
            )
        )
    ).scalar_one()
    return float(total or 0.0)


async def _check_budget(
    session: AsyncSession, *, tenant_id: int | None, estimated_cost_usd: float
) -> None:
    """Refuse a job the tenant's remaining budget cannot cover.

    The cap is the **stricter** of the catalogue's ``budget.usd_cap`` and any tenant-scoped
    ``budgets`` row, because two caps that both claim to bind must resolve to the tighter
    one or one of them is decoration. The window comes from the ``budgets`` row when there
    is one — a catalogue cap applied over a longer window is at worst stricter, never
    weaker, which is the safe direction for a guess.

    Args:
        session: The scoped session.
        tenant_id: The tenant the job would be billed to. ``None`` — a platform-level job
            — is not budget-checked: there is no principal to bill, and attributing the
            spend to an arbitrary tenant would be worse than not checking.
        estimated_cost_usd: What the job is expected to cost.

    Raises:
        BudgetExceededError: When committed spend plus the estimate exceeds the cap.
        ValueError: If the estimate is negative — a negative pre-authorisation would
            silently *raise* the tenant's remaining budget.
    """
    if estimated_cost_usd < 0:
        raise ValueError(
            f"estimated_cost_usd must not be negative, got {estimated_cost_usd!r}"
        )
    if tenant_id is None:
        return
    catalogue_cap, _source = await resolve(session, BUDGET_CAP_KEY, tenant_id=tenant_id)
    row_cap, window = await _tenant_budget_cap(session, tenant_id)
    cap = float(catalogue_cap) if row_cap is None else min(float(catalogue_cap), row_cap)
    # The ledger's ``ts`` is TIMESTAMP WITHOUT TIME ZONE holding UTC, so the bound is
    # naive UTC too — comparing an aware bound against it raises on PostgreSQL.
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=WINDOW_SECONDS[window]
    )
    spent = await _spent_usd(session, tenant_id=tenant_id, since=since)
    if spent + estimated_cost_usd > cap:
        raise BudgetExceededError(
            f"tenant {tenant_id} has spent ${spent:.4f} of its ${cap:.2f} "
            f"{window.value} cap, so an estimated ${estimated_cost_usd:.4f} job cannot "
            "be paid for; raise the cap or wait for the window to roll",
            cap_usd=cap,
            spent_usd=spent,
            estimated_cost_usd=float(estimated_cost_usd),
            window=window,
        )


async def admit(
    session: AsyncSession,
    *,
    tenant_id: int | None,
    job_type: str,
    estimated_cost_usd: float,
) -> None:
    """Decide whether a tenant may start another job, and say no out loud.

    Two independent gates. The concurrency cap stops one tenant occupying every worker
    slot; the budget pre-check stops a job starting that the tenant cannot afford to
    finish. Both raise rather than queueing silently: invisible backpressure is the same
    defect as a silent fallback, and a 429 a user can see beats a job that never runs for
    reasons nobody can name.

    **Why both caps are ``TIGHTEN_ONLY``.** A cap exists to protect something the tenant
    does not own — the shared worker pool, and the platform's exposure to the tenant's
    spend. Under :attr:`~aegis.settings.spec.MergeRule.OVERRIDE` a tenant could resolve its
    own cap upward and the control would protect nothing; ``TIGHTEN_ONLY`` folds
    :func:`~aegis.settings.spec.strictest` over a chain that always contains the platform
    value, so a tenant may only ever ask for *less* than the platform allowed. Lower is the
    stricter direction for both: fewer slots and fewer dollars are the safer failures.

    **Why this is not one query.** The two gates read different tables and answer to
    different people — a concurrency refusal clears by waiting, a budget refusal needs an
    administrator — so they raise distinct types carrying distinct facts. Concurrency is
    checked first because it is the cheaper answer and the recoverable one; when both would
    refuse, the caller is told the condition that will clear on its own.

    **What this does not claim.** The check is not a reservation. Two requests racing
    through it can both be admitted against the same remaining budget, because the spend
    they are pre-authorised against is *committed* spend from the ledger and neither job
    has spent anything yet. The concurrency cap is what bounds that exposure: at most
    ``jobs.max_inflight.{job_type}`` unbilled estimates can be outstanding for a tenant at
    once, which is the other half of why the two gates are worth having separately.

    Args:
        session: A session already bound to ``tenant_id``'s scope by the caller
            (:func:`aegis.governance.rls.set_tenant_scope`). No commit is issued here —
            admission reads.
        tenant_id: The tenant the job would belong to, or ``None`` for a platform-level
            job (concurrency-capped, not budget-checked).
        job_type: The ``job_runs.job_type`` value, which selects the concurrency cap.
        estimated_cost_usd: What the job is expected to cost. An estimate, not a charge;
            the ledger still records what the run really cost.

    Raises:
        AdmissionDeniedError: Tenant is at its in-flight cap for this job type.
        BudgetExceededError: Estimated cost exceeds the tenant's remaining budget.
        UnknownSettingError: No ``jobs.max_inflight.{job_type}`` entry is declared — an
            undeclared job type fails closed rather than resolving to "unlimited".
        ValueError: If ``estimated_cost_usd`` is negative.
    """
    await _check_concurrency(session, tenant_id=tenant_id, job_type=job_type)
    await _check_budget(
        session, tenant_id=tenant_id, estimated_cost_usd=estimated_cost_usd
    )
