// Canonical CustomEvent names — mirrors aegis/src/aegis/core/stream_names.py `ALL` EXACTLY.
//
// AG-UI carries domain payloads via CustomEvent(name, value); these constants are the
// agreed `name` strings shared between the backend and this frontend mirror. Every entry
// in the Python `ALL` frozenset MUST appear here.
//
// Parity is enforced by `backend/tests/api/test_stream_name_mirror.py`, which parses THIS
// file and diffs it against the Python frozenset. That test exists because the previous
// guarantee was a comment claiming "parity is asserted by the count below" next to
// `STREAM_NAME_COUNT = STREAM_NAME_SET.size` — a count derived from this list, compared
// against itself. It could never fail, and while it sat here the mirror silently lost the
// five media/voice/vision names added by later modules. A check that cannot fail is worse
// than no check: it stops anyone from writing the real one.
export const STREAM_NAMES = {
  REASONING: 'reasoning',
  GUARDRAIL_VERDICT: 'guardrail_verdict',
  GUARDRAIL_CACHE: 'guardrail_cache',
  GUARDRAIL_MEDIA: 'guardrail_media',
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
  VOICE_CHUNK: 'voice_chunk',
  VOICE_TRANSCRIPT: 'voice_transcript',
  VISION_SCREEN: 'vision_screen',
  VISION_ANALYSIS: 'vision_analysis',
  WEB_SEARCH: 'web_search',
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
