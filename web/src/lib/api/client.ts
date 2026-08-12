/**
 * Typed REST client for the non-streaming endpoints.
 *
 * In mock mode every call resolves from in-browser fixtures so the console runs
 * with no backend; otherwise each maps to its FastAPI route. Whether mock or
 * live is decided by the boot probe (`api/mode.ts` → {@link isMock}), so a
 * failed probe transparently serves the labelled offline demo.
 */

import {
  mockApprovals,
  mockAssignRole,
  mockAudit,
  mockBudgets,
  mockCreateBudget,
  mockGraph,
  mockMetrics,
  mockMlExplain,
  mockPatchCheck,
  mockRiskMap,
  mockSavings,
  mockStack,
  mockTenants,
  mockUsage,
  mockUsers,
} from '@/mock/fixtures'
import type {
  AdminUser,
  ApprovalDecisionResponse,
  ApprovalRequest,
  ApprovalResponse,
  ApprovalsResponse,
  AuditLogResponse,
  Budget,
  BudgetsResponse,
  BudgetScope,
  CreateBudgetRequest,
  GraphResponse,
  LoginRequest,
  LoginResponse,
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
import type { ApprovalDecision } from '@/lib/api/types'
import type { Role } from '@/lib/stream'
import type {
  MemoryFactsResponse,
  MemoryMessagesResponse,
  MemoryProfileResponse,
  MemorySessionsResponse,
  MemoryWritesResponse,
  RecallDebugResponse,
} from '@/lib/api/memory'
import type {
  OpsActivePromptResponse,
  OpsDiagnoseRequest,
  OpsDiagnoseResponse,
  OpsEvalsResponse,
  OpsPendingReleasesResponse,
  OpsPromptsResponse,
  OpsReleaseDecisionResponse,
  OpsReleaseRequest,
  OpsReleaseResponse,
  OpsRollbackResponse,
} from '@/lib/api/ops'
import {
  mockMemoryFacts,
  mockMemoryMessages,
  mockMemoryProfile,
  mockMemorySessions,
  mockMemoryWrites,
  mockOpsActivePrompt,
  mockOpsDiagnose,
  mockOpsEvals,
  mockOpsPendingReleases,
  mockOpsPrompts,
  mockOpsRelease,
  mockOpsReleaseDecision,
  mockOpsRollback,
  mockRecallDebug,
} from '@/mock/memoryOps'
import type {
  EvalsReportResponse,
  GatewayOptimizationResponse,
  GovernanceDashboardResponse,
  HarnessConfigResponse,
  LatencyResponse,
  ModelCardResponse,
  OpsParamsResponse,
  RedteamReportResponse,
  SecurityPostureResponse,
} from '@/lib/api/platform'
import {
  mockEvalsReport,
  mockGatewayOptimization,
  mockGovernanceDashboard,
  mockHarnessConfig,
  mockLatency,
  mockModelCard,
  mockOpsParams,
  mockRedteamRun,
  mockSecurityPosture,
} from '@/mock/platform'

import { API_BASE } from './config'
import { isMock } from './mode'

async function request<T>(
  path: string,
  init: RequestInit,
  token: string | null,
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    throw new Error(`${init.method ?? 'GET'} ${path} failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

/** Map a username to one of the four portal roles (mock-mode derivation). */
function roleFromUsername(username: string): Role {
  const u = username.toLowerCase()
  if (/admin/.test(u)) return 'admin'
  if (/ai[\s_-]?team|aiteam|^ai$|\bai\b/.test(u)) return 'ai_team'
  if (/devops|sre|ops/.test(u)) return 'devops'
  return 'client'
}

/**
 * Authenticate and receive a JWT + role + tenant. Mock mode derives the role
 * from the username (`admin`→admin, `ai`/`aiteam`→ai_team, `devops`→devops,
 * anything else→client) so all four portals are reachable without a backend.
 */
export async function login(body: LoginRequest): Promise<LoginResponse> {
  if (isMock()) {
    const role = roleFromUsername(body.username)
    const tenantByRole: Record<Role, number> = { admin: 1, ai_team: 1, devops: 1, client: 2 }
    return { role, token: `mock.${role}.${Date.now()}`, tenant_id: tenantByRole[role] }
  }
  return request<LoginResponse>('/auth/login', { method: 'POST', body: JSON.stringify(body) }, null)
}

/** Fetch the current context graph for the viz. */
export async function getGraph(token: string | null): Promise<GraphResponse> {
  if (isMock()) return mockGraph()
  return request<GraphResponse>('/graph', { method: 'GET' }, token)
}

/** Fetch live efficiency figures for the dashboard. */
export async function getMetrics(token: string | null): Promise<MetricsResponse> {
  if (isMock()) return mockMetrics()
  return request<MetricsResponse>('/metrics', { method: 'GET' }, token)
}

/**
 * Fetch the most recent audit-trail rows (admin-only, newest first). The bearer
 * token is required; the backend enforces the admin role.
 */
export async function getAudit(
  token: string | null,
  limit = 50,
): Promise<AuditLogResponse> {
  if (isMock()) return mockAudit(limit)
  return request<AuditLogResponse>(
    `/audit?limit=${encodeURIComponent(limit)}`,
    { method: 'GET' },
    token,
  )
}

/** Request a SHAP + conformal explanation for a set of features. */
export async function mlExplain(
  body: MLExplainRequest,
  token: string | null,
): Promise<MLExplainResponse> {
  if (isMock()) return mockMlExplain(body)
  return request<MLExplainResponse>(
    '/ml/explain',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/**
 * Resolve a paused action live on the SSE socket (approve/reject). In mock mode
 * the decision is routed back through the mock transport, so this is only used
 * against a live backend; the mock transport exposes
 * {@link RunController.resolveApproval}.
 */
export async function postApproval(
  body: ApprovalRequest,
  token: string | null,
): Promise<ApprovalResponse> {
  return request<ApprovalResponse>(
    '/approval',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

// ── Approvals inbox (durable, async) ────────────────────────────────────────

/** List pending approvals for the inbox (admin). */
export async function getApprovals(
  token: string | null,
  opts: { status?: string; limit?: number } = {},
): Promise<ApprovalsResponse> {
  const status = opts.status ?? 'pending'
  const limit = opts.limit ?? 50
  if (isMock()) return mockApprovals(status, limit)
  return request<ApprovalsResponse>(
    `/approvals?status=${encodeURIComponent(status)}&limit=${encodeURIComponent(limit)}`,
    { method: 'GET' },
    token,
  )
}

/** Approve or reject a durable approval row from the inbox. */
export async function postApprovalDecision(
  id: number,
  decision: ApprovalDecision,
  token: string | null,
): Promise<ApprovalDecisionResponse> {
  if (isMock()) {
    return { id, status: decision === 'approve' ? 'approved' : 'rejected', accepted: true }
  }
  return request<ApprovalDecisionResponse>(
    `/approvals/${encodeURIComponent(id)}/decision`,
    { method: 'POST', body: JSON.stringify({ decision }) },
    token,
  )
}

// ── Admin: tenants, users, budgets, usage ───────────────────────────────────

/** List tenants (platform admin). */
export async function getTenants(token: string | null): Promise<TenantsResponse> {
  if (isMock()) return mockTenants()
  return request<TenantsResponse>('/admin/tenants', { method: 'GET' }, token)
}

/** List users, optionally scoped to a tenant. */
export async function getUsers(
  token: string | null,
  tenantId?: number | null,
): Promise<UsersResponse> {
  if (isMock()) return mockUsers(tenantId ?? null)
  const q = tenantId != null ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  return request<UsersResponse>(`/admin/users${q}`, { method: 'GET' }, token)
}

/** List budgets, optionally scoped to a scope type / id. */
export async function getBudgets(
  token: string | null,
  scopeType?: BudgetScope | null,
  scopeId?: number | null,
): Promise<BudgetsResponse> {
  if (isMock()) return mockBudgets(scopeType ?? null, scopeId ?? null)
  const params = new URLSearchParams()
  if (scopeType != null) params.set('scope_type', scopeType)
  if (scopeId != null) params.set('scope_id', String(scopeId))
  const q = params.toString()
  return request<BudgetsResponse>(`/admin/budgets${q ? `?${q}` : ''}`, { method: 'GET' }, token)
}

/** Create (or upsert) a budget cap. */
export async function createBudget(
  body: CreateBudgetRequest,
  token: string | null,
): Promise<Budget> {
  if (isMock()) return mockCreateBudget(body)
  return request<Budget>('/admin/budgets', { method: 'POST', body: JSON.stringify(body) }, token)
}

/** Fetch usage roll-ups (cost/tokens by model + trend) for a tenant/window. */
export async function getUsage(
  token: string | null,
  opts: { tenantId?: number | null; window?: string } = {},
): Promise<UsageResponse> {
  if (isMock()) return mockUsage(opts.tenantId ?? null, opts.window ?? 'month')
  const params = new URLSearchParams()
  if (opts.tenantId != null) params.set('tenant_id', String(opts.tenantId))
  if (opts.window != null) params.set('window', opts.window)
  const q = params.toString()
  return request<UsageResponse>(`/admin/usage${q ? `?${q}` : ''}`, { method: 'GET' }, token)
}

// ── Memory: the Harness glass-box (semantic / episodic / structured) ─────────

/** Fetch the semantic facts for a subject (optionally including invalidated). */
export async function getMemoryFacts(
  token: string | null,
  subject: string,
  includeInvalid = false,
): Promise<MemoryFactsResponse> {
  if (isMock()) return mockMemoryFacts(subject, includeInvalid)
  const params = new URLSearchParams({ subject })
  if (includeInvalid) params.set('include_invalid', 'true')
  return request<MemoryFactsResponse>(`/memory/facts?${params.toString()}`, { method: 'GET' }, token)
}

/** Fetch the structured profile ("what the agent knows about you"). */
export async function getMemoryProfile(
  token: string | null,
  subject: string,
): Promise<MemoryProfileResponse> {
  if (isMock()) return mockMemoryProfile(subject)
  return request<MemoryProfileResponse>(
    `/memory/profile?subject=${encodeURIComponent(subject)}`,
    { method: 'GET' },
    token,
  )
}

/** Fetch the episodic sessions (each with a running summary) for a subject. */
export async function getMemorySessions(
  token: string | null,
  subject: string,
): Promise<MemorySessionsResponse> {
  if (isMock()) return mockMemorySessions(subject)
  return request<MemorySessionsResponse>(
    `/memory/sessions?subject=${encodeURIComponent(subject)}`,
    { method: 'GET' },
    token,
  )
}

/** Fetch the message turns for one episodic session. */
export async function getMemorySessionMessages(
  token: string | null,
  sessionId: string,
): Promise<MemoryMessagesResponse> {
  if (isMock()) return mockMemoryMessages(sessionId)
  return request<MemoryMessagesResponse>(
    `/memory/sessions/${encodeURIComponent(sessionId)}/messages`,
    { method: 'GET' },
    token,
  )
}

/** Fetch the memory write-log changelog (ADD/UPDATE/INVALIDATE) for a subject. */
export async function getMemoryWrites(
  token: string | null,
  subject: string,
): Promise<MemoryWritesResponse> {
  if (isMock()) return mockMemoryWrites(subject)
  return request<MemoryWritesResponse>(
    `/memory/writes?subject=${encodeURIComponent(subject)}`,
    { method: 'GET' },
    token,
  )
}

/** Trace recall for a query: ranked facts/episodic + the assembled context. */
export async function getRecallDebug(
  token: string | null,
  subject: string,
  query: string,
): Promise<RecallDebugResponse> {
  if (isMock()) return mockRecallDebug(subject, query)
  const params = new URLSearchParams({ subject, query })
  return request<RecallDebugResponse>(`/memory/recall_debug?${params.toString()}`, { method: 'GET' }, token)
}

// ── LLM-Ops: the self-improvement loop (prompts / evals / releases) ──────────

/** List the version timeline for a prompt key. */
export async function getOpsPrompts(
  token: string | null,
  promptKey: string,
): Promise<OpsPromptsResponse> {
  if (isMock()) return mockOpsPrompts(promptKey)
  return request<OpsPromptsResponse>(
    `/ops/prompts?prompt_key=${encodeURIComponent(promptKey)}`,
    { method: 'GET' },
    token,
  )
}

/** Fetch the currently-active prompt (system prompt + config) for a key. */
export async function getOpsActivePrompt(
  token: string | null,
  promptKey: string,
): Promise<OpsActivePromptResponse> {
  if (isMock()) return mockOpsActivePrompt(promptKey)
  return request<OpsActivePromptResponse>(
    `/ops/prompts/active?prompt_key=${encodeURIComponent(promptKey)}`,
    { method: 'GET' },
    token,
  )
}

/** Fetch recent eval rows (per-metric scores over time). */
export async function getOpsEvals(
  token: string | null,
  opts: { promptKey?: string | null; runId?: string | null; limit?: number } = {},
): Promise<OpsEvalsResponse> {
  const limit = opts.limit ?? 200
  if (isMock()) return mockOpsEvals(opts.promptKey ?? 'payments_ops_agent', limit)
  const params = new URLSearchParams()
  if (opts.promptKey != null) params.set('prompt_key', opts.promptKey)
  if (opts.runId != null) params.set('run_id', opts.runId)
  params.set('limit', String(limit))
  return request<OpsEvalsResponse>(`/ops/evals?${params.toString()}`, { method: 'GET' }, token)
}

/** List prompt releases awaiting the human gate. */
export async function getOpsPendingReleases(
  token: string | null,
  limit = 50,
): Promise<OpsPendingReleasesResponse> {
  if (isMock()) return mockOpsPendingReleases(limit)
  return request<OpsPendingReleasesResponse>(
    `/ops/releases/pending?limit=${encodeURIComponent(limit)}`,
    { method: 'GET' },
    token,
  )
}

/** Diagnose recent eval failures and propose a draft prompt version. */
export async function postOpsDiagnose(
  token: string | null,
  body: OpsDiagnoseRequest,
): Promise<OpsDiagnoseResponse> {
  if (isMock()) return mockOpsDiagnose(body)
  return request<OpsDiagnoseResponse>(
    '/ops/diagnose',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/** Release a draft version through the tiered gate (auto-promote or stage). */
export async function postOpsRelease(
  token: string | null,
  body: OpsReleaseRequest,
): Promise<OpsReleaseResponse> {
  if (isMock()) return mockOpsRelease(body)
  return request<OpsReleaseResponse>(
    '/ops/release',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/** Roll a prompt back to its last-good active version. */
export async function postOpsRollback(
  token: string | null,
  promptKey: string,
): Promise<OpsRollbackResponse> {
  if (isMock()) return mockOpsRollback(promptKey)
  return request<OpsRollbackResponse>(
    '/ops/rollback',
    { method: 'POST', body: JSON.stringify({ prompt_key: promptKey }) },
    token,
  )
}

/** Approve or reject a staged release (the human gate decision). */
export async function postOpsReleaseDecision(
  token: string | null,
  approvalId: string,
  approved: boolean,
): Promise<OpsReleaseDecisionResponse> {
  if (isMock()) return mockOpsReleaseDecision(approvalId, approved)
  return request<OpsReleaseDecisionResponse>(
    `/ops/releases/${encodeURIComponent(approvalId)}/decide`,
    { method: 'POST', body: JSON.stringify({ approved }) },
    token,
  )
}

// ── DevOps: tech stack + patch check ─────────────────────────────────────────

/** Fetch the running software bill of materials (DevOps stack inventory). */
export async function getStack(token: string | null = null): Promise<StackResponse> {
  if (isMock()) return mockStack()
  return request<StackResponse>('/stack', { method: 'GET' }, token)
}

/**
 * Run a freshness check comparing installed vs latest for the stack (or a subset
 * when `packages` is given). POST so the (optional) package list travels in the
 * body rather than a long query string.
 */
export async function checkPatches(
  packages?: string[],
  token: string | null = null,
): Promise<PatchCheckResponse> {
  if (isMock()) return mockPatchCheck(packages)
  return request<PatchCheckResponse>(
    '/stack/patch-check',
    { method: 'POST', body: JSON.stringify({ packages: packages ?? null }) },
    token,
  )
}

// ── Client: risk map + savings ───────────────────────────────────────────────

/** Fetch the agent-risk assurance heat-map (client-facing). */
export async function getRiskMap(token: string | null = null): Promise<RiskMapResponse> {
  if (isMock()) return mockRiskMap()
  return request<RiskMapResponse>('/risk-map', { method: 'GET' }, token)
}

/** Fetch the baseline-vs-actual savings roll-up (client-facing value view). */
export async function getSavings(token: string | null = null): Promise<SavingsResponse> {
  if (isMock()) return mockSavings()
  return request<SavingsResponse>('/savings', { method: 'GET' }, token)
}

// ── Admin: role assignment ───────────────────────────────────────────────────

/** Reassign a user's portal role (admin-only). */
export async function assignUserRole(
  userId: number,
  role: Role,
  token: string | null,
): Promise<AdminUser> {
  if (isMock()) return mockAssignRole(userId, role)
  return request<AdminUser>(
    `/admin/users/${encodeURIComponent(userId)}/role`,
    { method: 'POST', body: JSON.stringify({ role }) },
    token,
  )
}

// ── Platform read-surfaces (MLOps / LLMOps / evals / token-opt / harness /
//    governance / security / latency / red-team) ─────────────────────────────

/** Fetch the live model's measured model card (MLOps; admin/ai_team). */
export async function getModelCard(token: string | null): Promise<ModelCardResponse> {
  if (isMock()) return mockModelCard()
  return request<ModelCardResponse>('/ml/model-card', { method: 'GET' }, token)
}

/** Fetch the offline regression-gate rollup (evals; admin/ai_team). */
export async function getEvalsReport(token: string | null): Promise<EvalsReportResponse> {
  if (isMock()) return mockEvalsReport()
  return request<EvalsReportResponse>('/evals/report', { method: 'GET' }, token)
}

/** Fetch the tunable LLM-Ops self-improvement knobs (LLMOps; admin/ai_team). */
export async function getOpsParams(token: string | null): Promise<OpsParamsResponse> {
  if (isMock()) return mockOpsParams()
  return request<OpsParamsResponse>('/ops/params', { method: 'GET' }, token)
}

/** Fetch the token-optimization surface (savings summary + routing config). */
export async function getGatewayOptimization(
  token: string | null,
): Promise<GatewayOptimizationResponse> {
  if (isMock()) return mockGatewayOptimization()
  return request<GatewayOptimizationResponse>('/gateway/optimization', { method: 'GET' }, token)
}

/** Fetch the agent-harness tweakable-config record (harness; admin/ai_team). */
export async function getHarnessConfig(token: string | null): Promise<HarnessConfigResponse> {
  if (isMock()) return mockHarnessConfig()
  return request<HarnessConfigResponse>('/harness/config', { method: 'GET' }, token)
}

/** Fetch the governance dashboard snapshot for a tenant scope (admin-only). */
export async function getGovernanceDashboard(
  token: string | null,
  opts: { tenantId?: number | null; window?: string } = {},
): Promise<GovernanceDashboardResponse> {
  if (isMock()) return mockGovernanceDashboard(opts.tenantId ?? null, opts.window ?? 'day')
  const params = new URLSearchParams()
  if (opts.tenantId != null) params.set('tenant_id', String(opts.tenantId))
  if (opts.window != null) params.set('window', opts.window)
  const q = params.toString()
  return request<GovernanceDashboardResponse>(
    `/governance/dashboard${q ? `?${q}` : ''}`,
    { method: 'GET' },
    token,
  )
}

/** Fetch the live threat → control security posture (security; admin/devops). */
export async function getSecurityPosture(token: string | null): Promise<SecurityPostureResponse> {
  if (isMock()) return mockSecurityPosture()
  return request<SecurityPostureResponse>('/security/posture', { method: 'GET' }, token)
}

/** Fetch per-node + per-run latency percentiles (latency; admin/devops). */
export async function getLatency(token: string | null): Promise<LatencyResponse> {
  if (isMock()) return mockLatency()
  return request<LatencyResponse>('/latency', { method: 'GET' }, token)
}

/** Run the offline red-team attack battery and return the verdicts (admin/devops). */
export async function runRedteam(token: string | null): Promise<RedteamReportResponse> {
  if (isMock()) return mockRedteamRun()
  return request<RedteamReportResponse>('/redteam/run', { method: 'POST' }, token)
}
