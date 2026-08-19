"""Shared fixtures for the wiring tests — faked services over a **real** PostgreSQL.

Everything the vertical slice touches that is *not* the database (the LLM gateway,
retrieval, guardrails, the ML spine, the action tools) is faked here and injected
through :class:`AgentDeps`, so the agent graph and the API run with no network and no
keys. The database is the one dependency that is never faked.

**Why the database is real, and why it is not SQLite.** Production carries guards
shaped like ``if bind.dialect.name != "postgresql": return``. On SQLite
:func:`aegis.governance.rls.set_tenant_scope` therefore did nothing at all, so every
test that read like a proof of tenant isolation only ever exercised the app-level
``WHERE tenant_id = :ctx`` filter — which is exactly how ten tenant-scoped tables ended
up with no Row-Level Security policy while this suite stayed green. SQLite was removed
from the backend tests on 2026-08-16. The :func:`db` fixture binds a scratch PostgreSQL
database served by a ``NOSUPERUSER NOBYPASSRLS`` role, so RLS is genuinely enforced
against the connection the tests run on and a policy regression fails a test.

The scratch database and its role are provisioned once per session (see
:func:`postgres_database`) and destroyed in a ``finally``; each test starts from an
empty schema via a single ``TRUNCATE ... RESTART IDENTITY CASCADE``. See
``tests/pgsupport.py`` for the provisioning primitives, which
``tests/integration/test_tenant_isolation_live.py`` shares.
"""

from __future__ import annotations

# Before any import: this venv holds two OpenMP runtimes (torch's, via Docling and via
# presidio-analyzer's device detector, and xgboost's/scikit-learn's), and one process
# holding both segfaults or deadlocks depending on load order — measured 2026-08-18, and
# it took this suite down at ~24%. ``OMP_NUM_THREADS=1`` is the only value that fixes it.
# It must be set before the first OpenMP library loads, which is why it is here rather
# than in a fixture. See ``backend/src/app/__init__.py`` for the full note.
import os  # noqa: E402

os.environ.setdefault("OMP_NUM_THREADS", "1")


import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from aegis.gateway import reset_usage_tally
from aegis.retrieval.types import RetrievalScope

# Ensure the ``src`` layout is importable even when the editable install's .pth
# is not honoured by the active interpreter (keeps the suite runnable anywhere).
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ``tests/`` itself, so ``import pgsupport`` resolves from every test package. pytest's
# ``prepend`` import mode already puts this directory on ``sys.path`` (there is no
# ``tests/__init__.py``), but relying on that is relying on a collection detail — and a
# suite that cannot import its database scaffolding does not skip, it errors.
_TESTS = str(Path(__file__).resolve().parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)

# Same guard for the sibling ``aegis`` package: its editable install's .pth is
# subject to the identical interpreter quirk (observed on macOS, where the
# generated .pth can end up carrying the OS-level "hidden" file flag, which the
# hardened stdlib ``site.py`` silently skips). Falling back to a direct sys.path
# entry keeps ``import aegis`` working regardless of whether that .pth was honoured.
_AEGIS_SRC = Path(__file__).resolve().parents[2] / "aegis" / "src"
if _AEGIS_SRC.is_dir() and str(_AEGIS_SRC) not in sys.path:
    sys.path.insert(0, str(_AEGIS_SRC))

from typing import TYPE_CHECKING  # noqa: E402

import pgsupport  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.exc import SQLAlchemyError  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.api.schemas import (  # noqa: E402
    GraphEdge,
    GraphNode,
    GuardVerdict,
    MLExplainResponse,
    RiskLevel,
    ShapFeature,
)
from app.core.llm import LLMResult, ToolCallResult, Usage  # noqa: E402
from app.data import bootstrap, configure_engine, get_sessionmaker  # noqa: E402
from app.guardrails.models import GuardResult  # noqa: E402
from app.retrieval.models import GraphDelta, RetrievalResult, Source  # noqa: E402

if TYPE_CHECKING:
    from app.agent import AgentDeps


