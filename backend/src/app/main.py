"""FastAPI application factory — the composition root of the backend.

Wires the whole platform together: mounts the API + SSE router, initialises
OpenTelemetry/Phoenix observability on startup, and enables CORS for the Next.js
console (``http://localhost:3000``). The rich OpenAPI metadata is deliberate —
``docs/architecture/backend.md`` §1 counts the auto-generated docs as free documentation
points that the AI reader parses.

Run locally with::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING

from aegis.governance.schema import SchemaDriftError
from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.routing import BaseRoute

from app.api.openapi import API_PREFIX, INFRA_PATHS, build_openapi
from app.api.routes import router
from app.api.routes_analytics import mount as _mount_analytics
from app.api.routes_checkpoints import mount as _mount_checkpoints
from app.api.routes_compliance import mount as _mount_compliance
from app.api.routes_db import mount as _mount_db
from app.api.routes_guardrails import mount as _mount_guardrails
from app.api.routes_health import mount as _mount_health
from app.api.routes_llmops import mount as _mount_llmops
from app.api.routes_mcp import mount as _mount_mcp
from app.api.routes_memory import mount as _mount_memory
from app.api.routes_notifications import mount as _mount_notifications
from app.api.routes_pipelines import mount as _mount_pipelines
from app.api.routes_redteam import mount as _mount_redteam
from app.api.routes_reports import mount as _mount_reports
from app.api.routes_skills import mount as _mount_skills
from app.api.routes_standards import mount as _mount_standards

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
from app.api.routes_seats import mount as _mount_seats
from app.config import get_settings
from app.observability import init_observability

# The red-team control plane (§7.13). This line belongs at the bottom of
# ``app.api.routes`` beside the ``ingest_log`` / ``routes_console`` mounts and has the
# identical effect from here — the routes land on ``router`` itself, so the served
# table stays one table. ``mount`` is idempotent, so moving it there later is a
# one-line change that cannot double-register anything in the meantime.
_mount_redteam(router)

# The named-seat surface (§7.8) — tenant sub-roles were cut, so a seat is a named grant
# in the settings table and this is the only route that can write one *for somebody
# else*. Same shape, same reason, same idempotent mount.
_mount_seats(router)

# Embedded analytics (Apache Superset). Same shape, same reason, same idempotent mount.
# Nothing in that module runs until its first request: Superset is optional, and a
# deployment without it must boot and behave exactly as it did before.
_mount_analytics(router)

# The per-tenant prompt control plane (§7.7) — the surface the tenant-keyed registry
# read path made safe to build. Same shape, same reason, same idempotent mount.
_mount_llmops(router)

# The memory control plane (§7.5) — the write, the correction and the retention horizon
# that the read-only ``/memory/*`` surfaces in ``app.api.routes`` never had. Same shape,
# same reason, same idempotent mount.
_mount_memory(router)

# The guardrail control plane (§7.6) — the effective rail stack with each control's
# provenance, so an operator can see what the rails do and who decided it. Same shape,
# same reason, same idempotent mount.
_mount_guardrails(router)

# Pipeline health, /readyz and the live cache counters (§7.10 / §7.10b / §7.14). Same
# shape and the same idempotent mount: the routes land on ``router`` itself, so the
# served table stays one table, and nothing in that module dials a dependency until a
# request asks it to.
_mount_health(router)

# Downloadable reports (§7.12) — the four CSV exports and the short-lived download
# ticket a browser navigation needs. Same shape and the same idempotent mount: the
# routes land on ``router`` itself, so the served table stays one table.
_mount_reports(router)

# The database console (§7.9) — the schema browser and the closed set of parameterised
# reads, over a connection that holds SELECT and nothing else. Same shape and the same
# idempotent mount. Nothing in that module opens its connection until a request asks it
# to, and it opens none at all unless AEGIS_DB_CONSOLE_ENABLED and AEGIS_DB_CONSOLE_DSN
# are both set: the console is off by default, in every environment.
_mount_db(router)

# The pipeline declarations (§8.12) — the three flows Aegis runs, their stages, the
# module that owns each, and what each emits, served from ``aegis.pipelines`` and
# verified against the code before a byte of it goes out. Same shape and the same
# idempotent mount; it reads no database and dials nothing.
_mount_pipelines(router)

# The MCP control plane (§10.6/10.7) — the declared external tool servers, the tools
# discovered on them, and the tier each is gated at. Same shape and the same idempotent
# mount. It dials nothing until a platform admin presses discover: declaring a peer says
# where to look, and reaching one is an explicit act.
_mount_mcp(router)

# The skills control plane (§10.1-10.3) — authoring a SKILL.md at the platform, tenant
# or user layer, and putting it in force. Same shape and the same idempotent mount. It
# opens no connection until a request asks it to, and the input rail it screens an
# authored body with is the platform's already-bound one.
_mount_skills(router)

# The alert surface — the durable notification inbox plus the SSE stream that pushes
# into it. Same shape and the same idempotent mount. It opens no connection at import:
# the Redis subscription behind the stream is started by the lifespan below (and lazily
# by the first publish, so a worker process that never runs a lifespan still gets it).
_mount_notifications(router)

# The run's LangGraph checkpoint chain (ADR 0005) — the read-only evidence that the
# human gate parks on a checkpoint and that a resume continues from it rather than
# re-running the graph. Same shape and the same idempotent mount; it reads the shared
# checkpoint store and returns structure only, never the state a checkpoint holds.
_mount_checkpoints(router)

# The compliance-readiness map — nine published frameworks, a four-valued state per
# control, and the file / route / test behind every claim. Same shape and the same
# idempotent mount. Pure data assembly grounded in ``docs/compliance/README.md``: it
# reads no database, dials nothing, and carries its "readiness, not certification"
# disclaimer on every response.
_mount_compliance(router)

# The public standards summary — the same control table as above, counted and stripped
# to names, jurisdictions and the four state totals. Public because the landing page is
# public; free of control detail because a control-by-control gap map is a target list.
# Same shape and the same idempotent mount.
_mount_standards(router)

logger = logging.getLogger(__name__)

_DESCRIPTION = """\
**Aegis** — a domain-agnostic agentic platform: cheap-to-scale, trustworthy, secure
and auditable. Every capability is a first-class **Aegis module**, presented with a
branded name **plus its honest underlying tech** (branding, never hiding):

