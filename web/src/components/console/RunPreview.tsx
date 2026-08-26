'use client'

import { ChevronRight, Search, ShieldCheck, Signpost } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { useEffect, useState } from 'react'
import { useTick } from './useTick'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { beatStates, type BeatId, type BeatStatus } from './runPath'
import { INPUT_CHAIN, OUTPUT_CHAIN, formatDuration } from './stageTimeline'

/** Counts, formatted once at module scope rather than per render. */
const COUNT = new Intl.NumberFormat('en-US')

/**
 * The icon per beat. Lives here rather than on the beat itself so `runPath.ts` stays a
 * pure module Node can run under type-stripping, with no component import in it.
 */
const ICON: Record<BeatId, typeof ShieldCheck> = {
  input: ShieldCheck,
  route: Signpost,
  work: Search,
  output: ShieldCheck,
}

/**
 * How each status reads. Only the reserved status hues and the one blue ramp — a beat
 * that passed is `ok`, a beat that stopped the run is `block`, and everything still to
 * come is the border grey, which is the colour of "nothing claimed yet".
 */
const TONE: Record<BeatStatus, { icon: string; label: string }> = {
  pending: { icon: 'text-border', label: 'text-muted-foreground' },
  running: { icon: 'text-blue-700', label: 'text-foreground' },
  passed: { icon: 'text-ok-ink', label: 'text-foreground' },
  blocked: { icon: 'text-block-ink', label: 'text-block-ink font-semibold' },
}

/**
 * The path a question takes — promised when the console is empty, measured once it runs.
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
 * become one horizontal spine.
 *
 * ## Why it now survives the question being sent
 *
 * It used to be unmounted the moment a run started — at exactly the point the path stops
 * being a promise and becomes a measurement. A run is forty to sixty seconds and eleven
 * of them are the two guardrails, which the console spent showing a spinner. Handing this
 * component a {@link RunState} turns the same picture into the run's scaffold: each beat
 * lights as it is entered, settles with the wire's own `duration_ms`, and locks green or
 * red on the rail the wire named as deciding.
 *
 * **The six chips do not chase.** `per_rail_timing_ms` is `null` for every individual
 * rail and only `total` is measured, so a sequential sweep would be drawing six durations
 * the platform declines to claim. The beat lights as a whole, for its real total, and the
 * deciding layer is the only one that ever makes a claim of its own.
 *
 * @param state - The run this strip is scaffolding, or `null` (the default) for the idle
 *   console, where all four beats read as pending and not one figure is measured.
 */
