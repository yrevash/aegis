'use client'

import { PlugZap } from 'lucide-react'
import { useEffect, useState, type ReactElement, type ReactNode } from 'react'

import { probeBackend, type BackendStatus } from '@/lib/api/health'
import { API_BASE } from '@/lib/api/config'

/**
 * The one honest "there is no backend" state, shared by every portal surface.
 *
 * The console has no fixtures and no offline mode: a screen either shows what the
 * backend measured or says plainly that it could not reach it. Rendering an empty
 * shell, a spinner that never resolves, or a grid of zeros would all read as data
 * — so a failed probe stops here instead, naming the address that was tried so the
 * fix is obvious.
 */
export function BackendUnavailable({ detail }: { detail?: string }): ReactElement {
  return (
    <div
      role="status"
      className="flex min-h-[420px] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-surface-2/40 px-6 py-10 text-center"
    >
      <PlugZap className="size-8 text-muted-foreground/50" />
      <div className="max-w-md">
        <p className="text-sm font-medium text-foreground">Backend unavailable</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {detail ??
            'This surface renders measured data only. Start the Aegis backend and reload — nothing here is simulated while it is down.'}
        </p>
        <p className="mt-2 font-mono text-[0.7rem] text-muted-foreground/80">
          {API_BASE === '' ? 'same-origin API' : API_BASE}
        </p>
      </div>
    </div>
  )
}

/**
 * Probe the backend once, then render `children` only if it answered.
 *
 * Every section mount shares this gate so the reachability check, the connecting
 * state and the unavailable state are defined once rather than re-implemented per
 * surface. The probe runs in the browser (it is a network call), which is also why
 * the gate is a client component.
 */
export function BackendGate({ children }: { children: ReactNode }): ReactElement {
  const [status, setStatus] = useState<BackendStatus | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setStatus(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (status === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }
  if (status === 'unreachable') return <BackendUnavailable />
  return <>{children}</>
}
