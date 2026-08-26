'use client'

import { Crosshair, Loader2, Play, Square } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from 'react'

import { chartHex } from '@/components/charts/palette'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { Signal } from '@/config/signals'
import { errorSentence } from '@/lib/api/apiError'
import { streamGuardrailDemo } from '@/lib/api/guardrailDemo'
import { cn } from '@/lib/utils'

import {
  firingLine,
  isKnownVerdict,
  selectBatteryProbes,
  type FiringOutcome,
  type FiringPoint,
  type FiringProbe,
  type FiringRail,
  type FiringSummary,
} from './firingLine'
import { BATTERY_PROBES } from './batteryProbes'

/**
 * Milliseconds between one probe landing and the next being fired.
 *
 * Not a throttle — the rail is already the slow part. It is what turns a swarm into a
 * *line*: at this spacing exactly one row is animating at any instant, which is the
 * whole motion budget DESIGN.md §6 allows a panel, and a reader can follow each verdict
 * to the mark it produced instead of watching twelve appear at once.
 */
const GAP_MS = 700

/**
 * How many probes one press fires.
 *
 * The battery's input stage runs to dozens of payloads and the rail takes real seconds
 * per probe, so firing all of them is minutes of staring. The cap is stated on the panel
 * — "12 of 34 input probes" — because a truncated battery reported as *the* battery is
 * the same lie as an invented one.
 */
const MAX_PROBES = 12

/**
 * One benign payload fired before the battery, whose verdict is thrown away.
 *
 * The rail's first `check_input` of a process loads the PII analyser's model, and that
 * load is charged to whichever call happens to be first: measured against the live
 * endpoint, call one reported `5159.8 ms` and calls two and three reported `5.7 ms` and
 * `6.3 ms`. Plotting the cold call would set the chart's ceiling three orders of
 * magnitude above every other mark and flatten the whole battery onto the baseline —
 * a chart whose only legible feature is an artefact of process startup.
 *
 * So the load is paid up front, in the open, by a payload that is not a probe and is
 * never plotted or counted. What the chart then shows is the rail's steady-state cost,
 * which is the number the panel claims. This is stated on the panel rather than done
 * quietly, because a discarded measurement the reader cannot see is the kind of
 * omission this screen exists to argue against.
 */
const WARMUP_PROMPT = 'What are the standard business hours for customer support?'

/** Viewport of the strip chart. Fixed units; the element scales, the geometry does not. */
const VIEW_W = 320
const VIEW_H = 96
const PAD = 8

/** Headroom above the slowest probe, so the peak mark is not welded to the top edge. */
const HEADROOM = 1.15

/** Height of the dashed stub that marks a probe which produced no measurement. */
const UNMEASURED_STUB = 10

/**
 * Verdict → the signal that carries it, matching `GuardrailReveal.VERDICT_META`.
 *
 * `flag` is `risk`, not `block`: the rail noted something and let the request through,
 * and colouring an advisory as a refusal overstates it. Anything this build has never
 * heard of resolves to `neutral` rather than throwing — the same defect
 * `GuardrailReveal.UNKNOWN_VERDICT` exists for, where a `Record<GuardVerdict, …>`
 * returned `undefined` for `flag` and took the console down. A backend can ship a new
 * verdict before this client is rebuilt.
 */
function verdictSignal(verdict: string | null): Signal {
  switch (verdict) {
    case 'pass':
      return 'ok'
    case 'block':
      return 'block'
    case 'redact':
    case 'flag':
      return 'risk'
    default:
      return 'neutral'
  }
}

/**
 * The word printed on a mark. Colour is never the only channel (DESIGN.md §2).
 *
 * An unknown verdict prints the raw code, because inventing a friendly label for a
 * verdict we do not understand is a worse lie than the code itself.
 */
function outcomeWord(outcome: FiringOutcome): string {
  if (outcome.kind === 'silent') return 'no verdict'
  if (outcome.kind === 'failed') return 'unreachable'
  return outcome.verdict
}

/** The sentence under a mark — the rail's own words, or what happened instead. */
function outcomeSentence(outcome: FiringOutcome): string {
  if (outcome.kind === 'verdict') return outcome.rationale
  if (outcome.kind === 'failed') return outcome.message
  // The demonstrator stream has no RUN_ERROR frame: a rail that raised looks exactly
  // like a stream that stopped after STEP_STARTED. Saying so is the honest render.
  return 'The stream closed after the step opened and no verdict frame arrived, so nothing was measured and nothing was decided.'
}

