/**
 * Named seats — who can do what, and who gave it to them (§7.8).
 *
 * A faithful TypeScript mirror of `SeatsResponse` / `SeatRow` in
 * `backend/src/app/api/routes_seats.py`.
 *
 * **Why this module has its own fetch rather than `client.ts`'s `request`.** That
 * helper discards the response body, and here the body is the point: a seat write is
 * refused with a 409 whose sentence says the value was weaker than the tenant already
 * has in force, and a 403 whose sentence says which tenant the caller may act in.
 * Collapsing either into "request failed" turns a governance decision into a shrug.
 *
 * **A seat can only ever take capability away.** Sending `true` is not a grant: the
 * server folds it against the enclosing scopes and the strictest value wins, so `true`
 * either restores what the tenant already permits or is refused. The UI reflects that
 * rather than implementing it — the enforcement is the resolver's.
 *
 * @see backend/src/app/api/routes_seats.py
 * @see aegis/src/aegis/settings/seats.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** One capability of one seat, with the layer that decided it. */
export interface SeatCapability {
  key: string
  title: string
  allowed: boolean
  /** platform | tenant | user — the layer whose write decided this answer. */
  source: string
  /** Where the narrowing check that reads this key lives, in the server's own words. */
  gates: string
}

/** One user's seat. */
export interface Seat {
  userId: number
  username: string
  tenantId: number
  /** The seat's name, e.g. 'Support Lead'. Empty when nobody has named it. */
  label: string
  capabilities: SeatCapability[]
}

/** Body of `GET /admin/seats`. */
export interface SeatsResponse {
  tenantId: number
  rows: Seat[]
}

/** What `PUT /admin/seats/{userId}` accepts. Neither tenant nor user is on the wire. */
export interface SeatWrite {
  label?: string
  capabilities?: Record<string, boolean>
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
    throw new ApiError(res.status, method, path, detail)
  }
  return (await res.json()) as T
}

/** Every seat in one tenant. Platform staff must name the tenant; a tenant admin cannot. */
export async function getSeats(
  token: string | null,
  tenantId?: number | null,
): Promise<SeatsResponse> {
  const query = tenantId == null ? '' : `?tenant_id=${tenantId}`
  return call<SeatsResponse>(`/admin/seats${query}`, { method: 'GET' }, token)
}

/** Name a seat and set what it may do, for one user in the caller's tenant. */
export async function putSeat(
  token: string | null,
  userId: number,
  body: SeatWrite,
): Promise<Seat> {
  return call<Seat>(
    `/admin/seats/${userId}`,
    { method: 'PUT', body: JSON.stringify(body) },
    token,
  )
}
