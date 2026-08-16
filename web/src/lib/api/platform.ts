/**
 * Platform read-surface contracts — a faithful TypeScript mirror of the thin,
 * read-only `aegis.*` accessor routes in `backend/src/app/api/routes.py`
 * (Phase-3 · Task 3).
 *
 * These back the platform dashboards: MLOps (`/ml/model-card`), evals
 * (`/evals/report`), LLMOps (`/ops/params`), token-optimization
 * (`/gateway/optimization`), the harness (`/harness/config`), governance
 * (`/governance/dashboard`), security (`/security/posture`), latency
 * (`/latency`) and red-team (`/redteam/run`). Each shape matches its accessor
 * exactly.
 *
 * @see backend/src/app/api/schemas.py
 */

// ── MLOps · model card (`GET /ml/model-card`) ────────────────────────────────

/** One fitted soft-voting ensemble member. Mirrors `EnsembleMember`. */
export interface EnsembleMember {
  name: string
  kind: string
  weight: number
}

/** Body for `GET /ml/model-card` — honest, measured model metadata. */
export interface ModelCardResponse {
  task: string
  target: string
  features: string[]
  n_features: number
  categorical_features: string[]
  numeric_features: string[]
  encoded_feature_count: number
  ensemble_members: EnsembleMember[]
  conformal_method: string
  conformal_predictor: string
  conformal_coverage: number
  calibration_size: number
  training_size: number
  /** 'provided' | 'spec_provider' | 'synthetic' — how the training frame was sourced. */
  data_source: string
}

// ── Evals · regression-gate rollup (`GET /evals/report`) ─────────────────────

/** One metric's aggregate config + reading. Mirrors a `MetricConfig` dict. */
export interface EvalMetricConfig {
  name: string
  threshold: number
  higherIsBetter: boolean
  value: number | null
  passed: boolean
  cases: number
  computed?: boolean
}

/** One eval case's per-metric breakdown. */
export interface EvalCaseResult {
  name: string
  passed: boolean
  metrics: Array<{
    name: string
    value: number
    threshold: number
    higherIsBetter: boolean
    passed: boolean
  }>
}

/** Body for `GET /evals/report` — the offline regression-gate rollup. */
export interface EvalsReportResponse {
  overall: number
  passed: boolean
  metrics: EvalMetricConfig[]
  cases: EvalCaseResult[]
  /** How the figures were produced (e.g. 'offline_regression_gate'). */
  source: string
}

// ── LLMOps · loop params (`GET /ops/params`) ─────────────────────────────────

/** Body for `GET /ops/params` — the tunable self-improvement-loop knobs. */
export interface OpsParamsResponse {
  eval_margin: number
  high_diff_fraction: number
  low_diff_fraction: number
  safety_terms: string[]
  critical_config_markers: string[]
  tunable_config_keys: string[]
  tunable_max_delta: Record<string, number>
  auto_promote_ceiling: string
}

// ── Token-optimization (`GET /gateway/optimization`) ─────────────────────────

/** Measured per-role usage aggregate. */
export interface OptimizationRoleBreakdown {
  calls: number
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  small_model: boolean
}

/** The `summary` block — measured savings vs the frontier baseline. */
export interface OptimizationSummary {
  total_calls: number
  small_calls: number
  total_cost_usd: number
  baseline_cost_usd: number
  cost_saved_usd: number
  small_model_share: number | null
  by_role: Record<string, OptimizationRoleBreakdown>
  baseline_role: string
  baseline_model: string
}

/** The `config` block — the effective routing / fallback / baseline knobs. */
export interface OptimizationConfig {
  routing: Record<string, string>
  fallbacks: Record<string, string[]>
  timeout_seconds: number
  max_output_tokens: number
  baseline_role: string
  baseline_model: string
}

/** Body for `GET /gateway/optimization`. */
export interface GatewayOptimizationResponse {
  summary: OptimizationSummary
  config: OptimizationConfig
}

// ── Harness · tweakable config (`GET /harness/config`) ───────────────────────

/** One knob descriptor a UI renders an editable form field from. */
export interface HarnessKnob {
  key: string
  type: string
  value: unknown
  default: unknown
  doc: string
  allowed?: unknown[]
  minimum?: number
  maximum?: number
  nullable?: boolean
}

/** Body for `GET /harness/config`. */
export interface HarnessConfigResponse {
  knobs: HarnessKnob[]
  effective: Record<string, unknown>
}

