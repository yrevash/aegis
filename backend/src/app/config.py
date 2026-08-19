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
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Fields carrying an explicit ``validation_alias`` (the ``AEGIS_SUPERSET_*``
        # block) must still be constructible by their Python name — a test that builds
        # ``Settings(superset_enabled=True)`` should not have to know the env spelling.
        populate_by_name=True,
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

    # ── Long-term memory RETENTION (phase 7 §7.5) ────────────────────────────
    # The horizon past which stored memory is genuinely deleted. Everything else in
    # the memory subsystem is built to keep — supersession instead of overwrite, a
    # soft prune instead of a delete, an append-only write log — which is right for
    # auditability and leaves the store growing forever. These two numbers are the
    # only thing in the product that shrinks it.
    #
    # **These are the platform FLOOR, not the last word.** ``app.api.routes_memory``
    # resolves them through the settings catalogue first so a tenant can hold data
    # for less time than the platform's default without a deploy; these values are
    # what it falls back to while the catalogue entries (``memory.retention_days`` /
    # ``memory.closed_fact_retention_days``) do not yet exist. Zero disables that
    # half of the sweep entirely — a legal hold is a real, sayable state.
    #
    # Raw conversation turns (and the sessions left empty when they go).
    memory_retention_days: int = Field(default=90)
    # Facts that are ALREADY superseded or invalidated. A valid fact is never swept.
    memory_closed_fact_retention_days: int = Field(default=30)
    # How often the in-process retention task enforces those horizons (seconds). Daily:
    # the horizons are measured in days, so a tighter cadence only re-runs DELETEs that
    # match nothing. Separate from the consolidation sweeper's minute clock on purpose.
    memory_retention_sweep_interval_seconds: float = Field(default=86400.0)

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
    # Where an uploaded document's bytes and its parse artifact live. Not the database:
    # a 126-page PDF is megabytes of ``bytea`` on the hottest tenant-scoped table in the
    # system, in every backup, read past by every query that never wants it. The store is
    # content-addressed and tenant-partitioned (``app.ingestion.store``), so a re-upload
    # of identical bytes writes the same path the ``uq_documents_tenant_sha`` constraint
    # deduplicates the row on. The ``parse`` stage (CPU queue) writes the artifact the
    # ``chunk`` stage (default queue) reads, so a deployment that splits those queues
    # across machines must point this at shared storage.
    document_store_path: str = Field(default="document_storage")
    # ── Table summaries (D8 / task 4.10) ─────────────────────────────────────
    # A table's Markdown grid embeds badly: "| 27.3 | 28.4 |" is arithmetic with no
    # semantic surface, so "which model scored best" cannot find the table that answers
    # it. The ``chunk`` stage spends one cheap-model call per table on a sentence or two
    # describing what it shows, embedded *in front of* the grid — never instead of it,
    # because the numbers are the answer.
    #
    # Two knobs bound the cost, and only one of them is here: the other is the
    # ``table_summaries`` cache, keyed on the table's own content hash, which makes a
    # re-ingest and a 4.13 re-index free. This is the threshold below which a table is
    # not worth a call at all — its Markdown already reads as prose. The default
    # (3 rows x 3 columns and 12 cells) is the smallest genuinely two-dimensional grid:
    # two columns is a key-and-value list and two rows is a label and a value. Every one
    # of the twelve real tables in the phase's two paper fixtures clears it; the smallest
    # is 8x3. Raise it on a corpus of small inline tables, lower it at your own expense.
    table_summary_enabled: bool = Field(default=True)
    table_summary_min_rows: int = Field(default=3)
    table_summary_min_cols: int = Field(default=3)
    table_summary_min_cells: int = Field(default=12)
    # How much of a large grid the prompt carries. The cost of a summary is almost
    # entirely its input tokens, and a 21x13 table on the transformer fixture is ~9,600
    # characters. Rows past this are dropped and the prompt says so, so the model cannot
    # describe a range as the table's when it saw a third of it.
    table_summary_max_grid_chars: int = Field(default=6_000)

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

    # ── Web search (research sub-agent; phase-05 §5.6) ───────────────────────
    # NOTE THE SPELLING. ``backend/.env`` shipped ``TRAVILY_API_KEY`` — a name nothing
    # in this codebase ever read, which is exactly why web search never worked. Only
    # ``TAVILY_API_KEY`` is read, here and in ``aegis.websearch.tavily.API_KEY_ENV``.
    #
    # Empty is a supported posture: ``aegis.websearch.WebSearch`` degrades the research
    # path to the internal corpus, logs at ERROR and emits a ``web_search`` event with
    # ``status=degraded_no_key``. It never returns an empty result set that reads like a
    # clean search — that indistinguishability is the defect the seam exists to remove.
    tavily_api_key: str = Field(default="")
    # How long a cached search stays warm, in seconds, and how many searches the cache
    # may hold. The cache lives in Memurai/Redis; the cap is plan 02's R8 (an unbounded
    # cache of arbitrary web pages is the quickest route to memory pressure on a 16 GB
    # box). A warm cache is also what makes the phase-05 budget arithmetic hold: a
    # rehearsed demo query costs zero provider calls the second time.
    web_search_cache_ttl_seconds: int = Field(default=3600)
    web_search_cache_max_entries: int = Field(default=512)

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

    # ── Reranking (phase 4, D6) ──────────────────────────────────────────────
    # Second-stage reranking runs on a LOCAL ONNX cross-encoder (33M params, ~130 MB,
    # CPU-only — no torch, no GPU). Set ``RERANK_LOCAL=false`` to demote every query to the
    # API reranker; that is the switch for a box where the ONNX weights cannot be cached,
    # and it costs ~12 pp of recall@5, so it is a deliberate operator choice rather than a
    # default. It does NOT switch reranking off — nothing does that silently.
    rerank_local: bool = Field(default=True)

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

    # ── Embedded analytics (Apache Superset) ─────────────────────────────────
    # Superset is OPTIONAL and Aegis degrades: off by default, never touched at boot,
    # and a deployment without it differs only in that one page explains itself. The
    # variables carry an ``AEGIS_`` prefix because Superset reads ``SUPERSET_*`` names
    # of its own (``SUPERSET_CONFIG_PATH``, ``SUPERSET_HOME``, ``SUPERSET_SECRET_KEY``)
    # and the two processes are expected to run on the same host.
    #
    # Turn the feature on. Off means the analytics page says so and names this variable.
    superset_enabled: bool = Field(default=False, validation_alias="AEGIS_SUPERSET_ENABLED")
    # Where Superset is served, e.g. ``http://localhost:8088``.
    superset_base_url: str = Field(default="", validation_alias="AEGIS_SUPERSET_BASE_URL")
    # The Superset service account Aegis signs in as to MINT GUEST TOKENS, and for
    # nothing else. This credential never leaves the backend process: a Superset admin
    # JWT in a tenant's browser is the whole BI instance, every tenant's rows included.
    superset_username: str = Field(default="", validation_alias="AEGIS_SUPERSET_USERNAME")
    superset_password: str = Field(default="", validation_alias="AEGIS_SUPERSET_PASSWORD")
    # Superset's auth provider name — ``db`` for the local metadata-DB accounts that
    # ``superset fab create-admin`` produces.
    superset_provider: str = Field(default="db", validation_alias="AEGIS_SUPERSET_PROVIDER")
    # The tenant column every tenant-scoped Superset dataset carries. Named once, and
    # used by BOTH isolation layers: the guest token's row-level-security clause and the
    # filter Aegis welds into the query context it builds.
    superset_tenant_column: str = Field(
        default="tenant_id", validation_alias="AEGIS_SUPERSET_TENANT_COLUMN"
    )
    # Path to the JSON board catalogue — the finite set of questions Aegis will ask
    # Superset. Unset means no boards, which the page reports as itself.
    superset_boards: str = Field(default="", validation_alias="AEGIS_SUPERSET_BOARDS")
    # Whether ``EMBEDDED_SUPERSET`` is expected to work on this instance. Separate from
    # ``superset_enabled`` on purpose: the 6.1.0 wheel has already shipped three broken
    # paths, and the embed being one of them must cost the iframe and not the charts.
    superset_embed_enabled: bool = Field(
        default=False, validation_alias="AEGIS_SUPERSET_EMBED_ENABLED"
    )
    # Seconds a minted guest token is asked to live. Short: it is the only Superset
    # credential that ever reaches a browser.
    superset_guest_token_ttl_seconds: int = Field(
        default=300, validation_alias="AEGIS_SUPERSET_GUEST_TOKEN_TTL_SECONDS"
    )
    # Whether to verify Superset's TLS certificate. Only meaningful for an https base
    # URL; the localhost deployment this was written against is plain http.
    superset_ssl_verify: bool = Field(
        default=True, validation_alias="AEGIS_SUPERSET_SSL_VERIFY"
    )

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