/** Shorter labels for the rail layers whose raw token would read badly on a chip. */
const LAYER_LABEL: Record<string, string> = {
  content_safety: 'content',
  injection_unavailable: 'injection · screen unavailable',
}

/** One measured figure in the summary row — label above, value below (§3). */
function FiringFigure({
  label,
  value,
  absent,
}: {
  label: string
  value: string
  /** True when the figure does not exist yet; it is stated, never drawn as a zero. */
  absent?: boolean
}): ReactElement {
  return (
    <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-3.5">
      <p className="eyebrow">{label}</p>
      {absent ? (
        <p className="mt-1 text-sm leading-relaxed text-muted-foreground">not yet measured</p>
      ) : (
        <Figure size="stat" className="mt-1 block break-words text-foreground">
          {value}
        </Figure>
      )}
    </div>
  )
}

/**
 * The strip chart: one drop-line per fired probe, x = firing order, y = the rail's ms.
 *
 * Hand-built on the `MiniTrend` precedent — fixed `viewBox`, `preserveAspectRatio="none"`
 * so it fills whatever width it is given, `vectorEffect="non-scaling-stroke"` so a
 * stretched mark keeps its weight, and colour only from `chartHex`. Two consequences of
 * that non-uniform scale are load-bearing:
 *
 * - **The marks are drop-lines, not dots.** A circle under `preserveAspectRatio="none"`
 *   is an ellipse whose eccentricity depends on the container width, which is a mark
 *   that changes shape when the sidebar collapses. A vertical stem with a cap is
 *   unaffected, and it reads the y-value more precisely than a dot does anyway.
 * - **Nothing is interpolated between marks.** These are independent verdicts on
 *   independent payloads; a line joining them would assert a trend across probes that
 *   have nothing to do with each other.
 *
 * With no measurement yet it draws the quiet dashed baseline rather than inventing a
 * shape — the same rule `MiniTrend` follows below two finite points.
 *
 * `aria-hidden`: the accessible representation is the labelled list beside it.
 */
function FiringChart({
  summary,
  slots,
}: {
  summary: FiringSummary
  /** Total probes selected, so the line marches across the battery rather than the data. */
  slots: number
}): ReactElement {
  const baseline = VIEW_H - PAD
  const measured = summary.points
    .map((p) => p.totalMs)
    .filter((ms): ms is number => ms !== null)
  const top = measured.length === 0 ? 0 : Math.max(...measured) * HEADROOM
  const stepX = VIEW_W / Math.max(slots, 1)
  const y = (ms: number): number =>
    top <= 0 ? baseline : baseline - (ms / top) * (VIEW_H - PAD * 2)

  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      preserveAspectRatio="none"
      className="w-full"
      style={{ height: VIEW_H }}
      aria-hidden
    >
      <line
        x1="0"
        y1={baseline}
        x2={VIEW_W}
        y2={baseline}
        stroke={chartHex('neutral')}
        strokeOpacity="0.3"
        strokeWidth="1"
        strokeDasharray={measured.length === 0 ? '2 3' : undefined}
        vectorEffect="non-scaling-stroke"
      />
      {summary.points.map((point) => {
        const x = (point.index - 0.5) * stepX
        const hex = chartHex(verdictSignal(point.verdict))
        if (point.totalMs === null) {
          // Nothing was measured, so nothing may be plotted against the scale. A short
          // dashed stub says "this probe was fired and produced no figure"; drawing it
          // at zero would claim an instantaneous rail, and at the top a slow one.
          return (
            <line
              key={point.probe.id}
              x1={x}
              y1={baseline}
              x2={x}
              y2={baseline - UNMEASURED_STUB}
              stroke={hex}
              strokeOpacity="0.7"
              strokeWidth="1.5"
              strokeDasharray="2 2"
              vectorEffect="non-scaling-stroke"
            />
          )
        }
        const yv = y(point.totalMs)
        return (
          <g key={point.probe.id}>
            <line
              x1={x}
              y1={baseline}
              x2={x}
              y2={yv}
              stroke={hex}
              strokeOpacity="0.45"
              strokeWidth="1.5"
              vectorEffect="non-scaling-stroke"
            />
            <line
              x1={x - stepX * 0.3}
              y1={yv}
              x2={x + stepX * 0.3}
              y2={yv}
              stroke={hex}
              strokeWidth="3"
              strokeLinecap="butt"
              vectorEffect="non-scaling-stroke"
            />
          </g>
        )
      })}
    </svg>
  )
}

