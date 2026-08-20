'use client'

import type { ReactNode } from 'react'

import { TooltipProvider } from '@/components/primitives/tooltip'
import { AuthProvider } from '@/lib/auth/AuthContext'

/**
 * Client provider island mounted at the root so the whole app — every portal
 * route and the login page — shares one auth session.
 *
 * **`TooltipProvider` is here now, and it was not before.** `primitives/InfoTip`'s
 * own documentation claimed a root provider was already mounted; there wasn't one,
 * so every screen that used an `InfoTip` had to remember to wrap itself. Radix does
 * not degrade when the context is missing — it throws `Tooltip must be used within
 * TooltipProvider` and takes the whole screen down. Two redesign lanes hit it on
 * four screens between them, which is the shape of a defect that is one line to fix
 * and unbounded to keep rediscovering.
 *
 * Nesting a second provider under this one is harmless, so the ~20 screens that
 * already wrap themselves keep working untouched and can shed the wrapper whenever
 * they are next edited.
 */
export function Providers({ children }: { children: ReactNode }): ReactNode {
  return (
    <AuthProvider>
      <TooltipProvider delayDuration={150}>{children}</TooltipProvider>
    </AuthProvider>
  )
}
