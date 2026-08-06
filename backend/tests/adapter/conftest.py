"""Shared fixtures for the adapter test-suite.

These tests run with **no live infrastructure and no API keys**: the LLM gateway
and the audit sink are replaced by in-process fakes, and the record store is the
in-memory implementation. Nothing here touches Postgres, Neo4j, Redis or the
network.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make `app` importable from the source tree without an editable install.
_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.adapter.schema import Category  # noqa: E402
from app.core.models import ModelRole  # noqa: E402


class FakeLLMResult:
    """A stand-in for ``app.core.llm.LLMResult`` (only ``.content`` is read)."""

    def __init__(self, content: str) -> None:
        self.content = content


def _tickets_payload(count: int) -> str:
    tickets = [
        {"title": f"LLM ticket {i}", "description": f"Fabricated description number {i}."}
        for i in range(count)
    ]
    return json.dumps({"tickets": tickets})


def _documents_payload(count: int) -> str:
    docs = [
        {"title": f"LLM doc {i}", "body": f"Fabricated knowledge body number {i}."}
        for i in range(max(count, 6))
    ]
    return json.dumps({"documents": docs})


@pytest.fixture
def fake_complete():
    """Return an async ``complete`` stub honouring the gateway contract.

    It answers CHEAP calls with fabricated tickets and GENERATION calls with
    fabricated documents, and records every call it received for assertions.
    """
    calls: list[dict] = []

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):
        calls.append({"role": role, "response_format": response_format})
        if role is ModelRole.GENERATION:
            return FakeLLMResult(_documents_payload(6))
        return FakeLLMResult(_tickets_payload(8))

    complete.calls = calls  # type: ignore[attr-defined]
    return complete


@pytest.fixture
def audit_sink():
    """Return an async ``record_audit`` stub that captures every entry."""
    entries: list[dict] = []

    async def record_audit(*, action, actor, model, trace_id, payload, approved_by=None):
        entries.append(
            {
                "action": action,
                "actor": actor,
                "model": model,
                "trace_id": trace_id,
                "payload": payload,
                "approved_by": approved_by,
            }
        )

    record_audit.entries = entries  # type: ignore[attr-defined]
    return record_audit


@pytest.fixture
def all_categories():
    """Return every domain category (handy for coverage assertions)."""
    return list(Category)