// ── Governance dashboard (`GET /governance/dashboard`) ───────────────────────

/** A budget cap joined with its live consumption. Mirrors `BudgetStatusRow`. */
export interface BudgetStatusRow {
  budget: {
    id: number
    scope_type: string
    scope_id: number | null
    window: string
    token_cap: number | null
    usd_cap: number | null
    rpm: number | null
    tpm: number | null
    tenant_id: number | null
  }
  tokens_used: number
  cost_usd_used: number
  calls: number
  tokens_remaining: number | null
  usd_remaining: number | null
}

/** Rolled-up ledger spend for one tenant scope. Mirrors `UsageSummary`. */
export interface GovernanceUsageSummary {
  tenant_id: number | null
  window: string
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  total_cost_usd: number
  calls: number
  by_model: Array<{ model: string; prompt_tokens: number; completion_tokens: number; cost_usd: number; calls: number }>
  series: Array<{ bucket: string; cost_usd: number; tokens: number }>
}

/** Body for `GET /governance/dashboard` — the full tenant-scoped snapshot. */
export interface GovernanceDashboardResponse {
  tenant_id: number | null
  window: string
  tenants: Array<{ id: number; name: string; created_at?: string | null }>
  budgets: BudgetStatusRow[]
  users: Array<{ id: number; username: string; role: string; tenant_id: number | null }>
  usage: GovernanceUsageSummary
  recent_audit: Array<Record<string, unknown>>
}

// ── Security posture (`GET /security/posture`) ───────────────────────────────

/** Live status of a threat's control. */
export type PostureStatus = 'enforced' | 'partial' | 'not_covered'

/** One threat → control mapping with a live-derived status. Mirrors `PostureEntry`. */
export interface PostureEntry {
  threat_id: string
  name: string
  control: string
  module: string
  mechanism: string
  status: PostureStatus | string
  detail: string
  refs: string[]
}

/** The introspected wiring signals the statuses derive from. Mirrors `PostureSignals`. */
export interface PostureSignals {
  model_layer_wired: boolean
  nemo_available: boolean
  mode: string
  pii_engine: string
  rls_fail_closed: boolean
  rls_enforced_on: string
  jwt_dev_secret: boolean
  jwt_algorithm: string
  budget_hook_wired: boolean
  budget_fail_open: boolean
  gate_min_risk: string
  max_plan_iterations: number
  hazard_categories: number
  rls_tables: number
}

/** Body for `GET /security/posture`. */
export interface SecurityPostureResponse {
  entries: PostureEntry[]
  signals: PostureSignals
}

// ── Latency (`GET /latency`) ─────────────────────────────────────────────────

/** Aggregated latency for one node across recorded runs. */
export interface NodeLatency {
  node: string
  count: number
  p50_ms: number
  p95_ms: number
  max_ms: number
  total_ms: number
}

/** Body for `GET /latency` — per-node + per-run percentiles from real samples. */
export interface LatencyResponse {
  run_count: number
  per_node: NodeLatency[]
  run_p50_ms: number | null
  run_p95_ms: number | null
  run_max_ms: number | null
  slowest_node: string | null
  source: string
  window_capacity: number | null
  /** True when no runs have been recorded yet (an honest empty state). */
  empty: boolean
}

// ── Red-team (`POST /redteam/run`) ───────────────────────────────────────────

/** One scored attack (or benign control) result. */
export interface RedteamAttackResult {
  id: string
  category: string
  owasp?: string
  prompt: string
  expects: string
  verdict: string
  layer: string | null
  reason: string | null
  neutralized: boolean
  success: boolean
  needsLlm?: boolean
}

/** One per-category roll-up. `leaked` is the list of leaked attack *ids*. */
export interface RedteamCategoryReport {
  category: string
  total: number
  blocked: number
  blockRate: number
  leaked: string[]
}

/** Body for `POST /redteam/run` — the offline attack-battery report. */
export interface RedteamReportResponse {
  passed: boolean
  overall: {
    attacksTotal: number
    attacksBlocked: number
    blockRate: number
    controlsTotal: number
    falsePositives: number
    falsePositiveRate: number
  }
  thresholds: {
    minBlockRate: number
    maxFalsePositiveRate: number
  }
  categories: RedteamCategoryReport[]
  leaked: RedteamAttackResult[]
  falsePositiveDetail: RedteamAttackResult[]
  attacks: RedteamAttackResult[]
}
