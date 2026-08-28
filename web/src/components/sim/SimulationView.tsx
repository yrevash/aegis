'use client'

import { Check, GitCompare, Minus, Play, RotateCcw, Shield, ShieldCheck, UserCog, UserRound, X } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'
import { useState } from 'react'

import { ApprovalCard } from '@/components/approval/ApprovalCard'
import { NodeGantt } from '@/components/charts/NodeGantt'
import { RankedBars } from '@/components/charts/RankedBars'
import { AnswerPanel } from '@/components/console/AnswerPanel'
import { formatUsd } from '@/components/dashboard/roi'
import { Scene } from '@/components/illustration/Scene'
import { CapabilityMap, ComparisonCard, CountUp, RevealOnScroll, type Capability } from '@/components/shared'
import { AgentTracePanel } from '@/components/trace/AgentTracePanel'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Figure } from '@/components/primitives/Figure'
import { PageHeader } from '@/components/primitives/PageHeader'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'
import { useRunStream } from '@/state/useRunStream'
import type { ApprovalDecision } from '@/lib/api/types'

import { gateStatus, isSettled, rankedDivergence, toolMark, type Mark } from './simLogic'

/**
 * The single query both roles run at once. It is phrased to require a status
 * change — the one HIGH-risk tool — so the operations lead hits the human gate
 * while the client is refused it. The divergence comes from *who is asking*, not
 * from the text, and the query names no record id: the adapter generates those at
 * seed time, so a literal id would name a request that does not exist.
 */
const SIM_QUERY = 'Close out my oldest open request and record why it was closed'

/**
 * Counts, durations and reranker scores through module-level `Intl`, never a
 * per-row `toLocaleString()` — an unqualified one resolves to the *runtime's*
 * locale, which is the server's during SSR and the browser's after hydration.
 */
const COUNT = new Intl.NumberFormat('en-US')
const SCORE = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 3,
  maximumFractionDigits: 3,
})

/** Wall-clock milliseconds a lane's finished nodes reported, summed. */
function nodeMs(state: RunState): number {
  return state.nodeLedger.reduce((sum, node) => sum + node.duration_ms, 0)
}

/** A comparison cell: a small ✓ / ✗ / ● / — marker paired with a short label. */
const MARK: Record<Mark, { tone: string; render: (key: string) => ReactNode }> = {
  allow: { tone: 'text-ok-ink', render: (k) => <Check key={k} aria-hidden className="size-3.5 shrink-0" /> },
  deny: { tone: 'text-block-ink', render: (k) => <X key={k} aria-hidden className="size-3.5 shrink-0" /> },
  gate: {
    tone: 'text-risk-ink',
    render: (k) => <span key={k} aria-hidden className="mt-1 size-2 shrink-0 rounded-full bg-risk" />,
  },
  none: { tone: 'text-muted-foreground', render: (k) => <Minus key={k} aria-hidden className="size-3.5 shrink-0" /> },
}

function Cell({ mark, children }: { mark: Mark; children: ReactNode }): ReactElement {
  return (
    <span className={cn('flex items-start gap-1.5 font-medium', MARK[mark].tone)}>
      {MARK[mark].render('m')}
      <span className="text-foreground">{children}</span>
    </span>
  )
}

/** Cost read-out for a lane: counts up once the run reports usage. */
function CostCell({ state }: { state: RunState }): ReactElement {
  if (state.usage) {
    return (
      <span className="tabular font-mono text-[0.82rem] font-medium text-foreground">
        <CountUp value={state.usage.cost_usd} format={(n) => formatUsd(n, 4)} />
      </span>
    )
  }
  return <span className="font-mono text-[0.82rem] text-muted-foreground">{state.running ? '···' : '—'}</span>
}

/** Summed node time for a lane, or the reason there is not one yet. */
function NodeTimeCell({ state }: { state: RunState }): ReactElement {
  if (state.nodeLedger.length === 0) {
    return <span className="font-mono text-[0.82rem] text-muted-foreground">{state.running ? '···' : '—'}</span>
  }
  return (
    <Figure className="text-[0.82rem] font-medium" unit="ms">
      {COUNT.format(Math.round(nodeMs(state)))}
    </Figure>
  )
}

