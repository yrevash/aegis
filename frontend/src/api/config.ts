/**
 * Runtime configuration for the API layer, read from Vite env vars.
 *
 * The console is **live-first**: with no configuration it targets the real
 * backend, auto-probes it on boot, and only falls back to the in-browser mock
 * transport — behind a clearly-labelled "offline demo" banner — when the backend
 * is unreachable. See `api/mode.ts` for the probe + resolution logic.
 *
 * Mock mode is an explicit, labelled fallback (never the silent default). Force
 * it for stage rehearsal / offline UI work with `VITE_USE_MOCK=true` or `?mock=1`.
 */

/** Base URL of the backend API (no trailing slash). */
export const API_BASE: string = (import.meta.env.VITE_API_BASE ?? '').replace(/\/$/, '')

/** Path the boot probe hits to detect a reachable backend. */
export const HEALTH_PATH: string = import.meta.env.VITE_HEALTH_PATH ?? '/health'

/** Read `?mock=1` from the URL, tolerating non-browser (test) environments. */
function mockQueryParam(): boolean {
  try {
    if (typeof window === 'undefined') return false
    return new URLSearchParams(window.location.search).get('mock') === '1'
  } catch {
    return false
  }
}

/**
 * Whether mock mode is explicitly forced (rehearsal / offline). This is the ONLY
 * way to get mock without a failed probe — the default is live. Set via
 * `VITE_USE_MOCK=true` or the `?mock=1` query param.
 */
export const FORCE_MOCK: boolean =
  import.meta.env.VITE_USE_MOCK === 'true' || mockQueryParam()

/**
 * Legacy flag kept for compatibility. Now defaults to **false** (live-first);
 * it is true only when mock is explicitly forced. Prefer `isMock()` from
 * `api/mode.ts`, which also reflects the boot probe's fallback.
 */
export const USE_MOCK: boolean = FORCE_MOCK
