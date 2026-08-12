'use client'

import type { ReactNode } from 'react'

import { AuthProvider } from '@/lib/auth/AuthContext'

/**
 * Client provider island mounted at the root so the whole app — every portal
 * route and the login page — shares one auth session.
 */
export function Providers({ children }: { children: ReactNode }): ReactNode {
  return <AuthProvider>{children}</AuthProvider>
}