class _Outcome:
    """Minimal stand-in for an adapter ``ToolActionResult``."""

    def __init__(self, ok: bool = True, summary: str = "Status open -> resolved") -> None:
        self.ok = ok
        self.summary = summary


def build_fake_deps(
    *,
    propose_tool: bool = True,
    block_input: bool = False,
    high_risk: bool = False,
) -> AgentDeps:
    """Build an :class:`AgentDeps` wired entirely to canned fakes.

    Args:
        propose_tool: Whether the planner proposes an action tool call.
        block_input: Whether the input rail blocks the query.
        high_risk: Whether the tool is reported as HIGH risk (forces the gate).
    """
    from app.agent import AgentConfig, AgentDeps  # local: keeps langgraph lazy

    async def check_input(text: str) -> GuardResult:
        if block_input:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason="blocked by policy", text=text
            )
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def check_output(
        text: str, contexts: list[str] | None = None
    ) -> GuardResult:  # noqa: ARG001 - contexts accepted for the grounding-aware signature
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:
        return RetrievalResult(
            answer_context="Spotlighted context about request R1.",
            sources=[Source(id="kb-1", text="Escalation policy", score=0.9)],
            # Wide recall pulled 5 candidates; rerank kept 1 survivor above.
            num_candidates=5,
            graph_delta=GraphDelta(
                nodes=[GraphNode(id="R1", label="Request R1", kind="request")],
                edges=[GraphEdge(source="R1", target="C1", relation="raised_by")],
            ),
            cache_hit=False,
        )

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        # Faithful doubles for the two retrieval-intelligence prompts, so the REAL
        # rewrite + sufficiency code paths execute in every graph test but resolve to a
        # stable, deterministic single-round / unchanged-query outcome (prod == test).
        system = messages[0]["content"] if messages else ""
        if "standalone search query" in system or "rewrite a user's latest turn" in system:
            # Rewrite prompt → echo the user's latest turn unchanged (changed=False no-op).
            # The prompt wraps the query in a template, so recover the raw "LATEST TURN".
            user = messages[-1]["content"] if messages else ""
            match = re.search(r"LATEST TURN: (.*?)\n\n", user, re.DOTALL)
            user_query = match.group(1) if match else user
            return LLMResult(
                content=json.dumps(
                    {"rewritten": user_query, "reason": "no rewrite needed"}
                ),
                tool_calls=[],
                usage=Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.0001),
                model="fake-cheap",
            )
        if "retrieval sufficiency judge" in system:
            # Sufficiency prompt → sufficient on the first look (loop runs one round).
            return LLMResult(
                content=json.dumps(
                    {
                        "sufficient": True,
                        "reason": "context sufficient",
                        "followup_query": None,
                    }
                ),
                tool_calls=[],
                usage=Usage(prompt_tokens=3, completion_tokens=2, cost_usd=0.0001),
                model="fake-cheap",
            )
        if tools and propose_tool:
            return LLMResult(
                content=(
                    "The request is overdue and matches the escalation policy. "
                    "I will update its status to resolved."
                ),
                tool_calls=[
                    ToolCallResult(
                        id="call-1",
                        name="update_request_status",
                        args={"request_id": "R1", "status": "resolved"},
                    )
                ],
                usage=Usage(prompt_tokens=12, completion_tokens=4, cost_usd=0.0009),
                model="fake-generation",
            )
        return LLMResult(
            content="Request R1 has been resolved and the customer notified.",
            tool_calls=[],
            usage=Usage(prompt_tokens=9, completion_tokens=7, cost_usd=0.0006),
            model="fake-generation",
        )

    def tool_definitions_for(persona: str) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "update_request_status",
                    "description": "Change a request's status.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    def tool_risk(name: str) -> RiskLevel:
        return RiskLevel.HIGH if high_risk else RiskLevel.MEDIUM

    async def run_tool(persona, name, args, *, actor, model, trace_id, approver):  # noqa: ANN001
        return _Outcome()

    def render_system_prompt(persona: str, extra_context: str | None = None) -> str:
        base = "You are a helpful, grounded support assistant."
        return f"{base}\n\n{extra_context}" if extra_context else base

    return AgentDeps(
        complete=complete,
        retrieve=retrieve,
        check_input=check_input,
        check_output=check_output,
        tool_definitions_for=tool_definitions_for,
        run_tool=run_tool,
        tool_risk=tool_risk,
        render_system_prompt=render_system_prompt,
        config=AgentConfig(stream_chunk_words=4),
    )


