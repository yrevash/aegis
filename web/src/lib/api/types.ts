/**
 * Endpoint request/response contracts — a faithful TypeScript mirror of the
 * non-streaming Pydantic models in `backend/src/app/api/schemas.py`.
 *
 * @see backend/src/app/api/schemas.py
 */

import type { GraphEdge, GraphNode, RiskLevel, Role, ShapFeature } from '@/lib/stream'

/** Body for `POST /auth/login`. */
export interface LoginRequest {
  username: string
  password: string
}

/**
 * Response from `POST /auth/login`. `token` is now a signed JWT (stored and sent
 * as a Bearer token); `role` scopes the served portal and `tenant_id` pins the
 * session to its tenant for multi-tenant governance.
 */
export interface LoginResponse {
  token: string
  role: Role
  /** The tenant this session belongs to, or null (platform scope). */
  tenant_id: number | null
}

/** Body for `POST /query` (the response is the SSE stream, not JSON). */
export interface QueryRequest {
  query: string
  /** Adapter persona id; scopes data + tools. */
  persona?: string | null
}

/** Body for `GET /graph` — the current context graph for the viz. */
export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
}

/** Body for `POST /ml/explain`. */
export interface MLExplainRequest {
  /** Feature name → value for one prediction. */
  features: Record<string, number | string>
}

/** Response from `POST /ml/explain`. */
export interface MLExplainResponse {
  prediction: number | string
  conformal_interval: [number, number] | null
  conformal_confidence: number | null
  /** Conformal interval width (upper − lower); the deferral signal, or null. */
  interval_width: number | null
  /** Conformal prediction-set size (classification; 1 = singleton), or null. */
  prediction_set_size: number | null
  shap_attribution: ShapFeature[]
}

/** Body for `GET /metrics` — live figures for the efficiency dashboard. */
export interface MetricsResponse {
  cache_hit_rate: number
  small_model_share: number
  cost_per_1k_queries_usd: number
  /**
   * A measured **grounding proxy**, not an eval-harness/LLM-judge score: the
   * fraction of completed runs that both finished cleanly and retrieved backing
   * context (touched at least one knowledge-graph node before answering). Null
   * before any run.
   */
  quality_score: number | null
  /** Effective role → model map. */
  routing: Record<string, string>
  /**
   * Cumulative USD saved versus running every query on the frontier model —
   * the headline efficiency win. The backend tallies this from small-model
   * routing only; cache hits bypass the tally, so caching is not counted here.
   */
  cost_saved_usd: number
  /** What the same workload would have cost on the frontier model, in USD. */
  baseline_cost_usd: number
  /**
   * Measured chat completions served since the backend process started (the
   * gateway usage tally). Not a per-day figure — the honest process-wide count
   * of LLM calls; resets on restart.
   */
  total_calls: number
  /**
   * Count of human-gate approvals cleared to the terminal APPROVED state (from
   * the durable approvals store). 0 when none — never fabricated.
   */
  actions_approved: number
  /**
   * 95th-percentile whole-run duration in milliseconds, from the backend's
   * per-process latency window. Null before any run is recorded — an honest
   * empty state, not a fabricated zero.
   */
  p95_latency_ms: number | null
}

/** Whether a paused action was approved or rejected by the human gate. */
export type ApprovalDecision = 'approve' | 'reject'

/** Body for `POST /approval` — resolve a paused action. */
export interface ApprovalRequest {
  approval_id: string
  decision: ApprovalDecision
}

/** Response from `POST /approval`. */
export interface ApprovalResponse {
  approval_id: string
  accepted: boolean
}

/**
 * One row of the append-only audit trail (admin-only).
 * Mirrors the `AuditLog` record surfaced by `GET /audit` on the backend.
 */
export interface AuditLogRow {
  id: number
  /** ISO 8601 timestamp of when the action was recorded. */
  ts: string
  action: string
  actor: string | null
  model: string | null
  trace_id: string | null
  approved_by: string | null
}

/** Response from `GET /audit` — newest-first list of audit rows. */
export interface AuditLogResponse {
  rows: AuditLogRow[]
}

// ── Approvals inbox (durable, async approval path) ──────────────────────────

/** The ML readout captured with an approval row (for the inbox card). */
export interface ApprovalMlSnapshot {
  prediction?: number | string | null
  conformal_confidence?: number | null
  conformal_interval?: [number, number] | null
  /** The autonomy band that routed this to the inbox, e.g. 'defer'. */
  band?: string | null
}

/**
 * One row of the persisted approvals inbox. Mirrors the backend `approvals`
 * table surfaced by `GET /approvals`.
 */
export interface ApprovalRow {
  id: number
  run_id: string
  action: string
  args: Record<string, unknown>
  risk: RiskLevel
  rationale: string
  /** e.g. 'pending' | 'approved' | 'rejected' | 'escalated' | 'expired'. */
  status: string
  persona: string | null
  /** ISO 8601 SLA deadline, or null. */
  sla_deadline: string | null
  /** ISO 8601 creation time. */
  created_at: string
  ml_snapshot: ApprovalMlSnapshot | null
}

/** Response from `GET /approvals`. */
export interface ApprovalsResponse {
  rows: ApprovalRow[]
}

/** Body for `POST /approvals/{id}/decision`. */
export interface ApprovalDecisionRequest {
  decision: ApprovalDecision
}

/** Response from `POST /approvals/{id}/decision`. */
export interface ApprovalDecisionResponse {
  id: number
  status: string
  accepted: boolean
}

