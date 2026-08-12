/**
 * Typed REST + SSE client for the Aegis backend — the Next.js port of
 * frontend/src/api/client.ts. Live-only in this scaffold (the mock transport +
 * boot probe is a follow-up task); every function maps to its FastAPI route.
 *
 * The streaming `POST /query` endpoint is exposed via {@link openQueryStream},
 * which hands the response body to {@link readSSEStream} from `./sse`.
 */

import type { Role } from '@/lib/stream'
import { API_BASE } from './config'
import type {
  AdminUser,
  ApprovalDecision,
  ApprovalDecisionResponse,
  ApprovalRequest,
  ApprovalResponse,
  ApprovalsResponse,
  AuditLogResponse,
  Budget,
  BudgetScope,
  BudgetsResponse,
  CreateBudgetRequest,
  GraphResponse,
  LoginRequest,
  LoginResponse,
  MemoryFactsResponse,
  MemoryMessagesResponse,
  MemoryProfileResponse,
  MemorySessionsResponse,
  MemoryWritesResponse,
  MetricsResponse,
  MLExplainRequest,
  MLExplainResponse,
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
  PatchCheckResponse,
  RecallDebugResponse,
  RiskMapResponse,
  SavingsResponse,
  StackResponse,
  TenantsResponse,
  UsageResponse,
  UsersResponse,
} from './types'

