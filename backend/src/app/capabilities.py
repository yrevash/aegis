"""Aegis capabilities manifest — the single source of truth for the product's identity.

Every capability the platform ships is a first-class **Aegis module**: a branded
name presented **together with the honest underlying tech** (branding, never
hiding — this keeps the no-fakes bar). The canonical map lives in
``docs/learn/00-what-aegis-is.md`` and is mirrored here as typed data so the
API (``GET /platform/capabilities``), the frontend Platform view and the docs all
read one product from one place.

Each :class:`AegisModule` pairs the branded name with:

* the real ``tech`` underneath (LiteLLM, LangGraph, XGBoost/MAPIE/SHAP, …),
* a one-line honest ``summary`` of what it does,
* the actual ``module_path`` of the code that implements it (kept factual — every
  path is import-checked by ``tests/test_capabilities.py``), and
* a ``status`` — ``"live"`` for modules that always run, or ``"optional"`` for the
  ones gated on an optional dependency (e.g. the MCP tool server needs the MCP SDK;
  NeMo Colang is an optional guardrails layer atop the always-on programmatic rails).

This module is pure data + Pydantic types: importing it pulls in no heavy runtime
dependency, so it is safe to import from the API layer and from tests.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PRODUCT_NAME = "Aegis"
PRODUCT_VERSION = "0.1.0"
PRODUCT_TAGLINE = (
    "A domain-agnostic agentic platform — cheap-to-scale, trustworthy, secure and "
    "auditable — presented as a cohesive set of Aegis modules, each branded name "
    "paired with its honest underlying tech."
)

ModuleCategory = Literal["runtime", "knowledge", "trust", "ops", "platform"]
ModuleStatus = Literal["live", "optional"]


class AegisModule(BaseModel):
    """One branded Aegis module, paired with its honest underlying tech.

    The manifest entry for a single platform capability. ``name`` is the branded
    label the product surfaces; ``tech`` is always shown alongside it so the
    branding never hides what actually runs. ``module_path`` points at the real
    implementing code (import-checked in the test suite), keeping the manifest
    factual rather than aspirational.
    """

    key: str = Field(description="Stable machine key, e.g. 'gateway'.")
    name: str = Field(description="Branded module name, e.g. 'Aegis Gateway'.")
    tech: str = Field(description="Honest underlying tech, e.g. 'LiteLLM'.")
    summary: str = Field(description="One honest line describing what the module does.")
    category: ModuleCategory = Field(
        description="Coarse grouping: runtime | knowledge | trust | ops | platform."
    )
    module_path: str = Field(
        description="Importable path of the real implementing code, e.g. 'app.core.llm'."
    )
    status: ModuleStatus = Field(
        default="live",
        description="'live' (always runs) or 'optional' (gated on an optional dependency).",
    )


# ─────────────────────────────────────────────────────────────────────────────
# The manifest — one entry per row of ENTERPRISE_MATURITY_PLAN.md §1, verbatim
# names + tech. Keep factual: each ``module_path`` must import (see the test).
# ─────────────────────────────────────────────────────────────────────────────

AEGIS_MODULES: list[AegisModule] = [
    AegisModule(
        key="gateway",
        name="Aegis Gateway",
        tech="LiteLLM",
        summary="Single model chokepoint: role routing, budgets, timeout, retry, usage ledger.",
        category="runtime",
        module_path="app.core.llm",
    ),
    AegisModule(
        key="router",
        name="Aegis Router",
        tech="LangGraph",
        summary="Multi-agent supervisor — routes a turn to the right specialist.",
        category="runtime",
        module_path="app.agent.router",
    ),
    AegisModule(
        key="memory",
        name="Aegis Memory",
        tech="Postgres + embedded Chroma",
        summary="Long-term memory: episodic, semantic and procedural, bitemporal, consolidated.",
        category="knowledge",
        module_path="app.memory",
    ),
    AegisModule(
        key="cache",
        name="Aegis Cache",
        tech="Redis",
        summary="Semantic response cache keyed on query meaning, not exact bytes.",
        category="knowledge",
        module_path="app.retrieval.cache",
    ),
    AegisModule(
        key="retrieval",
        name="Aegis Retrieval",
        tech="Neo4j/LightRAG + embedded NanoVectorDB",
        summary="Hybrid RAG: vector + graph + BM25 fused via RRF, LLM rerank, spotlighting.",
        category="knowledge",
        module_path="app.retrieval.pipeline",
    ),
    AegisModule(
        key="signal",
        name="Aegis Signal",
        tech="XGBoost + MAPIE + SHAP",
        summary="Trustworthy ML: ensemble with calibrated conformal intervals and SHAP.",
        category="trust",
        module_path="app.ml.model",
    ),
    AegisModule(
        key="voice",
        name="Aegis Voice",
        tech="hosted Whisper via LiteLLM",
        summary="Speech to text, chunked on silence, guarded by the full text rails.",
        category="runtime",
        module_path="app.voice",
    ),
    AegisModule(
        key="forecast",
        name="Aegis Forecast",
        tech="Nixtla statsforecast (AutoARIMA/AutoETS) + conformal intervals",
        summary="Time-series forecasts whose accuracy and interval coverage are "
        "measured on held-out windows, feeding a budget burn-down projection.",
        category="trust",
        module_path="app.forecast.service",
        status="optional",
    ),
    AegisModule(
        key="guardrails",
        name="Aegis Guardrails",
        tech="programmatic + NeMo Colang",
        summary="Input/output rails: injection, PII, schema and content checks.",
        category="trust",
        module_path="app.guardrails.rails",
    ),
    AegisModule(
        key="evals",
        name="Aegis Evals",
        tech="RAGAS-style proxies + LLM judge",
        summary="Trace-level and answer evaluation of each run.",
        category="trust",
        module_path="app.eval.harness",
    ),
    AegisModule(
        key="loop",
        name="Aegis Loop",
        tech="native",
        summary="LLM-Ops self-improvement: trace to eval to diagnose to tiered release.",
        category="ops",
        module_path="app.ops.release",
    ),
    AegisModule(
        key="governance",
        name="Aegis Governance",
        tech="Postgres RLS + JWT",
        summary="Multi-tenant RBAC, budgets, row-level security and audit log.",
        category="platform",
        module_path="app.core.governance",
    ),
    AegisModule(
        key="trace",
        name="Aegis Trace",
        tech="OpenTelemetry -> Phoenix",
        summary="End-to-end, glass-box tracing of every run.",
        category="ops",
        module_path="app.observability.otel",
    ),
    AegisModule(
        key="vision",
        name="Aegis Vision",
        tech="hosted Llama-3.2-90B-Vision + Presidio image redactor",
        summary="Image understanding with the injection screen ahead of the model — "
        "hygiene, screen, image-PII redaction, then the vision call and the output rails.",
        category="trust",
        module_path="app.vision",
    ),
    AegisModule(
        key="mcp",
        name="Aegis Tools / MCP",
        tech="native + MCP SDK",
        summary="Risk-tiered tool registry with a human gate, exposed over MCP.",
        category="platform",
        module_path="app.mcp.server",
        status="optional",
    ),
]
"""The canonical Aegis module manifest — the product's module identity, one place."""


def module_count() -> int:
    """Return how many Aegis modules the manifest declares."""
    return len(AEGIS_MODULES)
