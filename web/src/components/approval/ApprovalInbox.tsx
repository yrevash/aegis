'use client'

import {
  CheckCircle2,
  Gavel,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Timer,
  XCircle,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import {
  ConsentStatement,
  GateReceipt,
  ProposedAction,
} from '@/components/approval/ApprovalCard'
import { readApproval } from '@/components/approval/approvalActions'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { Card, CardBody } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { SectionHeader } from '@/components/primitives/SectionHeader'
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
  { id: 'pending', label: 'Waiting on a decision' },
  { id: 'decided', label: 'Already decided' },
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

/** Who a gate belongs to, said plainly. */
function ownerLabel(tenantId: number | null): string {
  return tenantId === null ? 'Aegis · no tenant' : `Tenant #${tenantId}`
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
 * row and a checkpoint that no screen in the product could reach. That is what made
 * moving gate ownership to the tenant admin impossible to ship on its own — the
 * capability would have moved into nowhere.
 *
 * **Who sees what is the server's answer, not this component's.** Every row arrives
 * with `decidable` and, when it is false, the `blocked_reason` the decision endpoint
 * would give — so a platform operator looking at a tenant's gate sees the controls,
 * sees them disabled, and reads why. Hiding them would hide the rule; enabling them
 * would earn a 403. Deriving the rule here in TypeScript would be a second copy of it
 * that can drift from the one the button is about to hit.
 *
 * **A failure here is a failure of the queue, not of the backend.** It used to render
 * `BackendUnavailable`, which says the server is down — the sentence a 403 on one
 * tenant's queue least deserves. The server's own sentence goes on the screen instead,
 * with the retry beside it.
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
  const [notice, setNotice] = useState<{ kind: 'ok' | 'refused'; text: string } | null>(null)
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

  const rows = useMemo(
    () => (load.status === 'ready' ? load.rows : []),
    [load],
  )

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

  const waiting = rows.filter((row) => row.status === 'pending').length

  return (
    <div className="flex flex-col gap-4">
      {/* Controls: which queue, how far back, whose. */}
      <Card className="rounded-lg">
        <CardBody className="flex flex-wrap items-end gap-4 py-4">
          <fieldset className="min-w-0">
            <legend className="eyebrow mb-2">Queue</legend>
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
            <label htmlFor="approvals-window" className="eyebrow mb-2 block">
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
              <label htmlFor="approvals-tenant" className="eyebrow mb-2 block">
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

          <div className="flex items-center gap-3 sm:ml-auto">
            <p className="text-sm text-muted-foreground">
              {filter === 'decided' ? (
                <>
                  <Figure>{rows.length}</Figure> decided in this window
                </>
              ) : waiting === 0 ? (
                'Nothing is waiting on a decision'
              ) : (
                <>
                  <Figure>{waiting}</Figure> waiting on a decision
                </>
              )}
            </p>
            <Button type="button" size="sm" variant="outline" onClick={() => void refresh()}>
              <RefreshCw className="size-4" aria-hidden /> Refresh
            </Button>
          </div>
        </CardBody>
      </Card>

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

      {load.status === 'error' && (
        <ErrorState error={load.message} retry={() => void refresh()} />
      )}

      {load.status === 'loading' && <LoadingState rows={3} label="Reading the queue…" />}

      {load.status === 'ready' && rows.length === 0 && <EmptyQueue filter={filter} />}

      <ul className="grid gap-3">
        {rows.map((row) => (
          <li key={row.id}>
            <GateRow
              row={row}
              now={now}
              busy={busy === row.id}
              onDecide={(decision) => void decide(row.id, decision)}
            />
          </li>
        ))}
      </ul>
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

/**
 * One parked gate: everything approving would run, why it stopped, and the decision.
 *
 * The action list comes from `readApproval` — the same function the live card reads —
 * so a gate authorising three calls counts three here too. `action` alone is the
 * representative, and a queue that rendered it would ask a person to authorise a
 * fan-out while naming one of its writes. The consent sentence and the gate receipt
 * are the live card's own components for the same reason.
 */
function GateRow({
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
  const pending = row.status === 'pending'
  const left = pending ? slaLeft(row.sla_deadline, now) : null
  const consentId = `gate-consent-${row.id}`

  return (
    <Card className={cn('rounded-lg', pending && 'border-risk/40')}>
      <CardBody className="space-y-3 py-4">
        <div className="flex flex-wrap items-center gap-2">
          {pending ? (
            <ShieldAlert className="size-4 shrink-0 text-risk" aria-hidden />
          ) : (
            <CheckCircle2 className="size-4 shrink-0 text-muted-foreground" aria-hidden />
          )}
          <p className="font-medium text-foreground">
            {view.many ? (
              <>
                <Figure>{view.actions.length}</Figure> calls await a decision
              </>
            ) : (
              'One call awaits a decision'
            )}
          </p>
          <Badge tone={statusVariant(row.status)} className="uppercase">
            {row.status}
          </Badge>
          <Badge tone={riskSignal === 'block' ? 'block' : 'risk'} className="uppercase">
            {row.risk} risk
          </Badge>
          <Badge tone="neutral">{ownerLabel(row.tenant_id)}</Badge>
          {left && (
            <span className="inline-flex items-center gap-1 text-[0.72rem] text-muted-foreground">
              <Timer className="size-3.5" aria-hidden /> {left}
            </span>
          )}
        </div>

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
          <div>
            <p className="eyebrow mb-1">Why this needs a person</p>
            <p className="text-[0.8rem] leading-relaxed text-muted-foreground">{row.rationale}</p>
          </div>
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
          {CLOSED.has(row.status) && (
            <>
              <dt>Decided</dt>
              <dd className="min-w-0">
                <Figure className="text-foreground">{formatTime(row.decided_at)}</Figure>
              </dd>
              <dt>Decided by</dt>
              <dd className="min-w-0">
                <Figure className="break-all text-foreground">{row.decided_by ?? '—'}</Figure>
              </dd>
            </>
          )}
        </dl>

        {pending && <ConsentStatement id={consentId} view={view} />}

        <div className="flex flex-wrap items-center gap-2 pt-1">
          <Button
            aria-describedby={pending ? consentId : undefined}
            disabled={!row.decidable || busy}
            onClick={() => onDecide('approve')}
          >
            {busy ? <Loader2 className="size-4 animate-spin" aria-hidden /> : null}
            {view.many ? `Approve all ${view.actions.length}` : 'Approve'}
          </Button>
          <Button
            variant="outline"
            className="border-block/60 text-block-ink hover:bg-block/10 hover:text-block-ink"
            aria-describedby={pending ? consentId : undefined}
            disabled={!row.decidable || busy}
            onClick={() => onDecide('reject')}
          >
            Reject
          </Button>
          {row.blocked_reason && (
            <p className="min-w-0 flex-1 text-[0.78rem] leading-relaxed text-muted-foreground">
              {row.blocked_reason}
            </p>
          )}
        </div>

        <GateReceipt approvalId={row.id} view={view} />
      </CardBody>
    </Card>
  )
}

/**
 * Client entry for the Approvals section — gated on a reachable backend.
 *
 * The header copy changes with who is reading, because the screen genuinely means
 * three different things: the tenant admin's decisions to make, the platform
 * operator's own gates plus a read-only view of every tenant's, and — for everyone
 * else — the fate of the gates their own questions raised.
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
  const blurb = platform
    ? 'Gates Aegis’s own runs raised are yours to decide. A gate that names a tenant is that tenant’s business decision: you can see it, and the controls say why they are not yours to press.'
    : admin
      ? 'Every action the agent proposed for your tenant and did not take. Approving resumes the parked run and runs every call listed; rejecting ends it. Nothing high-risk executes without this decision.'
      : 'What happened to the actions your questions asked for. An administrator decides these — this is the record of what they decided, and what is still waiting.'

  return (
    <BackendGate>
      <div className="space-y-4">
        <SectionHeader as="h1" eyebrow="Bounded autonomy" title="Approvals" note={blurb} />
        <ApprovalInbox token={session?.token ?? null} canFilterByTenant={platform} />
      </div>
    </BackendGate>
  )
}
