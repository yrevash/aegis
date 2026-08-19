/**
 * The console's own endpoints — models, chat sessions, attachments, own budget.
 *
 * A faithful TypeScript mirror of the Pydantic models in
 * `backend/src/app/api/routes_console.py`, which is where those four surfaces live
 * (a separate router module, merged into the served table, rather than a 3,700th
 * line of `routes.py`).
 *
 * `consoleRequest` keeps the server's `detail` on a failure rather than collapsing it
 * into a status line, for the same reason `jobs.ts` does: on these routes the body is
 * the feature. "This account is not bound to a tenant" and "this token carries no user
 * identity" are two different 403s with two different fixes, and a surface that showed
 * `GET /sessions failed: 403` for both would be telling the user nothing.
 *
 * @see backend/src/app/api/routes_console.py
 */

import { getAuthToken } from './authToken'
import { API_BASE } from './config'

/** One role of the effective routing table. Mirrors `ModelRow`. */
export interface ModelRow {
  /** The gateway role, e.g. 'generation' | 'cheap'. */
  role: string
  /** The deployment id this role currently routes to. */
  model: string
  /** What the input rate is charged per: 'tokens' | 'audio_minutes' | 'images'. */
  billing_unit: string
  /** USD for ONE input unit (1k prompt tokens, a minute of audio, an image). */
  input_cost_usd: number
  /** USD per 1k completion tokens; 0 for roles that emit no text. */
  output_cost_usd_per_1k: number
  /** Whether this deployment counts as a small/cheap model. */
  small: boolean
}

/** Response from `GET /models` — the effective role → deployment map, priced. */
export interface ModelsResponse {
  rows: ModelRow[]
  /** The role a plain answer runs on. */
  default_role: string
}

/** One conversation in the session rail. Mirrors `ChatSessionRow`. */
export interface ChatSessionRow {
  /** Also the `memory_session.id` for this conversation. */
  id: string
  title: string
  created_at: string | null
  last_active_at: string | null
}

/** Response from `GET /sessions`. */
export interface ChatSessionsResponse {
  rows: ChatSessionRow[]
}

/** One turn of a conversation. Mirrors `ChatMessageRow`. */
export interface ChatMessageRow {
  turn_index: number
  /** 'user' | 'assistant'. */
  role: string
  content: string
  /** The run that produced an assistant turn; null on a user turn. */
  run_id: string | null
  created_at: string | null
}

/** Response from `GET /sessions/{id}/messages`. */
export interface ChatMessagesResponse {
  session_id: string
  rows: ChatMessageRow[]
}

/** Response from `DELETE /sessions/{id}`. */
export interface DeletedResponse {
  id: string
  deleted: boolean
}

/**
 * Response from `POST /attachments` — a screened attachment.
 *
 * `blocked: true` arrives as a **200**: a refused image is the injection screen
 * working, and the composer shows it as a guardrail chip rather than an error.
 */
export interface AttachmentResponse {
  /** Ephemeral handle for this attachment within the run — not a storage key. */
  id: string
  filename: string | null
  /** The SNIFFED content type, never the caller's declaration. Null if hygiene could not run. */
  mime_type: string | null
  blocked: boolean
  summary: string
  /** One line: which controls ran, and which did not. */
  coverage: string
}

/** One budget cap joined with its live spend. Mirrors `BudgetStatusRow`. */
export interface BudgetStatusRow {
  budget: {
    id: number
    /** 'tenant' | 'user'. */
    scope_type: string
    scope_id: number
    /** 'day' | 'month'. */
    window: string
    token_cap: number | null
    usd_cap: number | null
    rpm: number | null
    tpm: number | null
  }
  tokens_used: number
  cost_usd_used: number
  calls: number
  tokens_remaining: number | null
  usd_remaining: number | null
}

/**
 * Response from `GET /me/budget` — the caller's own caps and spend.
 *
 * `measured` is the field that matters. `false` means **no cap governs this
 * principal**, and the pill must say "not yet measured" rather than draw a plausible
 * zero: an unmeasured figure presented as a measurement is the one thing this surface
 * is judged on.
 */
export interface MyBudgetResponse {
  tenant_id: number | null
  user_id: number | null
  rows: BudgetStatusRow[]
  measured: boolean
  cost_usd_used: number
  usd_cap: number | null
  usd_remaining: number | null
}

/** A console-endpoint failure that kept the server's own explanation. */
export class ConsoleApiError extends Error {
  readonly status: number

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ConsoleApiError'
    this.status = status
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
async function consoleRequest<T>(
  path: string,
  init: RequestInit,
  token: string | null,
): Promise<T> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) throw new ConsoleApiError(res.status, await detailOf(res))
  return (await res.json()) as T
}

/** Read the effective role → deployment routing table with its unit costs. */
export async function getModels(token: string | null): Promise<ModelsResponse> {
  return consoleRequest<ModelsResponse>('/models', { method: 'GET' }, token)
}

/** List the caller's own conversations, most recently active first. */
export async function getSessions(token: string | null): Promise<ChatSessionsResponse> {
  return consoleRequest<ChatSessionsResponse>('/sessions', { method: 'GET' }, token)
}

/**
 * Start a conversation and return it, id included.
 *
 * The **server** mints the id, because the same string becomes `memory_session.id`:
 * a client-chosen id would be a client-chosen memory partition key.
 */
export async function createSession(
  token: string | null,
  title = 'New chat',
): Promise<ChatSessionRow> {
  return consoleRequest<ChatSessionRow>(
    '/sessions',
    { method: 'POST', body: JSON.stringify({ title }) },
    token,
  )
}

/** Retitle one of the caller's conversations. */
export async function renameSession(
  token: string | null,
  id: string,
  title: string,
): Promise<ChatSessionRow> {
  return consoleRequest<ChatSessionRow>(
    `/sessions/${encodeURIComponent(id)}`,
    { method: 'PATCH', body: JSON.stringify({ title }) },
    token,
  )
}

/** Delete one of the caller's conversations and its turns. */
export async function deleteSession(
  token: string | null,
  id: string,
): Promise<DeletedResponse> {
  return consoleRequest<DeletedResponse>(
    `/sessions/${encodeURIComponent(id)}`,
    { method: 'DELETE' },
    token,
  )
}

/** Read one conversation's turns in order — the transcript a reload restores. */
export async function getSessionMessages(
  token: string | null,
  id: string,
): Promise<ChatMessagesResponse> {
  return consoleRequest<ChatMessagesResponse>(
    `/sessions/${encodeURIComponent(id)}/messages`,
    { method: 'GET' },
    token,
  )
}

/** Screen one composer attachment and return the handle the run carries. */
export async function uploadAttachment(
  token: string | null,
  body: {
    image_base64: string
    mime_type?: string
    question?: string
    filename?: string | null
  },
): Promise<AttachmentResponse> {
  return consoleRequest<AttachmentResponse>(
    '/attachments',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/** Read the caller's own effective caps and live spend. */
export async function getMyBudget(token: string | null): Promise<MyBudgetResponse> {
  return consoleRequest<MyBudgetResponse>('/me/budget', { method: 'GET' }, token)
}
