'use client'

import { Quote, ShieldAlert } from 'lucide-react'
import { useEffect, useRef, useState, type ReactElement } from 'react'

import { Receipt } from '@/components/primitives/Receipt'
import { cn } from '@/lib/utils'

import { CHAIN, FINAL_INDEX, nextIndex, type ChainLink } from './chainOfCustody'

/**
 * The chain of custody — the one thing this page is meant to be remembered by.
 *
 * **Why this and not a screenshot.** The claim a jury has to believe in ten
 * seconds is that a run is *governed*, and a screenshot of a console cannot show
 * that: it shows an answer, which is what a chatbot also produces. What separates
 * the two is the record a run leaves behind it — the rail that fired, the router
 * that reported who decided the width, the person who signed for a write. So the
 * hero artefact is that record, drawn as the thing it actually is: a custody
 * chain, with the line splitting under the one node where four specialists run
 * at once and a single amber link where the run can stop and ask a human.
 *
 * **Where the page spends its boldness.** All of it, here. This is the only dark
 * surface, the only motion and the only reserved status colour on the page, and
 * everything under it is deliberately quiet. The navy is `--rail` — the product's
 * own single dark surface, not a hero gradient invented for a landing page.
 *
 * **The colour polarity is inverted on purpose.** DESIGN.md §2 makes `--risk` a
 * *fill* weight and `--risk-ink` the text that sits on it, because on white the
 * fill is too light to read. On `--rail` the polarity flips — `--risk` measures
 * 10.6:1 on this navy and `--risk-ink` would be unreadable — so the gate link
 * sets `--risk` as ink. The rule the tokens encode is contrast, and this is that
 * rule applied rather than broken. `--blue-600` stays off text here for the same
 * reason: it is 3.6:1 on navy, which is a line and a dot, not a word.
 *
 * **Nothing here is a figure**, and the receipt under it says so. Every label is
 * lifted from the running system — see `chainOfCustody.ts` for where each one lives.
 */

/** How long each link holds while the chain plays itself in, in ms. */
const STEP_MS = 850

/**
 * The eyebrow, on navy.
 *
 * Not the `.eyebrow` utility with a colour override: `.eyebrow` sets its own
 * `color`, both classes land in the same cascade layer, and which one wins then
 * depends on emission order rather than on intent. Restating the four properties
 * is shorter than the `!important` that would be needed to make the override
 * reliable, and it does not leave a landmine for the next person who edits it.
 */
const RAIL_EYEBROW =
  'font-mono text-[0.6875rem] font-medium tracking-[0.16em] text-rail-text uppercase' 

export function RunChain(): ReactElement {
  const [active, setActive] = useState(0)
  // Once a person touches the chain it is theirs; the intro never resumes.
  const driven = useRef(false)

  const take = (index: number): void => {
    driven.current = true
    setActive(index)
  }

  useEffect(() => {
    // Reduced motion gets the same terminal state without the journey: the chain
    // rests on its last link, which is the one the whole page is arguing for.
    const still = window.matchMedia('(prefers-reduced-motion: reduce)')
    if (still.matches) {
      setActive(FINAL_INDEX)
      return
    }
    let index = 0
    const id = window.setInterval(() => {
      if (driven.current) {
        window.clearInterval(id)
        return
      }
      const next = nextIndex(index)
      // No wrap. An infinite ambient loop in a hero is DESIGN.md §6's named "no".
      if (next === null) {
        window.clearInterval(id)
        return
      }
      index = next
      setActive(next)
    }, STEP_MS)
    return () => window.clearInterval(id)
  }, [])

  const link = CHAIN[active]

  return (
    <section id="run" className="scroll-mt-16 bg-background">
      <div className="mx-auto max-w-6xl px-4 pb-16 sm:px-6 sm:pb-20">
        <div className="shadow-pop overflow-hidden rounded-lg bg-rail">
          <div className="flex flex-col gap-8 p-5 sm:p-8 lg:grid lg:grid-cols-[minmax(0,7fr)_minmax(0,9fr)] lg:gap-12">
            {/* ── The rail: the whole shape of a run, always visible ───────── */}
            <div>
              <h2 className={RAIL_EYEBROW}>Chain of custody</h2>
              <p className="mt-2 text-[0.9375rem] leading-relaxed text-white/80">
                One question, from the request to the sentence that answers it.
              </p>

              <ol className="mt-6" aria-label="The stages of one run">
                {CHAIN.map((entry, index) => (
                  <ChainRow
                    key={entry.id}
                    link={entry}
                    index={index}
                    active={index === active}
                    onSelect={() => take(index)}
                  />
                ))}
              </ol>
            </div>

            {/* ── The record the selected link leaves ──────────────────────── */}
            <div
              aria-live="polite"
              className="flex min-w-0 flex-col rounded-lg border border-rail-border bg-rail-hover/35 p-5 sm:p-7 lg:justify-center"
            >
              <p className={RAIL_EYEBROW}>What it records</p>
              <p
                className={cn(
                  'tabular mt-3 font-mono text-xl leading-tight font-medium break-words sm:text-2xl',
                  link.kind === 'gate' ? 'text-risk' : 'text-blue-400',
                )}
              >
                {link.node}
              </p>
              <p className="mt-5 max-w-prose text-pretty text-base leading-relaxed text-white/85">
                {link.note}
              </p>

              <ul className="mt-6 space-y-2">
                {link.record.map((line) => (
                  <li
                    key={line}
                    className="tabular font-mono text-[0.78rem] leading-relaxed break-words text-rail-text"
                  >
                    {line}
                  </li>
                ))}
              </ul>

              {link.quote == null ? null : (
                <blockquote className="mt-5 flex items-start gap-2.5 border-t border-rail-border pt-4">
                  <Quote className="mt-0.5 size-3.5 shrink-0 text-risk" aria-hidden />
                  <p className="text-pretty text-[0.875rem] leading-relaxed font-medium text-risk">
                    {link.quote}
                  </p>
                </blockquote>
              )}

              {/* The panel stretches to the rail beside it, so it ends with the
                  position the rail used to carry as a column of ordinals. */}
              <p className="tabular mt-8 font-mono text-[0.6875rem] text-rail-text/70">
                stage {active + 1} of {CHAIN.length}
              </p>
            </div>
          </div>
        </div>

        {/* The page's own rule, applied to the page. The chain is the sequence in
            the agent graph — it is not a replay of a run, and a landing page that
            let a reader think it was would be inventing the figures it omits.

            The sentence is rendered rather than passed as the receipt's `detail`:
            `Receipt` moves any detail over 36 characters into an `InfoTip`, and a
            jury reading a landing page does not hover. */}
        <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:items-baseline sm:gap-4">
          <Receipt
            label="This diagram"
            origin="aegis.agent.graph"
            detail="the stage sequence"
            className="shrink-0 border-t-0 pt-0"
          />
          <p className="max-w-prose text-pretty text-xs leading-relaxed text-muted-foreground">
            The shape of a run, not a recording of one — a run&rsquo;s own figures live
            behind the login.
          </p>
        </div>
      </div>
    </section>
  )
}

