import type { CSSProperties, ReactElement } from 'react'

import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { StreamEvent } from '@/types/stream'

import { describeEvent } from './describeEvent'

/**
 * A single event in the agent trace, rendered on a signal-coloured timeline.
 * The newest row (`last`) pulses the active-beat in its own signal hue so the
 * trace stays in lockstep with the flow-map, graph and trust bar.
 */
export function TraceRow({ event, last }: { event: StreamEvent; last: boolean }): ReactElement {
  const { signal, icon: Icon, title, detail } = describeEvent(event)
  const token = SIGNALS[signal]
  // Only the freshly-mounted newest row fires the beat (keyed by remount).
  const beatStyle = last ? ({ '--beat': token.hex } as CSSProperties) : undefined
  return (
    <li className="animate-trace-in relative flex gap-3 pb-3">
      {/* Timeline spine */}
      {!last && (
        <span
          className="absolute top-6 left-[11px] h-full w-px bg-border"
          aria-hidden="true"
        />
      )}
      <span
        style={beatStyle}
        className={cn(
          'z-10 mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border',
          token.border,
          token.bg,
          last && 'animate-beat',
        )}
      >
        <Icon className={cn('size-3.5', token.text)} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <p className={cn('text-sm font-medium', token.text)}>{title}</p>
          <span className="tabular ml-auto font-mono text-[0.62rem] text-muted-foreground/70">
            #{event.seq}
          </span>
        </div>
        {detail && (
          <p className="mt-0.5 line-clamp-2 font-mono text-[0.72rem] leading-relaxed break-words text-muted-foreground">
            {detail}
          </p>
        )}
      </div>
    </li>
  )
}
