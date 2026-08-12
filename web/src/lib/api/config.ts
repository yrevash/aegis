/**
 * Runtime configuration for the API layer, read from Next public env vars.
 *
 * Live-first: with no configuration the client targets the same-origin backend.
 * (A mock/live probe like the Vite app's api/mode.ts is a follow-up task; this
 * scaffold ships the live-only typed client + SSE decoder.)
 */

/** Base URL of the backend API (no trailing slash). Empty ⇒ same-origin. */
export const API_BASE: string = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/$/, '')

/** Path the boot probe hits to detect a reachable backend. */
export const HEALTH_PATH: string = process.env.NEXT_PUBLIC_HEALTH_PATH ?? '/health'
