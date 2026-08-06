import { BrainCircuit } from 'lucide-react'
import { useEffect, useRef, type ReactElement } from 'react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { RunState } from '@/state/runReducer'

/**
 * The live reasoning lane — "watch it think." Streams the planner's
 * chain-of-thought (`state.reasoning`) as it forms, in a distinct agent-hued
 * lane so the jury reads the intermediate thought separately from the final
 * answer. Appears once the planner emits its first reasoning chunk and stays as
 * the durable record of how the agent decided.
 */
export function ReasoningLane({ state }: { state: RunState }): ReactElement | null {
  const bodyRef = useRef<HTMLParagraphElement>(null)

  // Keep the newest thought in view as chunks accumulate.
  useEffect(() => {
    const el = bodyRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [state.reasoning])

  // Nothing to think about yet — stay out of the layout entirely.
  if (state.reasoning.length === 0) return null

  const thinking = state.phase === 'streaming' && state.answer.length === 0

  return (
    <Card className="border-agent/40 bg-agent/[0.04]">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <BrainCircuit className="size-4 text-agent-ink" />
        <CardTitle className="text-agent-ink">Reasoning</CardTitle>
        <Badge variant="agent" className="ml-auto">
          {thinking ? (
            <>
              <span className="animate-pip mr-1 size-1.5 rounded-full bg-agent" /> thinking
            </>
          ) : (
            'reasoning'
          )}
        </Badge>
      </CardHeader>
      <CardContent>
        <p
          ref={bodyRef}
          className="max-h-28 overflow-y-auto font-mono text-[0.78rem] leading-relaxed break-words text-foreground/80"
        >
          {state.reasoning}
          {thinking && (
            <span className="ml-0.5 inline-block h-3.5 w-1.5 translate-y-0.5 animate-pulse bg-agent-ink" />
          )}
        </p>
      </CardContent>
    </Card>
  )
}
