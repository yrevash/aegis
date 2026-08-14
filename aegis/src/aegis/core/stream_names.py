"""Canonical CustomEvent names — the single source of truth shared by every module.

AG-UI carries domain payloads via ``CustomEvent(name, value)``. These constants are the
agreed ``name`` strings; the console mirrors them in ``web/src/lib/streamNames.ts``.
"""

from __future__ import annotations

REASONING = "reasoning"
GUARDRAIL_VERDICT = "guardrail_verdict"
#: An injection-classifier cache event (hit / miss) — the guardrail cost/observability feed.
GUARDRAIL_CACHE = "guardrail_cache"
#: A media guardrail verdict (image/audio): payload metadata plus the itemised list of
#: which media rails ran and which did not, so the console can never imply coverage
#: a control did not actually provide.
GUARDRAIL_MEDIA = "guardrail_media"
SHAP_EXPLANATION = "shap_explanation"
CONFORMAL_INTERVAL = "conformal_interval"
#: An ML model-card event — the honest, measured metadata of the model that is
#: serving (ensemble members, target/features, conformal coverage, split sizes).
ML_MODEL = "ml_model"
RETRIEVAL_CITATIONS = "retrieval_citations"
#: A retrieval semantic-cache event (hit / miss), carrying the cache provenance
#: (near-exact vs semantic, original query, cached-at) so the UI can show it.
RETRIEVAL_CACHE = "retrieval_cache"
ROUTING = "routing"
MEMORY_RECALL = "memory_recall"
#: A durable memory write (add/update/invalidate/delete/expire) — the memory changelog feed.
MEMORY_WRITE = "memory_write"
#: A semantic-cache event for recall (hit / miss / evict), with backend + similarity.
MEMORY_CACHE = "memory_cache"
MODEL_CALL = "model_call"
EVAL_RESULT = "eval_result"
#: The LLM-Ops self-improvement loop feed (aegis.ops). A Reflexion diagnose draft
#: (draft id + rationale + failure breakdown + risk tier).
OPS_DIAGNOSE = "ops_diagnose"
#: The release eval-gate + change-risk verdict (eval delta vs margin, risk tier, verdict).
OPS_GATE_DECISION = "ops_gate_decision"
#: The release outcome (promoted | staged_for_approval | rejected, version id, eval delta).
OPS_RELEASE = "ops_release"

ALL: frozenset[str] = frozenset(
    {REASONING, GUARDRAIL_VERDICT, GUARDRAIL_CACHE, GUARDRAIL_MEDIA, SHAP_EXPLANATION,
     CONFORMAL_INTERVAL,
     ML_MODEL, RETRIEVAL_CITATIONS, RETRIEVAL_CACHE, ROUTING, MEMORY_RECALL, MEMORY_WRITE,
     MEMORY_CACHE, MODEL_CALL, EVAL_RESULT, OPS_DIAGNOSE, OPS_GATE_DECISION, OPS_RELEASE}
)


def is_known(name: str) -> bool:
    """Return whether ``name`` is a registered CustomEvent name."""
    return name in ALL