# ── The vector-store declaration this test process makes out loud (§8.4) ──────
#
# ``aegis.memory``'s index and ``aegis.retrieval``'s backends no longer invent an
# ephemeral in-process engine for a caller that configured none. The ASGI transport these
# tests use does not run the app lifespan, so the declaration ``app.main`` makes at
# startup never happens here — the test process makes its own, once, and gets exactly
# the engine it wants instead of the one a leaf used to guess.
@pytest.fixture(scope="session", autouse=True)
def _ephemeral_vector_stores():
    """Declare the ephemeral in-process vector engine for the whole test session."""
    from aegis.memory import MemoryVectorIndex, set_default_index
    from aegis.retrieval import QdrantVectorStore, configure_vector_store

    configure_vector_store(QdrantVectorStore.local)
    set_default_index(MemoryVectorIndex.local())


@pytest.fixture
def make_deps():
    """Return the :func:`build_fake_deps` factory."""
    return build_fake_deps


@pytest.fixture
def fake_predict():
    """Return a canned ML predict-and-explain callable for ``/ml/explain``."""

    def _predict(features: dict) -> MLExplainResponse:
        return MLExplainResponse(
            prediction=7.5,
            conformal_interval=(6.0, 9.0),
            conformal_confidence=0.9,
            shap_attribution=[ShapFeature(feature="priority", value=2.0, contribution=0.3)],
        )

    return _predict


@dataclass(frozen=True, slots=True)
class PostgresDatabase:
    """The session-wide scratch database every ``db`` fixture binds to.

    Attributes:
        scratch: The provisioned database + unprivileged role.
        truncate_sql: A single ``TRUNCATE ... RESTART IDENTITY CASCADE`` covering every
            table the bootstrap created, used to reset between tests.
    """

    scratch: pgsupport.Scratch
    truncate_sql: str


async def _build_schema(scratch: pgsupport.Scratch) -> str:
    """Materialise the application schema on the scratch database, once.

    This calls the shipped :func:`app.data.session.bootstrap` rather than a test-local
    ``create_all``, deliberately: ``bootstrap`` is what a real deployment runs, so the
    suite exercises the same ``create_all`` → additive reconcile → ``timestamptz``
    alignment → :func:`aegis.governance.rls.grant_serving_role` →
    :func:`aegis.governance.rls.bootstrap_rls` sequence. A regression in any of those
    steps breaks the suite at setup instead of hiding behind a hand-rolled schema.

    The serving engine is then interrogated (:func:`pgsupport.assert_unprivileged`)
    before a single test runs, because a superuser serving role would make every
    tenant-isolation assertion in the suite vacuous.

    Args:
        scratch: The provisioned scratch handle. ``app.config`` must already point at
            it, since ``bootstrap`` reads the serving role's name from settings.

    Returns:
        The ``TRUNCATE`` statement that empties every table the bootstrap created.

    Raises:
        RuntimeError: If the serving role turns out to be exempt from row security.
    """
    owner = create_async_engine(scratch.owner_dsn)
    serving = create_async_engine(scratch.app_dsn)
    try:
        await bootstrap(owner)
        await pgsupport.assert_unprivileged(serving, expected_role=scratch.role)
        async with owner.connect() as conn:
            tables = (
                await conn.execute(
                    text(
                        "SELECT c.relname FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = current_schema() AND c.relkind = 'r' "
                        "ORDER BY c.relname"
                    )
                )
            ).scalars().all()
    finally:
        await serving.dispose()
        await owner.dispose()
    if not tables:
        raise RuntimeError(
            f"bootstrap created no tables in {scratch.database}; every test that "
            "believes it wrote a row would silently prove nothing"
        )
    names = ", ".join(f'"{name}"' for name in tables)
    return f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE"


