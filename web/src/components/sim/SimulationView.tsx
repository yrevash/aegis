'use client'

import { Check, Minus, Play, RotateCcw, Shield, ShieldCheck, UserCog, UserRound, X } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'
import { useState } from 'react'

import { ApprovalCard } from '@/components/approval/ApprovalCard'
import { AnswerPanel } from '@/components/console/AnswerPanel'
import { formatUsd } from '@/components/dashboard/roi'
import { CapabilityMap, ComparisonCard, CountUp, RevealOnScroll, type Capability } from '@/components/shared'
import { AgentTracePanel } from '@/components/trace/AgentTracePanel'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { PageHeader } from '@/components/primitives/PageHeader'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'
import { useRunStream } from '@/state/useRunStream'
import type { ApprovalDecision } from '@/lib/api/types'

import { gateStatus, isSettled, toolMark, type Mark } from './simLogic'

/**
 * The single query both roles run at once. It is phrased to require a status
 * change — the one HIGH-risk tool — so the operations lead hits the human gate
 * while the client is refused it. The divergence comes from *who is asking*, not
 * from the text, and the query names no record id: the adapter generates those at
 * seed time, so a literal id would name a request that does not exist.
 */
const SIM_QUERY = 'Close out my oldest open request and record why it was closed'

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
    <div className="flex flex-col gap-4">
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
          <Icon className={cn('size-4', accent === 'agent' ? 'text-blue-700' : 'text-blue-600')} />
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
          <ScopeIcon className="size-3" /> {scopeLabel}
        </Badge>
      </div>

      {state.approval && onDecision && (
        <ApprovalCard approval={state.approval} onDecision={onDecision} resolved={decided} />
      )}

      <div className="h-[380px]">
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
 * scripted split-screen. The comparison leads; the two lanes prove it.
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
  // Both runs settled → reveal the "differs" highlight on the rows that diverge.
  const settled = isSettled(ops, cli)
  const opsTool = toolMark(ops)
  const cliTool = toolMark(cli)

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
          Full account history{ops.candidates > 0 ? ` · ${ops.candidates} sources` : ''}
        </Cell>
      ),
      b: (
        <Cell mark="allow">
          Own account only{cli.candidates > 0 ? ` · ${cli.candidates} sources` : ''}
        </Cell>
      ),
      diff: settled,
    },
    {
      label: 'Status change',
      a: <Cell mark={settled ? opsTool.mark : 'allow'}>{settled ? opsTool.label : 'Can execute'}</Cell>,
      b: <Cell mark={settled ? cliTool.mark : 'deny'}>{settled ? cliTool.label : 'Not permitted'}</Cell>,
      diff: settled,
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
    {
      label: 'Run cost',
      a: <CostCell state={ops} />,
      b: <CostCell state={cli} />,
    },
  ]

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
                <Play className="size-4" /> Run demo
              </Button>
              <Button variant="outline" onClick={resetBoth} disabled={running || !started}>
                <RotateCcw className="size-4" /> Reset
              </Button>
            </div>
          }
        />
        <CardBody className="space-y-3">
          <p className="t-body text-muted-foreground">
            Same question, two roles — see what each is allowed to do. The operations lead may call
            update_request_status behind the human gate; the client is scoped to their own account
            and may only add a case note.
          </p>
          <div className="flex items-start gap-2 rounded-lg border border-border bg-surface-2/60 p-2.5">
            <span className="eyebrow mt-0.5 shrink-0">question</span>
            <p className="font-mono text-[0.72rem] leading-relaxed text-foreground">{SIM_QUERY}</p>
          </div>
        </CardBody>
      </Card>

      <RevealOnScroll>
        <ComparisonCard
          title="What each role is allowed to do"
          columns={['Operations lead', 'Client']}
          rows={comparisonRows}
        />
      </RevealOnScroll>

      <RevealOnScroll delayMs={40}>
        <Card>
          <CardHeader
            title={
              <span className="flex items-center gap-1.5">
                Controls at work
                <InfoTip label="Governance controls">
                  The four governance checks that decide the outcome — active as the runs progress.
                </InfoTip>
              </span>
            }
          />
          <CardBody>
            <CapabilityMap items={controls} layout="grid" />
          </CardBody>
        </Card>
      </RevealOnScroll>

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
    </div>
  )
}

/** Client entry for the Access demo section — gated on a reachable backend. */
export function SimulationMount(): ReactElement {
  return (
    <BackendGate>
      <TooltipProvider>
        <div className="space-y-4">
          <PageHeader
            eyebrow="RBAC scope"
            title="Access demo"
          />
          <SimulationView />
        </div>
      </TooltipProvider>
    </BackendGate>
  )
}
