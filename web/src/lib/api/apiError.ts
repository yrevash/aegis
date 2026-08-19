/**
 * What a failed backend call means to the person who triggered it.
 *
 * Every REST helper in this folder used to throw `new Error("GET /memory/facts?subject=
 * user%3A5 failed: 403 Forbidden")`, and that string reached the screen. It names the
 * route, the query string and the HTTP status — three facts about the *transport* — and
 * not one about what the reader should now do. An audit found it rendered verbatim in
 * the memory rail and under the composer.
 *
 * So a failure carries two things from here on, and they are kept apart on purpose:
 *
 * - `message` is a **sentence**: what happened, and what to do about it. It is the only
 *   thing a surface should render.
 * - `status`, `method` and `path` stay as fields, for the code that has to *decide*
 *   something — the 401 sign-out, the memory rail withholding a card it may not read —
 *   and for the console log line, where the route genuinely is the useful fact.
 *
 * The server's own `detail` wins when it sent one, because a backend that bothered to
 * explain a refusal ("this account is not bound to a tenant") is more specific than
 * anything this table can say. The table is the floor, not the ceiling.
 *
 * Pure and framework-free: `web/tests/api/apiError.test.mjs` exercises it directly.
 */

/** HTTP statuses that mean the bearer is no longer good — the session, not the request. */
const EXPIRED = 401

/** HTTP status that means the bearer is good but not allowed to read this. */
const REFUSED = 403

/**
 * The sentence a reader gets for one HTTP status.
 *
 * Active voice, sentence case, and every one of them ends by naming the next move — a
 * failure a person cannot act on is a failure they will read as a bug in the console.
 *
 * @param status - The HTTP status the backend answered with.
 * @returns One sentence naming what happened and what to do.
 */
export function apiMessage(status: number): string {
  if (status === EXPIRED) return 'Your session has expired. Sign in again to continue.'
  if (status === REFUSED) return 'This sign-in is not allowed to read that.'
  if (status === 404) return 'The backend does not serve that. Check it is up to date.'
  if (status === 409) return 'That conflicts with something already saved. Reload and try again.'
  if (status === 413) return 'That is too large for the backend to accept. Send less.'
  if (status === 422) return 'The backend refused those values. Check what was sent.'
  if (status === 429) return 'The backend is rate-limiting this account. Wait a moment, then retry.'
  if (status >= 500) return 'The backend failed on that request. Check its logs, then retry.'
  return 'That request did not go through. Try it again.'
}

/**
 * A failed backend call, carrying a sentence to render and the facts to act on.
 *
 * Extends `Error` so every existing `catch (err: unknown)` that reads `err.message`
 * keeps working — and starts showing a sentence instead of a route.
 */
export class ApiError extends Error {
  /** The HTTP status, or `0` when the request never reached the backend. */
  readonly status: number
  /** The HTTP method, for the log line. */
  readonly method: string
  /** The path, query string included, for the log line. */
  readonly path: string

  constructor(status: number, method: string, path: string, message?: string) {
    super(message ?? apiMessage(status))
    this.name = 'ApiError'
    this.status = status
    this.method = method
    this.path = path
  }

  /** The transport facts, for a console log — never for the screen. */
  get route(): string {
    return `${this.method} ${this.path}`
  }
}

/**
 * Whether a failure means this sign-in will never be allowed the reading.
 *
 * The memory rail asks this to decide whether to **withhold a card** rather than render
 * a refusal inside it. A 500 or a dropped connection is not this: that reading is
 * absent right now, not absent for this person, and a card that vanishes on a backend
 * hiccup teaches the wrong thing about what the agent knows.
 *
 * @param error - Whatever was caught.
 * @returns True for 401 and 403 — the two refusals about the caller, not the request.
 */
export function isAuthFailure(error: unknown): boolean {
  const status = statusOf(error)
  return status === EXPIRED || status === REFUSED
}

/**
 * Whether a failure means the bearer itself is finished.
 *
 * A 12-hour JWT and a console left open overnight land exactly here, and until this
 * existed the header went on showing a username and a Sign out button over a session
 * that could not read anything.
 *
 * @param error - Whatever was caught.
 * @returns True only for 401.
 */
export function isExpiredSession(error: unknown): boolean {
  return statusOf(error) === EXPIRED
}

/** The HTTP status a caught failure carries, or `0` when it carries none. */
export function statusOf(error: unknown): number {
  if (error !== null && typeof error === 'object' && 'status' in error) {
    const status = (error as { status: unknown }).status
    if (typeof status === 'number') return status
  }
  return 0
}
