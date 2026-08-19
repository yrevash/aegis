"""Pipeline health as an aggregation over what the platform already records (§7.10).

**Nothing here is a monitoring subsystem, and that is the design.** Five sources already
exist and every figure below is a join over them:

``aegis.core.health``
    Reachability probes for Redis, the embedded vector store and — new here — Neo4j,
    each of which makes a real round trip and reports the answer it got. Postgres is
    asked on the serving engine itself (see :func:`_probe_serving_postgres`), which is
    a stronger answer than a side connection: it is the pool requests are served on.
``app.jobs.health``
    The Temporal worker's live state in this process, already written by the supervisor
    in :func:`app.main.run_worker_supervised`.
``job_runs``
    The durable record of every unit of background work: its kind, its status, when it
    started and when it finished. Queue depth, the oldest pending job, the failure rate
    and the duration percentiles are all ``SELECT``s over it.
``run_events``
    The append-only per-run log. The ingest writes one ``ingest_stage`` event per
    committed stage carrying that stage's measured ``duration_ms``, so "where does the
    time go in the pipeline" is a read, not a new timer.
``usage_ledger``
    One row per model call at the gateway. The LLM's health is derived from *work
    already done* — the last call this platform actually recorded — rather than from a
    synthetic ping that spends money to prove it can spend money.

**Two states this surface keeps that most do not.**

``unknown`` is not ``down``. A probe that timed out has not established that a
dependency answered "no"; it has established nothing. They are separate statuses with
separate colours, and :func:`readyz` treats only ``down`` as a reason to refuse traffic
while reporting ``unknown`` for what it could not determine.

**Every component carries its evidence** — the query or the probe call that produced the
verdict — because a status with no provenance is exactly the class of claim this repo's
audits keep catching.

**What is refused.** Where nothing records a figure, the response carries a
:class:`NotRecorded` row naming the figure, why it cannot be derived, and what would have
to be emitted for it to exist. A health page that shows a plausible number nobody computed
is worse than one that admits the gap, so this module never fills one in. The gaps are
listed on :data:`_PIPELINE_GAPS` and :data:`_CACHE_GAPS` rather than in a comment, so they
are on the screen an operator reads.

**The cache surface (§7.10b / §7.14).** ``GET /platform/caches`` reports
:mod:`aegis.core.cache_stats` — counters incremented on the exact branch inside each cache
that decided hit or miss. The configuration it shows (backend, TTL, threshold, cap) is
what the *live instance in this process registered*, not a module default, so it cannot
describe a cache the process is not running.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from aegis.core.cache_stats import cache_reports, spec_for
from aegis.core.health import (
    DependencyStatus,
    probe_neo4j,
    probe_redis,
    probe_vector_store,
)
from aegis.observability.latency import percentile
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.routes import AuthContext, _scope_tenant, require_auth
from app.config import get_settings
from app.core.security import PLATFORM_ADMIN

__all__ = [
    "CacheRow",
    "CacheStatsResponse",
    "ComponentHealth",
    "NotRecorded",
    "PipelineHealthResponse",
    "PlatformHealthResponse",
    "health_router",
    "mount",
]

logger = logging.getLogger(__name__)

health_router = APIRouter()

#: How long any one dependency probe may take before the answer is ``unknown`` rather
#: than ``down``. Short enough that ``/readyz`` stays a probe a load balancer can poll,
#: long enough that a busy-but-alive store is not libelled as dead.
_PROBE_TIMEOUT_SECONDS = 3.0

#: The default window the pipeline aggregation looks back over.
_DEFAULT_WINDOW_HOURS = 24

#: Ceiling on how many finished job rows are pulled back to compute duration
#: percentiles. The percentiles are computed in Python from real rows rather than in SQL
#: so the same code path answers on every dialect; the bound is what keeps that honest
#: rather than unbounded.
_JOB_SAMPLE_LIMIT = 500

#: Ceiling on how many stage events are read for the per-stage timings.
_STAGE_SAMPLE_LIMIT = 2000

#: How recently the gateway must have recorded a call for it to count as *observed
#: working*. Beyond it the component is ``unknown`` — no evidence either way — never
#: ``down``: a platform nobody has queried for two hours has not failed.
_GATEWAY_FRESH_MINUTES = 60


# ─────────────────────────────────────────────────────────────────────────────
# Authorisation
# ─────────────────────────────────────────────────────────────────────────────

_NOT_PLATFORM_STAFF = (
    "Infrastructure health is a platform-operations surface. It reports process-wide "
    "and cross-tenant facts that are not scoped to any one tenant, so it requires a "
    "devops or platform-admin account."
)


def require_infra_reader(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Admit only a principal who operates the platform rather than a tenant.

    The component table and the cache counters are **process-wide and unscopeable**: a
    hit rate is one number over every tenant that shared the process, and a serving
    role's RLS attributes are a fact about the deployment. There is no tenant filter
    that would make either safe for a tenant admin to read, so the answer is the role
    gate rather than a filter that cannot be written.
    """
    if not (auth.is_platform_staff() or auth.fine_role == PLATFORM_ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=_NOT_PLATFORM_STAFF
        )
    return auth


