/**
 * Pipeline health and the live cache counters (§7.10 / §7.10b / §7.14).
 *
 * A faithful TypeScript mirror of `PlatformHealthResponse` / `PipelineHealthResponse` /
 * `CacheStatsResponse` in `backend/src/app/api/routes_health.py`.
 *
 * **Why the shapes carry `not_recorded`.** Every one of these responses names the figures
 * the platform does *not* record and what would have to be emitted for them to exist.
 * That list is data, not documentation: it renders on the page beside the figures that
 * are real, so a reader can tell a measured zero from an absent measurement.
 *
 * **Why this module has its own fetch instead of `client.ts`'s `request`.** That helper
 * discards the response body, and here a 403 says in a sentence why infrastructure health
 * is platform-staff only. Rendering that as "request failed" would hide a governance
 * decision behind a network error.
 *
 * **Why `getPipelines` is here too.** `GET /pipelines` is not a health figure — it is the
 * declaration the health figures are read against (§8.12): three pipelines, their stages,
 * the module that owns each stage and what each emits, served from `aegis.pipelines` and
 * verified against the code before it is served. The pipeline page consumes both in one
 * poll, so they share this module's bearer-preserving `call`.
 *
 * @see backend/src/app/api/routes_health.py
 * @see backend/src/app/api/routes_pipelines.py
 * @see aegis/src/aegis/pipelines/spec.py
 * @see aegis/src/aegis/core/cache_stats.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/**
 * A component's verdict. `unknown` is deliberately not `down`: a probe that timed out
 * established nothing, and rendering that as a refusal is a lie in the safe direction.
 */
export type ComponentStatus = 'up' | 'down' | 'degraded' | 'unknown' | 'not_applicable'

/** One component, and the probe or query that produced its verdict. */
export interface ComponentHealth {
  key: string
  name: string
  /** `store` · `substrate` · `model` · `isolation`. */
  category: string
  status: ComponentStatus
  detail: string | null
  /** The probe call or SQL behind the verdict. Never empty. */
  evidence: string
  measured_at: string
  /** Whether `/readyz` refuses traffic when this component is down. */
  required: boolean
}

/** A figure the platform deliberately does not show, and what it would take to. */
export interface NotRecorded {
  figure: string
  why: string
  needs: string
}

/** Body of `GET /platform/health`. */
export interface PlatformHealthResponse {
  components: ComponentHealth[]
  measured_at: string
  not_recorded: NotRecorded[]
}

/** How many jobs of one kind sit in one status. */
export interface JobDepthRow {
  job_type: string
  status: string
  count: number
}

/** Measured wall clock inside one ingest stage handler. */
export interface StageTiming {
  stage: string
  runs: number
  p50_ms: number
  p95_ms: number
  max_ms: number
}

/** One recent failure with the reason the worker recorded. */
export interface JobFailure {
  job_type: string
  error: string
  finished_at: string | null
}

/** Percentiles over real finished-job durations. Absent when nothing finished. */
export interface DurationSummary {
  count: number
  p50_ms: number
  p95_ms: number
  max_ms: number
}

/** The durable substrate's state in the API process. */
export interface WorkerState {
  state: string
  detail: string | null
  since: string | null
  restarts: number
}

/** Body of `GET /platform/pipeline`. */
export interface PipelineHealthResponse {
  available: boolean
  unavailable_reason: string | null
  tenant_id: number | null
  window_hours: number
  generated_at: string
  depth: JobDepthRow[]
  in_flight: number
  oldest_pending_age_seconds: number | null
  oldest_pending_created_at: string | null
  finished_in_window: number
  failed_in_window: number
  /** `null` — never 0 — when nothing finished in the window. */
  failure_rate: number | null
  durations: DurationSummary | null
  stages: StageTiming[]
  stage_events_read: number
  recent_failures: JobFailure[]
  worker: WorkerState
  /** Per-figure provenance: which query produced which block. */
  sources: Record<string, string>
  not_recorded: NotRecorded[]
}

