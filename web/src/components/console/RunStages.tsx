'use client'

import { Clock, RotateCw, ShieldCheck, Sparkles } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { deriveTiming, formatDuration, isGuardStage, type Stage } from './stageTimeline'

/** How often the live counters tick. Fast enough to read as live, slow enough to read. */
const TICK_MS = 100

/** The clock, while a run is in flight. Stops the moment it is not. */
function useTick(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    setNow(Date.now())
    const id = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(id)
  }, [active])
  return now
}

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
        'flex min-w-0 flex-col gap-0.5 rounded-md px-2 py-1',
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
            'min-w-0 shrink truncate text-[0.82rem] sm:w-[11rem] sm:shrink-0',
            stage.running ? 'font-medium text-foreground' : 'text-muted-foreground',
          )}
          title={stage.node}
        >
          {stage.label}
        </span>

        <div className="hidden h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2 sm:block">
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
        <span className="hidden w-[8.5rem] shrink-0 truncate text-right font-mono text-[0.66rem] text-muted-foreground/80 lg:inline-block">
          {stage.model ?? ''}
        </span>
        <Figure
          className={cn('shrink-0', stage.running ? 'text-blue-700' : 'text-muted-foreground')}
          label={stage.durationMs === null ? 'elapsed so far' : undefined}
        >
          {shownMs === null ? 'starting' : formatDuration(shownMs)}
        </Figure>
      </div>

      {/* Below `sm` the label needs the whole line, so the bar takes its own. */}
      <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2 sm:hidden">
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

      {/* The rails this stage runs, in order. Shown while it runs, because this is the
          three-to-eight seconds the console used to spend on a spinner — and a reader
          who can see what is being screened reads a governed product rather than a
          slow one. */}
      {guard && stage.running && stage.chain.length > 0 && (
        <p className="flex flex-wrap items-center gap-1 pl-3.5">
          {stage.chain.map((layer) => (
            <span
              key={layer}
              className="rounded-full border border-border bg-surface px-1.5 py-0.5 font-mono text-[0.62rem] text-muted-foreground"
            >
              {layer}
            </span>
          ))}
          <InfoTip label="About the rail chain">
            The layers this rail runs, in the order{' '}
            <span className="font-mono">aegis/guardrails/pipeline.py</span> runs them. The
            stream reports one verdict and, on a block, the layer that produced it — it does
            not report per-layer progress, so nothing here is lit as it passes.
          </InfoTip>
        </p>
      )}
    </li>
  )
}

/**
 * The run's spine — every stage it has entered, live, with what each one cost.
 *
 * ## Why this panel exists
 *
 * A measured run: the two guardrails take 10.9 of its 29 seconds, and for all 10.9 the
 * console showed a spinner. That is the single largest thing wrong with this screen,
 * and it is not a performance problem — an input rail that screens for prompt injection
 * and PII before a model sees a syllable is the *product*. Hiding it behind a spinner
 * spends the wait and buys nothing with it.
 *
 * So every stage the run enters appears here the moment it starts, with a brief naming
 * what it is doing, a bar that grows while it works, and its own duration the instant
 * the wire reports one.
 *
 * ## Which figures are whose
 *
 * Every settled duration, every cost and every token count is the wire's own — read
 * from `node_finished`, never computed here. The only figure this component measures is
 * the counter on the stage that is still running, because no event carries a start
 * timestamp; it is labelled as the browser's clock and it is replaced by the server's
 * `duration_ms` the moment the stage lands. The totals sum top-level stages only, so a
 * fan-out's lanes are never added to the `run_team` that contains them.
 */
export function RunStages({ state }: { state: RunState }): ReactElement | null {
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

  if (timing.stages.length === 0 && !running) return null

  const liveMs =
    currentKey !== null && startedAt?.key === currentKey ? Math.max(0, now - startedAt.at) : null
  const scaleMs = Math.max(timing.peakMs, liveMs ?? 0, 1)
  const total = timing.measuredMs + (liveMs ?? 0)
  const done = timing.stages.filter((s) => !s.running).length

  return (
    <section
      aria-label="Stages"
      className="flex flex-col gap-3 rounded-lg border border-border bg-card px-4 py-3"
    >
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-2">
        <span className="flex items-center gap-2">
          <Clock aria-hidden className="size-4 text-muted-foreground" />
          <h3 className="eyebrow">
            {running ? 'Running' : 'Stages'} · {done}/{timing.stages.length}
          </h3>
        </span>

        <div className="flex flex-wrap items-end gap-x-6 gap-y-2">
          <HeadFigure
            label={running ? 'Elapsed' : 'Measured'}
            info={
              running ? (
                <>
                  The stages this run has finished, summed from each one’s own{' '}
                  <span className="font-mono">duration_ms</span>, plus the stage in flight
                  counted by this browser. No event carries a start timestamp, so the live
                  part is the only figure on this panel the server did not send.
                </>
              ) : (
                <>
                  Summed from each stage’s own <span className="font-mono">duration_ms</span>,
                  across top-level stages only — a fan-out’s lanes run inside{' '}
                  <span className="font-mono">run_team</span>, whose duration already covers
                  them.
                </>
              )
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
      </header>

      {timing.stages.length === 0 ? (
        <p className="flex items-center gap-2 text-[0.78rem] text-muted-foreground">
          <Sparkles aria-hidden className="size-3.5" />
          The question is on its way to the input rail. Every stage it enters appears here.
        </p>
      ) : (
        <ol className="flex flex-col gap-0.5">
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
                stage.round !== null && stage.round !== (index === 0 ? null : timing.stages[index - 1].round)
                  ? stage.round
                  : null
              }
              roundBudget={timing.roundBudget}
            />
          ))}
        </ol>
      )}
    </section>
  )
}
