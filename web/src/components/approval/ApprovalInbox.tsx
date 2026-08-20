'use client'

import {
  AlertTriangle,
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
import { Card } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
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

/** One spelling of the console's native select, so three of them agree. */
const SELECT =
  'h-8 rounded-md border border-border bg-surface px-2 text-sm text-foreground outline-none transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:border-input focus-visible:ring-2 focus-visible:ring-ring'

/** The signal colour a lifecycle status carries. */
function statusVariant(status: string): 'ok' | 'block' | 'risk' | 'agent' | 'neutral' {
  if (status === 'approved') return 'ok'
  if (status === 'rejected') return 'block'
  if (status === 'expired') return 'risk'
  if (status === 'pending' || status === 'resuming') return 'agent'
  return 'neutral'
}

/** The icon that ships with a status word, so the tone is never alone. */
function statusIcon(status: string): typeof CheckCircle2 {
  if (status === 'approved') return CheckCircle2
  if (status === 'rejected') return XCircle
  if (status === 'expired') return Clock3
  return ShieldAlert
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

/** The fraction of the SLA window at which a gate stops being routine. */
const URGENT_AT = 0.75

/** Which urgency band a waiting gate is in — the ladder on the board, and the rail's hue. */
type Urgency = 'overdue' | 'soon' | 'ontrack' | 'unbounded'

function urgencyOf(row: ApprovalInboxRow, now: number): Urgency {
  if (isOverdue(row.sla_deadline, now)) return 'overdue'
  const spent = slaSpent(row.created_at, row.sla_deadline, now)
  if (spent == null) return 'unbounded'
  return spent >= URGENT_AT ? 'soon' : 'ontrack'
}

/** How each band reads: a word, an icon and a fill — never the fill alone. */
const URGENCY: Record<Urgency, { word: string; icon: typeof Clock3; fill: string; ink: string }> = {
  overdue: { word: 'Past deadline', icon: AlertTriangle, fill: 'var(--block)', ink: 'text-block-ink' },
  soon: { word: 'Under a quarter left', icon: Timer, fill: 'var(--risk)', ink: 'text-risk-ink' },
  ontrack: { word: 'On track', icon: Clock3, fill: 'var(--blue-600)', ink: 'text-muted-foreground' },
  unbounded: { word: 'No deadline recorded', icon: Clock3, fill: 'var(--blue-200)', ink: 'text-muted-foreground' },
}

/** The order the ladder reads in — worst first, because that is what is scanned for. */
const URGENCY_ORDER: Urgency[] = ['overdue', 'soon', 'ontrack', 'unbounded']

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
 * decided is a dense row that opens only if asked.
 *
 * **The board above both lists is one instrument, not five boxes.** It used to be a
 * bordered control strip followed by four equal stat cards, which is the "stacked
 * boxes" reading the screen was criticised for: five surfaces of identical weight,
 * none of them the subject. One panel now carries the controls, the waiting figure,
 * the **urgency ladder** that says how much of the queue is actually urgent, and the
 * outcome split of everything already decided. Same facts, one hierarchy.
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
  const { waiting, decided, counts, ladder } = useMemo(() => {
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
    const bands: Record<Urgency, number> = { overdue: 0, soon: 0, ontrack: 0, unbounded: 0 }
    for (const row of pend) bands[urgencyOf(row, now)] += 1
    return {
      waiting: [...pend].sort((a, b) => deadline(a) - deadline(b)),
      decided: [...done].sort((a, b) => decidedAt(b) - decidedAt(a)),
      ladder: bands,
      counts: {
        waiting: pend.length,
        overdue: bands.overdue,
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
  // A half of the board only appears when the loaded set could contain what it counts.
  // On the Decided queue the server sends no pending rows, so a "Waiting: 0" figure
  // would be counting rows it was never given — and five gates really are waiting one
  // tab away. An absence of data is not a zero, and it is said in one line.
  const showWaiting = filter !== 'decided'
  const showDecided = filter !== 'pending'

  return (
    <div className="flex flex-col gap-5">
      <QueueBoard
        filter={filter}
        onFilter={setFilter}
        lookback={lookback}
        onLookback={setLookback}
        tenant={tenant}
        onTenant={setTenant}
        knownTenants={canFilterByTenant ? knownTenants : null}
        onRefresh={() => void refresh()}
        busy={load.status === 'loading'}
        ready={load.status === 'ready'}
        showWaiting={showWaiting}
        showDecided={showDecided}
        counts={counts}
        ladder={ladder}
        scope={scope}
      />

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
          <ul className="grid gap-4">
            {waiting.map((row, index) => (
              <li
                key={row.id}
                className="animate-section"
                style={{ animationDelay: `${Math.min(index, 6) * 55}ms` }}
              >
                <WaitingGate
                  row={row}
                  rank={index + 1}
                  total={waiting.length}
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

// ── the board above both lists ───────────────────────────────────────────────

/** What the four counts are, once the query has answered. */
interface QueueCounts {
  waiting: number
  overdue: number
  approved: number
  rejected: number
  expired: number
}

/**
 * The queue's whole state, and its controls, as one instrument.
 *
 * The controls sit in the panel's own toolbar because they decide what every figure
 * below them counts; splitting them into a separate bordered strip made two surfaces
 * out of one thought. The waiting figure is the screen's single display numeral
 * (DESIGN.md §3) — a second one would be a hierarchy failure rather than emphasis.
 *
 * The **urgency ladder** is the risk visual the board turns on. A bare "5 waiting"
 * says nothing about whether any of them is close to deciding itself; five bars, worst
 * band first, each with its icon, its word and its count, says it at a glance and
 * still reads with the colour removed.
 */
function QueueBoard({
  filter,
  onFilter,
  lookback,
  onLookback,
  tenant,
  onTenant,
  knownTenants,
  onRefresh,
  busy,
  ready,
  showWaiting,
  showDecided,
  counts,
  ladder,
  scope,
}: {
  filter: ApprovalStatusFilter
  onFilter: (next: ApprovalStatusFilter) => void
  lookback: string
  onLookback: (next: string) => void
  tenant: string
  onTenant: (next: string) => void
  /** Tenants this operator may target, or `null` when they may not target one. */
  knownTenants: number[] | null
  onRefresh: () => void
  busy: boolean
  ready: boolean
  showWaiting: boolean
  showDecided: boolean
  counts: QueueCounts
  ladder: Record<Urgency, number>
  scope: string
}): ReactElement {
  const decidedTotal = counts.approved + counts.rejected + counts.expired
  const bands = URGENCY_ORDER.filter(
    (band) => ladder[band] > 0 || band === 'overdue' || band === 'ontrack',
  )
  const ladderMax = Math.max(1, ...URGENCY_ORDER.map((band) => ladder[band]))

  const outcomes: { key: string; word: string; n: number; fill: string; icon: typeof CheckCircle2 }[] =
    [
      { key: 'approved', word: 'Approved', n: counts.approved, fill: 'var(--ok)', icon: CheckCircle2 },
      { key: 'rejected', word: 'Rejected', n: counts.rejected, fill: 'var(--block)', icon: XCircle },
      { key: 'expired', word: 'Expired', n: counts.expired, fill: 'var(--risk)', icon: Clock3 },
    ]

  return (
    <Card className="overflow-hidden shadow-card">
      {/* ── the controls, which decide what everything below them counts ────── */}
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3 border-b border-border bg-surface-2/60 px-4 py-3 md:px-5">
        <div
          role="group"
          aria-label="Which queue"
          className="flex w-full rounded-lg border border-border bg-surface p-0.5 sm:inline-flex sm:w-auto"
        >
          {FILTERS.map((option) => {
            const active = filter === option.id
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={active}
                onClick={() => onFilter(option.id)}
                className={cn(
                  'flex-1 touch-manipulation rounded-md px-3 py-1 text-sm font-medium transition-colors duration-[--dur-fast] outline-none motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-ring sm:flex-none',
                  active
                    ? 'bg-blue-600 text-white'
                    : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
                )}
              >
                {option.label}
              </button>
            )
          })}
        </div>

        {/* `min-w-0` here let the two chooser columns collapse to the width of their
            own chevrons at 390px, and the two labels above them printed on top of one
            another. A select needs a floor, not a zero. */}
        <div className="min-w-[9rem] flex-1 sm:flex-none">
          <label htmlFor="approvals-window" className="eyebrow mb-1 block">
            Raised
          </label>
          <select
            id="approvals-window"
            value={lookback}
            onChange={(event) => onLookback(event.target.value)}
            className={cn(SELECT, 'w-full sm:w-auto')}
          >
            {WINDOWS.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {knownTenants !== null && (
          <div className="min-w-[9rem] flex-1 sm:flex-none">
            <label htmlFor="approvals-tenant" className="eyebrow mb-1 block">
              Whose gate
            </label>
            <select
              id="approvals-tenant"
              value={tenant}
              onChange={(event) => onTenant(event.target.value)}
              className={cn(SELECT, 'w-full sm:w-auto')}
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

        <Button
          type="button"
          size="sm"
          variant="outline"
          className="w-full sm:ml-auto sm:w-auto"
          onClick={onRefresh}
        >
          {busy ? (
            <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
          ) : (
            <RefreshCw className="size-4" aria-hidden />
          )}
          Refresh
        </Button>
      </div>

      {/* ── what the queue holds, before either list is read ────────────────── */}
      <div className="grid gap-6 px-4 py-5 md:px-5 lg:grid-cols-2 lg:gap-0 lg:divide-x lg:divide-border [&>*]:min-w-0">
        <div className="lg:pr-8">
          <p className="eyebrow">Waiting on a decision</p>
          {showWaiting ? (
            <>
              <div className="mt-1 flex items-end gap-2.5">
                <Figure
                  size="display"
                  className={counts.waiting > 0 ? 'text-risk-ink' : 'text-foreground'}
                >
                  {ready ? String(counts.waiting) : '—'}
                </Figure>
                <span className="pb-1 text-sm text-muted-foreground">
                  {counts.waiting === 1 ? 'gate is parked' : 'gates are parked'}
                </span>
              </div>
              <ul className="mt-4 max-w-[26rem] space-y-2">
                {bands.map((band) => {
                  const meta = URGENCY[band]
                  const Icon = meta.icon
                  const n = ladder[band]
                  return (
                    <li
                      key={band}
                      className="grid grid-cols-[minmax(0,9rem)_minmax(0,1fr)_auto] items-center gap-x-3"
                    >
                      <span
                        className={cn(
                          'flex min-w-0 items-center gap-1.5 text-[0.72rem]',
                          n > 0 ? meta.ink : 'text-muted-foreground',
                        )}
                      >
                        <Icon className="size-3.5 shrink-0" aria-hidden />
                        <span className="truncate">{meta.word}</span>
                      </span>
                      <span className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                        <span
                          className="block h-full rounded-full transition-[width] duration-[--dur-base] motion-reduce:transition-none"
                          style={{
                            width: `${n === 0 ? 0 : Math.max(6, (n / ladderMax) * 100)}%`,
                            background: meta.fill,
                          }}
                        />
                      </span>
                      <Figure className="w-6 text-right text-xs text-foreground">{n}</Figure>
                    </li>
                  )
                })}
              </ul>
            </>
          ) : (
            <NotInThisQueue what="This query asked the server for decided gates only, so nothing here counts what is still waiting. The Waiting tab loads them." />
          )}
        </div>

        <div className="lg:pl-8">
          <p className="eyebrow">Already decided</p>
          {showDecided ? (
            <>
              <div className="mt-1 flex items-end gap-2.5">
                <Figure size="stat" className="text-foreground">
                  {ready ? String(decidedTotal) : '—'}
                </Figure>
                <span className="pb-0.5 text-sm text-muted-foreground">
                  {decidedTotal === 1 ? 'gate is closed' : 'gates are closed'}
                </span>
              </div>
              <div
                className="mt-3 flex h-2.5 w-full max-w-[26rem] overflow-hidden rounded-full bg-surface-2"
                role="img"
                aria-label={outcomes.map((o) => `${o.n} ${o.word.toLowerCase()}`).join(', ')}
              >
                {outcomes.map((o) =>
                  o.n === 0 ? null : (
                    <span
                      key={o.key}
                      className="h-full transition-[width] duration-[--dur-base] motion-reduce:transition-none"
                      style={{
                        width: `${decidedTotal > 0 ? (o.n / decidedTotal) * 100 : 0}%`,
                        background: o.fill,
                      }}
                    />
                  ),
                )}
              </div>
              <ul className="mt-3 grid max-w-[26rem] grid-cols-3 gap-3">
                {outcomes.map((o) => {
                  const Icon = o.icon
                  return (
                    <li key={o.key} className="min-w-0">
                      <span className="flex min-w-0 items-center gap-1.5 text-[0.72rem] text-muted-foreground">
                        <span
                          aria-hidden
                          className="size-2 shrink-0 rounded-full"
                          style={{ background: o.fill }}
                        />
                        <Icon className="size-3 shrink-0" aria-hidden />
                        <span className="truncate">{o.word}</span>
                      </span>
                      <Figure className="mt-0.5 block text-sm text-foreground">{o.n}</Figure>
                    </li>
                  )
                })}
              </ul>
            </>
          ) : (
            <NotInThisQueue what="This query asked the server for waiting gates only, so nothing here counts what has already been decided. The Decided tab loads them." />
          )}
        </div>
      </div>

      <div className="border-t border-border px-4 py-2.5 md:px-5">
        <Receipt
          variant="inline"
          origin={scope}
          detail="every figure above counts the rows this query loaded, and nothing outside it"
        />
      </div>
    </Card>
  )
}

/**
 * The half of the board this query did not load, as one line.
 *
 * Zero is a claim. A queue that asked the server only for decided gates has not been
 * told that nothing is waiting — it has been told nothing at all, and printing `0`
 * there would be the dashboard inventing a fact in the reader's favour.
 */
function NotInThisQueue({ what }: { what: string }): ReactElement {
  return (
    <p className="mt-2 max-w-prose text-xs leading-relaxed text-muted-foreground">
      <span className="font-medium text-foreground">Not counted in this queue. </span>
      {what}
    </p>
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
 * How much of the gate's own deadline has been spent, drawn full-width.
 *
 * The rail is the one strong risk visual on a gate, so it spans the card rather than
 * hiding in a side box: five stacked gates then read as five stacked meters a person
 * can compare down the column without reading a word. It fills as the window closes,
 * takes the risk hue past three-quarters and the block hue once the sweeper rather
 * than a person is going to decide, and carries a tick at the threshold where it
 * changes. The words beside it say the same thing, because a bar alone is a colour
 * carrying a verdict. A gate with no deadline draws no bar and says so.
 */
function SlaRail({
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
  const urgent = spent != null && spent >= URGENT_AT

  if (spent == null || left == null) {
    return (
      <p
        className={cn(
          'flex items-center gap-1.5 rounded-md border border-border bg-surface-2/60 px-3 py-2 text-[0.72rem] text-muted-foreground',
          className,
        )}
      >
        <Clock3 className="size-3.5 shrink-0" aria-hidden />
        No SLA deadline is recorded on this gate, so there is no window to draw.
      </p>
    )
  }

  const pct = Math.round(spent * 100)
  const fill = overdue ? 'var(--block)' : urgent ? 'var(--risk)' : 'var(--blue-600)'

  return (
    <div className={cn('min-w-0', className)}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <p
          className={cn(
            'flex items-center gap-1.5 text-sm font-semibold',
            overdue ? 'text-block-ink' : urgent ? 'text-risk-ink' : 'text-foreground',
          )}
        >
          {overdue ? (
            <AlertTriangle className="size-4 shrink-0" aria-hidden />
          ) : (
            <Timer className="size-4 shrink-0" aria-hidden />
          )}
          {left}
        </p>
        <p className="text-[0.72rem] text-muted-foreground">
          <Figure>{`${pct}%`}</Figure> of the window spent · act by{' '}
          <Figure>{formatTime(deadline)}</Figure>
        </p>
      </div>
      <div
        className="relative mt-2 h-2 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`${pct}% of the SLA window spent — ${left}`}
      >
        <div
          className="h-full rounded-full transition-[width] duration-[--dur-base] motion-reduce:transition-none"
          style={{ width: `${Math.max(2, pct)}%`, background: fill }}
        />
        {/* the threshold at which the rail changes hue and the row changes band */}
        <span
          aria-hidden
          className="absolute inset-y-0 w-px bg-foreground/30"
          style={{ left: `${URGENT_AT * 100}%` }}
        />
      </div>
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
 *
 * **Evidence on the left, the decision on the right.** The card used to be six full
 * width bands stacked down a 1150px column, every one of them tinted the same amber:
 * five of those side by side read as a wall, not a queue, and nothing in it was louder
 * than anything else. Now a thin tinted head carries the identity of what would run,
 * the body splits at `lg` into what the gate *is* (the calls, the rationale) and what
 * you *do about it* (the SLA meter, the consent sentence, the two buttons) in a rail
 * with its own ground. Colour is spent once — the spine and the head — instead of on
 * every surface, so the risk hue still means something when it appears.
 *
 * The consent sentence stays inside the rail and still *encloses the two buttons it
 * describes*: the record of what a human authorised and the thing they pressed are one
 * object, at one weight, in one box.
 */
function WaitingGate({
  row,
  rank,
  total,
  now,
  busy,
  onDecide,
}: {
  row: ApprovalInboxRow
  /** Position in the urgency order, so the queue's ranking is visible not implied. */
  rank: number
  total: number
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
  const headline = view.actions[0]?.name ?? row.action

  return (
    <article
      className={cn(
        'relative overflow-hidden rounded-lg border border-border bg-surface shadow-card transition-shadow duration-[--dur-fast] motion-reduce:transition-none hover:shadow-hover',
        overdue && 'border-block/50',
      )}
    >
      {/* The severity spine — the one piece of pure colour, and never alone: the risk
          word, the countdown and the band on the board all say it in text as well. */}
      <span
        aria-hidden
        className="absolute inset-y-0 left-0 w-1"
        style={{ background: overdue ? 'var(--block)' : 'var(--risk)' }}
      />

      {/* ── head: what would run, and whose it is ─────────────────────────── */}
      <header
        className={cn(
          'flex flex-wrap items-center gap-x-3 gap-y-2 border-b py-3 pr-4 pl-5 md:pl-6',
          overdue ? 'border-block/30 bg-block/[0.06]' : 'border-risk/30 bg-risk/[0.07]',
        )}
      >
        <span
          className={cn(
            'grid size-8 shrink-0 place-items-center rounded-md',
            overdue ? 'bg-block/25 text-block-ink' : 'bg-risk/30 text-risk-ink',
          )}
        >
          <ShieldAlert className="size-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1 basis-[14rem]">
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <Figure className="text-[0.9rem] font-semibold break-words text-foreground">
              {headline}
            </Figure>
            {view.many && (
              <span className="text-[0.72rem] text-muted-foreground">
                and <Figure>{view.actions.length - 1}</Figure> more in the same gate
              </span>
            )}
          </p>
          <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.72rem] text-muted-foreground">
            <span>{ownerLabel(row.tenant_id)}</span>
            {persona && (
              <>
                <span aria-hidden>·</span>
                <span>raised for {persona}</span>
              </>
            )}
            <span aria-hidden>·</span>
            <span>
              raised <Figure>{ago(row.created_at, now) ?? formatTime(row.created_at)}</Figure>
            </span>
          </p>
        </div>
        <span className="flex shrink-0 items-center gap-2">
          <Badge tone={riskSignal === 'block' ? 'block' : 'risk'} className="gap-1 uppercase">
            <ShieldAlert className="size-3 shrink-0" aria-hidden />
            {row.risk} risk
          </Badge>
          <span className="rounded-md border border-border bg-surface px-2 py-0.5 text-[0.68rem] whitespace-nowrap text-muted-foreground">
            <Figure aria-label={`gate ${rank} of ${total} by urgency`}>
              {`${rank}/${total}`}
            </Figure>{' '}
            by urgency
          </span>
        </span>
      </header>

      {/*
        Two halves at `lg`: what the gate is, and what to do about it. Below that the
        rail simply follows the evidence, which is the same reading order.
      */}
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(21rem,26rem)] [&>*]:min-w-0">
        {/* ── the evidence ─────────────────────────────────────────────────── */}
        <div className="flex flex-col gap-4 py-4 pr-4 pl-5 md:pl-6">
          <div className="min-w-0">
            <p className="eyebrow mb-1.5">
              If approved, this runs
              {view.many ? (
                <>
                  {' · '}
                  <Figure>{view.actions.length}</Figure> calls
                </>
              ) : null}
            </p>
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

          {row.rationale && (
            <p className="flex items-start gap-1.5 text-[0.78rem] leading-relaxed text-muted-foreground">
              <Gavel className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              <span>
                <span className="font-medium text-foreground">Why a person: </span>
                {row.rationale}
              </span>
            </p>
          )}

          {/*
            Deliberately *not* `mt-auto`. The rail is the taller column once the server
            has a refusal to state, and pinning this line to the card's floor to square
            the two bottom edges only moves the slack into the middle of the evidence —
            a hole between the rationale and its provenance reads as something failed to
            render, where trailing space under a finished column reads as a finished
            column.
          */}
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1">
            <GateReceipt approvalId={row.id} view={view} variant="inline" />
            <span className="text-[0.68rem] text-muted-foreground">
              run <Figure className="break-all">{row.run_id}</Figure>
            </span>
          </div>
        </div>

        {/* ── the decision rail ────────────────────────────────────────────── */}
        <div className="space-y-4 border-t border-border bg-surface-2/50 px-5 py-4 md:px-6 lg:border-t-0 lg:border-l">
          <SlaRail createdAt={row.created_at} deadline={row.sla_deadline} now={now} />

          {/* Load-bearing: the sentence that records what approving authorised. It
              encloses the controls it describes and is their accessible description. */}
          <div
            className={cn(
              'rounded-md border bg-surface p-3',
              overdue ? 'border-block/40' : 'border-risk/40',
            )}
          >
            <p className="flex items-center gap-1.5 font-mono text-[0.66rem] font-medium tracking-[0.16em] text-risk-ink uppercase">
              <Gavel className="size-3.5 shrink-0" aria-hidden />
              What approving authorises
            </p>
            <ConsentStatement id={consentId} view={view} className="mt-1.5 text-[0.82rem]" />
            <div className="mt-3 grid gap-2 sm:grid-cols-2">
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
                className="border-block/60 bg-surface text-block-ink hover:bg-block/10 hover:text-block-ink"
                aria-describedby={consentId}
                disabled={!row.decidable || busy}
                onClick={() => onDecide('reject')}
              >
                <XCircle className="size-4" aria-hidden /> Reject
              </Button>
            </div>
            {/* The server's own rule, on its own line. Squeezed into the button row it
                had ~40px of column at 390px and read one word to a line. */}
            {row.blocked_reason && (
              <p className="mt-3 flex items-start gap-1.5 border-t border-border pt-3 text-[0.75rem] leading-relaxed text-muted-foreground">
                <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden />
                <span>{row.blocked_reason}</span>
              </p>
            )}
          </div>
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
  const StatusIcon = statusIcon(row.status)

  return (
    <li className="min-w-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((v) => !v)}
        className="grid w-full grid-cols-[minmax(0,6.5rem)_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 rounded-md px-1 py-2.5 text-left transition-colors duration-[--dur-fast] motion-reduce:transition-none hover:bg-surface-2/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none md:grid-cols-[minmax(0,6.5rem)_minmax(0,1fr)_minmax(0,7rem)_minmax(0,9rem)_auto]"
      >
        <Badge tone={statusVariant(row.status)} className="gap-1 uppercase">
          <StatusIcon className="size-3 shrink-0" aria-hidden />
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

          <dl className="grid grid-cols-[minmax(0,6rem)_minmax(0,1fr)] gap-x-4 gap-y-1 rounded-md bg-surface-2/60 p-3 text-[0.72rem] text-muted-foreground sm:grid-cols-[minmax(0,6rem)_minmax(0,1fr)_minmax(0,6rem)_minmax(0,1fr)]">
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
      <div className="space-y-5">
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
    </BackendGate>
  )
}
