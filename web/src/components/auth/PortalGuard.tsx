'use client'

import { useRouter } from 'next/navigation'
import { useEffect, type ReactNode } from 'react'

import { useAuth } from '@/lib/auth/AuthContext'
import { homePathFor, type Role } from '@/lib/portal'

/**
 * The frontend half of RBAC — the App Router equivalent of the Vite app's
 * `RequireRole`. Gates a portal to the signed-in role:
 *
 *   - no session            → redirect to `/login`
 *   - session.role ≠ portal  → redirect to the session role's own home
 *   - session.role = portal  → render the portal
 *
 * A session's role may ONLY reach its own portal, so a devops session that
 * navigates to `/app/ai_team/...` is bounced back to the devops home. The backend
 * still enforces scope on every request; this is defence-in-depth + UX.
 *
 * Redirects run in an effect (never during render) and the guard renders a
 * neutral placeholder until the stored session has hydrated, so a logged-in
 * operator is never briefly flashed to `/login` on a hard refresh.
 */
export function PortalGuard({ role, children }: { role: Role; children: ReactNode }): ReactNode {
  const { session, hydrated } = useAuth()
  const router = useRouter()

  const authed = session !== null
  const allowed = authed && session.role === role

  useEffect(() => {
    if (!hydrated) return
    if (!authed) {
      router.replace('/login')
    } else if (session.role !== role) {
      router.replace(homePathFor(session.role))
    }
  }, [hydrated, authed, session, role, router])

  if (!hydrated || !allowed) {
    return (
      <div className="flex min-h-dvh items-center justify-center text-sm text-muted-foreground">
        {hydrated ? 'Redirecting…' : 'Loading…'}
      </div>
    )
  }

  return children
}
