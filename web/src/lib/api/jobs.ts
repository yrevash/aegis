/**
 * Durable-job endpoints — list, re-queue, cancel (§3.4).
 *
 * A faithful TypeScript mirror of `JobRunRow` / `JobsResponse` / `JobActionResponse`
 * in `backend/src/app/api/schemas.py`.
 *
 * **Why this module has its own fetch instead of `client.ts`'s `request`.** That
 * helper collapses every failure into `"<METHOD> <path> failed: <status>"` and
 * discards the response body. For these routes the body *is* the feature: admission
 * control refuses a job with a reason precisely so the refusal is visible, and
 * throwing that reason away in the browser would recreate — one layer up — the
 * invisible backpressure the endpoint exists to prevent. `JobsApiError` therefore
 * carries the status and the server's `detail`, plus the `X-Admission-Gate` header
 * saying which of the two gates said no.
 *
 * @see backend/src/app/api/schemas.py
 * @see aegis/src/aegis/jobs/admission.py
 */

import { getAuthToken } from './authToken'
import { API_BASE } from './config'

/** Which admission gate refused a job, when one did. */
export type AdmissionGate = 'concurrency' | 'budget'

/** One durable background job. Mirrors `JobRunRow`. */
export interface JobRunRow {
  id: number
  job_type: string
  /** 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled' | 'reconciling'. */
  status: string
  completed_stage: string | null
  workflow_id: string
  document_id: number | null
  cost_usd: number
  error: string | null
  cancelled_by: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

/** Response from `GET /jobs`. */
export interface JobsResponse {
  rows: JobRunRow[]
}

/** Response from `POST /jobs/{id}/cancel` and `POST /jobs/{id}/requeue`. */
export interface JobActionResponse {
  job: JobRunRow
  detail: string
}

/**
 * A job-endpoint failure that kept the server's own explanation.
 *
 * `gate` is set only on a 429, and is what lets a surface say "your tenant is at
 * its in-flight cap" rather than the useless "request failed".
 */
export class JobsApiError extends Error {
  readonly status: number
  readonly gate: AdmissionGate | null

  constructor(status: number, detail: string, gate: AdmissionGate | null) {
    super(detail)
    this.name = 'JobsApiError'
    this.status = status
    this.gate = gate
  }

  /** Whether admission refused this request (rather than auth, state or the network). */
  get refusedByAdmission(): boolean {
    return this.status === 429
  }
}

/** Read the server's `detail` from an error body, falling back to the status line. */
async function detailOf(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => null)
  if (body !== null && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string' && detail.length > 0) return detail
  }
  return `${res.status} ${res.statusText}`
}

/** Issue one authenticated call, preserving the server's reason on failure. */
async function jobsRequest<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const header = res.headers.get('X-Admission-Gate')
    const gate = header === 'concurrency' || header === 'budget' ? header : null
    throw new JobsApiError(res.status, await detailOf(res), gate)
  }
  return (await res.json()) as T
}

/** List the caller's tenant's durable jobs, newest first (`GET /jobs`). */
export async function getJobs(token: string | null): Promise<JobsResponse> {
  return jobsRequest<JobsResponse>('/jobs', { method: 'GET' }, token)
}

/**
 * Re-run a job's ingestion, resuming after its last committed stage.
 *
 * This is the admission-controlled call: a tenant at its concurrency cap, or without
 * the budget to finish, gets a 429 carrying the reason.
 */
export async function requeueJob(id: number, token: string | null): Promise<JobActionResponse> {
  return jobsRequest<JobActionResponse>(
    `/jobs/${encodeURIComponent(id)}/requeue`,
    { method: 'POST' },
    token,
  )
}

/** Cancel a running job (`POST /jobs/{id}/cancel`). A tenant may only cancel its own. */
export async function cancelJob(id: number, token: string | null): Promise<JobActionResponse> {
  return jobsRequest<JobActionResponse>(
    `/jobs/${encodeURIComponent(id)}/cancel`,
    { method: 'POST' },
    token,
  )
}
