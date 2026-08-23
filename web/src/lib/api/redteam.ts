/**
 * The red-team control plane — the battery catalogue, a parameterised run, and history.
 *
 * A faithful TypeScript mirror of `RedteamSuitesResponse` / `RedteamRunDetailResponse` /
 * `RedteamHistoryResponse` in `backend/src/app/api/routes_redteam.py`.
 *
 * **Why this module has its own fetch instead of `client.ts`'s `request`.** That helper
 * discards the response body, and on these routes the body *is* the point twice over: a
 * 403 says in a sentence that this role may read reports but not fire the battery, and a
 * 429 says which tenant's budget refused a live run. Throwing those away would turn two
 * governance decisions into "request failed", which is the same defect the surface exists
 * to remove.
 *
 * @see backend/src/app/api/routes_redteam.py
 * @see aegis/src/aegis/redteam/battery.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** A run either wires the model layers in or it does not. Nothing in between. */
export type RedteamMode = 'offline' | 'live'

/** What a run of one suite costs, before anyone presses the button. */
export interface RedteamEstimate {
  probes: number
  modelCalls: number
  promptTokens: number
  completionTokens: number
  /** Upper-bound USD, priced with the same `unit_cost` the usage ledger uses. */
  costUsd: number
  /** The deployment the guardrail layers would call; empty for an offline run. */
  model: string
}

/** One selectable battery. */
export interface RedteamSuite {
  id: string
  title: string
  summary: string
  /** OWASP LLM Top-10 (2025) ids this suite exercises, e.g. `['LLM01']`. */
  owasp: string[]
  categories: string[]
  attacks: number
  controls: number
  /** Probes no deterministic signature can catch; they leak offline by design. */
  semanticOnly: number
  /**
   * Probes no rail catches in *any* configuration — a completer does not close these.
   * Separate from `semanticOnly`, which promises exactly that it would.
   */
  beyondRails: number
  /**
   * Probe count per rail stage: `input` / `output` / `tool_result` / `ingest` /
   * `sequence`.
   */
  stages: Record<string, number>
  offlineFloor: number
  liveFloor: number
  offline: RedteamEstimate
  live: RedteamEstimate
}

/** Body of `GET /redteam/suites` — the catalogue plus what this caller may do. */
export interface RedteamSuitesResponse {
  suites: RedteamSuite[]
  defaultSuite: string
  mayRun: boolean
  mayRunLive: boolean
  /** The server's own sentence explaining a refusal, when the caller may not run. */
  refusal: string | null
}

/** One scored probe. Mirrors `AttackResult.as_dict`. */
export interface RedteamProbe {
  id: string
  category: string
  owasp: string
  prompt: string
  expects: string
  /**
   * Which rail screened it: `input` | `output` | `tool_result` | `ingest` |
   * `sequence`. A `sequence` probe's `prompt` is one representative query out of a
   * burst — see `burstQueries`.
   */
  stage: string
  /** Queries in this probe's burst; `0` for every stage whose probe is one payload. */
  burstQueries?: number
  /** Simulated seconds between those queries; `0` means they all land in one window. */
  burstIntervalSeconds?: number
  description: string
  needsLlm: boolean
  verdict: string
  /** The rail that produced the verdict — `injection`, `pii`, `content_safety`, … */
  layer: string | null
  /** The rail's own rationale, verbatim. Never composed here. */
  reason: string
  neutralized: boolean
  success: boolean
}

/** One category roll-up. `leaked` carries probe *ids*. */
export interface RedteamCategoryRollup {
  category: string
  total: number
  blocked: number
  blockRate: number
  leaked: string[]
}

