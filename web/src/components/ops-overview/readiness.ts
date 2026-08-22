/**
 * The two unauthenticated probes the ops overview polls, and the vocabulary it
 * reads them with.
 *
 * **Why these two and not `/platform/health`.** `GET /readyz` is the probe a load
 * balancer holds this deployment to: it runs every dependency probe concurrently and
 * answers 200 only when no *required* component is down. That makes it the one place
 * where "is the platform healthy right now" is already decided by the server rather
 * than re-derived in a browser. `GET /health` adds the one thing `/readyz` does not
 * carry — the API process's own view of the durable job worker, and the product
 * version the operator is looking at.
 *
 * **Neither takes a bearer.** A liveness probe that needs a token is a probe a load
 * balancer cannot make, so both sit at {@link API_ORIGIN} rather than under `/v1`.
 * Nothing here reads the session.
 *
 * **`/readyz` answers 503 when it is not ready, and the body is the point.** A plain
 * `res.ok` check would throw away the exact response an operator needs most — the
 * failing list and the component detail that says how to fix it — and would render
 * the one interesting state as a network error. So 503 is an accepted status here,
 * and only a status outside `{200, 503}` or a dead socket is a failure.
 *
 * @see backend/src/app/api/routes_health.py
 * @see aegis/src/aegis/core/health.py
 */

import { API_ORIGIN } from '@/lib/api/config'

/**
 * One component's verdict.
 *
 * `unknown` is deliberately not `down`: a probe that timed out established nothing,
 * and drawing it as a refusal is a lie in the loud direction. Nothing in this module
 * or the components that read it maps `unknown` onto a failure tone.
 */
export type ComponentStatus = 'up' | 'down' | 'degraded' | 'unknown' | 'not_applicable'

/** One dependency, and the probe or query that produced its verdict. */
export interface ReadyComponent {
  key: string
  name: string
  /** `store` · `substrate` · `model` · `isolation`. */
  category: string
  status: ComponentStatus
  /** What the probe measured, in the server's own words. Often null. */
  detail: string | null
  /** The call or SQL behind the verdict. Never empty. */
  evidence: string
  measured_at: string
  /** Whether `/readyz` refuses traffic when this component is down. */
  required: boolean
}

/** Body of `GET /readyz` (200 when ready, 503 when a required component is down). */
export interface ReadyzResponse {
  /** `ready` | `not_ready`. */
  status: string
  /** Keys of the required components that are down. Empty when ready. */
  failing: string[]
  components: ReadyComponent[]
}

/** Body of `GET /health` — liveness plus the worker supervisor's own word. */
export interface LivenessResponse {
  status: string
  product: string
  version: string
  /** `running` | `down` | `starting` | `disabled` | `stopped`. */
  worker: string
}

/** GET a root probe, keeping the body on the statuses that carry one. */
async function probe<T>(path: string, alsoAccept: number[], signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_ORIGIN}${path}`, {
    method: 'GET',
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })
  if (!res.ok && !alsoAccept.includes(res.status)) {
    throw new Error(`The probe at ${path} answered HTTP ${res.status}, so this deployment's readiness is unknown.`)
  }
  return (await res.json()) as T
}

/** Every dependency's verdict with its evidence. 503 is a real answer, not a failure. */
export async function getReadyz(signal?: AbortSignal): Promise<ReadyzResponse> {
  return probe<ReadyzResponse>('/readyz', [503], signal)
}

/** Liveness, product version, and the job worker's supervisor state. */
export async function getLiveness(signal?: AbortSignal): Promise<LivenessResponse> {
  return probe<LivenessResponse>('/health', [], signal)
}

// ── Reading the payload ──────────────────────────────────────────────────────

/** The categories `/readyz` emits, in the order an operator reads them. */
export const CATEGORY_ORDER = ['store', 'substrate', 'model', 'isolation'] as const

/** Human heading per category; an unlisted one falls back to its own key. */
const CATEGORY_LABEL: Record<string, string> = {
  store: 'Stores',
  substrate: 'Substrate',
  model: 'Model plane',
  isolation: 'Isolation',
}

