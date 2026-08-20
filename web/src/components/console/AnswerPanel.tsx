'use client'

import { MessageSquareText, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { answerAbsence } from './answerAbsence'
import { useRevealedText } from './useRevealedText'

/**
 * The streamed final answer. Renders answer chunks as they arrive with a live
 * cursor, and surfaces the output-check verdict as a compact chip.
 *
 * When there is no answer it says **why**, in the run's own words. This is the default
 * tab, so the sentence here is the first explanation anybody gets — and it used to key
 * "Rejected at the human gate" on a status that also covers a guardrail block and a
 * budget refusal. {@link answerAbsence} reads the reason off the event log instead.
 *
 * ## Why the text types out
 *
 * The `stream` node measures 0 ms: `stream_answer` chunks an answer that `generate`
 * already produced in full and `guard_output` already cleared, because streaming raw
 * model tokens would put unguarded text on screen. So all sixty-odd `token` events land
 * together, and this panel used to assemble them and dump a finished paragraph — the
 * one moment the product is supposed to feel alive, over in a frame.
 * {@link useRevealedText} paces the reveal instead. Every character shown came off the
 * wire; only its arrival on screen is the console's decision, and the tooltip beside
 * the title says so.
 *
 * ## Why the body has a floor, and why the caret is in the flow
 *
 * This panel is the one place in the console where content arrives a token at a time,
 * so it is the one place where a layout that settles late is felt. Two fixes, both
 * structural rather than decorative: the body sits on a `min-h` floor so the panel does
 * not grow by a line the instant the first chunk lands, and the caret is an inline span
 * that occupies width from the start rather than an element that appears and pushes the
 * final word onto the next line.
 *
 * The answer is `whitespace-pre-wrap`. A model that returns a list returned newlines,
 * and collapsing them turned every list this console has ever streamed into one
 * paragraph.
 */
export function AnswerPanel({ state }: { state: RunState }): ReactElement {
  const outputGuard = state.guardrails.find((g) => g.stage === 'output')
  const absence = answerAbsence(state)
  const passed = outputGuard?.verdict === 'pass'
  // A run still in flight keeps typing; a settled turn renders whole, so scrolling back
  // through a thread never re-types an answer somebody has already read.
  const revealed = useRevealedText(state.answer, state.running)
  const streaming = revealed.typing || (state.running && state.answer.length > 0)

  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <MessageSquareText aria-hidden className="size-4 shrink-0 text-blue-700" />
            Answer
            <InfoTip label="About how the answer is streamed">
              Aegis generates the answer in full, screens the whole of it with the output
              rail, and only then releases it to this panel — so no unguarded model text
              ever reaches the screen. The typing is this console pacing text it has
              already received; every character shown came off the wire.
            </InfoTip>
          </span>
        }
        actions={
          outputGuard ? (
            <Badge tone={passed ? 'ok' : 'block'} className="gap-1">
              {passed ? (
                <ShieldCheck aria-hidden className="size-3" />
              ) : (
                <ShieldAlert aria-hidden className="size-3" />
              )}
              {passed ? 'output checked' : 'output blocked'}
            </Badge>
          ) : null
        }
      />
      <CardBody>
        <div className="min-h-10">
          {state.answer.length === 0 ? (
            <div className="flex flex-col gap-1">
              <p
                className={cn(
                  'text-sm leading-relaxed',
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
            <p
              aria-live="polite"
              aria-busy={streaming}
              className="text-sm leading-relaxed break-words whitespace-pre-wrap text-foreground"
            >
              {revealed.text}
              {/* Always in the flow, never mounted mid-sentence: the caret reserves its
                  own 2px from the first chunk, so the last word does not re-wrap when
                  the run settles and the caret goes. */}
              <span
                aria-hidden
                className={cn(
                  'ml-0.5 inline-block h-4 w-0.5 translate-y-0.5',
                  streaming ? 'animate-pulse bg-blue-700' : 'bg-transparent',
                )}
              />
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  )
}
