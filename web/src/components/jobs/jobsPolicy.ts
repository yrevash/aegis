/**
 * Who may *act* on the jobs queue — the one predicate the Jobs screen renders against.
 *
 * `lib/portal.ts` states the doctrine this file implements: *"The rule for adding a
 * section to a portal: that role must be able to act on it … it is a gap wearing a menu
 * entry, and it reads as capability the operator does not have."* The Jobs screen broke
 * that rule one level down — the section is on the platform operator's portal, and every
 * **write** on it was structurally impossible for that principal while still being drawn:
 *
 *   - `POST /jobs/{id}/requeue` and `/cancel` load the row as
 *     `WHERE id = :id AND tenant_id = :caller_tenant`, and for an untenanted caller that
 *     clause is `tenant_id IS NULL`. Every row a platform admin can *see* belongs to some
 *     tenant, so every one of those buttons returned 403 — *"job N is not this tenant's
 *     to re-queue (no such row under tenant None)"*.
 *   - `POST /documents` refuses outright: *"an upload needs an owning tenant"* (400),
 *     because `chunks.tenant_id` is NOT NULL.
 *
 * Neither route takes a tenant argument (`_scope_tenant(auth, None)` — there is no
 * `?tenant_id=` on either), so a tenant selector on this screen could not have supplied
 * the missing scope: it would send the identical request and collect the identical 403.
 * That is why direction (a) — do not render a control the guard makes impossible, and say
 * briefly why — is the one taken here, and (b) is not available without a backend change.
 *
 * The predicate is the principal's tenant pin, never their role name: `tenant_admin` and
 * `ai_team` reach this screen pinned to a tenant and keep every control, and if a
 * platform admin ever *is* given a tenant, the controls come back with no edit here.
 */

/**
 * Whether this session may run the queue's writes (re-queue, cancel, upload).
 *
 * @param tenantId The session's tenant pin — `null` for an untenanted principal
 *   (platform admin), which is exactly the case the backend guards refuse.
 */
export function canWriteJobs(tenantId: number | null): boolean {
  return tenantId !== null
}

/** The short reason the screen prints where the controls would have been. */
export const NO_TENANT_REASON = 'Read-only: no owning tenant'
