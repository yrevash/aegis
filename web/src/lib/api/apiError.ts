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

/**
 * The server's own reason for a refusal, flattened to one sentence.
 *
 * {@link ApiError} has always promised that "the server's own `detail` wins when it
 * sent one", and until the admin forms landed nothing read the failure body, so the
 * promise was only true of errors constructed by hand. It matters most exactly where
 * the backend is most specific: `A tenant-admin may only create users in its own
 * tenant.`, `A tenant-admin may only set budgets for its own tenant.`, `Tenant
 * 'Acme' already exists.` Replacing any of those with "The backend refused those
 * values" throws away the isolation story at the moment it is showing its work.
 *
 * FastAPI sends `detail` in two shapes and both have to survive the trip:
 *
 * - a **string**, from an explicit `HTTPException` — the interesting case;
 * - a **list of validation errors**, from Pydantic — `[{loc, msg, …}]`, which is
 *   joined field-first so `usd_cap: Input should be greater than 0` reads as an
 *   instruction rather than as a stack trace.
 *
 * @param body - The parsed failure body, or anything at all.
 * @returns One sentence from the server, or null when it sent nothing usable.
 */
export function serverDetail(body: unknown): string | null {
  if (body === null || typeof body !== 'object') return null
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === 'string') return sentence(detail)
  if (!Array.isArray(detail)) return null

  const parts: string[] = []
  for (const item of detail) {
    if (item === null || typeof item !== 'object') continue
    const msg = (item as { msg?: unknown }).msg
    if (typeof msg !== 'string' || msg.trim() === '') continue
    const loc = (item as { loc?: unknown }).loc
    const field = Array.isArray(loc)
      ? loc.filter((p) => typeof p === 'string' && p !== 'body').join('.')
      : ''
    parts.push(field === '' ? msg.trim() : `${field}: ${msg.trim()}`)
  }
  return parts.length === 0 ? null : sentence(parts.join('; '))
}

/** Trim a server string and give it a full stop, so it reads as one sentence. */
function sentence(raw: string): string | null {
  const text = raw.trim()
  if (text === '') return null
  return /[.!?]$/.test(text) ? text : `${text}.`
}

/**
 * The sentence to put on the screen for anything at all that was caught.
 *
 * `catch (e) { setError(e instanceof Error ? e.message : 'Failed to load stack') }`
 * appears more than twenty times across the console, and every one of those
 * fallbacks is a different string — "Failed to load seats", "Is the backend
 * running?", "Patch check failed". They are the shape DESIGN.md §7 forbids: a
 * console's own guess, standing in front of whatever the server actually said.
 *
 * This narrows anything to the one sentence a reader should see. The server's
 * own words win whenever they exist, because {@link ApiError} has already put
 * them in `message`; the fallback is only for the cases that are not errors at
 * all — a rejected promise carrying a string, or an object with nothing in it.
 *
 * @param error - Whatever was caught.
 * @param fallback - The sentence for a failure carrying no words of its own.
 *   Name what did not happen and what to do; never "something went wrong".
 * @returns One sentence, always non-empty.
 */
export function errorSentence(
  error: unknown,
  fallback = 'That request did not go through. Try it again.',
): string {
  if (error instanceof Error) {
    const message = error.message.trim()
    if (message !== '') return message
  }
  if (typeof error === 'string' && error.trim() !== '') return error.trim()
  return fallback
}

/**
 * Log one failed backend call, at a level proportionate to whose failure it is.
 *
 * Two things this fixes, both reported from a real console.
 *
 * The facts go in the **message**, never in a second object argument. Next.js's dev
 * overlay renders the message and prints an object argument as `{}`, so the previous
 * line surfaced as `[aegis] request failed {}` — naming neither the route nor the
 * status in the one place a developer actually reads it.
 *
 * And the level is graded. A refusal the screen handles on purpose — a tenant-pinned
 * account opening a platform-wide panel — is not a fault, and logging it as a red
 * console error makes a working boundary look like a broken product. Only a failure the
 * backend owns (5xx) is an error; a refusal is a warning, still visible, not alarming.
 *
 * @param route - `"GET /v1/…"`, from {@link ApiError.route}.
 * @param status - The HTTP status the backend answered with.
 * @param detail - The server's own sentence, when it sent one.
 */
export function logRequestFailure(route: string, status: number, detail?: string): void {
  const line = `[aegis] ${route} → ${status}${detail ? `: ${detail}` : ''}`
  if (status >= 500) console.error(line)
  else console.warn(line)
}
