'use client'

import { Activity } from 'lucide-react'
import { useEffect, useRef, type ReactElement } from 'react'

import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { deriveActivity } from './agentLanes'

/**
 * The activity rail — retrieval, the graph delta, guardrail verdicts, routing,
 * self-checks and memory recall.
 *
 * These are the events that carry **no** agent identity, which is why they are a rail
 * and not a card: they belong to the supervisor and to the run as a whole. Keeping them
 * beside the agent panel rather than inside it means a fan-out's lanes stay readable —
 * a retrieval line never lands in an agent's card just because it arrived while that
 * agent was talking.
 */
export function ActivityRail({ state }: { state: RunState }): ReactElement {
  const items = deriveActivity(state)
  const endRef = useRef<HTMLLIElement>(null)

  // Keep the newest line in view while the run streams.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [items.length])

  return (
    <section aria-label="Activity" className="flex min-h-0 flex-col gap-2">
      <header className="flex items-center gap-2">
        <Activity aria-hidden className="size-4 text-muted-foreground" />
        <h3 className="eyebrow">Activity</h3>
      </header>

      {items.length === 0 ? (
        <p className="text-[0.75rem] text-muted-foreground">
          {state.running
            ? 'Retrieval, guardrails and memory report here as they run.'
            : 'Nothing reported outside the agent lanes.'}
        </p>
      ) : (
        <ul className="flex max-h-72 min-h-0 flex-col gap-1 overflow-y-auto pr-1">
          {items.map((item, index) => {
            const token = SIGNALS[item.signal]
            return (
              <li
                key={item.seq}
                ref={index === items.length - 1 ? endRef : undefined}
                className="animate-trace-in flex items-baseline gap-2 rounded-md px-1.5 py-1"
              >
                <span
                  aria-hidden
                  className="mt-1 size-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: token.hex }}
                />
                <div className="min-w-0">
                  <p className={cn('text-[0.78rem] font-medium', token.text)}>{item.title}</p>
                  {item.detail !== '' && (
                    <p className="truncate text-[0.72rem] text-muted-foreground">
                      {item.detail}
                    </p>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
