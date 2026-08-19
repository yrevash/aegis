'use client'

import { Brain, User, Users } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import type { MemorySubjectRow } from '@/lib/api/memory'

import { subjectSummary } from './memoryControl'

/**
 * Choose whose memory to work on — from a list the server built, never a typed key.
 *
 * The surface this replaces was a free-text box, and its own comment said why: *"The
 * backend exposes no 'list known subjects' route — memory is keyed by an arbitrary
 * subject string the caller supplies."* That made the screen unusable in both
 * directions. An administrator could not discover whose records existed, and a client
 * had to know the internal `user:<id>` shape to look at their own — so the read screen
 * was, in practice, for whoever already knew the key.
 *
 * `GET /memory/subjects` is that missing route, and it is also the isolation boundary:
 * every row in it was derived server-side from the caller's sealed tenant scope through
 * the `memory_subject_for` adapter seam. A client sees exactly one row. An administrator
 * sees its own tenant's people and nobody else's. Because the browser only ever echoes
 * back a `subject` it was handed, there is no string a person could type here that would
 * reach a query as an isolation key.
 */
export function SubjectPicker({
  rows,
  selected,
  onSelect,
}: {
  rows: MemorySubjectRow[]
  selected: string | null
  onSelect: (subject: string) => void
}): ReactElement {
  if (rows.length === 0) {
    return (
      <div className="flex min-h-40 flex-col items-center justify-center gap-3 text-center">
        <Brain className="size-7 text-muted-foreground/50" aria-hidden />
        <div>
          <p className="text-sm font-medium text-foreground">No memory subjects yet</p>
          <p className="mt-1 max-w-md text-sm text-muted-foreground">
            Memory is keyed to a person. Ask the agent a question and this fills in with
            what it learned, or write the first fact yourself once a subject appears.
          </p>
        </div>
      </div>
    )
  }

  return (
    <ul className="flex flex-col gap-1.5" aria-label="Memory subjects">
      {rows.map((row) => {
        const active = row.subject === selected
        return (
          <li key={row.subject}>
            <button
              type="button"
              onClick={() => onSelect(row.subject)}
              aria-current={active ? 'true' : undefined}
              className={cn(
                'flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                active
                  ? 'border-agent/40 bg-agent/10'
                  : 'border-border bg-card hover:bg-surface-2/60',
              )}
            >
              {row.is_self ? (
                <User className="size-4 shrink-0 text-agent-ink" aria-hidden />
              ) : (
                <Users className="size-4 shrink-0 text-muted-foreground" aria-hidden />
              )}
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium text-foreground">
                  {row.label}
                </span>
                <span className="tabular block font-mono text-[0.62rem] text-muted-foreground">
                  {subjectSummary(row)}
                </span>
              </span>
              {row.is_self && (
                <Badge tone="agent" className="shrink-0 text-[0.56rem]">
                  You
                </Badge>
              )}
            </button>
          </li>
        )
      })}
    </ul>
  )
}
