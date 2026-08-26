'use client'

import {
  CheckCircle2,
  Fingerprint,
  ListChecks,
  ScanSearch,
  ShieldAlert,
  Sparkles,
  Users,
} from 'lucide-react'
import { useMemo, type ReactElement } from 'react'

import { RankedBars } from '@/components/charts/RankedBars'
import { StackedArea } from '@/components/charts/StackedArea'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { AuditLogRow } from '@/lib/api/types'

import {
  auditPulse,
  auditTrend,
  familyPrefix,
  percent,
  tallyBy,
  windowSentence,
  type AuditPulse,
} from './insights'
import { localSinceHoursAgo, type AuditQuery } from './query'

/** The one focus treatment on this surface: the ring token, at 2px, always visible. */
const FOCUS = 'outline-none focus-visible:ring-2 focus-visible:ring-ring'

/**
 * The questions a reviewer actually opens this screen to ask, as one click each.
 *
 * Every one of them is a **server** predicate — the same `GET /audit` parameters the
 * filter bar writes — so a lens narrows the whole trail rather than the page in view,
 * and the figures above the table keep describing exactly the set the table shows.
 * That is the difference between an analytic header and a decorative one.
 */
const LENSES: Array<{ id: string; label: string; patch: Partial<AuditQuery>; tip: string }> = [
  {
    id: 'blocked',
    label: 'Refused',
    patch: { outcome: 'blocked' },
    tip: 'Every action the platform declined. The outcome is classified server-side by aegis.governance.audit.classify_outcome — there is no verdict column on the trail.',
  },
  {
    id: 'guardrail',
    label: 'Guardrail',
    patch: { actionPrefix: 'guardrail.' },
    tip: 'The input and output rails: what they inspected and what they stopped.',
  },
  {
    id: 'query',
    label: 'Queries',
    patch: { actionPrefix: 'query.' },
    tip: 'Every question put to the agent, recorded before the answer exists.',
  },
  {
    id: 'approval',
    label: 'Approvals',
    patch: { actionPrefix: 'approval.' },
    tip: 'Human-in-the-loop decisions, with the person who made each one on the row.',
  },
  {
    id: 'documents',
    label: 'Uploads',
    patch: { actionPrefix: 'documents.' },
    tip: 'Documents entering the corpus — the front door of the ingest pipeline.',
  },
  {
    id: 'db',
    label: 'Console reads',
    patch: { actionPrefix: 'db.' },
    tip: 'The database console, audited on both sides of every statement it runs.',
  },
  {
    id: 'today',
    label: 'Last 24h',
    patch: { since: localSinceHoursAgo(24) },
    tip: 'Narrows the window to the last twenty-four hours, in your own time zone.',
  },
]

interface AuditInsightsProps {
  /** The rows `GET /audit` returned for the current filter. */
  rows: AuditLogRow[]
  loading: boolean
  /** The live server filter — lenses write into it. */
  query: AuditQuery
  onQuery: (next: AuditQuery) => void
}

/**
 * The analytic top of the audit screen: what is happening, by whom, and what is
 * being refused — before a single row of the trail.
 *
 * A 2,900-row append-only log is evidence, and evidence is not an answer. Read as a
 * table it can only be *searched*, which means a reviewer has to already know what
 * they are looking for; the shape of the trail — that refusals cluster in one rail,
 * that one actor accounts for four fifths of the volume, that activity stops at
 * 18:20 — is invisible in it. So the charts lead and the trail stays beneath them as
 * the thing they are derived from.
 *
 * **Every figure here is a count of the rows the server returned**, and the receipt
 * says so. `GET /audit` is filtered and paged server-side, so these describe *the
 * newest `limit` rows matching the filter*, never the whole trail — and there is no
 * honest way to extrapolate one to the other from a newest-first window, so nothing
 * here tries.
 */
