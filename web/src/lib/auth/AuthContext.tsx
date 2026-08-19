'use client'

/**
 * Authentication context — holds the authenticated session (role + JWT + user)
 * and scopes the app to a single portal. Role-based routing is an RBAC security
 * requirement, not only UX: each of the four portals is a distinct, role-gated
 * surface (see `PortalGuard`).
 *
 * Port of the Vite app's `frontend/src/auth/AuthContext.tsx`, adapted for the
 * Next.js App Router: the stored session is read in an effect (not the useState
 * initializer) so the server-rendered markup and the first client render agree
 * (no hydration mismatch), then `hydrated` flips true once localStorage is read.
 *
 * The session is persisted to `localStorage` so a projector refresh mid-demo does
 * not drop the operator back to the login screen. On every change the JWT is
 * mirrored into the API layer's token holder so live REST/SSE calls carry it.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import { login as apiLogin } from '@/lib/api/client'
import { setAuthToken } from '@/lib/api/authToken'
import type { FineRole } from '@/lib/api/types'
import type { Role } from '@/lib/stream'

/** The current session, or null when signed out. */
export interface Session {
  role: Role
  token: string
  username: string
  /** Tenant this session is scoped to, or null (platform scope). */
  tenantId: number | null
  /**
   * The fine RBAC tier from `POST /auth/login` — the only thing that separates a
   * platform admin (every tenant) from a tenant admin (pinned to one). `role` is
   * `admin` for both.
   */
  fineRole: FineRole
  /**
   * Who the caller is, from `POST /auth/login` — the same value the token carries as
   * its `sub` claim, and the identity `/memory/*` authorises a subject against.
   *
   * Separate from `tenantId`: a platform principal has no tenant and a real user id.
   * `null` only for the back-compat demo principals that are not backed by a `users`
   * row, and for a session stored before this field existed.
   */
  userId: number | null
}

/**
 * The fine tier to assume when a persisted session predates `fine_role` (a
 * localStorage session written by an older build). It fails **closed**: an admin
 * is treated as the narrower `tenant_admin`, so a stale session can never grant
 * platform-wide affordances it was never issued. The next sign-in carries the
 * real tier.
 */
function fallbackFineRole(role: Role): FineRole {
  return role === 'admin' ? 'tenant_admin' : role
}

interface AuthContextValue {
  session: Session | null
  /** Whether the stored session has been read from localStorage yet. */
  hydrated: boolean
  signIn: (username: string, password: string) => Promise<Session>
  signOut: () => void
}

const STORAGE_KEY = 'aegis.session'

const AuthContext = createContext<AuthContextValue | null>(null)

function readStoredSession(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const stored = JSON.parse(raw) as Partial<Session> & Pick<Session, 'role' | 'token' | 'username'>
    return {
      ...stored,
      tenantId: stored.tenantId ?? null,
      fineRole: stored.fineRole ?? fallbackFineRole(stored.role),
      // A session stored before `userId` existed rehydrates without one. Null is the
      // honest answer — the surfaces that need it withhold themselves and say why,
      // rather than guessing an id and asking the server to refuse it.
      userId: stored.userId ?? null,
    }
  } catch {
    return null
  }
}

/** Provides the auth session to the tree. */
export function AuthProvider({ children }: { children: ReactNode }): ReactNode {
  const [session, setSession] = useState<Session | null>(null)
  const [hydrated, setHydrated] = useState(false)

  // Read the persisted session once, client-side only, after the first paint.
  useEffect(() => {
    const stored = readStoredSession()
    setAuthToken(stored?.token ?? null)
    setSession(stored)
    setHydrated(true)
  }, [])

  const signIn = useCallback(async (username: string, password: string): Promise<Session> => {
    const { role, token, tenant_id, fine_role, user_id } = await apiLogin({ username, password })
    const next: Session = {
      role,
      token,
      username,
      tenantId: tenant_id,
      fineRole: fine_role,
      userId: user_id,
    }
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    } catch {
      // Non-fatal: session still lives in memory for this tab.
    }
    setAuthToken(token)
    setSession(next)
    return next
  }, [])

  const signOut = useCallback((): void => {
    try {
      localStorage.removeItem(STORAGE_KEY)
    } catch {
      // ignore
    }
    setAuthToken(null)
    setSession(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({ session, hydrated, signIn, signOut }),
    [session, hydrated, signIn, signOut],
  )

  return <AuthContext value={value}>{children}</AuthContext>
}

/** Access the auth session. Throws if used outside {@link AuthProvider}. */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (ctx === null) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
