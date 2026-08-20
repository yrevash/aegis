'use client'

import {
  CheckCircle2,
  ChevronDown,
  Clock3,
  Gavel,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Timer,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { ConsentStatement, GateReceipt, ProposedAction } from '@/components/approval/ApprovalCard'
import { readApproval } from '@/components/approval/approvalActions'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { signalForRisk } from '@/config/signals'
import { errorSentence } from '@/lib/api/apiError'
import {
  decideApproval,
  getApprovals,
  type ApprovalInboxRow,
  type ApprovalStatusFilter,
} from '@/lib/api/approvals'
import type { ApprovalDecision } from '@/lib/api/types'
import { useAuth } from '@/lib/auth/AuthContext'
import { isPlatformAdmin } from '@/lib/auth/tier'
import { cn } from '@/lib/utils'

/** How the list is loading, or what it loaded. */
type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: ApprovalInboxRow[] }

/** The three queues, and what each one answers. */
const FILTERS: { id: ApprovalStatusFilter; label: string }[] = [
  { id: 'pending', label: 'Waiting' },
  { id: 'decided', label: 'Decided' },
  { id: 'all', label: 'Everything' },
]

/** How far back to look. `null` is no `since` bound at all. */
const WINDOWS: { id: string; label: string; hours: number | null }[] = [
  { id: '24h', label: 'Last 24 hours', hours: 24 },
  { id: '7d', label: 'Last 7 days', hours: 24 * 7 },
  { id: 'all', label: 'Since the beginning', hours: null },
]

/** Statuses that mean the gate is closed — nothing more will happen to it. */
const CLOSED = new Set(['approved', 'rejected', 'expired'])

/** The signal colour a lifecycle status carries. */
function statusVariant(status: string): 'ok' | 'block' | 'risk' | 'agent' | 'neutral' {
  if (status === 'approved') return 'ok'
  if (status === 'rejected') return 'block'
  if (status === 'expired') return 'risk'
  if (status === 'pending' || status === 'resuming') return 'agent'
  return 'neutral'
}

/** Local wall-clock time from an ISO 8601 timestamp, or an em dash. */
function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const at = new Date(iso)
  if (Number.isNaN(at.getTime())) return iso
  return at.toLocaleString([], {
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  })
}

/** How long ago something was raised, in the fewest words that stay true. */
function ago(iso: string | null, now: number): string | null {
  if (!iso) return null
  const at = new Date(iso).getTime()
  if (Number.isNaN(at)) return null
  const minutes = Math.max(0, Math.round((now - at) / 60_000))
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h ago`
  return `${Math.round(hours / 24)} days ago`
}

/**
 * How long is left on a gate's SLA, in words.
 *
 * The deadline is real: the sweeper marks a past-deadline gate expired, and
 * auto-**rejects** a HIGH-risk one. So a queue that showed only a timestamp would be
 * hiding the fact that not deciding is itself a decision.
 */
function slaLeft(deadline: string | null, now: number): string | null {
  if (!deadline) return null
  const at = new Date(deadline).getTime()
  if (Number.isNaN(at)) return null
  const minutes = Math.round((at - now) / 60_000)
  if (minutes <= 0) return 'past its deadline'
  if (minutes < 60) return `${minutes} min left`
  const hours = Math.round(minutes / 60)
  if (hours < 48) return `${hours} h left`
  return `${Math.round(hours / 24)} days left`
}

/** True once the deadline has gone by, so the sweeper decides instead of a person. */
function isOverdue(deadline: string | null, now: number): boolean {
  if (!deadline) return false
  const at = new Date(deadline).getTime()
  return !Number.isNaN(at) && at <= now
}

/**
 * The share of a gate's SLA that has already been spent, 0..1.
 *
 * Drawn rather than described: a deadline printed as a date asks the reader to do the
 * subtraction, and the whole point of the queue is that a gate left alone eventually
 * decides itself. `null` when either end of the window is missing — there is no
 * proportion to draw, and inventing one would be inventing urgency.
 */
function slaSpent(createdAt: string | null, deadline: string | null, now: number): number | null {
  if (!createdAt || !deadline) return null
  const from = new Date(createdAt).getTime()
  const to = new Date(deadline).getTime()
  if (Number.isNaN(from) || Number.isNaN(to) || to <= from) return null
  return Math.max(0, Math.min(1, (now - from) / (to - from)))
}

/** Who a gate belongs to, said plainly. */
function ownerLabel(tenantId: number | null): string {
  return tenantId === null ? 'Aegis · no tenant' : `Tenant #${tenantId}`
}

