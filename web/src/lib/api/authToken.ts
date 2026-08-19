/**
 * Module-level bearer-token holder, and the seam a dead bearer reports through.
 *
 * The auth session (see `lib/auth/AuthContext`) mirrors its JWT here on sign-in /
 * sign-out so the REST client ({@link request}) and the live SSE transport can
 * attach `Authorization: Bearer <token>` on every live call — even where a view
 * component was written before auth existed and still passes `token={null}`.
 *
 * A per-call `token` argument always wins; this holder is only the fallback. A
 * null holder simply sends no `Authorization` header, which the backend answers
 * with a 401 the calling surface reports.
 *
 * ## Why the expiry handler lives here and not in a hook
 *
 * The JWT lasts 12 hours, so any console left open overnight wakes up with a bearer the
 * backend refuses. Before this, the header went on showing the username and a Sign out
 * button while every read 401'd — a signed-out console painted as a signed-in one, which
 * is the worst of both, because the person is looking at affordances that cannot work.
 *
 * A 401 can arrive from anywhere: a REST helper, the SSE transport, a panel that fires
 * on mount. None of them are inside the React tree that owns the session, and threading
 * a callback through every one of them would be a parameter on forty functions. So this
 * module — which already is the one place that knows the bearer — takes one handler,
 * registered by {@link AuthProvider}, and every transport reports through it.
 */

let authToken: string | null = null

/** Set (or clear) the current session's bearer token. */
export function setAuthToken(token: string | null): void {
  authToken = token
}

/** The current session's bearer token, or null when signed out. */
export function getAuthToken(): string | null {
  return authToken
}

/** What runs when the backend refuses the bearer. Registered by the auth provider. */
let onSessionExpired: (() => void) | null = null

/**
 * Register what happens when a call comes back 401 — signing out, in the real app.
 *
 * @param handler - Called once per expiry, or `null` to unregister.
 */
export function setSessionExpiredHandler(handler: (() => void) | null): void {
  onSessionExpired = handler
}

/**
 * Report that the backend refused the bearer.
 *
 * Idempotent by construction: it clears the held token *before* calling the handler, so
 * a burst of 401s from six panels mounting at once signs out once rather than six times,
 * and a 401 arriving when nobody is signed in does nothing at all.
 *
 * @returns True when this call was the one that ended the session.
 */
export function reportSessionExpired(): boolean {
  if (authToken === null) return false
  authToken = null
  onSessionExpired?.()
  return true
}
