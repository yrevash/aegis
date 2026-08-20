'use client'

import {
  AlertTriangle,
  History,
  Loader2,
  Play,
  ShieldAlert,
  ShieldCheck,
  Swords,
  Target,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

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

/** One probe row — the attack, the rail that judged it, and that rail's own words. */
function ProbeRow({ probe }: { probe: RedteamProbe }): ReactElement {
  return (
    <TR>
      <TD className="align-top">
        <span className="font-mono text-[0.72rem] text-muted-foreground">{probe.id}</span>
      </TD>
      <TD className="align-top">
        <p className="text-sm text-foreground">{probe.prompt}</p>
        <p className="mt-1 text-[0.72rem] capitalize text-muted-foreground">
          {label(probe.category)} · {probe.owasp} · {label(probe.stage)} rail
        </p>
      </TD>
      <TD className="align-top whitespace-nowrap">
        {probe.layer ? (
          <Badge tone={probe.neutralized ? 'ok' : 'neutral'} className="font-mono text-[0.66rem]">
            {probe.layer}
          </Badge>
        ) : (
          <span className="text-[0.72rem] text-muted-foreground">no rail fired</span>
        )}
      </TD>
      <TD className="align-top">
        <span className="font-mono text-[0.72rem] uppercase text-foreground">{probe.verdict}</span>
        <p className="mt-1 text-[0.72rem] leading-snug text-muted-foreground">{probe.reason}</p>
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
      <CardBody className="space-y-5 pt-0">
        <div className="grid gap-4 md:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">Battery</span>
            <select
              value={selected}
              onChange={(event) => onSelect(event.target.value)}
              className="rounded-xl border border-border bg-card px-3 py-2 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--blue-200)]"
            >
              {suites.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.title}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-sm font-medium text-foreground">Mode</span>
            <select
              value={mode}
              onChange={(event) => onMode(event.target.value as RedteamMode)}
              className="rounded-xl border border-border bg-card px-3 py-2 text-sm text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--blue-200)]"
            >
              <option value="offline">Offline — deterministic signatures only</option>
              <option value="live">Live — drives the model layers, spends budget</option>
            </select>
          </label>
        </div>

        {suite ? (
          <div className="rounded-xl border border-border bg-surface-2 px-4 py-3">
            <p className="text-sm text-foreground">{suite.summary}</p>
            <p className="mt-2 flex flex-wrap items-center gap-2 text-[0.72rem] text-muted-foreground">
              {suite.owasp.map((id) => (
                <Badge key={id} tone="neutral" className="font-mono text-[0.66rem]">
                  {id}
                </Badge>
              ))}
              <span>
                {suite.attacks} attacks · {suite.controls} benign controls ·{' '}
                {suite.semanticOnly} with no deterministic signature
              </span>
            </p>
            <p className="mt-2 text-[0.72rem] text-muted-foreground">
              {Object.entries(suite.stages)
                .map(([stage, count]) => `${count} at the ${label(stage)} rail`)
                .join(' · ')}
            </p>
          </div>
        ) : null}

        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {estimate && estimate.modelCalls > 0 ? (
              <>
                Estimated cost before you start:{' '}
                <span className="tabular font-medium text-foreground">{usd(estimate.costUsd)}</span>{' '}
                — up to {estimate.modelCalls} calls to{' '}
                <span className="font-mono text-foreground">{estimate.model}</span>, charged to the
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
            className="inline-flex shrink-0 items-center gap-2 rounded-xl bg-block-ink px-4 py-2.5 text-sm font-medium text-white transition-opacity hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)] disabled:cursor-not-allowed disabled:opacity-60 motion-reduce:transition-none"
          >
            {running ? (
              <>
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" /> Running…
              </>
            ) : (
              <>
                <Play className="size-4" /> Run battery
              </>
            )}
          </button>
        </div>

        {!mayRun && refusal ? (
          <p className="rounded-xl border border-border bg-surface-2 px-4 py-3 text-sm text-muted-foreground">
            {refusal}
          </p>
        ) : null}
      </CardBody>
    </Card>
  )
}

/**
 * Red-team — pick a battery, run it, and read what each rail actually did.
 *
 * Every number is the rail's own verdict on a real probe: no figure here is composed
 * in the browser. What was **blocked** is given the same room as what got through,
 * because a red team that finds nothing is a result and needs to be readable as one —
 * `verdictNote` says whether a perfect score is coverage or over-blocking rather than
 * showing a green tick either way.
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

  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">owasp llm top 10 · real verdicts</p>
        <h1 className="t-hero text-foreground">Red-team</h1>
      </div>

      {error ? (
        <Card>
          <CardBody>
            <p className="text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : null}

      {loading ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
              Reading the battery catalogue…
            </div>
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
          {/* ── Headline ─────────────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Attacks blocked"
              value={`${run_.attacksBlocked}/${run_.attacksTotal}`}
              icon={ShieldCheck}
              tone="ok"
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
              value={`${run_.attacksTotal - run_.attacksBlocked - run_.attacksUnchecked}`}
              icon={ShieldAlert}
              tone={
                run_.attacksTotal === run_.attacksBlocked + run_.attacksUnchecked ? 'ok' : 'block'
              }
            />
            <StatCard label="Block rate" value={pct(run_.blockRate)} icon={Swords} tone="neutral" />
            {run_.attacksUnchecked > 0 ? (
              <StatCard
                label="Refused without being examined"
                value={`${run_.attacksUnchecked}`}
                icon={AlertTriangle}
                tone="block"
              />
            ) : null}
            <StatCard
              label="Benign controls wrongly blocked"
              value={`${run_.falsePositives}/${run_.controlsTotal}`}
              icon={AlertTriangle}
              tone={run_.falsePositives === 0 ? 'ok' : 'block'}
            />
          </div>

          <Card>
            <CardBody className="space-y-2">
              <p className="text-sm text-foreground">
                {headline(run_)}.{' '}
                <span className="text-muted-foreground">{verdictNote(run_)}</span>
              </p>
              <p className="text-[0.72rem] text-muted-foreground">
                {run_.mode === 'live'
                  ? run_.tenantId == null
                    ? 'Live run against the platform’s own rails: the model-backed injection, content-safety and topical layers ran. There is no tenant to bill, so these calls are not in the usage ledger and the cost below is the estimate, not a charge.'
                    : 'Live run: the model-backed injection, content-safety and topical layers ran, and the calls are in this tenant’s usage ledger.'
                  : 'Offline run: no model was called, so this measures the deterministic signatures alone — not the whole stack.'}{' '}
                Judged against a {pct(run_.minBlockRate)} block-rate floor and a{' '}
                {pct(run_.maxFalsePositiveRate)} false-positive ceiling. Suite{' '}
                <span className="font-mono text-foreground">{run_.suite}</span>, run{' '}
                <span className="font-mono text-foreground">{run_.runId}</span> by {run_.initiatedBy}
                {run_.startedAt ? ` on ${new Date(run_.startedAt).toLocaleString()}` : ''}.
              </p>
              {comparison ? (
                <p className="text-[0.72rem] text-muted-foreground">
                  Against the previous {run_.mode} run of this battery (
                  <span className="font-mono text-foreground">{comparison.previousRunId}</span>):
                  block rate {points(comparison.blockRate.change)}, false-positive rate{' '}
                  {points(comparison.falsePositiveRate.change)},{' '}
                  {comparison.attacksLeakedDelta === 0
                    ? 'the same attacks got through'
                    : `${Math.abs(comparison.attacksLeakedDelta)} ${
                        comparison.attacksLeakedDelta > 0 ? 'more' : 'fewer'
                      } attacks got through`}
                  .
                </p>
              ) : (
                <p className="text-[0.72rem] text-muted-foreground">
                  First run of this battery in this mode — there is nothing to compare it to yet.
                </p>
              )}
            </CardBody>
          </Card>

          {/* ── What the rails stopped ───────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="blocked"
              title="Attacks the rails stopped"
              actions={
                <span className="text-[0.72rem] text-muted-foreground">
                  {report.rails.map((rail) => `${rail.layer} ${rail.blocks}`).join(' · ')}
                </span>
              }
            />
            <CardBody className="pt-0">
              {report.blocked.length === 0 ? (
                <p className="py-6 text-sm text-muted-foreground">
                  Nothing was blocked. Every probe in this battery reached the model.
                </p>
              ) : (
                <Table>
                  <THead>
                    <TH className="w-24">Probe</TH>
                    <TH>Attack</TH>
                    <TH className="w-36">Rail</TH>
                    <TH className="w-[40%]">Verdict</TH>
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
                <Badge tone={report.leaked.length === 0 ? 'ok' : 'block'}>
                  {report.leaked.length} of {run_.attacksTotal}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {report.leaked.length === 0 ? (
                <p className="py-6 text-sm text-muted-foreground">
                  Every attack in this battery was stopped. Check the benign controls above before
                  reading that as coverage.
                </p>
              ) : (
                <>
                  {leaks && leaks.unexpected.length > 0 ? (
                    <p className="mb-4 rounded-xl border border-border bg-surface-2 px-4 py-3 text-sm text-foreground">
                      {leaks.unexpected.length} of these has a deterministic signature and should
                      have been caught. That is a gap in the rails, not in the configuration.
                    </p>
                  ) : null}
                  <Table>
                    <THead>
                      <TH className="w-24">Probe</TH>
                      <TH>Attack</TH>
                      <TH className="w-36">Why</TH>
                      <TH className="w-[40%]">Verdict</TH>
                    </THead>
                    <TBody>
                      {report.leaked.map((probe) => (
                        <TR key={probe.id}>
                          <TD className="align-top">
                            <span className="font-mono text-[0.72rem] text-muted-foreground">
                              {probe.id}
                            </span>
                          </TD>
                          <TD className="align-top">
                            <p className="text-sm text-foreground">{probe.prompt}</p>
                            <p className="mt-1 text-[0.72rem] capitalize text-muted-foreground">
                              {label(probe.category)} · {probe.owasp} · {label(probe.stage)} rail
                            </p>
                          </TD>
                          <TD className="align-top">
                            <Badge tone={probe.needsLlm ? 'risk' : 'block'}>
                              {probe.needsLlm ? 'needs the model layer' : 'signature missed it'}
                            </Badge>
                          </TD>
                          <TD className="align-top">
                            <span className="font-mono text-[0.72rem] uppercase text-foreground">
                              {probe.verdict}
                            </span>
                            <p className="mt-1 text-[0.72rem] leading-snug text-muted-foreground">
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
            <CardBody className="flex flex-col gap-4 pt-0">
              {report.categories
                .filter((row) => row.category !== 'benign_control')
                .map((row) => {
                  const good = row.blockRate >= run_.minBlockRate
                  const hex = good ? 'var(--ok)' : 'var(--block)'
                  return (
                    <div key={row.category} className="flex flex-col gap-1.5">
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium capitalize text-foreground">
                          {label(row.category)}
                        </span>
                        <span className="flex items-center gap-2">
                          <span className="tabular font-mono text-[0.72rem] text-muted-foreground">
                            {row.blocked}/{row.total} blocked
                          </span>
                          <span
                            className="tabular w-11 text-right font-mono text-[0.8rem] font-semibold"
                            style={{ color: hex }}
                          >
                            {pct(row.blockRate)}
                          </span>
                        </span>
                      </div>
                      <div className="relative h-3 rounded-sm bg-surface-2">
                        <div
                          className="absolute inset-y-0 left-0 rounded-sm"
                          style={{
                            width: `${Math.max(row.blockRate * 100, 1.5)}%`,
                            background: hex,
                          }}
                        />
                      </div>
                    </div>
                  )
                })}
            </CardBody>
          </Card>
        </>
      ) : null}

      {/* ── History ────────────────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          eyebrow="history"
          title="Previous runs"
          actions={<History className="size-4 text-muted-foreground" />}
        />
        <CardBody className="pt-0">
          {history.length === 0 ? (
            <p className="py-6 text-sm text-muted-foreground">
              No run of this battery has been stored yet.
            </p>
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
                  <TR key={row.runId}>
                    <TD>
                      <button
                        type="button"
                        onClick={() => open(row.runId)}
                        className="rounded-sm font-mono text-[0.72rem] text-foreground underline decoration-border underline-offset-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--blue-200)]"
                      >
                        {row.runId}
                      </button>
                      <p className="text-[0.72rem] text-muted-foreground">
                        {row.startedAt ? new Date(row.startedAt).toLocaleString() : '—'} ·{' '}
                        {row.initiatedBy}
                      </p>
                    </TD>
                    <TD className="text-sm">{row.suite}</TD>
                    <TD>
                      <Badge tone={row.mode === 'live' ? 'risk' : 'neutral'}>{row.mode}</Badge>
                    </TD>
                    <TD className="tabular text-right font-mono text-[0.78rem]">
                      {row.attacksBlocked}/{row.attacksTotal}
                    </TD>
                    <TD className="tabular text-right font-mono text-[0.78rem]">
                      {row.falsePositives}/{row.controlsTotal}
                    </TD>
                    <TD>
                      <Badge tone={row.passed ? 'ok' : 'block'}>
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

      <p className="text-[0.72rem] leading-snug text-muted-foreground">
        Every verdict on this page is what the rail returned for that exact string —{' '}
        <Target className="inline size-3" /> nothing here is composed in the browser. Probes marked
        &ldquo;needs the model layer&rdquo; have no deterministic signature by design, so an offline
        run reports them as leaks rather than hiding them.
      </p>
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
