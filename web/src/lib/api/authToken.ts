/**
 * Module-level bearer-token holder.
 *
 * The auth session (see `lib/auth/AuthContext`) mirrors its JWT here on sign-in /
 * sign-out so the REST client ({@link request}) and the live SSE transport can
 * attach `Authorization: Bearer <token>` on every live call — even where a view
 * component was written before auth existed and still passes `token={null}`.
 *
 * A per-call `token` argument always wins; this holder is only the fallback. A
 * null holder simply sends no `Authorization` header, which the backend answers
 * with a 401 the calling surface reports.
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