/** One cache: what it is, what the live instance was built as, and what it did here. */
export interface CacheRow {
  key: string
  name: string
  holds: string
  method: string
  /** Whether an instance was constructed in this process. */
  registered: boolean
  backend: string | null
  ttl_seconds: number | null
  threshold: number | null
  capacity: number | null
  /** Entry count, for a backend that knows it without a round trip. */
  entries: number | null
  lookups: number
  hits: number
  misses: number
  writes: number
  /** `null` when this cache performs no eviction the process can observe. */
  evictions: number | null
  /** `null` — never 0 — before the first lookup. */
  hit_rate: number | null
}

/** Body of `GET /platform/caches`. */
export interface CacheStatsResponse {
  caches: CacheRow[]
  generated_at: string
  source: string
  caveat: string
  not_recorded: NotRecorded[]
}

/**
 * Where a stage's output goes, and therefore what may be asked of it.
 *
 * `run_event` is committed to `run_events` in the transaction that did the work, so it
 * can be read back an hour later. `stream` reaches the browser on the SSE stream and is
 * then gone. `result` is a field on the returned object. The distinction is the reason
 * this page can show ingest stage percentiles and cannot show a query's node timings.
 */
export type EmissionChannel = 'run_event' | 'stream' | 'result'

/** One thing a stage emits, on one channel. */
export interface Emission {
  channel: EmissionChannel
  /** The event type, or a dotted field path on the returned object. */
  name: string
  detail: string
}

/** One declared stage of one pipeline. */
export interface PipelineStage {
  name: string
  label: string
  /** The dotted module that implements it. */
  owner: string
  summary: string
  /** True when it runs only under a configuration or a route. */
  optional: boolean
  emits: Emission[]
}

/** One declared pipeline — retrieval, agent or ingestion. */
export interface PipelineDeclaration {
  name: string
  title: string
  summary: string
  entrypoint: string
  /** The table its stage transitions commit to, or null when it persists nothing. */
  durable_record: string | null
  stages: PipelineStage[]
  /** What this pipeline does not record, in its own words. */
  limits: string[]
}

/** Body of `GET /pipelines`. */
export interface PipelinesResponse {
  pipelines: PipelineDeclaration[]
  /** The channel legend, served rather than restated in the browser. */
  channels: Record<string, string>
}

/** A pipeline-health call that kept the server's own explanation. */
export class PipelineApiError extends ApiError {
  constructor(status: number, method: string, path: string, detail?: string) {
    super(status, method, path, detail)
    this.name = 'PipelineApiError'
  }
}

/** GET a health path with the bearer, preserving the server's `detail` on failure. */
async function call<T>(path: string, token: string | null): Promise<T> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { method: 'GET', headers })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    if (res.status === 401) reportSessionExpired()
    throw new PipelineApiError(res.status, 'GET', path, detail)
  }
  return (await res.json()) as T
}

/** Every component's verdict with its evidence (devops / platform admin). */
export async function getPlatformHealth(
  token: string | null,
): Promise<PlatformHealthResponse> {
  return call<PlatformHealthResponse>('/platform/health', token)
}

/** The job + stage aggregation, narrowed to the caller's tenant by the server. */
export async function getPipelineHealth(
  token: string | null,
  windowHours?: number,
): Promise<PipelineHealthResponse> {
  const query = windowHours ? `?window_hours=${windowHours}` : ''
  return call<PipelineHealthResponse>(`/platform/pipeline${query}`, token)
}

/** The live per-cache counters (devops / platform admin / AI team). */
export async function getCacheStats(token: string | null): Promise<CacheStatsResponse> {
  return call<CacheStatsResponse>('/platform/caches', token)
}

/**
 * The three pipeline declarations (§8.12).
 *
 * Read by this page rather than restated in it: the ingest stage list rendered beside
 * the timings comes from here, so a stage that has never run shows as unmeasured
 * instead of vanishing from a vocabulary derived only from the rows that exist.
 */
export async function getPipelines(token: string | null): Promise<PipelinesResponse> {
  return call<PipelinesResponse>('/pipelines', token)
}

/** The sentence to show a person when one of these calls fails. */
export function pipelineMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback
}
