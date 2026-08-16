"""Contract tests: AgentConfig defaults + the single durable-checkpointer seam.

Freezes the risk-only gate config and the durable-checkpointer selection seam
(§1.3). Defaults must preserve today's behaviour: risk-only gating, memory saver.
There is ONE checkpointer path — :func:`app.data.session.get_agent_checkpointer`.
"""

from __future__ import annotations

from langgraph.checkpoint.memory import InMemorySaver

from app.agent import AgentConfig
from app.api.schemas import RiskLevel
from app.data.session import get_agent_checkpointer, reset_agent_checkpointer


def test_agent_config_defaults_unchanged():
    cfg = AgentConfig()
    # The human gate is driven by tool risk (gate_min_risk) and nothing else.
    assert cfg.gate_min_risk is RiskLevel.HIGH
    assert cfg.stream_chunk_words == 4
    assert cfg.max_plan_iterations == 2


def test_agent_config_fields_are_settable():
    cfg = AgentConfig(gate_min_risk=RiskLevel.MEDIUM, stream_chunk_words=8)
    assert cfg.gate_min_risk is RiskLevel.MEDIUM
    assert cfg.stream_chunk_words == 8


def test_default_checkpointer_is_in_memory(monkeypatch):
    # Default settings ('memory') must select InMemorySaver, requiring no Postgres.
    reset_agent_checkpointer()
    try:
        saver = get_agent_checkpointer()
        assert isinstance(saver, InMemorySaver)
    finally:
        reset_agent_checkpointer()


def test_postgres_checkpointer_is_selected_lazily(monkeypatch):
    # With the flag flipped, selection routes to the Postgres builder; because the
    # 'langgraph-checkpoint-postgres' package is not installed here it raises a
    # clear RuntimeError rather than importing Postgres at module load.
    import app.config as config_mod

    config_mod.get_settings.cache_clear()
    reset_agent_checkpointer()
    monkeypatch.setenv("AGENT_CHECKPOINTER", "postgres")
    try:
        try:
            get_agent_checkpointer()
        except RuntimeError as exc:
            assert "langgraph-checkpoint-postgres" in str(exc)
        except ImportError:
            # Package genuinely missing surfaced as ImportError is also acceptable.
            pass
    finally:
        config_mod.get_settings.cache_clear()
        reset_agent_checkpointer()
