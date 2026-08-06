/**
 * In-browser fixtures backing mock mode.
 *
 * These stand in for `GET /graph`, `GET /metrics` and `POST /ml/explain` so the
 * console is fully demoable with no backend. The graph here is the substrate
 * the mock run animates over — the scenario's `retrieval` events reference these
 * node ids, so touched nodes light up in place.
 */

import type {
  ApprovalRow,
  ApprovalsResponse,
  AuditLogResponse,
  Budget,
  BudgetScope,
  BudgetsResponse,
  CreateBudgetRequest,
  GraphResponse,
  MLExplainRequest,
  MLExplainResponse,
  MetricsResponse,
  TenantsResponse,
  UsageResponse,
  UsersResponse,
} from '@/types/api'
import type { GraphEdge, GraphNode } from '@/types/stream'

/** The base context graph (domain-neutral operations scenario). */
export const BASE_NODES: GraphNode[] = [
  { id: 'case-4821', label: 'Case #4821', kind: 'Case' },
  { id: 'acct-771', label: 'Account A-771', kind: 'Account' },
  { id: 'cust-mreed', label: 'M. Reed', kind: 'Customer' },
  { id: 'charge-dup', label: 'Duplicate charge $4,200', kind: 'Transaction' },
  { id: 'order-90142', label: 'Order 90142', kind: 'Order' },
  { id: 'policy-refund', label: 'Refund Policy v3', kind: 'Policy' },
  { id: 'sla-premium', label: 'Premium SLA', kind: 'SLA' },
  { id: 'proc-stripe', label: 'Payment Processor', kind: 'System' },
  { id: 'kb-refund-flow', label: 'KB: Refund Flow', kind: 'Article' },
  { id: 'kb-duplicate', label: 'KB: Duplicate Charges', kind: 'Article' },
  { id: 'tier-premium', label: 'Tier: Premium', kind: 'Segment' },
  { id: 'agent-payments', label: 'Payments Agent', kind: 'Tool' },
]

/** The base context graph edges. */
export const BASE_EDGES: GraphEdge[] = [
  { source: 'case-4821', target: 'acct-771', relation: 'raised_on' },
  { source: 'acct-771', target: 'cust-mreed', relation: 'owned_by' },
  { source: 'acct-771', target: 'tier-premium', relation: 'in_segment' },
  { source: 'case-4821', target: 'charge-dup', relation: 'concerns' },
  { source: 'charge-dup', target: 'proc-stripe', relation: 'settled_via' },
  { source: 'charge-dup', target: 'order-90142', relation: 'billed_for' },
  { source: 'case-4821', target: 'policy-refund', relation: 'governed_by' },
  { source: 'tier-premium', target: 'sla-premium', relation: 'entitled_to' },
  { source: 'policy-refund', target: 'kb-refund-flow', relation: 'documented_in' },
  { source: 'charge-dup', target: 'kb-duplicate', relation: 'matches' },
  { source: 'policy-refund', target: 'agent-payments', relation: 'executed_by' },
]

/** Mock `GET /graph`. */
export function mockGraph(): GraphResponse {
  return { nodes: BASE_NODES, edges: BASE_EDGES }
}

let baselineTick = 0

/**
 * Mock `GET /metrics`. Small deterministic drift each call keeps the dashboard
 * feeling live without depending on a backend.
 */
export function mockMetrics(): MetricsResponse {
  baselineTick += 1
  const wobble = Math.sin(baselineTick / 3) * 0.01
  const costPer1k = 3.85 - wobble * 4
  // What the same 1k queries would cost if every one ran on the frontier model
  // (no caching, no small-model routing) — the baseline the savings are against.
  const baselineCostPer1k = 12.4 - wobble * 4
  return {
    cache_hit_rate: 0.62 + wobble,
    small_model_share: 0.71 - wobble,
    cost_per_1k_queries_usd: costPer1k,
    quality_score: 0.94 + wobble / 2,
    routing: {
      planner: 'gpt-4o-mini',
      generation: 'gpt-4o',
      reranker: 'rerank-v3',
      embedder: 'text-embedding-3-large',
    },
    baseline_cost_usd: baselineCostPer1k,
    cost_saved_usd: baselineCostPer1k - costPer1k,
  }
}

