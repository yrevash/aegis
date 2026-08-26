'use client'

import { Check, RotateCw, ShieldCheck } from 'lucide-react'
import { useEffect, useState, type CSSProperties, type ReactElement, type ReactNode } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { ActivityRail } from './ActivityRail'
import { deriveActivity, deriveAgentPanel, isFailure } from './agentLanes'
import type { Beat } from './motion'
import { RunMark } from './RunMark'
import { RunPreview } from './RunPreview'
import { deriveTiming, formatDuration, isGuardStage, type Stage } from './stageTimeline'
import { TRUST_CHECKS } from './trustChecks'
import { useTick } from './useTick'


/** Cost, as a governance figure: always four decimals, never rounded to nothing. */
function formatUsd(usd: number): string {
  return `$${usd.toFixed(4)}`
}

/** Thousands separators, so a six-figure token count is readable at a glance. */
function formatCount(n: number): string {
  return n.toLocaleString('en-US')
}

/** One header figure: a quiet label above the number it names. */
function HeadFigure({
  label,
  children,
  info,
}: {
  label: string
  children: ReactElement | string
  info?: ReactElement | string
}): ReactElement {
  return (
    <div className="min-w-0">
      <span className="eyebrow flex items-center gap-1">
        {label}
        {info !== undefined && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
      </span>
      <div className="mt-0.5 flex min-h-6 items-baseline">{children}</div>
    </div>
  )
}

/**
 * The trust stack, as four checks in the run header.
 *
 * These were a bordered bar of four lit chips sitting between the question and the run —
 * a fifth animating hierarchy on a screen that already had four. They are the same four
 * predicates, reduced to four checks inside the run header, because a summary of what the
 * run will have proved belongs *on* the run rather than above it. The predicates
 * themselves are {@link TRUST_CHECKS}.
 */
function TrustChecks({ state }: { state: RunState }): ReactElement {
  return (
    <ul aria-label="Trust stack" className="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
      {TRUST_CHECKS.map((check) => {
        const done = check.done(state)
        const token = SIGNALS[check.signal]
        return (
          <li key={check.key} className="flex items-center gap-1">
            <span
              className={cn(
                'grid size-3.5 shrink-0 place-items-center rounded-full border transition-colors duration-500 motion-reduce:transition-none',
                done ? cn(token.border, token.bg, token.text) : 'border-border text-muted-foreground/50',
              )}
            >
              {done ? (
                <Check className="size-2" strokeWidth={3} aria-hidden />
              ) : (
                <span aria-hidden className="size-1 rounded-full bg-current" />
              )}
            </span>
            <span
              className={cn(
                'text-[0.7rem] whitespace-nowrap',
                done ? token.text : 'text-muted-foreground',
              )}
            >
              {check.label}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

interface StageRowProps {
  stage: Stage
  /** Milliseconds this stage has been running, measured here. `null` once it lands. */
  liveMs: number | null
  /** The longest stage in the run, for scaling the bar. */
  scaleMs: number
  /** The self-repair round this row opens, or `null` when it continues one. */
  openRound: number | null
  /** The loop's configured budget, when a `reflection` has reported one. */
  roundBudget: number | null
}

/**
 * One stage: what it is, how long it is taking, and what it is doing meanwhile.
 *
 * The bar is a duration chart, so it is one hue at two intensities (DESIGN.md §2) —
 * never the status palette, which would paint a passing guardrail red for the crime of
 * being a guardrail. Identity is carried by the label, the shield and the dot.
 */
function StageRow({
  stage,
  liveMs,
  scaleMs,
  openRound,
  roundBudget,
}: StageRowProps): ReactElement {
  const token = SIGNALS[stage.signal]
  const shownMs = stage.durationMs ?? liveMs
  const width = shownMs === null || scaleMs <= 0 ? 0 : Math.min(100, (shownMs / scaleMs) * 100)
  const guard = isGuardStage(stage.node)
  const lane = stage.agentId !== null

  // What the row says under its bar. A stage in flight says what it is doing; a
  // finished guardrail quotes its own verdict, because that is the moment the eleven
  // seconds it just spent become worth something to read.
  const detail = stage.running ? stage.what : stage.verdict

  return (
    <li
      className={cn(
        '@container/stage flex min-w-0 flex-col gap-0.5 rounded-md px-2 py-1',
        lane && 'ml-3 border-l-2 border-blue-100 pl-3',
        stage.running && 'bg-blue-50',
        stage.blocked && 'bg-block/10',
      )}
    >
      {openRound !== null && (
        <p
          className={cn(
            'flex items-baseline gap-2',
            openRound > 1 && 'mt-1 border-t border-border pt-2',
          )}
        >
          <RotateCw aria-hidden className="size-3 shrink-0 translate-y-0.5 text-blue-700" />
          <span className="eyebrow">
            Round {openRound}
            {roundBudget !== null && ` of ${roundBudget}`}
          </span>
          {openRound > 1 && (
            <span className="text-[0.72rem] text-muted-foreground">
              it judged the previous round insufficient and went again
            </span>
          )}
        </p>
      )}

      {/* One line per stage: what it is, a bar for how long it took, and the figure.
          Fourteen stages is a normal run — the two-line row this used to be turned a
          settled turn into eight hundred pixels of timeline above its own answer. */}
      <div className="flex min-w-0 items-center gap-2">
        <span
          aria-hidden
          className={cn('size-1.5 shrink-0 rounded-full', stage.running && 'animate-pip')}
          style={{ backgroundColor: token.hex, ['--pip-color' as string]: token.hex }}
        />
        {guard && <ShieldCheck aria-hidden className="size-3.5 shrink-0 text-block-ink" />}
        <span
          className={cn(
            'min-w-0 shrink truncate text-[0.82rem] @[26rem]/stage:w-[11rem] @[26rem]/stage:shrink-0',
            stage.running ? 'font-medium text-foreground' : 'text-muted-foreground',
          )}
          title={stage.node}
        >
          {stage.label}
        </span>

        <div className="hidden h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2 @[26rem]/stage:block">
          <div
            className={cn(
              'relative h-full rounded-full transition-[width] duration-[var(--dur-fast)] ease-linear',
              stage.running ? 'bg-blue-600' : 'bg-blue-400',
            )}
            style={{ width: `${width}%` }}
          >
            {stage.running && (
              <span
                aria-hidden
                className="animate-trust-shimmer absolute inset-y-0 left-0 w-1/2 bg-gradient-to-r from-transparent via-white/50 to-transparent"
              />
            )}
          </div>
        </div>

        {/* A fixed slot, empty on a non-LLM node, so every bar track ends at the same
            x and the column of durations reads as a column. A model name that reserved
            width only on the rows that had one left the chart ragged. */}
        <span className="hidden w-[8.5rem] shrink-0 truncate text-right font-mono text-[0.6875rem] text-muted-foreground/80 @[44rem]/stage:inline-block">
          {stage.model ?? ''}
        </span>
        <Figure
          className={cn('shrink-0', stage.running ? 'text-blue-700' : 'text-muted-foreground')}
          label={stage.durationMs === null ? 'elapsed so far' : undefined}
        >
          {shownMs === null ? 'starting' : formatDuration(shownMs)}
        </Figure>
      </div>

      {/* In a narrow column the label needs the whole line, so the bar takes its own.
          This was `sm:hidden` — a **viewport** breakpoint, which fires at 640px of
          browser however wide the column it is measuring happens to be. That is the
          class of bug that has now cost this screen three separate defects, so the
          query is on the row's own container. */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2 @[26rem]/stage:hidden">
        <div
          className={cn('h-full rounded-full', stage.running ? 'bg-blue-600' : 'bg-blue-400')}
          style={{ width: `${width}%` }}
        />
      </div>

      {detail !== '' && (
        <p
          className={cn(
            'pl-3.5 text-[0.72rem] leading-snug',
            stage.blocked ? 'text-block-ink' : 'text-muted-foreground',
          )}
        >
          {detail}
        </p>
      )}
    </li>
  )
}

/** A disclosure that keeps a long list one click away rather than always open. */
function Disclosure({
  summary,
  children,
}: {
  summary: string
  children: ReactNode
}): ReactElement {
  return (
    <details className="min-w-0 rounded-md border border-border bg-surface-2/30 px-2.5 py-1.5">
      <summary className="cursor-pointer text-[0.75rem] font-medium text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {summary}
      </summary>
      <div className="min-w-0 pt-1.5">{children}</div>
    </details>
  )
}

/**
 * What a screen reader is told about this run, in one sentence.
 *
 * The console had exactly one live region — the answer — so a run starting, a run
 * finishing and a lane failing were all silent. This is the sentence the console's own
 * status line announces; it is a pure function of the run so the region's text changes
 * once per transition rather than on every token.
 *
 * @param state - The reduced run, or `null` when no turn has been sent.
 * @returns The sentence to announce, or `''` when there is nothing to say.
 */
export function announceRun(state: RunState | null): string {
  if (state === null) return ''
  if (state.error !== null) return `Run stopped. ${state.error}`
  const failed = deriveAgentPanel(state).lanes.filter((lane) => isFailure(lane.status))
  if (state.running) {
    if (failed.length > 0) return `Run in progress. ${failed[0].label} ${failed[0].status}.`
    return state.events.length === 0 ? 'Run started.' : 'Run in progress.'
  }
  if (state.events.length === 0) return ''
  const timing = deriveTiming(state)
  const measured = timing.measured ? ` in ${formatDuration(timing.measuredMs)}` : ''
  if (failed.length > 0) return `Run finished${measured}. ${failed[0].label} ${failed[0].status}.`
  return `Run finished${measured}.`
}

/**
 * The run panel — one surface for everything a turn is doing while it does it.
 *
 * ## What this replaces
 *
 * Four panels used to animate at once under a question: a bordered stage timeline with
 * three head figures and fourteen always-open rows, a bordered trust bar of four lit
 * chips, the lane board, and the activity rail. Four independent hierarchies, no focal
 * point — measured at fourteen simultaneous regions on a streaming turn, and the
 * owner's verdict on it was "too cluttered".
 *
 * They are one panel with three zones now:
 *
 * - **The header** carries the run's own figures — elapsed, cost, tokens — and the four
 *   trust checks. One place for what the run *is*.
 * - **The left zone** is the lane board, passed in as `children` so this file never
 *   imports it: that is the money shot, N agents working at once, and it is the one
 *   thing that got *more* room out of the merge.
 * - **The right zone** is the merged stage-and-activity feed, with the full fourteen-row
 *   timeline one disclosure away.
 *
 * ## Which figures are whose
 *
 * Every settled duration, every cost and every token count is the wire's own — read
 * from `node_finished`, never computed here. The only figure this component measures is
 * the counter on the stage that is still running, because no event carries a start
 * timestamp; it is labelled as the browser's clock and it is replaced by the server's
 * `duration_ms` the moment the stage lands. The totals sum top-level stages only, so a
 * fan-out's lanes are never added to the `run_team` that contains them.
 *
 * **Cost has one home per state.** While the run streams it is here, because nothing
 * else is on screen to carry it; once the run settles it is the decision strip's, and
 * this panel's settled summary states stages, duration and tokens only. A spend figure
 * printed twice two hundred pixels apart is the clutter this rebuild exists to remove.
 */
export function RunPanel({
  state,
  beat,
  children,
}: {
  state: RunState
  /**
   * The console's heartbeat — the newest event's hue and sequence number.
   *
   * It used to be computed on every turn and handed only to `ResultTabs`, a component
   * that mounts *after* the run ends: a hundred and forty-four wire events, every one of
   * them an invisible tick. The header pulses on each one now, and the signature mark
   * takes the same beat. `null` before the first event, and deliberately ignored once
   * `state.running` is false — `lastSignal` updates on `run_finished` like any other
   * event, so an ungated pulse never stops.
   */
  beat: Beat | null
  /** The lane board — the left zone. Passed in, so this file never imports it. */
  children: ReactNode
}): ReactElement {
  const timing = deriveTiming(state)
  const running = state.running
  const now = useTick(running && timing.current !== null)

  // When the current stage was first seen here. No event carries a wall-clock stamp, so
  // the live counter can only ever be this browser's, and the receipt says so.
  const [startedAt, setStartedAt] = useState<{ key: string; at: number } | null>(null)
  const currentKey = timing.current?.key ?? null
  useEffect(() => {
    if (currentKey === null) return
    setStartedAt((prev) => (prev?.key === currentKey ? prev : { key: currentKey, at: Date.now() }))
  }, [currentKey])

  // No early return: this panel owns the lane board now, and a run that reported no
  // stage at all still has lanes — the supervisor's, at minimum — to show.
  const liveMs =
    currentKey !== null && startedAt?.key === currentKey ? Math.max(0, now - startedAt.at) : null
  const scaleMs = Math.max(timing.peakMs, liveMs ?? 0, 1)
  const total = timing.measuredMs + (liveMs ?? 0)
  const done = timing.stages.filter((s) => !s.running).length
  const activityCount = deriveActivity(state).length

  const stageList = (
    <ol className="flex min-w-0 flex-col gap-0.5">
      {timing.stages.map((stage, index) => (
        <StageRow
          key={stage.key}
          stage={stage}
          liveMs={stage.key === currentKey ? liveMs : null}
          scaleMs={scaleMs}
          // A round header where the round changes. The self-repair loop runs
          // `plan → gate → act → reflect` once per round, and eight identical-looking
          // rows read as duplicated noise rather than as an agent that judged its own
          // first attempt insufficient and went again.
          openRound={
            stage.round !== null &&
            stage.round !== (index === 0 ? null : timing.stages[index - 1].round)
              ? stage.round
              : null
          }
          roundBudget={timing.roundBudget}
        />
      ))}
    </ol>
  )

  return (
    <section
      aria-label="Run"
      className="flex min-w-0 flex-col gap-3 rounded-lg border border-border bg-card px-4 py-3"
    >
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5">
          <span className="flex items-center gap-2">
            {/* The signature mark stands where a clock icon used to. A clock said the
                one thing the three head figures beside it already say; the mark says
                what the run is *doing* — screening, thinking, fanned out, gated,
                blocked — and every one of those is a fact the wire named. */}
            <RunMark state={state} beat={beat} />
            {/* The header takes the beat: one same-hue pulse per wire event, re-keyed on
                `seq` so two consecutive events of the same hue still fire twice. The key
                is on the heading rather than on the row, because re-keying the row would
                remount the mark on every event and restart its animation each time. */}
            <h3
              key={running && beat !== null ? beat.seq : 'still'}
              style={
                running && beat !== null ? ({ '--beat': beat.hex } as CSSProperties) : undefined
              }
              className={cn(
                'eyebrow rounded-sm',
                running && beat !== null && 'animate-beat',
              )}
            >
              {running
                ? `Running · ${done}/${timing.stages.length}`
                : `Run · ${timing.stages.length} ${timing.stages.length === 1 ? 'stage' : 'stages'}`}
            </h3>
          </span>

          {/* Settled, the head figures collapse into this one line: the ledger of what
              ran. Cost is deliberately not here — it is the decision strip's, directly
              under this panel. */}
          {!running && timing.measured && (
            <span className="tabular flex min-w-0 items-baseline gap-1.5 font-mono text-[0.72rem] text-muted-foreground">
              <span>{formatDuration(timing.measuredMs)}</span>
              <span aria-hidden>·</span>
              <span>{formatCount(timing.tokens)} tok</span>
            </span>
          )}

          <TrustChecks state={state} />
        </div>

        {running && (
          <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
            <HeadFigure
              label="Elapsed"
              info={
                <>
                  The stages this run has finished, summed from each one’s own{' '}
                  <span className="font-mono">duration_ms</span>, plus the stage in flight
                  counted by this browser. No event carries a start timestamp, so the live
                  part is the only figure on this panel the server did not send.
                </>
              }
            >
              <Figure size="stat" className="text-foreground">
                {timing.measured || liveMs !== null ? formatDuration(total) : 'not measured'}
              </Figure>
            </HeadFigure>

            <HeadFigure label="Cost">
              {timing.measured ? (
                <Figure className="text-foreground">{formatUsd(timing.costUsd)}</Figure>
              ) : (
                <span className="text-[0.8125rem] leading-5 text-muted-foreground">
                  not measured
                </span>
              )}
            </HeadFigure>

            <HeadFigure label="Tokens">
              {timing.measured ? (
                <Figure className="text-foreground">{formatCount(timing.tokens)}</Figure>
              ) : (
                <span className="text-[0.8125rem] leading-5 text-muted-foreground">
                  not measured
                </span>
              )}
            </HeadFigure>
          </div>
        )}
      </header>

      {/* The run's scaffold — the same four-beat spine an empty console draws, filled in
          as the run walks it. It is here rather than beside the panel because the shape
          it draws *is* this panel's contents arriving: the reader watches one picture
          fill, not two pictures agree. Full width, above the lanes, inside the card. */}
      <RunPreview state={state} />

      {running ? (
        /* Again a container query: the feed only earns a column when the turn itself is
           wide, which at 1440px with both rails out it is not. */
        <div className="grid min-w-0 gap-4 @[46rem]/turn:grid-cols-[minmax(0,1fr)_16rem]">
          <div className="flex min-w-0 flex-col gap-3">{children}</div>
          <div className="flex min-w-0 flex-col gap-2">
            <ActivityRail state={state} current={timing.current} liveMs={liveMs} />
            {timing.stages.length > 0 && (
              <Disclosure summary={`All stages · ${timing.stages.length}`}>{stageList}</Disclosure>
            )}
          </div>
        </div>
      ) : (
        <div className="flex min-w-0 flex-col gap-3">
          {children}
          <div className="flex min-w-0 flex-wrap gap-2">
            {timing.stages.length > 0 && (
              <div className="min-w-0 flex-1 basis-64">
                <Disclosure summary={`All stages · ${timing.stages.length}`}>
                  {stageList}
                </Disclosure>
              </div>
            )}
            {activityCount > 0 && (
              <div className="min-w-0 flex-1 basis-64">
                <Disclosure summary={`Activity · ${activityCount}`}>
                  <ActivityRail state={state} />
                </Disclosure>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  )
}
