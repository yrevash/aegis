/**
 * Backend-mode React context — runs the boot probe once and exposes the
 * resolved mode to the tree so the UI can render the labelled offline banner.
 *
 * The probe result is cached module-side in `api/mode.ts` (which `factory.ts`
 * and `client.ts` read synchronously), so REST/transport calls made after
 * `ready` is true see the same resolution the banner shows. Until the probe
 * resolves the app renders optimistically as live (never a mock flash).
 */

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

import { getResolvedMode, probeBackend, type ResolvedMode } from '@/api/mode'

interface BackendModeValue extends ResolvedMode {
  /** Whether the boot probe has completed. */
  ready: boolean
}

const BackendModeContext = createContext<BackendModeValue | null>(null)

/** Provides the resolved backend mode; probes the backend once on mount. */
export function BackendModeProvider({ children }: { children: ReactNode }): ReactNode {
  const [state, setState] = useState<BackendModeValue>(() => ({ ...getResolvedMode(), ready: false }))

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setState({ ...resolved, ready: true })
    })
    return () => {
      alive = false
    }
  }, [])

  const value = useMemo(() => state, [state])
  return <BackendModeContext value={value}>{children}</BackendModeContext>
}

/** Read the resolved backend mode. Throws outside {@link BackendModeProvider}. */
export function useBackendMode(): BackendModeValue {
  const ctx = useContext(BackendModeContext)
  if (ctx === null) throw new Error('useBackendMode must be used within BackendModeProvider')
  return ctx
}
