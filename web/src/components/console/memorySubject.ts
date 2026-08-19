/**
 * The memory subject this sign-in owns.
 *
 * `/memory/*` is keyed by a subject string, and a non-admin may read only its own — the
 * backend computes it as `user:<id>` from the caller's identity (see
 * `backend/src/app/adapter/memory_spec.py::memory_subject_for`). The id comes from
 * `POST /auth/login`'s `user_id`, which is the same value the token carries as its `sub`
 * claim, so there is exactly one source of truth for who the caller is.
 *
 * An earlier version of this file decoded the `sub` claim out of the bearer token in the
 * browser, because the login response did not carry a user id. That worked and was not a
 * security hole — the server verifies the signature on every read, so a tampered token
 * buys a 403 rather than somebody else's memory — but it was a second way of learning the
 * same fact, and it would have drifted the day the server changed how a subject is
 * derived. The login response carries the id now; this reads it.
 *
 * `memory_subject_for` is an **adapter seam**: a domain that scopes memory to a business
 * entity rather than to the person would change it, and this must change with it. That is
 * the one coupling worth knowing about here.
 */

/** The shape this needs from a session: the caller's own id, or null when unknown. */
export interface MemoryIdentity {
  userId: number | null
}

/**
 * The `user:<id>` subject this session owns, or `null` when it owns none.
 *
 * `null` is a real answer rather than a failure. The back-compat demo principals are not
 * backed by a `users` row, and a session stored before `userId` existed rehydrates
 * without one. Memory genuinely is not scoped for either, so the rail withholds its
 * memory cards and says why instead of reading an arbitrary subject.
 *
 * @param session The signed-in session, or null when signed out.
 */
export function memorySubjectOf(session: MemoryIdentity | null): string | null {
  if (session === null) return null
  const { userId } = session
  if (userId === null || !Number.isInteger(userId) || userId <= 0) return null
  return `user:${userId}`
}
