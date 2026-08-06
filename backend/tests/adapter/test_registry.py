"""Registry, prompts and corpus surface tests."""

from __future__ import annotations

import app.adapter as adapter
from app.adapter.corpus import load_seed_corpus
from app.adapter.personas import get_persona
from app.adapter.prompts import render_system_prompt
from app.adapter.tools import TOOL_REGISTRY


def test_registry_reexports_present():
    for name in (
        "generate_synthetic",
        "TOOL_REGISTRY",
        "ALLOWLIST",
        "run_tool",
        "PERSONAS",
        "SYSTEM_PROMPTS",
        "FEATURES",
        "TARGET",
        "load_seed_corpus",
    ):
        assert hasattr(adapter, name), name


def test_tool_definitions_are_mcp_shaped():
    for spec in TOOL_REGISTRY.values():
        definition = spec.definition()
        assert definition["type"] == "function"
        assert definition["function"]["name"] == spec.name
        assert "parameters" in definition["function"]
        assert definition["function"]["parameters"]["type"] == "object"


def test_admin_prompt_lists_all_tools():
    prompt = render_system_prompt(get_persona("operations_lead"))
    for name in TOOL_REGISTRY:
        assert name in prompt


def test_client_prompt_scopes_data_and_tools():
    prompt = render_system_prompt(get_persona("client"))
    assert "add_case_note" in prompt
    assert "assign_request" not in prompt
    assert "customer_id" in prompt  # OWN data scope surfaced


def test_seed_corpus_loads_typed_documents():
    docs = load_seed_corpus()
    assert len(docs) >= 3
    ids = {d.id for d in docs}
    assert "doc-seed-0001" in ids
    assert all(d.body for d in docs)
    assert all(d.source == "seed" for d in docs)
