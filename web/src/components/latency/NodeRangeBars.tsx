'use client'

import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import type { NodeLatency } from '@/lib/api/platform'

/** Format a millisecond figure for a label, or return null when there is no reading. */
function ms(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null
  return value >= 1000 ? `${(value / 1000).toFixed(2)}s` : `${value.toFixed(0)}ms`
}

/**
 * Per-node latency as a **range bar**, not a single-value bar.
 *
 * The screen used to draw one bar per node at its p95, which answers "which node
 * is slowest?" and hides the question an operator actually has: *how wide is the
 * tail?* A node whose p50 and p95 sit on top of each other is a node that behaves;
 * a node whose p95 is six times its p50 is where a timeout will come from, and
 * both drew the identical bar.
 *
 * So each row is the p50 → p95 span as a filled track, the p50 marked at its
 * head, and `max` as a perpendicular tick — which is DESIGN.md §2's bullet-bar
 * prescription for several bounded values side by side, and the reason a row of
 * radial gauges is refused here. One hue at three intensities; nothing is
 * categorical, so nothing is coloured as if it were.
 *
 * Every geometry on this row is a measured percentile divided by the slowest
 * `max` on the page. No value is synthesised: a node with no reading draws no
 * mark and says so in words.
 */
export function NodeRangeBars({ nodes }: { nodes: NodeLatency[] }): ReactElement {
  const ceiling = Math.max(1, ...nodes.map((n) => n.max_ms ?? n.p95_ms ?? 0))
  const pct = (v: number | null | undefined): number =>
    v == null || !Number.isFinite(v) ? 0 : Math.max(0, Math.min(100, (v / ceiling) * 100))

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <span className="eyebrow">p50 → p95 span · max tick</span>
        <span className="flex items-center gap-4 text-[0.72rem] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-full bg-blue-800" aria-hidden />
            p50
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-4 rounded-sm bg-blue-400" aria-hidden />
            to p95
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-3 w-0.5 bg-blue-800" aria-hidden />
            max
          </span>
        </span>
      </div>

      <ul className="flex flex-col gap-2">
        {nodes.map((node) => {
          const p50 = pct(node.p50_ms)
          const p95 = pct(node.p95_ms)
          const max = pct(node.max_ms)
          const p95Text = ms(node.p95_ms)
          return (
            <li
              key={node.node}
              className="grid grid-cols-[minmax(0,1fr)_2fr_auto] items-center gap-3"
            >
              <Figure className="min-w-0 truncate text-foreground">{node.node}</Figure>

              <div
                aria-hidden
                className="relative h-3.5 overflow-hidden rounded-sm bg-surface-2"
              >
                {/* The p50 → p95 span. */}
                <span
                  className="absolute inset-y-0 rounded-sm bg-blue-400"
                  style={{ left: `${p50}%`, width: `${Math.max(p95 - p50, 0.8)}%` }}
                />
                {/* The p50 head — the typical case, where most runs actually land. */}
                <span
                  className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-blue-800"
                  style={{ left: `${p50}%` }}
                />
                {/* The worst sample in the window. */}
                {node.max_ms == null ? null : (
                  <span
                    className="absolute inset-y-0 w-0.5 -translate-x-1/2 bg-blue-800"
                    style={{ left: `${max}%` }}
                  />
                )}
              </div>

              <span className="w-20 text-right">
                {p95Text === null ? (
                  <span className="text-xs text-muted-foreground italic">no reading</span>
                ) : (
                  <Figure className="font-semibold text-foreground">{p95Text}</Figure>
                )}
              </span>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
