import { WifiOff } from 'lucide-react'
import type { ReactElement } from 'react'

import { useBackendMode } from '@/state/backendMode'

/**
 * A persistent, unmistakable banner shown whenever the console is running on the
 * in-browser mock instead of a live backend — so a rehearsal or a fallback run
 * is never mistaken for real, measured data. Renders nothing in live mode.
 */
export function OfflineBanner(): ReactElement | null {
  const { mode, reason } = useBackendMode()
  if (mode !== 'mock') return null

  const detail =
    reason === 'forced-mock'
      ? 'Mock transport forced (rehearsal / offline). Set VITE_USE_MOCK=false to go live.'
      : 'Backend unreachable — showing scripted demo data. Start the backend and reload to go live.'

  return (
    <div
      role="status"
      className="flex items-center justify-center gap-2 bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
    >
      <WifiOff className="size-3.5 shrink-0" />
      <span className="font-mono tracking-wide uppercase">Offline demo — mock data</span>
      <span className="hidden text-white/85 sm:inline">· {detail}</span>
    </div>
  )
}
