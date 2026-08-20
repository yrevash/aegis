'use client'

import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { cn } from '@/lib/utils'

import type { StackGroup } from './stackDisplay'

/**
 * The four steps of the one blue ramp this bar is allowed to use.
 *
 * DESIGN.md §2: a sequential scale is **one hue light → dark**, never a cycled
 * set of categorical colours. The layers of a stack are ordered — runtime under
 * backend under frontend under infrastructure — so an ordered ramp is the honest
 * encoding, and the label under each segment is what actually names it.
 */
const RAMP = ['bg-blue-800', 'bg-blue-600', 'bg-blue-400', 'bg-blue-200'] as const

/** The matching dot for the legend row, so the key and the bar agree. */
const DOT = ['bg-blue-800', 'bg-blue-600', 'bg-blue-400', 'bg-blue-200'] as const

/**
 * How the inventory distributes across the layers of the stack — one bar, four
 * ordered segments, drawn from the same grouped rows the table below renders.
 *
 * This exists because "34 components" is a number nobody can picture. The shape
 * of a stack is the fact an operator reads first: a runtime-heavy inventory and
 * an infra-heavy one are different systems, and no column of version pins shows
 * that. Every width here is a real count divided by a real total — there is no
 * figure on this bar that is not also in the table.
 */
export function StackLayers({ groups, total }: { groups: StackGroup[]; total: number }): ReactElement {
  if (total <= 0 || groups.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">No component reported a layer.</p>
    )
  }
  return (
    <div className="flex flex-col gap-3">
      <div
        className="flex h-4 w-full gap-0.5 overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={groups
          .map((g) => `${g.label}: ${g.rows.length} of ${total} components`)
          .join('; ')}
      >
        {groups.map((group, i) => (
          <span
            key={group.category}
            className={cn('h-full first:rounded-l-full last:rounded-r-full', RAMP[i % RAMP.length])}
            style={{ width: `${(group.rows.length / total) * 100}%` }}
          />
        ))}
      </div>
      <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {groups.map((group, i) => (
          <li key={group.category} className="flex items-center gap-2">
            <span
              className={cn('size-2.5 shrink-0 rounded-full', DOT[i % DOT.length])}
              aria-hidden
            />
            <span className="text-[0.8125rem] text-foreground">{group.label}</span>
            <Figure className="text-muted-foreground">
              {`${group.rows.length} · ${Math.round((group.rows.length / total) * 100)}%`}
            </Figure>
          </li>
        ))}
      </ul>
    </div>
  )
}
