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

/**
 * One entry on the risk map (OWASP-Agentic-aligned). Carries **two** points on
 * the same 1..5 grid — where the risk sits with no control (`likelihood` ×
 * `impact`) and where the Aegis control leaves it (`residual_*`). `residual` is
 * derived server-side from the residual point, never authored beside it.
 */
export interface RiskEntry {
  id: string
  title: string
  category: string
  /** Inherent 1..5 likelihood, before the control. */
  likelihood: number
  /** Inherent 1..5 impact, before the control. */
  impact: number
  /** 1..5 likelihood left after the control — the axis controls actually move. */
  residual_likelihood: number
  /** 1..5 impact left after the control — moves only if the blast radius shrinks. */
  residual_impact: number
  /** Short client-facing name of the control, e.g. 'Human approval gate'. */
  control_name: string
  /** One plain-language sentence: what the control does. */
  mitigation: string
  /** Real file/module implementing the control — auditor provenance, not client copy. */
  control_ref: string
  /** Residual band, derived from `residual_likelihood × residual_impact`. */
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

/** One executable node of the agent graph, as served by `GET /agent/topology`. */
export interface AgentTopologyNode {
  /** Stable node id — exactly the name carried on `node_started`/`node_finished`. */
  id: string
  /** Human label the node's stream events carry. */
  label: string
  /** The graph's entrypoint routes straight here. */
  entry: boolean
  /** A run can finish at this node. */
  terminal: boolean
}

/** One directed edge between two executable nodes of the agent graph. */
export interface AgentTopologyEdge {
  source: string
  target: string
  /** True when the edge is a branch of a conditional router, not a fixed edge. */
  conditional: boolean
}

/**
 * Response from `GET /agent/topology` — the agent graph's real node/edge shape,
 * read off the compiled LangGraph by `aegis.agent.graph_topology`.
 *
 * The console's orchestration map renders from this instead of a hand-written DAG,
 * so the published picture cannot drift from the graph that actually runs.
 */
export interface AgentTopologyResponse {
  nodes: AgentTopologyNode[]
  edges: AgentTopologyEdge[]
}

// ── Aegis Voice — POST /voice/transcribe ────────────────────────────────────

/**
 * One time-aligned segment of a transcript.
 *
 * `confidence` is `null` whenever the provider reports none — which is the case
 * for the fleet's hosted Whisper deployment today. The UI renders that as
 * "not reported"; it must never be substituted with a derived number.
 */
export interface VoiceSegmentRow {
  index: number
  /** Seconds from the start of the WHOLE recording (chunk offsets added back). */
  start: number | null
  end: number | null
  text: string
  /** Provider-reported confidence in [0,1], or null when none was reported. */
  confidence: number | null
  /** Which chunk of a split recording produced it. */
  chunk: number
}

/**
 * Response from `POST /voice/transcribe`.
 *
 * `transcript` is evidence for the operator. `agent_input` is the only text that
 * may be forwarded to the agent: it is `null` on a block, and on a redact it is
 * the *redacted* string. Sending `transcript` instead would bypass the rails.
 */
export interface VoiceTranscribeResponse {
  transcript: string
  language: string | null
  duration_seconds: number | null
  segments: VoiceSegmentRow[]
  /** Whether ANY segment carries a reported confidence (drives the honest label). */
  has_confidence: boolean
  model: string
  chunk_count: number
  /** One honest line on whether the recording was split, and why. */
  chunking: string
  cost_usd: number
  audio_seconds_billed: number
  /**
   * The full text rail stack's verdict on the transcript. Spelled out here rather
   * than reusing `@/lib/stream`'s `GuardVerdict`, which predates the additive
   * `flag` member and would silently narrow this field.
   */
  verdict: 'pass' | 'block' | 'redact' | 'flag'
  verdict_reason: string
  verdict_layer: string | null
  redactions: string[]
  controls_run: string[]
  controls_skipped: string[]
  agent_input: string | null
}

// ── Aegis Vision — POST /vision/analyse ─────────────────────────────────────

/** The ordered stages of one analysis. The order IS the security control. */
export type VisionStage =
  | 'hygiene'
  | 'injection_screen'
  | 'image_pii'
  | 'vision_model'
  | 'output_rails'

/**
 * What one control decided, or why it decided nothing.
 *
 * `not_run` and `failed_closed` are deliberately distinct: "the operator did not
 * enable the image-PII rail" and "the injection screen had no completer, so the
 * image was blocked rather than passed" are different statements about coverage.
 * A UI that renders them the same way is lying about one of them.
 */
export type VisionControlOutcome =
  | 'passed'
  | 'blocked'
  | 'redacted'
  | 'not_run'
  | 'failed_closed'

/** One control's line in the audit record. */
export interface VisionControlReport {
  stage: VisionStage
  outcome: VisionControlOutcome
  detail: string
}

/** One rectangle of personal data found burned into the pixels (source-image space). */
export interface VisionPIIRegion {
  /** Presidio entity kind, e.g. 'EMAIL_ADDRESS'. Never the recognised value. */
  entity_type: string
  left: number
  top: number
  width: number
  height: number
  score: number | null
}

/** What payload hygiene measured about the image — facts, not claims. */
export interface VisionImageFacts {
  /** Attacker-controlled and kept only so a mismatch is visible. */
  declared_mime: string
  /** Derived from magic bytes — the only one anything downstream should believe. */
  sniffed_mime: string | null
  byte_size: number | null
  width: number | null
  height: number | null
  provenance: string
}

/** Billable accounting for the analysis call. */
export interface VisionUsage {
  model: string
  prompt_tokens: number
  completion_tokens: number
  images: number
  cost_usd: number
  /** 'provider' | 'estimated' | 'unpriced' — an unpriced $0 is not a real $0. */
  cost_source: string
}

/**
 * The image-injection screen's verdict — the differentiator.
 *
 * `screened: false` means no vision model actually looked at the image, so the
 * block is a fail-closed one. Rendering that as "we looked and it was clean" is
 * the single worst thing this surface could do.
 */
export interface VisionScreenVerdict {
  injection: boolean
  contains_text: boolean
  reason: string
  screened: boolean
}

/** What the existing text output rails decided about the model's answer. */
export interface VisionOutputRailVerdict {
  verdict: string
  reason: string
  layer: string | null
  redactions: string[]
}

/** The full, itemised result of one image analysis (mirrors `aegis.vision.VisionAnalysis`). */
export interface VisionAnalysis {
  outcome: 'answered' | 'blocked'
  question: string
  /** Empty unless `outcome` is 'answered' — a blocked run carries no model text. */
  answer: string
  blocked_stage: VisionStage | null
  blocked_reason: string
  screen: VisionScreenVerdict | null
  /** Entity kinds only — never the recognised values. */
  pii_entities: string[]
  pii_regions: VisionPIIRegion[]
  image: VisionImageFacts | null
  /** One line per control, in execution order, INCLUDING the ones that did not run. */
  controls: VisionControlReport[]
  usage: VisionUsage
  output: VisionOutputRailVerdict | null
}

/** Body for `POST /vision/analyse`. */
export interface VisionAnalyseRequest {
  /** The image bytes, base64-encoded. A `data:` URL is accepted too. */
  image_base64: string
  /** Declared content type — verified against magic bytes by the backend. */
  mime_type: string
  question: string
  filename?: string | null
}

/** Response from `POST /vision/analyse`. */
export interface VisionAnalyseResponse {
  analysis: VisionAnalysis
  /** One line: which controls ran, and which did not. */
  coverage: string
}

// ─────────────────────────────────────────────────────────────────────────────
// Forecast (`GET /forecast/...`) — mirrors aegis.forecast.types
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Which kind of band `lo`/`hi` are. `conformal` bounds are calibrated on
 * out-of-sample errors from chronologically earlier windows; `parametric` bounds
 * are the fitted model's own predictive distribution and hold only as far as its
 * residual assumptions do. Never render one as the other.
 */
export type ForecastIntervalMethod = 'conformal' | 'parametric'

/** One observed point of the input history. */
export interface ForecastSeriesPoint {
  ts: string
  value: number
}

/** One forecast step: the point prediction and its interval bounds. */
export interface ForecastHorizonPoint {
  ts: string
  point: number
  lo: number
  hi: number
  step: number
}

/**
 * Accuracy and interval coverage MEASURED on chronologically held-out windows.
 *
 * `requested_coverage` is an input echoed back; `empirical_coverage` is the only
 * coverage number that is evidence. Rendering the first as though it were the
 * second is the exact overclaim this surface exists to prevent.
 */
export interface ForecastBacktest {
  windows: number
  horizon: number
  n_points: number
  smape: number
  mape: number | null
  mae: number
  requested_coverage: number
  empirical_coverage: number
  coverage_meets_request: boolean
  interval_method: ForecastIntervalMethod
}

/** One candidate model's backtest score — losers included, so selection is auditable. */
export interface ForecastCandidate {
  model: string
  smape: number
  mape: number | null
  mae: number
  empirical_coverage: number
  selected: boolean
}

/** A candidate that could not be scored, with the real reason it was dropped. */
export interface ForecastExcludedModel {
  model: string
  reason: string
}

/** A horizon-indexed forecast plus everything needed to discount it. */
export interface ForecastResult {
  series_id: string
  label: string
  unit: string | null
  /** Provenance: 'usage_ledger' | 'adapter' | … — never let a demo pass as live. */
  data_source: string
  freq: string
  season_length: number
  history_points: number
  history: ForecastSeriesPoint[]
  horizon: number
  points: ForecastHorizonPoint[]
  model: string
  selection_metric: string
  candidates: ForecastCandidate[]
  excluded_models: ForecastExcludedModel[]
  interval_method: ForecastIntervalMethod
  interval_method_detail: string
  requested_level: number
  backtest: ForecastBacktest
  model_selected_on_backtest_windows: boolean
  generated_at: string
}

/** One step of a projected budget burn-down. */
export interface ForecastBurndownPoint {
  ts: string
  step: number
  increment: number
  cumulative: number
  cumulative_lo: number
  cumulative_hi: number
  over_budget: boolean
}

/**
 * A cap, the spend against it so far, and where the forecast says it lands.
 *
 * `cumulative_bounds_are_calibrated` is always false: summed marginal conformal
 * bounds are an envelope, not a calibrated interval on a cumulative total.
 */
export interface ForecastBurndown {
  scope: 'tenant' | 'user'
  scope_id: number | null
  window: string
  limit_usd: number | null
  spent_usd: number
  projected_total_usd: number
  projected_total_lo: number
  projected_total_hi: number
  cumulative_bounds_are_calibrated: boolean
  exhaustion_ts: string | null
  exhaustion_step: number | null
  exhausted_within_horizon: boolean
  headroom_usd: number | null
  interval_method: ForecastIntervalMethod
  points: ForecastBurndownPoint[]
}

/** Why a forecast was NOT produced — a first-class outcome, not an error page. */
export interface ForecastRefusal {
  code: 'insufficient_history' | 'degenerate_series' | 'fit_failed' | 'extra_missing'
  reason: string
  have: number | null
  need: number | null
}

/** Body for every `GET /forecast/...` route: a forecast **or** a stated refusal. */
export interface ForecastResponse {
  available: boolean
  forecast: ForecastResult | null
  burndown: ForecastBurndown | null
  refusal: ForecastRefusal | null
}
