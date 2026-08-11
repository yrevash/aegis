"""OpenTelemetry GenAI semantic-convention attribute keys and operation names.

Centralising these constants keeps span instrumentation consistent across a
host application and documents which version of the (still experimental) GenAI
conventions we target.

Verified against: OpenTelemetry GenAI semantic conventions, August 2026. Note the
recent renames — ``gen_ai.system`` → ``gen_ai.provider.name`` (semconv v1.37.0)
and ``prompt_tokens``/``completion_tokens`` → ``usage.input_tokens`` /
``usage.output_tokens``. We emit the new keys and also set the deprecated
``gen_ai.system`` alias for tooling still reading it.

``SpanKind`` is **not** redefined here — it is reused from
:mod:`aegis.core.events`, which already carries the superset of all 9 kinds
(the pre-extraction 8-kind enum plus ``EVALUATOR``) so the same enum drives both
the live AG-UI event stream and the exported OTel/OpenInference spans.
"""

from __future__ import annotations

from enum import StrEnum

from aegis.core.events import SpanKind

__all__ = [
    "A2A_FROM",
    "A2A_PROTOCOL",
    "A2A_REASON",
    "A2A_TO",
    "ANSWER_CACHE_HIT",
    "ANSWER_CACHE_SIMILARITY",
    "DEFAULT_PROVIDER",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_PROVIDER_NAME",
    "GEN_AI_REQUEST_MAX_TOKENS",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_REQUEST_TEMPERATURE",
    "GEN_AI_RESPONSE_MODEL",
    "GEN_AI_SYSTEM",
    "GEN_AI_USAGE_COST",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "GRAPH_NODE",
    "GRAPH_NODE_DURATION_MS",
    "GRAPH_NODE_LABEL",
    "GUARDRAIL_LAYER",
    "GUARDRAIL_STAGE",
    "GUARDRAIL_VERDICT",
    "OPENINFERENCE_SPAN_KIND",
    "RERANK_INPUT_COUNT",
    "RERANK_OUTPUT_COUNT",
    "RETRIEVAL_CACHE_HIT",
    "RETRIEVAL_CANDIDATE_COUNT",
    "RETRIEVAL_QUERY",
    "RETRIEVAL_RESULT_COUNT",
    "RETRIEVAL_REWRITTEN",
    "RETRIEVAL_ROUNDS",
    "ROUTER_REASON",
    "ROUTER_ROLE",
    "ROUTER_USED_LLM",
    "TOOL_NAME",
    "TOOL_OK",
    "TOOL_RISK",
    "GenAIOperation",
    "SpanKind",
]

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
# attribute (AGENT/CHAIN/TOOL/RETRIEVER/RERANKER/GUARDRAIL/LLM/EMBEDDING/
# EVALUATOR). We set this string attribute directly — the ``openinference-*``
# instrumentation packages are *not* a dependency, so we neither import nor
# require them.
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

# A2A-style labelled agent handoff (the supervisor dispatching a turn to a
# specialist). Emitted as a dedicated span so the trace tree reads as an
# explicit agent-to-agent handoff, not just a routing attribute on a node.
A2A_FROM = "app.a2a.from"  # the dispatching agent (e.g. "supervisor")
A2A_TO = "app.a2a.to"  # the specialist role the turn is handed to
A2A_REASON = "app.a2a.reason"  # why this specialist was chosen
A2A_PROTOCOL = "app.a2a.protocol"  # labelling convention ("a2a")

# Agentic retrieval (bounded reformulate-and-re-retrieve loop).
RETRIEVAL_ROUNDS = "app.retrieval.rounds"
RETRIEVAL_REWRITTEN = "app.retrieval.rewritten"

# Answer-level semantic cache (final-answer reuse across equivalent questions).
ANSWER_CACHE_HIT = "app.answer_cache.hit"
ANSWER_CACHE_SIMILARITY = "app.answer_cache.similarity"

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
