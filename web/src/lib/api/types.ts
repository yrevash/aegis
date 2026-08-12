/**
 * REST response contracts for the non-streaming endpoints.
 *
 * These are LEAN scaffold mirrors of the Vite app's frontend/src/types/api.ts
 * (and types/memory.ts, types/ops.ts): enough shape to type the client and the
 * placeholder surfaces. Full-fidelity field-by-field parity is a follow-up as
 * each surface is wired. The stream/event contract lives in `@/lib/stream`.
 */

import type { Role, GraphNode, GraphEdge, RiskLevel, ShapFeature } from '@/lib/stream'

// ── Auth ─────────────────────────────────────────────────────────────────────
export interface LoginRequest {
  username: string
  password: string
}
export interface LoginResponse {
  role: Role
  token: string
  tenant_id: number
}

// ── Graph / metrics ────────────────────────────────────────────────────────────
export interface GraphResponse {
  nodes: GraphNode[]
  edges: GraphEdge[]
}
export interface MetricsResponse {
  [key: string]: number | string | boolean | null
}

// ── ML explain ─────────────────────────────────────────────────────────────────
export interface MLExplainRequest {
  features: Record<string, number | string>
}
export interface MLExplainResponse {
  prediction: number | string
  conformal_interval: [number, number] | null
  conformal_confidence: number | null
  shap_attribution: ShapFeature[]
}

// ── Approvals ──────────────────────────────────────────────────────────────────
export type ApprovalDecision = 'approve' | 'reject'
export interface ApprovalRequest {
  approval_id: string
  decision: ApprovalDecision
}
export interface ApprovalResponse {
  approval_id: string
  status: string
  accepted: boolean
}
export interface ApprovalRow {
  id: number
  action: string
  args: Record<string, unknown>
  risk: RiskLevel
  rationale: string
  status: string
  created_at: string
  sla_deadline: string | null
}
export interface ApprovalsResponse {
  approvals: ApprovalRow[]
}
export interface ApprovalDecisionResponse {
  id: number
  status: string
  accepted: boolean
}

// ── Audit ──────────────────────────────────────────────────────────────────────
export interface AuditRow {
  id: number
  ts: string
  actor: string
  action: string
  detail: string
  trace_id: string | null
}
export interface AuditLogResponse {
  rows: AuditRow[]
}

// ── Admin: tenants / users / budgets / usage ───────────────────────────────────
export interface Tenant {
  id: number
  name: string
}
export interface TenantsResponse {
  tenants: Tenant[]
}
export interface AdminUser {
  id: number
  username: string
  role: Role
  tenant_id: number
}
export interface UsersResponse {
  users: AdminUser[]
}
export type BudgetScope = 'tenant' | 'user' | 'global'
export interface Budget {
  id: number
  scope_type: BudgetScope
  scope_id: number | null
  limit_type: string
  limit: number
  used: number
}
export interface BudgetsResponse {
  budgets: Budget[]
}
export interface CreateBudgetRequest {
  scope_type: BudgetScope
  scope_id: number | null
  limit_type: string
  limit: number
}
export interface UsageResponse {
  window: string
  total_cost_usd: number
  total_tokens: number
  by_model: Array<{ model: string; cost_usd: number; tokens: number }>
  trend: Array<{ date: string; cost_usd: number }>
}

// ── Memory (glass-box) ─────────────────────────────────────────────────────────
export interface MemoryFact {
  id: string
  subject: string
  predicate: string
  object: string
  valid: boolean
  updated_at: string
}
export interface MemoryFactsResponse {
  facts: MemoryFact[]
}
export interface MemoryProfileResponse {
  subject: string
  profile: Record<string, unknown>
}
export interface MemorySession {
  id: string
  summary: string
  started_at: string
}
export interface MemorySessionsResponse {
  sessions: MemorySession[]
}
export interface MemoryMessage {
  role: string
  content: string
  ts: string
}
export interface MemoryMessagesResponse {
  messages: MemoryMessage[]
}
export interface MemoryWrite {
  op: string
  fact: string
  ts: string
}
export interface MemoryWritesResponse {
  writes: MemoryWrite[]
}
export interface RecallDebugResponse {
  query: string
  ranked_facts: Array<{ fact: string; score: number }>
  assembled_context: string
}

// ── LLM-Ops (prompts / evals / releases) ───────────────────────────────────────
export interface OpsPromptVersion {
  version_id: string
  status: string
  created_at: string
  note: string | null
}
export interface OpsPromptsResponse {
  prompt_key: string
  versions: OpsPromptVersion[]
}
export interface OpsActivePromptResponse {
  prompt_key: string
  version_id: string
  system_prompt: string
  config: Record<string, unknown>
}
export interface OpsEvalRow {
  run_id: string
  metric: string
  score: number
  ts: string
}
export interface OpsEvalsResponse {
  evals: OpsEvalRow[]
}
export interface OpsPendingRelease {
  approval_id: string
  prompt_key: string
  version_id: string
  eval_delta: number
  risk_tier: string
}
export interface OpsPendingReleasesResponse {
  pending: OpsPendingRelease[]
}
export interface OpsDiagnoseRequest {
  prompt_key: string
  limit?: number
}
export interface OpsDiagnoseResponse {
  draft_id: string
  rationale: string
  failure_breakdown: Record<string, number>
  risk_tier: string
}
export interface OpsReleaseRequest {
  prompt_key: string
  draft_id: string
}
export interface OpsReleaseResponse {
  outcome: 'promoted' | 'staged_for_approval' | 'rejected'
  version_id: string
  eval_delta: number
}
export interface OpsRollbackResponse {
  prompt_key: string
  reverted_to: string
}
export interface OpsReleaseDecisionResponse {
  approval_id: string
  approved: boolean
  applied: boolean
}

// ── DevOps: stack + patch check ────────────────────────────────────────────────
export interface StackComponent {
  name: string
  version: string
  kind: string
}
export interface StackResponse {
  components: StackComponent[]
}
export interface PatchRow {
  name: string
  installed: string
  latest: string
  outdated: boolean
}
export interface PatchCheckResponse {
  packages: PatchRow[]
}

// ── Client: risk map + savings ─────────────────────────────────────────────────
export interface RiskCell {
  id: string
  label: string
  severity: RiskLevel
  control: string
}
export interface RiskMapResponse {
  risks: RiskCell[]
}
export interface SavingsResponse {
  baseline_usd: number
  actual_usd: number
  saved_usd: number
  saved_pct: number
}