async function request<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    throw new Error(`${init.method ?? 'GET'} ${path} failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

// ── Streaming: POST /query (SSE) ────────────────────────────────────────────────

/**
 * Open the SSE stream for a query run. Returns the raw `fetch` Response; the
 * caller pipes `res.body` into {@link readSSEStream}. Uses a `fetch` reader (not
 * `EventSource`) because the request carries a JSON body.
 */
export async function openQueryStream(
  body: { query: string; persona?: string },
  token: string | null,
  signal?: AbortSignal,
): Promise<Response> {
  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'text/event-stream' })
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(`${API_BASE}/query`, {
    method: 'POST',
    headers,
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok || !res.body) {
    throw new Error(`POST /query failed: ${res.status} ${res.statusText}`)
  }
  return res
}

// ── Auth ─────────────────────────────────────────────────────────────────────
export async function login(body: LoginRequest): Promise<LoginResponse> {
  return request<LoginResponse>('/auth/login', { method: 'POST', body: JSON.stringify(body) }, null)
}

// ── Graph / metrics ────────────────────────────────────────────────────────────
export async function getGraph(token: string | null): Promise<GraphResponse> {
  return request<GraphResponse>('/graph', { method: 'GET' }, token)
}
export async function getMetrics(token: string | null): Promise<MetricsResponse> {
  return request<MetricsResponse>('/metrics', { method: 'GET' }, token)
}

// ── Audit ──────────────────────────────────────────────────────────────────────
export async function getAudit(token: string | null, limit = 50): Promise<AuditLogResponse> {
  return request<AuditLogResponse>(`/audit?limit=${encodeURIComponent(limit)}`, { method: 'GET' }, token)
}

// ── ML explain ─────────────────────────────────────────────────────────────────
export async function mlExplain(body: MLExplainRequest, token: string | null): Promise<MLExplainResponse> {
  return request<MLExplainResponse>('/ml/explain', { method: 'POST', body: JSON.stringify(body) }, token)
}

// ── Approvals (live socket + durable inbox) ────────────────────────────────────
export async function postApproval(body: ApprovalRequest, token: string | null): Promise<ApprovalResponse> {
  return request<ApprovalResponse>('/approval', { method: 'POST', body: JSON.stringify(body) }, token)
}
export async function getApprovals(
  token: string | null,
  opts: { status?: string; limit?: number } = {},
): Promise<ApprovalsResponse> {
  const status = opts.status ?? 'pending'
  const limit = opts.limit ?? 50
  return request<ApprovalsResponse>(
    `/approvals?status=${encodeURIComponent(status)}&limit=${encodeURIComponent(limit)}`,
    { method: 'GET' },
    token,
  )
}
export async function postApprovalDecision(
  id: number,
  decision: ApprovalDecision,
  token: string | null,
): Promise<ApprovalDecisionResponse> {
  return request<ApprovalDecisionResponse>(
    `/approvals/${encodeURIComponent(id)}/decision`,
    { method: 'POST', body: JSON.stringify({ decision }) },
    token,
  )
}

// ── Admin: tenants / users / budgets / usage ───────────────────────────────────
export async function getTenants(token: string | null): Promise<TenantsResponse> {
  return request<TenantsResponse>('/admin/tenants', { method: 'GET' }, token)
}
export async function getUsers(token: string | null, tenantId?: number | null): Promise<UsersResponse> {
  const q = tenantId != null ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  return request<UsersResponse>(`/admin/users${q}`, { method: 'GET' }, token)
}
export async function getBudgets(
  token: string | null,
  scopeType?: BudgetScope | null,
  scopeId?: number | null,
): Promise<BudgetsResponse> {
  const params = new URLSearchParams()
  if (scopeType != null) params.set('scope_type', scopeType)
  if (scopeId != null) params.set('scope_id', String(scopeId))
  const q = params.toString()
  return request<BudgetsResponse>(`/admin/budgets${q ? `?${q}` : ''}`, { method: 'GET' }, token)
}
export async function createBudget(body: CreateBudgetRequest, token: string | null): Promise<Budget> {
  return request<Budget>('/admin/budgets', { method: 'POST', body: JSON.stringify(body) }, token)
}
export async function getUsage(
  token: string | null,
  opts: { tenantId?: number | null; window?: string } = {},
): Promise<UsageResponse> {
  const params = new URLSearchParams()
  if (opts.tenantId != null) params.set('tenant_id', String(opts.tenantId))
  if (opts.window != null) params.set('window', opts.window)
  const q = params.toString()
  return request<UsageResponse>(`/admin/usage${q ? `?${q}` : ''}`, { method: 'GET' }, token)
}
export async function assignUserRole(userId: number, role: Role, token: string | null): Promise<AdminUser> {
  return request<AdminUser>(
    `/admin/users/${encodeURIComponent(userId)}/role`,
    { method: 'POST', body: JSON.stringify({ role }) },
    token,
  )
}

// ── Memory (glass-box) ─────────────────────────────────────────────────────────
export async function getMemoryFacts(
  token: string | null,
  subject: string,
  includeInvalid = false,
): Promise<MemoryFactsResponse> {
  const params = new URLSearchParams({ subject })
  if (includeInvalid) params.set('include_invalid', 'true')
  return request<MemoryFactsResponse>(`/memory/facts?${params.toString()}`, { method: 'GET' }, token)
}
export async function getMemoryProfile(token: string | null, subject: string): Promise<MemoryProfileResponse> {
  return request<MemoryProfileResponse>(
    `/memory/profile?subject=${encodeURIComponent(subject)}`,
    { method: 'GET' },
    token,
  )
}
export async function getMemorySessions(token: string | null, subject: string): Promise<MemorySessionsResponse> {
  return request<MemorySessionsResponse>(
    `/memory/sessions?subject=${encodeURIComponent(subject)}`,
    { method: 'GET' },
    token,
  )
}
export async function getMemorySessionMessages(
  token: string | null,
  sessionId: string,
): Promise<MemoryMessagesResponse> {
  return request<MemoryMessagesResponse>(
    `/memory/sessions/${encodeURIComponent(sessionId)}/messages`,
    { method: 'GET' },
    token,
  )
}
export async function getMemoryWrites(token: string | null, subject: string): Promise<MemoryWritesResponse> {
  return request<MemoryWritesResponse>(
    `/memory/writes?subject=${encodeURIComponent(subject)}`,
    { method: 'GET' },
    token,
  )
}
export async function getRecallDebug(
  token: string | null,
  subject: string,
  query: string,
): Promise<RecallDebugResponse> {
  const params = new URLSearchParams({ subject, query })
  return request<RecallDebugResponse>(`/memory/recall_debug?${params.toString()}`, { method: 'GET' }, token)
}

// ── LLM-Ops (prompts / evals / releases) ───────────────────────────────────────
export async function getOpsPrompts(token: string | null, promptKey: string): Promise<OpsPromptsResponse> {
  return request<OpsPromptsResponse>(
    `/ops/prompts?prompt_key=${encodeURIComponent(promptKey)}`,
    { method: 'GET' },
    token,
  )
}
export async function getOpsActivePrompt(
  token: string | null,
  promptKey: string,
): Promise<OpsActivePromptResponse> {
  return request<OpsActivePromptResponse>(
    `/ops/prompts/active?prompt_key=${encodeURIComponent(promptKey)}`,
    { method: 'GET' },
    token,
  )
}
export async function getOpsEvals(
  token: string | null,
  opts: { promptKey?: string | null; runId?: string | null; limit?: number } = {},
): Promise<OpsEvalsResponse> {
  const limit = opts.limit ?? 200
  const params = new URLSearchParams()
  if (opts.promptKey != null) params.set('prompt_key', opts.promptKey)
  if (opts.runId != null) params.set('run_id', opts.runId)
  params.set('limit', String(limit))
  return request<OpsEvalsResponse>(`/ops/evals?${params.toString()}`, { method: 'GET' }, token)
}
export async function getOpsPendingReleases(
  token: string | null,
  limit = 50,
): Promise<OpsPendingReleasesResponse> {
  return request<OpsPendingReleasesResponse>(
    `/ops/releases/pending?limit=${encodeURIComponent(limit)}`,
    { method: 'GET' },
    token,
  )
}
export async function postOpsDiagnose(
  token: string | null,
  body: OpsDiagnoseRequest,
): Promise<OpsDiagnoseResponse> {
  return request<OpsDiagnoseResponse>('/ops/diagnose', { method: 'POST', body: JSON.stringify(body) }, token)
}
export async function postOpsRelease(token: string | null, body: OpsReleaseRequest): Promise<OpsReleaseResponse> {
  return request<OpsReleaseResponse>('/ops/release', { method: 'POST', body: JSON.stringify(body) }, token)
}
export async function postOpsRollback(token: string | null, promptKey: string): Promise<OpsRollbackResponse> {
  return request<OpsRollbackResponse>(
    '/ops/rollback',
    { method: 'POST', body: JSON.stringify({ prompt_key: promptKey }) },
    token,
  )
}
export async function postOpsReleaseDecision(
  token: string | null,
  approvalId: string,
  approved: boolean,
): Promise<OpsReleaseDecisionResponse> {
  return request<OpsReleaseDecisionResponse>(
    `/ops/releases/${encodeURIComponent(approvalId)}/decide`,
    { method: 'POST', body: JSON.stringify({ approved }) },
    token,
  )
}

// ── DevOps: stack + patch check ────────────────────────────────────────────────
export async function getStack(token: string | null = null): Promise<StackResponse> {
  return request<StackResponse>('/stack', { method: 'GET' }, token)
}
export async function checkPatches(
  packages?: string[],
  token: string | null = null,
): Promise<PatchCheckResponse> {
  return request<PatchCheckResponse>(
    '/stack/patch-check',
    { method: 'POST', body: JSON.stringify({ packages: packages ?? null }) },
    token,
  )
}

// ── Client: risk map + savings ─────────────────────────────────────────────────
export async function getRiskMap(token: string | null = null): Promise<RiskMapResponse> {
  return request<RiskMapResponse>('/risk-map', { method: 'GET' }, token)
}
export async function getSavings(token: string | null = null): Promise<SavingsResponse> {
  return request<SavingsResponse>('/savings', { method: 'GET' }, token)
}
