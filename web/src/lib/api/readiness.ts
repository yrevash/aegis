/**
 * The two unauthenticated root probes: `GET /readyz` and `GET /health`.
 *
 * **Why these live here and not beside the screen that draws them.** Only
 * `web/src/lib/api` may call `fetch` — `backend/tests/api/test_route_coverage.py`
 * asserts it, and the reason is not tidiness: route coverage is proved by walking the
 * import graph from this directory, so an endpoint fetched anywhere else is invisible
 * to that walk and can rot unnoticed. The ops overview had its own `fetch` and duly
 * failed that test.
 *
 * These sit at the **root**, not under `/v1`, so they use `API_ORIGIN` rather than
 * `API_BASE`: a liveness probe that moved when the API version moved would start 404ing
 * during a rollout, which is the one moment it is most needed.
 */

import { API_ORIGIN } from './config'

/** One dependency's verdict, with the evidence behind it. */
export interface ReadyComponent {
  key: string
  name: string
  /** `store` | `substrate` | `model` | `isolation`. */
  category: string
  /** `up` | `down` | `unknown`. **`unknown` is not `down`** — a probe that timed out
   *  has established nothing, and drawing it as a failure would be a claim we cannot make. */
  status: string
  detail: string | null
  /** What was actually executed to decide this — the receipt for the verdict. */
  evidence: string | null
  measured_at: string
  /** Whether readiness fails when this one is down. */
  required: boolean
}

/** Body of `GET /readyz` — the deep readiness answer. */
export interface ReadyzResponse {
  status: string
  failing: string[]
  components: ReadyComponent[]
}

/** Body of `GET /health` — liveness plus the worker supervisor's own word. */
export interface LivenessResponse {
  status: string
  product: string
  version: string
  /** `running` | `down` | `starting` | `disabled` | `stopped`. */
  worker: string
}

/**
 * GET a root probe, keeping the body on the statuses that carry one.
 *
 * `alsoAccept` exists for `/readyz`, whose contract is to answer **503 with a full
 * component body** when a required dependency is down — that is the probe working, and
 * treating it as a transport failure would throw away the very payload naming what broke.
 */
async function probe<T>(path: string, alsoAccept: number[], signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })
  if (!res.ok && !alsoAccept.includes(res.status)) {
    throw new Error(
      `The probe at ${path} answered HTTP ${res.status}, so this deployment's readiness is unknown.`,
    )
  }
  return (await res.json()) as T
}

/** Every dependency's verdict with its evidence. 503 is a real answer, not a failure. */
export async function getReadyz(signal?: AbortSignal): Promise<ReadyzResponse> {
  return probe<ReadyzResponse>('/readyz', [503], signal)
}

/** Liveness, product version, and the job worker's supervisor state. */
export async function getLiveness(signal?: AbortSignal): Promise<LivenessResponse> {
  return probe<LivenessResponse>('/health', [], signal)
}
