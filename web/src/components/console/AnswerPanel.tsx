'use client'

import { MessageSquareText } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { answerAbsence } from './answerAbsence'

/**
 * The streamed final answer. Renders answer chunks as they arrive with a live
 * cursor, and surfaces the output-check verdict as a compact chip.
 *
 * When there is no answer it says **why**, in the run's own words. This is the default
 * tab, so the sentence here is the first explanation anybody gets — and it used to key
 * "Rejected at the human gate" on a status that also covers a guardrail block and a
 * budget refusal. {@link answerAbsence} reads the reason off the event log instead.
 */
export function AnswerPanel({ state }: { state: RunState }): ReactElement {
  const outputGuard = state.guardrails.find((g) => g.stage === 'output')
  const streaming = state.phase === 'streaming' && state.answer.length > 0
  const absence = answerAbsence(state)

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
          <div className="flex flex-col gap-1">
            <p
              className={cn(
                'text-sm',
                absence.stopped ? 'font-medium text-foreground' : 'text-muted-foreground',
              )}
            >
              {absence.headline}
            </p>
            {absence.detail !== '' && (
              <p className="text-[0.8rem] leading-relaxed text-muted-foreground">
                {absence.detail}
              </p>
            )}
          </div>
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