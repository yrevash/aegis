"""Shared fixtures for the wiring tests — fakes only, no live infrastructure.

Everything the vertical slice touches (the LLM gateway, retrieval, guardrails, the
ML spine, the action tools) is faked here and injected through :class:`AgentDeps`
so the agent graph and the API run with no network, no keys, and no Postgres/
Neo4j/Redis. The one real dependency exercised is the audit log, bound to an
in-memory aiosqlite database so audit writes actually round-trip.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# Ensure the ``src`` layout is importable even when the editable install's .pth
# is not honoured by the active interpreter (keeps the suite runnable anywhere).
_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# Same guard for the sibling ``aegis`` package: its editable install's .pth is
# subject to the identical interpreter quirk (observed on macOS, where the
# generated .pth can end up carrying the OS-level "hidden" file flag, which the
# hardened stdlib ``site.py`` silently skips). Falling back to a direct sys.path
# entry keeps ``import aegis`` working regardless of whether that .pth was honoured.
_AEGIS_SRC = Path(__file__).resolve().parents[2] / "aegis" / "src"
if _AEGIS_SRC.is_dir() and str(_AEGIS_SRC) not in sys.path:
    sys.path.insert(0, str(_AEGIS_SRC))

from typing import TYPE_CHECKING  # noqa: E402

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
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
    uncertain: bool = True,
    block_input: bool = False,
    high_risk: bool = False,
    degenerate: bool = False,
) -> AgentDeps:
    """Build an :class:`AgentDeps` wired entirely to canned fakes.

    Args:
        propose_tool: Whether the planner proposes an action tool call.
        uncertain: Whether the ML fake returns a wide, defer-triggering interval.
        block_input: Whether the input rail blocks the query.
        high_risk: Whether the tool is reported as HIGH risk (forces the gate).
        degenerate: Whether the ML fake returns a degenerate, abstain-triggering
            prediction (very low confidence + very wide interval).
    """
    from app.adapter import describe_prediction
    from app.agent import AgentConfig, AgentDeps  # local: keeps langgraph lazy

    async def check_input(text: str) -> GuardResult:
        if block_input:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason="blocked by policy", text=text
            )
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def check_output(text: str) -> GuardResult:
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    async def retrieve(query: str, *, persona: str | None = None) -> RetrievalResult:
        return RetrievalResult(
            answer_context="Spotlighted context about request R1.",
            sources=[Source(id="kb-1", text="Refund policy", score=0.9)],
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
                    "The request is overdue and matches the refund policy. "
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

    def predict_explain(features: dict) -> MLExplainResponse:
        if degenerate:
            interval, confidence = (0.0, 240.0), 0.3
        elif uncertain:
            interval, confidence = (2.0, 48.0), 0.6
        else:
            interval, confidence = (11.0, 13.0), 0.95
        width = interval[1] - interval[0]
        return MLExplainResponse(
            prediction=12.0,
            conformal_interval=interval,
            conformal_confidence=confidence,
            interval_width=width,
            shap_attribution=[
                ShapFeature(feature="priority", value=1.0, contribution=0.42),
                ShapFeature(feature="queue_depth_at_open", value=8.0, contribution=-0.20),
            ],
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

    def features_for(query: str, persona: str | None) -> dict:
        return {"priority": 1, "queue_depth_at_open": 8}

    return AgentDeps(
        complete=complete,
        retrieve=retrieve,
        check_input=check_input,
        check_output=check_output,
        predict_explain=predict_explain,
        tool_definitions_for=tool_definitions_for,
        run_tool=run_tool,
        tool_risk=tool_risk,
        render_system_prompt=render_system_prompt,
        features_for=features_for,
        describe_prediction=describe_prediction,
        config=AgentConfig(stream_chunk_words=4),
    )


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


@pytest_asyncio.fixture
async def db(tmp_path):
    """Bind an aiosqlite engine and create every table (for the audit log)."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'wiring.db'}")
    configure_engine(engine)
    await bootstrap(engine)
    yield get_sessionmaker()
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    """Yield an httpx client bound to the ASGI app with isolated dashboards.

    Fresh graph/metrics stores are injected per test so accumulated state never
    leaks between tests. Dependency overrides are cleared on teardown.
    """
    import httpx

    from app.api import routes as api_routes
    from app.main import app

    graph_store = api_routes.GraphStore()
    metrics_store = api_routes.MetricsStore()
    app.dependency_overrides[api_routes.get_graph_store] = lambda: graph_store
    app.dependency_overrides[api_routes.get_metrics_store] = lambda: metrics_store

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def admin_headers(client, db):
    """Log in as the demo admin (password ``demo``) and return an auth header."""
    resp = await client.post(
        "/auth/login", json={"username": "admin", "password": "demo"}
    )
    return {"Authorization": f"Bearer {resp.json()['token']}"}


@pytest_asyncio.fixture
async def user_headers(client, db):
    """Log in as the demo client (the end-user role) and return an auth header."""
    resp = await client.post(
        "/auth/login", json={"username": "client", "password": "demo"}
    )
    return {"Authorization": f"Bearer {resp.json()['token']}"}


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
