/**
 * Downloadable reports — the record, as a file the operator keeps (§7.12).
 *
 * **Why this module does not fetch the CSV.** The obvious implementation is to fetch
 * the file with the bearer, wrap the body in a `Blob` and click a synthetic
 * `<a download>`. That buffers a whole export in the tab's memory — defeating the
 * server's streaming — and the synthetic click is inert inside a sandboxed frame. The
 * mechanism that actually works everywhere is the one the web already has: a request
 * whose response carries `Content-Disposition: attachment`, written to disk by the
 * browser as it arrives.
 *
 * A navigation cannot set an `Authorization` header, so the download is two steps: mint
 * a **download ticket** (`POST /reports/tickets`, authenticated normally), then navigate
 * to the CSV route with `?ticket=`. The ticket lives 60 seconds, names one report, and
 * is not an access token — the backend refuses it on every other route. See
 * `backend/src/app/api/routes_reports.py`.
 *
 * @see backend/src/app/api/routes_reports.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** The four exports. The id is the ticket's subject and the filename's stem. */
export type ReportId = 'audit' | 'tenant' | 'budget' | 'forecast'

/** The query parameters an export may be narrowed by, mirroring the routes. */
export interface ReportFilters {
  /** Audit: ISO 8601 lower bound on the row timestamp. */
  since?: string
  /** Audit: ISO 8601 upper bound on the row timestamp. */
  until?: string
  /** Audit: exact actor. */
  actor?: string
  /** Audit: action prefix, e.g. `memory.`. */
  actionPrefix?: string
  /** Forecast: the tenant to project, for platform staff only. */
  tenantId?: number | null
  /** Forecast: `spend` or `calls`. */
  metric?: string
  /** Forecast: steps to project. */
  horizon?: number
  /** Forecast: the budget window the burn-down is against. */
  window?: string
}

/** What `POST /reports/tickets` returns. */
export interface ReportTicket {
  ticket: string
  report: ReportId
  expiresIn: number
}

/**
 * Build the download path for one report, with its filters.
 *
 * Exported (and unit-tested) rather than inlined because it is the one place the
 * console decides *what* an export contains: a filter dropped here is a file whose
 * contents quietly disagree with the screen it was downloaded from.
 */
export function reportDownloadPath(report: ReportId, filters: ReportFilters = {}): string {
  const path =
    report === 'audit'
      ? '/reports/audit.csv'
      : report === 'tenant'
        ? '/reports/tenant.csv'
        : report === 'budget'
          ? '/reports/budget.csv'
          : '/reports/forecast.csv'
  const query = new URLSearchParams()
  if (report === 'audit') {
    if (filters.since) query.set('since', filters.since)
    if (filters.until) query.set('until', filters.until)
    if (filters.actor) query.set('actor', filters.actor)
    if (filters.actionPrefix) query.set('actionPrefix', filters.actionPrefix)
  }
  if (report === 'forecast') {
    // `tenantId` is a request, never an authority: the server re-resolves the scope
    // from the caller's own token and refuses a tenant it may not read.
    if (filters.tenantId != null) query.set('tenant_id', String(filters.tenantId))
    if (filters.metric) query.set('metric', filters.metric)
    if (filters.horizon != null) query.set('horizon', String(filters.horizon))
    if (filters.window) query.set('window', filters.window)
  }
  const suffix = query.toString()
  return suffix ? `${path}?${suffix}` : path
}

/** Join the ticket to the download path — the URL the browser is actually sent to. */
export function ticketedUrl(path: string, ticket: string): string {
  const separator = path.includes('?') ? '&' : '?'
  return `${API_BASE}${path}${separator}ticket=${encodeURIComponent(ticket)}`
}

/**
 * Take the record away: mint a ticket, then let the browser save the file.
 *
 * The mint is the only `fetch` here — the download itself is a navigation, because a
 * navigation is what `Content-Disposition: attachment` needs and what streams to disk
 * without the page holding the export in memory. It is opened in a background tab
 * rather than by replacing the current location, so a refusal the ticket could not
 * anticipate (a session that expired between the two steps) renders in its own tab
 * instead of throwing away the console's state.
 *
 * @throws ApiError when the ticket is refused — the caller renders the server's own
 *   sentence rather than a generic failure.
 */
export async function startReportDownload(
  token: string | null,
  report: ReportId,
  filters: ReportFilters = {},
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}/reports/tickets`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ report }),
  })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    if (res.status === 401) reportSessionExpired()
    throw new ApiError(res.status, 'POST', '/reports/tickets', detail)
  }
  const { ticket } = (await res.json()) as ReportTicket
  const url = ticketedUrl(reportDownloadPath(report, filters), ticket)
  if (typeof window === 'undefined') return
  const opened = window.open(url, '_blank', 'noopener')
  if (opened === null) window.location.assign(url)
}