interface LaneProps {
  title: string
  subtitle: string
  /** Honest role identifier surfaced in an InfoTip (kept out of the primary label). */
  roleId: string
  icon: typeof UserCog
  accent: 'agent' | 'graph'
  scopeIcon: typeof ShieldCheck
  scopeLabel: string
  state: RunState
  /** Rendered when this lane pauses at the human gate. */
  onDecision?: (decision: ApprovalDecision) => void
  decided?: boolean
}

/** The role chip that heads a lane, and heads its column in the two chart cards. */
function LaneChip({
  title,
  roleId,
  icon: Icon,
  accent,
}: Pick<LaneProps, 'title' | 'roleId' | 'accent'> & { icon: typeof UserCog }): ReactElement {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span
        className={cn(
          'grid size-6 shrink-0 place-items-center rounded-md',
          accent === 'agent' ? 'bg-blue-200/15' : 'bg-blue-400/15',
        )}
      >
        <Icon aria-hidden className={cn('size-3.5', accent === 'agent' ? 'text-blue-700' : 'text-blue-600')} />
      </span>
      <span className="t-label min-w-0 truncate text-foreground">{title}</span>
      <code className="hidden font-mono text-[0.6875rem] text-muted-foreground sm:inline" translate="no">
        {roleId}
      </code>
    </span>
  )
}

/** One role's live trajectory — trace, gate (if any) and streamed answer. */
function Lane({
  title,
  subtitle,
  roleId,
  icon: Icon,
  accent,
  scopeIcon: ScopeIcon,
  scopeLabel,
  state,
  onDecision,
  decided,
}: LaneProps): ReactElement {
  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div
        className={cn(
          'flex items-center gap-2.5 rounded-lg border p-3',
          accent === 'agent' ? 'border-blue-200/40 bg-blue-200/[0.06]' : 'border-blue-400/40 bg-blue-400/[0.06]',
        )}
      >
        <span
          className={cn(
            'grid size-8 place-items-center rounded-lg',
            accent === 'agent' ? 'bg-blue-200/15' : 'bg-blue-400/15',
          )}
        >
          <Icon aria-hidden className={cn('size-4', accent === 'agent' ? 'text-blue-700' : 'text-blue-600')} />
        </span>
        <div className="min-w-0">
          <p className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
            {title}
            <InfoTip label="Role details">
              Role <span className="font-mono">{roleId}</span> — access is decided by role, not by the query text.
            </InfoTip>
          </p>
          <p className="truncate text-[0.72rem] text-muted-foreground">{subtitle}</p>
        </div>
        <Badge tone={accent === 'agent' ? 'agent' : 'graph'} className="ml-auto">
          <ScopeIcon aria-hidden className="size-3" /> {scopeLabel}
        </Badge>
      </div>

      {state.approval && onDecision && (
        <ApprovalCard approval={state.approval} onDecision={onDecision} resolved={decided} />
      )}

      {/*
        Was `h-[380px]`, and the two lanes together put 760px of empty box on
        screen before a run existed. The panel now mounts only once a run has
        started, and its scroll window is viewport-relative so a short laptop
        does not lose the answer below it.
      */}
      <div className="h-[min(26rem,60vh)]">
        <AgentTracePanel state={state} />
      </div>

      <AnswerPanel state={state} />
    </div>
  )
}

/**
 * Access demo (Aegis Governance) — the same question, two roles, one system. Two
 * independent run streams play the *same* query as two roles at once: an
 * **operations lead** (full retrieval → human gate → can act) and a **client**
 * (own-account retrieval, status changes not permitted). Each lane owns its own
 * `useRunStream`, so the divergence is real — two live trajectories, not a
 * scripted split-screen.
 *
 * ## Why nothing below the control card renders before a run
 *
 * Everything on this screen is *derived from a run*: the comparison rows, the
 * governance-control statuses, the node ledger, the trace log, the answer. Before
 * a run they are all placeholders, and this screen used to draw every one of them
 * — two fixed-height empty trace panels, two empty answer cards and a comparison
 * table of em-dashes. That is the largest dead space in the portal, and the fix is
 * not a shorter box: it is to render the pre-run state as one composed card and
 * mount the rest when there is something in it.
 *
 * ## Why the two chart cards are small multiples and not one grouped chart
 *
 * Both lanes emit structurally identical series, which makes ops-vs-client a
 * genuine A/B no other screen can offer — but `NodeGantt` and `RankedBars` each
 * scale to their own data, so the two halves are compared by the figures printed
 * on every row, not by bar length across the gutter. The `InfoTip` on each card
 * says so rather than letting a reader assume a shared axis.
 */