@pytest.fixture(scope="session")
def postgres_database():
    """Provision one scratch database + unprivileged role for the whole session.

    Session-scoped because provisioning a database per test would dominate the runtime
    of a ~700-test suite; per-test isolation comes from the ``TRUNCATE`` in :func:`db`
    instead, which costs milliseconds. Synchronous (driving ``asyncio.run`` itself) so
    each async test still builds its engines inside its *own* event loop — there is no
    session-scoped loop to keep in step, and no pooled connection can be handed across
    loops.

    ``app.config``'s DSNs are repointed at the scratch database for the session and
    restored in the ``finally``, so ``bootstrap`` grants to the right serving role and
    any code under test that reads settings sees the same database the fixtures do.

    Yields:
        The :class:`PostgresDatabase` handle. Skips (or, under
        ``AEGIS_REQUIRE_PG_TESTS=1``, fails) the dependent tests when no cluster can be
        provisioned.
    """
    from app.config import get_settings

    dsn = pgsupport.admin_dsn()
    try:
        scratch = asyncio.run(pgsupport.create_scratch(dsn, prefix="aegis_backend"))
    except (OSError, SQLAlchemyError) as exc:
        pgsupport.skip_or_fail(
            unverified=(
                "the entire database-backed backend suite — tenant scoping, RLS "
                "enforcement, governed persistence and every API surface that reads or "
                "writes a row."
            ),
            reason=f"{type(exc).__name__}: {exc}",
        )

    settings = get_settings()
    restore = (settings.postgres_dsn, settings.postgres_admin_dsn)
    settings.postgres_dsn = scratch.app_dsn
    settings.postgres_admin_dsn = scratch.owner_dsn
    try:
        truncate_sql = asyncio.run(_build_schema(scratch))
        yield PostgresDatabase(scratch=scratch, truncate_sql=truncate_sql)
    finally:
        settings.postgres_dsn, settings.postgres_admin_dsn = restore
        asyncio.run(pgsupport.drop_scratch(dsn, scratch))


@pytest_asyncio.fixture
async def db(postgres_database):
    """Bind the process-wide session factory to the scratch PostgreSQL database.

    The serving engine connects as the ``NOSUPERUSER NOBYPASSRLS`` scratch role, so the
    ``tenant_isolation`` policies actually apply to everything the tests do; the owner
    engine is bound separately as the DDL engine, mirroring the production split in
    :mod:`app.data.session`. That split is the reason ``set_tenant_scope`` is a real
    filter here and not the no-op it was on SQLite.

    Isolation between tests is one ``TRUNCATE ... RESTART IDENTITY CASCADE`` rather than
    a fresh schema (which, per test, would cost more than the rest of the suite put
    together). It runs on the **owner** connection because truncation is an owner
    privilege the serving role deliberately does not have.

    The engines are built per test, inside the test's own event loop, and disposed in a
    ``finally``: an ``asyncpg`` connection is bound to the loop that opened it, so a
    session-scoped pool would hand a dead connection to the next test.

    Yields:
        The process-wide ``async_sessionmaker``, ready to use.
    """
    from app.ops import registry

    owner = create_async_engine(postgres_database.scratch.owner_dsn)
    serving = create_async_engine(postgres_database.scratch.app_dsn)
    try:
        async with owner.begin() as conn:
            await conn.execute(text(postgres_database.truncate_sql))
        configure_engine(serving, admin_engine=owner)
        # The active-prompt cache mirrors a ``prompt_versions`` row; the TRUNCATE above
        # just deleted every one of them, so a cache left populated is a lie about the
        # database this test is looking at.
        registry.clear_cache()
        yield get_sessionmaker()
    finally:
        registry.clear_cache()
        await serving.dispose()
        await owner.dispose()


