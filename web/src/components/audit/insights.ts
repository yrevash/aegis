/**
 * The audit trail's analytics, as pure functions over the rows the server returned.
 *
 * Kept out of the component for the reason `dbView.ts` is: bucketing a time series,
 * choosing a grain from a span, and splitting a tally by outcome are all easy to get
 * subtly wrong and impossible to test through a rendered tree. `web/tests/audit/
 * insights.test.mjs` exercises them directly.
 *
 * **Everything here describes the rows in hand and nothing else.** `GET /audit` is
 * filtered and paged server-side (§7.11), so what comes back is *the newest `limit`
 * rows matching the filter* — not the whole trail. Every figure derived here is
 * therefore a statement about that set, and the screen says so in a `Receipt` rather
 * than letting a reader mistake a page for a population. That is also why nothing in
 * this file extrapolates, projects or annualises: there is no honest way to do it from
 * a truncated newest-first window.
 */

import type { AuditLogRow } from '@/lib/api/types'

/** One named thing in the trail, with how much of it was refused. */
export interface AuditTally {
  name: string
  total: number
  /** Rows in this group the server classified `blocked`. */
  blocked: number
}

/** The headline figures for a set of audit rows. */
export interface AuditPulse {
  /** Rows in hand. Never presented as "events in the trail". */
  total: number
  blocked: number
  completed: number
  /** Rows carrying a human approver — the real HITL trail, not a guess. */
  approved: number
  /** Distinct non-empty actors. */
  actors: number
  /** Distinct action names. */
  actions: number
  /** Distinct model deployments named on a row. */
  models: number
  /** Rows carrying a trace id, and the share of rows that do. */
  traced: number
  /** Blocked ÷ total, in [0, 1]. Zero when there are no rows. */
  refusalRate: number
  /** Rows carrying a trace id ÷ total, in [0, 1]. */
  traceRate: number
}

/**
 * The family an action belongs to — the segment before its first separator.
 *
 * The trail's vocabulary is namespaced two ways at once: `guardrail.input` and
 * `query.start` use a dot, `router:route` and `tool:<name>` use a colon. Splitting on
 * whichever comes first is what makes `tool:search_docs` and `tool:send_email` group
 * together as *tool calls* rather than appearing as two unrelated verbs.
 */
export function actionFamily(action: string): string {
  const dot = action.indexOf('.')
  const colon = action.indexOf(':')
  const cut =
    dot < 0 ? colon : colon < 0 ? dot : Math.min(dot, colon)
  return cut <= 0 ? action : action.slice(0, cut)
}

/**
 * The prefix that would re-select an action family through `GET /audit`.
 *
 * `action_prefix` is matched as a literal prefix by the server, so the separator has
 * to be kept: `guardrail` alone would also match a hypothetical `guardrails_v2`, and
 * a lens that quietly widens is worse than no lens.
 */
export function familyPrefix(action: string): string {
  const family = actionFamily(action)
  if (family === action) return action
  return action.slice(0, family.length + 1)
}

/** Count rows into named groups, largest first, carrying each group's refusals. */
export function tallyBy(
  rows: readonly AuditLogRow[],
  pick: (row: AuditLogRow) => string | null | undefined,
): AuditTally[] {
  const seen = new Map<string, AuditTally>()
  for (const row of rows) {
    const name = pick(row)
    if (name == null || name === '') continue
    const entry = seen.get(name) ?? { name, total: 0, blocked: 0 }
    entry.total += 1
    if (row.outcome === 'blocked') entry.blocked += 1
    seen.set(name, entry)
  }
  return [...seen.values()].sort((a, b) => b.total - a.total || a.name.localeCompare(b.name))
}

/** Distinct non-empty values of one field. */
function distinct(rows: readonly AuditLogRow[], pick: (row: AuditLogRow) => string | null | undefined): number {
  const seen = new Set<string>()
  for (const row of rows) {
    const value = pick(row)
    if (value != null && value !== '') seen.add(value)
  }
  return seen.size
}

/** The headline figures. Every one of them is a count of the rows in hand. */
export function auditPulse(rows: readonly AuditLogRow[]): AuditPulse {
  let blocked = 0
  let approved = 0
  let traced = 0
  for (const row of rows) {
    if (row.outcome === 'blocked') blocked += 1
    if (row.approved_by != null && row.approved_by !== '') approved += 1
    if (row.trace_id != null && row.trace_id !== '') traced += 1
  }
  const total = rows.length
  return {
    total,
    blocked,
    completed: total - blocked,
    approved,
    actors: distinct(rows, (r) => r.actor),
    actions: distinct(rows, (r) => r.action),
    models: distinct(rows, (r) => r.model),
    traced,
    refusalRate: total === 0 ? 0 : blocked / total,
    traceRate: total === 0 ? 0 : traced / total,
  }
}