/**
 * Mock `GET /audit`. Rows mirror the backend `AuditLog` shape (newest first) so
 * the admin audit view is fully exercisable without a backend. Timestamps are
 * anchored to "now" so the trail reads as recent in the demo.
 */
interface AuditSeed {
  agoMs: number
  action: string
  actor: string | null
  model: string | null
  trace_id: string | null
  approved_by: string | null
}

const AUDIT_SEED: AuditSeed[] = [
  { agoMs: 8_000, action: 'route.small_model gpt-4o-mini (saved $0.0039 vs frontier)', actor: 'router', model: 'gpt-4o-mini', trace_id: 'trace-mock-ops', approved_by: null },
  { agoMs: 15_000, action: 'update_request_status A-771 resolved', actor: 'ops-analyst', model: 'genailab-maas-llama-3.3-70b', trace_id: 'trace-9f2a', approved_by: 'risk-reviewer' },
  { agoMs: 214_000, action: 'assign_request INC-1190 tier-3', actor: 'ops-analyst', model: 'genailab-maas-llama-3.1-8b', trace_id: 'trace-71bd', approved_by: 'risk-reviewer' },
  { agoMs: 1_020_000, action: 'guardrail.output_blocked A-204', actor: 'ops-analyst', model: 'genailab-maas-llama-3.3-70b', trace_id: 'trace-33e1', approved_by: null },
  { agoMs: 1_980_000, action: 'retrieve_knowledge refund-policy', actor: 'client-mreed', model: 'genailab-maas-llama-3.1-8b', trace_id: 'trace-1c40', approved_by: null },
  { agoMs: 3_600_000, action: 'tool_denied issue_refund (not allowlisted)', actor: 'client-mreed', model: 'genailab-maas-llama-3.1-8b', trace_id: 'trace-0aa2', approved_by: null },
]

/**
 * Mock `GET /audit`. Rows mirror the backend `AuditLog` shape (newest first) so
 * the admin audit view is fully exercisable without a backend. Timestamps are
 * anchored to "now" so the trail reads as recent in the demo.
 */
export function mockAudit(limit = 50): AuditLogResponse {
  const now = Date.now()
  const rows = AUDIT_SEED.slice(0, limit).map((r, i) => ({
    id: AUDIT_SEED.length - i,
    ts: new Date(now - r.agoMs).toISOString(),
    action: r.action,
    actor: r.actor,
    model: r.model,
    trace_id: r.trace_id,
    approved_by: r.approved_by,
  }))
  return { rows }
}

// ── Approvals inbox (durable, async) ────────────────────────────────────────

interface ApprovalSeed {
  id: number
  run_id: string
  action: string
  args: Record<string, unknown>
  risk: ApprovalRow['risk']
  rationale: string
  persona: string | null
  /** SLA deadline relative to "now" (ms; negative ⇒ already overdue). */
  slaInMs: number | null
  createdAgoMs: number
  ml_snapshot: ApprovalRow['ml_snapshot']
}

