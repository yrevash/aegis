/**
 * In-browser fixtures backing mock mode.
 *
 * These stand in for `GET /graph`, `GET /metrics` and `POST /ml/explain` so the
 * console is fully demoable with no backend. The graph here is the substrate
 * the mock run animates over — the scenario's `retrieval` events reference these
 * node ids, so touched nodes light up in place.
 */

import type {
  AdminUser,
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
  PatchCheckResponse,
  RiskMapResponse,
  SavingsResponse,
  StackResponse,
  TenantsResponse,
  UsageResponse,
  UsersResponse,
} from '@/lib/api/types'
import type { GraphEdge, GraphNode, Role } from '@/lib/stream'

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
    // Offline demo figures. total_calls climbs monotonically so the derived
    // per-interval query-volume bars read as real deltas between polls; p95 and
    // actions_approved are steady illustrative values (the offline banner marks
    // the whole surface as mock — honest without a per-tile "sample" badge).
    total_calls: 1240 + baselineTick,
    actions_approved: 41,
    p95_latency_ms: 1900 + Math.round(wobble * 400),
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

// ── DevOps: tech stack + patch check ─────────────────────────────────────────

/**
 * Mock `GET /stack`. The real Aegis stack (runtime → backend → frontend → infra)
 * with plausible pinned versions. Marked clearly as a sample inventory: against
 * a live backend the versions come from the actual lockfiles / runtime.
 */
export function mockStack(): StackResponse {
  return {
    generated_at: new Date().toISOString(),
    components: [
      { name: 'Python', category: 'runtime', package: 'python', version: '3.12.4', aegis_module: null },
      { name: 'Node.js', category: 'runtime', package: 'node', version: '20.14.0', aegis_module: null },
      { name: 'FastAPI', category: 'backend', package: 'fastapi', version: '0.115.0', aegis_module: 'Aegis API' },
      { name: 'Uvicorn', category: 'backend', package: 'uvicorn', version: '0.30.6', aegis_module: 'Aegis API' },
      { name: 'LangGraph', category: 'backend', package: 'langgraph', version: '0.2.28', aegis_module: 'Aegis Router' },
      { name: 'Pydantic', category: 'backend', package: 'pydantic', version: '2.9.2', aegis_module: 'Aegis API' },
      { name: 'SQLAlchemy', category: 'backend', package: 'sqlalchemy', version: '2.0.35', aegis_module: 'Aegis Memory' },
      { name: 'OpenAI SDK', category: 'backend', package: 'openai', version: '1.51.0', aegis_module: 'Aegis Router' },
      { name: 'NeMo Guardrails', category: 'backend', package: 'nemoguardrails', version: '0.10.1', aegis_module: 'Aegis Guardrails' },
      { name: 'scikit-learn', category: 'backend', package: 'scikit-learn', version: '1.5.2', aegis_module: 'Aegis ML' },
      { name: 'MAPIE (conformal)', category: 'backend', package: 'mapie', version: '0.9.1', aegis_module: 'Aegis ML' },
      { name: 'SHAP', category: 'backend', package: 'shap', version: '0.46.0', aegis_module: 'Aegis ML' },
      { name: 'OpenTelemetry SDK', category: 'backend', package: 'opentelemetry-sdk', version: '1.27.0', aegis_module: 'Aegis Trace' },
      { name: 'React', category: 'frontend', package: 'react', version: '19.1.0', aegis_module: 'Console' },
      { name: 'Vite', category: 'frontend', package: 'vite', version: '6.0.1', aegis_module: 'Console' },
      { name: 'TypeScript', category: 'frontend', package: 'typescript', version: '5.6.3', aegis_module: 'Console' },
      { name: 'Recharts', category: 'frontend', package: 'recharts', version: '2.13.0', aegis_module: 'Console' },
      { name: 'Tailwind CSS', category: 'frontend', package: 'tailwindcss', version: '4.0.0', aegis_module: 'Console' },
      { name: 'PostgreSQL', category: 'infra', package: 'postgres', version: '16.4', aegis_module: 'Aegis Memory' },
      { name: 'pgvector', category: 'infra', package: 'pgvector', version: '0.7.4', aegis_module: 'Aegis Memory' },
      { name: 'Redis', category: 'infra', package: 'redis', version: '7.4.0', aegis_module: 'Aegis Cache' },
      { name: 'Docker', category: 'infra', package: 'docker', version: '27.1.1', aegis_module: null },
    ],
  }
}

/**
 * Mock `POST /stack/patch-check`. Compares installed vs latest for a subset of
 * the stack. `online: false` here is honest — the mock cannot reach a registry,
 * so the "latest" figures are illustrative samples, not a live lookup.
 */
export function mockPatchCheck(packages?: string[]): PatchCheckResponse {
  const all: PatchCheckResponse['results'] = [
    { name: 'fastapi', installed: '0.115.0', latest: '0.115.0', status: 'current' },
    { name: 'langgraph', installed: '0.2.28', latest: '0.2.44', status: 'outdated', note: 'minor bump — orchestration fixes' },
    { name: 'pydantic', installed: '2.9.2', latest: '2.9.2', status: 'current' },
    { name: 'openai', installed: '1.51.0', latest: '1.54.3', status: 'outdated', note: 'new model ids + streaming fixes' },
    { name: 'nemoguardrails', installed: '0.10.1', latest: '0.10.1', status: 'current' },
    { name: 'react', installed: '19.1.0', latest: '19.1.0', status: 'current' },
    { name: 'vite', installed: '6.0.1', latest: '6.0.7', status: 'outdated', note: 'patch — security + HMR' },
    { name: 'pgvector', installed: '0.7.4', latest: null, status: 'unknown', note: 'registry unreachable in offline demo' },
  ]
  const results = packages && packages.length > 0 ? all.filter((r) => packages.includes(r.name)) : all
  return {
    checked_at: new Date().toISOString(),
    online: false,
    note: 'Sample check — offline demo cannot reach the package registry; against a live backend these compare real installed pins to the registry.',
    results,
  }
}

