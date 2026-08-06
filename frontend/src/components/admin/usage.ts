/**
 * Pure roll-up + formatting helpers for the admin usage dashboard. Side-effect
 * free so the aggregation is unit-testable (see `usage.test.ts`); the view only
 * renders these figures.
 */

import type { UsageModelRow, UsageResponse } from '@/types/api'

/** Total tokens across prompt + completion. */
export function totalTokens(u: Pick<UsageResponse, 'total_prompt_tokens' | 'total_completion_tokens'>): number {
  return u.total_prompt_tokens + u.total_completion_tokens
}

/** A per-model row plus its share of total spend in `[0, 1]`. */
export interface ModelShare extends UsageModelRow {
  share: number
}

/**
 * Per-model rows sorted by cost (desc), each with its share of total spend. A
 * zero/negative total yields zero shares (never divides by zero).
 */
export function modelShares(rows: UsageModelRow[]): ModelShare[] {
  const total = rows.reduce((s, r) => s + r.cost_usd, 0)
  return [...rows]
    .sort((a, b) => b.cost_usd - a.cost_usd)
    .map((r) => ({ ...r, share: total > 0 ? r.cost_usd / total : 0 }))
}

/** Format a token count compactly, e.g. `42.8M`, `120K`. */
export function formatTokens(n: number): string {
  return n.toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 1 })
}

/**
 * Signal palette cycled across model series so the bar / donut of "spend by
 * model" stays inside the trust taxonomy (§2.4) instead of inventing hues.
 */
const MODEL_SIGNALS = ['ml', 'graph', 'agent', 'ok', 'risk', 'neutral'] as const

/** A chart-ready model row: short label, spend, and its cycled signal colour. */
export interface ModelSpendDatum {
  model: string
  cost: number
  color: (typeof MODEL_SIGNALS)[number]
}

/**
 * The top `limit` models by spend as chart data (bar + donut share the shape),
 * folding any remainder into a single "others" row so the visual never sprouts
 * a long tail. Spend is rounded to cents; colours cycle the signal palette.
 */
export function modelSpendData(rows: UsageModelRow[], limit = 5): ModelSpendDatum[] {
  const sorted = [...rows].sort((a, b) => b.cost_usd - a.cost_usd)
  const head = sorted.slice(0, limit)
  const tail = sorted.slice(limit)
  const out: ModelSpendDatum[] = head.map((r, i) => ({
    model: r.model,
    cost: Number(r.cost_usd.toFixed(2)),
    color: MODEL_SIGNALS[i % MODEL_SIGNALS.length],
  }))
  if (tail.length > 0) {
    const rest = tail.reduce((s, r) => s + r.cost_usd, 0)
    if (rest > 0) {
      out.push({ model: `+${tail.length} more`, cost: Number(rest.toFixed(2)), color: 'neutral' })
    }
  }
  return out
}