const APPROVAL_SEED: ApprovalSeed[] = [
  {
    id: 5021,
    run_id: 'run_ops_4821',
    action: 'Issue $4,200 refund to account A-771',
    args: { account: 'A-771', amount_usd: 4200, reason: 'duplicate charge' },
    risk: 'high',
    rationale:
      'Amount exceeds the $2,000 auto-approval ceiling and the conformal interval (0.64–0.93) does not clear the 0.85 confidence gate — bounded autonomy defers to a human.',
    persona: 'operations_lead',
    slaInMs: 9 * 60_000,
    createdAgoMs: 6 * 60_000,
    ml_snapshot: {
      prediction: 0.82,
      conformal_confidence: 0.9,
      conformal_interval: [0.64, 0.93],
      band: 'defer',
    },
  },
  {
    id: 5019,
    run_id: 'run_ops_3390',
    action: 'Escalate incident INC-1190 to tier 3',
    args: { incident: 'INC-1190', tier: 3 },
    risk: 'medium',
    rationale:
      'Predicted SLA-breach risk 0.71 with a wide interval; escalation is reversible but crosses a team boundary, so it routes to the inbox.',
    persona: 'risk-reviewer',
    slaInMs: 3 * 60_000,
    createdAgoMs: 22 * 60_000,
    ml_snapshot: {
      prediction: 0.71,
      conformal_confidence: 0.9,
      conformal_interval: [0.55, 0.9],
      band: 'defer',
    },
  },
  {
    id: 5014,
    run_id: 'run_ops_2210',
    action: 'Cancel duplicate subscription on account A-655',
    args: { account: 'A-655', subscription: 'sub_88213' },
    risk: 'high',
    rationale:
      'High-risk irreversible cancellation; policy requires human sign-off regardless of model confidence.',
    persona: 'operations_lead',
    slaInMs: -4 * 60_000,
    createdAgoMs: 47 * 60_000,
    ml_snapshot: {
      prediction: 0.88,
      conformal_confidence: 0.9,
      conformal_interval: [0.8, 0.94],
      band: 'defer',
    },
  },
]

/**
 * Mock `GET /approvals`. Deadlines are anchored to "now" so the SLA countdowns
 * tick live in the offline demo (one row is intentionally overdue).
 */
export function mockApprovals(status = 'pending', limit = 50): ApprovalsResponse {
  const now = Date.now()
  const rows: ApprovalRow[] = APPROVAL_SEED.slice(0, limit).map((s) => ({
    id: s.id,
    run_id: s.run_id,
    action: s.action,
    args: s.args,
    risk: s.risk,
    rationale: s.rationale,
    status,
    persona: s.persona,
    sla_deadline: s.slaInMs != null ? new Date(now + s.slaInMs).toISOString() : null,
    created_at: new Date(now - s.createdAgoMs).toISOString(),
    ml_snapshot: s.ml_snapshot,
  }))
  return { rows }
}

// ── Admin: tenants, users, budgets, usage ───────────────────────────────────

/** Mock `GET /admin/tenants`. */
export function mockTenants(): TenantsResponse {
  const now = Date.now()
  return {
    rows: [
      { id: 1, name: 'Aegis Platform (internal)', status: 'active', created_at: new Date(now - 90 * 86_400_000).toISOString() },
      { id: 2, name: 'Northwind Financial', status: 'active', created_at: new Date(now - 41 * 86_400_000).toISOString() },
      { id: 3, name: 'Contoso Logistics', status: 'active', created_at: new Date(now - 18 * 86_400_000).toISOString() },
      { id: 4, name: 'Globex Health (trial)', status: 'suspended', created_at: new Date(now - 6 * 86_400_000).toISOString() },
    ],
  }
}

const USER_SEED: UsersResponse['rows'] = [
  { id: 1, username: 'platform.admin', email: 'admin@aegis.internal', role: 'platform_admin', tenant_id: 1, is_active: true },
  { id: 2, username: 'nw.admin', email: 'ada@northwind.example', role: 'tenant_admin', tenant_id: 2, is_active: true },
  { id: 3, username: 'nw.analyst', email: 'omar@northwind.example', role: 'member', tenant_id: 2, is_active: true },
  { id: 4, username: 'nw.reviewer', email: 'rin@northwind.example', role: 'member', tenant_id: 2, is_active: true },
  { id: 5, username: 'contoso.admin', email: 'lee@contoso.example', role: 'tenant_admin', tenant_id: 3, is_active: true },
  { id: 6, username: 'contoso.ops', email: 'mira@contoso.example', role: 'member', tenant_id: 3, is_active: false },
]

