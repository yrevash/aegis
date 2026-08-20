'use client'

import {
  CircleCheck,
  CircleHelp,
  CircleMinus,
  CircleX,
  Clock,
  Layers,
  Loader2,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import {
  getPipelineHealth,
  getPipelines,
  getPlatformHealth,
  pipelineMessage,
  type ComponentHealth,
  type ComponentStatus,
  type NotRecorded,
  type PipelineHealthResponse,
  type PipelineStage,
  type PipelinesResponse,
  type PlatformHealthResponse,
} from '@/lib/api/pipeline'
import { useAuth } from '@/lib/auth/AuthContext'

import { PipelineDeclarationPanel } from './PipelineDeclarationView'

/** How often the aggregation is re-read. Slower than the cache page: these are queries. */
const POLL_MS = 15000

/**
 * Status → tone, word and icon.
 *
 * `unknown` is its own row and is never dressed as `down`: a probe that timed out has
 * established nothing, and colouring "nothing" the same as "no" is a lie in the safe
 * direction. `not_applicable` is likewise not a pass — it says the question does not
 * apply here (a non-Postgres dialect has no row security to bypass).
 */
const STATUS_META: Record<ComponentStatus, { tone: BadgeTone; label: string; icon: LucideIcon }> = {
  up: { tone: 'ok', label: 'up', icon: CircleCheck },
  degraded: { tone: 'risk', label: 'degraded', icon: TriangleAlert },
  down: { tone: 'block', label: 'down', icon: CircleX },
  unknown: { tone: 'neutral', label: 'unknown', icon: CircleHelp },
  not_applicable: { tone: 'neutral', label: 'not applicable', icon: CircleMinus },
}

/** A duration in milliseconds, in the largest unit that keeps it readable. */
function ms(value: number): string {
  if (value < 1000) return `${Math.round(value)} ms`
  if (value < 60_000) return `${(value / 1000).toFixed(2)} s`
  return `${(value / 60_000).toFixed(1)} min`
}

/** An age in seconds, in the largest unit that keeps it readable. */
function age(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)} s`
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`
  if (seconds < 86_400) return `${(seconds / 3600).toFixed(1)} h`
  return `${(seconds / 86_400).toFixed(1)} d`
}

/** A status pill — tinted band plus an icon plus the word, never colour alone. */
function StatusPill({ status }: { status: ComponentStatus }): ReactElement {
  const meta = STATUS_META[status] ?? STATUS_META.unknown
  const Icon = meta.icon
  return (
    <Badge tone={meta.tone} className="gap-1.5">
      <Icon className="size-3" />
      {meta.label}
    </Badge>
  )
}

/** One component row: what it is, its verdict, and the probe that produced it. */
function ComponentRow({ row }: { row: ComponentHealth }): ReactElement {
  return (
    <TR className="align-top">
      <TD className="whitespace-nowrap">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-foreground">{row.name}</span>
          <span className="font-mono text-[0.68rem] uppercase tracking-wide text-muted-foreground">
            {row.category}
            {row.required ? ' · required' : ''}
          </span>
        </div>
      </TD>
      <TD>
        <div className="flex max-w-xl flex-col gap-1">
          {row.detail ? (
            <span className="text-[0.8rem] leading-snug text-foreground">{row.detail}</span>
          ) : null}
          <span className="font-mono text-[0.68rem] leading-snug text-muted-foreground">
            {row.evidence}
          </span>
        </div>
      </TD>
      <TD className="whitespace-nowrap text-right">
        <StatusPill status={row.status} />
      </TD>
    </TR>
  )
}

/** The gaps card — every figure the platform refuses to show, and what it would take. */
function NotRecordedCard({
  title,
  gaps,
}: {
  title: string
  gaps: NotRecorded[]
}): ReactElement | null {
  if (gaps.length === 0) return null
  return (
    <Card>
      <CardHeader eyebrow="stated, not filled in" title={title} />
      <CardBody>
        <dl className="divide-y divide-border/60">
          {gaps.map((gap) => (
            <div key={gap.figure} className="py-3 first:pt-0 last:pb-0">
              <dt className="text-sm font-medium text-foreground">{gap.figure}</dt>
              <dd className="mt-1 text-[0.8rem] leading-snug text-muted-foreground">
                {gap.why}
                <span className="block pt-1 text-foreground">Needs: {gap.needs}</span>
              </dd>
            </div>
          ))}
        </dl>
      </CardBody>
    </Card>
  )
}

