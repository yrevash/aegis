/**
 * Derivations for the **native** analytics path — the one that does not need
 * Superset.
 *
 * Analytics had exactly one source: `board_data()` is
 * `return await self._live_client().board_data(...)`, so with Superset off the
 * screen had nothing to draw and said so in a dashed rectangle. The rectangle was
 * honest and the page was dead.
 *
 * Every function here reads the usage ledger through `GET /admin/usage`, which is
 * already in the client and already RBAC-scoped, and none of them invents a
 * point: a profile bucket with no rows is absent rather than zero, and a rate is
 * only computed where its denominator was actually reported.
 */

import { toDaily, type ModelSpend, type SpendPoint } from '@/components/dashboard/adminOverview'

/** One bucket of a profile — a named slot and the spend recorded in it. */
export interface ProfileBucket {
  label: string
  cost: number
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as const

/**
 * Mean spend per calendar day, by day of week.
 *
 * The mean, not the total: a 30-day window holds four or five of some weekdays
 * and four of others, so totals encode how the window happened to be cut as much
 * as they encode behaviour. Dividing by the number of that weekday actually
 * present makes the seven bars comparable, which is the only reason to draw them
 * side by side.
 *
 * A weekday with no day in the window is dropped, not plotted at zero — zero is a
 * claim that nothing was spent, and "not observed" is a different claim.
 */
export function weekdayProfile(series: readonly SpendPoint[]): ProfileBucket[] {
  const sums = new Array<number>(7).fill(0)
  const counts = new Array<number>(7).fill(0)
  for (const { day, cost } of toDaily(series)) {
    const t = Date.parse(`${day}T00:00:00Z`)
    if (Number.isNaN(t)) continue
    // `getUTCDay()` is Sunday-first; the labels read Monday-first.
    const idx = (new Date(t).getUTCDay() + 6) % 7
    sums[idx] += cost
    counts[idx] += 1
  }
  return WEEKDAYS.map((label, i) => ({ label, cost: counts[i] > 0 ? sums[i] / counts[i] : NaN }))
    .filter((b) => Number.isFinite(b.cost))
}

/**
 * Total spend by hour of the UTC day, across the whole window.
 *
 * Hours with no ledger bucket at all are omitted rather than drawn flat, for the
 * same reason: the ledger buckets hourly and only writes a bucket that has rows,
 * so an absent hour is an hour nobody used, and the gap says that more precisely
 * than a zero-height bar does.
 */
export function hourProfile(series: readonly SpendPoint[]): ProfileBucket[] {
  const sums = new Map<number, number>()
  for (const p of series) {
    if (!Number.isFinite(p.cost_usd)) continue
    const hour = Number(String(p.ts).slice(11, 13))
    if (!Number.isInteger(hour) || hour < 0 || hour > 23) continue
    sums.set(hour, (sums.get(hour) ?? 0) + p.cost_usd)
  }
  return [...sums.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([hour, cost]) => ({ label: `${String(hour).padStart(2, '0')}h`, cost }))
}

/** One model's unit economics: what a thousand of its tokens cost. */
export interface UnitCost {
  model: string
  usdPer1kTokens: number
}

/**
 * Cost per 1,000 tokens, per model, measured rather than quoted.
 *
 * This is the one figure on the page a price list cannot give you: it is the
 * blended rate the platform *actually paid*, which moves with the prompt/completion
 * split of the real traffic. Models with no tokens in the window are excluded —
 * an audio or image deployment bills on seconds and frames, and dividing its spend
 * by a token count of zero would either divide by zero or, worse, report a rate
 * from a denominator that does not describe it.
 */
export function unitCosts(rows: readonly ModelSpend[]): UnitCost[] {
  return rows
    .filter((r) => Number.isFinite(r.tokens) && r.tokens > 0 && Number.isFinite(r.cost_usd))
    .map((r) => ({ model: r.model, usdPer1kTokens: (r.cost_usd / r.tokens) * 1000 }))
    .sort((a, b) => b.usdPer1kTokens - a.usdPer1kTokens)
}

/** Total tokens across every model in the window. */
export function totalTokens(rows: readonly ModelSpend[]): number {
  return rows.reduce((sum, r) => sum + (Number.isFinite(r.tokens) ? r.tokens : 0), 0)
}
