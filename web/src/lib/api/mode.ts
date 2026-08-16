/**
 * Backend-mode resolution — the live-first auto-probe with a labelled mock
 * fallback.
 *
 * On boot the app probes the backend once (see {@link probeBackend}). The pure
 * {@link decideMode} maps (force flag, probe outcome) → the resolved mode, so
 * the fallback logic is unit-testable with no network. The rest of the API layer
 * (`factory.ts`, `client.ts`) reads the cached result via {@link isMock}.
 */

import { API_BASE, FORCE_MOCK, HEALTH_PATH } from './config'

/** Which transport the app is using. */
export type BackendMode = 'live' | 'mock'

/** Why the current mode was chosen (drives the banner copy). */
export type ModeReason = 'forced-mock' | 'probe-live' | 'probe-failed'

/** The resolved backend mode plus the reason it was chosen. */
export interface ResolvedMode {
  mode: BackendMode
  reason: ModeReason
}

/**
 * Pure resolution: force flag + probe outcome → resolved mode.
 *
 * @param forceMock - Mock is explicitly forced (rehearsal/offline).
 * @param reachable - Probe result: `true` reachable, `false` unreachable, `null`
 *   not yet probed (optimistic live default so the app never flashes mock).
 */
export function decideMode(forceMock: boolean, reachable: boolean | null): ResolvedMode {
  if (forceMock) return { mode: 'mock', reason: 'forced-mock' }
  if (reachable === false) return { mode: 'mock', reason: 'probe-failed' }
  return { mode: 'live', reason: 'probe-live' }
}

/** The cached resolved mode; starts optimistic (or forced) before the probe. */
let resolved: ResolvedMode = decideMode(FORCE_MOCK, null)

/** Whether the app is currently using the mock transport. */
export function isMock(): boolean {
  return resolved.mode === 'mock'
}

/**
 * Probe the backend once and cache the resolved mode. Any HTTP-level `ok`
 * response means "reachable → live"; a network error, non-ok status, or timeout
 * means "unreachable → mock". A forced-mock config skips the probe entirely.
 */
export async function probeBackend(
  opts: { fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<ResolvedMode> {
  if (FORCE_MOCK) {
    resolved = decideMode(true, null)
    return resolved
  }
  const reachable = await pingBackend(opts.fetchImpl ?? fetch, opts.timeoutMs ?? 2500)
  resolved = decideMode(false, reachable)
  return resolved
}

/** GET the health path with a timeout; resolves to whether it answered ok. */
async function pingBackend(fetchImpl: typeof fetch, timeoutMs: number): Promise<boolean> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeoutMs)
  try {
    const res = await fetchImpl(`${API_BASE}${HEALTH_PATH}`, {
      method: 'GET',
      signal: ctrl.signal,
    })
    return res.ok
  } catch {
    return false
  } finally {
    clearTimeout(timer)
  }
}