/**
 * A single-hue magnitude bar for one stage's p95, scaled against the slowest stage.
 *
 * A zero value draws an empty track rather than the 2% minimum sliver: a declared stage
 * with no run in the window has no measurement, and a visible bar would read as a fast
 * one.
 */
function StageBar({ value, max }: { value: number; max: number }): ReactElement {
  const pct = max > 0 && value > 0 ? Math.max(2, (value / max) * 100) : 0
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-surface-2">
      <div
        className="h-full rounded-full bg-blue-400 transition-[width] duration-500 motion-reduce:transition-none"
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}

/** The component table, rendered only for a principal the backend admits. */
function ComponentPanel({ data }: { data: PlatformHealthResponse }): ReactElement {
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader
          eyebrow="probed concurrently · every verdict carries its evidence"
          title="Dependencies"
        />
        <CardBody>
          <Table>
            <THead>
              <TR>
                <TH>Component</TH>
                <TH>What answered</TH>
                <TH className="text-right">Status</TH>
              </TR>
            </THead>
            <TBody>
              {data.components.map((row) => (
                <ComponentRow key={row.key} row={row} />
              ))}
            </TBody>
          </Table>
        </CardBody>
      </Card>
      <NotRecordedCard title="What the dependency table does not measure" gaps={data.not_recorded} />
    </div>
  )
}

/**
 * The job + stage aggregation, read against the declared ingest pipeline.
 *
 * `declared` is the ingestion pipeline's stage list from `GET /pipelines` (§8.12). It is
 * what the stage vocabulary below comes from — **not** the set of stages that happen to
 * have rows in `run_events`. Deriving the list from the rows made a stage that has never
 * run indistinguishable from one that does not exist, which is the drift this page is
 * now built to be immune to. When the declaration cannot be read the panel falls back to
 * the measured rows rather than inventing a list.
 */
