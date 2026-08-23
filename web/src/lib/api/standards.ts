/**
 * The public standards summary — `GET /v1/platform/standards`.
 *
 * The landing page's standards band reads this and nothing else. It is the *summary*
 * projection of `GET /v1/compliance`: the framework names, the jurisdiction each
 * belongs to, and the four derived state counts. The control-by-control map — every
 * gap sentence and every file that would have closed it — stays behind
 * `require_platform_security_reader`, because a public gap map is a target list.
 *
 * **Unauthenticated on purpose,** so no bearer token is attached: the page that calls
 * this has no session, and sending a stale one would be the only way to make an
 * anonymous read fail.
 *
 * The types are aliases of the generated contract rather than a hand-written mirror,
 * so a change to the Pydantic model lands here as a compile error rather than as a
 * silent `undefined`. Nothing in this file names a number — that is the whole point
 * of the endpoint existing.
 *
 * @see backend/src/app/api/routes_standards.py
 */

import type { components } from '@/lib/api/generated/schema'

import { ApiError } from './apiError'
import { API_BASE } from './config'

type Schemas = components['schemas']

/**
 * A response as it is actually *sent*, with every property present.
 *
 * OpenAPI's `required` means "the client may not omit this" on the way in; FastAPI
 * serialises a response model with every field set, so a reader never sees the key
 * missing and `?? 0` at the call site could never fire.
 */
type Sent<T> = T extends readonly unknown[]
  ? { [K in keyof T]: Sent<T[K]> }
  : T extends object
    ? { [K in keyof T]-?: Sent<T[K]> }
    : T

/** The four derived state counts, and the total they sum to. */
export type StandardsCoverage = Sent<Schemas['FrameworkCoverage']>

/** One framework: what it is called, whose law it is, how far it goes. */
export type FrameworkSummary = Sent<Schemas['FrameworkSummary']>

/** The whole public band: the disclaimer, every framework, and the totals. */
export type StandardsResponse = Sent<Schemas['StandardsResponse']>

/**
 * Fetch the public standards summary.
 *
 * Rejects with an {@link ApiError} on any non-2xx, which the band renders as a stated
 * absence in the slot the counts would have occupied. It never falls back to a
 * remembered figure: a number this page cannot source is a number it does not print.
 */
export async function getStandards(): Promise<StandardsResponse> {
  const res = await fetch(`${API_BASE}/platform/standards`, {
    method: 'GET',
    headers: { 'Content-Type': 'application/json' },
  })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    throw new ApiError(res.status, 'GET', '/platform/standards', detail)
  }
  return (await res.json()) as StandardsResponse
}
