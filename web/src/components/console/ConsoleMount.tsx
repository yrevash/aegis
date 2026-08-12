'use client'

import dynamic from 'next/dynamic'
import { WifiOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { TooltipProvider } from '@/components/primitives/tooltip'
import type { Role } from '@/lib/stream'

// The console tree pulls in canvas (react-force-graph-2d) and chart libraries
// that reach for `window` at module scope, so mount the whole thing client-only.
// The boot probe (below) also has to run in the browser before the first query,
// so a server render would be throwaway regardless.
const MoneyShotConsole = dynamic(
  () => import('@/components/console/MoneyShotConsole').then((m) => m.MoneyShotConsole),
  {
    ssr: false,
    loading: () => (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Loading console…
      </div>
    ),
  },
)

/**
 * Honest offline banner — shown whenever the console resolved to the in-browser
 * mock instead of a live backend, so a rehearsal / fallback run is never mistaken
 * for real, measured data. Mirrors the Vite app's OfflineBanner.
 */
function OfflineBanner({ reason }: { reason: ResolvedMode['reason'] }): ReactElement {
  const detail =
    reason === 'forced-mock'
      ? 'Mock transport forced (rehearsal / offline). Unset NEXT_PUBLIC_USE_MOCK / drop ?mock=1 to go live.'
      : 'Backend unreachable — showing scripted demo data. Start the backend and reload to go live.'
  return (
    <div
      role="status"
      className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
    >
      <WifiOff className="size-3.5 shrink-0" />
      <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
      <span className="hidden text-white/85 sm:inline">· {detail}</span>
    </div>
  )
}

/**
 * Client entry for the Console section. Runs the boot probe once (live-first,
 * mock fallback), then mounts the money-shot. Gating the mount on the probe means
 * the first `getGraph` / `getMetrics` calls read the resolved mode, so the
 * offline demo seeds its base graph + efficiency numbers from the mock fixtures.
 */
export function ConsoleMount({ role }: { role: Role }): ReactElement {
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div>
        {mode.mode === 'mock' && <OfflineBanner reason={mode.reason} />}
        <MoneyShotConsole role={role} />
      </div>
    </TooltipProvider>
  )
}
