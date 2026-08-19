/**
 * The per-tenant prompt control plane (§7.7) — read the live prompt, change it, and see
 * which version a run was served.
 *
 * A faithful TypeScript mirror of `PromptScreen` / `PromptVersionRow` /
 * `PromptRunsResponse` in `backend/src/app/api/routes_llmops.py`.
 *
 * **The tenant is not on the wire from here.** Every one of these calls is scoped by the
 * server from the caller's sealed `AuthContext`; a tenant admin has exactly one scope and
 * naming another is a 403. The optional `tenantId` is the platform-staff *selector* only,
 * and passing it from a tenant-bound session simply fails — the browser is not where that
 * decision is made.
 *
 * Like `redteam.ts`, this module has its own fetch rather than `client.ts`'s `request`:
 * these routes answer a refusal with a sentence (why a prompt key is platform-owned, why
 * a scope is not yours), and discarding the body would turn a governance decision into
 * "request failed".
 *
 * @see backend/src/app/api/routes_llmops.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** Lifecycle of one version. */
export type PromptVersionStatus = 'draft' | 'staged' | 'active' | 'archived' | string

/** One version in a scope's history. */
export interface PromptVersionRow {
  id: number
  version: number
  status: PromptVersionStatus
  isActive: boolean
  systemPrompt: string
  createdBy: string | null
  notes: string | null
  createdAt: string | null
  activatedAt: string | null
}

/** Everything one prompt key's screen needs, read in one sealed scope. */
export interface PromptScreen {
  promptKey: string
  tenantId: number | null
  /** Names the scope the answer was read in — "Tenant 1", or the platform default. */
  scopeLabel: string
  activeVersion: number | null
  activePrompt: string | null
  /** True when nothing is active here and the shipped prompt is running. */
  onShippedPrompt: boolean
  /** The platform floor — composed underneath every version, editable by nobody. */
  floor: string
  versions: PromptVersionRow[]
  /** False when this key belongs to the platform, so the form is read-only. */
  editable: boolean
}

/** Which prompt version one run was served. */
export interface PromptRunRow {
  runId: string
  promptKey: string
  version: number | null
  /** `registry` — a promoted version; `floor` — the shipped prompt. */
  source: string
  ts: string
}

/** Body of `GET /llmops/runs`. */
export interface PromptRunsResponse {
  rows: PromptRunRow[]
  /** The server's own sentence about what "recent" honestly covers. */
  window: string
}

/** A prompt-control failure that kept the server's own explanation. */
export class LLMOpsApiError extends ApiError {
  constructor(status: number, method: string, path: string, detail?: string) {
    super(status, method, path, detail)
    this.name = 'LLMOpsApiError'
  }
}

async function call<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
  const method = init.method ?? 'GET'
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    if (res.status === 401) reportSessionExpired()
    throw new LLMOpsApiError(res.status, method, path, detail)
  }
  return (await res.json()) as T
}

/** The live prompt, its history and the floor, for one key in the caller's scope. */
export async function getPromptScreen(
  token: string | null,
  promptKey: string,
): Promise<PromptScreen> {
  return call<PromptScreen>(
    `/llmops/prompts?prompt_key=${encodeURIComponent(promptKey)}`,
    { method: 'GET' },
    token,
  )
}

/** Write a new draft of a task prompt. */
export async function createPromptVersion(
  token: string | null,
  body: { promptKey: string; systemPrompt: string; notes?: string | null },
): Promise<PromptVersionRow> {
  return call<PromptVersionRow>(
    '/llmops/prompts/versions',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/** Make one version live — the change an operator makes without a deploy. */
export async function activatePromptVersion(
  token: string | null,
  versionId: number,
): Promise<PromptScreen> {
  return call<PromptScreen>(
    `/llmops/prompts/versions/${encodeURIComponent(String(versionId))}/activate`,
    { method: 'POST' },
    token,
  )
}

/** Revert to the version that was live before the current one. */
export async function rollbackPrompt(
  token: string | null,
  promptKey: string,
): Promise<PromptScreen> {
  return call<PromptScreen>(
    '/llmops/prompts/rollback',
    { method: 'POST', body: JSON.stringify({ promptKey }) },
    token,
  )
}

/** Which prompt version each of this scope's recent runs was served. */
export async function getPromptRuns(token: string | null): Promise<PromptRunsResponse> {
  return call<PromptRunsResponse>('/llmops/runs', { method: 'GET' }, token)
}

/**
 * Which prompt version one named run was served.
 *
 * Scoped by the server to the caller's own tenant, and the scope is a filter rather than
 * a hint: a run id belonging to somebody else answers 404, never with their prompt.
 */
export async function getPromptRun(
  token: string | null,
  runId: string,
): Promise<PromptRunRow> {
  return call<PromptRunRow>(
    `/llmops/runs/${encodeURIComponent(runId)}`,
    { method: 'GET' },
    token,
  )
}

/** The sentence to show a person when a prompt-control call fails. */
export function llmopsErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message
  return 'Could not reach the prompt control plane. Is the backend running?'
}