function PipelinePanel({
  data,
  declared,
}: {
  data: PipelineHealthResponse
  declared: PipelineStage[] | null
}): ReactElement {
  if (!data.available) {
    return (
      <Card>
        <CardHeader eyebrow="job_runs · run_events" title="Pipeline" />
        <CardBody>
          <p className="py-6 text-sm text-muted-foreground">
            {data.unavailable_reason ??
              'The job and run-event tables could not be read.'}{' '}
            No figures are shown, because an unreadable pipeline and an idle one are
            different facts.
          </p>
        </CardBody>
      </Card>
    )
  }

  const slowest = data.stages.reduce((acc, s) => Math.max(acc, s.p95_ms), 0)
  const timed = new Map(data.stages.map((stage) => [stage.stage, stage]))
  const declaredNames = new Set((declared ?? []).map((stage) => stage.name))
  const stageRows = [
    ...(declared ?? []).map((stage) => ({
      name: stage.name,
      label: stage.label,
      timing: timed.get(stage.name) ?? null,
    })),
    // A measured stage the declaration does not name cannot happen while the tripwire in
    // `aegis.pipelines.bindings` holds — but if it ever did, hiding it would be the worse
    // failure, so it is rendered rather than dropped.
    ...data.stages
      .filter((stage) => !declaredNames.has(stage.stage))
      .map((stage) => ({ name: stage.stage, label: null, timing: stage })),
  ]

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="In flight" value={String(data.in_flight)} icon={Layers} tone="agent" />
        <StatCard
          label="Oldest pending"
          value={
            data.oldest_pending_age_seconds === null
              ? 'nothing pending'
              : age(data.oldest_pending_age_seconds)
          }
          icon={Clock}
          tone={
            data.oldest_pending_age_seconds !== null && data.oldest_pending_age_seconds > 900
              ? 'risk'
              : 'neutral'
          }
        />
        <StatCard
          label={`Failed · last ${data.window_hours}h`}
          value={
            data.finished_in_window === 0
              ? 'nothing finished'
              : `${data.failed_in_window} of ${data.finished_in_window}`
          }
          icon={TriangleAlert}
          tone={data.failed_in_window > 0 ? 'block' : 'ok'}
        />
        <StatCard
          label="Job duration p95"
          value={data.durations === null ? 'no finished job' : ms(data.durations.p95_ms)}
          icon={Clock}
        />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader eyebrow="job_runs · grouped by kind and status" title="Queue depth" />
          <CardBody>
            {data.depth.length === 0 ? (
              <p className="py-6 text-sm text-muted-foreground">
                No job has ever been recorded for this scope.
              </p>
            ) : (
              <Table>
                <THead>
                  <TR>
                    <TH>Kind</TH>
                    <TH>Status</TH>
                    <TH className="text-right">Jobs</TH>
                  </TR>
                </THead>
                <TBody>
                  {data.depth.map((row) => (
                    <TR key={`${row.job_type}:${row.status}`}>
                      <TD className="font-mono text-[0.75rem]">{row.job_type}</TD>
                      <TD>{row.status}</TD>
                      <TD className="tabular text-right">{row.count}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader
            eyebrow="every declared ingest stage · timed from run_events where it ran"
            title="Where the time goes"
          />
          <CardBody>
            {stageRows.length === 0 ? (
              <p className="py-6 text-sm text-muted-foreground">
                No ingest stage has committed in the last {data.window_hours} hours, so
                there is nothing timed to show. This is the only pipeline the platform
                writes stage events for — a query&rsquo;s node timings stream and are not
                persisted.
              </p>
            ) : (
              <ul className="space-y-3">
                {stageRows.map((row) => (
                  <li key={row.name}>
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                      <span className="font-mono text-[0.75rem] text-foreground">
                        {row.name}
                        {row.label ? (
                          <span className="pl-2 font-sans text-[0.7rem] text-muted-foreground">
                            {row.label}
                          </span>
                        ) : null}
                      </span>
                      <span className="tabular text-[0.75rem] text-muted-foreground">
                        {row.timing === null
                          ? `no run in the last ${data.window_hours}h`
                          : `p50 ${ms(row.timing.p50_ms)} · p95 ${ms(row.timing.p95_ms)} · ${
                              row.timing.runs
                            } run${row.timing.runs === 1 ? '' : 's'}`}
                      </span>
                    </div>
                    <div className="mt-1.5">
                      <StageBar value={row.timing?.p95_ms ?? 0} max={slowest} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>

      {data.recent_failures.length > 0 ? (
        <Card>
          <CardHeader
            eyebrow="job_runs.error · the reason the worker recorded"
            title="Recent failures"
          />
          <CardBody>
            <Table>
              <THead>
                <TR>
                  <TH>Kind</TH>
                  <TH>Reason</TH>
                  <TH className="text-right">Finished</TH>
                </TR>
              </THead>
              <TBody>
                {data.recent_failures.map((row, index) => (
                  <TR key={`${row.job_type}:${row.finished_at}:${index}`} className="align-top">
                    <TD className="whitespace-nowrap font-mono text-[0.75rem]">{row.job_type}</TD>
                    <TD className="text-[0.8rem] leading-snug">{row.error}</TD>
                    <TD className="whitespace-nowrap text-right text-[0.75rem] text-muted-foreground">
                      {row.finished_at ? new Date(row.finished_at).toLocaleString() : '—'}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardBody>
        </Card>
      ) : null}

      <Card>
        <CardHeader eyebrow="app.jobs.health · this API process" title="Durable job worker" />
        <CardBody>
          <div className="flex flex-wrap items-center gap-3">
            <Badge
              tone={
                data.worker.state === 'running'
                  ? 'ok'
                  : data.worker.state === 'down'
                    ? 'block'
                    : 'neutral'
              }
            >
              {data.worker.state}
            </Badge>
            <span className="text-sm text-muted-foreground">
              {data.worker.restarts} supervised restart
              {data.worker.restarts === 1 ? '' : 's'} since boot
              {data.worker.since
                ? ` · in this state since ${new Date(data.worker.since).toLocaleTimeString()}`
                : ''}
            </span>
          </div>
          {data.worker.detail ? (
            <p className="mt-2 text-[0.8rem] leading-snug text-foreground">{data.worker.detail}</p>
          ) : null}
        </CardBody>
      </Card>

      <Card>
        <CardHeader eyebrow="provenance" title="Where each figure comes from" />
        <CardBody>
          <dl className="divide-y divide-border/60">
            {Object.entries(data.sources).map(([key, sql]) => (
              <div key={key} className="flex flex-col gap-1 py-2 first:pt-0 last:pb-0">
                <dt className="eyebrow text-[0.58rem]">{key}</dt>
                <dd className="font-mono text-[0.7rem] leading-snug text-muted-foreground">
                  {sql}
                </dd>
              </div>
            ))}
          </dl>
        </CardBody>
      </Card>

      <NotRecordedCard title="What the pipeline view does not measure" gaps={data.not_recorded} />
    </div>
  )
}

/**
 * Pipeline health — an aggregation over what the platform already records.
 *
 * Nothing here is a monitoring subsystem. Queue depth, the oldest pending job, the
 * failure count and the duration percentiles are reads of `job_runs`; the per-stage
 * timings are reads of the `ingest_stage` events the ingest already commits with the
 * work they describe; the dependency table is the four reachability probes plus the
 * worker state this process already publishes plus the serving role's RLS attributes.
 *
 * The **dependency table appears only for platform staff** — its rows are process-wide
 * and cross-tenant, so there is no filter that would make them safe for a tenant admin
 * and the backend refuses instead. The pipeline figures below it are narrowed to the
 * caller's own tenant by the server, so a tenant admin reads its own queue and nobody
 * else's.
 */
export function PipelineHealthPanel(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [pipeline, setPipeline] = useState<PipelineHealthResponse | null>(null)
  const [components, setComponents] = useState<PlatformHealthResponse | null>(null)
  const [declared, setDeclared] = useState<PipelinesResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      setPipeline(await getPipelineHealth(token))
      setError(null)
    } catch (err) {
      setError(pipelineMessage(err, 'Could not read the pipeline aggregation.'))
    }
    try {
      // The declaration, not a figure: it is static per build, but it is re-read with the
      // rest so a deploy that changes the pipeline changes this page without a reload.
      setDeclared(await getPipelines(token))
    } catch {
      // The measured figures are still true without it; the stage list below simply
      // falls back to the rows that exist rather than showing a list it cannot vouch for.
      setDeclared(null)
    }
    try {
      setComponents(await getPlatformHealth(token))
    } catch {
      // A 403 is the expected answer for a tenant admin, and it is not an error to
      // report: the dependency table is simply not this principal's surface, so it is
      // absent rather than shown as a failure.
      setComponents(null)
    }
  }, [token])

  useEffect(() => {
    if (!hydrated) return
    let alive = true
    const tick = (): void => {
      if (alive) void load()
    }
    tick()
    const timer = setInterval(tick, POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [hydrated, load])

  if (error) {
    return (
      <Card>
        <CardBody>
          <p className="py-6 text-center text-sm text-danger">{error}</p>
        </CardBody>
      </Card>
    )
  }

  if (pipeline === null) {
    return (
      <Card>
        <CardBody>
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
            Aggregating the pipeline record…
          </div>
        </CardBody>
      </Card>
    )
  }

  return (
    <section className="space-y-4">
      <div>
        <p className="eyebrow mb-1">job_runs · run_events · four probes</p>
        <h2 className="t-title text-foreground">Pipeline health</h2>
      </div>
      {components ? <ComponentPanel data={components} /> : null}
      <PipelinePanel
        data={pipeline}
        declared={
          declared?.pipelines.find((spec) => spec.name === 'ingestion')?.stages ?? null
        }
      />
      {declared ? <PipelineDeclarationPanel data={declared} /> : null}
    </section>
  )
}
