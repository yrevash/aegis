'use client'

import { CircleSlash } from 'lucide-react'
import type { ReactElement } from 'react'

import { NOT_RECORDED } from '@/components/forecast/sources'

/**
 * The figures this page will not show, and what would have to be recorded first.
 *
 * A dashboard's silences are invisible: a reader cannot tell a figure that is
 * missing from a figure that was never possible, so the missing one gets invented —
 * which is exactly how a hardcoded array of fictional numbers survived on the cache
 * page until 7.10b deleted it. Naming each gap, with the emission that would close
 * it, turns a silence into a piece of evidence about the platform.
 */
export function NotRecordedPanel(): ReactElement {
  return (
    <ul className="space-y-3">
      {NOT_RECORDED.map((row) => (
        <li key={row.figure} className="rounded-xl border border-border bg-surface-2/40 p-4">
          <div className="flex items-start gap-2">
            <CircleSlash className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
            <div className="min-w-0 space-y-1.5">
              <p className="text-sm font-semibold text-foreground">{row.figure}</p>
              <p className="text-[0.78rem] leading-relaxed text-muted-foreground">{row.why}</p>
              <p className="text-[0.78rem] leading-relaxed text-foreground">
                <span className="eyebrow mr-1.5">to measure it</span>
                {row.needed}
              </p>
            </div>
          </div>
        </li>
      ))}
    </ul>
  )
}
