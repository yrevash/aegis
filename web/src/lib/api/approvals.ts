/**
 * The durable approvals inbox — the list around the gate (§7.1).
 *
 * A faithful TypeScript mirror of `ApprovalInboxRow` / `ApprovalInboxResponse` /
 * `ApprovalDecisionResponse` in `backend/src/app/api/schemas.py`.
 *
 * **Why this module has its own fetch instead of `client.ts`'s `request`.** Same
 * reason `jobs.ts` does: on these routes the server's `detail` *is* the feature. The
 * 403 a decision endpoint answers with — *"This gate belongs to a tenant. A tenant's
 * own admin decides it"* — is the ownership rule stating itself in front of whoever
 * tried, and rendering it as "something went wrong" would throw away the one sentence
 * that explains the product.
 *
 * Note what this module does **not** contain: any rule about who may decide what.
 * That lives once, on the server (`app.api.routes._decision_refusal`), and arrives on
 * every row as `decidable` + `blocked_reason`. A copy of it here would be a second
 * source of truth that can disagree with the 403 the button is about to receive.
 *
 * @see backend/src/app/api/schemas.py
 * @see backend/src/app/api/routes.py `approvals_inbox`
 */

import type { ApprovalDecision } from '@/lib/api/types'
import type { ApprovalAction, RiskLevel } from '@/lib/stream'

import { ApiError, serverDetail } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** One row of the durable inbox. Mirrors `ApprovalInboxRow`. */
export interface ApprovalInboxRow {
  id: string
  run_id: string
  /** Owning tenant, or null for one of Aegis's own actions. */
  tenant_id: number | null
  /** The representative (highest-risk) call — never the whole story on a fan-out. */
  action: string
  args: Record<string, unknown>
  risk: RiskLevel
  rationale: string | null
  /** 'pending' | 'approved' | 'rejected' | 'resuming' | 'escalated' | 'expired'. */
  status: string
  persona: string | null
  sla_deadline: string | null
  created_at: string
  ml_snapshot: Record<string, unknown>
  /** Every call approving runs. Empty on a row written before the column existed. */
  actions: ApprovalAction[]
  requested_by: number | null
  decided_at: string | null
  decided_by: string | null
  /** Whether **this caller** may decide this gate — computed by the server. */
  decidable: boolean
  /** Why they may not, in the server's own words. Null when `decidable`. */
  blocked_reason: string | null
}

/** Response from `GET /approvals`. */
export interface ApprovalsResponse {
  rows: ApprovalInboxRow[]
}

/** Response from `POST /approvals/{id}/decision`. Mirrors `ApprovalDecisionResponse`. */
export interface ApprovalDecisionResult {
  id: string
  status: string
  /** False on a replayed or late decision — the idempotency guard, not a failure. */
  accepted: boolean
}

/** The `status` words the inbox filter offers. `all` applies no status predicate. */
export type ApprovalStatusFilter = 'pending' | 'decided' | 'all'

/** Read the server's `detail` from an error body, or fall back to the status line. */
async function approvalsRequest<T>(
  path: string,
  init: RequestInit,
  token: string | null,
): Promise<T> {
  const method = init.method ?? 'GET'
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const detail = await res
      .json()
      .then(serverDetail)
      .catch(() => null)
    if (res.status === 401) reportSessionExpired()
    throw new ApiError(res.status, method, path, detail ?? undefined)
  }
  return (await res.json()) as T
}

/** What one inbox read asks for. Every field narrows; none of them widens. */
export interface ApprovalsQuery {
  status?: ApprovalStatusFilter
  /** ISO 8601 instant — only gates raised at or after it. */
  since?: string | null
  /**
   * One tenant's queue. **Platform staff only**: a tenant principal naming another
   * tenant is a 403 from the server, which is the isolation rule showing its work.
   */
  tenantId?: number | null
  limit?: number
}

/**
 * Read the approvals this caller may see (`GET /approvals`).
 *
 * Who sees what is decided entirely by the bearer: platform staff get every tenant's
 * queue, a tenant admin gets its own tenant's, and everybody else gets the gates they
 * raised themselves. There is no parameter here that widens that.
 */
export async function getApprovals(
  query: ApprovalsQuery,
  token: string | null,
): Promise<ApprovalsResponse> {
  const params = new URLSearchParams()
  params.set('status', query.status ?? 'pending')
  if (query.since) params.set('since', query.since)
  if (query.tenantId != null) params.set('tenant_id', String(query.tenantId))
  if (query.limit != null) params.set('limit', String(query.limit))
  return approvalsRequest<ApprovalsResponse>(
    `/approvals?${params.toString()}`,
    { method: 'GET' },
    token,
  )
}

/**
 * Decide one parked gate (`POST /approvals/{id}/decision`).
 *
 * Idempotent on the server: a replayed decision comes back `accepted: false` and
 * never resumes the run twice.
 */
export async function decideApproval(
  id: string,
  decision: ApprovalDecision,
  token: string | null,
): Promise<ApprovalDecisionResult> {
  return approvalsRequest<ApprovalDecisionResult>(
    `/approvals/${encodeURIComponent(id)}/decision`,
    { method: 'POST', body: JSON.stringify({ decision }) },
    token,
  )
}