// ── Admin: tenants, users, budgets, usage (multi-tenant governance) ─────────

/** A tenant (enterprise client). */
export interface Tenant {
  id: number
  name: string
  status: string
  created_at: string
}

/** Response from `GET /admin/tenants`. */
export interface TenantsResponse {
  rows: Tenant[]
}

/** A user within a tenant. */
export interface AdminUser {
  id: number
  username: string
  email: string | null
  role: string
  tenant_id: number | null
  is_active: boolean
}

/** Response from `GET /admin/users`. */
export interface UsersResponse {
  rows: AdminUser[]
}

/** Which entity a budget caps. */
export type BudgetScope = 'tenant' | 'user'

/** The rolling window a budget resets on. */
export type BudgetWindow = 'day' | 'month'

/** A hierarchical spend / rate cap. */
export interface Budget {
  id?: number
  scope_type: BudgetScope
  scope_id: number
  window: BudgetWindow
  token_cap: number | null
  usd_cap: number | null
  rpm: number | null
  tpm: number | null
}

/** Response from `GET /admin/budgets`. */
export interface BudgetsResponse {
  rows: Budget[]
}

/** Body for `POST /admin/budgets`. */
export type CreateBudgetRequest = Budget

// ── DevOps: tech stack + patch check (supply-chain transparency) ────────────

/** One dependency in the running Aegis stack. */
export interface StackComponent {
  name: string
  category: 'runtime' | 'backend' | 'frontend' | 'infra'
  /** The installable package / image name. */
  package: string
  /** Resolved version, or null when it could not be determined. */
  version: string | null
  /** Which Aegis module this component powers, or null for shared infra. */
  aegis_module: string | null
}

/** Response from `GET /stack` — the full software bill of materials. */
export interface StackResponse {
  /** ISO 8601 timestamp the stack was inventoried. */
  generated_at: string
  components: StackComponent[]
}

/** One package's freshness verdict from the patch check. */
export interface PatchResult {
  name: string
  installed: string | null
  latest: string | null
  status: 'current' | 'outdated' | 'unknown'
  note?: string
}

/**
 * Body for `POST /stack/patch-check` — optionally narrow the check to a subset
 * of packages; omit to check the whole stack.
 */
export interface PatchCheckRequest {
  packages?: string[]
}

/** Response from `POST /stack/patch-check` — installed vs latest per package. */
export interface PatchCheckResponse {
  /** ISO 8601 timestamp the check ran. */
  checked_at: string
  /** Whether the registry could be reached (false ⇒ results are best-effort). */
  online: boolean
  note: string
  results: PatchResult[]
}

// ── Client: risk map + savings (value + assurance) ──────────────────────────

/** One entry on the risk heat-map (OWASP-Agentic-aligned). */
export interface RiskEntry {
  id: string
  title: string
  category: string
  /** 1..5 likelihood band. */
  likelihood: number
  /** 1..5 impact band. */
  impact: number
  mitigation: string
  /** The control / requirement this maps to. */
  control_ref: string
  /** Residual risk after mitigation. */
  residual: 'low' | 'medium' | 'high'
}

/** Response from `GET /risk-map` — the agent-risk heat-map + its scale. */
export interface RiskMapResponse {
  /** ISO 8601 timestamp the map was generated. */
  generated_at: string
  note: string
  scale: {
    likelihood: number[]
    impact: number[]
  }
  risks: RiskEntry[]
}

/** One contributor to the total savings. */
export interface SavingsBreakdownRow {
  source: string
  saved_usd: number
  explanation: string
}

/** Response from `GET /savings` — baseline vs actual spend and what drove it. */
export interface SavingsResponse {
  /** ISO 8601 timestamp the figures were computed. */
  generated_at: string
  baseline_cost_usd: number
  actual_cost_usd: number
  saved_usd: number
  /** Fraction saved vs baseline, 0..1. */
  saved_pct: number
  note: string
  breakdown: SavingsBreakdownRow[]
}

/** Body for `POST /admin/users/{id}/role` — reassign a user's portal role. */
export interface UserRoleUpdateRequest {
  role: Role
}

/** Per-model usage roll-up row. */
export interface UsageModelRow {
  model: string
  cost_usd: number
  tokens: number
}

/** One point on the usage cost trend. */
export interface UsageSeriesPoint {
  ts: string
  cost_usd: number
}

/** Response from `GET /admin/usage` — spend + tokens by model, plus a trend. */
export interface UsageResponse {
  total_prompt_tokens: number
  total_completion_tokens: number
  total_cost_usd: number
  by_model: UsageModelRow[]
  series: UsageSeriesPoint[]
}

/** One branded Aegis module, paired with its honest underlying tech. */
export interface AegisModuleRow {
  name: string
  tech: string
  summary: string
  module_path: string
  category: 'runtime' | 'knowledge' | 'trust' | 'ops' | 'platform'
  status: 'live' | 'optional'
}

/** Response from the public `GET /platform/capabilities` — the module manifest. */
export interface CapabilitiesResponse {
  product: string
  tagline: string
  module_count: number
  modules: AegisModuleRow[]
}

/**
 * Response from the public `GET /platform/public-metrics`.
 *
 * Ratios and counts only — the absolute cost figures and the routing map stay
 * behind auth on `/metrics`. `p95_latency_ms` is null until runs are recorded,
 * which the UI renders as an honest "not yet measured", never a fabricated 0.
 */
export interface PublicMetricsResponse {
  cache_hit_rate: number
  small_model_share: number
  total_calls: number
  actions_approved: number
  p95_latency_ms: number | null
}
