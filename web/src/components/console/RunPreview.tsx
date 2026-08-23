'use client'

import { ChevronRight, Search, ShieldCheck, Signpost } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
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

/** Counts, formatted once at module scope rather than per render. */
const COUNT = new Intl.NumberFormat('en-US')

/**
 * What is about to happen to the question — one strip, under the composer.
 *
 * ## Why an idle console draws anything at all
 *
 * The rule this surface has always held to is that an empty console must not invent
 * content: no placeholder cards, no sample results, no fabricated figures. That rules out
 * a fake dashboard. It does **not** rule out the one thing an empty console can state
 * truthfully — *the path the next question will take* — and that path is the product's
 * whole argument. Twelve named rails run around every answer, and until a question is
 * sent nobody watching this screen knows that.
 *
 * ## Why it is a strip and no longer four cards
 *
 * It shipped as a four-cell card grid where every beat carried its own sentence and the
 * whole thing closed with a second one, which made the *preview* of a run taller than the
 * control that starts one. Four bordered cells, four sentences and a footnote to say
 * "there are four steps and two of them are rails" is exactly the text bomb
 * `03-AI-TEAM-PASS.md` rules out — and the sentences were restatement, not information:
 * a beat labelled **Route** does not need a line underneath saying it sizes the turn.
 *
 * So the sentences are **deleted**, not relocated; the twelve layer names move into the
 * one `InfoTip` that was already here, where mechanism prose belongs; and the four beats
 * become one horizontal spine. What survives on the page is the count — `6` under each
 * rail — because that is a measured fact about the shipped pipeline rather than a
 * description of it, and it is the number that makes somebody open the tip.
 *
 * Not one figure is invented. The moment a question is sent this component is gone and
 * {@link RunStages} draws the same spine filled in with the wire's own durations — the
 * preview and the run are deliberately the same picture, one empty and one measured.
 */
export function RunPreview(): ReactElement {
  return (
    <section aria-label="What happens to a question" className="@container/preview w-full min-w-0">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border bg-surface-2/40 px-3 py-2">
        {/* `shrink-0` on a heading whose text does not shrink: at 390px and the 125%
            text setting this row measured 394px of content inside a 390px viewport,
            and the ⓘ ended 1.4px from the edge — so the 24px pointer target around it
            (`TAP_TARGET`) had nowhere to go and pushed the document into a horizontal
            scroll. The heading wraps instead; nothing else on the row moves. */}
        <h2 className="eyebrow flex min-w-0 flex-wrap items-center gap-1.5">
          Every question takes this path
          <InfoTip label="About the path">
            The four beats are the compiled graph’s own. The middle one stands for whichever
            route the supervisor picks: answering from memory, retrieving and reasoning, or
            fanning out to a team. Nothing here is measured, because nothing has run yet.
            <span className="mt-2 block font-mono text-[0.68rem] leading-relaxed">
              <span className="text-muted-foreground">_screen_input</span> ·{' '}
              {INPUT_CHAIN.join(' · ')}
            </span>
            <span className="mt-1 block font-mono text-[0.68rem] leading-relaxed">
              <span className="text-muted-foreground">check_output</span> ·{' '}
              {OUTPUT_CHAIN.join(' · ')}
            </span>
          </InfoTip>
        </h2>

        <ol className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1.5">
          {BEATS.map((beat, index) => {
            const Icon = beat.icon
            return (
              <li key={beat.label} className="flex min-w-0 items-center gap-1.5">
                {index > 0 && (
                  <ChevronRight aria-hidden className="size-3 shrink-0 text-border" />
                )}
                <Icon aria-hidden className="size-3.5 shrink-0 text-blue-700" />
                <span className="min-w-0 truncate text-[0.78rem] font-medium text-foreground">
                  {beat.label}
                </span>
                {beat.chain.length > 0 && (
                  <Figure
                    className="shrink-0 text-[0.66rem] text-muted-foreground"
                    label={`${COUNT.format(beat.chain.length)} layers`}
                  >
                    {COUNT.format(beat.chain.length)}
                  </Figure>
                )}
              </li>
            )
          })}
        </ol>
      </div>
    </section>
  )
}