/** `operations_lead` → `Operations lead`, for the persona that raised the gate. */
function personaLabel(persona: string | null): string | null {
  if (!persona) return null
  const words = persona.replace(/[_-]+/g, ' ').trim()
  return words === '' ? null : words.charAt(0).toUpperCase() + words.slice(1)
}

/** The `since` instant for a window, or null for no bound. */
function sinceFor(hours: number | null): string | null {
  if (hours === null) return null
  return new Date(Date.now() - hours * 3_600_000).toISOString()
}

interface ApprovalInboxProps {
  token: string | null
  /** Whether the signed-in operator may target one tenant's queue (platform staff). */
  canFilterByTenant: boolean
}

/**
 * The durable approvals queue — every action the agent paused on rather than took.
 *
 * `ApprovalCard` renders one live gate inside a run. This is the list around it, and
 * until §7.1 there was no list: a run that parked at the gate survived as a database
 * row and a checkpoint that no screen in the product could reach.
 *
 * **It is a decision queue, not a feed.** The screen used to render one uniform card
 * per gate, so five gates that need a person and ninety-five that are already history
 * arrived at the same size, in the same order, in the same voice — and the reader had
 * to read all hundred to find the five. Here the split is structural: what is waiting
 * is opened, ordered by how little time is left, and carries the decision; what is
 * decided is a dense row that opens only if asked. The counting strip above says how
 * many of each there are before either list is read.
 *
 * **Who sees what is the server's answer, not this component's.** Every row arrives
 * with `decidable` and, when it is false, the `blocked_reason` the decision endpoint
 * would give — so a platform operator looking at a tenant's gate sees the controls,
 * sees them disabled, and reads why. Hiding them would hide the rule; enabling them
 * would earn a 403. Deriving the rule here in TypeScript would be a second copy of it
 * that can drift from the one the button is about to hit.
 *
 * **A failure here is a failure of the queue, not of the backend.** The server's own
 * sentence goes on the screen, with the retry beside it.
 */
