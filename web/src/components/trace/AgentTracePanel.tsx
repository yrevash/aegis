'use client'

import { Activity } from 'lucide-react'
import { useEffect, useRef, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { ScrollArea } from '@/components/primitives/scroll-area'
import type { RunState } from '@/state/runReducer'

import { TraceRow } from './TraceRow'

/**
 * The streaming agent-trace panel — the show edge. Renders the agent's
 * intermediate steps live as they arrive over SSE, not just the final answer.
 */
export function AgentTracePanel({ state }: { state: RunState }): ReactElement {
  const wrapRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to the newest event as the stream advances.
  useEffect(() => {
    const el = wrapRef.current?.querySelector<HTMLElement>('[data-radix-scroll-area-viewport]')
    if (el) el.scrollTop = el.scrollHeight
  }, [state.events.length])

  const streaming = state.phase === 'streaming'

  return (
    <Card className="flex h-full flex-col">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <Activity className="size-4 text-blue-700" />
        <CardTitle>Activity</CardTitle>
        <InfoTip label="About Activity">
          The agent&rsquo;s step-by-step run log, streamed live as each step lands.
        </InfoTip>
        {streaming && (
          <Badge variant="agent" className="ml-auto">
            <span className="animate-pip mr-1 size-1.5 rounded-full bg-blue-200" /> live
          </Badge>
        )}
        {!streaming && state.events.length > 0 && (
          <Badge variant="secondary" className="ml-auto">
            {state.events.length} events
          </Badge>
        )}
      </CardHeader>
      <CardContent ref={wrapRef} className="min-h-0 flex-1 p-0">
        {state.events.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center text-sm text-muted-foreground">
            <Activity className="size-6 text-muted-foreground/50" />
            <p>Run a query to watch the agent reason and act, step by step.</p>
          </div>
        ) : (
          <ScrollArea className="h-full px-5 pb-4">
            <ol className="pt-1">
              {state.events.map((event, i) => (
                <TraceRow
                  key={`${event.seq}-${i}`}
                  event={event}
                  last={i === state.events.length - 1}
                />
              ))}
            </ol>
          </ScrollArea>
        )}
      </CardContent>
    </Card>
  )
}