/**
 * Runtime configuration for the API layer, read from Next public env vars.
 *
 * The console is **live-only**: every figure it draws is measured by the backend,
 * and there are no fixtures to fall back on. With no configuration it targets the
 * real backend on the same origin; `health.ts` probes it on boot so a surface with
 * no backend says so rather than rendering an empty shell.
 *
 * **Two bases, because the backend serves two kinds of thing** (§8.6). Every product
 * route lives under `/v1` — the version boundary that lets the HTTP API promise
 * anything at all — while `/health`, `/ready` and `/readyz` stay at the root, because
 * a liveness probe that moves when the API version moves is a probe that starts
 * 404-ing during a rollout. {@link API_BASE} carries the version segment so the ~60
 * call sites that write `${API_BASE}/metrics` moved with one edit rather than sixty;
 * {@link API_ORIGIN} is the unversioned root, and only the boot probe and the links to
 * FastAPI's own `/docs` use it.
 */

/** Root of the backend deployment (no trailing slash, no version). Empty ⇒ same-origin. */
export const API_ORIGIN: string = (process.env.NEXT_PUBLIC_API_BASE ?? '').replace(/\/$/, '')

/** The version segment every product route is served under. */
export const API_VERSION = 'v1'

/** Base URL of the **versioned** backend API (no trailing slash). */
export const API_BASE: string = `${API_ORIGIN}/${API_VERSION}`

/**
 * Path the boot probe hits to detect a reachable backend, joined onto
 * {@link API_ORIGIN} rather than {@link API_BASE}: it is infrastructure, not product.
 */
export const HEALTH_PATH: string = process.env.NEXT_PUBLIC_HEALTH_PATH ?? '/health'
