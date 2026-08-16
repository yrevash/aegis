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
from app.config import get_settings
from app.observability import init_observability

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
        if sweeper_task is not None or memory_sweeper_task is not None:
            sweeper_stop.set()
        if sweeper_task is not None:
            sweeper_task.cancel()
        if memory_sweeper_task is not None:
            memory_sweeper_task.cancel()


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
