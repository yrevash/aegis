/**
 * The console's own endpoints — models, chat sessions, attachments, own budget,
 * the settings catalogue and the tool roster.
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

import { ApiError, apiMessage, logRequestFailure } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
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
  /**
   * Why a rail refused, in the server's own sentence; `''` when nothing refused.
   * It distinguishes "blocked by the injection screen" (the image carries an
   * instruction) from "blocked because the injection screen could not run" (the rail
   * failed closed) — two states the operator has to act on differently.
   */
  blocked_reason: string
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

/**
 * A console-endpoint failure that kept the server's own explanation.
 *
 * An {@link ApiError} subclass, so the 401 sign-out and the memory rail's withholding
 * rule read `status` the same way here as on every other route, while the `message`
 * stays the server's `detail` whenever it sent one.
 */
export class ConsoleApiError extends ApiError {
  constructor(status: number, method: string, path: string, detail: string) {
    super(status, method, path, detail)
    this.name = 'ConsoleApiError'
  }
}

/**
 * Read the server's `detail` from an error body, falling back to a sentence.
 *
 * The fallback was `"403 Forbidden"` — a status line, rendered verbatim into a panel.
 * When the server declined to explain itself, {@link apiMessage} says what the status
 * means and what to do next instead.
 */
async function detailOf(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => null)
  if (body !== null && typeof body === 'object' && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string' && detail.length > 0) return detail
  }
  return apiMessage(res.status)
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
  if (!res.ok) {
    const failure = new ConsoleApiError(res.status, init.method ?? 'GET', path, await detailOf(res))
    if (res.status === 401) reportSessionExpired()
    // Same line, same reasons, as `lib/api/client.ts` — see the comment there. Facts in
    // the message because an object argument renders as `{}` in Next.js's overlay, and
    // a graded level because a deliberate refusal is not a backend failure.
    logRequestFailure(failure.route, res.status, failure.message)
    throw failure
  }
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

// ─────────────────────────────────────────────────────────────────────────────
// Settings — the resolved catalogue, and where each value came from
// ─────────────────────────────────────────────────────────────────────────────

/**
 * The catalogue's own UI descriptor for one control. Mirrors
 * `aegis.settings.spec.setting_controls`, which is where the type, the default, the
 * legal values and the help text are declared — once, as data. Nothing here is
 * restated in the browser, because a second copy of a control's bounds is a second
 * place for the form and the enforcement to disagree.
 */
export interface SettingControl {
  key: string
  /** The Python type name: 'bool' | 'int' | 'float' | 'str' | 'list'. */
  type: string
  /** Which control to render: 'toggle' | 'number' | 'text' | 'tags' | 'select'. */
  control: string
  /** The platform default — the value in force when nobody has written anything. */
  default: unknown
  /** How the scopes combine: 'override' | 'tighten_only' | 'union'. */
  merge: string
  writable_by: string[]
  readable_by: string[]
  description: string
  minimum?: number
  maximum?: number
  choices?: unknown[]
  /** For a tighten-only key: which direction is stricter. */
  stricter?: string
  /**
   * Whether anything in the system actually reads this key yet.
   *
   * **Not decoration, and not optional to render.** `false` means the control binds to
   * nothing: a write is accepted, audited and resolved, and changes no run. Six keys
   * once did that silently — `agent.gate_min_risk` was writable, tighten-only and
   * renderable while reaching no resolver at all, which is the defect the whole
   * settings package exists to stop. The server now declares it; a screen that draws
   * an `effective: false` control as a live input re-creates the defect on this side of
   * a wire that is finally telling the truth.
   */
  effective: boolean
  /**
   * Present only when `effective` is false: what would have to change for the key to
   * bind. Written for an operator and rendered verbatim — it is the difference between
   * "this does nothing" and "this does nothing *yet*, and here is why".
   */
  inert_reason?: string
}

/**
 * One resolved control. Mirrors `SettingRow`.
 *
 * `source` is the field this endpoint exists for: "Team (your setting)" and "Team
 * (your tenant's default)" render identically without it and mean opposite things
 * the moment somebody wants to change one.
 */
export interface SettingRow {
  key: string
  value: unknown
  /** Which scope decided the effective value. */
  source: 'platform' | 'tenant' | 'user'
  control: SettingControl
  /** Whether this caller's role may write the key at all. */
  writable: boolean
}

/** Body of `GET /settings` — every control this caller may read, resolved. */
export interface SettingsResponse {
  tenant_id: number | null
  user_id: number | null
  rows: SettingRow[]
}

/** Which layer of the caller's own chain a write lands on. */
export type SettingScope = 'platform' | 'tenant' | 'user'

/** Read every control this caller may read, each with the scope that decided it. */
export async function getSettings(token: string | null): Promise<SettingsResponse> {
  return consoleRequest<SettingsResponse>('/settings', { method: 'GET' }, token)
}

/** Read one control's effective value and its source. */
export async function getSetting(token: string | null, key: string): Promise<SettingRow> {
  return consoleRequest<SettingRow>(
    `/settings/${encodeURIComponent(key)}`,
    { method: 'GET' },
    token,
  )
}

/**
 * Write one control at one of the caller's **own** layers and return it re-resolved.
 *
 * The returned row is the value now in force, which is not always the value written:
 * a tenant admin who tightens a key their platform already set stricter gets the
 * platform's value back with `source: 'platform'`. Rendering the response rather than
 * the request is what stops the screen claiming a setting that is not in effect.
 *
 * Every refusal is the server's, with its reason on `ConsoleApiError.message`: 403 the
 * role or the scope, 409 a weakening of a tighten-only key, 422 an illegal value.
 */
export async function putSetting(
  token: string | null,
  key: string,
  value: unknown,
  scope: SettingScope = 'user',
): Promise<SettingRow> {
  return consoleRequest<SettingRow>(
    `/settings/${encodeURIComponent(key)}`,
    { method: 'PUT', body: JSON.stringify({ value, scope }) },
    token,
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Tools — the effective roster, and which layer decided each row
// ─────────────────────────────────────────────────────────────────────────────

/**
 * One action tool as this caller may use it. Mirrors `ToolRow`.
 *
 * `decided_by` names the narrowest layer that constrains it: `platform` (declared and
 * unconstrained — it runs), `persona` (the allowlist does not carry it), or `tenant`
 * (allowed, but the tenant's gate floor means a human decides before it runs).
 */
export interface ToolRow {
  name: string
  description: string
  /** The declared risk tier: 'low' | 'medium' | 'high'. */
  risk: string
  allowed: boolean
  decided_by: 'platform' | 'persona' | 'tenant'
  requires_approval: boolean
}

/** Body of `GET /tools` — the effective roster for one caller. */
export interface ToolRosterResponse {
  persona: string
  /** The tenant's effective human-gate floor, resolved as a run resolves it. */
  gate_min_risk: string
  rows: ToolRow[]
  allowed_count: number
  total: number
}

/**
 * Read the effective tool roster for a persona — "6 of 9", and why the other three.
 *
 * A **report**. Pinning a subset for one run needs a per-run field the query request
 * does not carry, so this endpoint does not pretend to offer one.
 */
export async function getToolRoster(
  token: string | null,
  persona?: string | null,
): Promise<ToolRosterResponse> {
  const query = persona == null || persona === '' ? '' : `?persona=${encodeURIComponent(persona)}`
  return consoleRequest<ToolRosterResponse>(`/tools${query}`, { method: 'GET' }, token)
}
