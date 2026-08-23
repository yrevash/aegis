/**
 * One run's LangGraph checkpoint chain (ADR 0005).
 *
 * A read-only projection of `GET /v1/agent/checkpoints/{run_id}` — ids, structure and
 * timing. The server deliberately withholds each checkpoint's state payload (the query,
 * the retrieved passages, the proposed tool arguments), so nothing here can leak it.
 *
 * The types are aliases of the generated contract, not a hand-written mirror: change the
 * Pydantic model, run `scripts/build_openapi.py` + `npm run gen:api`, and a shape change
 * lands here as a compile error rather than as a silent `undefined`.
 *
 * @see backend/src/app/api/routes_checkpoints.py
 */

import type { components } from '@/lib/api/generated/schema'

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

type Schemas = components['schemas']

/**
 * A response as it is actually *sent*, with every property present — the local twin of
 * `types.ts`'s `Sent`. OpenAPI's `required` means "the client may not omit this" on the
 * way in; FastAPI serialises a response model with every field set, so a reader never
 * sees the key missing and `?? []` at the call site could never fire.
 */
type Sent<T> = T extends readonly unknown[]
  ? { [K in keyof T]: Sent<T[K]> }
  : T extends object
    ? { [K in keyof T]-?: Sent<T[K]> }
    : T

/** One checkpoint: which node produced it, what was pending, whether it parked. */
export type CheckpointRow = Sent<Schemas['CheckpointRow']>

/** The whole chain for one run, oldest first. */
export type CheckpointHistory = Sent<Schemas['CheckpointHistoryResponse']>

/**
 * Fetch one run's checkpoint chain.
 *
 * A run the caller's tenant does not own answers 404 — the same answer as a run that
 * does not exist, so an id cannot be probed. That surfaces here as an {@link ApiError},
 * and the caller renders an absence rather than an empty timeline.
 */
export async function getCheckpointHistory(
  runId: string,
  token: string | null = null,
): Promise<CheckpointHistory> {
  const path = `/agent/checkpoints/${encodeURIComponent(runId)}`
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
    throw new ApiError(res.status, 'GET', path, detail)
  }
  return (await res.json()) as CheckpointHistory
}
