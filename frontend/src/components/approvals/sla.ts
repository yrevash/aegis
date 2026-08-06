/**
 * Pure SLA-countdown formatting for the approvals inbox. Given a deadline and a
 * clock, returns display text + an urgency band so the inbox can colour the
 * countdown (and flag overdue rows) deterministically. Unit-testable — the UI
 * only supplies `now` from a ticking clock (see `sla.test.ts`).
 */

/** Urgency band for a countdown. */
export type SlaUrgency = 'none' | 'ok' | 'warn' | 'overdue'

/** A formatted SLA countdown. */
export interface SlaReadout {
  text: string
  urgency: SlaUrgency
  /** Milliseconds remaining (negative when overdue), or null when no deadline. */
  ms: number | null
}

/** Warn threshold: under five minutes remaining. */
const WARN_MS = 5 * 60_000

/** Format a positive millisecond span as a compact human duration. */
function formatSpan(ms: number): string {
  const totalSec = Math.floor(ms / 1000)
  const h = Math.floor(totalSec / 3600)
  const m = Math.floor((totalSec % 3600) / 60)
  const s = totalSec % 60
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

/**
 * Resolve an SLA countdown for a deadline relative to `now`.
 *
 * @param deadlineIso - ISO 8601 deadline, or null (no SLA).
 * @param now - Current epoch ms (defaults to `Date.now()`).
 */
export function slaCountdown(deadlineIso: string | null, now: number = Date.now()): SlaReadout {
  if (!deadlineIso) return { text: 'no SLA', urgency: 'none', ms: null }
  const t = new Date(deadlineIso).getTime()
  if (Number.isNaN(t)) return { text: 'no SLA', urgency: 'none', ms: null }
  const ms = t - now
  if (ms <= 0) return { text: 'overdue', urgency: 'overdue', ms }
  return {
    text: `${formatSpan(ms)} left`,
    urgency: ms < WARN_MS ? 'warn' : 'ok',
    ms,
  }
}
