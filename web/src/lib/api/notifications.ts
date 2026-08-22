/**
 * The alert feed — the console's own inbox, and the live socket that fills it.
 *
 * A faithful TypeScript mirror of the four `/v1/notifications*` routes:
 *
 * ```
 * GET  /notifications?unread_only=<bool>&limit=<int>  -> { rows, unread }
 * POST /notifications/{id}/read                       -> { id, read }
 * POST /notifications/read-all                        -> { marked }
 * GET  /notifications/stream                          -> SSE, event "notification"
 * ```
 *
 * **The stream is read with a `fetch` reader, not `EventSource`.** Not a style
 * preference: `EventSource` cannot send an `Authorization` header, and every route on
 * this backend is bearer-authenticated — the browser API would send an anonymous GET
 * and take a 401 forever. The frame parsing is {@link readSSEStream}, the same one the
 * run console has used since phase 2, so there is exactly one implementation of the
 * CRLF-vs-LF frame splitting that has already broken this product once.
 *
 * **An absent endpoint is an absence, not a crash.** While the backend route did not
 * exist this module answered 404, and the bell renders with no count and the panel says
 * so. Nothing here invents a row.
 */

import { ApiError, serverDetail } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'
import { readSSEStream } from './sse'

/**
 * One notification.
 *
 * `kind` and `severity` are **strings, not unions**, and that is deliberate. The
 * console shipped a hand-written `GuardVerdict` union that was missing one of the
 * backend's four values, and the first `flag` on the wire took a whole screen down
 * behind an error boundary — a value TypeScript believed impossible arriving anyway.
 * The producer here is a separate service adding kinds on its own schedule, so this
 * type does not claim to know them all; {@link severityTone} maps what it recognises
 * and gives everything else the neutral treatment.
 */
export interface NotificationRow {
  id: string
  /** e.g. `job.succeeded`, `job.failed`, `approval.awaiting`, `budget.exceeded`. */
  kind: string
  /** `info` | `warning` | `critical` — see the note above on why this is not a union. */
  severity: string
  title: string
  body: string
  /** The entity the alert is about (e.g. `job:412`), or null. */
  entity_ref: string | null
  /** In-app path to open, or null when the alert has nowhere to go. */
  href: string | null
  /** ISO 8601. */
  created_at: string
  /** ISO 8601 when it has been read, else null. */
  read_at: string | null
}

/** Response from `GET /notifications`. */
export interface NotificationsResponse {
  rows: NotificationRow[]
  /** Unread across the whole feed — **not** across `rows`, which the limit truncates. */
  unread: number
}

/** Response from `POST /notifications/{id}/read`. */
export interface NotificationReadResponse {
  id: string
  read: boolean
}

/** Response from `POST /notifications/read-all`. */
export interface NotificationReadAllResponse {
  marked: number
}

/** Where a subscription currently is. Rendered as a line in the panel, never hidden. */
export type StreamStatus = 'connecting' | 'live' | 'retrying' | 'closed'

/** Everything a subscriber gets told. */
export interface NotificationStreamHandlers {
  /** One notification arrived on the socket. */
  onNotification: (row: NotificationRow) => void
  /** The socket changed state. Called on every transition, including repeats of `retrying`. */
  onStatus?: (status: StreamStatus) => void
}

/** A live subscription. `close()` is idempotent and cancels any pending retry. */
export interface NotificationSubscription {
  close: () => void
}

/** First reconnect wait. Nothing retries faster than this — see {@link retryDelayMs}. */
const FIRST_RETRY_MS = 1_000

/** Ceiling on the reconnect wait. A backend that is down for an hour is polled 120 times. */
const MAX_RETRY_MS = 30_000

/**
 * How long a stream has to survive before its failure counts as a fresh outage.
 *
 * Resetting the attempt counter the moment the socket *opens* is the subtle version of a
 * tight loop: a backend that accepts the connection and immediately drops it would be
 * reconnected once a second, for ever, and every one of those is a real request. The
 * counter only resets once a connection has actually been useful for this long.
 */
const HEALTHY_MS = 30_000

/**
 * The wait before reconnect attempt `attempt` (0-based), exponential with jitter.
 *
 * Jitter is a multiplier in `[0.8, 1.2)` rather than an additive spread, so the floor
 * scales with the delay and the first retry can never round down to zero — the property
 * `tests/notifications/notificationStream.test.mjs` pins, because "reconnects" and
 * "reconnects in a loop that hammers a backend that is already unwell" are one typo
 * apart.
 *
 * @param attempt - Consecutive failures so far; 0 is the first retry.
 * @param random - Injectable for tests. Defaults to `Math.random`.
 */
export function retryDelayMs(attempt: number, random: () => number = Math.random): number {
  // `Math.max(0, NaN)` is `NaN`, and `NaN` propagates all the way to a `setTimeout`
  // delay — which the platform then treats as 0. That is the tight loop, arrived at
  // through arithmetic rather than through a bad constant.
  const counted = Number.isFinite(attempt) ? Math.trunc(attempt) : 0
  const steps = Math.min(Math.max(0, counted), 16)
  const base = Math.min(MAX_RETRY_MS, FIRST_RETRY_MS * 2 ** steps)
  return Math.round(base * (0.8 + 0.4 * random()))
}

/** Whether a decoded frame is really a notification, and not some other event's payload. */
export function isNotificationRow(value: unknown): value is NotificationRow {
  if (typeof value !== 'object' || value === null) return false
  const row = value as Record<string, unknown>
  return (
    typeof row.id === 'string' &&
    typeof row.kind === 'string' &&
    typeof row.title === 'string' &&
    typeof row.created_at === 'string'
  )
}

