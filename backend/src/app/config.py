"""Application settings, loaded from the environment (`.env` in dev).

All configuration is centralised here so nothing in the core reads `os.environ`
directly. Values are typed and validated by pydantic-settings at startup.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The non-secret dev fallback JWT signing key. It is deliberately long enough to
# clear PyJWT's minimum-key-length warning on the offline/test path, but it must
# NEVER sign tokens in a real deployment — the startup guard rejects it outside dev.
DEFAULT_JWT_SECRET = "dev-insecure-jwt-secret-change-me-0123456789abcdef"
# Minimum acceptable length for a production HS256 signing secret (bytes/chars).
MIN_JWT_SECRET_LEN = 32


class InsecureConfigurationError(RuntimeError):
    """Raised at startup when a non-dev deployment carries an insecure secret."""


class Settings(BaseSettings):
    """Typed application configuration."""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ── Model gateway ────────────────────────────────────────────────────────
    genailab_base_url: str = Field(default="https://genailab.tcs.in")
    genailab_api_key: str = Field(default="")
    # The gateway presents a self-signed certificate; verification is disabled
    # for it specifically (documented as a known, scoped exception).
    genailab_ssl_verify: bool = Field(default=False)
    # ── Per-call safety caps (production call-safety; cost + latency control) ──
    # Default per-generation output ceiling applied to every ``complete`` call
    # that does not pass an explicit ``max_tokens`` — so no single generation can
    # run away on cost or latency. A per-call argument overrides this.
    llm_max_output_tokens: int = Field(default=1024)
    # Per-attempt wall-clock timeout (seconds) for each model call. Forwarded to
    # LiteLLM (so it bounds each attempt AND participates in the fallback chain)
    # and enforced as a hard outer backstop via ``asyncio.wait_for`` so a hung
    # upstream can never block a run indefinitely.
    llm_timeout_seconds: float = Field(default=60.0)

    # ── Stores ───────────────────────────────────────────────────────────────
    # The **serving** DSN: the connection every request runs on. It must name a role
    # with neither SUPERUSER nor BYPASSRLS (``aegis_app``, provisioned by
    # ``scripts/db-roles.sh`` / ``scripts/db-roles.ps1``), because Postgres skips row
    # security entirely for a role that has either — which would make all thirteen
    # tenant_isolation policies inert. The default still points at ``postgres`` so a
    # bare checkout runs; ``verify_rls_enforcement`` logs an ERROR naming that exact
    # consequence when it does. See docs/operations/runbook.md § Database roles.
    postgres_dsn: str = Field(default="postgresql://postgres:postgres@localhost:5432/taif")
    # The **owner/DDL** DSN: the connection that runs ``create_all``, the additive
    # schema reconciler and the RLS bootstrap. Those legitimately bypass RLS (they own
    # the tables), and keeping them on a second connection is what makes bypass a
    # property of the *connection* rather than something request-handling code could
    # forget to avoid. Empty (the default) means "no split configured": DDL falls back
    # to ``postgres_dsn`` and the platform runs exactly as it did before — loudly, not
    # silently, because that is also the configuration in which RLS does nothing.
    postgres_admin_dsn: str = Field(default="")
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="")
    redis_url: str = Field(default="redis://localhost:6379/0")
    # The vector store is EMBEDDED and file-backed — the ANN engine behind retrieval +
    # memory recall runs in this process, so there is no server binary to install and no
    # port to open. That is what makes Aegis installable on a locked-down enterprise
    # machine. (pgvector was removed earlier; the SQL ``embedding`` columns are now only
    # the JSON source-of-record.) In full stores mode this directory must be usable —
    # boot fails loud if it is not, exactly like Postgres/Redis — and tests use an
    # explicit in-memory engine, never a silent RAM fallback.
    vector_store_path: str = Field(default="vector_storage")

    # ── Agent checkpointer (durable-execution seam; §1.3) ────────────────────
    # "memory" (default, used by tests) compiles the graph with LangGraph's
    # ``InMemorySaver``; "postgres" selects a durable ``PostgresSaver`` so a paused
    # run survives a restart / another worker. The Postgres saver is imported and
    # connected lazily, so the default path never requires Postgres or the
    # ``langgraph-checkpoint-postgres`` package.
    agent_checkpointer: str = Field(default="memory")

    # ── Durable approvals inbox (§1.3) ───────────────────────────────────────
    # A gated run parks as a PENDING row carrying an SLA deadline. The async
    # sweeper (an ``asyncio`` task in this process — no cron, no Docker) marks
    # past-deadline rows EXPIRED and auto-REJECTS HIGH-risk ones (decision D5).
    # Seconds a queued approval may wait before the SLA sweeper acts on it.
    approval_sla_seconds: int = Field(default=3600)
    # The approver tier a fresh gate is assigned to (escalation rewrites it).
    approval_default_tier: str = Field(default="tier-1")
    # How often the SLA sweeper scans the inbox (seconds).
    approval_sweeper_interval_seconds: float = Field(default=30.0)

    # ── Long-term memory consolidation sweeper (docs/architecture/memory-spec.md §D) ──────
    # Fire-and-forget consolidation (``asyncio.create_task``) loses work on a
    # restart/error; the durable ``memory_consolidation_job`` queue is the backstop.
    # This in-process asyncio task (like the SLA sweeper — no cron, no Docker) drains
    # PENDING jobs periodically. Only runs with the real stores; lite/off and tests skip
    # it. How often the memory consolidation sweeper drains the queue (seconds).
    memory_sweeper_interval_seconds: float = Field(default=60.0)
    # Max PENDING consolidation jobs drained per sweep pass.
    memory_sweeper_batch: int = Field(default=10)

    # ── Durable job substrate (Temporal; docs/dev_new_docs_v2/phase-03) ──────
    # Temporal orchestrates execution (retries, timers, resumability, cancellation);
    # our own ``job_runs``/``documents`` tables stay the system of record. The address
    # is the dev server's default; a deployment points it at its own cluster.
    temporal_address: str = Field(default="localhost:7233")
    temporal_namespace: str = Field(default="default")
    # Which task queues this process's worker polls. Comma-separated, and validated
    # against ``aegis.jobs.TASK_QUEUES`` by :func:`app.jobs.worker.configured_queues` —
    # a typo here would otherwise start a worker that polls a queue nothing schedules
    # onto while the real queue goes unserved, and no error would be raised anywhere.
    # Empty (the default) means "every declared queue", which is the single-box demo
    # posture; a scale-out deployment runs one process per queue.
    temporal_task_queues: str = Field(default="")
    # Run the worker in-process, as an asyncio task in the API's lifespan. That is what
    # runs on demo day (one process, no extra service to babysit); the identical code
    # path is also launchable standalone as ``python -m app.jobs.worker``. Gated on the
    # real stores, because an activity with no database has nothing to write.
    temporal_worker_inprocess: bool = Field(default=True)
    # ── Reconciliation (§3.3) ────────────────────────────────────────────────
    # How often the reconciler's Temporal Schedule sweeps for open job rows whose
    # workflow no longer exists. A Schedule and not a ``next_run_at`` column: see
    # :mod:`app.jobs.schedules`.
    temporal_reconcile_interval_seconds: int = Field(default=300)
    # How long a row may sit RUNNING before the sweep questions it. Comfortably longer
    # than the slowest stage's own timeout (``parse``: 1800s), because a sweep that
    # questioned a job still inside its legitimate attempt window would fight the
    # pipeline it exists to protect.
    temporal_reconcile_stale_after_seconds: int = Field(default=3600)
    # Most rows examined per sweep. Bounded because each row costs one RPC to the
    # orchestrator, and an unbounded sweep after an outage would be a thundering herd
    # against the server that is already struggling.
    temporal_reconcile_batch: int = Field(default=50)
    # ── Re-index debounce and cadence (§3.5) ─────────────────────────────────
    # How long a tenant's re-index window stays open for more requests. Every request
    # resets it, which is what folds a burst of uploads into one re-index.
    temporal_reindex_debounce_seconds: int = Field(default=30)
    # How far that window may be pushed out in total. Without a ceiling, a tenant
    # uploading steadily would reset the timer forever and never be re-indexed at all.
    temporal_reindex_max_wait_seconds: int = Field(default=600)
    # The re-index cadence, per tenant, when a schedule is created for one. Daily by
    # default: the debounce covers freshness after real changes, so this is the floor
    # that catches drift nothing signalled.
    temporal_reindex_interval_seconds: int = Field(default=86_400)

    # ── Ingestion (Docling; docs/dev_new_docs_v2/phase-04) ───────────────────
    # Load the layout and table models when the worker starts rather than when the first
    # document arrives (D4). OFF by default and turned on by the deployment that actually
    # parses: the models hold a little under a gigabyte resident, and an API-only process
    # or a test worker that will never run the parse stage must not carry that. Measured
    # on an M3 with the model cache primed the saving is ~3 s, not the 50-120 s the phase
    # doc assumed - the large cost is the 730 MB first download, which
    # ``spikes/docling_spike.py --prefetch`` exists to pay in advance.
    docling_warm_on_start: bool = Field(default=False)

    # ── Guardrails engine (one policy, two front doors; docs/security/overview.md §3) ──
    # Two front doors enforce the same policy: the fast, offline-testable
    # programmatic rails (``app.guardrails.check_input``/``check_output``,
    # delegating to ``aegis.guardrails``) the agent graph calls directly, and the
    # declarative **NeMo Guardrails Colang** policy (``app.guardrails.nemo``,
    # delegating to ``aegis.guardrails.nemo``) callable directly for the jury's
    # readable security artifact. This field is currently unused for automatic
    # dispatch (the strangler shim onto ``aegis.guardrails`` — see
    # docs/module/MODULE_REFERENCE.md — always uses
    # the programmatic rails); kept for forward compatibility / config parity.
    guardrails_engine: str = Field(default="programmatic")

    # ── Output grounding rail (OWASP LLM09; docs/security/overview.md §3) ──
    # The output rail self-checks the answer against the retrieved contexts it was
    # generated from (NeMo self-check-facts / RAGAS groundedness). Advisory by
    # default: an ungrounded answer is surfaced as a non-blocking FLAG in the trace,
    # never withheld. Flip ``grounding_block`` to hard-block ungrounded answers.
    grounding_block: bool = Field(default=False)

    # ── Observability ────────────────────────────────────────────────────────
    phoenix_enabled: bool = Field(default=True)

    # ── Retrieval intelligence (docs/architecture/eval-strategy.md) ───────────────────────
    # A cheap-model step rewrites the turn into a standalone, context-resolved
    # search query before retrieval (app/retrieval/query_rewrite.py).
    query_rewrite_enabled: bool = Field(default=True)
    # A bounded Self-RAG/FLARE-style loop: retrieve → judge sufficiency →
    # reformulate → re-retrieve, up to N rounds (app/agent/retrieval_loop.py).
    agentic_retrieval_enabled: bool = Field(default=True)
    agentic_retrieval_max_rounds: int = Field(default=2)
    # Answer-level semantic cache: a semantically-equivalent question reuses the
    # cached final answer, skipping the generation call (app/retrieval/answer_cache.py).
    # Scoped per tenant+persona+role so answers never cross principals.
    answer_cache_enabled: bool = Field(default=True)
    answer_cache_threshold: float = Field(default=0.97)
    answer_cache_ttl_seconds: int = Field(default=1800)

    # ── Run mode (see docs/operations/runbook.md) ───────────────────────────────────────
    # "on" (default) uses the real stores — LightRAG over Neo4j + NanoVectorDB with a
    # Redis semantic cache. "off" runs a self-contained in-memory backend + cache
    # (no databases) — the "lite" demo mode that needs only the model gateway.
    stores: str = Field(default="on")
    # Create database tables on startup (best-effort; failures are logged, not
    # fatal). Enable for the run scripts; off by default so tests and
    # migration-managed deployments are unaffected.
    db_bootstrap: bool = Field(default=False)

    # ── Auth / JWT (multi-tenant RBAC; §3.3) ─────────────────────────────────
    # HS256 signing secret for the access-token JWT. NEVER ship the dev default:
    # set ``JWT_SECRET`` in the environment for any real deployment. The dev
    # fallback is long enough to clear PyJWT's minimum-key-length warning so the
    # test/offline path stays quiet, but it is explicitly non-secret.
    jwt_secret: str = Field(default=DEFAULT_JWT_SECRET)
    jwt_algorithm: str = Field(default="HS256")
    # Access-token lifetime in minutes.
    jwt_expire_minutes: int = Field(default=720)

    # ── Governance enforcement posture (§3.3) ────────────────────────────────
    # Budget/rate caps fail CLOSED by default: if the enforcement read errors
    # (e.g. a database blip) the call is DENIED rather than silently uncapped, so a
    # transient failure can never disable every spend cap. Set to ``True`` to opt
    # into soft/fail-open enforcement (caps become best-effort ceilings).
    budget_fail_open: bool = Field(default=False)

    # ── App ──────────────────────────────────────────────────────────────────
    app_env: str = Field(default="dev")
    log_level: str = Field(default="INFO")

    @property
    def stores_enabled(self) -> bool:
        """Whether the real databases (Neo4j/Redis/Postgres) are in use."""
        return self.stores.strip().lower() not in {"off", "false", "0", "none"}

    @property
    def is_dev(self) -> bool:
        """Whether the app is running in the developer/offline environment."""
        return self.app_env.strip().lower() == "dev"

    @property
    def admin_dsn(self) -> str:
        """The DSN for DDL — the owner connection, falling back to the serving one.

        The fallback is what keeps a single-DSN developer install working: with no
        ``POSTGRES_ADMIN_DSN`` set, ``create_all``/RLS bootstrap run on the same
        connection that serves requests, exactly as they did before the split. It is
        not a silent fallback — :func:`app.data.session.serving_role_name` returns
        ``None`` in that case (there is no distinct serving role), and
        :func:`app.data.session.verify_rls_enforcement` says so at WARNING and reports
        at ERROR that row security is inert whenever the serving role can bypass it.

        A **non-Postgres** serving DSN overrides the admin one entirely. Lite mode and
        the test suite repoint only ``POSTGRES_DSN`` (at SQLite); honouring a leftover
        ``POSTGRES_ADMIN_DSN`` there would create the tables in one database while
        serving reads from another — a split brain far worse than the unsplit posture,
        and invisible until a query returned nothing. There are no roles on SQLite, so
        there is nothing the split could buy in exchange.
        """
        if not self.postgres_dsn.startswith(("postgresql:", "postgresql+", "postgres:")):
            return self.postgres_dsn
        return self.postgres_admin_dsn.strip() or self.postgres_dsn

    def ensure_secure_secrets(self) -> None:
        """Fail-fast on an insecure JWT signing secret outside dev (§3.3, H4).

        Dev keeps the non-secret fallback so the offline/test path stays quiet. In
        any other environment a default or too-short ``jwt_secret`` is a hard error
        at startup — a real deployment MUST set ``JWT_SECRET`` to a strong value.

        Raises:
            InsecureConfigurationError: When ``app_env`` is not ``dev`` and the
                signing secret is the built-in default or shorter than
                :data:`MIN_JWT_SECRET_LEN`.
        """
        if self.is_dev:
            return
        if self.jwt_secret == DEFAULT_JWT_SECRET:
            raise InsecureConfigurationError(
                "JWT_SECRET is the built-in dev default; set a strong secret "
                f"(>= {MIN_JWT_SECRET_LEN} chars) for a non-dev deployment."
            )
        if len(self.jwt_secret) < MIN_JWT_SECRET_LEN:
            raise InsecureConfigurationError(
                f"JWT_SECRET is too short ({len(self.jwt_secret)} chars); use at "
                f"least {MIN_JWT_SECRET_LEN} for a non-dev deployment."
            )


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
