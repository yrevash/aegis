/**
 * The composer's budget line — what it may say, and what it must refuse to say.
 *
 * `GET /me/budget` reads the same `BudgetStatusRow`s the gateway compares every call
 * against, so the pill and a refusal cannot disagree. The field that decides what the
 * line renders is `measured`:
 *
 * - `measured: false` means **no cap governs this principal**. There is no denominator,
 *   so `$0.00 of $50 today` would be an invention and `$0.00 of $0.00` would read as a
 *   spent balance. The line says it is not measured.
 * - `measured: true` with a null `usd_cap` means the caps that govern this caller are
 *   token or rate caps, not a dollar cap. Spend is real and is shown; the "of $X" half
 *   is not, because there is no dollar ceiling to be a fraction of.
 *
 * A read that fails outright is the third case, and it is not the same as an unmeasured
 * one: the caller could not see their budget, which is a different sentence from having
 * no budget.
 *
 * Pure, so the refusal can be tested without a renderer.
 */

import type { MyBudgetResponse } from '@/lib/api/console'

/** What the composer draws under the box. */
export interface BudgetLine {
  /** The line itself, sentence case, already formatted. */
  text: string
  /** True only when a dollar cap exists and spend is a real fraction of it. */
  meterable: boolean
  /** Spend ÷ cap, clamped to 0–1. Meaningless unless {@link meterable}. */
  ratio: number
  /** True when the figure shown is a measurement rather than an absence. */
  measured: boolean
}

/** Two decimals, the way money is written on this screen. */
function money(value: number): string {
  return `$${value.toFixed(2)}`
}

/**
 * The budget line for one reading.
 *
 * @param budget - The response, or null when the read has not answered.
 * @param failed - True when the read answered with an error rather than not yet.
 */
export function budgetLine(budget: MyBudgetResponse | null, failed = false): BudgetLine {
  if (budget === null) {
    return {
      text: failed ? 'Budget unavailable' : 'Reading budget…',
      meterable: false,
      ratio: 0,
      measured: false,
    }
  }
  if (!budget.measured) {
    return {
      text: 'Spend not yet measured — no cap governs this account',
      meterable: false,
      ratio: 0,
      measured: false,
    }
  }
  const used = budget.cost_usd_used
  const cap = budget.usd_cap
  if (cap === null) {
    return {
      text: `${money(used)} spent · no dollar cap set`,
      meterable: false,
      ratio: 0,
      measured: true,
    }
  }
  return {
    text: `${money(used)} of ${money(cap)}`,
    meterable: true,
    ratio: cap > 0 ? Math.min(1, Math.max(0, used / cap)) : 0,
    measured: true,
  }
}
