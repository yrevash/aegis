/**
 * Pure derivations for the Audit surface — result classification, header
 * counts, per-hour activity buckets, actor list and row filtering. Kept free of
 * any recharts import so the logic is unit-testable; the view renders these
 * figures.
 */

import type { AuditLogRow } from '@/lib/api/types'

/** A row's coarse result — either an action that says it was blocked, or done. */
export type AuditResult = 'blocked' | 'completed'

/**
 * Honest, action-derived result label. The backend has no verdict field, so a
 * row is only ever "blocked" when the action itself says so (a guardrail block
 * or a denied tool call); everything else is neutral. No fabricated state.
 */
export function resultOf(action: string): AuditResult {
  const a = action.toLowerCase()
  return a.startsWith('guardrail') || a.includes('block') || a.includes('denied')
    ? 'blocked'
    : 'completed'
}

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
    if (resultOf(r.action) === 'blocked') blocked += 1
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

/** Distinct, sorted actor names present in the rows (excludes null/blank). */
export function actorsOf(rows: AuditLogRow[]): string[] {
  const set = new Set<string>()
  for (const r of rows) if (r.actor) set.add(r.actor)
  return [...set].sort((a, b) => a.localeCompare(b))
}

/** Active filter for the audit table. `result: null` / `actor: null` = all. */
export interface AuditFilter {
  result: AuditResult | null
  actor: string | null
}

/** Apply a result + actor filter to the rows (pure). */
export function filterRows(rows: AuditLogRow[], filter: AuditFilter): AuditLogRow[] {
  return rows.filter((r) => {
    if (filter.result != null && resultOf(r.action) !== filter.result) return false
    if (filter.actor != null && r.actor !== filter.actor) return false
    return true
  })
}
