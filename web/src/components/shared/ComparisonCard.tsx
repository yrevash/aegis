'use client'

import type { ReactElement, ReactNode } from 'react'

import { Card } from '@/components/primitives/card'
import { cn } from '@/lib/utils'

interface ComparisonRow {
  label: string
  a: ReactNode
  b: ReactNode
  /** Highlight the row when the two sides meaningfully differ. */
  diff?: boolean
}

interface ComparisonCardProps {
  title: string
  /** Column headers, e.g. ["Operations lead", "Client"]. */
  columns: [string, string]
  rows: ComparisonRow[]
  className?: string
}

/**
 * A side-by-side A-vs-B comparison (§5) — the Access-demo divergence hero and
 * the Approvals "what changed" pattern. A real table for accessibility; rows
 * that differ get a subtle tint and a "differs" marker (colour is never the only
 * signal).
 */
export function ComparisonCard({
  title,
  columns,
  rows,
  className,
}: ComparisonCardProps): ReactElement {
  return (
    <Card className={cn('overflow-hidden p-0', className)}>
      <table className="w-full border-collapse text-left">
        <caption className="px-5 pt-4 pb-3 text-left text-base leading-6 font-semibold text-foreground">
          {title}
        </caption>
        <thead>
          <tr className="border-b border-border">
            <th scope="col" className="eyebrow px-5 py-2 font-normal">
              What differs
            </th>
            <th scope="col" className="t-label px-5 py-2 text-foreground">
              {columns[0]}
            </th>
            <th scope="col" className="t-label px-5 py-2 text-foreground">
              {columns[1]}
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.label}
              className={cn(
                'border-b border-border/70 last:border-0',
                row.diff && 'bg-risk/8',
              )}
            >
              <th scope="row" className="t-label px-5 py-3 font-medium text-muted-foreground">
                <span className="flex items-center gap-1.5">
                  {row.label}
                  {row.diff && (
                    <span className="eyebrow rounded-sm border border-risk/40 px-1 py-px text-[0.52rem] text-risk-ink">
                      differs
                    </span>
                  )}
                </span>
              </th>
              <td className="t-body px-5 py-3 text-foreground">{row.a}</td>
              <td className="t-body px-5 py-3 text-foreground">{row.b}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  )
}