// Canonical CustomEvent names — mirrors aegis/src/aegis/core/stream_names.py `ALL` EXACTLY.
//
// AG-UI carries domain payloads via CustomEvent(name, value); these constants are the
// agreed `name` strings shared between the backend and this frontend mirror. Every entry
// in the Python `ALL` frozenset MUST appear here (parity is asserted by the count below).
export const STREAM_NAMES = {
  REASONING: 'reasoning',
  GUARDRAIL_VERDICT: 'guardrail_verdict',
  GUARDRAIL_CACHE: 'guardrail_cache',
  SHAP_EXPLANATION: 'shap_explanation',
  CONFORMAL_INTERVAL: 'conformal_interval',
  ML_MODEL: 'ml_model',
  RETRIEVAL_CITATIONS: 'retrieval_citations',
  RETRIEVAL_CACHE: 'retrieval_cache',
  ROUTING: 'routing',
  MEMORY_RECALL: 'memory_recall',
  MEMORY_WRITE: 'memory_write',
  MEMORY_CACHE: 'memory_cache',
  MODEL_CALL: 'model_call',
  EVAL_RESULT: 'eval_result',
  OPS_DIAGNOSE: 'ops_diagnose',
  OPS_GATE_DECISION: 'ops_gate_decision',
  OPS_RELEASE: 'ops_release',
} as const

/** The canonical set of every known stream name (mirrors the Python `ALL`). */
export const STREAM_NAME_SET: ReadonlySet<string> = new Set(Object.values(STREAM_NAMES))

/** Number of known stream names — must equal len(aegis.core.stream_names.ALL). */
export const STREAM_NAME_COUNT = STREAM_NAME_SET.size

/** Return whether `name` is a registered CustomEvent name (mirrors is_known). */
export function isKnownStreamName(name: string): boolean {
  return STREAM_NAME_SET.has(name)
}

export type StreamName = (typeof STREAM_NAMES)[keyof typeof STREAM_NAMES]