- **Aegis Gateway** (LiteLLM) — single model chokepoint: routing, budgets, retry, usage ledger.
- **Aegis Router** (LangGraph) — multi-agent supervisor routing each turn to a specialist.
- **Aegis Memory** (Postgres + embedded vectors) — bitemporal episodic/semantic/procedural memory.
- **Aegis Cache** (Redis) — semantic response cache.
- **Aegis Retrieval** (Neo4j/LightRAG + embedded vectors) — hybrid vector+graph+BM25, RRF, rerank.
- **Aegis Signal** (XGBoost + MAPIE + SHAP) — calibrated conformal intervals + SHAP explanations.
- **Aegis Guardrails** (programmatic + NeMo Colang) — input/output rails: injection, PII, schema.
- **Aegis Evals** (RAGAS-style proxies + LLM judge) — trace-level and answer evaluation.
- **Aegis Loop** (native) — LLM-Ops self-improvement: trace → eval → diagnose → tiered release.
- **Aegis Governance** (Postgres RLS + JWT) — multi-tenant RBAC, budgets, RLS, audit log.
- **Aegis Trace** (OpenTelemetry → Phoenix) — end-to-end, glass-box tracing.
- **Aegis Tools / MCP** (native + MCP SDK) — risk-tiered tool registry + human gate over MCP.