export function AuditInsights({
  rows,
  loading,
  query,
  onQuery,
}: AuditInsightsProps): ReactElement {
  const pulse = useMemo(() => auditPulse(rows), [rows])
  const trend = useMemo(() => auditTrend(rows, 14), [rows])
  const byAction = useMemo(() => tallyBy(rows, (r) => r.action), [rows])
  const byActor = useMemo(() => tallyBy(rows, (r) => r.actor), [rows])
  const refusals = useMemo(
    () => tallyBy(rows.filter((r) => r.outcome === 'blocked'), (r) => r.action),
    [rows],
  )

  const trendRows = useMemo(
    () =>
      trend.buckets.map((bucket) => ({
        bucket: bucket.label,
        completed: bucket.completed,
        refused: bucket.blocked,
      })),
    [trend],
  )

  const window = windowSentence(trend)
  const patch = (next: Partial<AuditQuery>): void => onQuery({ ...query, ...next })

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader
          eyebrow={`audit_log · ${rows.length.toLocaleString()} of at most ${query.limit} rows`}
          title="What is happening"
          actions={
            <div className="flex flex-wrap items-center justify-end gap-1.5">
              {LENSES.map((lens) => (
                <Lens
                  key={lens.id}
                  label={lens.label}
                  tip={lens.tip}
                  active={isLensOn(query, lens.patch)}
                  onClick={() => patch(isLensOn(query, lens.patch) ? clearOf(lens.patch) : lens.patch)}
                />
              ))}
            </div>
          }
        />
        <CardBody className="grid gap-5 pt-0 xl:grid-cols-[minmax(0,24rem)_minmax(0,1fr)] xl:gap-6">
          <Facts pulse={pulse} loading={loading} />

          <div className="flex min-w-0 flex-col">
            <div className="mb-2 flex flex-wrap items-baseline gap-x-2 gap-y-1">
              <span className="eyebrow">Activity over the window</span>
              <span className="tabular font-mono text-[0.6875rem] text-muted-foreground">
                {window}
              </span>
              <InfoTip label="What the axis covers">
                The axis is the span of the rows in hand, split into fourteen equal slices
                at {trend.grain} — not a fixed &ldquo;last 12 hours&rdquo;. A fixed axis
                against a filter that matched nothing recent draws an empty chart and reads
                as an idle system, when what actually happened is that you asked about a
                different day.
              </InfoTip>
            </div>
            {trendRows.length === 0 ? (
              <Absence
                figure="Activity over time"
                why="No rows came back for this filter, so there is no window to plot."
                needed="Widen the time range or clear a predicate on the filter bar below."
              />
            ) : (
              <StackedArea
                data={trendRows}
                index="bucket"
                series={[
                  { key: 'completed', label: 'Completed', value: pulse.completed },
                  { key: 'refused', label: 'Refused', value: pulse.blocked },
                ]}
                height={188}
                xTickCount={5}
                valueFormatter={(value) => value.toLocaleString()}
              />
            )}
          </div>
        </CardBody>
        <div className="px-5 pb-5 md:px-6 md:pb-6">
          <Receipt
            origin="GET /audit · Postgres, append-only"
            detail={`${rows.length.toLocaleString()} rows in this view · ${window} · filtered server-side`}
          />
        </div>
      </Card>

      <Card>
        <CardHeader
          eyebrow="counted over the rows in this view"
          title="What is being done, by whom, and what was refused"
          actions={
            <InfoTip label="How these are counted">
              Three tallies over the same set the table below shows. An action is counted
              once per row; a refusal is a row the server classified `blocked`. Nothing is
              projected onto the rest of the trail — narrow the filter and every bar
              re-counts against the new answer.
            </InfoTip>
          }
        />
        <CardBody className="grid gap-6 pt-0 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)_minmax(0,1fr)]">
          <Panel
            icon={Sparkles}
            title="Actions"
            note={`${pulse.actions} distinct in this view`}
            tip="The trail's verb vocabulary. Each name is namespaced by the subsystem that wrote it, which is what makes an action prefix a usable filter."
          >
            {byAction.length === 0 ? (
              <NoRows what="actions" />
            ) : (
              <RankedBars
                data={byAction.map((entry) => ({ name: entry.name, value: entry.total }))}
                label="Actions in this view, most frequent first"
                color="graph"
                maxRows={7}
                valueFormatter={(value) => value.toLocaleString()}
              />
            )}
          </Panel>

          <Panel
            icon={Users}
            title="Actors"
            note={`${pulse.actors} distinct`}
            tip="The principal recorded on each row. A service identity and a person are both actors here; the trail does not flatten them into one."
          >
            {byActor.length === 0 ? (
              <NoRows what="actors" />
            ) : (
              <RankedBars
                data={byActor.map((entry) => ({ name: entry.name, value: entry.total }))}
                label="Actors in this view, most active first"
                color="agent"
                maxRows={6}
                valueFormatter={(value) => value.toLocaleString()}
              />
            )}
          </Panel>

          <Panel
            icon={ShieldAlert}
            title="Refusals"
            note={
              pulse.blocked > 0
                ? `${pulse.blocked} of ${pulse.total} · ${percent(pulse.refusalRate)}`
                : 'none in this view'
            }
            tip="Where the platform said no, grouped by the action it said no to. Clustering is the signal: refusals concentrated in one rail is that rail working, refusals spread evenly is a policy problem."
          >
            {refusals.length === 0 ? (
              <Absence
                figure="Refusals by action"
                why="No row in this view was classified blocked, so there is no distribution to draw."
                needed="Open the Refused lens above to ask the server for the refusals in the whole trail."
              />
            ) : (
              <RankedBars
                data={refusals.map((entry) => ({ name: entry.name, value: entry.total }))}
                label="Refused actions in this view, most frequent first"
                color="ml"
                maxRows={6}
                valueFormatter={(value) => value.toLocaleString()}
              />
            )}
          </Panel>
        </CardBody>
        <div className="px-5 pb-5 md:px-6 md:pb-6">
          <Receipt
            label="Counted from"
            origin="audit_log.action · audit_log.actor · audit_log.outcome"
            detail={
              byAction.length > 0
                ? `top family ${familyPrefix(byAction[0].name)}* · ${byAction[0].total.toLocaleString()} rows`
                : 'no rows to count'
            }
          />
        </div>
      </Card>
    </div>
  )
}

