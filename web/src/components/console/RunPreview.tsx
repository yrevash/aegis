'use client'

import { ShieldCheck, Signpost, Search, MessageSquareQuote } from 'lucide-react'
import type { ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'

import { INPUT_CHAIN, OUTPUT_CHAIN } from './stageTimeline'

/** One beat of the path, and the rails it runs when it is a guardrail. */
interface Beat {
  label: string
  icon: typeof ShieldCheck
  /** The layer chain, for a guardrail beat. Empty otherwise. */
  chain: readonly string[]
}

/**
 * The four beats every turn passes through, in graph order.
 *
 * Read from the same two places {@link RunStages} reads: the node briefs in
 * `aegis/agent/graph.py` and the two rail chains in `aegis/guardrails/pipeline.py`. The
 * middle is deliberately one beat rather than a guess at which of `answer_memory`,
 * `retrieve → plan → act → reflect` or a fan-out this particular question will take —
 * that is the router's decision and it has not been made yet.
 */
const BEATS: readonly Beat[] = [
  { label: 'Input rail', icon: ShieldCheck, chain: INPUT_CHAIN },
  { label: 'Route', icon: Signpost, chain: [] },
  { label: 'Retrieve & answer', icon: Search, chain: [] },
  { label: 'Output rail', icon: ShieldCheck, chain: OUTPUT_CHAIN },
]

/**
 * The layers under a guardrail beat, in the order it runs them.
 *
 * One flowing mono line rather than a chip each. Twelve pills in four narrow cells is
 * the "pill overuse" DESIGN.md §9 names, and it cost the strip about eighty pixels of
 * height to say the same six words.
 */
function Chain({ layers }: { layers: readonly string[] }): ReactElement {
  return (
    <p className="font-mono text-[0.62rem] leading-relaxed text-muted-foreground">
      {layers.join(' · ')}
    </p>
  )
}

/**
 * What is about to happen to the question — the idle console's one true visual.
 *
 * ## Why an idle console draws anything at all
 *
 * The idle screen had a wordmark, a field and a screen of nothing, and the nothing was
 * the largest element on it. The rule this surface has always held to is that an empty
 * console must not invent content: no placeholder cards, no sample results, no fabricated
 * figures. That rule rules out a fake dashboard. It does **not** rule out the one thing
 * an empty console can state truthfully — *the path the next question will take* — and
 * that path is the product's whole argument. Twelve named rails run around every answer,
 * and until a question is sent nobody watching this screen knows that.
 *
 * So: four beats, the twelve real layer names, and **not one number**. There is nothing
 * to measure yet and nothing here pretends there is. The moment a question is sent this
 * component is gone and {@link RunStages} draws the same spine filled in with the wire's
 * own durations — the preview and the run are deliberately the same picture, one empty
 * and one measured.
 */
export function RunPreview(): ReactElement {
  return (
    <section
      aria-label="What happens to a question"
      className="@container/preview flex w-full flex-col gap-2.5"
    >
      <h2 className="eyebrow flex items-center gap-1.5">
        Every question takes this path
        <InfoTip label="About the path">
          The four beats are the compiled graph’s own, and the layer names under the two
          rails are the literal order in{' '}
          <span className="font-mono">aegis/guardrails/pipeline.py</span> —{' '}
          <span className="font-mono">_screen_input</span> and{' '}
          <span className="font-mono">check_output</span>. The middle beat stands for
          whichever route the supervisor picks: answering from memory, retrieving and
          reasoning, or fanning out to a team. Nothing here is measured, because nothing has
          run yet.
        </InfoTip>
      </h2>

      <ol className="grid grid-cols-2 gap-2 @[36rem]/preview:grid-cols-4">
        {BEATS.map((beat, index) => {
          const Icon = beat.icon
          return (
            <li
              key={beat.label}
              className="relative flex min-w-0 flex-col gap-2 rounded-lg border border-border bg-surface-2/40 px-3 py-2.5"
            >
              {/* The connector — the track a run fills in. Drawn only where a beat has
                  a neighbour on the same row. */}
              {index > 0 && (
                <span
                  aria-hidden
                  className="absolute top-1/2 -left-2 hidden h-px w-2 bg-border @[36rem]/preview:block"
                />
              )}
              <span className="flex min-w-0 items-center gap-1.5">
                <Icon aria-hidden className="size-3.5 shrink-0 text-blue-700" />
                <span className="min-w-0 truncate text-[0.8rem] font-medium text-foreground">
                  {beat.label}
                </span>
                {beat.chain.length > 0 && (
                  <span className="tabular ml-auto shrink-0 font-mono text-[0.62rem] text-muted-foreground">
                    {beat.chain.length}
                  </span>
                )}
              </span>
              {beat.chain.length > 0 ? (
                <Chain layers={beat.chain} />
              ) : (
                <p className="text-[0.68rem] leading-snug text-muted-foreground">
                  {beat.label === 'Route'
                    ? 'Sizes the turn: one agent or a team.'
                    : 'The corpus, the graph, the tools — whichever the route chose.'}
                </p>
              )}
            </li>
          )
        })}
      </ol>

      <p className="flex items-center gap-1.5 text-[0.7rem] text-muted-foreground">
        <MessageSquareQuote aria-hidden className="size-3.5 shrink-0" />
        Ask something and this spine fills in live, each stage carrying its own duration and
        cost.
      </p>
    </section>
  )
}
