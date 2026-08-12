import type { ReactElement } from 'react'

import type { NodeFinished } from '@/types/stream'

import { buildGantt, formatCost, formatMs } from './nodeGantt'

interface NodeGanttProps {
  /** Finished graph nodes in execution order (the run's `nodeLedger`). */
  nodes: NodeFinished[]
}

/**
 * Per-node cost/latency timeline: each graph node is a bar laid end-to-end on a
 * run-scaled track, and its latency and cost sit in two aligned tabular-mono
 * right columns — never inside the bar, so the numbers are always legible.
 * A node that spent on an LLM reads in the graph hue; a non-LLM node is muted.
 */
export function NodeGantt({ nodes }: NodeGanttProps): ReactElement {
  const gantt = buildGantt(nodes)

  if (gantt.bars.length === 0) {
    return (
      <div className="flex flex-col gap-2.5">
        <Header />
        <p className="py-3 text-center text-[0.72rem] text-muted-foreground">
          No nodes have finished yet.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2.5">
      <Header />

      <div className="flex flex-col gap-1.5">
        {gantt.bars.map((bar) => {
          const spent = bar.costUsd > 0
          return (
            <div
              key={bar.node}
              className="grid grid-cols-[minmax(0,1fr)_1.9fr_auto_auto] items-center gap-3"
            >
              <span className="min-w-0 truncate font-mono text-[0.74rem] text-foreground">
                {bar.label}
              </span>

              <div className="relative h-4 rounded-sm bg-muted/50">
                <div
                  className="absolute inset-y-0 rounded-sm"
                  style={{
                    left: `${bar.offsetPct}%`,
                    width: `${Math.max(bar.widthPct, 1.5)}%`,
                    background: spent ? 'var(--graph-ink)' : 'var(--muted-foreground)',
                    opacity: spent ? 1 : 0.45,
                  }}
                />
              </div>

              <span className="tabular w-14 text-right font-mono text-[0.76rem] font-semibold text-foreground">
                {bar.ms}
              </span>
              <span
                className="tabular w-20 text-right font-mono text-[0.72rem]"
                style={{ color: spent ? 'var(--graph-ink)' : 'var(--muted-foreground)' }}
              >
                {bar.cost}
              </span>
            </div>
          )
        })}
      </div>

      <div className="grid grid-cols-[minmax(0,1fr)_1.9fr_auto_auto] items-center gap-3 border-t border-border pt-2">
        <span className="eyebrow text-foreground">total</span>
        <span />
        <span className="tabular w-14 text-right font-mono text-[0.82rem] font-bold text-foreground">
          {formatMs(gantt.totalMs)}
        </span>
        <span
          className="tabular w-20 text-right font-mono text-[0.78rem] font-bold"
          style={{ color: 'var(--graph-ink)' }}
        >
          {formatCost(gantt.totalCost)}
        </span>
      </div>
    </div>
  )
}

function Header(): ReactElement {
  return (
    <div className="flex items-center justify-between">
      <span className="eyebrow flex items-center gap-1.5">
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: 'var(--graph-ink)' }}
        />
        glass-box · per-node cost &amp; latency
      </span>
      <span className="tabular font-mono text-[0.62rem] text-muted-foreground">
        ms · usd
      </span>
    </div>
  )
}