/** One authenticated JSON call against the feed, carrying the server's own refusal. */
async function call<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
  const method = init.method ?? 'GET'
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  let res: Response
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  } catch {
    // Nothing answered. `TypeError: Failed to fetch` names a browser API and not the
    // thing that is wrong, and the bell is chrome on every screen — it must not put
    // that string in front of an operator on every page.
    throw new ApiError(0, method, path, 'The backend stopped answering.')
  }
  if (!res.ok) {
    const detail = await res
      .json()
      .then(serverDetail)
      .catch(() => null)
    if (res.status === 401) reportSessionExpired()
    // The server's own sentence wins everywhere in this console — except when it is
    // FastAPI's `{"detail": "Not Found"}`, which is the status line spelled out and not
    // a refusal anybody wrote. The bell is chrome on every screen, so a route that is
    // not deployed would otherwise put the word "Not Found." in front of an operator on
    // every page, saying nothing about what is missing or what to do.
    const useless = res.status === 404 && (detail === 'Not Found.' || detail === 'Not Found')
    throw new ApiError(res.status, method, path, useless ? undefined : (detail ?? undefined))
  }
  return (await res.json()) as T
}

/**
 * Read the feed, newest first.
 *
 * @param token - Bearer, or null to use the signed-in session's.
 * @param opts.unreadOnly - Ask for only the unread rows.
 * @param opts.limit - How many rows; the `unread` count in the response is not limited.
 */
export async function getNotifications(
  token: string | null,
  opts: { unreadOnly?: boolean; limit?: number } = {},
): Promise<NotificationsResponse> {
  const params = new URLSearchParams()
  if (opts.unreadOnly === true) params.set('unread_only', 'true')
  params.set('limit', String(opts.limit ?? 30))
  return call<NotificationsResponse>(`/notifications?${params.toString()}`, { method: 'GET' }, token)
}

/** Mark one notification read. */
export async function markNotificationRead(
  token: string | null,
  id: string,
): Promise<NotificationReadResponse> {
  return call<NotificationReadResponse>(
    `/notifications/${encodeURIComponent(id)}/read`,
    { method: 'POST' },
    token,
  )
}

/** Mark every notification read, and report how many that was. */
export async function markAllNotificationsRead(
  token: string | null,
): Promise<NotificationReadAllResponse> {
  return call<NotificationReadAllResponse>('/notifications/read-all', { method: 'POST' }, token)
}

/**
 * Subscribe to the live feed, reconnecting with backoff for as long as the caller
 * keeps the subscription open.
 *
 * The loop is deliberately unconditional: a 404 (the route not deployed yet), a 503
 * (the backend restarting) and a dropped socket are all "not right now, ask again
 * later", and the caller has no way to distinguish them that would justify giving up
 * permanently. What stops it is `close()` — which the bell calls on sign-out and on
 * unmount, so a signed-out session holds no socket.
 *
 * A 401 is the exception: the bearer is dead, so it is reported to the session (which
 * signs the console out, which unmounts the bell, which closes this).
 */
export function subscribeNotifications(
  token: string | null,
  handlers: NotificationStreamHandlers,
): NotificationSubscription {
  let closed = false
  let attempt = 0
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | null = null

  const status = (next: StreamStatus): void => {
    if (!closed || next === 'closed') handlers.onStatus?.(next)
  }

  const connect = async (): Promise<void> => {
    const bearer = token ?? getAuthToken()
    const headers = new Headers({ Accept: 'text/event-stream' })
    if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
    controller = new AbortController()
    const res = await fetch(`${API_BASE}/notifications/stream`, {
      method: 'GET',
      headers,
      cache: 'no-store',
      signal: controller.signal,
    })
    if (res.status === 401) reportSessionExpired()
    if (!res.ok || res.body === null) {
      throw new ApiError(res.ok ? 0 : res.status, 'GET', '/notifications/stream')
    }
    const openedAt = Date.now()
    status('live')
    try {
      await readSSEStream<unknown>(
        res.body,
        (event) => {
          // The stream is multiplexed and {@link readSSEStream} discards the `event:`
          // name, so the payload is narrowed here instead. This is not hypothetical:
          // the backend opens every connection with `event: ready`, whose data is
          // `{"mode": "redis" | "in-process"}` — the transport reporting itself. Without
          // the guard that first frame would prepend a card with no title and no id.
          if (isNotificationRow(event)) handlers.onNotification(event)
        },
        controller.signal,
      )
    } finally {
      // A connection that lasted is evidence the backend is well; one that died on
      // arrival is not, and its failure keeps the previous attempt's place in the
      // backoff ladder. See {@link HEALTHY_MS}.
      if (Date.now() - openedAt >= HEALTHY_MS) attempt = 0
    }
  }

  const schedule = (): void => {
    if (closed) return
    const wait = retryDelayMs(attempt)
    attempt += 1
    status('retrying')
    timer = setTimeout(run, wait)
  }

  const run = (): void => {
    if (closed) return
    status(attempt === 0 ? 'connecting' : 'retrying')
    connect()
      .then(() => schedule())
      .catch(() => schedule())
  }

  run()

  return {
    close(): void {
      if (closed) return
      closed = true
      if (timer !== null) clearTimeout(timer)
      timer = null
      controller?.abort()
      controller = null
      status('closed')
    },
  }
}
