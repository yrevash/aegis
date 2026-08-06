/**
 * Pure roll-up helpers for the Governance overview strip. Side-effect free and
 * free of any recharts import so the derivations stay unit-testable (see
 * `governance.test.ts`) without pulling the chart library into the test graph.
 */

import type { AdminUser, Budget } from '@/types/api'

/** How many users are currently enabled. */
export function activeUserCount(users: AdminUser[]): number {
  return users.reduce((n, u) => (u.is_active ? n + 1 : n), 0)
}

/**
 * Total monthly USD cap across every month-window budget that sets one. Returns
 * `null` when no month budget carries a USD cap, so the caller can honestly show
 * "no cap set" rather than a fabricated 0.
 */
export function monthUsdCapTotal(budgets: Budget[]): number | null {
  let total = 0
  let seen = false
  for (const b of budgets) {
    if (b.window === 'month' && b.usd_cap != null) {
      total += b.usd_cap
      seen = true
    }
  }
  return seen ? total : null
}

/**
 * Fraction of the month USD cap already spent, clamped to `[0, 1]`. Returns
 * `null` when there is no cap to measure against (never divides by zero, never
 * invents a utilisation).
 */
export function budgetUtilisation(spendUsd: number, capTotal: number | null): number | null {
  if (capTotal == null || capTotal <= 0) return null
  return Math.max(0, Math.min(1, spendUsd / capTotal))
}