export function SimulationView(): ReactElement {
  // Live session token so the two run streams carry `Authorization`. Both runs
  // are user-triggered, so no hydration gate is needed — by click time the
  // session has been restored.
  const { session } = useAuth()
  const token = session?.token ?? null

  const opsLead = useRunStream()
  const client = useRunStream()
  const [decided, setDecided] = useState(false)

  const running = opsLead.running || client.running

  const runBoth = (): void => {
    setDecided(false)
    opsLead.reset()
    client.reset()
    opsLead.start(SIM_QUERY, 'operations_lead', token)
    client.start(SIM_QUERY, 'client', token)
  }

  const resetBoth = (): void => {
    setDecided(false)
    opsLead.reset()
    client.reset()
  }

  const handleDecision = (decision: ApprovalDecision): void => {
    setDecided(true)
    opsLead.resolveApproval(decision)
  }

  const ops = opsLead.state
  const cli = client.state
  const started = ops.events.length > 0 || cli.events.length > 0
  // The window between the click and the first event: the run is real, so the
  // lanes mount and stream into view rather than appearing all at once.
  const active = started || running
  // Both runs settled → reveal the "differs" highlight on the rows that diverge.
  const settled = isSettled(ops, cli)
  const opsTool = toolMark(ops)
  const cliTool = toolMark(cli)

  const ledgered = ops.nodeLedger.length > 0 || cli.nodeLedger.length > 0
  const ranked = ops.retrievalScores.length > 0 || cli.retrievalScores.length > 0
  // What the two ranked lists actually differ by, measured from the source ids. The
  // panel asserted a difference and drew two identical lists; it now says which.
  const divergence = rankedDivergence(
    ops.retrievalScores,
    cli.retrievalScores,
    ops.candidates,
    cli.candidates,
    ['the operations lead', 'the client'],
  )

  /** The two lanes, in the order both small-multiple cards draw them. */
  const columns = [
    { lane: ops, title: 'Operations lead', roleId: 'operations_lead', icon: UserCog, accent: 'agent' as const },
    { lane: cli, title: 'Client', roleId: 'client', icon: UserRound, accent: 'graph' as const },
  ]

  // The governance controls that produce the divergence — honest tech one line down.
  const controls: Capability[] = [
    { name: 'Role-based access', tech: 'RBAC · who is asking', status: started ? 'live' : 'idle' },
    {
      name: 'Retrieval scope',
      tech: 'per-role data access',
      status: ops.candidates > 0 || cli.candidates > 0 ? 'live' : 'idle',
    },
    {
      name: 'Tool allowlist',
      tech: 'who is allowed to act',
      status: ops.toolCalls.length > 0 || cli.toolCalls.length > 0 ? 'live' : 'idle',
    },
    {
      name: 'Human gate',
      tech: 'approval on risky actions',
      status: gateStatus(ops),
    },
  ]

  const comparisonRows = [
    {
      label: 'Retrieval scope',
      a: (
        <Cell mark="allow">
          Full account history{ops.candidates > 0 ? ` · ${COUNT.format(ops.candidates)} sources` : ''}
        </Cell>
      ),
      b: (
        <Cell mark="allow">
          Own account only{cli.candidates > 0 ? ` · ${COUNT.format(cli.candidates)} sources` : ''}
        </Cell>
      ),
      diff: settled,
    },
    /*
      Two rows where there was one. The single row was headed **Status change** — a
      write — and filled after settle with `toolMark`, which named whatever call the
      run reached for first. On the seeded question that is `find_requests`, a read, so
      the row read "find_requests denied" under a write heading, beside a "Human
      approval" cell saying the run was still parked at its gate. A permission and an
      observed outcome are two different propositions and they now sit on two rows.
    */
    {
      label: 'Status change',
      a: <Cell mark="allow">Permitted for this role</Cell>,
      b: <Cell mark="deny">Not permitted for this role</Cell>,
      diff: true,
    },
    {
      label: 'Action taken',
      a: <Cell mark={opsTool.mark}>{opsTool.label}</Cell>,
      b: <Cell mark={cliTool.mark}>{cliTool.label}</Cell>,
      diff: settled && opsTool.label !== cliTool.label,
    },
    {
      label: 'Human gate',
      a: (
        <Cell mark={ops.awaitedApproval ? 'gate' : 'none'}>
          {ops.awaitedApproval ? 'Approval required' : 'Human approval'}
        </Cell>
      ),
      b: <Cell mark="none">Not reached</Cell>,
      diff: settled,
    },
    { label: 'Node time', a: <NodeTimeCell state={ops} />, b: <NodeTimeCell state={cli} /> },
    { label: 'Run cost', a: <CostCell state={ops} />, b: <CostCell state={cli} /> },
  ]

  const queryChip = (
    <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 p-2.5">
      <span className="eyebrow mt-0.5 shrink-0">question</span>
      <p className="min-w-0 font-mono text-[0.72rem] leading-relaxed break-words text-foreground">
        {SIM_QUERY}
      </p>
    </div>
  )

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader
          eyebrow="Aegis Governance"
          title={
            <span className="flex items-center gap-1.5">
              Access demo
              <InfoTip label="How this works">
                Role-based access, retrieval scope and the tool allowlist make one query resolve
                differently by who is asking — enforced on every request.
              </InfoTip>
            </span>
          }
          actions={
            <div className="flex flex-wrap gap-2">
              <Button onClick={runBoth} disabled={running}>
                <Play aria-hidden className="size-4" /> Run demo
              </Button>
              <Button variant="outline" onClick={resetBoth} disabled={running || !active}>
                <RotateCcw aria-hidden className="size-4" /> Reset
              </Button>
            </div>
          }
        />
        <CardBody className="@container/hero">
          {active ? (
            <div className="space-y-3">
              {queryChip}
              <CapabilityMap items={controls} layout="grid" />
            </div>
          ) : (
            <div className="grid items-center gap-6 @[34rem]/hero:grid-cols-[auto_minmax(0,1fr)]">
              <Scene name="exercising" size="md" className="mx-auto shrink-0" />
              <div className="min-w-0 space-y-3">
                <p className="t-body text-pretty text-muted-foreground">
                  Same question, two roles — see what each is allowed to do.
                </p>
                {queryChip}
              </div>
            </div>
          )}
          <p className="sr-only" role="status">
            {running ? 'Both runs are streaming.' : settled ? 'Both runs have settled.' : ''}
          </p>
        </CardBody>
      </Card>

      {active && (
        <>
          <RevealOnScroll>
            <ComparisonCard
              title="What each role is allowed to do"
              columns={['Operations lead', 'Client']}
              rows={comparisonRows}
            />
          </RevealOnScroll>

          {ledgered && (
            <RevealOnScroll delayMs={40}>
              <DataPanel
                className="min-w-0"
                eyebrow="glass-box · per lane"
                // A string title, so `DataPanel` can name its own scroll region:
                // the two timelines are wide, and the part a 390px viewport pushes
                // off to the right has to be reachable and announced.
                title="Where each run spent its time"
                actions={
                  <InfoTip label="How to read these two timelines">
                    Each lane is scaled to its own run, so compare the milliseconds and the cost
                    printed on every row rather than bar length across the two halves.
                  </InfoTip>
                }
                maxHeight={520}
                footer={
                  <Receipt
                    origin="node_finished · live run stream"
                    detail={`${COUNT.format(ops.nodeLedger.length)} + ${COUNT.format(
                      cli.nodeLedger.length,
                    )} nodes`}
                  />
                }
              >
                <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
                  {columns.map((col) => (
                    <div key={col.roleId} className="flex min-w-[22rem] flex-col gap-2.5">
                      <h3>
                        <LaneChip
                          title={col.title}
                          roleId={col.roleId}
                          icon={col.icon}
                          accent={col.accent}
                        />
                      </h3>
                      {col.lane.nodeLedger.length > 0 ? (
                        <NodeGantt nodes={col.lane.nodeLedger} />
                      ) : (
                        <Absence
                          figure="Per-node cost and latency"
                          why="No node has finished in this lane yet."
                          needed="A completed graph node."
                        />
                      )}
                    </div>
                  ))}
                </div>
              </DataPanel>
            </RevealOnScroll>
          )}

          {ranked && (
            <RevealOnScroll delayMs={60}>
              <Card className="min-w-0">
                <CardHeader
                  eyebrow="per-role retrieval"
                  title={
                    <span className="flex items-center gap-1.5">
                      What each role was allowed to rank
                      <InfoTip label="How to read these two lists">
                        Reranker relevance for the sources each role could reach; the two lists are
                        scaled separately because they are drawn from different documents.
                      </InfoTip>
                    </span>
                  }
                />
                <CardBody className="space-y-4">
                  {/* What the two lists differ by, before either is read. A panel
                      whose evidence contradicts its own headline is worse than none. */}
                  <p
                    className="flex items-start gap-2 rounded-md border border-border bg-surface-2/50 px-3 py-2 text-[0.8rem] leading-relaxed text-foreground"
                    aria-live="polite"
                  >
                    <GitCompare className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                    <span className="min-w-0">{divergence.note}</span>
                  </p>
                  <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-2">
                    {columns.map((col, i) => (
                      <div key={col.roleId} className="flex min-w-0 flex-col gap-2.5">
                        <h3 className="flex min-w-0 items-center gap-2">
                          <LaneChip
                            title={col.title}
                            roleId={col.roleId}
                            icon={col.icon}
                            accent={col.accent}
                          />
                          {(i === 0 ? divergence.onlyA : divergence.onlyB) > 0 ? (
                            <Badge tone="neutral" className="ml-auto shrink-0">
                              {COUNT.format(i === 0 ? divergence.onlyA : divergence.onlyB)} only here
                            </Badge>
                          ) : null}
                        </h3>
                        {col.lane.retrievalScores.length > 0 ? (
                          <RankedBars
                            data={col.lane.retrievalScores.map((s) => ({
                              name: s.label,
                              value: s.score,
                            }))}
                            valueFormatter={(v) => SCORE.format(v)}
                            color={col.accent}
                            tail="omit"
                            label={`Sources ranked for ${col.title}`}
                          />
                        ) : (
                          <Absence
                            figure="Ranked sources"
                            why="This lane's retrieval returned no scored source."
                            needed="A rerank step that reaches at least one document this role may read."
                          />
                        )}
                      </div>
                    ))}
                  </div>
                  <Receipt
                    origin="retrieval · scored_sources"
                    detail={`${COUNT.format(ops.retrievalScores.length)} + ${COUNT.format(
                      cli.retrievalScores.length,
                    )} sources ranked`}
                  />
                </CardBody>
              </Card>
            </RevealOnScroll>
          )}

          <RevealOnScroll delayMs={80}>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <Lane
                title="Operations lead"
                subtitle="Handles the case end-to-end"
                roleId="operations_lead"
                icon={UserCog}
                accent="agent"
                scopeIcon={ShieldCheck}
                scopeLabel="Full access"
                state={ops}
                onDecision={handleDecision}
                decided={decided}
              />
              <Lane
                title="Client"
                subtitle="Sees only their own account"
                roleId="client"
                icon={UserRound}
                accent="graph"
                scopeIcon={Shield}
                scopeLabel="Own records"
                state={cli}
              />
            </div>
          </RevealOnScroll>
        </>
      )}
    </div>
  )
}

/** Client entry for the Access demo section — gated on a reachable backend. */
export function SimulationMount(): ReactElement {
  return (
    <BackendGate>
        <div className="space-y-4">
          <PageHeader
            eyebrow="RBAC scope"
            title="Access demo"
          />
          <SimulationView />
        </div>
    </BackendGate>
  )
}
