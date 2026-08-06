/**
 * Authentication context — holds the authenticated role + token and scopes the
 * app to a portal. Role-based routing is a security requirement (RBAC), not
 * only UX: `/admin` and `/app` are distinct, role-gated surfaces.
 *
 * The session is persisted to `localStorage` so a projector refresh mid-demo
 * does not drop the operator back to the login screen.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react'

import { login as apiLogin } from '@/api/client'
import type { Role } from '@/types/stream'

/** The current session, or null when signed out. */
export interface Session {
  role: Role
  token: string
  username: string
  /** Tenant this session is scoped to, or null (platform scope). */
  tenantId: number | null
}

interface AuthContextValue {
  session: Session | null
  signIn: (username: string, password: string) => Promise<Session>
  signOut: () => void
}

const STORAGE_KEY = 'aegis.session'

const AuthContext = createContext<AuthContextValue | null>(null)

function readStoredSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Session) : null
  } catch {
    return null
  }
}

/** Provides the auth session to the tree. */
export function AuthProvider({ children }: { children: ReactNode }): ReactNode {
  const [session, setSession] = useState<Session | null>(readStoredSession)

  const signIn = useCallback(async (username: string, password: string): Promise<Session> => {
    const { role, token, tenant_id } = await apiLogin({ username, password })
    const next: Session = { role, token, username, tenantId: tenant_id }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    setSession(next)
    return next
  }, [])

  const signOut = useCallback((): void => {
    localStorage.removeItem(STORAGE_KEY)
    setSession(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ session, signIn, signOut }),
    [session, signIn, signOut],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}

/** Access the auth session. Throws if used outside {@link AuthProvider}. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === null) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