/** One landed probe: what was sent, what the rail said, and how long it took. */
function ArrivalRow({ point, newest }: { point: FiringPoint; newest: boolean }): ReactElement {
  const signal = verdictSignal(point.verdict)
  const word = outcomeWord(point.outcome)
  const layer = point.layer === null ? null : (LAYER_LABEL[point.layer] ?? point.layer)

  return (
    <li
      className={cn(
        'rounded-lg border border-border bg-surface-2/40 p-3',
        // Only the row that just landed animates, which is exactly one moving element
        // per arrival. The global `prefers-reduced-motion` block in `globals.css`
        // collapses the animation to nothing without removing the row.
        newest && 'animate-reveal',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Figure className="text-muted-foreground" label={`probe ${point.index}`}>
          {String(point.index).padStart(2, '0')}
        </Figure>
        <Badge tone={signal} className="uppercase">
          {word}
        </Badge>
        {point.totalMs === null ? (
          <span className="text-xs text-muted-foreground">no rail time recorded</span>
        ) : (
          <Figure unit="ms" className="text-foreground">
            {point.totalMs.toFixed(1)}
          </Figure>
        )}
        {layer === null ? null : (
          <Figure className="min-w-0 break-words text-muted-foreground">{layer}</Figure>
        )}
        <Badge tone="neutral" className="ml-auto">
          {point.probe.owasp}
        </Badge>
      </div>
      <p
        translate="no"
        className="mt-2 line-clamp-2 font-mono text-[0.7rem] leading-snug break-words text-muted-foreground"
        title={point.probe.prompt}
      >
        {point.probe.prompt}
      </p>
      <p className="mt-1.5 text-[0.74rem] leading-snug break-words text-foreground">
        {outcomeSentence(point.outcome)}
      </p>
      {point.verdict !== null && !isKnownVerdict(point.verdict) ? (
        <p className="mt-1 text-[0.7rem] leading-snug text-muted-foreground">
          This build has no treatment for that verdict, so it is shown as the rail spelled it.
        </p>
      ) : null}
    </li>
  )
}

/** Resolve after `ms`, or immediately when the run is stopped. Never leaks its timer. */
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve()
      return
    }
    let timer: ReturnType<typeof setTimeout>
    const done = (): void => {
      clearTimeout(timer)
      signal.removeEventListener('abort', done)
      resolve()
    }
    timer = setTimeout(done, ms)
    signal.addEventListener('abort', done, { once: true })
  })
}

export interface RailFiringLineProps {
  /** The input rail cards, so a deciding layer can be named by the rail that owns it. */
  rails: FiringRail[]
}

/**
 * The rail, firing — real adversarial payloads, the real input guardrail, live.
 *
 * Each probe is a payload the shipped red-team battery actually ran, replayed one at a
 * time against `GET /v1/stream/guardrail-demo`, which runs a real
 * `Guardrails().stream_check_input_agui`. The mark that lands is the verdict frame's own
 * `per_rail_timing_ms.total` — a figure the server measured with `time.monotonic()`
 * around `check_input`, not one this component timed.
 *
 * **The honesty ladder for payloads has two rungs and no third.** A stored run supplies
 * them, or a stated absence does. There is no fixture and no fallback: this is the one
 * surface whose entire subject is adversarial honesty, and inventing a probe on it would
 * discredit every other figure on the screen. The absence has two spellings, because the
 * screen already encodes two reasons the probes can be missing — nothing is stored, or
 * `GET /redteam/runs` refused a tenant-pinned principal a platform-wide reading.
 */