// ── Client: risk map + savings ───────────────────────────────────────────────

/**
 * Mock `GET /risk-map`. Six OWASP-Agentic-style agent risks placed on a 1..5
 * likelihood × impact grid, each mapped to the concrete Aegis control that
 * mitigates it and a residual band after that control.
 */
export function mockRiskMap(): RiskMapResponse {
  return {
    generated_at: new Date().toISOString(),
    note: 'Sample assurance map — OWASP-Agentic-aligned. Bands are illustrative; a live backend derives residuals from real guardrail + audit telemetry.',
    scale: { likelihood: [1, 2, 3, 4, 5], impact: [1, 2, 3, 4, 5] },
    risks: [
      {
        id: 'AA-01',
        title: 'Excessive agency',
        category: 'Autonomy',
        likelihood: 3,
        impact: 5,
        mitigation: 'Human-in-the-loop gate on high-risk tools; conformal deferral routes uncertain actions to an approver.',
        control_ref: 'Aegis Tools · approval gate',
        residual: 'low',
      },
      {
        id: 'AA-02',
        title: 'Tool misuse / unsafe invocation',
        category: 'Tools',
        likelihood: 3,
        impact: 4,
        mitigation: 'Allowlisted tools with typed args; non-allowlisted calls are denied and audited.',
        control_ref: 'Aegis Tools · allowlist',
        residual: 'low',
      },
      {
        id: 'AA-03',
        title: 'Prompt injection',
        category: 'Input integrity',
        likelihood: 4,
        impact: 4,
        mitigation: 'Input rail scans and neutralises injection patterns before the planner sees untrusted context.',
        control_ref: 'Aegis Guardrails · input rail',
        residual: 'medium',
      },
      {
        id: 'AA-04',
        title: 'Sensitive-information disclosure',
        category: 'Output integrity',
        likelihood: 3,
        impact: 5,
        mitigation: 'Output rail redacts PII (kind-only logging); RBAC scopes retrieval so answers never exceed the caller.',
        control_ref: 'Aegis Guardrails · output rail + RBAC',
        residual: 'low',
      },
      {
        id: 'AA-05',
        title: 'Unbounded consumption / cost runaway',
        category: 'Governance',
        likelihood: 3,
        impact: 3,
        mitigation: 'Per-tenant and per-user budgets at the model chokepoint degrade gracefully to "budget exceeded".',
        control_ref: 'Aegis Governance · budgets',
        residual: 'low',
      },
      {
        id: 'AA-06',
        title: 'Hallucination / ungrounded answer',
        category: 'Reliability',
        likelihood: 4,
        impact: 3,
        mitigation: 'Retrieval-grounded generation with provenance; conformal abstention when confidence is degenerate.',
        control_ref: 'Aegis ML · conformal + provenance',
        residual: 'medium',
      },
    ],
  }
}

/**
 * Mock `GET /savings`. Baseline (everything on the frontier model) vs actual,
 * with an honest breakdown of what drove the delta. Marked sample: a live
 * backend tallies these from real routing + cache telemetry.
 */
export function mockSavings(): SavingsResponse {
  const baseline = 12_480
  const actual = 3_910
  const saved = baseline - actual
  return {
    generated_at: new Date().toISOString(),
    baseline_cost_usd: baseline,
    actual_cost_usd: actual,
    saved_usd: saved,
    saved_pct: saved / baseline,
    note: 'Sample 30-day roll-up — figures illustrate the routing + caching win; a live backend computes them from real usage.',
    breakdown: [
      { source: 'Small-model routing', saved_usd: 6_120, explanation: 'Low-risk turns handled by an 8B/mini model instead of the frontier model.' },
      { source: 'Semantic cache hits', saved_usd: 1_740, explanation: 'Near-duplicate queries served from cache, skipping generation entirely.' },
      { source: 'Prompt / context trimming', saved_usd: 710, explanation: 'Retrieval scoping and prompt compaction cut prompt tokens per run.' },
    ],
  }
}

// ── Admin: role assignment ───────────────────────────────────────────────────

/** Portal-role → the internal RBAC role label the admin store uses. */
const ROLE_LABELS: Record<Role, string> = {
  admin: 'platform_admin',
  ai_team: 'member',
  devops: 'member',
  client: 'member',
}

/**
 * Mock `POST /admin/users/{id}/role`. Echoes the reassigned user so the Roles &
 * Access surface can reflect the change optimistically without a backend.
 */
export function mockAssignRole(userId: number, role: Role): AdminUser {
  const existing = USER_SEED.find((u) => u.id === userId)
  return {
    id: userId,
    username: existing?.username ?? `user.${userId}`,
    email: existing?.email ?? null,
    role: ROLE_LABELS[role],
    tenant_id: existing?.tenant_id ?? null,
    is_active: existing?.is_active ?? true,
  }
}

