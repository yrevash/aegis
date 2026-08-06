/**
 * Route guard — gates a portal to a specific authenticated role.
 *
 * Unauthenticated users are sent to `/login`; a user who reaches the wrong
 * portal is redirected to the one their role owns. This is the frontend half of
 * RBAC; the backend still enforces scope on every request.
 */

import { Navigate } from 'react-router-dom'
import type { ReactNode } from 'react'

import { useAuth } from '@/auth/AuthContext'
import type { Role } from '@/types/stream'

/** Home path for a given role — each of the four roles owns a distinct portal. */
export function homePathFor(role: Role): string {
  switch (role) {
    case 'admin':
      return '/admin'
    case 'ai_team':
      return '/ai-team'
    case 'devops':
      return '/devops'
    case 'client':
      return '/client'
  }
}

/** Renders `children` only if the session role matches `role`. */
export function RequireRole({ role, children }: { role: Role; children: ReactNode }): ReactNode {
  const { session } = useAuth()
  if (session === null) return <Navigate to="/login" replace />
  if (session.role !== role) return <Navigate to={homePathFor(session.role)} replace />
  return children
}
