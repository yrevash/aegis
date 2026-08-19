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
from contextlib import asynccontextmanager

from aegis.governance.schema import SchemaDriftError
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.routes_analytics import mount as _mount_analytics
from app.api.routes_health import mount as _mount_health
from app.api.routes_llmops import mount as _mount_llmops
from app.api.routes_memory import mount as _mount_memory
from app.api.routes_redteam import mount as _mount_redteam
from app.config import get_settings
from app.observability import init_observability

# The red-team control plane (§7.13). This line belongs at the bottom of
# ``app.api.routes`` beside the ``ingest_log`` / ``routes_console`` mounts and has the
# identical effect from here — the routes land on ``router`` itself, so the served
# table stays one table. ``mount`` is idempotent, so moving it there later is a
# one-line change that cannot double-register anything in the meantime.
_mount_redteam(router)

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

# Pipeline health, /readyz and the live cache counters (§7.10 / §7.10b / §7.14). Same
# shape and the same idempotent mount: the routes land on ``router`` itself, so the
# served table stays one table, and nothing in that module dials a dependency until a
# request asks it to.
_mount_health(router)

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


async def _run_memory_sweeper(stop: asyncio.Event) -> None:
    """Drain the durable memory-consolidation queue until ``stop`` is set.

    Mirrors :func:`app.data.run_sla_sweeper`: each cycle opens a short-lived session and
    calls :func:`app.memory.consolidate.sweep_pending` once, then waits the configured
    interval (or until ``stop`` fires). Every error is logged and swallowed so a
    transient database blip never kills the sweeper. Bound to the live LLM ``complete``
    and the retrieval embedding function, exactly like the request-path consolidation.
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

    # Honest infra: a non-dev full-stores deployment REQUIRES a usable vector store — the
    # ANN engine behind retrieval + memory recall — exactly like Postgres/Redis. The store
    # is *embedded* (in-process, file-backed under ``VECTOR_STORE_PATH``), so there is no
    # server to install or reach; the failure it can still have is an unusable directory.
    # Construction is therefore deliberately NOT wrapped in a try/except: an unwritable or
    # corrupt store raises here and the process refuses to boot, rather than degrading to
    # a silent, non-durable RAM index. Dev/tests keep the ephemeral in-memory engine (the
    # sanctioned offline path), so this block is gated on ``not is_dev``.
    if settings.stores_enabled and not settings.is_dev:
        from aegis.memory import MemoryVectorIndex, set_default_index

        set_default_index(MemoryVectorIndex.local(path=settings.vector_store_path))
        logger.info(
            "Aegis Memory vector index bound to the embedded vector store at %s.",
            settings.vector_store_path,
        )

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

    warm_task = asyncio.create_task(_warm_ml())
    try:
        yield
    finally:
        warm_task.cancel()
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
    app.include_router(router)
    return app


app = create_app()
