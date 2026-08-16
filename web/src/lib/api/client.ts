/**
 * Typed REST client for the non-streaming endpoints.
 *
 * Every call maps to a FastAPI route — there is no fixture path. A surface with
 * no backend renders the shared "backend unavailable" state (see `BackendGate`)
 * rather than substituting invented data, so a rejected promise here always means
 * a real failure worth showing.
 */

import type {
  AdminUser,
  AgentTopologyResponse,
  ApprovalRequest,
  ApprovalResponse,
  AuditLogResponse,
  Budget,
  BudgetsResponse,
  BudgetScope,
  CapabilitiesResponse,
  CreateBudgetRequest,
  ForecastResponse,
  GraphResponse,
  LoginRequest,
  LoginResponse,
  MLExplainRequest,
  MLExplainResponse,
  MetricsResponse,
  PatchCheckResponse,
  PublicMetricsResponse,
  RiskMapResponse,
  SavingsResponse,
  StackResponse,
  TenantsResponse,
  UsageResponse,
  UsersResponse,
  VisionAnalyseRequest,
  VisionAnalyseResponse,
  VoiceTranscribeResponse,
} from '@/lib/api/types'
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

import { getAuthToken } from './authToken'
import { API_BASE } from './config'

async function request<T>(
  path: string,
  init: RequestInit,
  token: string | null,
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  // Per-call token wins; otherwise fall back to the signed-in session's token so
  // RBAC-scoped backend routes authorize even where the caller passes null.
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    throw new Error(`${init.method ?? 'GET'} ${path} failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as T
}

/**
 * Authenticate and receive a JWT + role + tenant. The backend is the sole
 * authority on which portal a user lands in — the role travels in the response,
 * never derived client-side from the username.
 */
export async function login(body: LoginRequest): Promise<LoginResponse> {
  return request<LoginResponse>('/auth/login', { method: 'POST', body: JSON.stringify(body) }, null)
}

/** Fetch the current context graph for the viz. */
export async function getGraph(token: string | null): Promise<GraphResponse> {
  return request<GraphResponse>('/graph', { method: 'GET' }, token)
}

/**
 * Fetch the agent graph's real node/edge topology.
 *
 * The console's orchestration map renders from this rather than from a hardcoded
 * DAG, so the picture cannot drift from the graph that actually runs.
 */
export async function getAgentTopology(token: string | null): Promise<AgentTopologyResponse> {
  return request<AgentTopologyResponse>('/agent/topology', { method: 'GET' }, token)
}

/** Fetch live efficiency figures for the dashboard. */
export async function getMetrics(token: string | null): Promise<MetricsResponse> {
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
  return request<MLExplainResponse>(
    '/ml/explain',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/**
 * Resolve a paused action live on the SSE socket (approve/reject). Sent out of
 * band from the run stream; the backend resumes the parked run on receipt.
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

// ── Admin: tenants, users, budgets, usage ───────────────────────────────────

/** List tenants (platform admin). */
export async function getTenants(token: string | null): Promise<TenantsResponse> {
  return request<TenantsResponse>('/admin/tenants', { method: 'GET' }, token)
}

/** List users, optionally scoped to a tenant. */
export async function getUsers(
  token: string | null,
  tenantId?: number | null,
): Promise<UsersResponse> {
  const q = tenantId != null ? `?tenant_id=${encodeURIComponent(tenantId)}` : ''
  return request<UsersResponse>(`/admin/users${q}`, { method: 'GET' }, token)
}

/** List budgets, optionally scoped to a scope type / id. */
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

/** Create (or upsert) a budget cap. */
export async function createBudget(
  body: CreateBudgetRequest,
  token: string | null,
): Promise<Budget> {
  return request<Budget>('/admin/budgets', { method: 'POST', body: JSON.stringify(body) }, token)
}

/** Fetch usage roll-ups (cost/tokens by model + trend) for a tenant/window. */
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

// ── Memory: the Harness glass-box (semantic / episodic / structured) ─────────

/** Fetch the semantic facts for a subject (optionally including invalidated). */
export async function getMemoryFacts(
  token: string | null,
  subject: string,
  includeInvalid = false,
): Promise<MemoryFactsResponse> {
  const params = new URLSearchParams({ subject })
  if (includeInvalid) params.set('include_invalid', 'true')
  return request<MemoryFactsResponse>(`/memory/facts?${params.toString()}`, { method: 'GET' }, token)
}

/** Fetch the structured profile ("what the agent knows about you"). */
export async function getMemoryProfile(
  token: string | null,
  subject: string,
): Promise<MemoryProfileResponse> {
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
  const params = new URLSearchParams({ subject, query })
  return request<RecallDebugResponse>(`/memory/recall_debug?${params.toString()}`, { method: 'GET' }, token)
}

// ── LLM-Ops: the self-improvement loop (prompts / evals / releases) ──────────

/** List the version timeline for a prompt key. */
export async function getOpsPrompts(
  token: string | null,
  promptKey: string,
): Promise<OpsPromptsResponse> {
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
  return request<OpsReleaseDecisionResponse>(
    `/ops/releases/${encodeURIComponent(approvalId)}/decide`,
    { method: 'POST', body: JSON.stringify({ approved }) },
    token,
  )
}

// ── DevOps: tech stack + patch check ─────────────────────────────────────────

/** Fetch the running software bill of materials (DevOps stack inventory). */
export async function getStack(token: string | null = null): Promise<StackResponse> {
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
  return request<PatchCheckResponse>(
    '/stack/patch-check',
    { method: 'POST', body: JSON.stringify({ packages: packages ?? null }) },
    token,
  )
}

// ── Client: risk map + savings ───────────────────────────────────────────────

/** Fetch the agent-risk assurance heat-map (client-facing). */
export async function getRiskMap(token: string | null = null): Promise<RiskMapResponse> {
  return request<RiskMapResponse>('/risk-map', { method: 'GET' }, token)
}

/** Fetch the baseline-vs-actual savings roll-up (client-facing value view). */
export async function getSavings(token: string | null = null): Promise<SavingsResponse> {
  return request<SavingsResponse>('/savings', { method: 'GET' }, token)
}

// ── Admin: role assignment ───────────────────────────────────────────────────

/** Reassign a user's portal role (admin-only). */
export async function assignUserRole(
  userId: number,
  role: Role,
  token: string | null,
): Promise<AdminUser> {
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
  return request<ModelCardResponse>('/ml/model-card', { method: 'GET' }, token)
}

/** Fetch the offline regression-gate rollup (evals; admin/ai_team). */
export async function getEvalsReport(token: string | null): Promise<EvalsReportResponse> {
  return request<EvalsReportResponse>('/evals/report', { method: 'GET' }, token)
}

/** Fetch the tunable LLM-Ops self-improvement knobs (LLMOps; admin/ai_team). */
export async function getOpsParams(token: string | null): Promise<OpsParamsResponse> {
  return request<OpsParamsResponse>('/ops/params', { method: 'GET' }, token)
}

/** Fetch the token-optimization surface (savings summary + routing config). */
export async function getGatewayOptimization(
  token: string | null,
): Promise<GatewayOptimizationResponse> {
  return request<GatewayOptimizationResponse>('/gateway/optimization', { method: 'GET' }, token)
}

/** Fetch the agent-harness tweakable-config record (harness; admin/ai_team). */
export async function getHarnessConfig(token: string | null): Promise<HarnessConfigResponse> {
  return request<HarnessConfigResponse>('/harness/config', { method: 'GET' }, token)
}

/** Fetch the governance dashboard snapshot for a tenant scope (admin-only). */
export async function getGovernanceDashboard(
  token: string | null,
  opts: { tenantId?: number | null; window?: string } = {},
): Promise<GovernanceDashboardResponse> {
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
  return request<SecurityPostureResponse>('/security/posture', { method: 'GET' }, token)
}

/** Fetch per-node + per-run latency percentiles (latency; admin/devops). */
export async function getLatency(token: string | null): Promise<LatencyResponse> {
  return request<LatencyResponse>('/latency', { method: 'GET' }, token)
}

/** Run the offline red-team attack battery and return the verdicts (admin/devops). */
export async function runRedteam(token: string | null): Promise<RedteamReportResponse> {
  return request<RedteamReportResponse>('/redteam/run', { method: 'POST' }, token)
}

/**
 * Fetch the Aegis module manifest.
 *
 * Public — no bearer token. The landing page at `/` renders this before anyone
 * signs in, which is why the backend route carries no auth dependency.
 */
export async function getCapabilities(): Promise<CapabilitiesResponse> {
  return request<CapabilitiesResponse>('/platform/capabilities', { method: 'GET' }, null)
}

/**
 * Fetch the pre-login efficiency figures.
 *
 * Public, and deliberately narrow: ratios and counts only. The cost figures stay
 * behind auth on `/metrics` — see the backend `PublicMetricsResponse` docstring.
 */
export async function getPublicMetrics(): Promise<PublicMetricsResponse> {
  return request<PublicMetricsResponse>('/platform/public-metrics', { method: 'GET' }, null)
}

// ── Aegis Voice ─────────────────────────────────────────────────────────────

/**
 * Transcribe a recording and screen the transcript with the input rail stack.
 *
 * Sent as `multipart/form-data`, not JSON: the request body is a binary
 * recording, and base64 would inflate it ~33% for no benefit. This is the one
 * client call that must NOT go through {@link request}, which forces a JSON
 * content type — the browser has to set the multipart boundary itself, so the
 * header is deliberately left unset here.
 *
 * The response separates `transcript` (evidence) from `agent_input` (the only
 * text the rails cleared). Callers must forward `agent_input`; forwarding
 * `transcript` would defeat the rails.
 */
export async function transcribeVoice(
  audio: Blob,
  opts: { filename?: string; language?: string | null } = {},
  token: string | null,
): Promise<VoiceTranscribeResponse> {
  const form = new FormData()
  form.append('file', audio, opts.filename ?? 'recording.wav')
  if (opts.language) form.append('language', opts.language)
  const headers = new Headers()
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}/voice/transcribe`, {
    method: 'POST',
    headers,
    body: form,
  })
  if (!res.ok) {
    throw new Error(`POST /voice/transcribe failed: ${res.status} ${res.statusText}`)
  }
  return (await res.json()) as VoiceTranscribeResponse
}

