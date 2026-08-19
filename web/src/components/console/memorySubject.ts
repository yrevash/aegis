/**
 * The memory subject this sign-in owns.
 *
 * `/memory/*` is keyed by a subject string and a non-admin may read only its own, which
 * the backend computes as `user:<id>` from the bearer's `sub` claim (see
 * `backend/src/app/adapter/memory_spec.py::memory_subject_for`). `POST /auth/login`
 * returns no user id, so the console reads the same claim out of the same token rather
 * than inventing a second source of truth or borrowing one from an unrelated endpoint.
 *
 * Nothing is verified here. The signature is the server's business and the server checks
 * it on every read; this only names the row the caller is already allowed to see, so a
 * tampered token buys a 403 rather than somebody else's memory.
 *
 * Returns `null` for a token with no `sub` — the back-compat demo principals that are
 * not backed by a `users` row. Memory genuinely is not scoped for those, and the rail
 * says so instead of reading an arbitrary subject.
 */

/** Decode one base64url JWT segment to text, or null if it is not decodable. */
function decodeSegment(segment: string): string | null {
  if (typeof atob !== 'function') return null
  try {
    const base64 = segment.replace(/-/g, '+').replace(/_/g, '/')
    const binary = atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '='))
    const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0))
    return new TextDecoder().decode(bytes)
  } catch {
    return null
  }
}

/**
 * The `user:<id>` subject the bearer owns, or null when the token carries no user id.
 *
 * @param token The session bearer, or null when signed out.
 */
export function memorySubjectFromToken(token: string | null): string | null {
  if (token === null || token === '') return null
  const parts = token.split('.')
  if (parts.length !== 3) return null
  const payload = decodeSegment(parts[1] ?? '')
  if (payload === null) return null
  let claims: unknown
  try {
    claims = JSON.parse(payload)
  } catch {
    return null
  }
  if (typeof claims !== 'object' || claims === null) return null
  const sub = (claims as { sub?: unknown }).sub
  if (typeof sub === 'string' && sub !== '') return `user:${sub}`
  if (typeof sub === 'number') return `user:${sub}`
  return null
}
