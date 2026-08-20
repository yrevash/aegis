'use client'

import {
  AlertTriangle,
  CircleCheck,
  CircleX,
  History,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
  Swords,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import {
  getRedteamHistory,
  getRedteamRun,
  getRedteamSuites,
  redteamMessage,
  startRedteamRun,
  type RedteamMode,
  type RedteamProbe,
  type RedteamRun,
  type RedteamRunDetail,
  type RedteamSuite,
} from '@/lib/api/redteam'
import { useAuth } from '@/lib/auth/AuthContext'

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
            <p className="text-sm leading-relaxed text-foreground">{suite.summary}</p>
            <p className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {suite.owasp.map((id) => (
                <Badge key={id} tone="neutral" className="font-mono">
                  {id}
                </Badge>
              ))}
              <span>
                {suite.attacks} attacks · {suite.controls} benign controls ·{' '}
                {suite.semanticOnly} with no deterministic signature
              </span>
            </p>
            <p className="mt-2 text-xs text-muted-foreground">
              {Object.entries(suite.stages)
                .map(([stage, count]) => `${count} at the ${label(stage)} rail`)
                .join(' · ')}
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="max-w-prose text-pretty text-sm leading-relaxed text-muted-foreground">
            {estimate && estimate.modelCalls > 0 ? (
              <>
                Estimated cost before you start:{' '}
                <Figure className="font-medium text-foreground">{usd(estimate.costUsd)}</Figure>, up
                to {estimate.modelCalls} calls to{' '}
                <Figure className="text-foreground">{estimate.model}</Figure>, charged to the
                tenant&apos;s budget.
              </>
            ) : (
              <>Offline runs cost nothing: no model is called, and the ledger is untouched.</>
            )}
          </p>
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
 * One attack category's block rate, as a bar and as a verdict in words.
 *
 * The percentage used to be tinted `--ok` or `--block` and nothing else said which
 * it was — colour carrying the whole verdict, which DESIGN.md §2 rules out and
 * which fails outright for a red/green-confusable reader on the one screen where
 * pass and fail are the entire content. The word and the icon carry it now; the
 * tint is the third cue.
 */
function CategoryBar({
  name,
  blocked,
  total,
  rate,
  floor,
}: {
  name: string
  blocked: number
  total: number
  rate: number
  floor: number
}): ReactElement {
  const good = rate >= floor
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <span className="text-sm font-medium text-foreground capitalize">{name}</span>
        <span className="flex items-center gap-2.5">
          <span className="text-xs text-muted-foreground">
            <Figure>{`${blocked}/${total}`}</Figure> blocked
          </span>
          <Badge tone={good ? 'ok' : 'block'} className="gap-1.5">
            {good ? (
              <CircleCheck className="size-3 shrink-0" aria-hidden />
            ) : (
              <CircleX className="size-3 shrink-0" aria-hidden />
            )}
            {pct(rate)} {good ? 'clears the floor' : 'below the floor'}
          </Badge>
        </span>
      </div>
      <div
        role="img"
        aria-label={`${name}: ${pct(rate)} blocked, ${good ? 'at or above' : 'below'} the ${pct(floor)} floor`}
        className="h-1.5 overflow-hidden rounded-sm bg-surface-2"
      >
        <div
          className={good ? 'h-full rounded-sm bg-blue-600' : 'h-full rounded-sm bg-block-ink'}
          style={{ width: `${Math.max(rate * 100, 1.5)}%` }}
        />
      </div>
    </div>
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
      <SectionHeader
        as="h1"
        eyebrow="owasp llm top 10 · real verdicts"
        title="Red-team"
        note="Every verdict on this page is what the rail returned for that exact string. Nothing here is composed in the browser."
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

      {run_ && report ? (
        <>
          {/* ── What happened to the attacks ─────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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

          <Card>
            <CardBody className="space-y-2">
              <p className="max-w-prose text-pretty text-sm leading-relaxed text-foreground">
                {headline(run_)}.{' '}
                <span className="text-muted-foreground">{verdictNote(run_)}</span>
              </p>
              <p className="max-w-prose text-pretty text-xs leading-relaxed text-muted-foreground">
                {run_.mode === 'live'
                  ? run_.tenantId == null
                    ? 'Live run against the platform’s own rails: the model-backed injection, content-safety and topical layers ran. There is no tenant to bill, so these calls are not in the usage ledger and the cost above is the estimate, not a charge.'
                    : 'Live run: the model-backed injection, content-safety and topical layers ran, and the calls are in this tenant’s usage ledger.'
                  : 'Offline run: no model was called, so this measures the deterministic signatures alone — not the whole stack.'}
              </p>
              {comparison ? (
                <p className="max-w-prose text-pretty text-xs leading-relaxed text-muted-foreground">
                  Against the previous {run_.mode} run of this battery (
                  <Figure className="text-foreground">{comparison.previousRunId}</Figure>): block
                  rate {points(comparison.blockRate.change)}, false-positive rate{' '}
                  {points(comparison.falsePositiveRate.change)},{' '}
                  {comparison.attacksLeakedDelta === 0
                    ? 'the same attacks got through'
                    : `${Math.abs(comparison.attacksLeakedDelta)} ${
                        comparison.attacksLeakedDelta > 0 ? 'more' : 'fewer'
                      } attacks got through`}
                  .
                </p>
              ) : (
                <p className="text-xs leading-relaxed text-muted-foreground">
                  First run of this battery in this mode — there is nothing to compare it to yet.
                </p>
              )}
              <Receipt
                label="Run"
                origin={`${run_.suite} · ${run_.runId}`}
                detail={`started by ${run_.initiatedBy}${
                  run_.startedAt ? ` on ${new Date(run_.startedAt).toLocaleString()}` : ''
                }`}
              />
            </CardBody>
          </Card>

          {/* ── What the rails stopped ───────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="blocked"
              title="Attacks the rails stopped"
              actions={
                <span className="text-xs text-muted-foreground">
                  {report.rails.map((rail) => `${rail.layer} ${rail.blocks}`).join(' · ')}
                </span>
              }
            />
            <CardBody>
              {report.blocked.length === 0 ? (
                <EmptyState
                  icon={ShieldAlert}
                  title="Nothing was blocked"
                  body="Every probe in this battery reached the model. On an offline run that is expected for probes with no deterministic signature; on a live run it is a finding."
                />
              ) : (
                <Table>
                  <THead>
                    <TH className="w-24">Probe</TH>
                    <TH>Attack</TH>
                    <TH className="w-36">Rail</TH>
                    <TH className="w-[38%]">Verdict</TH>
                  </THead>
                  <TBody>
                    {report.blocked.map((probe) => (
                      <ProbeRow key={probe.id} probe={probe} />
                    ))}
                  </TBody>
                </Table>
              )}
            </CardBody>
          </Card>

          {/* ── What got through ─────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="got through"
              title="Attacks nothing stopped"
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
            />
            <CardBody>
              {report.leaked.length === 0 ? (
                <EmptyState
                  icon={ShieldCheck}
                  title="Every attack in this battery was stopped"
                  body="Read that against the two figures above before calling it coverage: a battery whose attacks were refused without examination, or one whose benign controls were also blocked, produces the same clean row."
                />
              ) : (
                <>
                  {leaks && leaks.unexpected.length > 0 ? (
                    <p className="mb-4 rounded-lg border border-block bg-block/10 px-4 py-3 text-sm leading-relaxed text-foreground">
                      <strong className="font-semibold">{leaks.unexpected.length}</strong> of these
                      has a deterministic signature and should have been caught. That is a gap in
                      the rails, not in the configuration.
                    </p>
                  ) : null}
                  <Table>
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
            </CardBody>
          </Card>

          {/* ── Per category ─────────────────────────────────────────────────── */}
          <Card>
            <CardHeader eyebrow="by category" title="Block rate per attack category" />
            <CardBody className="flex flex-col gap-4">
              {report.categories
                .filter((row) => row.category !== 'benign_control')
                .map((row) => (
                  <CategoryBar
                    key={row.category}
                    name={label(row.category)}
                    blocked={row.blocked}
                    total={row.total}
                    rate={row.blockRate}
                    floor={run_.minBlockRate}
                  />
                ))}
            </CardBody>
          </Card>
        </>
      ) : null}

      {/* ── History ────────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          eyebrow="history"
          title="Previous runs"
          actions={<History className="size-4 text-muted-foreground" aria-hidden />}
        />
        <CardBody>
          {history.length === 0 ? (
            <EmptyState
              icon={History}
              title="No run of this battery has been stored yet"
              body="Every battery you run is written to the platform's own record and appears here, newest first, so a block rate can be read as a trend rather than a snapshot."
            />
          ) : (
            <Table>
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
                        {row.startedAt
                          ? new Date(row.startedAt).toLocaleString()
                          : 'no start time recorded'}{' '}
                        · {row.initiatedBy}
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
        </CardBody>
      </Card>

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