export function RunPreview({ state = null }: { state?: RunState | null } = {}): ReactElement {
  const beats = beatStates(state)
  const live = state !== null

  /*
   * The open beat's own elapsed.
   *
   * A beat prints the wire's `duration_ms` when it lands, and printed nothing at all
   * while it was open — which is the whole of the longest stage. Agentic retrieval runs
   * for a minute and emits nothing between its open and its close, so the beat that was
   * doing all the work was the one beat carrying no figure.
   *
   * This is measured here rather than taken from the wire because the wire has not sent
   * it yet: it is time since *this* beat opened, restarted whenever a different beat
   * does, and it is labelled `elapsed so far` so it is never mistaken for the measured
   * total that replaces it. The clock stops when the run does.
   */
  const openBeat = beats.find((b) => b.status === 'running') ?? null
  const now = useTick(openBeat !== null)
  const [openedAt, setOpenedAt] = useState<{ id: string; at: number } | null>(null)
  useEffect(() => {
    if (openBeat === null) {
      setOpenedAt(null)
      return
    }
    setOpenedAt((prev) => (prev?.id === openBeat.id ? prev : { id: openBeat.id, at: Date.now() }))
  }, [openBeat])
  const openMs =
    openBeat !== null && openedAt?.id === openBeat.id ? Math.max(0, now - openedAt.at) : null

  const heading = live ? 'The path this run is taking' : 'Every question takes this path'
  const tip = (
    <InfoTip label="About the path">
      The four beats are the compiled graph’s own. The middle one stands for whichever
      route the supervisor picks: answering from memory, retrieving and reasoning, or
      fanning out to a team.{' '}
      {live
        ? 'Every duration here is the stage’s own duration_ms. The rails are not timed individually — the wire reports one total per guardrail and at most one deciding layer, so only that layer is marked.'
        : 'Nothing here is measured, because nothing has run yet.'}
      <span className="mt-2 block font-mono text-[0.68rem] leading-relaxed">
        <span className="text-muted-foreground">_screen_input</span> · {INPUT_CHAIN.join(' · ')}
      </span>
      <span className="mt-1 block font-mono text-[0.68rem] leading-relaxed">
        <span className="text-muted-foreground">check_output</span> · {OUTPUT_CHAIN.join(' · ')}
      </span>
    </InfoTip>
  )

  return (
    <section
      aria-label={live ? 'The path this run is taking' : 'What happens to a question'}
      className="@container/preview w-full min-w-0"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-border bg-surface-2/40 px-3 py-2">
        {/* `shrink-0` on a heading whose text does not shrink: at 390px and the 125%
            text setting this row measured 394px of content inside a 390px viewport,
            and the ⓘ ended 1.4px from the edge — so the 24px pointer target around it
            (`TAP_TARGET`) had nowhere to go and pushed the document into a horizontal
            scroll. The heading wraps instead; nothing else on the row moves. */}
        {live ? (
          /* Inside the run card this sits under an `h3`, so it is a label rather than a
             heading: a fifth level under a third is a document outline nobody can read. */
          <span className="eyebrow flex min-w-0 flex-wrap items-center gap-1.5">
            {heading}
            {tip}
          </span>
        ) : (
          <h2 className="eyebrow flex min-w-0 flex-wrap items-center gap-1.5">
            {heading}
            {tip}
          </h2>
        )}

        <ol className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1.5">
          {beats.map((beat, index) => {
            const Icon = ICON[beat.id]
            const tone = TONE[beat.status]
            return (
              <li key={beat.id} className="flex min-w-0 items-center gap-1.5">
                {index > 0 && <ChevronRight aria-hidden className="size-3 shrink-0 text-border" />}
                <Icon
                  aria-hidden
                  className={cn(
                    'size-3.5 shrink-0',
                    tone.icon,
                    beat.status === 'running' && 'animate-beat-open',
                  )}
                />
                <span className={cn('min-w-0 truncate text-[0.78rem] font-medium', tone.label)}>
                  {beat.label}
                </span>

                {/* The rail count while it is still a promise; the wire's own total the
                    moment the beat lands. Never both — the count is what makes somebody
                    open the tip, and the duration is what replaces it. */}
                {beat.durationMs === null && beat.status === 'running' && openMs !== null ? (
                  /* The open beat, ticking. `elapsed so far` is the same label the stage
                     rows use for the same fact, so the two surfaces cannot be read as
                     claiming different things. */
                  <Figure
                    className="tabular shrink-0 text-[0.66rem] text-blue-700"
                    label="elapsed so far"
                  >
                    {formatDuration(openMs)}
                  </Figure>
                ) : beat.durationMs !== null ? (
                  <Figure
                    className="tabular shrink-0 text-[0.66rem] text-muted-foreground"
                    label={`${formatDuration(beat.durationMs)} measured`}
                  >
                    {formatDuration(beat.durationMs)}
                  </Figure>
                ) : (
                  beat.chain.length > 0 && (
                    <Figure
                      className="shrink-0 text-[0.66rem] text-muted-foreground"
                      label={`${COUNT.format(beat.chain.length)} layers`}
                    >
                      {COUNT.format(beat.chain.length)}
                    </Figure>
                  )
                )}

                {/* One wire fact, or nothing: the routed width, or the rail that decided.
                    Never a description of what the beat does. */}
                {beat.caption !== '' && (
                  <span
                    className={cn(
                      'shrink-0 truncate text-[0.66rem]',
                      beat.status === 'blocked' ? 'text-block-ink' : 'text-muted-foreground',
                    )}
                  >
                    {beat.caption}
                  </span>
                )}
              </li>
            )
          })}
        </ol>
      </div>
    </section>
  )
}