def require_pipeline_reader(auth: AuthContext = Depends(require_auth)) -> AuthContext:
    """Admit any authenticated principal to the pipeline aggregation.

    Unlike the component table, every figure here is a ``SELECT`` that
    :func:`app.api.routes._scope_tenant` narrows to the caller's own tenant, so a
    tenant admin reading its own queue depth and its own failure rate is reading its own
    data. The narrowing is on the server; a ``tenant_id`` parameter can only ever
    re-state a scope the principal already holds.
    """
    return auth


# ─────────────────────────────────────────────────────────────────────────────
# Wire models
# ─────────────────────────────────────────────────────────────────────────────

#: The verdicts a component can carry. ``unknown`` and ``down`` are deliberately
#: distinct — see the module docstring.
ComponentStatus = Literal["up", "down", "degraded", "unknown", "not_applicable"]


class ComponentHealth(BaseModel):
    """One component's verdict, and the thing that produced it."""

    key: str
    name: str
    category: str = Field(description="store · substrate · model · isolation")
    status: ComponentStatus
    detail: str | None = Field(
        default=None, description="Why, in the words of whatever answered."
    )
    evidence: str = Field(
        description="The probe call or SQL that produced this verdict. Required: a "
        "status with no provenance is not a measurement."
    )
    measured_at: str
    required: bool = Field(
        description="Whether /readyz refuses traffic when this component is down."
    )


class NotRecorded(BaseModel):
    """A figure this surface deliberately does not show, and what it would take.

    Rendered on the page next to the figures that *are* real. A gap named on the screen
    is a specification; a gap papered over with a plausible number is a defect nobody
    can see.
    """

    figure: str
    why: str
    needs: str


class PlatformHealthResponse(BaseModel):
    """Every component, measured concurrently, with its evidence."""

    components: list[ComponentHealth]
    measured_at: str
    not_recorded: list[NotRecorded] = Field(default_factory=list)


class JobDepthRow(BaseModel):
    """How many jobs of one kind sit in one status right now."""

    job_type: str
    status: str
    count: int


class StageTiming(BaseModel):
    """Measured wall-clock inside one ingest stage handler."""

    stage: str
    runs: int
    p50_ms: float
    p95_ms: float
    max_ms: float


class JobFailure(BaseModel):
    """One recent failure, with the reason the worker recorded."""

    job_type: str
    error: str
    finished_at: str | None


class DurationSummary(BaseModel):
    """Percentiles over real finished-job durations. Never present when empty."""

    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float


class WorkerState(BaseModel):
    """The durable substrate's state in this process."""

    state: str
    detail: str | None = None
    since: str | None = None
    restarts: int


class PipelineHealthResponse(BaseModel):
    """The pipeline, aggregated from ``job_runs`` and ``run_events``.

    ``available`` is false when the record tables could not be read at all (the offline
    demo, or a database that is down). Every list is then empty and ``unavailable_reason``
    says which — an empty pipeline and an unreadable one are different facts.
    """

    available: bool
    unavailable_reason: str | None = None
    tenant_id: int | None
    window_hours: int
    generated_at: str
    depth: list[JobDepthRow] = Field(default_factory=list)
    in_flight: int = 0
    oldest_pending_age_seconds: float | None = None
    oldest_pending_created_at: str | None = None
    finished_in_window: int = 0
    failed_in_window: int = 0
    failure_rate: float | None = None
    durations: DurationSummary | None = None
    stages: list[StageTiming] = Field(default_factory=list)
    stage_events_read: int = 0
    recent_failures: list[JobFailure] = Field(default_factory=list)
    worker: WorkerState
    sources: dict[str, str]
    not_recorded: list[NotRecorded] = Field(default_factory=list)


class CacheRow(BaseModel):
    """One cache: what it is, what it was built as, and what it did here."""

    key: str
    name: str
    holds: str
    method: str
    registered: bool
    backend: str | None
    ttl_seconds: int | None
    threshold: float | None
    capacity: int | None
    entries: int | None
    lookups: int
    hits: int
    misses: int
    writes: int
    evictions: int | None
    hit_rate: float | None


class CacheStatsResponse(BaseModel):
    """The live cache counters, with the caveats that make them readable."""

    caches: list[CacheRow]
    generated_at: str
    source: str
    caveat: str
    not_recorded: list[NotRecorded] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# The gaps, named where an operator reads them
# ─────────────────────────────────────────────────────────────────────────────