/** The lossless report a run stored. */
export interface RedteamReport {
  passed: boolean
  suite?: string
  mode?: string
  overall: {
    attacksTotal: number
    attacksBlocked: number
    /** Refused because a rail could not run — stopped, but nothing was learned. */
    attacksUnchecked: number
    blockRate: number
    controlsTotal: number
    falsePositives: number
    falsePositiveRate: number
  }
  thresholds: { minBlockRate: number; maxFalsePositiveRate: number }
  categories: RedteamCategoryRollup[]
  /** Which rail stopped how many attacks, most active first. */
  rails: { layer: string; blocks: number }[]
  blocked: RedteamProbe[]
  /** The probes a rail refused without examining them. */
  unchecked: RedteamProbe[]
  leaked: RedteamProbe[]
  falsePositiveDetail: RedteamProbe[]
  attacks: RedteamProbe[]
}

/** One run's scalars — what a history row renders. */
export interface RedteamRun {
  runId: string
  tenantId: number | null
  suite: string
  mode: string
  startedAt: string | null
  durationMs: number
  initiatedBy: string
  attacksTotal: number
  attacksBlocked: number
  /**
   * Attacks refused because a rail was unavailable rather than because it found
   * anything. Deliberately outside `attacksBlocked`: a dead screen refuses everything,
   * and a 100% block rate earned that way is the harness reporting its own outage.
   */
  attacksUnchecked: number
  blockRate: number
  controlsTotal: number
  falsePositives: number
  falsePositiveRate: number
  minBlockRate: number
  maxFalsePositiveRate: number
  passed: boolean
  estimatedCostUsd: number
}

/** Body of `POST /redteam/runs` and `GET /redteam/runs/{id}`. */
export interface RedteamRunDetail {
  run: RedteamRun
  report: RedteamReport
  /** The previous run of the same suite in the same mode, or null when this is the first. */
  previous: RedteamRun | null
  estimate: RedteamEstimate | null
}

/** Body of `GET /redteam/runs`. */
export interface RedteamHistoryResponse {
  rows: RedteamRun[]
}

/**
 * A red-team endpoint failure that kept the server's own explanation.
 *
 * `gate` is set only on a 429 and names which admission gate refused — the same
 * `X-Admission-Gate` contract the durable job substrate uses, so a budget refusal
 * reads the same wherever it comes from.
 */
export class RedteamApiError extends ApiError {
  readonly gate: string | null

  constructor(status: number, method: string, path: string, detail?: string, gate?: string | null) {
    super(status, method, path, detail)
    this.name = 'RedteamApiError'
    this.gate = gate ?? null
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
    throw new RedteamApiError(
      res.status,
      method,
      path,
      detail,
      res.headers.get('X-Admission-Gate'),
    )
  }
  return (await res.json()) as T
}

/** The battery catalogue, with the cost of a live run of each suite. */
export async function getRedteamSuites(token: string | null): Promise<RedteamSuitesResponse> {
  return call<RedteamSuitesResponse>('/redteam/suites', { method: 'GET' }, token)
}

/** Run one suite and persist the report (platform staff only). */
export async function startRedteamRun(
  token: string | null,
  body: { suite: string; mode: RedteamMode; minBlockRate?: number; maxFalsePositiveRate?: number },
): Promise<RedteamRunDetail> {
  return call<RedteamRunDetail>(
    '/redteam/runs',
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}

/** This scope's runs, newest first. */
export async function getRedteamHistory(
  token: string | null,
  suite?: string,
): Promise<RedteamHistoryResponse> {
  const query = suite ? `?suite=${encodeURIComponent(suite)}` : ''
  return call<RedteamHistoryResponse>(`/redteam/runs${query}`, { method: 'GET' }, token)
}

/** One stored run in full, with the previous run of the same suite beside it. */
export async function getRedteamRun(
  token: string | null,
  runId: string,
): Promise<RedteamRunDetail> {
  return call<RedteamRunDetail>(
    `/redteam/runs/${encodeURIComponent(runId)}`,
    { method: 'GET' },
    token,
  )
}

/**
 * The sentence to show a person when a red-team call fails.
 *
 * `ApiError.message` is already the server's own `detail` when it sent one, falling
 * back to the status table — so a 403 renders the governance rule in the words the
 * server wrote it in, not a generic "forbidden".
 */
export function redteamMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}