/** Whether every field a lens sets is already set to the same value. */
function isLensOn(query: AuditQuery, patch: Partial<AuditQuery>): boolean {
  return (Object.keys(patch) as Array<keyof AuditQuery>).every(
    (key) => query[key] === patch[key],
  )
}

/** The patch that turns a lens back off — its own fields, emptied. */
function clearOf(patch: Partial<AuditQuery>): Partial<AuditQuery> {
  const off: Partial<AuditQuery> = {}
  for (const key of Object.keys(patch) as Array<keyof AuditQuery>) {
    if (key === 'outcome' || key === 'tenantId') Object.assign(off, { [key]: null })
    else Object.assign(off, { [key]: '' })
  }
  return off
}

/** One lens chip: a named server predicate, on or off. */
function Lens({
  label,
  tip,
  active,
  onClick,
}: {
  label: string
  tip: string
  active: boolean
  onClick: () => void
}): ReactElement {
  return (
    <span className="inline-flex items-center gap-1">
      <button
        type="button"
        aria-pressed={active}
        onClick={onClick}
        className={cn(
          'h-7 touch-manipulation rounded-full border px-2.5 text-xs font-medium transition-colors duration-[--dur-fast]',
          FOCUS,
          active
            ? 'border-blue-600 bg-blue-50 text-blue-700'
            : 'border-border bg-card text-muted-foreground hover:bg-surface-2',
        )}
      >
        {label}
      </button>
      <InfoTip label={`What the ${label} lens asks for`}>{tip}</InfoTip>
    </span>
  )
}

