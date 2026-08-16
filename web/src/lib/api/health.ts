/**
 * Backend reachability — the boot probe every portal surface gates on.
 *
 * The console has no offline mode: with the backend down there is nothing honest
 * to draw, so each section mount probes once and renders the shared
 * "backend unavailable" state instead of an empty shell. The result is cached for
 * the page's lifetime because the probe answers a boot-time question (is there a
 * backend at all), not a per-request one — a call that fails later surfaces as
 * that call's own error, which is more precise than a stale global flag.
 */

import { API_BASE, HEALTH_PATH } from './config'

/** Whether the backend answered the boot probe. */
export type BackendStatus = 'reachable' | 'unreachable'

/** The cached probe result; `null` until the first probe resolves. */
let resolved: BackendStatus | null = null

/**
 * Probe the backend once and cache the outcome. Any HTTP-level `ok` response
 * means reachable; a network error, non-ok status, or timeout means unreachable.
 * Concurrent mounts share the cached result rather than each issuing a ping.
 */
export async function probeBackend(
  opts: { fetchImpl?: typeof fetch; timeoutMs?: number } = {},
): Promise<BackendStatus> {
  if (resolved !== null) return resolved
  const reachable = await pingBackend(opts.fetchImpl ?? fetch, opts.timeoutMs ?? 2500)
  resolved = reachable ? 'reachable' : 'unreachable'
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