export function ApprovalInbox({ token, canFilterByTenant }: ApprovalInboxProps): ReactElement {
  const [filter, setFilter] = useState<ApprovalStatusFilter>('pending')
  const [lookback, setLookback] = useState<string>('7d')
  const [tenant, setTenant] = useState<string>('all')
  // The tenants seen in an *unfiltered* read. Kept out of the row list because
  // narrowing to one tenant narrows the rows too, and a chooser rebuilt from those
  // rows would drop every option except the one already chosen.
  const [knownTenants, setKnownTenants] = useState<number[]>([])
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [busy, setBusy] = useState<string | null>(null)
  const [notice, setNotice] = useState<{
    kind: 'ok' | 'refused'
    text: string
  } | null>(null)
  // One clock for every countdown, ticking a minute at a time. Per-row timers would be
  // a dozen intervals redrawing the same list.
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60_000)
    return () => clearInterval(id)
  }, [])

  const refresh = useCallback(async (): Promise<void> => {
    const hours = WINDOWS.find((w) => w.id === lookback)?.hours ?? null
    try {
      const res = await getApprovals(
        {
          status: filter,
          since: sinceFor(hours),
          tenantId: canFilterByTenant && tenant !== 'all' ? Number(tenant) : null,
          limit: 100,
        },
        token,
      )
      setLoad({ status: 'ready', rows: res.rows })
      if (tenant === 'all') {
        const seen = new Set<number>()
        for (const row of res.rows) if (row.tenant_id !== null) seen.add(row.tenant_id)
        setKnownTenants([...seen].sort((a, b) => a - b))
      }
    } catch (error: unknown) {
      setLoad({
        status: 'error',
        message: errorSentence(
          error,
          'The approvals queue did not load. Check the backend is reachable, then retry.',
        ),
      })
    }
  }, [token, filter, lookback, tenant, canFilterByTenant])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const rows = useMemo(() => (load.status === 'ready' ? load.rows : []), [load])

  /**
   * The two lists, and the counts above them. Waiting gates are ordered by how
   * little time is left, because a queue sorted by arrival buries the one that is
   * about to expire; decided gates stay newest-first, which is how history reads.
   */
  const { waiting, decided, counts } = useMemo(() => {
    const pend = rows.filter((r) => r.status === 'pending' || r.status === 'resuming')
    const done = rows.filter((r) => CLOSED.has(r.status))
    const deadline = (r: ApprovalInboxRow): number => {
      const t = r.sla_deadline ? new Date(r.sla_deadline).getTime() : Number.NaN
      return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t
    }
    const decidedAt = (r: ApprovalInboxRow): number => {
      const t = new Date(r.decided_at ?? r.created_at).getTime()
      return Number.isNaN(t) ? 0 : t
    }
    return {
      waiting: [...pend].sort((a, b) => deadline(a) - deadline(b)),
      decided: [...done].sort((a, b) => decidedAt(b) - decidedAt(a)),
      counts: {
        waiting: pend.length,
        overdue: pend.filter((r) => isOverdue(r.sla_deadline, now)).length,
        approved: done.filter((r) => r.status === 'approved').length,
        rejected: done.filter((r) => r.status === 'rejected').length,
        expired: done.filter((r) => r.status === 'expired').length,
      },
    }
  }, [rows, now])

  const decide = async (id: string, decision: ApprovalDecision): Promise<void> => {
    setBusy(id)
    setNotice(null)
    try {
      const result = await decideApproval(id, decision, token)
      setNotice({
        kind: 'ok',
        text: result.accepted
          ? `Gate ${decision === 'approve' ? 'approved' : 'rejected'} — the parked run is now ${result.status}.`
          : 'That gate had already been decided. Nothing ran twice.',
      })
      await refresh()
    } catch (error: unknown) {
      // The server's refusal is the ownership rule stating itself. Show it verbatim.
      setNotice({
        kind: 'refused',
        text: errorSentence(error, 'The decision did not go through. Try it again.'),
      })
    } finally {
      setBusy(null)
    }
  }

  const windowLabel = WINDOWS.find((w) => w.id === lookback)?.label ?? 'this window'
  const scope = `aegis.approvals · ${windowLabel.toLowerCase()}`
  // A tile only appears when the loaded set could contain what it counts. On the
  // Decided queue the server sends no pending rows, so a "Waiting on a decision: 0"
  // tile would be counting rows it was never given — and five gates really are
  // waiting one tab away. An absence of data is not a zero.
  const showPendingTiles = filter !== 'decided'
  const showDecidedTiles = filter !== 'pending'

  return (
    <div className="flex flex-col gap-4">
      {/* ── The one control strip, above both lists ─────────────────────────── */}
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3 rounded-lg border border-border bg-surface px-4 py-3">
        <fieldset className="min-w-0">
          <legend className="eyebrow mb-1.5">Queue</legend>
          <div className="flex flex-wrap gap-1.5">
            {FILTERS.map((option) => (
              <Button
                key={option.id}
                type="button"
                size="sm"
                variant={filter === option.id ? 'default' : 'outline'}
                aria-pressed={filter === option.id}
                onClick={() => setFilter(option.id)}
              >
                {option.label}
              </Button>
            ))}
          </div>
        </fieldset>

        <div className="min-w-0">
          <label htmlFor="approvals-window" className="eyebrow mb-1.5 block">
            Raised
          </label>
          <select
            id="approvals-window"
            value={lookback}
            onChange={(event) => setLookback(event.target.value)}
            className="h-8 rounded-lg border border-border bg-card px-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {WINDOWS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {canFilterByTenant && (
          <div className="min-w-0">
            <label htmlFor="approvals-tenant" className="eyebrow mb-1.5 block">
              Whose gate
            </label>
            <select
              id="approvals-tenant"
              value={tenant}
              onChange={(event) => setTenant(event.target.value)}
              className="h-8 rounded-lg border border-border bg-card px-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <option value="all">Every tenant</option>
              {knownTenants.map((id) => (
                <option key={id} value={String(id)}>
                  Tenant #{id}
                </option>
              ))}
            </select>
          </div>
        )}

        <div className="flex items-center gap-2 sm:ml-auto">
          <Button type="button" size="sm" variant="outline" onClick={() => void refresh()}>
            <RefreshCw className="size-4" aria-hidden /> Refresh
          </Button>
        </div>
      </div>

      {/* ── What the queue holds, before either list is read ─────────────────── */}
      {load.status === 'ready' && rows.length > 0 && (
        <div
          className={cn(
            'grid grid-cols-2 gap-4 [&>*]:min-w-0',
            showPendingTiles && showDecidedTiles ? 'lg:grid-cols-4' : 'lg:grid-cols-2',
          )}
        >
          {showPendingTiles && (
            <>
              <StatCard
                label="Waiting on a decision"
                value={String(counts.waiting)}
                icon={ShieldAlert}
                tone={counts.waiting > 0 ? 'risk' : 'ok'}
                source={scope}
                className="rounded-lg"
              />
              <StatCard
                label="Past its deadline"
                value={String(counts.overdue)}
                icon={Clock3}
                tone={counts.overdue > 0 ? 'block' : 'neutral'}
                source={`${scope} · the sweeper decides these if a person does not`}
                className="rounded-lg"
              />
            </>
          )}
          {showDecidedTiles && (
            <>
              <StatCard
                label="Approved"
                value={String(counts.approved)}
                icon={CheckCircle2}
                tone="ok"
                source={scope}
                className="rounded-lg"
              />
              <StatCard
                label="Rejected or expired"
                value={String(counts.rejected + counts.expired)}
                icon={XCircle}
                tone="block"
                source={`${scope} · ${counts.rejected} rejected, ${counts.expired} expired`}
                className="rounded-lg"
              />
            </>
          )}
        </div>
      )}

      {notice && (
        <p
          role="status"
          className={cn(
            'flex items-start gap-2 rounded-lg border px-3 py-2 text-sm',
            notice.kind === 'ok'
              ? 'border-ok/60 bg-ok/10 text-ok-ink'
              : 'border-block/60 bg-block/10 text-block-ink',
          )}
        >
          {notice.kind === 'ok' ? (
            <CheckCircle2 aria-hidden className="mt-0.5 size-4 shrink-0" />
          ) : (
            <XCircle aria-hidden className="mt-0.5 size-4 shrink-0" />
          )}
          <span>
            <span className="font-medium">{notice.kind === 'ok' ? 'Recorded. ' : 'Refused. '}</span>
            {notice.text}
          </span>
        </p>
      )}

      {load.status === 'error' && <ErrorState error={load.message} retry={() => void refresh()} />}

      {load.status === 'loading' && <LoadingState rows={3} label="Reading the queue…" />}

      {load.status === 'ready' && rows.length === 0 && <EmptyQueue filter={filter} />}

      {/* ── Waiting: opened, most urgent first ──────────────────────────────── */}
      {waiting.length > 0 && (
        <section aria-labelledby="approvals-waiting" className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <h2
              id="approvals-waiting"
              className="flex items-center gap-2 text-base font-semibold text-foreground"
            >
              <ShieldAlert className="size-4 text-risk" aria-hidden />
              Waiting on a decision
            </h2>
            <Badge tone="risk">
              <Figure>{waiting.length}</Figure>
            </Badge>
            <InfoTip label="How this list is ordered">
              Ordered by how little of its SLA is left, not by when it arrived. A gate past its
              deadline is decided by the sweeper instead of by a person — HIGH-risk gates are
              auto-rejected — so not deciding is itself a decision.
            </InfoTip>
          </div>
          <ul className="grid gap-3">
            {waiting.map((row) => (
              <li key={row.id}>
                <WaitingGate
                  row={row}
                  now={now}
                  busy={busy === row.id}
                  onDecide={(decision) => void decide(row.id, decision)}
                />
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* ── Decided: dense, and opened only if asked ────────────────────────── */}
      {decided.length > 0 && (
        <DataPanel
          className="rounded-lg"
          eyebrow="aegis.approvals · decided"
          title="Decision history"
          maxHeight={520}
          actions={
            <Badge tone="neutral" className="gap-1.5">
              <Gavel className="size-3" aria-hidden />
              <Figure>{decided.length}</Figure> decided
            </Badge>
          }
        >
          <ul className="divide-y divide-border">
            {decided.map((row) => (
              <DecidedRow key={row.id} row={row} now={now} />
            ))}
          </ul>
        </DataPanel>
      )}
    </div>
  )
}

/** The empty state for each queue — an invitation, not a shrug. */
function EmptyQueue({ filter }: { filter: ApprovalStatusFilter }): ReactElement {
  const body =
    filter === 'pending'
      ? 'When the agent proposes an action above the risk floor your tenant set, it parks here instead of running it — and the run waits until you decide.'
      : filter === 'decided'
        ? 'No gate has been decided in this window. Widen it, or look at what is waiting.'
        : 'No action has reached the human gate in this window. Ask the console for something consequential and one will.'
  const title =
    filter === 'pending'
      ? 'Nothing is waiting on you'
      : filter === 'decided'
        ? 'No decisions in this window'
        : 'No gates in this window'
  return <EmptyState icon={Gavel} title={title} body={body} />
}

// ── the SLA meter ────────────────────────────────────────────────────────────

/**
 * How much of the gate's own deadline has been spent, drawn.
 *
 * The bar is the risk visual the queue turns on: it fills as the window closes, and
 * takes the block hue once the sweeper rather than a person is going to decide. The
 * words beside it say the same thing, because a bar alone is a colour carrying a
 * verdict. A gate with no deadline draws no bar and says so.
 */
function SlaMeter({
  createdAt,
  deadline,
  now,
  className,
}: {
  createdAt: string
  deadline: string | null
  now: number
  className?: string
}): ReactElement {
  const spent = slaSpent(createdAt, deadline, now)
  const left = slaLeft(deadline, now)
  const overdue = isOverdue(deadline, now)
  const urgent = spent != null && spent >= 0.75

  if (spent == null || left == null) {
    return (
      <p className={cn('text-[0.72rem] text-muted-foreground', className)}>
        No SLA deadline recorded on this gate.
      </p>
    )
  }

  const pct = Math.round(spent * 100)
  return (
    <div className={cn('min-w-0', className)}>
      <p className="eyebrow">Time on the clock</p>
      <p
        className={cn(
          'mt-1 flex items-center gap-1.5 text-sm font-semibold',
          overdue ? 'text-block-ink' : urgent ? 'text-risk-ink' : 'text-foreground',
        )}
      >
        <Timer className="size-4 shrink-0" aria-hidden />
        {left}
      </p>
      <div
        className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`${pct}% of the SLA window spent — ${left}`}
      >
        <div
          className="h-full rounded-full transition-[width] duration-[--dur-base] motion-reduce:transition-none"
          style={{
            width: `${Math.max(2, pct)}%`,
            background: overdue ? 'var(--block)' : urgent ? 'var(--risk)' : 'var(--blue-600)',
          }}
        />
      </div>
      <p className="mt-1.5 text-[0.68rem] leading-relaxed text-muted-foreground">
        <Figure>{`${pct}%`}</Figure> spent · deadline <Figure>{formatTime(deadline)}</Figure>
      </p>
    </div>
  )
}

// ── one gate that is waiting ─────────────────────────────────────────────────

/**
 * One parked gate: everything approving would run, how long is left, and the decision.
 *
 * The action list comes from `readApproval` — the same function the live card reads —
 * so a gate authorising three calls counts three here too. `action` alone is the
 * representative, and a queue that rendered it would ask a person to authorise a
 * fan-out while naming one of its writes. The consent sentence and the gate receipt
 * are the live card's own components for the same reason: a second spelling of the
 * sentence that records what a person authorised is a second thing to keep true.
 */
function WaitingGate({
  row,
  now,
  busy,
  onDecide,
}: {
  row: ApprovalInboxRow
  now: number
  busy: boolean
  onDecide: (decision: ApprovalDecision) => void
}): ReactElement {
  const view = readApproval({
    approval_id: row.id,
    action: row.action,
    args: row.args,
    risk: row.risk,
    actions: row.actions,
  })
  const riskSignal = signalForRisk(row.risk)
  const overdue = isOverdue(row.sla_deadline, now)
  const consentId = `gate-consent-${row.id}`
  const persona = personaLabel(row.persona)

  return (
    <article
      className={cn(
        'relative overflow-hidden rounded-lg border bg-surface shadow-card',
        overdue ? 'border-block/60' : 'border-risk/60',
      )}
    >
      {/* The severity rail — the one piece of pure colour, and never alone: the risk
          word, the status badge and the countdown all say it in text as well. */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-1"
        style={{ background: overdue ? 'var(--block)' : 'var(--risk)' }}
      />
      <div className="space-y-3 py-4 pr-4 pl-5">
        <div className="flex flex-wrap items-center gap-2">
          <ShieldAlert
            className={cn('size-4 shrink-0', overdue ? 'text-block' : 'text-risk')}
            aria-hidden
          />
          <p className="font-medium text-foreground">
            {view.many ? (
              <>
                <Figure>{view.actions.length}</Figure> calls await a decision
              </>
            ) : (
              'One call awaits a decision'
            )}
          </p>
          <Badge tone={riskSignal === 'block' ? 'block' : 'risk'} className="uppercase">
            {row.risk} risk
          </Badge>
          <Badge tone="neutral">{ownerLabel(row.tenant_id)}</Badge>
          {persona && <Badge tone="neutral">Raised for {persona}</Badge>}
          <span className="ml-auto text-[0.72rem] text-muted-foreground">
            raised <Figure>{ago(row.created_at, now) ?? formatTime(row.created_at)}</Figure>
          </span>
        </div>

        <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,15rem)]">
          <div className="min-w-0 space-y-2">
            <p className="eyebrow">If approved, this runs</p>
            <ul className="grid gap-2">
              {view.actions.map((action, index) => (
                <ProposedAction
                  key={action.id === '' ? `${action.name}-${index}` : action.id}
                  action={action}
                  showRisk={view.many}
                />
              ))}
            </ul>
          </div>

          <div className="min-w-0 rounded-lg border border-border bg-surface-2/50 p-3">
            <SlaMeter createdAt={row.created_at} deadline={row.sla_deadline} now={now} />
          </div>
        </div>

        {row.rationale && (
          <p className="flex items-start gap-1.5 text-[0.78rem] leading-relaxed text-muted-foreground">
            <Gavel className="mt-0.5 size-3.5 shrink-0" aria-hidden />
            <span>
              <span className="font-medium text-foreground">Why a person: </span>
              {row.rationale}
            </span>
          </p>
        )}

        {/* Load-bearing: the sentence that records what approving authorised. It sits
            directly above the control it describes and is its accessible description. */}
        <ConsentStatement
          id={consentId}
          view={view}
          className="rounded-md border border-risk/40 bg-risk/[0.06] px-3 py-2"
        />

        <div className="flex flex-wrap items-center gap-2">
          <Button
            aria-describedby={consentId}
            disabled={!row.decidable || busy}
            onClick={() => onDecide('approve')}
          >
            {busy ? (
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <CheckCircle2 className="size-4" aria-hidden />
            )}
            {view.many ? `Approve all ${view.actions.length}` : 'Approve'}
          </Button>
          <Button
            variant="outline"
            className="border-block/60 text-block-ink hover:bg-block/10 hover:text-block-ink"
            aria-describedby={consentId}
            disabled={!row.decidable || busy}
            onClick={() => onDecide('reject')}
          >
            <XCircle className="size-4" aria-hidden /> Reject
          </Button>
          {row.blocked_reason && (
            <p className="min-w-0 flex-1 text-[0.78rem] leading-relaxed text-muted-foreground">
              {row.blocked_reason}
            </p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <GateReceipt approvalId={row.id} view={view} />
          <span className="text-[0.68rem] text-muted-foreground">
            run <Figure className="break-all">{row.run_id}</Figure> · raised{' '}
            <Figure>{formatTime(row.created_at)}</Figure>
          </span>
        </div>
      </div>
    </article>
  )
}

// ── one gate that is already history ─────────────────────────────────────────

/**
 * A closed gate as one dense line — status, what ran, who decided, when — that opens
 * into the full record on request.
 *
 * Ninety-five decided gates rendered at the size of a live decision is the reason this
 * screen read as text with no shape. The record is not removed; it is folded. The
 * arguments, the rationale and the gate receipt are all still one click away, and the
 * summary line already carries the four facts an auditor scans for.
 */
function DecidedRow({ row, now }: { row: ApprovalInboxRow; now: number }): ReactElement {
  const [open, setOpen] = useState(false)
  const view = readApproval({
    approval_id: row.id,
    action: row.action,
    args: row.args,
    risk: row.risk,
    actions: row.actions,
  })
  const panelId = `gate-detail-${row.id}`

  return (
    <li className="min-w-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="grid w-full grid-cols-[minmax(0,5.5rem)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-md px-1 py-2.5 text-left transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:bg-surface-2/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none md:grid-cols-[minmax(0,5.5rem)_minmax(0,1fr)_minmax(0,7rem)_minmax(0,9rem)_auto]"
      >
        <Badge tone={statusVariant(row.status)} className="uppercase">
          {row.status}
        </Badge>
        <span className="min-w-0 truncate font-mono text-[0.8rem] text-foreground">
          {row.action}
          {view.many ? (
            <span className="ml-1.5 text-muted-foreground">+{view.actions.length - 1} more</span>
          ) : null}
        </span>
        <span className="hidden min-w-0 truncate text-[0.72rem] text-muted-foreground md:block">
          {ownerLabel(row.tenant_id)}
        </span>
        <span className="hidden min-w-0 truncate text-[0.72rem] text-muted-foreground md:block">
          {row.decided_by ? `by ${row.decided_by}` : 'no decider recorded'} ·{' '}
          {ago(row.decided_at ?? row.created_at, now) ?? '—'}
        </span>
        <ChevronDown
          aria-hidden
          className={cn(
            'size-4 shrink-0 text-muted-foreground transition-transform duration-[--dur-fast] motion-reduce:transition-none',
            open && 'rotate-180',
          )}
        />
      </button>

      {open && (
        <div id={panelId} className="space-y-3 px-1 pt-1 pb-4">
          <ul className="grid gap-2">
            {view.actions.map((action, index) => (
              <ProposedAction
                key={action.id === '' ? `${action.name}-${index}` : action.id}
                action={action}
                showRisk={view.many}
              />
            ))}
          </ul>

          {row.rationale && (
            <p className="text-[0.78rem] leading-relaxed text-muted-foreground">
              <span className="font-medium text-foreground">Why a person: </span>
              {row.rationale}
            </p>
          )}

          <dl className="grid grid-cols-[minmax(0,6rem)_minmax(0,1fr)] gap-x-4 gap-y-1 text-[0.72rem] text-muted-foreground sm:grid-cols-[minmax(0,6rem)_minmax(0,1fr)_minmax(0,6rem)_minmax(0,1fr)]">
            <dt>Raised</dt>
            <dd className="min-w-0">
              <Figure className="text-foreground">{formatTime(row.created_at)}</Figure>
            </dd>
            <dt>Run</dt>
            <dd className="min-w-0">
              <Figure className="break-all text-foreground">{row.run_id}</Figure>
            </dd>
            <dt>Decided</dt>
            <dd className="min-w-0">
              <Figure className="text-foreground">{formatTime(row.decided_at)}</Figure>
            </dd>
            <dt>Decided by</dt>
            <dd className="min-w-0">
              <Figure className="break-all text-foreground">{row.decided_by ?? '—'}</Figure>
            </dd>
          </dl>

          <GateReceipt approvalId={row.id} view={view} />
        </div>
      )}
    </li>
  )
}

/**
 * Client entry for the Approvals section — gated on a reachable backend.
 *
 * The header copy changes with who is reading, because the screen genuinely means
 * three different things: the tenant admin's decisions to make, the platform
 * operator's own gates plus a read-only view of every tenant's, and — for everyone
 * else — the fate of the gates their own questions raised. It is one line on the page
 * and the rest in an `InfoTip`, per DESIGN.md §4: a paragraph explaining a mechanism
 * is a tooltip.
 */
export function ApprovalsMount(): ReactElement {
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  const platform = isPlatformAdmin(session)
  const admin = session?.role === 'admin'
  const note = platform
    ? 'Gates Aegis’s own runs raised are yours to decide.'
    : admin
      ? 'Every action the agent proposed for your tenant and did not take.'
      : 'What happened to the actions your questions asked for.'
  const detail = platform
    ? 'A gate that names a tenant is that tenant’s business decision: you can see it, and the controls say why they are not yours to press. Approving resumes the parked run and runs every call listed; rejecting ends it.'
    : admin
      ? 'Approving resumes the parked run and runs every call listed; rejecting ends it without running any of them. Nothing high-risk executes without this decision.'
      : 'An administrator decides these — this is the record of what they decided, and what is still waiting.'

  return (
    <BackendGate>
      <TooltipProvider>
        <div className="space-y-4">
          <PageHeader
            eyebrow="bounded autonomy"
            title="Approvals"
            note={note}
            actions={
              <Badge tone="neutral" className="gap-1.5">
                <Gavel className="size-3 shrink-0" aria-hidden />
                Human gate
                <InfoTip label="What this screen decides">{detail}</InfoTip>
              </Badge>
            }
          />
          <ApprovalInbox token={session?.token ?? null} canFilterByTenant={platform} />
        </div>
      </TooltipProvider>
    </BackendGate>
  )
}
