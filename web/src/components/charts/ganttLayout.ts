/**
 * Pure domain math for the per-node cost/latency gantt.
 *
 * Each finished graph node becomes a bar on a shared timeline: bars are laid
 * end-to-end (the agent runs its nodes sequentially), scaled so the whole run
 * fills the track. The formatted `ms`/`cost` strings live here — not baked into
 * the bar — so the component can render them in aligned right columns, the
 * legibility fix (numbers must never sit clipped inside a narrow bar).
 */

import type { NodeFinished } from '@/lib/stream'

/** One node's bar on the timeline, with its figures pre-formatted for the columns. */
export interface GanttBar {
  node: string
  label: string
  /** Bar start as a percent of the total run duration. */
  offsetPct: number
  /** Bar width as a percent of the total run duration. */
  widthPct: number
  durationMs: number
  costUsd: number
  /** Compact latency, e.g. `"612ms"` / `"1.2s"`. */
  ms: string
  /** Compact cost, e.g. `"$0.0004"`, or `"—"` for a non-LLM node. */
  cost: string
}

/** The full timeline: laid-out bars plus run totals for the footer row. */
export interface Gantt {
  bars: GanttBar[]
  totalMs: number
  totalCost: number
}

/**
 * Format a duration in milliseconds compactly: sub-second stays in `ms`
 * (rounded), one second and over switches to one-decimal `s`.
 *
 * @param ms - Duration in milliseconds.
 * @returns e.g. `"612ms"` or `"1.2s"`.
 */
export function formatMs(ms: number): string {
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

/**
 * Format a USD cost to four decimals with a leading `$`. A zero cost (a
 * non-LLM node) renders as an em dash so the column reads "no LLM spend" at a
 * glance rather than a misleading `$0.0000`.
 *
 * @param usd - Marginal cost in USD.
 * @returns e.g. `"$0.0031"` or `"—"`.
 */
export function formatCost(usd: number): string {
  return usd > 0 ? `$${usd.toFixed(4)}` : '—'
}

/**
 * Lay finished nodes out end-to-end on a shared timeline scaled to the run.
 *
 * Bars accumulate: node *i* starts where node *i−1* ended, and every bar is
 * expressed as a percent of the total run duration so the track always fills.
 * A zero-duration run yields zero-width bars (no divide-by-zero).
 *
 * @param nodes - Finished graph nodes in execution order.
 * @returns The laid-out bars and the run's total duration and cost.
 */
export function buildGantt(nodes: NodeFinished[]): Gantt {
  const totalMs = nodes.reduce((sum, n) => sum + n.duration_ms, 0)
  const totalCost = nodes.reduce((sum, n) => sum + n.cost_usd, 0)

  let cursor = 0
  const bars: GanttBar[] = nodes.map((n) => {
    const offsetPct = totalMs > 0 ? (cursor / totalMs) * 100 : 0
    const widthPct = totalMs > 0 ? (n.duration_ms / totalMs) * 100 : 0
    cursor += n.duration_ms
    return {
      node: n.node,
      label: n.label,
      offsetPct,
      widthPct,
      durationMs: n.duration_ms,
      costUsd: n.cost_usd,
      ms: formatMs(n.duration_ms),
      cost: formatCost(n.cost_usd),
    }
  })

  return { bars, totalMs, totalCost }
}
