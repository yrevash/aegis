import type { ReactElement } from 'react'

import { Absence } from '@/components/primitives/Receipt'
import { NOT_RECORDED } from '@/components/forecast/sources'

/**
 * The figures this page will not show, and what would have to be recorded first.
 *
 * A dashboard's silences are invisible: a reader cannot tell a figure that is
 * missing from a figure that was never possible, so the missing one gets invented —
 * which is exactly how a hardcoded array of fictional numbers survived on the cache
 * page until 7.10b deleted it. Naming each gap, with the emission that would close
 * it, turns a silence into a piece of evidence about the platform.
 *
 * The rendering is {@link Absence}, so a stated absence looks the same here as it
 * does anywhere else a figure cannot be sourced.
 */
export function NotRecordedPanel(): ReactElement {
  return (
    <ul className="space-y-3">
      {NOT_RECORDED.map((row) => (
        <li key={row.figure}>
          <Absence figure={row.figure} why={row.why} needed={row.needed} />
        </li>
      ))}
    </ul>
  )
}
