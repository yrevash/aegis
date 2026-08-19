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

/** The `documents` row an upload produced. Mirrors `DocumentUploadResponse`. */
export interface DocumentUpload {
  document_id: number
  filename: string
  content_sha256: string
  size_bytes: number
  status: string
  workflow_id: string | null
  /** False when identical bytes were already uploaded — no second ingest was started. */
  created: boolean
  /**
   * True when the bytes matched a document that was stored but never ingested (the
   * orchestrator was unreachable at upload time) and this call finally started it.
   * Re-uploading the file is the way out of that state; there is no second row.
   */
  restarted: boolean
  title: string | null
  doc_type: string | null
  doc_date: string | null
  detail: string
}

/**
 * Upload a document and start its ingest (`POST /documents`).
 *
 * Multipart, not JSON: base64 would inflate a 126-page PDF by a third and force the
 * whole thing to be materialised as one string on both sides. The `Content-Type` header
 * is deliberately left unset so the browser writes the multipart boundary itself.
 *
 * `docType` and `docDate` are optional and are the two chunk-prefix fields nothing but
 * the uploader can honestly know — a MIME type is `application/pdf` for the whole corpus,
 * and the upload time is not the date the document is *from*. Omitted, the backend
 * renders them as `untyped` / `undated` rather than inventing a value.
 *
 * Admission runs before any ingest starts, so a tenant at its in-flight cap or out of
 * budget gets a 429 whose reason `JobsApiError` carries verbatim.
 */
export async function uploadDocument(
  file: File,
  options: { docType?: string; docDate?: string },
  token: string | null,
): Promise<DocumentUpload> {
  const form = new FormData()
  form.append('file', file)
  if (options.docType) form.append('doc_type', options.docType)
  if (options.docDate) form.append('doc_date', options.docDate)

  const headers = new Headers()
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}/documents`, { method: 'POST', body: form, headers })
  if (!res.ok) {
    const header = res.headers.get('X-Admission-Gate')
    const gate = header === 'concurrency' || header === 'budget' ? header : null
    throw new JobsApiError(res.status, await detailOf(res), gate)
  }
  return (await res.json()) as DocumentUpload
}

// ─────────────────────────────────────────────────────────────────────────────
// The corpus listing
// ─────────────────────────────────────────────────────────────────────────────

/** One document in this tenant's corpus. Mirrors `DocumentRow`. */
export interface DocumentRow {
  document_id: number
  filename: string
  title: string | null
  /** 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'. */
  status: string
  completed_stage: string | null
  page_count: number | null
  chunk_count: number | null
  /** D-parse's own score in [0, 1]; null before the parse has run. */
  parse_confidence: number | null
  size_bytes: number
  doc_type: string | null
  doc_date: string | null
  workflow_id: string | null
  /** Why it failed — naming the stage and the underlying cause, not a wrapper. */
  error: string | null
  created_at: string | null
}

/** Response from `GET /documents`. */
export interface DocumentsResponse {
  rows: DocumentRow[]
}

/**
 * List this tenant's documents, newest first (`GET /documents`).
 *
 * The answer to "show me what you have ingested". Tenant-scoped in the backend through
 * the sealed `TenantScope` type, so a principal bound to no tenant gets an empty list
 * rather than everybody's corpus.
 */
export async function getDocuments(token: string | null): Promise<DocumentsResponse> {
  return jobsRequest<DocumentsResponse>('/documents', { method: 'GET' }, token)
}

// ─────────────────────────────────────────────────────────────────────────────
// The live ingest log (phase 4 §4.12 / §4.12b)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * What a stage is doing right now. Mirrors `app/ingestion/progress.py`.
 *
 * `failed` is the stage a failed run stopped in, and it is deliberately distinct from
 * `queued`: collapsing them made the stage that broke render identically to the stages
 * that never ran, so the only stage the log could name was the last one that succeeded.
 */
export type StageState = 'completed' | 'running' | 'failed' | 'queued'

/** One stage of the six-stage ingest pipeline. */
export interface IngestStage {
  name: string
  state: StageState
  /** The task queue whose concurrency policy made it wait. */
  queue: string
  /** ISO 8601 commit time, or null for a stage that has not run. */
  at: string | null
  duration_ms: number | null
  /** What the stage found — its own report merged with the columns it set. */
  detail: Record<string, unknown>
}

/** The D-parse quality gate's verdict, which until 4.12 only reached a log file. */
export interface IngestParse {
  confidence: number | null
  low: boolean
  threshold: number
  /** One line per signal, written for a person — 4.6c's explicit hand-off. */
  reasons: string[]
  heading_histogram: Record<string, number>
  ocr_enabled: boolean | null
  ocr_reason: string | null
  parser: string | null
  parse_seconds: number | null
}

/** One table the chunk stage lifted out as its own chunk (D8). */
export interface IngestTable {
  caption: string | null
  rows: number | null
  cols: number | null
  summarised: boolean
  reason: string | null
}

/** One entity the graph stage extracted, with its mention count. */
export interface IngestEntity {
  id: string
  label: string
  kind: string
  mentions: number
}

/** One extracted edge, both ends already resolved to their human labels. */
export interface IngestRelation {
  source: string
  phrase: string
  target: string
  mentions: number
}

/** The knowledge graph this ingest built — §4.12b. */
export interface IngestGraph {
  extractor: string | null
  entity_total: number
  relation_total: number
  entities: IngestEntity[]
  relations: IngestRelation[]
}

/** What the document became, counted off `chunks`. */
export interface IngestCorpus {
  chunks: number
  tables: number
  summarised: number
  enriched: number
  embedded: number
}

/** One chronological line of the log. */
export interface IngestLogEntry {
  seq: number
  ts: string
  kind: string
  stage: string | null
  message: string
}

/** Body of `GET /documents/{id}/ingest`. Mirrors `IngestProgressResponse`. */
export interface IngestProgress {
  document_id: number
  filename: string
  title: string | null
  status: string
  completed_stage: string | null
  page_count: number | null
  chunk_count: number | null
  parse_confidence: number | null
  workflow_id: string | null
  error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  stages: IngestStage[]
  parse: IngestParse
  corpus: IngestCorpus
  tables: IngestTable[]
  graph: IngestGraph
  entries: IngestLogEntry[]
}

/**
 * Read one document's live ingest log (`GET /documents/{id}/ingest`).
 *
 * A **projection**, not a stream: which stages completed is read off
 * `documents.completed_stage` and what each produced off the `run_events` entry written
 * in that stage's own transaction. So polling this is a replay — a browser that
 * refreshes, reconnects, or opens the document an hour later gets the same answer, and a
 * worker killed mid-ingest cannot make it disagree with what actually committed.
 */
export async function getIngestProgress(
  documentId: number,
  token: string | null,
): Promise<IngestProgress> {
  return jobsRequest<IngestProgress>(
    `/documents/${encodeURIComponent(documentId)}/ingest`,
    { method: 'GET' },
    token,
  )
}