/** Heading for a category, without inventing one the server did not send. */
export function categoryLabel(category: string): string {
  return CATEGORY_LABEL[category] ?? category.replace(/_/g, ' ')
}

/** Severity order inside a group: what needs attention sits at the top. */
const SEVERITY: Record<ComponentStatus, number> = {
  down: 0,
  degraded: 1,
  unknown: 2,
  up: 3,
  not_applicable: 4,
}

/**
 * Group the components by category, in `CATEGORY_ORDER`, worst-first inside a group.
 *
 * The category order is fixed rather than sorted by severity: an operator who has
 * learned where Postgres lives should find it in the same place on the morning the
 * platform is broken, and the banner above the board is what carries the urgency.
 */
export function groupByCategory(
  components: ReadyComponent[],
): Array<{ category: string; rows: ReadyComponent[] }> {
  const seen = new Map<string, ReadyComponent[]>()
  for (const c of components) {
    const bucket = seen.get(c.category)
    if (bucket) bucket.push(c)
    else seen.set(c.category, [c])
  }
  const known = CATEGORY_ORDER.filter((c) => seen.has(c))
  const extra = [...seen.keys()].filter((c) => !(CATEGORY_ORDER as readonly string[]).includes(c)).sort()
  return [...known, ...extra].map((category) => ({
    category,
    // Worst verdict first, then the components `/readyz` will actually refuse traffic
    // for, then alphabetically — so an optional store never sits above a required one.
    rows: [...(seen.get(category) ?? [])].sort(
      (a, b) =>
        SEVERITY[a.status] - SEVERITY[b.status] ||
        Number(b.required) - Number(a.required) ||
        a.name.localeCompare(b.name),
    ),
  }))
}

/** How many components sit in each verdict — the board's one-line summary. */
export function tally(components: ReadyComponent[]): Record<ComponentStatus, number> {
  const counts: Record<ComponentStatus, number> = {
    up: 0,
    down: 0,
    degraded: 0,
    unknown: 0,
    not_applicable: 0,
  }
  for (const c of components) counts[c.status] += 1
  return counts
}

/**
 * The worker supervisor's five states, spelled out.
 *
 * `disabled` is **ready, not broken**: a deployment that never intended to run a
 * worker is not failing, and colouring it as a fault teaches an operator to ignore
 * the tile. Only `down` and `stopped` mean work is not being taken.
 */
export const WORKER_STATE: Record<string, { word: string; tone: 'ok' | 'risk' | 'block' | 'neutral'; line: string }> = {
  running: { word: 'Running', tone: 'ok', line: 'The supervisor holds a live worker, so queued jobs are being taken.' },
  starting: { word: 'Starting', tone: 'risk', line: 'Coming up — it is not taking work yet.' },
  disabled: { word: 'Disabled', tone: 'neutral', line: 'This deployment runs no worker by configuration — ready, not broken.' },
  stopped: { word: 'Stopped', tone: 'risk', line: 'It ran and was stopped, so nothing is taking queued jobs.' },
  down: { word: 'Down', tone: 'block', line: 'No worker is reachable, so no ingest can be started.' },
}

// ── Formatting ───────────────────────────────────────────────────────────────

/** Module-level, per DESIGN.md §4 — never a per-row `toLocaleString`. */
const TIME = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
const INTEGER = new Intl.NumberFormat('en-US')
const PERCENT = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 })

/** `12,481`. */
export function fmtInt(n: number): string {
  return INTEGER.format(n)
}

/** `39.4%`. Only ever called with a real ratio — a null rate is a stated absence. */
export function fmtPercent(ratio: number): string {
  return PERCENT.format(ratio)
}

/** `03:04:37` — wall clock, for a row whose age is not the point. */
export function fmtClock(iso: string): string {
  return TIME.format(new Date(iso))
}

/** `2.4s` / `740ms`, the same ladder the latency screen uses. */
export function fmtMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

/**
 * How long ago an instant was, from a caller-supplied `now`.
 *
 * `now` is passed in rather than read here so one interval drives every age on the
 * screen: eight components each owning a timer is eight renders a second, and they
 * would disagree by a tick.
 */
export function ago(iso: string, now: number): string {
  const seconds = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000))
  if (seconds < 5) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}
