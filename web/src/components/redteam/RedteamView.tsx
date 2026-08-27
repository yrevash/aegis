'use client'

import {
  AlertTriangle,
  CircleCheck,
  CircleX,
  EyeOff,
  History,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
  Swords,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { RankedBars } from '@/components/charts/RankedBars'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { SceneState } from '@/components/illustration/Scene'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import {
  getRedteamHistory,
  getRedteamRun,
  getRedteamSuites,
  redteamMessage,
  startRedteamRun,
  type RedteamCategoryRollup,
  type RedteamMode,
  type RedteamProbe,
  type RedteamRun,
  type RedteamRunDetail,
  type RedteamSuite,
} from '@/lib/api/redteam'
import { useAuth } from '@/lib/auth/AuthContext'

import { BlockRateTrend } from './BlockRateTrend'
import {
  compareRuns,
  headline,
  label,
  pct,
  points,
  splitLeaks,
  usd,
  verdictNote,
} from './redteamReport'

/**
 * Timestamps on this screen, in the reader's own locale — **one** formatter, not
 * one `toLocaleString` per history row.
 */
const STAMP = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

/** A run's start time, or the words for its absence. Never a fabricated stamp. */
function stamp(iso: string | null): string {
  if (iso == null) return 'no start time recorded'
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? 'no start time recorded' : STAMP.format(at)
}

/** The one focus treatment on this screen: the ring token, at 2px, always visible. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/** A select on the run panel. Sized for a thumb, cornered on the radius token. */
const SELECT = `h-11 w-full touch-manipulation rounded-lg border border-border bg-card px-3 text-sm text-foreground transition-colors duration-[--dur-fast] ${FOCUS}`

/** One probe row — the attack, the rail that judged it, and that rail's own words. */
function ProbeRow({ probe }: { probe: RedteamProbe }): ReactElement {
  return (
    <TR className="align-top">
      <TD>
        <Figure className="text-muted-foreground">{probe.id}</Figure>
      </TD>
      <TD>
        <p className="text-sm leading-relaxed text-foreground">{probe.prompt}</p>
        <p className="mt-1 text-xs text-muted-foreground capitalize">
          {label(probe.category)} · {probe.owasp} · {label(probe.stage)} rail
        </p>
      </TD>
      <TD className="whitespace-nowrap">
        {probe.layer ? (
          <Badge tone={probe.neutralized ? 'ok' : 'neutral'} className="gap-1.5 font-mono">
            {probe.neutralized ? <CircleCheck className="size-3 shrink-0" aria-hidden /> : null}
            {probe.layer}
          </Badge>
        ) : (
          <span className="text-xs text-muted-foreground italic">no rail fired</span>
        )}
      </TD>
      <TD>
        <Figure className="uppercase text-foreground">{probe.verdict}</Figure>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{probe.reason}</p>
      </TD>
    </TR>
  )
}

/** The run controls: pick a battery, pick a mode, see the price, press it. */
function RunPanel({
  suites,
  selected,
  onSelect,
  mode,
  onMode,
  mayRun,
  refusal,
  running,
  onRun,
}: {
  suites: RedteamSuite[]
  selected: string
  onSelect: (id: string) => void
  mode: RedteamMode
  onMode: (mode: RedteamMode) => void
  mayRun: boolean
  refusal: string | null
  running: boolean
  onRun: () => void
}): ReactElement {
  const suite = suites.find((s) => s.id === selected) ?? suites[0]
  const estimate = suite ? (mode === 'live' ? suite.live : suite.offline) : null

  return (
    <Card>
      <CardHeader eyebrow="aegis · redteam" title="Run a battery" />
      <CardBody className="space-y-5">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-[0.8125rem] font-medium text-foreground">Battery</span>
            <select value={selected} onChange={(e) => onSelect(e.target.value)} className={SELECT}>
              {suites.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.title}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-[0.8125rem] font-medium text-foreground">Mode</span>
            <select
              value={mode}
              onChange={(e) => onMode(e.target.value as RedteamMode)}
              className={SELECT}
            >
              <option value="offline">Offline — deterministic signatures only</option>
              <option value="live">Live — drives the model layers, spends budget</option>
            </select>
          </label>
        </div>

        {suite ? (
          <div className="rounded-lg border border-border bg-surface-2 px-4 py-3">
            <p className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {suite.owasp.map((id) => (
                <Badge key={id} tone="neutral" className="font-mono">
                  {id}
                </Badge>
              ))}
              <span>
                {suite.attacks} attacks · {suite.controls} benign controls ·{' '}
                {suite.semanticOnly} with no deterministic signature
              </span>
              <InfoTip label={`What the ${suite.title} battery is`}>{suite.summary}</InfoTip>
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              {Object.entries(suite.stages)
                .map(([stage, count]) => `${count} at the ${label(stage)} rail`)
                .join(' · ')}
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          {/* The estimate was a paragraph restating three figures it already
              printed. It is the three figures (DESIGN.md §9). */}
          <span className="flex min-w-0 flex-wrap items-center gap-2">
            {estimate && estimate.modelCalls > 0 ? (
              <>
                <Badge tone="risk" className="gap-1.5 font-mono">
                  <AlertTriangle className="size-3 shrink-0" aria-hidden />
                  {usd(estimate.costUsd)} est.
                </Badge>
                <Badge tone="neutral" className="min-w-0 font-mono">
                  <span className="truncate">
                    ≤{estimate.modelCalls} calls · {estimate.model}
                  </span>
                </Badge>
                <InfoTip label="Who pays for a live run">
                  Charged to this tenant&rsquo;s budget at the usage ledger&rsquo;s own unit cost.
                </InfoTip>
              </>
            ) : (
              <Badge tone="ok" className="gap-1.5">
                <CircleCheck className="size-3 shrink-0" aria-hidden />
                no model called · ledger untouched
              </Badge>
            )}
          </span>
          <button
            type="button"
            onClick={onRun}
            disabled={running || !mayRun}
            title={mayRun ? undefined : (refusal ?? undefined)}
            className={`inline-flex h-11 shrink-0 touch-manipulation items-center gap-2 rounded-lg bg-primary px-5 text-sm font-medium text-primary-foreground transition-colors duration-[--dur-fast] hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-60 ${FOCUS}`}
          >
            {running ? (
              <>
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />{' '}
                Running…
              </>
            ) : (
              <>
                <Play className="size-4" aria-hidden /> Run battery
              </>
            )}
          </button>
        </div>

        {!mayRun && refusal ? (
          <p
            role="status"
            className="rounded-lg border border-border bg-surface-2 px-4 py-3 text-sm leading-relaxed text-muted-foreground"
          >
            {refusal}
          </p>
        ) : null}
      </CardBody>
    </Card>
  )
}

/**
 * Block rate per attack category, ranked against the floor.
 *
 * This was `CategoryBar` — one hand-rolled progress track per category, which is
 * the progress-bar-as-chart the owner has rejected twice: no axis, no ordering,
 * and the tint carrying the verdict. `RankedBars` sorts by magnitude and prints
 * every value beside its own bar, so the answer to "which category is weakest"
 * is the bottom row rather than a colour comparison.
 *
 * The floor does not disappear with the tracks. It is stated once in the header
 * and the categories that fall under it are **named** in one line, with an icon
 * and a word — never hue alone (DESIGN.md §2).
 */
function CategoryBlockRates({
  categories,
  floor,
}: {
  categories: RedteamCategoryRollup[]
  floor: number
}): ReactElement {
  const attacks = categories.filter((row) => row.category !== 'benign_control')
  const below = attacks.filter((row) => row.blockRate < floor)
  const blockedTotal = attacks.reduce((sum, row) => sum + row.blocked, 0)
  const probeTotal = attacks.reduce((sum, row) => sum + row.total, 0)

  return (
    <Card className="flex min-w-0 flex-col">
      <CardHeader
        eyebrow="by category"
        title="Block rate per attack category"
        actions={
          <Badge tone="neutral" className="font-mono">
            floor {pct(floor)}
          </Badge>
        }
      />
      <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
        {attacks.length === 0 ? (
          <Absence
            className="text-left"
            figure="Block rate per category"
            why="This battery ran no attack probes, only benign controls."
          />
        ) : (
          <>
            <RankedBars
              data={attacks.map((row) => ({ name: label(row.category), value: row.blockRate }))}
              valueFormatter={pct}
              maxRows={attacks.length}
              tail="omit"
              label="Block rate by attack category, highest first"
            />
            <p className="flex flex-wrap items-center gap-2">
              <Badge tone={below.length === 0 ? 'ok' : 'block'} className="gap-1.5">
                {below.length === 0 ? (
                  <CircleCheck className="size-3 shrink-0" aria-hidden />
                ) : (
                  <CircleX className="size-3 shrink-0" aria-hidden />
                )}
                {below.length === 0
                  ? `all ${attacks.length} clear the floor`
                  : `${below.length} below the floor`}
              </Badge>
              {below.length === 0 ? null : (
                <span className="min-w-0 text-xs break-words text-muted-foreground capitalize">
                  {below.map((row) => label(row.category)).join(' · ')}
                </span>
              )}
            </p>
          </>
        )}
        <Receipt
          className="mt-auto"
          origin="report.categories · blocked ÷ total per category"
          detail={`${blockedTotal} of ${probeTotal} attack probes blocked`}
        />
      </CardBody>
    </Card>
  )
}

/**
 * **Which rail actually caught how much** — `report.rails`, drawn.
 *
 * It was `rails.map(r => `${r.layer} ${r.blocks}`).join(' · ')`: a real
 * distribution flattened into a run-on string in a card's action slot, where the
 * one question it answers — is the whole defence resting on a single rail? — is
 * the hardest thing to read off it.
 */
function RailBlocks({ rails }: { rails: { layer: string; blocks: number }[] }): ReactElement {
  const firing = rails.filter((rail) => rail.blocks > 0)
  const total = rails.reduce((sum, rail) => sum + rail.blocks, 0)

  return (
    <Card className="flex min-w-0 flex-col">
      <CardHeader
        eyebrow="by rail"
        title="Which rail stopped it"
        actions={
          <Badge tone="neutral" className="font-mono">
            {firing.length}/{rails.length} fired
          </Badge>
        }
      />
      <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
        {total === 0 ? (
          <Absence
            className="text-left"
            figure="Blocks per rail"
            why="No rail returned a block in this run, so there is no distribution to draw."
            needed="a run in which at least one rail fires"
          />
        ) : (
          <RankedBars
            data={rails.map((rail) => ({ name: label(rail.layer), value: rail.blocks }))}
            valueFormatter={(v) => String(v)}
            maxRows={rails.length}
            tail="omit"
            label="Attacks blocked per rail, most active first"
            color="agent"
          />
        )}
        <Receipt
          className="mt-auto"
          origin="report.rails · the rail that returned each block"
          detail={`${total} blocks across ${rails.length} rails`}
        />
      </CardBody>
    </Card>
  )
}

/** A probe table with the shared four columns — blocked, unchecked and leaked share it. */
function ProbeTable({ probes }: { probes: RedteamProbe[] }): ReactElement {
  return (
    <Table className="min-w-[720px]">
      <THead>
        <TH className="w-24">Probe</TH>
        <TH>Attack</TH>
        <TH className="w-36">Rail</TH>
        <TH className="w-[38%]">Verdict</TH>
      </THead>
      <TBody>
        {probes.map((probe) => (
          <ProbeRow key={probe.id} probe={probe} />
        ))}
      </TBody>
    </Table>
  )
}

/**
 * Red-team — pick a battery, run it, and read what each rail actually did.
 *
 * Every number is the rail's own verdict on a real probe: no figure here is composed
 * in the browser. What was **blocked** is given the same room as what got through,
 * because a red team that finds nothing is a result and needs to be readable as one.
 *
 * **The unchecked count is always on screen**, including when it is zero. It used to
 * render only when it was above zero, which is precisely backwards: a run reporting
 * 100% blocked is either lying or testing nothing, and "0 refused without being
 * examined" is the fact that tells those two apart. Hiding it left the reassuring
 * reading unopposed.
 *
 * **What is drawn.** {@link BlockRateTrend} is the one genuine time-series in these
 * three portals — `history[].startedAt × blockRate`, against the stored floor — and
 * the page leads with it, because 82% blocked means nothing without last time.
 * {@link CategoryBlockRates} and {@link RailBlocks} are the two distributions the
 * report already carried and never drew: block rate by attack category against the
 * floor, and blocks per rail, which is the only mark that answers whether the whole
 * defence is resting on one screen. `report.unchecked` and
 * `report.falsePositiveDetail` are typed, were rendered nowhere, and now have their
 * own panels — shown only when they have rows, because their zero case is already a
 * tile above.
 */
function RedteamView(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [suites, setSuites] = useState<RedteamSuite[]>([])
  const [selected, setSelected] = useState<string>('')
  const [mode, setMode] = useState<RedteamMode>('offline')
  const [mayRun, setMayRun] = useState(false)
  const [refusal, setRefusal] = useState<string | null>(null)

  const [detail, setDetail] = useState<RedteamRunDetail | null>(null)
  const [history, setHistory] = useState<RedteamRun[]>([])
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refreshHistory = useCallback(
    async (suite: string) => {
      const rows = await getRedteamHistory(token, suite)
      setHistory(rows.rows)
      return rows.rows
    },
    [token],
  )

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    setLoading(true)
    getRedteamSuites(token)
      .then(async (catalogue) => {
        if (!alive) return
        setSuites(catalogue.suites)
        setMayRun(catalogue.mayRun)
        setRefusal(catalogue.refusal)
        const suite = catalogue.defaultSuite
        setSelected(suite)
        const rows = await refreshHistory(suite)
        // Show the most recent stored run rather than an empty screen — and rather
        // than firing a battery nobody asked for.
        if (alive && rows.length > 0) {
          const latest = await getRedteamRun(token, rows[0].runId)
          if (alive) setDetail(latest)
        }
      })
      .catch((err: unknown) => {
        if (alive) setError(redteamMessage(err, 'Could not read the red-team catalogue.'))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated, refreshHistory])

  function run(): void {
    setRunning(true)
    setError(null)
    startRedteamRun(token, { suite: selected, mode })
      .then(async (result) => {
        setDetail(result)
        await refreshHistory(result.run.suite)
      })
      .catch((err: unknown) => setError(redteamMessage(err, 'The battery could not be run.')))
      .finally(() => setRunning(false))
  }

  function open(runId: string): void {
    setError(null)
    getRedteamRun(token, runId)
      .then((result) => setDetail(result))
      .catch((err: unknown) => setError(redteamMessage(err, 'That run could not be read.')))
  }

  const run_ = detail?.run
  const report = detail?.report
  const comparison = run_ ? compareRuns(run_, detail?.previous ?? null) : null
  const leaks = report ? splitLeaks(report.leaked) : null
  const gotThrough = run_ ? run_.attacksTotal - run_.attacksBlocked - run_.attacksUnchecked : 0

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="owasp llm top 10 · real verdicts"
        title="Red-team"
        actions={
          <InfoTip label="Where these verdicts come from">
            Every verdict is what the rail returned for that exact string; nothing here is
            composed in the browser.
          </InfoTip>
        }
      />

      {error ? <ErrorState error={error} /> : null}

      {loading ? (
        <Card>
          <CardBody>
            <LoadingState rows={3} label="Reading the battery catalogue…" />
          </CardBody>
        </Card>
      ) : (
        <RunPanel
          suites={suites}
          selected={selected}
          onSelect={setSelected}
          mode={mode}
          onMode={setMode}
          mayRun={mayRun}
          refusal={refusal}
          running={running}
          onRun={run}
        />
      )}

      {/*
        ── Block rate over runs ──────────────────────────────────────────────
        The one real time-series in these three portals, and the page leads with
        it: a block rate read against last time is the only way 82% means
        anything. The stored runs are the source; the table at the foot is the
        same rows, itemised.
      */}
      {history.length === 0 ? null : <BlockRateTrend history={history} />}

      {run_ && report ? (
        <>
          {/* ── What happened to the attacks ─────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 [&>*]:min-w-0">
            <StatCard
              label="Attacks blocked"
              value={`${run_.attacksBlocked}/${run_.attacksTotal}`}
              icon={ShieldCheck}
              tone="ok"
              source="a rail returned a block for this probe"
              delta={
                comparison && !comparison.blockRate.unchanged
                  ? {
                      value: points(comparison.blockRate.change),
                      direction: comparison.blockRate.improved ? 'up' : 'down',
                    }
                  : undefined
              }
            />
            <StatCard
              label="Attacks that got through"
              value={String(gotThrough)}
              icon={ShieldAlert}
              tone={gotThrough === 0 ? 'ok' : 'block'}
              source="examined by every rail, and stopped by none"
            />
            {/* Always rendered, zero included: this is the figure that decides
                whether a perfect block rate is coverage or an untested battery. */}
            <StatCard
              label="Refused without being examined"
              value={String(run_.attacksUnchecked)}
              icon={AlertTriangle}
              tone={run_.attacksUnchecked > 0 ? 'block' : 'neutral'}
              source={
                run_.attacksUnchecked > 0
                  ? 'these never reached a rail, so they count as neither blocked nor leaked'
                  : 'every attack in this battery reached a rail'
              }
            />
          </div>

          {/* ── How that scores ──────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 [&>*]:min-w-0">
            <StatCard
              label="Block rate"
              value={pct(run_.blockRate)}
              icon={Swords}
              source={`judged against a ${pct(run_.minBlockRate)} floor`}
            />
            <StatCard
              label="Benign controls wrongly blocked"
              value={`${run_.falsePositives}/${run_.controlsTotal}`}
              icon={AlertTriangle}
              tone={run_.falsePositives === 0 ? 'ok' : 'block'}
              source={`judged against a ${pct(run_.maxFalsePositiveRate)} ceiling`}
            />
          </div>

          {/*
            The verdict, as one sentence and a row of measured chips.
            It was four stacked paragraphs — the headline, what the mode means, the
            comparison with the previous run, and the receipt — three of which were
            explaining rather than reporting. The explanation is now an InfoTip on the
            mode badge and the comparison is three deltas you can read at a glance
            (DESIGN.md §9: never a paragraph where a badge would do).
          */}
          <Card>
            <CardBody className="space-y-3">
              {/* The one region a run changes asynchronously, so it is the one that
                  announces. */}
              <p aria-live="polite" className="text-pretty text-sm leading-relaxed text-foreground">
                {headline(run_)}. <span className="text-muted-foreground">{verdictNote(run_)}</span>
              </p>
              <div className="flex flex-wrap items-center gap-2">
                <Badge tone={run_.mode === 'live' ? 'risk' : 'neutral'}>{run_.mode} run</Badge>
                <InfoTip label={`What a ${run_.mode} run measures`}>
                  {run_.mode === 'live'
                    ? run_.tenantId == null
                      ? 'The model-backed layers ran against the platform’s own rails, and with no tenant to bill the cost stays an estimate.'
                      : 'The model-backed layers ran, and the calls are in this tenant’s usage ledger.'
                    : 'No model was called, so this measures the deterministic signatures alone — not the whole stack.'}
                </InfoTip>
                {comparison ? (
                  <>
                    <span className="eyebrow">vs {comparison.previousRunId}</span>
                    <Badge tone={comparison.blockRate.improved ? 'ok' : 'block'}>
                      block rate {points(comparison.blockRate.change)}
                    </Badge>
                    <Badge tone="neutral">
                      false positives {points(comparison.falsePositiveRate.change)}
                    </Badge>
                    <Badge tone={comparison.attacksLeakedDelta > 0 ? 'block' : 'ok'}>
                      {comparison.attacksLeakedDelta === 0
                        ? 'same attacks got through'
                        : `${Math.abs(comparison.attacksLeakedDelta)} ${
                            comparison.attacksLeakedDelta > 0 ? 'more' : 'fewer'
                          } got through`}
                    </Badge>
                  </>
                ) : (
                  <span className="text-xs text-muted-foreground">
                    first run in this mode — nothing to compare to
                  </span>
                )}
              </div>
              <Receipt
                label="Run"
                origin={`${run_.suite} · ${run_.runId}`}
                detail={`started by ${run_.initiatedBy} · ${stamp(run_.startedAt)}`}
              />
            </CardBody>
          </Card>

          {/* ── The two distributions this report actually carries ───────────── */}
          <div className="grid min-w-0 grid-cols-1 items-start gap-4 lg:grid-cols-2 [&>*]:min-w-0">
            <CategoryBlockRates categories={report.categories} floor={run_.minBlockRate} />
            <RailBlocks rails={report.rails} />
          </div>

          {/* ── What the rails stopped ───────────────────────────────────────── */}
          <DataPanel
            eyebrow="blocked"
            title="Attacks the rails stopped"
            collapsible
            summary={`${report.blocked.length} attack${report.blocked.length === 1 ? "" : "s"} stopped`}
            maxHeight={520}
            actions={
              <Badge tone="ok" className="gap-1.5">
                <ShieldCheck className="size-3 shrink-0" aria-hidden />
                {report.blocked.length} of {run_.attacksTotal}
              </Badge>
            }
          >
            {report.blocked.length === 0 ? (
              <SceneState name="redteam" size="md">
                <Absence
                  className="text-left"
                  figure="Attacks the rails stopped"
                  why="Every probe reached the model — expected offline for probes with no deterministic signature, a finding on a live run."
                />
              </SceneState>
            ) : (
              <ProbeTable probes={report.blocked} />
            )}
          </DataPanel>

          {/* ── What got through ─────────────────────────────────────────────── */}
          <DataPanel
            eyebrow="got through"
            title="Attacks nothing stopped"
            collapsible
            summary={`${report.leaked.length} got through`}
            maxHeight={520}
            actions={
              <Badge tone={report.leaked.length === 0 ? 'ok' : 'block'} className="gap-1.5">
                {report.leaked.length === 0 ? (
                  <CircleCheck className="size-3 shrink-0" aria-hidden />
                ) : (
                  <CircleX className="size-3 shrink-0" aria-hidden />
                )}
                {report.leaked.length} of {run_.attacksTotal}
              </Badge>
            }
          >
            {report.leaked.length === 0 ? (
                /* The one place on this screen where the picture is the point: a
                   battery that got through nothing. `sealed` is data held behind a
                   lock, which is what this state is, and the sentence beside it is
                   still what a screen reader gets. */
                <SceneState name="sealed" size="md">
                  <EmptyState
                    className="items-center border-none text-center"
                    title="Every attack in this battery was stopped"
                    body="Coverage only if the two figures above hold: nothing refused unexamined, no benign control blocked."
                  />
                </SceneState>
              ) : (
                <>
                  {leaks && leaks.unexpected.length > 0 ? (
                    <p className="mb-4 rounded-lg border border-block bg-block/10 px-4 py-3 text-sm leading-relaxed text-foreground">
                      <strong className="font-semibold">{leaks.unexpected.length}</strong> of these
                      has a deterministic signature and should have been caught. That is a gap in
                      the rails, not in the configuration.
                    </p>
                  ) : null}
                  <Table className="min-w-[720px]">
                    <THead>
                      <TH className="w-24">Probe</TH>
                      <TH>Attack</TH>
                      <TH className="w-36">Why</TH>
                      <TH className="w-[38%]">Verdict</TH>
                    </THead>
                    <TBody>
                      {report.leaked.map((probe) => (
                        <TR key={probe.id} className="align-top">
                          <TD>
                            <Figure className="text-muted-foreground">{probe.id}</Figure>
                          </TD>
                          <TD>
                            <p className="text-sm leading-relaxed text-foreground">{probe.prompt}</p>
                            <p className="mt-1 text-xs text-muted-foreground capitalize">
                              {label(probe.category)} · {probe.owasp} · {label(probe.stage)} rail
                            </p>
                          </TD>
                          <TD>
                            <Badge tone={probe.needsLlm ? 'risk' : 'block'} className="gap-1.5">
                              <AlertTriangle className="size-3 shrink-0" aria-hidden />
                              {probe.needsLlm ? 'needs the model layer' : 'signature missed it'}
                            </Badge>
                          </TD>
                          <TD>
                            <Figure className="uppercase text-foreground">{probe.verdict}</Figure>
                            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                              {probe.reason}
                            </p>
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </>
              )}
          </DataPanel>

          {/*
            `report.unchecked` and `report.falsePositiveDetail` are in the type and
            were rendered nowhere: the counts were on the tiles above with no way to
            reach the probes behind them. Each panel appears only when it has rows —
            the zero case is already stated, on a tile, in words.
          */}
          {report.unchecked.length === 0 ? null : (
            <DataPanel
              eyebrow="unchecked"
              title="Refused without being examined"
              collapsible
              summary={`${report.unchecked.length} unchecked`}
              maxHeight={420}
              actions={
                <Badge tone="block" className="gap-1.5">
                  <EyeOff className="size-3 shrink-0" aria-hidden />
                  {report.unchecked.length} of {run_.attacksTotal}
                </Badge>
              }
            >
              <ProbeTable probes={report.unchecked} />
            </DataPanel>
          )}

          {report.falsePositiveDetail.length === 0 ? null : (
            <DataPanel
              eyebrow="false positives"
              title="Benign controls the rails blocked"
              collapsible
              summary={`${report.falsePositiveDetail.length} false positive${report.falsePositiveDetail.length === 1 ? "" : "s"}`}
              maxHeight={420}
              actions={
                <Badge tone="block" className="gap-1.5">
                  <AlertTriangle className="size-3 shrink-0" aria-hidden />
                  {report.falsePositiveDetail.length} of {run_.controlsTotal}
                </Badge>
              }
            >
              <ProbeTable probes={report.falsePositiveDetail} />
            </DataPanel>
          )}
        </>
      ) : loading ? null : (
        /* With no run, this page was ~70% empty panels. One scene, one stated
           absence, and the history panel below it — nothing else. */
        <Card>
          <CardBody>
            <SceneState name="redteam" size="lg">
              <Absence
                className="text-left"
                figure="Every figure on this page"
                why="No battery has been run in this scope, so there is no stored report to read."
                needed="run a battery above — its report is written to the platform's own record and opens here"
              />
            </SceneState>
          </CardBody>
        </Card>
      )}

      {/* ── History ────────────────────────────────────────────────────────── */}
      <DataPanel
        eyebrow="history"
        title="Previous runs"
        collapsible
        summary={`${history.length} run${history.length === 1 ? "" : "s"}`}
        maxHeight={420}
        actions={
          <Badge tone="neutral" className="gap-1.5 font-mono">
            <History className="size-3 shrink-0" aria-hidden />
            {history.length} stored
          </Badge>
        }
      >
          {history.length === 0 ? (
            /* An honest empty state, which this product produces a great many of by
               design. The scene is Storyset's "No data" — it depicts nothing recorded
               yet, which is exactly what this is, and it sits above words that say the
               same thing so it is never the only thing saying it. */
            <SceneState name="empty" size="md">
              <Absence
                className="text-left"
                figure="Block rate over runs"
                why="No run of this battery has been stored in this scope yet."
                needed="run a battery — every run is stored, and the second one is what makes a block rate a trend rather than a snapshot"
              />
            </SceneState>
          ) : (
            <Table className="min-w-[720px]">
              <THead>
                <TH>Run</TH>
                <TH>Battery</TH>
                <TH>Mode</TH>
                <TH className="text-right">Blocked</TH>
                <TH className="text-right">False positives</TH>
                <TH>Verdict</TH>
              </THead>
              <TBody>
                {history.map((row) => (
                  <TR key={row.runId} className="align-top">
                    <TD>
                      <button
                        type="button"
                        onClick={() => open(row.runId)}
                        className={`rounded-sm text-left underline decoration-border underline-offset-2 hover:decoration-foreground ${FOCUS}`}
                      >
                        <Figure className="text-foreground">{row.runId}</Figure>
                        <span className="sr-only">, open this run</span>
                      </button>
                      <p className="text-xs text-muted-foreground">
                        {stamp(row.startedAt)} · {row.initiatedBy}
                      </p>
                    </TD>
                    <TD className="text-sm">{row.suite}</TD>
                    <TD>
                      <Badge tone={row.mode === 'live' ? 'risk' : 'neutral'}>{row.mode}</Badge>
                    </TD>
                    <TD className="text-right">
                      <Figure>{`${row.attacksBlocked}/${row.attacksTotal}`}</Figure>
                    </TD>
                    <TD className="text-right">
                      <Figure>{`${row.falsePositives}/${row.controlsTotal}`}</Figure>
                    </TD>
                    <TD>
                      <Badge tone={row.passed ? 'ok' : 'block'} className="gap-1.5">
                        {row.passed ? (
                          <CircleCheck className="size-3 shrink-0" aria-hidden />
                        ) : (
                          <CircleX className="size-3 shrink-0" aria-hidden />
                        )}
                        {row.passed ? 'cleared the floor' : 'below the floor'}
                      </Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
      </DataPanel>

      <Receipt
        label="Method"
        origin="every verdict is the rail's own return value for that exact string"
        detail="probes marked “needs the model layer” have no deterministic signature by design, so an offline run reports them as leaks rather than hiding them"
      />
    </div>
  )
}

/** Client entry for the Red-team section — gated on a reachable backend. */
export function RedteamMount(): ReactElement {
  return (
    <BackendGate>
      <RedteamView />
    </BackendGate>
  )
}