@pytest_asyncio.fixture
async def client():
    """Yield an httpx client bound to the ASGI app with isolated dashboards.

    Fresh graph/metrics stores are injected per test so accumulated state never
    leaks between tests. Dependency overrides are cleared on teardown.

    The latency window is the one telemetry store that cannot be injected — it is a
    process-global rolling buffer in ``aegis.observability`` — so it is cleared here
    too. Without this, a test that asserts the honest empty state (``p95_latency_ms``
    is ``None``) passes alone and fails in a full run, because an earlier test's run
    left samples behind: an order-dependent false failure, not an endpoint defect.
    """
    import httpx
    from aegis.observability import reset_latency_window

    from app.api import routes as api_routes
    from app.main import app

    reset_latency_window()
    graph_store = api_routes.GraphStore()
    metrics_store = api_routes.MetricsStore()
    app.dependency_overrides[api_routes.get_graph_store] = lambda: graph_store
    app.dependency_overrides[api_routes.get_metrics_store] = lambda: metrics_store

    # Pinned to the **versioned API root** (§8.6), so a test writes ``/query`` and
    # reaches ``/v1/query``: httpx joins a relative path onto the base URL's own path.
    # The alternative — rewriting some 900 call sites — would have spelled the version
    # segment 900 times and made moving it a 900-line change. The three unversioned
    # infrastructure probes are dialled absolutely (``http://test/health``) by the
    # handful of tests that assert on them, which is the point: reaching one from a
    # test now says out loud that it is not part of the versioned surface.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/v1") as c:
        yield c
    app.dependency_overrides.clear()


async def login_as(client, username: str) -> dict[str, str]:
    """Provision one seeded platform account, log in as it, return an auth header.

    The hardcoded ``_DEMO_USERS`` login table was deleted in §3.8, so an account only
    exists if a row exists. Every fixture and test that authenticates therefore writes
    the row first — through :mod:`app.seed`, the same code ``python -m app.seed`` runs,
    rather than a test-local imitation of it that could drift from what an operator
    actually gets.

    One principal is provisioned rather than all five because an Argon2 hash costs ~30ms
    and the whole suite pays it: the tests that need the other roles ask for the
    :func:`platform_principals` fixture.

    Args:
        client: The httpx client bound to the ASGI app.
        username: A username from :data:`app.seed.PLATFORM_PRINCIPALS`.

    Returns:
        The ``Authorization`` header for the freshly minted token.
    """
    from app.seed import SeedSummary, ensure_principal, platform_principal, seed_password

    await ensure_principal(platform_principal(username), SeedSummary())
    resp = await client.post(
        "/auth/login", json={"username": username, "password": seed_password()}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest_asyncio.fixture
async def platform_principals(db):
    """Seed all five un-tenanted platform accounts (``app.seed``'s staff half).

    For the tests that log in as more than one role, or as a role no ``*_headers``
    fixture covers. Returns the password they were created with, so a test never
    hardcodes a credential the seed alone decides.
    """
    from app.seed import seed_password, seed_platform_principals

    await seed_platform_principals()
    return seed_password()


@pytest_asyncio.fixture
async def admin_headers(client, db):
    """Log in as the seeded platform admin and return an auth header."""
    return await login_as(client, "admin")


@pytest_asyncio.fixture
async def user_headers(client, db):
    """Log in as the seeded platform client (the end-user role) and return a header."""
    return await login_as(client, "client")


@pytest.fixture
def parse_sse():
    """Return a parser turning a raw SSE body into ``[{event, data}, ...]``."""

    def _parse(text: str) -> list[dict]:
        events: list[dict] = []
        for block in text.replace("\r\n", "\n").split("\n\n"):
            event, data = None, None
            for line in block.splitlines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data = line.split(":", 1)[1].strip()
            if event is not None:
                events.append({"event": event, "data": data})
        return events

    return _parse


@pytest.fixture(autouse=True)
def _isolate_usage_tally():
    """Give every test a clean gateway usage tally.

    ``aegis.gateway``'s tally is a process global, which is correct for the metric
    it serves and wrong for a suite: two platform tests assert honest zeros
    "before any metered call", and they began failing the moment an unrelated file
    that meters calls started sorting ahead of them. Reordering would only move the
    problem to the next file that meters.
    """
    reset_usage_tally()
    yield
    reset_usage_tally()