/** Mock `GET /admin/users` (optionally scoped to a tenant). */
export function mockUsers(tenantId: number | null): UsersResponse {
  const rows = tenantId == null ? USER_SEED : USER_SEED.filter((u) => u.tenant_id === tenantId)
  return { rows }
}

/** In-memory budget store so mock create/list round-trips within a session. */
const budgetStore: Budget[] = [
  { id: 1, scope_type: 'tenant', scope_id: 2, window: 'month', token_cap: 5_000_000, usd_cap: 1200, rpm: 600, tpm: 200_000 },
  { id: 2, scope_type: 'tenant', scope_id: 3, window: 'month', token_cap: 2_000_000, usd_cap: 500, rpm: 300, tpm: 120_000 },
  { id: 3, scope_type: 'user', scope_id: 3, window: 'day', token_cap: 120_000, usd_cap: 25, rpm: 60, tpm: 20_000 },
]
let nextBudgetId = 4

/** Mock `GET /admin/budgets` (optionally scoped). */
export function mockBudgets(scopeType: BudgetScope | null, scopeId: number | null): BudgetsResponse {
  const rows = budgetStore.filter(
    (b) => (scopeType == null || b.scope_type === scopeType) && (scopeId == null || b.scope_id === scopeId),
  )
  return { rows: rows.map((b) => ({ ...b })) }
}

/** Mock `POST /admin/budgets` — upserts by (scope_type, scope_id, window). */
export function mockCreateBudget(body: CreateBudgetRequest): Budget {
  const existing = budgetStore.find(
    (b) => b.scope_type === body.scope_type && b.scope_id === body.scope_id && b.window === body.window,
  )
  if (existing) {
    Object.assign(existing, body, { id: existing.id })
    return { ...existing }
  }
  const created: Budget = { ...body, id: nextBudgetId }
  nextBudgetId += 1
  budgetStore.push(created)
  return { ...created }
}

/**
 * Mock `GET /admin/usage`. A deterministic 14-day cost trend plus a by-model
 * breakdown, so the usage dashboard renders fully with no backend.
 */
export function mockUsage(_tenantId: number | null, _window: string): UsageResponse {
  const now = Date.now()
  const series = Array.from({ length: 14 }, (_v, i) => {
    const day = 13 - i
    const wobble = Math.sin((i + 1) / 2.2) * 6
    return {
      ts: new Date(now - day * 86_400_000).toISOString().slice(0, 10),
      cost_usd: Math.max(2, 34 + wobble + i * 0.8),
    }
  })
  const by_model = [
    { model: 'gpt-4o-mini', cost_usd: 128.4, tokens: 42_800_000 },
    { model: 'gpt-4o', cost_usd: 302.7, tokens: 9_600_000 },
    { model: 'text-embedding-3-large', cost_usd: 41.2, tokens: 61_000_000 },
    { model: 'rerank-v3', cost_usd: 18.9, tokens: 7_300_000 },
  ]
  const total_cost_usd = by_model.reduce((s, m) => s + m.cost_usd, 0)
  return {
    total_prompt_tokens: 96_400_000,
    total_completion_tokens: 24_300_000,
    total_cost_usd,
    by_model,
    series,
  }
}

/** The SHAP explanation the scenario surfaces (also served via `POST /ml/explain`). */
export function mockMlExplain(_req: MLExplainRequest): MLExplainResponse {
  return {
    prediction: 0.82,
    conformal_interval: [0.64, 0.93],
    conformal_confidence: 0.9,
    interval_width: 0.29,
    prediction_set_size: 2,
    shap_attribution: [
      { feature: 'duplicate_charge_confirmed', value: 1, contribution: 0.34 },
      { feature: 'account_tenure_months', value: 41, contribution: 0.18 },
      { feature: 'premium_tier', value: 1, contribution: 0.12 },
      { feature: 'prior_refunds_90d', value: 2, contribution: -0.15 },
      { feature: 'amount_usd', value: 4200, contribution: -0.09 },
      { feature: 'chargeback_risk', value: 0.22, contribution: -0.06 },
    ],
  }
}

