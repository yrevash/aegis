"""Shared fixtures for gateway unit tests (no live litellm, no network).

Every test gets a fresh, known gateway state: a fake `GatewayConfig` (mirroring
the values a host application would supply) and the default no-op
governance/observability hooks, reset before each test so nothing leaks
between tests or test files.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

import aegis.gateway.llm as llm_mod


@dataclass
class FakeGatewayConfig:
    """A `GatewayConfig` with fixed test values (mirrors a real host's settings)."""

    base_url: str = "https://genailab.tcs.in"
    api_key: str = "test-key"
    ssl_verify: bool = False
    max_output_tokens: int = 1024
    timeout_seconds: float = 60.0
    budget_fail_open: bool = False


@pytest.fixture(autouse=True)
def _reset_gateway_state(monkeypatch):
    """Reset every module-global the gateway carries, before each test."""
    monkeypatch.setattr(llm_mod, "_config", FakeGatewayConfig())
    monkeypatch.setattr(llm_mod, "_governance", llm_mod._NoOpGovernance())
    monkeypatch.setattr(llm_mod, "_observability", llm_mod._NoOpObservability())
    monkeypatch.setattr(llm_mod, "_ssl_configured", False)
    monkeypatch.setattr(llm_mod, "_tally", llm_mod._UsageTally())
