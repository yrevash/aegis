'use client'

import { CheckCircle2, ShieldCheck, XCircle } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { getEvalsReport,
  LIVE_EVAL_CASES,
} from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { runLiveEvals } from '@/lib/api/client'
import { errorSentence } from '@/lib/api/apiError'
import type { LiveEvalResponse } from '@/lib/api/types'
import type { EvalCaseResult, EvalMetricConfig, EvalsReportResponse } from '@/lib/api/platform'
import { cn } from '@/lib/utils'

import { MetricBullets, type BulletDatum } from './MetricBullets'

/** A minimal async-load result, mirroring the LLMOps view's fetch pattern. */
interface Loaded<T> {
  data: T | null
  loading: boolean
  error: string | null
}

/**
 * Load with an async call, re-running whenever `key` changes.
 *
 * `key` carries the bearer token, so the fetch re-fires once `AuthProvider` has
 * restored the persisted session (its effect runs *after* this one on a reload).
 * `enabled` holds the call back until then, so it never fires without a bearer
 * and 401s into a permanently-stuck state.
 */
function useLoad<T>(fn: () => Promise<T>, key: string | null, enabled: boolean): Loaded<T> {
  const [state, setState] = useState<Loaded<T>>({ data: null, loading: true, error: null })
  useEffect(() => {
    if (!enabled) return
    let alive = true
    setState((prev) => ({ ...prev, loading: true }))
    fn()
      .then((data) => {
        if (alive) setState({ data, loading: false, error: null })
      })
      .catch(() => {
        // Always resolve `loading` — a swallowed failure would spin forever.
        if (alive)
          setState({ data: null, loading: false, error: 'Could not load. Is the backend running?' })
      })
    return () => {
      alive = false
    }
    // `fn` is recreated per render; `key` + `enabled` are the real inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, enabled])
  return state
}

/**
 * Percent string for a [0,1] score (null → em dash).
 *
 * Through `Intl.NumberFormat` rather than a template, so the separator follows
 * the locale instead of being hardcoded to the one this file was written in.
 */
const PERCENT = new Intl.NumberFormat('en-US', {
  style: 'percent',
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
})

function pct(value: number | null | undefined): string {
  return value == null ? '—' : PERCENT.format(value)
}

/** Turn a raw metric id (context_precision@1) into a readable label. */
function metricLabel(name: string): string {
  const base = name
    .replace(/_/g, ' ')
    .replace(/@(\d+)/, ' @$1')
    .replace(/\baccuracy\b/, 'accuracy')
  return base.charAt(0).toUpperCase() + base.slice(1)
}

/** A short human gloss per known metric family (honest, no fabrication). */
function metricGloss(name: string): string {
  if (name.startsWith('context_precision')) return 'Is the right passage ranked at the top?'
  if (name.startsWith('context_recall')) return 'Did retrieval surface the gold passage at all?'
  if (name.startsWith('groundedness')) return 'Is the answer supported by the retrieved context?'
  if (name.startsWith('tool_selection')) return 'Did the agent pick the correct tool?'
  return 'Deterministic overlap metric — no LLM.'
}

/**
 * Whether a metric was actually computed.
 *
 * `computed` is optional on the wire, so its absence must not be read as `false`
 * — a report from a spine that does not emit the flag would then show every
 * metric as uncomputed while carrying a real reading. A reading is the evidence;
 * the flag only overrides it when it is explicitly `false`.
 */
function wasComputed(m: EvalMetricConfig): boolean {
  return m.computed !== false && m.value != null
}

/** A small counted verdict — "5 / 6 passed" — with its icon and its word. */
function Tally({
  label,
  passed,
  total,
}: {
  label: string
  passed: number
  total: number
}): ReactElement {
  const clean = total > 0 && passed === total
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-md border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow">{label}</span>
      <span className="flex items-baseline gap-2">
        <Figure size="stat">
          {passed}
          <span className="text-muted-foreground">/{total}</span>
        </Figure>
        <span
          className={cn(
            'inline-flex items-center gap-1 text-[0.7rem] font-medium',
            clean ? 'text-[color:var(--ok-ink)]' : 'text-block-ink',
          )}
        >
          {clean ? (
            <CheckCircle2 className="size-3.5" aria-hidden />
          ) : (
            <XCircle className="size-3.5" aria-hidden />
          )}
          {clean ? 'complete' : `${total - passed} short`}
        </span>
      </span>
    </div>
  )
}

/**
 * The metric × case grid, as a real matrix.
 *
 * It was a flat table with one row per *pair*, so a reader could not see that
 * the same four metrics were scored against the same seven cases — the grid the
 * gate actually computes was flattened into a list, and the one question the
 * screen exists to answer ("which case is dragging which metric down") needed a
 * pencil. Cases are rows, metrics are columns, and the case's own verdict —
 * `EvalCaseResult.passed`, which nothing on this screen read — closes each row.
 */
function CaseMatrix({
  cases,
  order,
}: {
  cases: readonly EvalCaseResult[]
  order: readonly string[]
}): ReactElement {
  // Column order follows the report's own metric order, then any metric that
  // appears only on a case (never dropped — a hidden column is a hidden score).
  const columns = useMemo(() => {
    const seen = new Set<string>()
    const cols: string[] = []
    for (const name of order) {
      if (cases.some((c) => c.metrics.some((m) => m.name === name))) {
        cols.push(name)
        seen.add(name)
      }
    }
    for (const c of cases) {
      for (const m of c.metrics) {
        if (!seen.has(m.name)) {
          cols.push(m.name)
          seen.add(m.name)
        }
      }
    }
    return cols
  }, [cases, order])

  const thresholds = useMemo(() => {
    const map = new Map<string, { threshold: number; higherIsBetter: boolean }>()
    for (const c of cases) {
      for (const m of c.metrics) {
        if (!map.has(m.name)) map.set(m.name, { threshold: m.threshold, higherIsBetter: m.higherIsBetter })
      }
    }
    return map
  }, [cases])

  return (
    <Table className="min-w-[44rem]">
      <THead>
        <TH className="pl-6">Case</TH>
        {columns.map((name) => {
          const bar = thresholds.get(name)
          return (
            <TH key={name} className="text-right whitespace-nowrap">
              <span className="inline-flex items-center gap-1.5">
                <span className="normal-case">{metricLabel(name)}</span>
                <InfoTip label={`What ${metricLabel(name)} measures`}>{metricGloss(name)}</InfoTip>
              </span>
              {bar ? (
                <span className="mt-0.5 block font-normal normal-case">
                  <Figure className="text-[0.6875rem] leading-4">
                    {bar.higherIsBetter ? '≥' : '≤'} {pct(bar.threshold)}
                  </Figure>
                </span>
              ) : null}
            </TH>
          )
        })}
        <TH className="pr-6 text-right">Case verdict</TH>
      </THead>
      <TBody>
        {cases.map((c) => {
          const byName = new Map(c.metrics.map((m) => [m.name, m]))
          return (
            <TR key={c.name}>
              <TD className="max-w-[22rem] pl-6">
                <span className="line-clamp-2 text-sm text-foreground" title={c.name}>
                  {c.name}
                </span>
              </TD>
              {columns.map((name) => {
                const m = byName.get(name)
                if (m == null) {
                  return (
                    <TD key={name} className="text-right text-muted-foreground/50">
                      <span aria-label="not scored on this case">—</span>
                    </TD>
                  )
                }
                return (
                  <TD key={name} className="text-right whitespace-nowrap">
                    <span className="inline-flex items-center justify-end gap-1.5">
                      {m.passed ? (
                        <CheckCircle2 className="size-3.5 text-[color:var(--ok-ink)]" aria-hidden />
                      ) : (
                        <XCircle className="size-3.5 text-block-ink" aria-hidden />
                      )}
                      <Figure className={m.passed ? undefined : 'text-danger'}>{pct(m.value)}</Figure>
                      <span className="sr-only">{m.passed ? 'pass' : 'fail'}</span>
                    </span>
                  </TD>
                )
              })}
              <TD className="pr-6 text-right">
                <Badge tone={c.passed ? 'ok' : 'block'}>
                  {c.passed ? (
                    <CheckCircle2 className="size-3.5" aria-hidden />
                  ) : (
                    <XCircle className="size-3.5" aria-hidden />
                  )}
                  {c.passed ? 'Pass' : 'Fail'}
                </Badge>
              </TD>
            </TR>
          )
        })}
      </TBody>
    </Table>
  )
}

/**
 * Evals — the offline regression gate. Reads `getEvalsReport()` (→ `/evals/report`),
 * the deterministic RAGAS/DeepEval-pattern gate scored with **no LLM**.
 *
 * The screen leads with the gate verdict and its three tallies, then draws the
 * only chart the payload honestly supports: every metric's reading against its
 * own threshold, as bullet bars on one shared axis. **There is no time-series
 * here** — the report carries no timestamp on any metric or case, so a trend
 * line would be invented rather than measured, and this product does not do
 * that. The metric × case matrix underneath is the grid the gate computes.
 */
function EvalsView(): ReactElement {

  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  /*
   * The live run is a button, never an effect. It costs model calls, and a page that
   * spends money on mount is a page somebody turns off.
   */
  const [live, setLive] = useState<LiveEvalResponse | null>(null)
  const [liveError, setLiveError] = useState<string | null>(null)
  const [scoring, setScoring] = useState(false)

  /**
   * Elapsed seconds while a judged run is in flight.
   *
   * The wait is 14–134 seconds of real model calls and the only feedback used to be a
   * disabled button. On a screen being demonstrated to a room, a control that goes quiet
   * for two minutes reads as broken long before it reads as working.
   */
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!scoring) return
    setElapsed(0)
    const started = performance.now()
    const id = window.setInterval(() => {
      setElapsed(Math.floor((performance.now() - started) / 1000))
    }, 1000)
    return () => window.clearInterval(id)
  }, [scoring])

  const runLive = async (): Promise<void> => {
    setScoring(true)
    setLiveError(null)
    try {
      setLive(await runLiveEvals(token))
    } catch (error) {
      // The previous result is KEPT. Clearing it sent the card back to "One cell left
      // empty" — copy that reads as a deliberate policy ("the platform refuses to fake
      // it") rather than as the failure it actually is. A failed re-score must not
      // rewrite the history of a successful one.
      setLiveError(errorSentence(error, 'The live evaluation could not run.'))
    } finally {
      setScoring(false)
    }
  }
  const report = useLoad<EvalsReportResponse>(() => getEvalsReport(token), token, hydrated)
  const data = report.data

  const metrics = useMemo(() => data?.metrics ?? [], [data])
  const cases = useMemo(() => data?.cases ?? [], [data])
  const overallPass = data?.passed ?? false

  const computed = useMemo(() => metrics.filter(wasComputed), [metrics])
  const uncomputed = useMemo(() => metrics.filter((m) => !wasComputed(m)), [metrics])
  const metricsPassed = metrics.filter((m) => m.passed).length
  const casesPassed = cases.filter((c) => c.passed).length

  // Only metrics that were actually computed become bars. A metric with no
  // reading would otherwise draw at zero, which reads as a measured zero.
  const bullets: BulletDatum[] = useMemo(
    () =>
      computed.map((m) => ({
        name: metricLabel(m.name),
        gloss: metricGloss(m.name),
        value: m.value ?? 0,
        threshold: m.threshold,
        higherIsBetter: m.higherIsBetter,
        passed: m.passed,
        cases: m.cases,
      })),
    [computed],
  )

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="retrieval quality · offline regression gate" title="Evals" />

      {report.error ? <ErrorState error={report.error} /> : null}

      {/* Row 1 — the gate verdict and its three tallies. */}
      <Card className={cn(overallPass ? 'ring-1 ring-ok/40' : 'ring-1 ring-block/50')}>
        <CardHeader
          eyebrow="release verdict"
          title="Regression gate"
          actions={
            <span
              className={cn(
                'grid size-8 place-items-center rounded-lg',
                overallPass ? 'bg-ok/15' : 'bg-block/20',
              )}
            >
              <ShieldCheck
                aria-hidden
                className={cn('size-4', overallPass ? 'text-ok-ink' : 'text-block-ink')}
              />
            </span>
          }
        />
        <CardBody className="space-y-5">
          {report.loading ? (
            <LoadingState rows={2} label="Loading the gate verdict…" />
          ) : (
            <>
              <div className="flex flex-wrap items-end justify-between gap-4">
                <div className="flex min-w-0 items-center gap-4">
                  <span
                    className={cn(
                      'grid size-14 shrink-0 place-items-center rounded-lg',
                      overallPass ? 'bg-ok/15 text-ok-ink' : 'bg-block/20 text-block-ink',
                    )}
                  >
                    {overallPass ? (
                      <CheckCircle2 className="size-7" aria-hidden />
                    ) : (
                      <XCircle className="size-7" aria-hidden />
                    )}
                  </span>
                  <div>
                    {/* The one `display` figure on this screen (DESIGN.md §3). */}
                    <Figure size="display" className={overallPass ? 'text-foreground' : 'text-danger'}>
                      {pct(data?.overall)}
                    </Figure>
                    <p className="mt-1 text-sm text-muted-foreground">
                      mean score across {metrics.length} gated metric
                      {metrics.length === 1 ? '' : 's'}
                    </p>
                  </div>
                </div>
                <Badge tone={overallPass ? 'ok' : 'block'} className="px-2.5 py-1">
                  {overallPass ? (
                    <CheckCircle2 className="size-3.5" aria-hidden />
                  ) : (
                    <XCircle className="size-3.5" aria-hidden />
                  )}
                  {overallPass ? 'Gate passed' : 'Gate failed'}
                </Badge>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <Tally label="metrics over their bar" passed={metricsPassed} total={metrics.length} />
                <Tally label="cases clean" passed={casesPassed} total={cases.length} />
                <Tally label="metrics computed" passed={computed.length} total={metrics.length} />
              </div>
            </>
          )}
          {report.loading ? null : (
            <Receipt origin={data?.source ?? 'not reported'} detail="deterministic · no LLM judge" />
          )}
        </CardBody>
      </Card>

      {/* Row 2 — the chart, and beside it the metric the platform refuses to fake. */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card className="min-w-0 xl:col-span-2">
          <CardHeader
            eyebrow="value against threshold"
            title="Every metric against its own bar"
            actions={
              <InfoTip label="Why there is no trend line">
                The report carries no timestamps, so a curve would be invented rather than measured.
              </InfoTip>
            }
          />
          <CardBody className="space-y-4">
            {report.loading ? (
              <LoadingState rows={4} label="Loading metric readings…" />
            ) : bullets.length ? (
              <>
                <MetricBullets data={bullets} label="Each metric's reading against its threshold" />
                {uncomputed.length ? (
                  <EmptyState
                    title={`${uncomputed.length} configured, not computed`}
                    body={`${uncomputed.map((m) => metricLabel(m.name)).join(', ')} — no reading to plot.`}
                  />
                ) : null}
                <Receipt
                  origin={data?.source ?? 'not reported'}
                  detail={`${bullets.length}/${metrics.length} metrics carry a reading`}
                />
              </>
            ) : (
              <Absence
                figure="Every bar on this chart"
                why="No metric in this report carries a reading."
                needed="A completed run of the offline gate."
              />
            )}
          </CardBody>
        </Card>

        {/* This card used to be a dashed box holding one badge — the weakest thing
            on the screen, and it was carrying the most interesting claim: that the
            platform leaves a cell of the score matrix empty rather than fill it
            with a number it cannot defend. */}
        <Card className="min-w-0">
          <CardHeader
            eyebrow="ragas · answer relevancy"
            title={live === null ? 'One cell left empty' : 'Scored by ragas'}
          />
          <CardBody className="space-y-3">
            {live === null ? (
              <Absence
                figure="Answer relevancy"
                why="Scoring it needs a model to judge a model, and every figure on this page is deterministic. The number is not withheld — it is not computed until somebody asks, because asking costs model calls."
                needed="Press the button; the run is metered like any other call."
                className="text-left"
              />
            ) : (
              <>
                {/* The caveat sits ABOVE the numbers, not below them, because the number
                    is what gets read and quoted. Faithfulness here is scored with the
                    retrieved context standing in as the answer, which makes it 1.000 by
                    construction — and a card whose own copy argues against filling a cell
                    with an undefendable figure must not then present that 1.000 as
                    "this platform's answers are perfectly grounded". Stating the setup is
                    what keeps the figure a measurement of the metrics rather than a claim
                    about the product. */}
                <p className="text-xs leading-relaxed text-muted-foreground">
                  Scored with the retrieved context standing in as the answer, so this
                  measures that the ragas metrics run end-to-end against real content —
                  not that a generated answer is good.{' '}
                  <span className="text-foreground">
                    Faithfulness is therefore 1.000 by construction.
                  </span>{' '}
                  Scoring a generated answer costs one generation call per case and is the
                  next increment.
                </p>
                <dl className="space-y-2">
                {live.metrics.map((m) => (
                  <div key={m.name} className="flex items-baseline justify-between gap-3">
                    <dt className="min-w-0 truncate font-mono text-xs text-muted-foreground">
                      {m.name}
                    </dt>
                    <dd className="shrink-0">
                      {m.value === null ? (
                        <span className="text-xs text-muted-foreground">{m.note}</span>
                      ) : (
                        <Figure className="tabular text-lg font-semibold text-foreground">
                          {m.value.toFixed(3)}
                        </Figure>
                      )}
                    </dd>
                  </div>
                ))}
                </dl>
              </>
            )}
            <button
              type="button"
              onClick={() => void runLive()}
              disabled={scoring}
              className="inline-flex h-11 w-full touch-manipulation items-center justify-center gap-2 rounded-lg border border-border bg-card px-4 text-sm font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 disabled:opacity-60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              {scoring
                ? `Judging… ${elapsed}s`
                : live === null
                  ? `Score ${LIVE_EVAL_CASES} cases with ragas`
                  : 'Score again'}
            </button>
            {/* Said before it is pressed, not after. The card's copy admitted "asking
                costs model calls" without ever saying how many, which is the same
                omission it criticises elsewhere on this page. */}
            <p className="text-center text-[0.6875rem] text-muted-foreground">
              {scoring
                ? 'Judged calls are in flight; this takes 15–120 seconds.'
                : `${LIVE_EVAL_CASES} cases · ~${LIVE_EVAL_CASES * 9} gateway calls · metered to your tenant`}
            </p>
            {liveError !== null && (
              <p className="text-xs text-block-ink">{liveError}</p>
            )}
            {live !== null && <Receipt origin={live.source} />}
          </CardBody>
        </Card>
      </div>

      {/* Row 3 — the grid the gate actually computes. */}
      <DataPanel
        eyebrow="seed corpus"
        title="Metric × case matrix"
        collapsible
        actions={
          cases.length ? (
            <Badge tone="neutral">
              <Figure className="text-xs leading-4">{cases.length}</Figure>{' '}
              {cases.length === 1 ? 'case' : 'cases'}
            </Badge>
          ) : null
        }
        maxHeight={520}
        footer={
          cases.length ? (
            <Receipt
              origin={data?.source ?? 'not reported'}
              detail={`${casesPassed}/${cases.length} cases clean · ✓ over the bar, ✗ under`}
            />
          ) : null
        }
      >
        {report.loading ? (
          <LoadingState rows={5} label="Loading cases…" />
        ) : cases.length ? (
          <CaseMatrix cases={cases} order={metrics.map((m) => m.name)} />
        ) : (
          <SceneState name="grading" size="md">
            <EmptyState
              title="No case has been graded yet"
              body="Each seed case becomes a row, one column per gated metric."
            />
          </SceneState>
        )}
      </DataPanel>
    </div>
  )
}

/** Client entry for the Evals section — gated on a reachable backend. */
export function EvalsMount(): ReactElement {
  return (
    <BackendGate>
      <EvalsView />
    </BackendGate>
  )
}
