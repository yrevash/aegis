// The probes moved to `@/lib/api/readiness` — only `web/src/lib/api` may call
// `fetch`, because route coverage is proved by walking the import graph from
// there. Re-exported so this module's consumers are unaffected.
// Imported as well as re-exported: `export … from` forwards the names to this
// module's consumers but does not bring them into its own scope, and the helpers
// below annotate against them.
import type { ReadyComponent } from '@/lib/api/readiness'

export {
  getReadyz,
  getLiveness,
  type ReadyComponent,
  type ReadyzResponse,
  type LivenessResponse,
} from '@/lib/api/readiness'

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
 * balancer cannot make, so both sit at {@link } rather than under `/v1`.
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


/**
 * One component's verdict.
 *
 * `unknown` is deliberately not `down`: a probe that timed out established nothing,
 * and drawing it as a refusal is a lie in the loud direction. Nothing in this module
 * or the components that read it maps `unknown` onto a failure tone.
 */
export type ComponentStatus = 'up' | 'down' | 'degraded' | 'unknown' | 'not_applicable'

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
/**
 * Narrow a wire status to one this build can rank, without pretending to know it.
 *
 * `status` is `string` on the wire on purpose: a newer backend can ship a verdict this
 * client has never heard of, and typing it as the union would be a claim we cannot keep.
 * An unrecognised code sorts and counts as `unknown`, which is already the "we have not
 * established anything" bucket — never as `up`, which would quietly upgrade a state we
 * do not understand into a healthy one.
 */
export function asStatus(value: string): ComponentStatus {
  return (['up', 'down', 'degraded', 'unknown', 'not_applicable'] as const).includes(
    value as ComponentStatus,
  )
    ? (value as ComponentStatus)
    : 'unknown'
}

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
        SEVERITY[asStatus(a.status)] - SEVERITY[asStatus(b.status)] ||
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
  for (const c of components) counts[asStatus(c.status)] += 1
  return counts
}

/**
 * The order the health strip reads in, left to right.
 *
 * Not severity order. The strip is read as *the platform*, and a platform is
 * mostly working: `up` anchors the left edge so the healthy mass keeps the same
 * position every time an operator glances at it, and the exceptions accumulate
 * from the right in the order they matter.
 */
export const VERDICT_ORDER = ['up', 'degraded', 'down', 'unknown', 'not_applicable'] as const

/** One segment of the health strip: a verdict, how many wear it, what share that is. */
export interface VerdictSlice {
  status: ComponentStatus
  count: number
  /** `count / components.length`, 0..1. */
  share: number
}

/**
 * The health strip's segments — the tally as widths, in {@link VERDICT_ORDER}.
 *
 * **Only verdicts that actually occurred get a segment.** A zero-width slice for
 * every state the platform is *not* in is the bar-chart spelling of zero-fill: it
 * puts a red key on the legend of a platform with nothing red in it, and after a
 * week of that an operator stops reading the legend. Nothing is invented and
 * nothing is padded — the shares are counts over the real denominator, so they
 * sum to 1 whenever there is anything to draw.
 */
export function verdictSplit(components: ReadyComponent[]): VerdictSlice[] {
  const total = components.length
  if (total === 0) return []
  const counts = tally(components)
  return VERDICT_ORDER.filter((status) => counts[status] > 0).map((status) => ({
    status,
    count: counts[status],
    share: counts[status] / total,
  }))
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

/**
 * Split a server-supplied origin into the identifier and the sentence after it.
 *
 * `GET /platform/caches` answers `source: "aegis.core.cache_stats — counters
 * incremented inside each cache"`. That is two different things joined by a dash:
 * the **identifier**, which is the receipt (§5), and an **explanation of the
 * mechanism**, which §4 sends to an `InfoTip`. Printed whole under a 230px triage
 * tile it wrapped to three lines of monospace and was the longest text on the card
 * whose subject is a hit rate.
 *
 * So the caller prints the identifier and passes the remainder as `detail`, which
 * {@link Receipt} already relocates to its own tooltip once it is longer than a
 * measured fact. **Nothing is dropped** — the sentence is one hover or one tab
 * away, in the treatment the whole console already uses for it.
 *
 * The split is on a spaced em or en dash only. A dash *inside* an identifier is
 * not spaced (`aegis.core.cache_stats`, `in_process_rolling_window`), and an
 * origin with no dash at all comes back whole with no note — never truncated.
 */
export function splitOrigin(source: string): { origin: string; note: string | null } {
  const match = /\s+[—–]\s+/.exec(source)
  if (!match) return { origin: source.trim(), note: null }
  return {
    origin: source.slice(0, match.index).trim(),
    note: source.slice(match.index + match[0].length).trim() || null,
  }
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
