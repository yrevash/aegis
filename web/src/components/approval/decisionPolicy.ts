/**
 * Who may *decide* a gate — the one predicate the approval controls render against.
 *
 * `lib/portal.ts` states the doctrine: *"a portal must not offer a control the backend
 * guard makes impossible."* The live in-run gate broke it. `ApprovalCard` drew Approve
 * and Reject for whoever was looking, and the endpoint behind them —
 * `POST /approval`, and `POST /approvals/{id}/decision` beside it — is
 * `Depends(require_admin)`. A tenant's own analyst parked a gate from the console,
 * pressed Approve, and collected *"Run stopped. This action requires the admin role."*
 * with the run still parked: a button whose only possible outcome was a 403.
 *
 * The durable inbox never had this problem, because the server annotates every row it
 * returns with `decidable` / `blocked_reason` from `_decision_refusal` and the screen
 * renders that. The live gate arrives on the run stream, which carries no such
 * annotation — so the rule has to be readable in the browser, and this is the one place
 * it is written.
 *
 * The rule, transcribed from `backend/src/app/api/routes.py::_decision_refusal`:
 *
 *   - **A non-admin never decides.** Both decision endpoints are `require_admin`.
 *   - **Platform staff decide Aegis's own gates and nobody else's.** An un-tenanted
 *     gate belongs to the operator of the platform; a gate naming a tenant is that
 *     tenant's business decision.
 *   - **A tenant principal decides its own tenant's gates.**
 *
 * Note what this is *not*: a check that the session's `fineRole` string looks like an
 * admin's. It is the capability — may this principal commit this decision — assembled
 * from the same two facts the server assembles it from, in the same order. That is why
 * a `tenant_admin` keeps both controls on its own tenant's gate and a `platform_admin`
 * keeps them on Aegis's own, exactly as before: the fix takes the button away from the
 * principals the guard refuses, and from nobody else.
 *
 * One narrowing is deliberately **not** modelled here. `_require_seat(auth,
 * "seat.can_approve")` can revoke approval from an individual admin seat, and no
 * endpoint publishes seat rows to the browser. Those toggles default to *allowed* and
 * exist to take capability away from a principal who otherwise has it, so a revoked
 * seat still surfaces as the server's own refusal sentence on the card — one 403 for a
 * deliberately narrowed account, not one for every analyst in the product.
 *
 * Pure and framework-free, so the rule is testable without a renderer.
 */

import type { Session } from '@/lib/auth/AuthContext'

/** Why this principal may not decide a gate, as the sentence the card prints. */
export const NOT_AN_ADMIN =
  'Deciding a gate is an administrator’s action. You can see what happened to the ones you raised.'

/** Why the platform operator may not decide a gate a tenant raised. */
export const NOT_YOUR_TENANT =
  'This gate belongs to a tenant. A tenant’s own admin decides it.'

/** Why a tenant principal may not decide another tenant’s gate. */
export const ANOTHER_TENANT = 'This gate belongs to another tenant.'

/** Why a signed-out reader may not decide anything. */
export const NOT_SIGNED_IN = 'Sign in to decide this gate.'

/**
 * Why `session` may not decide a gate owned by `owner`, or `null` when it may.
 *
 * @param session - The signed-in principal, or `null`.
 * @param owner - The gate's owning tenant, or `null` for a gate Aegis itself raised.
 *   `null` means two different things on the two sides — "this principal belongs to no
 *   tenant" and "this gate belongs to no tenant" — and the ordering below is what keeps
 *   them from being equated, exactly as on the server.
 */
export function decisionRefusal(session: Session | null, owner: number | null): string | null {
  if (session === null) return NOT_SIGNED_IN
  // Ahead of any scope question, because it is the guard that actually runs first: a
  // client with no tenant has no tenant authority to resolve, and asking for one would
  // answer the wrong question about them.
  if (session.role !== 'admin') return NOT_AN_ADMIN
  if (session.tenantId == null) return owner == null ? null : NOT_YOUR_TENANT
  return owner === session.tenantId ? null : ANOTHER_TENANT
}

/**
 * Why `session` may not decide the gate its **own run** just parked at, or `null`.
 *
 * A live gate reaches the browser on that session's own `/query` stream, so its owner
 * is that session's tenant by construction — an un-tenanted principal's run raises an
 * un-tenanted gate, a tenant's run raises that tenant's. Passing the pin as the owner
 * is that fact written down, and it keeps the live card on the same predicate as the
 * inbox rather than a second, looser one.
 */
export function liveGateRefusal(session: Session | null): string | null {
  return decisionRefusal(session, session?.tenantId ?? null)
}
