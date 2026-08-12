'use client'

import { MessageSquareText } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import type { RunState } from '@/state/runReducer'

/**
 * The streamed final answer. Renders answer chunks as they arrive with a live
 * cursor, and surfaces the output-check verdict as a compact chip.
 */
export function AnswerPanel({ state }: { state: RunState }): ReactElement {
  const outputGuard = state.guardrails.find((g) => g.stage === 'output')
  const streaming = state.phase === 'streaming' && state.answer.length > 0
  const blocked = state.finishedStatus === 'blocked'

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <MessageSquareText className="size-4 text-agent-ink" />
        <CardTitle>Answer</CardTitle>
        {outputGuard && (
          <Badge variant={outputGuard.verdict === 'pass' ? 'ok' : 'block'} className="ml-auto">
            {outputGuard.verdict === 'pass' ? 'output checked' : 'output blocked'}
          </Badge>
        )}
      </CardHeader>
      <CardContent>
        {state.answer.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            {blocked
              ? 'Rejected at the human gate — no answer generated.'
              : 'The answer streams here once the agent responds.'}
          </p>
        ) : (
          <p className="text-[0.9rem] leading-relaxed text-foreground">
            {state.answer}
            {streaming && (
              <span className="ml-0.5 inline-block h-4 w-2 translate-y-0.5 animate-pulse bg-agent-ink" />
            )}
          </p>
        )}
      </CardContent>
    </Card>
  )
}