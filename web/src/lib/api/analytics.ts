/**
 * Embedded analytics — the Superset seam, from the browser's side.
 *
 * Four calls, and what is *not* in any of them is the point: no datasource, no column,
 * no metric, no row limit and no tenant id. A board id and a window key go out; rows
 * that are already narrowed to this session's tenant come back. The tenant filter is a
 * `WHERE` clause the backend derived from the sealed session scope and compiled into
 * the Superset query — there is no field here that could move it.
 *
 * Its own `fetch` rather than `client.ts`'s `request`, for the same reason
 * `redteam.ts` has one: the *body* of a failure is the useful part. A 503 here says
 * "Superset is not answering at http://localhost:8088. Start it with `superset run …`",
 * and collapsing that into "request failed" would turn the one actionable sentence on
 * the page into a shrug.
 *
 * @see backend/src/app/api/routes_analytics.py
 * @see aegis/src/aegis/analytics/rls.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** Whether the analytics page can draw anything, and what to do when it cannot. */
export interface AnalyticsStatus {
  /** Whether the operator turned the feature on at all. */
  enabled: boolean
  /** Whether the Superset address and service account are filled in. */
  configured: boolean
  /** Whether Superset answered its health endpoint just now. */
  reachable: boolean
  /** Whether this deployment expects `EMBEDDED_SUPERSET` to work. */
  embedEnabled: boolean
  /** What is true right now, in one sentence. */
  detail: string
  /** What to do next, in one sentence. Empty when nothing is wrong. */
  action: string
  /** The Superset origin that was tried. Never a credential. */
  baseUrl: string
  /** How many boards this role may select. */
  boards: number
}

/** One board this role may select. Carries no datasource and no credential. */
export interface AnalyticsBoard {
  id: string
  title: string
  summary: string
  /** `chart` (drawn by Aegis), `dashboard` (embedded Superset), or both. */
  kinds: string[]
  /** The window this board opens on. */
  window: string
  /** The dimension column drawn on the x axis. */
  x: string
  /** The measure keys each row carries. */
  series: string[]
}

/** The catalogue, narrowed to this session's role. */
export interface AnalyticsBoardsResponse {
  boards: AnalyticsBoard[]
  /** The selectable windows: key → label. */
  windows: Record<string, string>
  /** False only for a platform-wide session, which reads across tenants on purpose. */
  tenantScoped: boolean
}

/** The rows behind one board, already narrowed to this session's tenant. */
export interface AnalyticsBoardData {
  boardId: string
  title: string
  window: string
  columns: string[]
  rows: Array<Record<string, unknown>>
  x: string
  series: string[]
  tenantScoped: boolean
}

/** A minted guest token and the embedded dashboard it opens. */
export interface AnalyticsEmbedGrant {
  boardId: string
  token: string
  supersetDomain: string
  uuid: string
  expiresInSeconds: number
}

/** The sentence to render for a failed analytics call. */
export function analyticsMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  return 'Aegis could not reach its own backend for this page. Check the backend is running, then reload.'
}

/**
 * One call to the analytics API.
 *
 * Takes an options object rather than positional arguments so `method: 'POST'` appears
 * as a literal in each caller's own body. That is not a style preference:
 * `backend/tests/api/test_route_coverage.py` reads this folder statically to decide
 * which endpoints a portal can reach, and it finds the verb by looking for exactly that
 * literal. A method threaded through as a positional string makes every route in this
 * file read as a GET, and the two POSTs then look unreachable from any portal.
 */
async function call<T>({
  method,
  path,
  body,
}: {
  method: string
  path: string
  body?: unknown
}): Promise<T> {
  const token = getAuthToken()
  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
  } catch {
    throw new ApiError(0, method, path)
  }
  if (response.status === 401) reportSessionExpired()
  if (!response.ok) {
    let detail = ''
    try {
      const payload: unknown = await response.json()
      if (payload && typeof payload === 'object' && 'detail' in payload) {
        const raw = (payload as { detail: unknown }).detail
        if (typeof raw === 'string') detail = raw
      }
    } catch {
      detail = ''
    }
    // `detail || undefined`, not `detail`: an empty string is not nullish, so passing
    // it through would replace the status sentence with nothing at all.
    throw new ApiError(response.status, method, path, detail || undefined)
  }
  return (await response.json()) as T
}

/** `GET /analytics/status` — the honest state, in every state. Never 500s on a dead Superset. */
export async function getAnalyticsStatus(): Promise<AnalyticsStatus> {
  return call<AnalyticsStatus>({ method: 'GET', path: '/analytics/status' })
}

/** `GET /analytics/boards` — the boards this role is an audience for. */
export async function getAnalyticsBoards(): Promise<AnalyticsBoardsResponse> {
  return call<AnalyticsBoardsResponse>({ method: 'GET', path: '/analytics/boards' })
}

/**
 * `POST /analytics/boards/{id}/data` — one board's rows, scoped to this tenant.
 *
 * @param boardId - A board id from {@link getAnalyticsBoards}.
 * @param window - A key of the `windows` map, or null for the board's own default.
 */
export async function getAnalyticsBoardData(
  boardId: string,
  window: string | null,
): Promise<AnalyticsBoardData> {
  return call<AnalyticsBoardData>({
    method: 'POST',
    path: `/analytics/boards/${encodeURIComponent(boardId)}/data`,
    body: { window },
  })
}

/**
 * `POST /analytics/boards/{id}/embed-token` — a short-lived, tenant-scoped guest token.
 *
 * The only Superset credential that ever reaches this browser. It grants one dashboard
 * and carries the tenant's row filter, which Superset compiles into every query run
 * under it.
 */
export async function getAnalyticsEmbedToken(boardId: string): Promise<AnalyticsEmbedGrant> {
  return call<AnalyticsEmbedGrant>({
    method: 'POST',
    path: `/analytics/boards/${encodeURIComponent(boardId)}/embed-token`,
    body: {},
  })
}
