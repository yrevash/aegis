'use client'

import { MessageSquareText, ShieldAlert, ShieldCheck } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { AnswerBody } from './AnswerBody'
import { answerAbsence } from './answerAbsence'
import { readSources } from './sources'
import { useRevealedText } from './useRevealedText'

/**
 * What the answer stands on, at the foot of the answer itself.
 *
 * This used to be its own `Card` directly beneath this one — a card under a card, saying
 * something about the card above it. The citations are part of the answer, not a panel
 * beside it, so they close it: a hairline, the label once, and at most three chips with
 * the page each source reported and the span check's verdict where one ran.
 */
function StandsOn({
  state,
  onSeeSources,
}: {
  state: RunState
  onSeeSources?: () => void
}): ReactElement | null {
  const cited = readSources(state.retrievalScores).slice(0, 3)
  if (cited.length === 0) return null

  return (
    <div className="mt-4 flex min-w-0 flex-wrap items-center gap-1.5 border-t border-border pt-3">
      <span className="eyebrow mr-1">Stands on</span>
      {cited.map((source) => (
        <span
          key={source.id}
          className="inline-flex max-w-full min-w-0 items-center gap-1.5 rounded-md border border-border bg-surface-2/50 px-2 py-1"
        >
          <span className="max-w-[16rem] truncate text-[0.74rem] text-foreground">
            {source.label}
          </span>
          {source.page !== null && <Badge tone="neutral">page {source.page}</Badge>}
          {source.verbatim === 'verified' && <Badge tone="ok">verbatim</Badge>}
          {source.verbatim === 'unverified' && <Badge tone="block">unverified</Badge>}
        </span>
      ))}
      {onSeeSources !== undefined && (
        <button
          type="button"
          onClick={onSeeSources}
          className="ml-auto rounded-md px-2 py-1 text-[0.74rem] font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
        >
          See all sources
        </button>
      )}
    </div>
  )
}

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
 * ## Why the body is parsed rather than pre-wrapped
 *
 * `whitespace-pre-wrap` kept a model's newlines, which was the right fix while an answer
 * was a paragraph. It is the wrong one now that an answer is a briefing: `**bold**`,
 * `## headings` and numbered steps rendered as literal punctuation running down the
 * page. {@link AnswerBody} renders the structure instead — as React elements, never as
 * HTML.
 */
export function AnswerPanel({
  state,
  onSeeSources,
}: {
  state: RunState
  /**
   * Opens the Sources tab from the citation strip. Optional — a caller with no tabs to
   * open (the simulation view) gets the chips and no link, rather than a dead control.
   */
  onSeeSources?: () => void
}): ReactElement {
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
            <div aria-live="polite" aria-busy={streaming}>
              <AnswerBody
                text={revealed.text}
                caret={
                  /* Always in the flow, never mounted mid-sentence: the caret reserves
                     its own 2px from the first chunk, so the last word does not re-wrap
                     when the run settles and the caret goes. */
                  <span
                    aria-hidden
                    className={cn(
                      'ml-0.5 inline-block h-4 w-0.5 translate-y-0.5',
                      streaming ? 'animate-pulse bg-blue-700' : 'bg-transparent',
                    )}
                  />
                }
              />
            </div>
          )}
        </div>

        <StandsOn state={state} onSeeSources={onSeeSources} />
      </CardBody>
    </Card>
  )
}
