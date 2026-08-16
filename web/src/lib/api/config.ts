/**
 * Runtime configuration for the API layer, read from Next public env vars.
 *
 * The console is **live-only**: every figure it draws is measured by the backend,
 * and there are no fixtures to fall back on. With no configuration it targets the
 * real backend on the same origin; `health.ts` probes it on boot so a surface with
 * no backend says so rather than rendering an empty shell.
 */

/** Base URL of the backend API (no trailing slash). Empty ⇒ same-origin. */
export const API_BASE: string = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/$/, '')

/** Path the boot probe hits to detect a reachable backend. */
export const HEALTH_PATH: string = process.env.NEXT_PUBLIC_HEALTH_PATH ?? '/health'
