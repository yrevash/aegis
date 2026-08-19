/**
 * Pure derivations for the Audit surface — header counts and per-hour activity
 * buckets over the rows the server returned. Kept free of any recharts import so the
 * logic is unit-testable; the view renders these figures.
 *
 * **Filtering does not live here any more.** It moved to the server (§7.11,
 * `components/audit/query.ts`), because narrowing an already-fetched page cannot answer
 * a question about anything older than that page. What remains is arithmetic over what
 * came back, which is honestly what these figures are.
 */

import type { AuditLogRow, AuditOutcome } from '@/lib/api/types'

/**
 * A row's coarse result. Classified **by the server** and carried on the row, so the
 * word rendered here is the same word `?outcome=` selected by — re-deriving it in the
 * browser is exactly how the label and the filter would come to disagree.
 */
export type AuditResult = AuditOutcome

/** Header tallies: total events loaded, blocked actions, and approved actions. */
export interface AuditCounts {
  total: number
  blocked: number
  /** Rows carrying a human approver — the real approval trail, not a guess. */
  approved: number
}

/** Count events, blocked results, and rows that went through a human approver. */
export function auditCounts(rows: AuditLogRow[]): AuditCounts {
  let blocked = 0
  let approved = 0
  for (const r of rows) {
    if (r.outcome === 'blocked') blocked += 1
    if (r.approved_by != null && r.approved_by !== '') approved += 1
  }
  return { total: rows.length, blocked, approved }
}

/** One hourly activity bucket for the header sparkline / bar. */
export interface HourBucket {
  /** `HH:00` label for the bucket start (local time). */
  hour: string
  count: number
}

/**
 * Bucket rows into the `hours` clock-hours ending at the newest row (or now when
 * empty). Every bucket is present even at zero so the bar reads as a real shape
 * over time rather than a jagged set of only-active hours.
 */
export function eventsPerHour(rows: AuditLogRow[], hours = 12): HourBucket[] {
  const times = rows
    .map((r) => new Date(r.ts).getTime())
    .filter((t) => Number.isFinite(t))
  const end = times.length > 0 ? Math.max(...times) : Date.now()
  const HOUR = 3_600_000
  const endHour = Math.floor(end / HOUR) * HOUR
  const start = endHour - (hours - 1) * HOUR

  const buckets: HourBucket[] = []
  for (let i = 0; i < hours; i += 1) {
    const bucketStart = start + i * HOUR
    const label = new Date(bucketStart).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    buckets.push({ hour: label, count: 0 })
  }
  for (const t of times) {
    const idx = Math.floor((t - start) / HOUR)
    if (idx >= 0 && idx < hours) buckets[idx].count += 1
  }
  return buckets
}

/** Serialise the (filtered) rows to a CSV string for export/download. */
export function rowsToCsv(rows: AuditLogRow[]): string {
  const esc = (v: unknown): string => {
    const s = v == null ? '' : String(v)
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
  }
  const head = ['time', 'action', 'actor', 'model', 'trace_id', 'approved_by', 'result']
  const lines = rows.map((r) =>
    [r.ts, r.action, r.actor, r.model, r.trace_id, r.approved_by, r.outcome].map(esc).join(','),
  )
  return [head.join(','), ...lines].join('\n')
}
