/**
 * Fine-tier predicates — the single place the browser decides what an *admin*
 * may see.
 *
 * The coarse `role` is `admin` for both admin tiers, so it cannot answer the one
 * question every per-tenant surface asks: is this operator scoped to the whole
 * platform, or pinned to a single tenant? `fine_role` (backend §3.3, derived by
 * `aegis.governance.security.principal_role` and echoed on `POST /auth/login`)
 * is what answers it. Deriving the answer a second time from `role` + `tenantId`
 * would be a second source of truth that can disagree with the JWT the request
 * actually carries, so every helper here reads `fineRole` and nothing else.
 *
 * @see backend/src/app/api/schemas.py `LoginResponse.fine_role`
 * @see aegis/src/aegis/governance/security.py `principal_role`
 */

import type { Session } from '@/lib/auth/AuthContext'

/**
 * Whether the session is the global operator: every tenant, no tenant pin.
 *
 * Signed out is `false` — the affordance is granted, never assumed.
 */
export function isPlatformAdmin(session: Session | null): boolean {
  return session?.fineRole === 'platform_admin'
}

/** Whether the session is an admin pinned to exactly one tenant. */
export function isTenantAdmin(session: Session | null): boolean {
  return session?.fineRole === 'tenant_admin'
}

/**
 * The honest scope caption for an admin surface.
 *
 * The backend pins a tenant admin to its own tenant (`_scope_tenant`), so the
 * rows a tenant admin sees are a subset — captioning both tiers identically
 * would present one tenant's figures as the platform's.
 */
export function adminScopeCaption(session: Session | null): string {
  if (isPlatformAdmin(session)) return 'platform scope · every tenant'
  if (isTenantAdmin(session)) {
    return session?.tenantId != null
      ? `tenant scope · tenant #${session.tenantId} only`
      : 'tenant scope · your tenant only'
  }
  return 'scoped to your session'
}
