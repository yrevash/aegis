/**
 * How the composer prices the effective routing table.
 *
 * `GET /models` is a projection over the gateway's own `routing_table()`, joined with
 * the cost table beside it. The one thing it does *not* do is normalise the unit: a
 * transcription role bills per minute of audio and an image role bills per image, and
 * `input_cost_usd` is "one input unit" in whatever unit `billing_unit` names. So a
 * single "per 1k tokens" column across all rows would be wrong on two rows out of
 * three — it would print a per-minute rate under a per-token heading.
 *
 * That is the whole reason this module exists and is pure: the formatting rule is the
 * claim, so it is testable without a renderer.
 *
 * `output_cost_usd_per_1k` is always per 1k completion tokens, and is `0` for roles
 * that emit no text at all. Zero here means "this role does not bill for output",
 * which is not the same as "output is free", so it is omitted rather than drawn as
 * `$0.00`.
 */

import type { ModelRow } from '@/lib/api/console'

/** What one input unit is called, in the unit the role actually bills in. */
const INPUT_UNIT: Record<string, string> = {
  tokens: 'per 1k prompt tokens',
  audio_minutes: 'per audio minute',
  images: 'per image',
}

/**
 * Render a USD rate at enough precision to be true.
 *
 * Gateway rates run to four decimal places and several are below a cent, so the
 * two-decimal currency format every other figure on this screen uses would print
 * `$0.00` for a rate that is not zero. Trailing zeros are trimmed so the common
 * rates stay readable.
 */
export function usd(rate: number): string {
  if (!Number.isFinite(rate)) return '—'
  if (rate === 0) return '$0'
  const fixed = rate.toFixed(rate < 0.01 ? 5 : 4)
  return `$${fixed.replace(/0+$/, '').replace(/\.$/, '')}`
}

/** The name of the unit a role's input rate is charged per. */
export function inputUnitLabel(billingUnit: string): string {
  return INPUT_UNIT[billingUnit] ?? `per ${billingUnit.replace(/_/g, ' ')}`
}

/**
 * One row's price, in that row's own billing unit.
 *
 * Returns one or two clauses: the input rate always, and the completion rate only
 * when the role bills for one.
 */
export function priceClauses(row: ModelRow): string[] {
  const clauses = [`${usd(row.input_cost_usd)} ${inputUnitLabel(row.billing_unit)}`]
  if (row.output_cost_usd_per_1k > 0) {
    clauses.push(`${usd(row.output_cost_usd_per_1k)} per 1k completion tokens`)
  }
  return clauses
}

/** A gateway role name as a person reads it: `answer_synthesis` → `Answer synthesis`. */
export function roleLabel(role: string): string {
  const words = role.replace(/[_-]+/g, ' ').trim()
  return words === '' ? role : words.charAt(0).toUpperCase() + words.slice(1)
}

/** The row a plain answer runs on, or null when the table does not carry it. */
export function defaultRow(
  rows: readonly ModelRow[],
  defaultRole: string,
): ModelRow | null {
  return rows.find((row) => row.role === defaultRole) ?? null
}

/**
 * What the control's own button says.
 *
 * It names the deployment a plain answer would run on, because that is the one figure
 * the person is choosing between when they open the menu. Before the table has loaded
 * it says so rather than guessing a model name.
 */
export function modelButtonLabel(
  rows: readonly ModelRow[] | null,
  defaultRole: string,
): string {
  if (rows === null) return 'Loading…'
  const row = defaultRow(rows, defaultRole)
  return row === null ? 'Not reported' : row.model
}