export function RailFiringLine({ rails }: RailFiringLineProps): ReactElement {
  const [arrivals, setArrivals] = useState<{ probeId: string; outcome: FiringOutcome }[]>([])
  const [firing, setFiring] = useState(false)
  const [warming, setWarming] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  // Stop an in-flight line when the panel goes away; an aborted fetch is the only way
  // to stop a stream that is waiting on a rail.
  useEffect(() => () => abortRef.current?.abort(), [])

  const probes = useMemo<FiringProbe[]>(
    () => selectBatteryProbes(BATTERY_PROBES, MAX_PROBES),
    [],
  )
  const summary = useMemo(() => firingLine(probes, arrivals, rails), [probes, arrivals, rails])

  const stop = useCallback((): void => {
    abortRef.current?.abort()
    abortRef.current = null
    setFiring(false)
  }, [])

  const fire = useCallback((): void => {
    abortRef.current?.abort()
    const controller = new AbortController()
    abortRef.current = controller
    setArrivals([])
    setFiring(true)

    const fireAll = async (): Promise<void> => {
      // Pay the model load before the first probe is timed. A failure here is not
      // reported: it is not a measurement, and whatever broke will break the first
      // real probe a moment later and be reported there, in its own row, with its
      // own error sentence.
      setWarming(true)
      try {
        await streamGuardrailDemo(WARMUP_PROMPT, controller.signal)
      } catch {
        /* reported by the first real probe, not here */
      } finally {
        setWarming(false)
      }
      if (controller.signal.aborted) return

      for (const [i, probe] of probes.entries()) {
        if (controller.signal.aborted) return
        if (i > 0) await pause(GAP_MS, controller.signal)
        if (controller.signal.aborted) return
        let outcome: FiringOutcome
        try {
          const verdict = await streamGuardrailDemo(probe.prompt, controller.signal)
          outcome =
            verdict === null
              ? { kind: 'silent' }
              : {
                  kind: 'verdict',
                  verdict: verdict.verdict,
                  layer: verdict.rules[0] ?? null,
                  rationale: verdict.rationale,
                  totalMs: verdict.per_rail_timing_ms.total,
                  redactions: verdict.redactions,
                }
        } catch (error) {
          if (controller.signal.aborted) return
          outcome = {
            kind: 'failed',
            message: errorSentence(
              error,
              'The demonstrator stream did not open. Check the backend is reachable.',
            ),
          }
        }
        if (controller.signal.aborted) return
        setArrivals((prev) => [...prev, { probeId: probe.id, outcome }])
      }
    }

    void fireAll().finally(() => {
      if (abortRef.current === controller) {
        abortRef.current = null
        setFiring(false)
      }
    })
  }, [probes])

  const newest = summary.points.at(-1)

  return (
    <Card className="rounded-lg">
      <CardHeader
        eyebrow="real payloads · the real input rail · the server's own milliseconds"
        title={
          <span className="flex items-center gap-2">
            <Crosshair className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            The rail, firing
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            {summary.fired > 0 ? (
              <Badge tone={summary.blocked > 0 ? 'block' : 'neutral'} className="uppercase">
                {summary.blocked} blocked / {summary.fired} fired
              </Badge>
            ) : null}
            <InfoTip label="About the firing line">
              Each probe is replayed against{' '}
              <code className="font-mono">GET /v1/stream/guardrail-demo</code>, which runs a real{' '}
              <code className="font-mono">check_input</code> and streams its verdict. Nothing here
              is replayed from the stored report — only the payloads are. One benign payload is
              fired first to load the PII model, and is neither plotted nor counted, so the chart
              carries the rail's steady-state cost rather than process startup.
            </InfoTip>
          </div>
        }
      />
      <CardBody className="@container space-y-4">
        {probes.length === 0 ? (
          <Absence
            figure="Adversarial probes"
            why="The committed battery extract holds no input-stage payload that can be fired on its own. Nothing is invented to fill this panel."
            needed="Re-run scripts/gen-battery-probes.py after editing aegis.redteam.battery."
          />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <button
                type="button"
                onClick={firing ? stop : fire}
                className="inline-flex h-11 shrink-0 touch-manipulation items-center gap-2 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors duration-[--dur-fast] outline-none hover:bg-primary/90 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
              >
                {firing ? (
                  <>
                    <Square className="size-4" aria-hidden /> Stop the line
                  </>
                ) : (
                  <>
                    <Play className="size-4" aria-hidden /> Fire {probes.length} probes
                  </>
                )}
              </button>
              <p className="min-w-0 text-sm leading-relaxed text-muted-foreground">
                {firing ? (
                  <span className="inline-flex items-center gap-1.5">
                    <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
                    {warming
                      ? 'Warming the rail — loading the PII model, not a probe…'
                      : `Firing one at a time · ${summary.remaining} to go`}
                  </span>
                ) : (
                  `${probes.length} of ${BATTERY_PROBES.length} input-stage payloads from the shipped battery, fired one at a time.`
                )}
              </p>
            </div>

            {/* The one line a screen reader needs per arrival — the list below carries
                the detail, but re-reading twelve rows on every landing is unusable. */}
            <p className="sr-only" role="status" aria-live="polite">
              {newest === undefined
                ? 'No probe has been fired yet.'
                : `Probe ${newest.index} of ${probes.length}: ${outcomeWord(newest.outcome)}${
                    newest.totalMs === null ? '' : `, ${newest.totalMs.toFixed(1)} milliseconds`
                  }.`}
            </p>

            <div className="grid min-w-0 gap-3 @sm:grid-cols-2 @3xl:grid-cols-4">
              <FiringFigure
                label="fired"
                value={`${summary.fired}/${probes.length}`}
              />
              <FiringFigure label="blocked" value={String(summary.blocked)} />
              <FiringFigure
                label="block rate · of fired"
                value={
                  summary.blockRate === null ? '' : `${Math.round(summary.blockRate * 100)}%`
                }
                absent={summary.blockRate === null}
              />
              <FiringFigure
                label="peak rail time"
                value={summary.peakMs === null ? '' : `${summary.peakMs.toFixed(1)} ms`}
                absent={summary.peakMs === null}
              />
            </div>

            <div className="min-w-0 rounded-lg border border-border bg-surface-2/30 p-3">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <p className="eyebrow">rail time per probe · ms</p>
                <p className="text-xs text-muted-foreground">
                  {summary.peakMs === null
                    ? 'the scale appears with the first measured verdict'
                    : `top of scale ${(summary.peakMs * HEADROOM).toFixed(0)} ms`}
                </p>
              </div>
              <div className="mt-2 overflow-x-auto">
                <FiringChart summary={summary} slots={probes.length} />
              </div>
            </div>

            {summary.byRail.length > 0 ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="eyebrow">decided by</span>
                {summary.byRail.map((row) => (
                  <Badge key={row.layer} tone="neutral">
                    {row.name} · {row.count}
                  </Badge>
                ))}
                {summary.unchecked > 0 ? (
                  <Badge tone="risk">{summary.unchecked} unchecked</Badge>
                ) : null}
              </div>
            ) : null}

            {summary.points.length === 0 ? (
              <p className="rounded-lg border border-dashed border-border px-4 py-8 text-sm leading-relaxed text-muted-foreground">
                Nothing has been fired yet. Each verdict lands here as its frame arrives, newest
                first, with the rail&rsquo;s own sentence.
              </p>
            ) : (
              <ol
                aria-label="Probe verdicts, newest first"
                className="max-h-96 space-y-2 overflow-y-auto"
              >
                {[...summary.points].reverse().map((point) => (
                  <ArrivalRow
                    key={point.probe.id}
                    point={point}
                    newest={point.index === summary.points.length}
                  />
                ))}
              </ol>
            )}

            <div className="space-y-1.5 rounded-lg border border-border bg-surface-2/40 p-3.5">
              <p className="eyebrow">what these figures are not</p>
              <p className="text-xs leading-relaxed text-muted-foreground">
                The y-axis is the <strong className="font-medium text-foreground">rail&rsquo;s</strong>{' '}
                own time — <code className="font-mono">per_rail_timing_ms.total</code>, measured
                server-side around <code className="font-mono">check_input</code> — not the round
                trip. Network and queueing time are not in these figures. The rail&rsquo;s per-layer
                fields arrive as <code className="font-mono">null</code>, so there are no six
                per-rail durations to draw and none are drawn.
              </p>
              <p className="text-xs leading-relaxed text-muted-foreground">
                The demonstrator route constructs a bare <code className="font-mono">Guardrails()</code>{' '}
                — platform floor rails, no tenant policy folded in, no completer wired — so a block
                rate measured here can differ from the one the console reports on a tenant&rsquo;s
                own traffic.
              </p>
            </div>
          </>
        )}

        <Receipt
          origin="GET /v1/stream/guardrail-demo · guardrail_verdict"
          detail={
            probes.length === 0
              ? null
              : `${probes.length} of ${BATTERY_PROBES.length} · aegis.redteam.battery`
          }
        />
      </CardBody>
    </Card>
  )
}