/** One bucket of the activity trend. */
export interface TrendBucket {
  /** Wall-clock label for the bucket start, at the grain the span deserves. */
  label: string
  completed: number
  blocked: number
  total: number
}

/** The trend, plus what it is a trend *of*. */
export interface AuditTrend {
  buckets: TrendBucket[]
  /** The window the rows actually cover, as a sentence for the receipt. */
  grain: string
  /** Epoch ms of the oldest and newest row, or null when there are none. */
  from: number | null
  to: number | null
}

/** Bucket size, in ms, and how to label a bucket at that size. */
const HOUR = 3_600_000

function labeller(spanMs: number): { format: (at: Date) => string; grain: string } {
  if (spanMs <= 3 * HOUR) {
    return {
      format: (at) => at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      grain: 'minutes',
    }
  }
  if (spanMs <= 72 * HOUR) {
    return {
      format: (at) =>
        at.toLocaleString([], { day: '2-digit', hour: '2-digit', minute: '2-digit' }),
      grain: 'hours',
    }
  }
  return {
    format: (at) => at.toLocaleDateString([], { month: 'short', day: '2-digit' }),
    grain: 'days',
  }
}

/**
 * Bucket the rows into `count` equal slices of the window they themselves span.
 *
 * The window is taken from the data rather than from the clock, and that is the whole
 * point. A fixed "last 12 hours" axis against a filter that matched nothing in the
 * last 12 hours draws twelve empty columns and reads as *the system is idle*, when
 * what actually happened is that the reader asked about last Tuesday. Deriving the
 * axis from the oldest and newest row in hand means the chart always shows the shape
 * of the answer the server gave, and the receipt names the window it covers.
 *
 * Every bucket is present even at zero, so a quiet hour reads as a gap in a real
 * shape rather than being closed up.
 */
export function auditTrend(rows: readonly AuditLogRow[], count = 14): AuditTrend {
  const times: Array<{ at: number; blocked: boolean }> = []
  for (const row of rows) {
    const at = new Date(row.ts).getTime()
    if (Number.isFinite(at)) times.push({ at, blocked: row.outcome === 'blocked' })
  }
  if (times.length === 0) {
    return { buckets: [], grain: 'no rows', from: null, to: null }
  }

  const from = Math.min(...times.map((t) => t.at))
  const to = Math.max(...times.map((t) => t.at))
  const span = Math.max(to - from, 1)
  const slices = Math.max(1, count)
  const width = span / slices
  const { format, grain } = labeller(span)

  const buckets: TrendBucket[] = []
  for (let i = 0; i < slices; i += 1) {
    buckets.push({ label: format(new Date(from + i * width)), completed: 0, blocked: 0, total: 0 })
  }
  for (const t of times) {
    const index = Math.min(slices - 1, Math.floor((t.at - from) / width))
    const bucket = buckets[index]
    if (t.blocked) bucket.blocked += 1
    else bucket.completed += 1
    bucket.total += 1
  }
  return { buckets, grain, from, to }
}

/**
 * The window the rows cover, written for a person.
 *
 * Returned as a fragment for a {@link Receipt}, so the figures above it can never be
 * read as "all time" when they describe forty minutes.
 */
export function windowSentence(trend: AuditTrend): string {
  if (trend.from === null || trend.to === null) return 'no rows in this view'
  const from = new Date(trend.from)
  const to = new Date(trend.to)
  const sameDay = from.toDateString() === to.toDateString()
  const stamp = (at: Date, withDate: boolean): string =>
    withDate
      ? at.toLocaleString([], {
          month: 'short',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        })
      : at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  return `${stamp(from, true)} → ${stamp(to, !sameDay)}`
}

/** A percentage, rounded for display and never rounded up to 100 from below. */
export function percent(fraction: number): string {
  if (!Number.isFinite(fraction) || fraction <= 0) return '0%'
  if (fraction >= 1) return '100%'
  const value = fraction * 100
  if (value < 1) return '<1%'
  if (value > 99) return '>99%'
  return `${Math.round(value)}%`
}