_PIPELINE_GAPS: tuple[NotRecorded, ...] = (
    NotRecorded(
        figure="Retries per job",
        why=(
            "job_runs has no attempt counter. The orchestrator retries an activity "
            "internally and the row is only ever updated to its latest state, so the "
            "number of attempts behind a succeeded job is not in this database."
        ),
        needs=(
            "an attempt column on job_runs bumped by the activity wrapper, or a "
            "job_attempt event written to run_events on every retry."
        ),
    ),
    NotRecorded(
        figure="Queue wait time",
        why=(
            "started_at is recorded, but a job that has never started has no start, "
            "and a job that finished carries no record of how long it queued before a "
            "worker picked it up beyond created_at→started_at — which is only "
            "available for rows that did start."
        ),
        needs=(
            "nothing new for finished jobs; the figure below is the honest subset "
            "(oldest pending age), and a full wait-time distribution needs the "
            "started_at of jobs that are still pending, which does not exist."
        ),
    ),
    NotRecorded(
        figure="Per-stage timings for anything but ingestion",
        why=(
            "run_events is written today only by the ingest stage log. The agent "
            "query path emits the same event vocabulary to the SSE stream and "
            "persists none of it, so a query's node timings die with the socket."
        ),
        needs=(
            "the orchestrator's event stream folded into run_events the way "
            "app.jobs.ingest_log folds the ingest's, which is the record layer's "
            "documented purpose and is already built."
        ),
    ),
)

_COMPONENT_GAPS: tuple[NotRecorded, ...] = (
    NotRecorded(
        figure="LLM gateway error rate",
        why=(
            "usage_ledger has one row per call and no outcome column, so a failed "
            "call leaves no row at all. Dividing recorded rows by recorded rows would "
            "always produce a 0% error rate, which is a fabricated number, not a "
            "measured one."
        ),
        needs=(
            "an outcome (and error class) column on usage_ledger written for failed "
            "calls as well as successful ones — the gateway already knows both."
        ),
    ),
    NotRecorded(
        figure="Dependency latency",
        why=(
            "each probe reports reachability only. Timing one round trip of a probe "
            "that opens its own connection would mostly measure connection setup."
        ),
        needs=(
            "a timed probe against a pooled connection, which is a different probe "
            "from the reachability one and should not silently replace it."
        ),
    ),
)