/**
 * One link, its node on the line, and — under the team link — the four lanes.
 *
 * The lanes render whether or not the team link is selected, because they are the
 * picture: a reader who never clicks anything should still see the line split
 * four ways and come back together. That is the fan-out, and it is the claim.
 */
function ChainRow({
  link,
  index,
  active,
  onSelect,
}: {
  link: ChainLink
  index: number
  active: boolean
  onSelect: () => void
}): ReactElement {
  const first = index === 0
  const last = index === FINAL_INDEX
  const gate = link.kind === 'gate'

  return (
    <li className="relative">
      <button
        type="button"
        onClick={onSelect}
        aria-current={active ? 'step' : undefined}
        className={cn(
          'group flex w-full touch-manipulation items-center gap-3.5 rounded-md py-2 pr-3 pl-1 text-left outline-none',
          'transition-colors duration-[--dur-fast] hover:bg-rail-hover/60',
          'focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 focus-visible:ring-offset-rail',
          active && 'bg-rail-hover/70',
        )}
      >
        {/* The line, drawn per row so it needs no measurement to stay joined. */}
        {/* `-my-2` cancels the button's own padding so the segments meet: without
            it the line stopped at each content box and the chain read as nine
            unconnected dots. */}
        <span aria-hidden className="relative -my-2 flex w-4 shrink-0 justify-center self-stretch">
          <span
            className={cn(
              'absolute inset-y-0 w-px bg-rail-text/25',
              first && 'top-1/2',
              last && !link.lanes && 'bottom-1/2',
            )}
          />
          {gate ? (
            <ShieldAlert className="relative size-4 self-center text-risk" strokeWidth={2} />
          ) : (
            <span
              className={cn(
                'relative size-2.5 self-center rounded-full ring-4 ring-rail transition-colors duration-[--dur-fast]',
                active ? 'bg-blue-600' : 'bg-rail-text/45 group-hover:bg-blue-400',
              )}
            />
          )}
        </span>

        <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
          <span
            className={cn(
              'text-[0.9375rem] font-medium transition-colors duration-[--dur-fast]',
              active ? 'text-white' : 'text-white/65 group-hover:text-white',
            )}
          >
            {link.label}
          </span>
          {gate ? <span className="font-mono text-[0.6875rem] text-risk">gate</span> : null}
        </span>
        {/* The numeral is decoration for a sighted reader; what the control does
            is not obvious from the stage name alone, so it is said here. */}
        <span className="sr-only">
          {active ? '— showing what this stage records' : '— show what this stage records'}
        </span>
      </button>

      {link.lanes == null ? null : (
        <ul aria-label="The concurrent specialists" className="relative pb-1">
          {link.lanes.map((lane, laneIndex) => {
            const lastLane = laneIndex === link.lanes!.length - 1
            return (
              <li key={lane.label} className="flex items-center gap-3.5 py-1 pr-3 pl-1">
                <span aria-hidden className="relative -my-1 flex w-4 shrink-0 justify-center self-stretch">
                  {/* The trunk continues past every lane but the last, so the four
                      strands read as a split that rejoins rather than a dead end. */}
                  <span
                    className={cn(
                      'absolute inset-y-0 w-px bg-rail-text/25',
                      lastLane && 'bottom-1/2',
                    )}
                  />
                </span>
                <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2.5 gap-y-0.5">
                  <span aria-hidden className="h-px w-3 shrink-0 bg-rail-text/25 sm:w-4" />
                  <span
                    aria-hidden
                    className={cn(
                      'size-1.5 shrink-0 rounded-full',
                      lane.writes ? 'bg-risk' : 'bg-blue-400',
                    )}
                  />
                  <span className="text-[0.8125rem] text-white/70">{lane.label}</span>
                  <span
                    className={cn(
                      'font-mono text-[0.6875rem]',
                      lane.writes ? 'text-risk' : 'text-rail-text/70',
                    )}
                  >
                    {lane.remit}
                  </span>
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </li>
  )
}