/** The six headline counts, as one inset grid of measured facts. */
function Facts({ pulse, loading }: { pulse: AuditPulse; loading: boolean }): ReactElement {
  const cells: Array<{
    label: string
    value: string
    sub: string
    icon: typeof ListChecks
    tone: Signal
    tip: string
  }> = [
    {
      label: 'Events',
      value: pulse.total.toLocaleString(),
      sub: 'rows in this view',
      icon: ListChecks,
      tone: 'neutral',
      tip: 'Rows the server returned for the current filter — not the size of the trail. The page size is set on the filter bar below.',
    },
    {
      label: 'Refused',
      value: pulse.blocked.toLocaleString(),
      sub: `${percent(pulse.refusalRate)} of this view`,
      icon: ShieldAlert,
      tone: 'block',
      tip: 'Rows the server classified blocked. Classification is server-side, so the word here is the same word the outcome filter selects by.',
    },
    {
      label: 'Human-gated',
      value: pulse.approved.toLocaleString(),
      sub: 'carry an approver',
      icon: CheckCircle2,
      tone: 'ok',
      tip: 'Rows carrying approved_by — a real person named on the record, not an inference from the action name.',
    },
    {
      label: 'Traced',
      value: pulse.traced.toLocaleString(),
      sub: `${percent(pulse.traceRate)} carry a trace id`,
      icon: Fingerprint,
      tone: 'graph',
      tip: 'Rows joined to an OpenTelemetry trace. A row without one is not a defect: not every audited action runs inside a traced request.',
    },
    {
      label: 'Actors',
      value: pulse.actors.toLocaleString(),
      sub: 'distinct principals',
      icon: Users,
      tone: 'agent',
      tip: 'Distinct non-empty actors in this view. Service identities and people are both counted, because the trail records both.',
    },
    {
      label: 'Models',
      value: pulse.models.toLocaleString(),
      sub: 'deployments named',
      icon: ScanSearch,
      tone: 'ml',
      tip: 'Distinct model deployments named on a row. Most audited actions never touch a model, so this is far smaller than the event count by design.',
    },
  ]

  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3 xl:grid-cols-2">
      {cells.map((cell) => {
        const signal = SIGNALS[cell.tone]
        return (
          <div key={cell.label} className="bg-card p-3.5">
            <p className="flex items-center gap-1.5">
              <span
                className={cn('grid size-5 shrink-0 place-items-center rounded', signal.bg)}
                aria-hidden
              >
                <cell.icon className={cn('size-3', signal.text)} />
              </span>
              <span className="eyebrow mb-0">{cell.label}</span>
              <InfoTip label={`About ${cell.label}`}>{cell.tip}</InfoTip>
            </p>
            <Figure size="stat" className="mt-1 block text-foreground">
              {loading ? '·' : cell.value}
            </Figure>
            <p className="mt-0.5 font-mono text-[0.6875rem] leading-4 text-muted-foreground">
              {cell.sub}
            </p>
          </div>
        )
      })}
    </div>
  )
}

/** One titled distribution, with the sentence that explains it in a tip. */
function Panel({
  icon: Icon,
  title,
  note,
  tip,
  children,
}: {
  icon: typeof ListChecks
  title: string
  note: string
  tip: string
  children: ReactElement
}): ReactElement {
  return (
    <section className="flex min-w-0 flex-col gap-2.5">
      <h3 className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
        <span className="inline-flex items-center gap-1.5">
          <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="text-sm font-semibold text-foreground">{title}</span>
        </span>
        <span className="font-mono text-[0.6875rem] text-muted-foreground">{note}</span>
        <InfoTip label={`About ${title}`}>{tip}</InfoTip>
      </h3>
      {children}
    </section>
  )
}

/** The slot a distribution would have occupied, when there is nothing to distribute. */
function NoRows({ what }: { what: string }): ReactElement {
  return (
    <p className="rounded-md border border-dashed border-border bg-surface-2/40 px-3 py-4 text-xs text-muted-foreground">
      No {what} in this view — the filter matched no rows.
    </p>
  )
}
