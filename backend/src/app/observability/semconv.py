"""OpenTelemetry GenAI semantic-convention attribute keys and operation names.

Centralising these constants keeps span instrumentation consistent across the
codebase and documents which version of the (still experimental) GenAI
conventions we target.

Verified against: OpenTelemetry GenAI semantic conventions, August 2026. Note the
recent renames — ``gen_ai.system`` → ``gen_ai.provider.name`` (semconv v1.37.0)
and ``prompt_tokens``/``completion_tokens`` → ``usage.input_tokens`` /
``usage.output_tokens``. We emit the new keys and also set the deprecated
``gen_ai.system`` alias for tooling still reading it.
"""

from __future__ import annotations

from enum import StrEnum

# ── Request / response attributes ────────────────────────────────────────────
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_PROVIDER_NAME = "gen_ai.provider.name"
GEN_AI_SYSTEM = "gen_ai.system"  # deprecated alias of provider.name; still emitted
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"

# ── Usage / cost attributes ──────────────────────────────────────────────────
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_USAGE_COST = "gen_ai.usage.cost"  # non-standard extension: USD cost

# The real gateway is the TCS GenAI Lab (an OpenAI-compatible endpoint,
# ``genailab.tcs.in``) — stamp that, not a fabricated Azure provider.
DEFAULT_PROVIDER = "tcs.genailab"

# ── OpenInference span-kind convention ───────────────────────────────────────
# Phoenix renders a span in its trace tree by the ``openinference.span.kind``
# attribute (AGENT/CHAIN/TOOL/RETRIEVER/RERANKER/GUARDRAIL/LLM/EMBEDDING). We set
# this string attribute directly — the ``openinference-*`` instrumentation
# packages are *not* a dependency, so we neither import nor require them.
OPENINFERENCE_SPAN_KIND = "openinference.span.kind"

# ── Non-LLM span attributes (graph node / retrieval / guardrail / tool) ───────
# Namespaced app.* keys plus a couple of OpenInference-compatible ones so Phoenix
# surfaces the retrieval query and result count on the RETRIEVER span.
GRAPH_NODE = "app.graph.node"
GRAPH_NODE_LABEL = "app.graph.node.label"
GRAPH_NODE_DURATION_MS = "app.graph.node.duration_ms"

RETRIEVAL_QUERY = "input.value"  # OpenInference: the retrieval query text
RETRIEVAL_RESULT_COUNT = "app.retrieval.result_count"
RETRIEVAL_CANDIDATE_COUNT = "app.retrieval.candidate_count"
RETRIEVAL_CACHE_HIT = "app.retrieval.cache_hit"

RERANK_INPUT_COUNT = "app.rerank.input_count"
RERANK_OUTPUT_COUNT = "app.rerank.output_count"

ROUTER_ROLE = "app.router.role"
ROUTER_REASON = "app.router.reason"
ROUTER_USED_LLM = "app.router.used_llm"

GUARDRAIL_STAGE = "app.guardrail.stage"
GUARDRAIL_VERDICT = "app.guardrail.verdict"
GUARDRAIL_LAYER = "app.guardrail.layer"

TOOL_NAME = "tool.name"  # OpenInference-compatible tool attribute
TOOL_RISK = "app.tool.risk"
TOOL_OK = "app.tool.ok"


class GenAIOperation(StrEnum):
    """The ``gen_ai.operation.name`` values used in this codebase."""

    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    TEXT_COMPLETION = "text_completion"


class SpanKind(StrEnum):
    """OpenInference ``openinference.span.kind`` values Phoenix knows how to render.

    Setting this attribute is what makes a plain OTel span show up in Phoenix's
    trace tree as an AGENT / RETRIEVER / TOOL / … node rather than an unclassified
    span, so the whole agent run reads as a nested tree.
    """

    AGENT = "AGENT"
    CHAIN = "CHAIN"
    TOOL = "TOOL"
    RETRIEVER = "RETRIEVER"
    RERANKER = "RERANKER"
    GUARDRAIL = "GUARDRAIL"
    LLM = "LLM"
    EMBEDDING = "EMBEDDING"
