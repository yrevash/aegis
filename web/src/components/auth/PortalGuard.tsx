'use client'

import { useRouter } from 'next/navigation'
import { useEffect, type ReactNode } from 'react'

import { useAuth } from '@/lib/auth/AuthContext'
import { homePathFor, type Portal } from '@/lib/portal'

/**
 * The frontend half of RBAC — the App Router equivalent of the Vite app's
 * `RequireRole`. Gates a portal to the signed-in session:
 *
 *   - no session                → redirect to `/login`
 *   - session.fineRole ≠ portal → redirect to the session's own home
 *   - session.fineRole = portal → render the portal
 *
 * **It compares the fine role, not the coarse one** (§7.2). The coarse `role` is
 * `admin` for both admin tiers, so comparing it would let a tenant admin walk into
 * the platform operator's portal — the nav would offer every tenant's screens and
 * the backend would refuse them one 403 at a time. `fineRole` is the value the JWT
 * actually carries, and a session persisted before it existed rehydrates as the
 * narrower `tenant_admin` (see `AuthContext.fallbackFineRole`), so the failure mode
 * is a portal too small rather than one too large.
 *
 * A session may ONLY reach its own portal, so a devops session that navigates to
 * `/app/ai_team/...` is bounced back to the devops home. The backend still enforces
 * scope on every request; this is defence-in-depth + UX.
 *
 * Redirects run in an effect (never during render) and the guard renders a
 * neutral placeholder until the stored session has hydrated, so a logged-in
 * operator is never briefly flashed to `/login` on a hard refresh.
 */
export function PortalGuard({
  portal,
  children,
}: {
  portal: Portal
  children: ReactNode
}): ReactNode {
  const { session, hydrated } = useAuth()
  const router = useRouter()

  const authed = session !== null
  const allowed = authed && session.fineRole === portal

  useEffect(() => {
    if (!hydrated) return
    if (!authed) {
      router.replace('/login')
    } else if (session.fineRole !== portal) {
      router.replace(homePathFor(session.fineRole))
    }
  }, [hydrated, authed, session, portal, router])

  if (!hydrated || !allowed) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-muted-foreground">
        {hydrated ? 'Redirecting…' : 'Loading…'}
      </div>
    )
  }

  return children
}