The live manifest is served at `GET /platform/capabilities` (and `GET /about`).
Every autonomous action is uncertainty-bounded (conformal prediction), explainable
(SHAP), guarded, gated (human-in-the-loop) and fully traced.
"""

# Origins allowed to call the API from the browser: the Next.js dev server (:3000,
# the current web app) and the legacy Vite dev server (:5173, the old frontend).
# Without the :3000 entries the browser probe fails CORS and the web app silently
# falls back to its offline mock fixtures.
#: How long shutdown waits for the Temporal worker to drain in-flight activities before
#: cancelling it. Generous enough for a record-layer write to commit, short enough that a
#: wedged activity cannot hold the process open indefinitely.
_WORKER_DRAIN_SECONDS = 10.0

#: First retry delay after the worker dies. Short, because the overwhelmingly common cause
#: is "the developer had not started Temporal yet", and the recovery should feel immediate
#: once they do.
_WORKER_RETRY_MIN_SECONDS = 1.0

#: The retry ceiling. Doubling stops here so a long outage costs one dial every half
#: minute — often enough that recovery is picked up while a person is still watching,
#: rare enough that a genuinely absent orchestrator is not hammered.
_WORKER_RETRY_MAX_SECONDS = 30.0

_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


def _supervise(task: asyncio.Task[None], name: str) -> None:
    """Log loudly if a long-lived background task ever stops on its own.

    A bare ``asyncio.create_task`` is a silent-failure seam: if the coroutine raises
    before (or outside) its own ``try`` the task just ends, and the exception surfaces
    only as a ``Task exception was never retrieved`` message at GC time — possibly
    never. The process then keeps serving traffic with a dead sweeper and no signal.
    This done-callback turns that into an explicit ERROR. Normal shutdown (the task is
    cancelled in the lifespan's ``finally``) is not an error and stays quiet.

    Args:
        task: The background task to supervise.
        name: Human name used in the log line.
    """

    def _done(finished: asyncio.Task[None]) -> None:
        if finished.cancelled():
            return
        exc = finished.exception()
        if exc is not None:
            logger.error("Background task %s died: %r", name, exc, exc_info=exc)
        else:
            logger.error("Background task %s stopped unexpectedly.", name)

    task.add_done_callback(_done)


async def run_worker_supervised(stop: asyncio.Event) -> None:
    """Run the Temporal worker, and keep re-running it until ``stop`` is set.

    **The defect this replaces.** ``start_worker_task`` created the worker as a bare task
    and :func:`_supervise` logged its death. With Temporal down at boot that produced one
    ERROR line and then a permanently dead substrate: starting Temporal afterwards did not
    bring the worker back, because nothing was left to try. The API meanwhile went on
    accepting uploads into a queue with no consumer, and ``/health`` went on saying ``ok``.

    **Both halves of the fix live here.** The loop is the *supervision*: a capped
    exponential backoff, retried for as long as the process lives, so an orchestrator that
    comes back is picked up without a restart. :mod:`app.jobs.health` is the *surface*: the
    state this loop writes is what ``GET /health`` and ``GET /ready`` report, so the outage
    is visible while it lasts rather than only in a log line nobody tailed. Restarting
    without reporting would hide the outage; reporting without restarting would name a
    problem that needs a process bounce to clear.

    The connection is dialled *before* the workers are built, and deliberately: it is the
    step that actually fails, and doing it here means "we are connected" is a fact this
    function established rather than one it assumed. The cached client is dropped on
    failure so the next attempt genuinely re-dials instead of re-finding a dead singleton.

    Args:
        stop: The lifespan's shutdown event. Set it and this returns after the workers
            have drained; it is also the thing waited on between retries, so shutdown
            during a backoff is immediate rather than up to 30 seconds late.
    """
    from app.jobs.client import get_temporal_client, reset_temporal_client
    from app.jobs.health import (
        WORKER_DOWN,
        WORKER_RUNNING,
        WORKER_STARTING,
        WORKER_STOPPED,
        note_worker_restart,
        set_worker_state,
    )
    from app.jobs.worker import run_workers

    delay = _WORKER_RETRY_MIN_SECONDS
    attempt = 0
    while not stop.is_set():
        if attempt:
            note_worker_restart()
        attempt += 1
        try:
            set_worker_state(WORKER_STARTING)
            await get_temporal_client()
            set_worker_state(WORKER_RUNNING)
            delay = _WORKER_RETRY_MIN_SECONDS  # a good connect forgets the old backoff
            await run_workers(stop)
            set_worker_state(WORKER_STOPPED)
            return
        except asyncio.CancelledError:
            set_worker_state(WORKER_STOPPED)
            raise
        except Exception as exc:  # noqa: BLE001 - a dead worker must be retried, not fatal
            reset_temporal_client()
            set_worker_state(WORKER_DOWN, detail=str(exc))
            logger.error(
                "Temporal worker is down; retrying in %.0fs. %s",
                delay,
                exc,
                exc_info=True,
            )
        try:
            await asyncio.wait_for(stop.wait(), timeout=delay)
        except TimeoutError:
            delay = min(delay * 2, _WORKER_RETRY_MAX_SECONDS)
    set_worker_state(WORKER_STOPPED)


async def _rls_scope(session: AsyncSession, tenant_id: int | None) -> None:
    """Bind the per-request Postgres RLS scope, resolved late through the shim module.

    ``app.data.governance`` re-exports ``set_tenant_scope`` as a module-level name and
    resolves it on every call, so ``monkeypatch.setattr(app.data.governance,
    "set_tenant_scope", spy)`` observes every governed write's tenant scope (the H1 test
    seam). Passing ``aegis.governance.rls.set_tenant_scope`` here would bypass that
    module and make the seam — and every test built on it — vacuous.
    """
    from app.data import governance

    await governance.set_tenant_scope(session, tenant_id)


async def _run_memory_sweeper(stop: asyncio.Event) -> None:
    """Drain the durable memory-consolidation queue until ``stop`` is set.

    Mirrors :func:`app.data.run_sla_sweeper`: each cycle opens a short-lived session and
    calls :func:`app.memory.consolidate.sweep_pending` once, then waits the configured
    interval (or until ``stop`` fires). Every error is logged and swallowed so a
    transient database blip never kills the sweeper. Bound to the live LLM ``complete``
    and the retrieval embedding function, exactly like the request-path consolidation.

    **And governed like the request path too, since task 9.2** — which it was not
    before: this loop binds the live completer and the real embedder on a sixty-second
    timer, and it bound no governance context at all, so every consolidation it drained
    spent a tenant's money against no cap and left no ledger row. The context
    is bound *per job* inside :func:`~aegis.memory.consolidate.sweep_pending`, because
    one drain covers several tenants and this loop is in no position to know whose work
    it is about to run.
    """
    settings = get_settings()
    period = settings.memory_sweeper_interval_seconds
    limit = settings.memory_sweeper_batch
    from app.core.llm import complete
    from app.data.session import get_sessionmaker
    from app.memory.config import MemoryConfig
    from app.memory.consolidate import sweep_pending
    from app.retrieval.gateway import default_embed

    config = MemoryConfig()
    embed = default_embed()
    while not stop.is_set():
        try:
            async with get_sessionmaker()() as session:
                await sweep_pending(
                    session, config=config, complete=complete, embed=embed, limit=limit
                )
        except Exception:  # noqa: BLE001 - the sweeper must survive transient errors
            logger.warning("Memory consolidation sweep failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=period)
        except TimeoutError:
            continue


async def _run_memory_retention(stop: asyncio.Event) -> None:
    """Enforce the memory retention horizon on a timer until ``stop`` is set.

    The other half of the memory sweeper's job, and deliberately a separate task from
    it: consolidation drains a queue every minute and must stay cheap, while retention
    is a bounded set of DELETEs that only has anything to do once a day. Running them
    on one clock would either sweep retention sixty times an hour for nothing or slow
    consolidation to retention's cadence.

    This is what makes retention a *policy* rather than a button somebody remembers to
    press. :func:`app.api.routes_memory.sweep_retention_everywhere` is the same function
    ``POST /memory/retention/sweep`` calls, so the timer and the operator can never
    enforce two different horizons. Every error is logged and swallowed — a transient
    database blip must never kill the task, and a deletion that did not happen is
    strictly safer than one that half did.
    """
    from app.api.routes_memory import sweep_retention_everywhere

    period = get_settings().memory_retention_sweep_interval_seconds
    while not stop.is_set():
        try:
            removed = await sweep_retention_everywhere()
            if any(removed.values()):
                logger.info("Memory retention swept %s", removed)
        except Exception:  # noqa: BLE001 - the sweeper must survive transient errors
            logger.warning("Memory retention sweep failed", exc_info=True)
        try:
            await asyncio.wait_for(stop.wait(), timeout=period)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialise observability and (optionally) the database schema on startup.

    Both steps degrade gracefully: observability falls back to a no-op provider
    if Phoenix is absent, and — when ``DB_BOOTSTRAP`` is enabled — table creation
    is best-effort so an unreachable database never blocks startup (the audit
    sink is already best-effort at the edge).

    **One bootstrap failure is not degradable and must not start the API.**
    :class:`~aegis.governance.schema.SchemaDriftError` means the database is
    perfectly reachable but a live table is missing a column that cannot be added
    additively. For ``usage_ledger`` that is not a degraded feature: every ledger
    INSERT raises, the gateway swallows it (usage recording is best-effort by
    design), the rows vanish, and the USD budget caps computed by summing them stop
    binding — the system serves paid model calls with no spend ceiling and no
    record. Booting anyway would be the silent failure this exception exists to
    prevent, so it propagates.

    **The second non-degradable failure is an inert tenant-isolation control.**
    :func:`app.data.session.verify_rls_enforcement` asks the database whether the role
    serving requests can bypass Row-Level Security. In dev that is logged at ERROR and
    the process continues; anywhere else it raises
    :class:`~aegis.governance.rls.RlsBypassError` and the API does not start, because a
    deployment whose isolation policies are enforced against nobody would still *look*
    healthy on every dashboard.
    """
    init_observability(app)
    settings = get_settings()

    # What is actually bounding model concurrency in this process, said out loud at boot
    # (task 9.3). The word that matters is ``scope``: ``fleet`` means the leases live in
    # a store every process shares, ``process`` means this interpreter only, and
    # ``unlimited`` means nothing is holding the line. An operator reading a number
    # without that word would assume the strongest of the three.
    from aegis.gateway import limiter_status

    logger.info("Model-call limiter: %s", limiter_status())
    if settings.db_bootstrap:
        try:
            from app.data.session import bootstrap

            await bootstrap()
        except SchemaDriftError:
            logger.critical(
                "DB bootstrap FAILED: irreconcilable schema drift. Refusing to serve "
                "— the usage ledger may be unwritable, which disables every USD "
                "budget cap.",
                exc_info=True,
            )
            raise
        except Exception:  # noqa: BLE001 - the database is optional; degrade cleanly
            logger.warning("DB bootstrap skipped — database unreachable.", exc_info=True)

    # Build the agent's checkpoint store now rather than on the first run. With
    # ``AGENT_CHECKPOINTER=postgres`` this is where the checkpoint tables are migrated
    # and the serving role granted, and a failure there (bad DSN, no CREATE rights)
    # belongs in the boot log — lazily, it would surface as a 500 in the middle of the
    # first run, after the user had already asked a question. The ``memory`` default
    # builds an ``InMemorySaver`` and touches nothing.
    try:
        from app.data.session import get_agent_checkpointer

        logger.info(
            "Agent checkpointer: %s (%s)",
            settings.agent_checkpointer,
            type(get_agent_checkpointer()).__name__,
        )
    except Exception:  # noqa: BLE001 - a checkpointer is required; say so and stop
        logger.critical(
            "Agent checkpointer FAILED to initialise. Refusing to serve — every run "
            "needs a checkpoint store, and the human gate's interrupt/resume needs a "
            "durable one.",
            exc_info=True,
        )
        raise

    # Is the tenant-isolation control actually ON? Postgres skips row security entirely
    # for a SUPERUSER/BYPASSRLS role, so serving requests as ``postgres`` leaves every
    # tenant_isolation policy installed and enforced against nobody — which is how this
    # platform ran until the owner/serving DSN split. The check is deliberately NOT
    # inside a try/except here: ``verify_rls_enforcement`` catches connection failures
    # itself (an absent database still starts, as documented) and lets the bypass
    # verdict through, so a broad handler at this level is exactly what would make the
    # diagnostic unable to fire. Outside dev it raises and the API does not start.
    if settings.stores_enabled:
        from app.data.session import verify_rls_enforcement

        await verify_rls_enforcement()

    # State the at-rest posture where an operator actually looks, for the reason
    # ``verify_rls_enforcement`` logs its verdict: a control whose absence is only
    # visible to somebody who goes looking is one nobody finds until an auditor does.
    # Never fatal — unlike an inert RLS policy this is a fact about the host's storage,
    # which the process cannot fix by refusing to start.
    from app.platform.at_rest import at_rest_summary  # noqa: PLC0415 - startup-only

    logger.info("%s", at_rest_summary(settings))

    # Load every ACTIVE prompt version into the LLM-Ops registry's process-wide cache
    # so the harness reads a live, promoted system prompt synchronously on the hot path
    # (falling back to the adapter default when none is active). Best-effort and gated on
    # the real stores; a failure never blocks startup.
    if settings.stores_enabled:
        try:
            from app.data.session import get_sessionmaker
            from app.ops import registry

            async with get_sessionmaker()() as session:
                loaded = await registry.refresh_cache(session)
            logger.info("LLM-Ops registry cache warmed: %d active prompt(s).", loaded)
        except Exception:  # noqa: BLE001 - the registry cache is best-effort at startup
            logger.warning("Prompt registry cache refresh skipped.", exc_info=True)

    # Hydrate the external MCP connections an operator declared through the console
    # (§10.6). Without this they would exist only until the process that created them
    # stopped, which is not a connection anybody can rely on. Best-effort and gated on
    # the real stores: an unreachable database means the deployment falls back to the
    # peers named in ``AEGIS_MCP_CLIENT_SERVERS`` and nothing else — fewer peers, never
    # a peer nobody declared. Hydrating a connection reaches no third party: discovery
    # and admission are separate, explicit acts.
    if settings.stores_enabled:
        try:
            from app.api.routes_mcp import load_servers

            logger.info("MCP connections hydrated: %d external server(s).", await load_servers())
        except Exception:  # noqa: BLE001 - a peer list is never worth failing a boot
            logger.warning("MCP connection hydration skipped.", exc_info=True)

    # ── The one way up (§8.3) ────────────────────────────────────────────────
    # One call replaces the ten ordered ``configure_*`` reaches this composition root
    # used to make — the vector-store pair that lived here, and the three that fired as
    # import side effects of ``app.core.llm``, ``app.core.security`` and ``app.ops``.
    # The ordering that used to be a reader's problem is ``aegis.runtime``'s now, and the
    # first thing it does is verify ``app.adapter`` against ``aegis.adapter.DomainAdapter``
    # — so a missing piece is named here, at boot, instead of surfacing three layers away
    # on the first turn that needed it. (``missing_members(app.adapter)`` returned
    # ``['memory_spec']`` on our own reference adapter with no error anywhere.)
    #
    # ``AEGIS_MODE`` is deliberately not read: this deployment already has one source of
    # truth for dev-vs-production, and duplicating it into a second variable is how two
    # of them drift apart. Every infra value is passed for the same reason.
    #
    # Honest infra, unchanged: a non-dev full-stores deployment REQUIRES a usable
    # Qdrant node (``QDRANT_URL``) and refuses to boot
    # without one, exactly like Postgres/Redis — an embedded store is single-process
    # and cannot serve ``--workers>1``. A dev/lite process gets
    # the EPHEMERAL engine because this call asks for it — and ``from_env`` logs which
    # one it chose, at WARNING, so a non-durable process is never a quiet one.
    from aegis import Aegis

    from app.data.approvals import enqueue_approval
    from app.data.models import Approval, ApprovalStatus
    from app.data.session import get_sessionmaker

    await Aegis.from_env(
        adapter="app.adapter",
        mode="full" if (settings.stores_enabled and not settings.is_dev) else "lite",
        # One value serves both LightRAG and Aegis (QDRANT_URL), so there is one
        # number to set rather than two to drift apart. Full-stores boot refuses
        # without it rather than falling back to an embedded store.
        vector_store_url=settings.qdrant_url,
        vector_store_path=settings.vector_store_path,
        database_url=settings.postgres_dsn,
        redis_url=settings.redis_url,
        # The host owns the engine, the pool and *which Postgres role serves requests* —
        # the split that made RLS enforceable at all. Late-binding on purpose: it
        # re-resolves the sessionmaker per call, so a test that swaps the engine is
        # honoured, which is the behaviour ``app.data.governance``'s wiring already had.
        session_factory=lambda: get_sessionmaker()(),
        set_tenant_scope=_rls_scope,
        enqueue_approval=enqueue_approval,
        approval_model=Approval,
        approval_status=ApprovalStatus,
        jwt_secret=settings.jwt_secret,
        jwt_algorithm=settings.jwt_algorithm,
        jwt_expire_minutes=settings.jwt_expire_minutes,
    )

    # The notification fan-out. Started here so the Redis subscription exists before the
    # first SSE connection rather than being raced into life by it, and so a Redis that
    # is down says so once, at boot, instead of once per stream. It never raises: an
    # unreachable Redis degrades the bus to in-process delivery and logs exactly what
    # that costs (see :mod:`app.notifications`). The rows stay durable in Postgres
    # either way, which is why this is not a startup gate.
    from app.notifications import get_bus

    notification_bus = get_bus()
    await notification_bus.start()

    # The SLA sweeper (§1.3): an in-process asyncio task — no cron, no Docker — that
    # expires past-deadline approvals and auto-rejects HIGH-risk ones. Only runs with
    # the real stores; the offline "lite" demo and tests skip it.
    sweeper_stop = asyncio.Event()
    sweeper_task: asyncio.Task[None] | None = None
    memory_sweeper_task: asyncio.Task[None] | None = None
    retention_task: asyncio.Task[None] | None = None
    if settings.stores_enabled:
        from app.data import run_sla_sweeper

        sweeper_task = asyncio.create_task(run_sla_sweeper(sweeper_stop))
        _supervise(sweeper_task, "sla-sweeper")
        # The memory consolidation sweeper (§D): an in-process asyncio task that drains
        # the durable ``memory_consolidation_job`` queue — the backstop for background
        # consolidations lost on restart/error. Same posture as the SLA sweeper: only
        # with the real stores; the lite demo and tests skip it.
        memory_sweeper_task = asyncio.create_task(
            _run_memory_sweeper(sweeper_stop)
        )
        _supervise(memory_sweeper_task, "memory-consolidation-sweeper")
        # The retention horizon (§7.5). Memory is the one subsystem built entirely to
        # keep things — supersession instead of overwrite, a soft prune instead of a
        # delete, an append-only write log — so without this task the store grows for
        # the life of the deployment and "how long do you keep what I said" has no
        # honest answer. Same posture again: real stores only.
        retention_task = asyncio.create_task(_run_memory_retention(sweeper_stop))
        _supervise(retention_task, "memory-retention-sweeper")

    # The durable job substrate (§3.2): the Temporal worker, as an asyncio task in this
    # process — the in-process launch mode, identical in code path to
    # ``python -m app.jobs.worker``. Gated on the real stores because every activity
    # writes to the record tables.
    #
    # **Supervised means restarted, not merely mourned.** It used to be a bare task whose
    # death was logged once; with Temporal down at boot that left a permanently dead
    # substrate that starting Temporal afterwards could not revive, while ``/health`` went
    # on saying ok. :func:`run_worker_supervised` re-runs it with backoff and publishes its
    # state to :mod:`app.jobs.health`, which ``/health`` and ``/ready`` now report. The API
    # keeps serving either way — but it no longer claims to be fine while it is not.
    worker_task: asyncio.Task[None] | None = None
    if settings.stores_enabled and settings.temporal_worker_inprocess:
        from app.ingestion.reindex import register_corpus_reindex_handler
        from app.ingestion.stages import register_ingest_handlers

        # The composition root: this is where the *work* of the ingest stages is bound to
        # the substrate that runs them. It is here rather than inside ``run_workers``
        # because that function is the bootstrap both launch modes share, and starting a
        # worker must not silently replace handlers a host has already registered — the
        # substrate's registry is deliberately a seam, not a hard-coded table.
        register_ingest_handlers()
        # The other half of the same seam, and the same reasoning: the scheduled re-index
        # is Phase 3's machinery with no work of its own, and this binds Phase 4's work to
        # it. Without this line every cadence tick raises rather than recording a
        # ``succeeded`` re-index that rebuilt nothing.
        register_corpus_reindex_handler()
        worker_task = asyncio.create_task(
            run_worker_supervised(sweeper_stop), name="temporal-worker"
        )
        # Still supervised by the done-callback as well: the retry loop handles the worker
        # dying, and this catches the retry loop itself dying — a bug in the supervisor
        # would otherwise be the one silent failure the supervisor cannot report.
        _supervise(worker_task, "temporal-worker-supervisor")

    # Warm the ML spine off the hot path: load the artifact (or train it from the
    # real domain frame if absent) in a worker thread so the first live query never
    # pays the fit cost. Best-effort — a failure here never blocks the API.
    async def _warm_ml() -> None:
        try:
            from app.ml import get_model

            await asyncio.to_thread(get_model)
        except Exception:  # noqa: BLE001 - the ML signal is best-effort, never gating
            logger.warning("ML spine warm-up skipped.", exc_info=True)

    # The MCP front door (§10.4). The SDK's Streamable HTTP app owns a lifespan — the
    # ``StreamableHTTPSessionManager`` task group every MCP session's dispatch loop runs
    # in — and a mounted ASGI app never receives lifespan events from its host, so it is
    # entered here. Without this the mount would accept a POST and then hang on a task
    # group that was never started. The exit stack (rather than an ``async with`` around
    # this whole function) keeps the change to three lines in a lifespan several other
    # lanes edit.
    exits = AsyncExitStack()
    mcp_mount = getattr(app.state, "mcp_mount", None)
    if mcp_mount is not None:
        await exits.enter_async_context(mcp_mount.running())

    warm_task = asyncio.create_task(_warm_ml())
    try:
        yield
    finally:
        await exits.aclose()
        warm_task.cancel()
        # Cancel the bus's Redis reader before the loop closes. Without this the task is
        # torn down by interpreter exit and asyncio reports "Task was destroyed but it is
        # pending" — a shutdown-time diagnostic that looks like a defect and is not.
        await notification_bus.stop()
        if (
            sweeper_task is not None
            or memory_sweeper_task is not None
            or retention_task is not None
            or worker_task is not None
        ):
            sweeper_stop.set()
        if sweeper_task is not None:
            sweeper_task.cancel()
        if memory_sweeper_task is not None:
            memory_sweeper_task.cancel()
        if retention_task is not None:
            retention_task.cancel()
        if worker_task is not None:
            # Not cancelled outright: ``run_workers`` reacts to the stop event with a
            # graceful ``Worker.shutdown``, which lets an in-flight activity finish its
            # transaction rather than having it torn out mid-stage. Bounded, and via
            # ``asyncio.wait`` rather than ``await``, for two reasons — a wedged activity
            # must not hang shutdown forever, and a worker that already died must not
            # re-raise its exception out of the lifespan's ``finally`` (the supervisor
            # callback has already logged it).
            finished, _ = await asyncio.wait({worker_task}, timeout=_WORKER_DRAIN_SECONDS)
            if not finished:
                logger.warning(
                    "Temporal worker did not drain within %.0fs; cancelling.",
                    _WORKER_DRAIN_SECONDS,
                )
                worker_task.cancel()


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        A fully-wired :class:`~fastapi.FastAPI` app with the router mounted,
        CORS enabled for the frontend, and observability started on lifespan.

    Raises:
        InsecureConfigurationError: When a non-dev deployment carries a default or
            too-short ``JWT_SECRET`` (fail-fast startup guard; §3.3, H4).
    """
    settings = get_settings()
    # Fail-fast: refuse to boot a non-dev deployment on an insecure signing secret.
    settings.ensure_secure_secrets()
    # Fail-fast: refuse to boot a non-dev deployment whose spend caps do not bind —
    # fail-open budgets, or no governance hook at the gateway chokepoint at all.
    settings.ensure_spend_caps_bind()
    # Honour the configured LOG_LEVEL knob so the app's log verbosity is actually
    # driven by settings (INFO by default). ``force`` re-points the root handler even
    # if a library already configured logging at import time. An unknown level name
    # falls back to INFO rather than raising.
    level = logging.getLevelName(settings.log_level.strip().upper())
    logging.basicConfig(
        level=level if isinstance(level, int) else logging.INFO, force=True
    )
    app = FastAPI(
        title="Aegis — Agentic Platform API",
        description=_DESCRIPTION,
        version="0.1.0",
        contact={"name": "Aegis Platform Team"},
        license_info={"name": "Proprietary"},
        openapi_tags=[
            {
                "name": "platform",
                "description": "Product identity: the honest Aegis capabilities manifest "
                "(branded module names paired with their real tech).",
            },
            {"name": "auth", "description": "Login and role/token issuance."},
            {"name": "agent", "description": "Streaming agent runs and approvals."},
            {"name": "graph", "description": "The live knowledge-graph context."},
            {"name": "ml", "description": "Conformalised, explainable predictions."},
            {"name": "metrics", "description": "Efficiency and cost dashboard."},
            {
                "name": "memory",
                "description": "Long-term memory read/admin surfaces + GDPR erasure.",
            },
            {
                "name": "ops",
                "description": "LLM-Ops closed loop: prompt registry, trace-eval trend, "
                "diagnose → release → rollback, and the staged-release inbox.",
            },
        ],
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # §8.6 — the version boundary. Every product route is served under ``/v1``; the
    # three infrastructure probes stay at the root and are served at exactly one path
    # each. See ``app.api.openapi`` for why the probes are the exception.
    #
    # The split is done here, at the composition root, rather than by giving ``router``
    # a prefix: the control planes attach themselves by extending ``router.routes``
    # (``app.api.ingest_log.mount`` explains why it is not ``include_router``), and an
    # ``APIRouter(prefix=...)`` applies its prefix only to routes declared *on* it — so
    # a prefix there would have versioned ``app.api.routes`` and silently left every
    # mounted plane at the root. Both routers below share the route objects with
    # ``router`` and neither mutates them, so ``tests/api/test_route_coverage.py`` goes
    # on reading the unprefixed table it was written against.
    versioned, infra = _split_infra_probes(router)
    app.include_router(versioned, prefix=API_PREFIX)
    app.include_router(infra)
    # §10.4 — the MCP front door, served over Streamable HTTP at ``/mcp/mcp``. It is a
    # MOUNT rather than a FastAPI route because the ``mcp`` SDK hands us a complete ASGI
    # application: the transport, the bearer-auth middleware chain, the session manager
    # and its lifespan. Re-hosting that inside a route would mean re-implementing the
    # parts we deliberately adopted. It carries no allowlist entry in
    # ``tests/api/test_route_coverage.py`` because it is not a portal route at all —
    # it is a protocol endpoint whose only clients speak MCP.
    #
    # Same auth, same tokens, same governance as ``/v1``: see ``app.mcp.server``.
    try:
        from app.mcp.server import MCP_MOUNT, McpTransportMount
    except ImportError:  # pragma: no cover - the mcp SDK is an optional dependency
        logger.warning("MCP server not mounted: the `mcp` SDK is not installed.")
    else:
        # The mount is a restartable *slot*, not the transport itself: the SDK's session
        # manager may only be run once, and this lifespan is entered more than once (by
        # the suite, and by any host that restarts). See ``McpTransportMount``.
        app.state.mcp_mount = McpTransportMount()
        app.mount(MCP_MOUNT, app.state.mcp_mount)
    # The served document, with the ``StreamEvent`` union published into it (§8.8).
    # ``backend/openapi.json`` is a committed snapshot of exactly this.
    app.openapi = lambda: build_openapi(app)  # type: ignore[method-assign]
    return app


def _split_infra_probes(source: APIRouter) -> tuple[APIRouter, APIRouter]:
    """Split one router into the versioned surface and the unversioned probes.

    Args:
        source: The single router every route in this API is registered on.

    Returns:
        ``(versioned, infra)`` — the product routes, and the ``/health`` / ``/ready``
        / ``/readyz`` probes, as two routers over the *same* route objects.

    Raises:
        RuntimeError: When a path in :data:`~app.api.openapi.INFRA_PATHS` is not
            actually served. The set names the URLs a load balancer is configured
            with; an entry that stopped existing would move a probe under ``/v1``
            without one test noticing, so the mismatch is fatal at import time.
    """

    def _is_probe(route: BaseRoute) -> bool:
        return getattr(route, "path", None) in INFRA_PATHS

    versioned = APIRouter()
    versioned.routes.extend(r for r in source.routes if not _is_probe(r))
    infra = APIRouter()
    infra.routes.extend(r for r in source.routes if _is_probe(r))

    served = {getattr(r, "path", None) for r in infra.routes}
    missing = sorted(INFRA_PATHS - served)
    if missing:
        raise RuntimeError(
            f"these infrastructure probes are declared unversioned but no longer "
            f"served: {missing}. A load balancer is dialling them at the root."
        )
    return versioned, infra


app = create_app()