_CACHE_GAPS: tuple[NotRecorded, ...] = (
    NotRecorded(
        figure="Memory semantic cache counters",
        why=(
            "aegis.memory.cache decides hits and misses and records neither. It is "
            "the one cache on this page with no counters, so it is listed nowhere "
            "rather than shown at zero."
        ),
        needs=(
            "the same two calls the other caches make — record_hit/record_miss on the "
            "branch in MemorySemanticCache.check that decided the verdict."
        ),
    ),
    NotRecorded(
        figure="Entry count and memory footprint of a Redis-backed cache",
        why=(
            "the counters are in-process. Asking Redis for its keyspace size on every "
            "page load is a round trip per render that nothing here has earned, and "
            "an estimate would not be a measurement."
        ),
        needs=(
            "a periodic sampler that runs DBSIZE / ZCARD on a schedule and stores the "
            "sample, or an explicit refresh control on the page."
        ),
    ),
    NotRecorded(
        figure="Spend saved by cache hits",
        why=(
            "a hit is counted where it happens, inside the cache; the price of the "
            "call it avoided is known at the gateway. Nothing joins the two, so any "
            "saved-dollars figure would be the product of a real count and an assumed "
            "unit price."
        ),
        needs=(
            "the avoided call's model role carried onto the hit, priced with "
            "aegis.gateway.routing.unit_cost — the same table the ledger is priced "
            "with, never a second one."
        ),
    ),
    NotRecorded(
        figure="Cross-process totals",
        why=(
            "these counters live in one process's RAM and reset with it. A second API "
            "worker has its own answer; summing them here would invent a consensus "
            "nothing measured."
        ),
        needs=(
            "a shared counter store (a Redis hash incremented on the same branch), "
            "which is a durable metrics store and a decision of its own."
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Component probes
# ─────────────────────────────────────────────────────────────────────────────


def _now() -> str:
    """Return the current UTC instant, ISO-8601, as every timestamp here is stamped."""
    return datetime.now(UTC).isoformat()


async def _timed_probe(
    coro: Any,  # noqa: ANN401 - an awaitable DependencyStatus
    *,
    key: str,
    name: str,
    category: str,
    evidence: str,
    required: bool,
) -> ComponentHealth:
    """Await one probe under a timeout and translate it into a component row.

    A timeout yields ``unknown``, never ``down``. The probe established nothing, and
    rendering "nothing" as "no" is a lie in the safe direction — the direction this
    project has already been bitten in.
    """
    try:
        result: DependencyStatus = await asyncio.wait_for(
            coro, timeout=_PROBE_TIMEOUT_SECONDS
        )
    except TimeoutError:
        return ComponentHealth(
            key=key,
            name=name,
            category=category,
            status="unknown",
            detail=(
                f"The probe did not answer within {_PROBE_TIMEOUT_SECONDS:.0f}s. That "
                "is not the same fact as the dependency answering no."
            ),
            evidence=evidence,
            measured_at=_now(),
            required=required,
        )
    return ComponentHealth(
        key=key,
        name=name,
        category=category,
        status="up" if result.status == "up" else "down",
        detail=result.detail,
        evidence=evidence,
        measured_at=_now(),
        required=required,
    )


async def _probe_serving_postgres() -> DependencyStatus:
    """``SELECT 1`` on the engine that serves requests, not on a side connection.

    :func:`aegis.core.health.probe_postgres` dials a fresh ``asyncpg`` connection from a
    libpq URL, and this host's ``POSTGRES_DSN`` is a SQLAlchemy URL — it carries the
    ``+asyncpg`` driver suffix, which ``asyncpg`` rejects outright. Handing it over
    unchanged made the probe report a perfectly healthy database as **down**, which is
    the same class of defect as reporting a broken one as up.

    Rewriting the URL would fix the crash and still answer about the wrong thing. The
    question this row exists to answer is "can requests reach the database?", and the
    connection requests use is the pool behind :func:`app.data.session.get_engine`. So
    that is what is asked, and the evidence string says so.

    Returns:
        A :class:`~aegis.core.health.DependencyStatus`, never an exception: a probe that
        raises is a probe that cannot report.
    """
    try:
        from sqlalchemy import text

        from app.data.session import get_engine

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return DependencyStatus(name="postgres", status="up")
    except Exception as exc:  # noqa: BLE001 - a probe reports failure, never raises
        return DependencyStatus(name="postgres", status="down", detail=str(exc))


def _worker_component() -> ComponentHealth:
    """Translate the in-process worker state into a component row.

    ``disabled`` is ``not_applicable`` rather than ``down``: a deployment that never
    intended to run a worker in this process is not failing, which is the same reading
    ``GET /ready`` already takes.
    """
    from app.jobs.health import WORKER_DISABLED, WORKER_DOWN, WORKER_RUNNING, worker_health

    snapshot = worker_health()
    if snapshot.state == WORKER_RUNNING:
        verdict: ComponentStatus = "up"
    elif snapshot.state == WORKER_DOWN:
        verdict = "down"
    elif snapshot.state == WORKER_DISABLED:
        verdict = "not_applicable"
    else:
        # starting / stopped: it is not serving and it has not failed.
        verdict = "degraded"
    detail = snapshot.detail
    if snapshot.restarts and detail is None:
        detail = f"Recovered after {snapshot.restarts} supervised restart(s)."
    return ComponentHealth(
        key="job_worker",
        name="Durable job worker",
        category="substrate",
        status=verdict,
        detail=detail,
        evidence="app.jobs.health.worker_health() — this process's supervisor state",
        measured_at=_now(),
        required=True,
    )


def _limiter_component() -> ComponentHealth:
    """Report what is actually bounding concurrent model calls, in its own word.

    :mod:`aegis.gateway.limiter` computes an honest ``scope`` — ``fleet``, ``process``,
    ``unlimited``, or ``fleet (degraded)`` while the shared store cannot be reached — and
    documents it as *"reported on the platform surface"*. It was not: the only reader was
    one ``logger.info`` at boot, which by construction runs before any Redis failure can
    have happened. So the degradation counter existed and nothing could ever see it, and a
    Redis outage silently converted a fleet-wide bound into no bound at all.

    The verdict is the scope, not a ping. ``process`` is **not** ``up``: it is an accurate
    limiter and a narrower claim than a multi-process deployment assumes, and the word for
    "working, but less than you think" is ``degraded``. ``unlimited`` is the same reading
    for the same reason. Neither is ``required``: an unbounded gateway still serves.
    """
    from aegis.gateway import limiter_status

    status_payload = limiter_status()
    scope = str(status_payload.get("scope") or "unknown")
    limit = status_payload.get("limit")
    if scope == "fleet":
        verdict: ComponentStatus = "up"
        detail = (
            f"{limit} concurrent provider calls across every process, held as leases in "
            "the shared store."
        )
    elif scope.startswith("fleet"):  # "fleet (degraded)" — the store went away
        verdict = "degraded"
        detail = (
            f"The shared store could not be reached, so the fleet-wide bound of {limit} "
            f"is NOT being held ({status_payload.get('degraded')} call(s) proceeded "
            "unbounded). Calls fail OPEN deliberately: a Redis blip becoming a total "
            "model outage is the bigger failure."
        )
    elif scope == "process":
        verdict = "degraded"
        detail = (
            f"{limit} concurrent provider calls in THIS process only. A second process "
            "would double it — set REDIS_URL for a bound that holds across the fleet."
        )
    else:
        verdict = "degraded"
        detail = (
            "Nothing is bounding concurrent model calls. Five users asking four-agent "
            "questions is twenty simultaneous provider calls; set "
            "GATEWAY_MAX_CONCURRENT_CALLS."
        )
    return ComponentHealth(
        key="model_limiter",
        name="Model-call limiter",
        category="model",
        status=verdict,
        detail=detail,
        evidence="aegis.gateway.limiter_status() — the installed limiter's own counters",
        measured_at=_now(),
        required=False,
    )


async def _gateway_component() -> ComponentHealth:
    """Derive the LLM gateway's health from work already done, never from a ping.

    A probe that spends money to prove it can spend money is a bad trade on a fixed
    credit budget, and evidence of real work is the stronger claim anyway. So this reads
    ``usage_ledger``: the most recent call the platform actually recorded, and how many
    landed in the window.

    The verdict is deliberately two-valued. A recent recorded call is ``up`` — the
    gateway demonstrably worked. No recent call is ``unknown``, **not** ``down``: a
    platform nobody has queried has not failed, and there is no failed-call row to read
    because ``usage_ledger`` has no outcome column (see :data:`_COMPONENT_GAPS`).
    """
    sql = (
        "SELECT max(ts), count(*) FROM usage_ledger "
        f"WHERE ts >= now() - interval '{_GATEWAY_FRESH_MINUTES} minutes'"
    )
    try:
        from aegis.governance.models import UsageLedger
        from sqlalchemy import func, select

        from app.data.session import get_sessionmaker

        since = datetime.now(UTC) - timedelta(minutes=_GATEWAY_FRESH_MINUTES)
        async with get_sessionmaker()() as session:
            row = (
                await session.execute(
                    select(func.max(UsageLedger.ts), func.count()).where(
                        UsageLedger.ts >= since
                    )
                )
            ).first()
    except Exception as exc:  # noqa: BLE001 - an unreadable ledger is unknown, not down
        return ComponentHealth(
            key="llm_gateway",
            name="LLM gateway",
            category="model",
            status="unknown",
            detail=f"The usage ledger could not be read: {exc}",
            evidence=sql,
            measured_at=_now(),
            required=False,
        )
    last_ts, calls = (row[0], int(row[1] or 0)) if row is not None else (None, 0)
    if calls:
        return ComponentHealth(
            key="llm_gateway",
            name="LLM gateway",
            category="model",
            status="up",
            detail=(
                f"{calls} call(s) recorded in the last {_GATEWAY_FRESH_MINUTES} "
                f"minutes; the most recent at {last_ts}."
            ),
            evidence=sql,
            measured_at=_now(),
            required=False,
        )
    return ComponentHealth(
        key="llm_gateway",
        name="LLM gateway",
        category="model",
        status="unknown",
        detail=(
            f"No model call has been recorded in the last {_GATEWAY_FRESH_MINUTES} "
            "minutes. That is an absence of evidence, not evidence of failure — and "
            "there is no failed-call row to read, because usage_ledger records only "
            "calls that succeeded."
        ),
        evidence=sql,
        measured_at=_now(),
        required=False,
    )


async def _rls_component() -> ComponentHealth:
    """Ask the database whether the serving role is exempt from row security.

    Red, always, no exceptions, when the serving role holds ``SUPERUSER`` or
    ``BYPASSRLS``: every ``tenant_isolation`` policy the bootstrap installs is then
    enforced against nobody, and a deployment in that state looks perfectly healthy on
    every other row of this table. A non-PostgreSQL dialect has no row security at all,
    which is reported as ``not_applicable`` rather than faked as a pass.
    """
    evidence = (
        "aegis.governance.rls.audit_rls_enforcement() — pg_roles lookup of "
        "rolsuper/rolbypassrls for current_user on the SERVING engine"
    )
    try:
        from aegis.governance.rls import audit_rls_enforcement

        from app.data.session import get_engine

        verdict = await audit_rls_enforcement(get_engine())
    except Exception as exc:  # noqa: BLE001 - unreadable is unknown, never a pass
        return ComponentHealth(
            key="rls",
            name="Tenant isolation (RLS)",
            category="isolation",
            status="unknown",
            detail=f"The serving role's attributes could not be read: {exc}",
            evidence=evidence,
            measured_at=_now(),
            required=True,
        )
    if not verdict.applicable:
        return ComponentHealth(
            key="rls",
            name="Tenant isolation (RLS)",
            category="isolation",
            status="not_applicable",
            detail=(
                f"The serving engine speaks {verdict.dialect}, which has no row "
                "security. This is reported rather than scored as a pass."
            ),
            evidence=evidence,
            measured_at=_now(),
            required=True,
        )
    if verdict.bypassed:
        return ComponentHealth(
            key="rls",
            name="Tenant isolation (RLS)",
            category="isolation",
            status="down",
            detail=(
                f"The serving role {verdict.role!r} holds {verdict.cause}, so Postgres "
                "skips every tenant_isolation policy for it. The policies are installed "
                "and enforced against nobody."
            ),
            evidence=evidence,
            measured_at=_now(),
            required=True,
        )
    detail = f"Serving as {verdict.role!r}: no SUPERUSER, no BYPASSRLS."
    if verdict.escalatable_via:
        detail += (
            " It is a member of "
            + ", ".join(verdict.escalatable_via)
            + ", which is one SET ROLE away from an exemption."
        )
    return ComponentHealth(
        key="rls",
        name="Tenant isolation (RLS)",
        category="isolation",
        status="degraded" if verdict.escalatable_via else "up",
        detail=detail,
        evidence=evidence,
        measured_at=_now(),
        required=True,
    )


async def _components() -> list[ComponentHealth]:
    """Run every probe concurrently and return one row each, in reading order."""
    settings = get_settings()
    stores = settings.stores_enabled
    if not stores:
        note = (
            "The real stores are switched off in this deployment (STORES_ENABLED is "
            "false), so nothing was dialled. This is a configuration fact, not a "
            "failure."
        )
        rows = [
            ("postgres", "PostgreSQL", "store", "SELECT 1 on the serving engine", True),
            ("redis", "Redis / Memurai", "store", "probe_redis — PING", True),
            (
                "vector_store",
                "Qdrant vector store",
                "store",
                "aegis.core.health.probe_vector_store — QdrantClient(url).get_collections()",
                True,
            ),
            (
                "neo4j",
                "Neo4j",
                "store",
                "probe_neo4j — driver.verify_connectivity()",
                False,
            ),
        ]
        return [
            ComponentHealth(
                key=key,
                name=name,
                category=category,
                status="not_applicable",
                detail=note,
                evidence=evidence,
                measured_at=_now(),
                required=required,
            )
            for key, name, category, evidence, required in rows
        ] + [_worker_component(), _limiter_component()]

    probes = [
        _timed_probe(
            _probe_serving_postgres(),
            key="postgres",
            name="PostgreSQL",
            category="store",
            evidence=(
                "SELECT 1 on the SERVING engine — the pool requests are answered on, "
                "not a fresh side connection"
            ),
            required=True,
        ),
        _timed_probe(
            probe_redis(settings.redis_url),
            key="redis",
            name="Redis / Memurai",
            category="store",
            evidence="aegis.core.health.probe_redis — PING",
            required=True,
        ),
        _timed_probe(
            probe_vector_store(settings.qdrant_url),
            key="vector_store",
            name="Qdrant vector store",
            category="store",
            evidence=(
                "aegis.core.health.probe_vector_store — "
                "QdrantClient(url).get_collections()"
            ),
            required=True,
        ),
        _timed_probe(
            probe_neo4j(
                settings.neo4j_uri,
                user=settings.neo4j_user,
                password=settings.neo4j_password,
            ),
            key="neo4j",
            name="Neo4j",
            category="store",
            evidence="aegis.core.health.probe_neo4j — driver.verify_connectivity()",
            # Not required: with the graph arm down, hybrid retrieval keeps working on
            # the vector and BM25 arms. It is a degradation of the answer, not an
            # outage of the platform — and it is the answer that has to say so.
            required=False,
        ),
        _gateway_component(),
        _rls_component(),
    ]
    measured = await asyncio.gather(*probes)
    return [*measured[:4], _worker_component(), *measured[4:], _limiter_component()]


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@health_router.get(
    "/platform/health", response_model=PlatformHealthResponse, tags=["platform"]
)
async def platform_health(
    auth: AuthContext = Depends(require_infra_reader),
) -> PlatformHealthResponse:
    """Return every component's verdict with the evidence that produced it."""
    del auth
    return PlatformHealthResponse(
        components=await _components(),
        measured_at=_now(),
        not_recorded=list(_COMPONENT_GAPS),
    )


@health_router.get("/readyz", tags=["platform"])
async def readyz() -> JSONResponse:
    """Run every probe concurrently; 200 only if no **required** component is down.

    Unauthenticated, like ``/health`` and ``/ready``, because a load balancer holds no
    token. It is the deeper of the two readiness answers: ``/ready`` asks whether the
    durable substrate can accept work, this asks whether every dependency the platform
    needs actually answered.

    ``unknown`` never fails the check. A probe that timed out has not established that a
    dependency is down, and refusing traffic on an absence of evidence would make this
    endpoint flap under load — which is exactly when it matters most.
    """
    components = await _components()
    failing = [c for c in components if c.required and c.status == "down"]
    body = {
        "status": "ready" if not failing else "not_ready",
        "failing": [c.key for c in failing],
        "components": [c.model_dump() for c in components],
    }
    code = (
        status.HTTP_200_OK if not failing else status.HTTP_503_SERVICE_UNAVAILABLE
    )
    return JSONResponse(status_code=code, content=body)


@health_router.get(
    "/platform/pipeline", response_model=PipelineHealthResponse, tags=["platform"]
)
async def pipeline_health(
    tenant_id: int | None = None,
    window_hours: int = Query(default=_DEFAULT_WINDOW_HOURS, ge=1, le=720),
    auth: AuthContext = Depends(require_pipeline_reader),
) -> PipelineHealthResponse:
    """Aggregate ``job_runs`` and ``run_events`` into an honest pipeline verdict.

    Every figure is a read of a table the platform already writes. Where a figure would
    need something nobody emits — how many times a job was retried, how long a query's
    nodes took — it is absent from the numbers and present on ``not_recorded`` instead.
    """
    scope = _scope_tenant(auth, tenant_id)
    worker = _worker_state()
    sources = {
        "depth": "SELECT job_type, status, count(*) FROM job_runs GROUP BY 1, 2",
        "oldest_pending": "SELECT min(created_at) FROM job_runs WHERE status='pending'",
        "durations": (
            "finished_at - started_at over the most recent "
            f"{_JOB_SAMPLE_LIMIT} finished job_runs in the window, "
            "percentiles computed from the real samples"
        ),
        "stages": (
            "run_events WHERE event_type='ingest_stage' — payload.duration_ms, the "
            "wall clock the stage handler measured inside its own transaction"
        ),
        "scope": (
            "every query is filtered to the caller's tenant scope and runs on a "
            "connection with app.tenant_id bound"
        ),
    }
    try:
        payload = await _read_pipeline(scope, window_hours)
    except Exception as exc:  # noqa: BLE001 - an unreadable record layer is a state
        logger.debug("pipeline health read failed", exc_info=True)
        return PipelineHealthResponse(
            available=False,
            unavailable_reason=(
                f"The job and run-event tables could not be read: {exc}"
            ),
            tenant_id=scope,
            window_hours=window_hours,
            generated_at=_now(),
            worker=worker,
            sources=sources,
            not_recorded=list(_PIPELINE_GAPS),
        )
    return PipelineHealthResponse(
        available=True,
        tenant_id=scope,
        window_hours=window_hours,
        generated_at=_now(),
        worker=worker,
        sources=sources,
        not_recorded=list(_PIPELINE_GAPS),
        **payload,
    )


def _worker_state() -> WorkerState:
    """Return the worker's live state as a wire model."""
    from app.jobs.health import worker_health

    snapshot = worker_health()
    return WorkerState(
        state=snapshot.state,
        detail=snapshot.detail,
        since=snapshot.since.isoformat() if snapshot.since else None,
        restarts=snapshot.restarts,
    )


async def _read_pipeline(scope: int | None, window_hours: int) -> dict[str, Any]:
    """Read every pipeline figure inside one tenant-scoped session."""
    from aegis.jobs.models import JobRun, JobStatus
    from aegis.runs.models import RunEvent
    from sqlalchemy import func, select

    from app.data.session import get_sessionmaker, set_tenant_scope
    from app.jobs.ingest_log import INGEST_STAGE_EVENT

    since = datetime.now(UTC) - timedelta(hours=window_hours)
    now = datetime.now(UTC)

    def scoped(stmt: Any) -> Any:  # noqa: ANN401 - a SQLAlchemy Select
        """AND the caller's tenant into a statement, when it has one."""
        return stmt if scope is None else stmt.where(JobRun.tenant_id == scope)

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, scope)

        depth_rows = (
            await session.execute(
                scoped(
                    select(JobRun.job_type, JobRun.status, func.count()).group_by(
                        JobRun.job_type, JobRun.status
                    )
                )
            )
        ).all()

        oldest_pending = (
            await session.execute(
                scoped(
                    select(func.min(JobRun.created_at)).where(
                        JobRun.status == JobStatus.PENDING
                    )
                )
            )
        ).scalar()

        finished = (
            (
                await session.execute(
                    scoped(
                        select(
                            JobRun.job_type,
                            JobRun.status,
                            JobRun.started_at,
                            JobRun.finished_at,
                            JobRun.error,
                        )
                        .where(JobRun.finished_at.is_not(None))
                        .where(JobRun.finished_at >= since)
                        .order_by(JobRun.finished_at.desc())
                        .limit(_JOB_SAMPLE_LIMIT)
                    )
                )
            )
            .all()
        )

        stage_stmt = (
            select(RunEvent.payload)
            .where(RunEvent.event_type == INGEST_STAGE_EVENT)
            .where(RunEvent.ts >= since)
            .order_by(RunEvent.ts.desc())
            .limit(_STAGE_SAMPLE_LIMIT)
        )
        if scope is not None:
            stage_stmt = stage_stmt.where(RunEvent.tenant_id == scope)
        stage_payloads = (await session.execute(stage_stmt)).scalars().all()

    depth = [
        JobDepthRow(job_type=str(t), status=str(getattr(s, "value", s)), count=int(c))
        for t, s, c in depth_rows
    ]
    in_flight = sum(
        row.count
        for row in depth
        if row.status in {JobStatus.PENDING, JobStatus.RUNNING, JobStatus.RECONCILING}
    )

    oldest_age: float | None = None
    if oldest_pending is not None:
        moment = oldest_pending
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        oldest_age = max(0.0, (now - moment).total_seconds())

    durations_ms: list[float] = []
    failures: list[JobFailure] = []
    failed = 0
    for job_type, job_status, started_at, finished_at, error in finished:
        state = str(getattr(job_status, "value", job_status))
        if state == JobStatus.FAILED:
            failed += 1
            if len(failures) < 5:
                failures.append(
                    JobFailure(
                        job_type=str(job_type),
                        error=(error or "no reason recorded")[:400],
                        finished_at=(
                            finished_at.isoformat() if finished_at is not None else None
                        ),
                    )
                )
        if started_at is not None and finished_at is not None:
            start = started_at if started_at.tzinfo else started_at.replace(tzinfo=UTC)
            end = finished_at if finished_at.tzinfo else finished_at.replace(tzinfo=UTC)
            delta = (end - start).total_seconds() * 1000.0
            if delta >= 0:
                durations_ms.append(delta)

    durations = (
        DurationSummary(
            count=len(durations_ms),
            p50_ms=percentile(sorted(durations_ms), 50),
            p95_ms=percentile(sorted(durations_ms), 95),
            max_ms=max(durations_ms),
        )
        if durations_ms
        else None
    )

    by_stage: dict[str, list[float]] = {}
    for payload in stage_payloads:
        if not isinstance(payload, dict):
            continue
        stage = payload.get("stage")
        duration = payload.get("duration_ms")
        if not isinstance(stage, str) or not isinstance(duration, int | float):
            continue
        by_stage.setdefault(stage, []).append(float(duration))
    stages = [
        StageTiming(
            stage=stage,
            runs=len(samples),
            p50_ms=percentile(sorted(samples), 50),
            p95_ms=percentile(sorted(samples), 95),
            max_ms=max(samples),
        )
        for stage, samples in sorted(by_stage.items(), key=lambda kv: -sum(kv[1]))
    ]

    return {
        "depth": depth,
        "in_flight": in_flight,
        "oldest_pending_age_seconds": oldest_age,
        "oldest_pending_created_at": (
            oldest_pending.isoformat() if oldest_pending is not None else None
        ),
        "finished_in_window": len(finished),
        "failed_in_window": failed,
        # ``None``, not zero, before anything finished: a window in which no job ran is
        # not a window in which nothing failed.
        "failure_rate": (failed / len(finished)) if finished else None,
        "durations": durations,
        "stages": stages,
        "stage_events_read": len(stage_payloads),
        "recent_failures": failures,
    }


@health_router.get(
    "/platform/caches", response_model=CacheStatsResponse, tags=["metrics"]
)
async def platform_caches(
    auth: AuthContext = Depends(require_infra_reader),
) -> CacheStatsResponse:
    """Return the live per-cache counters and the configuration each instance registered.

    Every number is incremented inside the cache on the branch that decided it, so a
    figure here is the same event the cache acted on. ``hit_rate`` is ``None`` before the
    first lookup and ``evictions`` is ``None`` for a cache with no eviction this process
    can observe — neither is zero-filled.
    """
    del auth
    rows = []
    for report in cache_reports():
        spec = spec_for(report.key)
        rows.append(
            CacheRow(
                key=report.key,
                name=spec.name,
                holds=spec.holds,
                method=spec.method,
                registered=report.registered,
                backend=report.backend,
                ttl_seconds=report.ttl_seconds,
                threshold=report.threshold,
                capacity=report.capacity,
                entries=report.entries,
                lookups=report.lookups,
                hits=report.hits,
                misses=report.misses,
                writes=report.stores,
                evictions=report.evictions,
                hit_rate=report.hit_rate,
            )
        )
    return CacheStatsResponse(
        caches=rows,
        generated_at=_now(),
        source="aegis.core.cache_stats — counters incremented inside each cache",
        caveat=(
            "Per-process and in RAM: these counters start at zero when this API process "
            "starts and are not merged across workers. They are not a metrics store."
        ),
        not_recorded=list(_CACHE_GAPS),
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, exactly like :func:`app.api.routes_redteam.mount` and for the same
    reason: this module is mounted from the composition root while
    :mod:`app.api.routes` is being edited elsewhere, and a second shadowed copy of a
    handler is invisible at runtime and confusing in the route-coverage test.

    Args:
        target: The application's main router; its ``routes`` list is extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in health_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
