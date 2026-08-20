'use client'

import dynamic from 'next/dynamic'
import type { ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import type { Role } from '@/lib/stream'

// The console tree pulls in canvas (react-force-graph-2d) and chart libraries
// that reach for `window` at module scope, so mount the whole thing client-only.
// The backend probe in `BackendGate` also has to run in the browser before the
// first query, so a server render would be throwaway regardless.
const ChatConsole = dynamic(
  () => import('@/components/console/ChatConsole').then((m) => m.ChatConsole),
  {
    ssr: false,
    loading: () => (
      <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Loading console…
      </div>
    ),
  },
)

/**
 * Client entry for the Console section — gated on a reachable backend, so the
 * first `getGraph` / `getMetrics` calls only fire once one is known to answer.
 *
 * No `TooltipProvider` here. One is mounted at the app root in `auth/Providers`, and a
 * second provider around one screen is a nested Radix context that buys nothing and can
 * only ever disagree with the root's `delayDuration`.
 */
export function ConsoleMount({ role }: { role: Role }): ReactElement {
  return (
    <BackendGate>
      <ChatConsole role={role} />
    </BackendGate>
  )
}