/**
 * Analyse an image — hygiene, the injection screen, image-PII redaction, the
 * hosted vision call, and the platform's own output rails, in that order.
 *
 * JSON + base64 rather than multipart: the browser already holds the file as a
 * `data:` URL from `FileReader`, an image is small, and the backend's media
 * payloads serialise their bytes as base64 natively — so the body round-trips
 * the exact bytes the rails screen.
 *
 * A control refusing is a **200 with `outcome: 'blocked'`**, not an error. The
 * refusal and its per-control audit record are the product; only a malformed
 * request throws.
 */
export async function analyseImage(
  req: VisionAnalyseRequest,
  token: string | null,
): Promise<VisionAnalyseResponse> {
  return request<VisionAnalyseResponse>(
    '/vision/analyse',
    { method: 'POST', body: JSON.stringify(req) },
    token,
  )
}

/**
 * Fetch a tenant's spend / call-volume forecast (`GET /forecast/usage`).
 *
 * A refusal is a **200 with `available: false`**, not an error: "this tenant has
 * nine days of ledger and needs seventy-one" is the most useful thing this surface
 * can say, and an HTTP error would be discarded as a connectivity blip. Only a
 * genuinely broken request throws.
 */
export async function getForecastUsage(
  token: string | null,
  horizon = 14,
  metric: 'spend' | 'calls' = 'spend',
): Promise<ForecastResponse> {
  return request<ForecastResponse>(
    `/forecast/usage?horizon=${encodeURIComponent(horizon)}&metric=${encodeURIComponent(metric)}`,
    { method: 'GET' },
    token,
  )
}

/** Fetch the spend forecast projected against the tenant's cap (`GET /forecast/budget`). */
export async function getForecastBudget(
  token: string | null,
  horizon = 14,
  window: 'day' | 'month' = 'month',
): Promise<ForecastResponse> {
  return request<ForecastResponse>(
    `/forecast/budget?horizon=${encodeURIComponent(horizon)}&window=${encodeURIComponent(window)}`,
    { method: 'GET' },
    token,
  )
}

/** Fetch the client's domain demand forecast, read through the adapter seam. */
export async function getForecastDomain(
  token: string | null,
  horizon = 14,
): Promise<ForecastResponse> {
  return request<ForecastResponse>(
    `/forecast/domain?horizon=${encodeURIComponent(horizon)}`,
    { method: 'GET' },
    token,
  )
}
