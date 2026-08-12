// Canonical CustomEvent names — mirrors aegis/src/aegis/core/stream_names.py EXACTLY.
// AG-UI carries domain payloads via CustomEvent(name, value); these constants are the
// agreed `name` strings shared between the backend and this frontend mirror.
export const STREAM_NAMES = {
  REASONING: "reasoning",
  GUARDRAIL_VERDICT: "guardrail_verdict",
  SHAP_EXPLANATION: "shap_explanation",
  CONFORMAL_INTERVAL: "conformal_interval",
  RETRIEVAL_CITATIONS: "retrieval_citations",
  ROUTING: "routing",
  MEMORY_RECALL: "memory_recall",
  MODEL_CALL: "model_call",
  EVAL_RESULT: "eval_result",
} as const;
