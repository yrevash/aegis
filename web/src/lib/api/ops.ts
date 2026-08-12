/**
 * LLM-Ops endpoint contracts — a faithful TypeScript mirror of the `/ops/*`
 * Pydantic models in `backend/src/app/api/schemas.py`.
 *
 * These back the self-improvement loop: the harness watches its own eval traces,
 * `diagnose` proposes a better prompt (a draft version), a human approves via the
 * tiered gate, and one call can `rollback` to the last-good version.
 *
 * @see backend/src/app/api/schemas.py
 */

/** Lifecycle status of a prompt version. */
export type PromptStatus = 'draft' | 'staged' | 'active' | 'archived'

/** One prompt version row. Mirrors `OpsPromptVersionRow`. */
export interface OpsPromptVersionRow {
  id: number
  prompt_key: string
  version: number
  status: PromptStatus | string
  created_by: string | null
  notes: string | null
  created_at: string | null
}

/** Response from `GET /ops/prompts`. */
export interface OpsPromptsResponse {
  prompt_key: string
  rows: OpsPromptVersionRow[]
}

/** Response from `GET /ops/prompts/active`. */
export interface OpsActivePromptResponse {
  prompt_key: string
  version: number | null
  status: string | null
  system_prompt: string | null
  config: Record<string, unknown>
  created_by: string | null
  notes: string | null
  cached: boolean
}

/** The eval metric families the harness scores. */
export type EvalMetric = 'answer' | 'retrieval' | 'tool' | 'guardrail' | string

/** One eval result row. Mirrors `OpsEvalRow`. */
export interface OpsEvalRow {
  id: number
  run_id: string | null
  metric: EvalMetric
  score: number
  passed: boolean
  detail: Record<string, unknown>
  ts: string | null
}

/** Response from `GET /ops/evals`. */
export interface OpsEvalsResponse {
  rows: OpsEvalRow[]
}

/** One release awaiting the human gate. Mirrors `OpsReleaseApprovalRow`. */
export interface OpsReleaseApprovalRow {
  approval_id: string
  prompt_key: string | null
  draft_version_id: number | null
  /** Plain string (not narrowed): 'low' | 'medium' | 'high'. */
  risk: string
  reason: string | null
  created_at: string | null
}

/** Response from `GET /ops/releases/pending`. */
export interface OpsPendingReleasesResponse {
  rows: OpsReleaseApprovalRow[]
}

/** Body for `POST /ops/diagnose`. */
export interface OpsDiagnoseRequest {
  prompt_key: string
  limit?: number
}

/** Response from `POST /ops/diagnose`. */
export interface OpsDiagnoseResponse {
  draft_version_id: number | null
  failure_summary: string
  failures_considered: number
  metric_breakdown: Record<string, number>
}

/** Body for `POST /ops/release`. */
export interface OpsReleaseRequest {
  draft_version_id: number
  autonomy?: 'tiered' | 'auto' | 'manual' | string
  margin?: number
}

/** Response from `POST /ops/release`. */
export interface OpsReleaseResponse {
  /** 'promoted' | 'staged_for_approval' | 'rejected'. */
  outcome: string
  /** Plain string: 'low' | 'medium' | 'high'. */
  risk_level: string
  risk_reasons: string[]
  eval_score: number
  baseline_score: number
  reason: string
  approval_id: string | null
}

/** Body for `POST /ops/rollback`. */
export interface OpsRollbackRequest {
  prompt_key: string
}

/** Response from `POST /ops/rollback`. */
export interface OpsRollbackResponse {
  prompt_key: string
  reverted: boolean
  active_version: number | null
}

/** Response from `POST /ops/releases/{id}/decide`. */
export interface OpsReleaseDecisionResponse {
  approval_id: string
  approved: boolean
  /** 'promoted' | 'archived' | 'unknown'. */
  outcome: string
  prompt_key: string | null
  active_version: number | null
}
