/**
 * Runtime configuration for the API layer, read from Next public env vars.
 *
 * The console is **live-first**: with no configuration it targets the real
 * backend, auto-probes it on boot, and only falls back to the in-browser mock
 * transport — behind a clearly-labelled "offline demo" banner — when the backend
 * is unreachable. See `mode.ts` for the probe + resolution logic.
 *
 * Mock mode is an explicit, labelled fallback (never the silent default). Force
 * it for stage rehearsal / offline UI work with `NEXT_PUBLIC_USE_MOCK=true` or
 * the `?mock=1` query param.
 */

/** Base URL of the backend API (no trailing slash). Empty ⇒ same-origin. */
export const API_BASE: string = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/$/, '')

/** Path the boot probe hits to detect a reachable backend. */
export const HEALTH_PATH: string = process.env.NEXT_PUBLIC_HEALTH_PATH ?? '/health'

/** Read `?mock=1` from the URL, tolerating non-browser (SSR/test) environments. */
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
 * `NEXT_PUBLIC_USE_MOCK=true` or the `?mock=1` query param.
 */
export const FORCE_MOCK: boolean =
  process.env.NEXT_PUBLIC_USE_MOCK === 'true' || mockQueryParam()
