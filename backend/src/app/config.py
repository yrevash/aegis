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
    postgres_dsn: str = Field(default="postgresql://postgres:postgres@localhost:5432/taif")
    neo4j_uri: str = Field(default="bolt://localhost:7687")
    neo4j_user: str = Field(default="neo4j")
    neo4j_password: str = Field(default="")
    redis_url: str = Field(default="redis://localhost:6379/0")
    # Qdrant is the vector DB — the ANN engine behind retrieval + memory recall (pgvector
    # was removed; the SQL ``embedding`` columns are now only the JSON source-of-record).
    # In full stores mode a reachable Qdrant node is REQUIRED (fail loud at boot, exactly
    # like Postgres/Redis); dev/tests use the explicit embedded engine, never a RAM fallback.
    qdrant_url: str = Field(default="http://localhost:6333")
    qdrant_api_key: str = Field(default="")

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

    # ── Long-term memory consolidation sweeper (docs/MEMORY_SPEC.md §D) ──────
    # Fire-and-forget consolidation (``asyncio.create_task``) loses work on a
    # restart/error; the durable ``memory_consolidation_job`` queue is the backstop.
    # This in-process asyncio task (like the SLA sweeper — no cron, no Docker) drains
    # PENDING jobs periodically. Only runs with the real stores; lite/off and tests skip
    # it. How often the memory consolidation sweeper drains the queue (seconds).
    memory_sweeper_interval_seconds: float = Field(default=60.0)
    # Max PENDING consolidation jobs drained per sweep pass.
    memory_sweeper_batch: int = Field(default=10)

    # ── Guardrails engine (one policy, two front doors; docs/security.md §3) ──
    # Two front doors enforce the same policy: the fast, offline-testable
    # programmatic rails (``app.guardrails.check_input``/``check_output``,
    # delegating to ``aegis.guardrails``) the agent graph calls directly, and the
    # declarative **NeMo Guardrails Colang** policy (``app.guardrails.nemo``,
    # delegating to ``aegis.guardrails.nemo``) callable directly for the jury's
    # readable security artifact. This field is currently unused for automatic
    # dispatch (the strangler shim onto ``aegis.guardrails`` — see
    # docs/superpowers/sdd/2026-08-11-aegis-core-guardrails-pilot/ — always uses
    # the programmatic rails); kept for forward compatibility / config parity.
    guardrails_engine: str = Field(default="programmatic")

    # ── Output grounding rail (OWASP LLM09; docs/security.md §3) ──
    # The output rail self-checks the answer against the retrieved contexts it was
    # generated from (NeMo self-check-facts / RAGAS groundedness). Advisory by
    # default: an ungrounded answer is surfaced as a non-blocking FLAG in the trace,
    # never withheld. Flip ``grounding_block`` to hard-block ungrounded answers.
    grounding_block: bool = Field(default=False)

    # ── Observability ────────────────────────────────────────────────────────
    phoenix_enabled: bool = Field(default=True)

    # ── Retrieval intelligence (docs/EVAL_STRATEGY.md) ───────────────────────
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

    # ── Run mode (see docs/RUNBOOK.md) ───────────────────────────────────────
    # "on" (default) uses the real stores — LightRAG over Neo4j + Qdrant with a
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
        """Whether the real databases (Neo4j/Qdrant/Redis) are in use."""
        return self.stores.strip().lower() not in {"off", "false", "0", "none"}

    @property
    def is_dev(self) -> bool:
        """Whether the app is running in the developer/offline environment."""
        return self.app_env.strip().lower() == "dev"

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
