import { Suspense, lazy, useEffect, useState, type ReactElement } from 'react'
import { toast } from 'sonner'

import { getGraph } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { ApprovalCard } from '@/components/approval/ApprovalCard'
import { AnswerPanel } from '@/components/console/AnswerPanel'
import { ApprovalSpotlight } from '@/components/console/ApprovalSpotlight'
import { DecisionStrip } from '@/components/console/DecisionStrip'
import { beatFromSignal } from '@/components/console/motion'
import { QueryBar } from '@/components/console/QueryBar'
import { ReasoningLane } from '@/components/console/ReasoningLane'
import { StreamBanners } from '@/components/console/StreamBanners'
import { NodeGantt } from '@/components/charts/NodeGantt'
import { GuardrailReveal } from '@/components/guardrail/GuardrailReveal'
import { TrustBar } from '@/components/layout/TrustBar'
import { EfficiencyPanel } from '@/components/metrics/EfficiencyPanel'
import { ConfidenceCard } from '@/components/ml/ConfidenceCard'
import { ShapPanel } from '@/components/ml/ShapPanel'
import { RerankScoreboard } from '@/components/retrieval/RerankScoreboard'
import { RevealOnScroll } from '@/components/shared'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { AgentTracePanel } from '@/components/trace/AgentTracePanel'
import { personasForRole } from '@/config/personas'
import { useMetrics } from '@/state/useMetrics'
import { useRunStream } from '@/state/useRunStream'
import type { ApprovalDecision, GraphResponse } from '@/types/api'

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }

// The knowledge-graph viz pulls in `react-force-graph-2d` (canvas + d3 forces);
// lazy-load it so that heavy library ships as its own async chunk.
const KnowledgeGraph = lazy(() =>
  import('@/components/graph/KnowledgeGraph').then((m) => ({ default: m.KnowledgeGraph })),
)

/** Placeholder shown while the graph chunk streams in. */
function GraphFallback(): ReactElement {
  return (
    <Card className="flex h-full flex-col overflow-hidden">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <CardTitle>Orchestration</CardTitle>
      </CardHeader>
      <div className="min-h-0 flex-1 animate-pulse bg-surface-2/30" />
    </Card>
  )
}

/**
 * The console (Aegis Router) — one screen where a query streams the agent's
 * activity while the orchestration graph animates, the outcome resolves in a
 * Decision strip (confidence · guardrails · sources · cost), and the supporting
 * panels — Confidence, Sources, Why, Efficiency — read as compact bento tiles
 * rather than a stack of jargon cards. A single active-beat pulses the live
 * subsystem in lockstep across the trace, graph, and trust bar; between runs the
 * console idles with a gentle attract-loop. Numbers and state lead; the honest
 * technical depth lives one hover / expander down.
 */
export function MoneyShotConsole(): ReactElement {
  const { session } = useAuth()
  const token = session?.token ?? null
  const role = session?.role ?? 'ai_team'

  const { state, running, start, resolveApproval, reset } = useRunStream()
  const metrics = useMetrics(token)

  const [graph, setGraph] = useState<GraphResponse>(EMPTY_GRAPH)
  const [personaId, setPersonaId] = useState(personasForRole(role)[0]?.id ?? '')
  const [decided, setDecided] = useState(false)

  useEffect(() => {
    void getGraph(token).then(setGraph).catch(() => setGraph(EMPTY_GRAPH))
  }, [token])

  const handleRun = (query: string): void => {
    setDecided(false)
    start(query, personaId, token)
  }

  const handleDecision = (decision: ApprovalDecision): void => {
    setDecided(true)
    resolveApproval(decision)
    toast[decision === 'approve' ? 'success' : 'warning'](
      decision === 'approve' ? 'Action approved' : 'Action rejected',
      { description: state.approval?.action },
    )
  }

  const handleReset = (): void => {
    setDecided(false)
    reset()
  }

  // The active-beat drives the synchronized same-hue pulse; `idle` drives the
  // attract-loop when nothing has run yet.
  const beat = beatFromSignal(state.lastSignal)
  const idle = !running && state.events.length === 0

  return (
    <div className="flex flex-col gap-4">
      <QueryBar
        role={role}
        personaId={personaId}
        onPersonaChange={setPersonaId}
        onRun={handleRun}
        onReset={handleReset}
        running={running}
      />
      <TrustBar state={state} beat={beat} idle={idle} />
      <StreamBanners state={state} />

      {/*
        Fluid multi-column console. The layout collapses in two honest steps so
        nothing is ever squeezed to an unreadable width or clipped:
          • < lg (≤1023): a single stacked column — every panel full width.
          • lg–2xl (1024–1535): a two-column stage (activity + centre) with the
            right rail dropped BENEATH, flowing its panels 2-up so they read at
            comfortable width instead of a cramped ~280px sliver.
          • ≥ 2xl (1536+): the full three-column view (activity · centre · rail).
        Every column and panel carries `min-w-0` so grid children shrink to fit
        instead of overflowing the `overflow-x-hidden` page.
      */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        {/* Left rail — the live activity log (full height). */}
        <div className="min-w-0 lg:col-span-4 2xl:col-span-3">
          <RevealOnScroll className="block h-[440px] lg:h-[620px] 2xl:h-[760px]">
            <AgentTracePanel state={state} />
          </RevealOnScroll>
        </div>

        {/* Centre stage — orchestration hero, the Decision strip, reasoning,
            answer, and the collapsible guardrail glass box. */}
        <div className="flex min-w-0 flex-col gap-4 lg:col-span-8 2xl:col-span-6">
          <RevealOnScroll className="block h-[380px] lg:h-[460px]" delayMs={40}>
            <Suspense fallback={<GraphFallback />}>
              <KnowledgeGraph base={graph} state={state} beat={beat} idle={idle} />
            </Suspense>
          </RevealOnScroll>

          <RevealOnScroll delayMs={80}>
            <DecisionStrip state={state} />
          </RevealOnScroll>

          <ReasoningLane state={state} />

          <RevealOnScroll delayMs={120}>
            <AnswerPanel state={state} />
          </RevealOnScroll>

          {state.nodeLedger.length > 0 && (
            <RevealOnScroll delayMs={140}>
              <Card>
                <CardHeader className="flex-row items-center gap-2 space-y-0">
                  <CardTitle>Glass box</CardTitle>
                </CardHeader>
                <CardContent>
                  <NodeGantt nodes={state.nodeLedger} />
                </CardContent>
              </Card>
            </RevealOnScroll>
          )}

          <RevealOnScroll delayMs={160}>
            <GuardrailReveal guardrails={state.guardrails} />
          </RevealOnScroll>
        </div>

        {/* Right rail — confidence, sources, why, and fleet efficiency. Beneath
            the stage it flows 2-up (md); as the true rail at 2xl it stacks. */}
        <div className="grid min-w-0 grid-cols-1 gap-4 md:grid-cols-2 lg:col-span-12 2xl:col-span-3 2xl:grid-cols-1">
          <RevealOnScroll className="min-w-0" delayMs={40}>
            <ConfidenceCard ml={state.ml} />
          </RevealOnScroll>
          <RevealOnScroll className="min-w-0" delayMs={80}>
            <RerankScoreboard
              scores={state.retrievalScores}
              candidates={state.candidates}
              provenance={state.provenance}
            />
          </RevealOnScroll>
          <RevealOnScroll className="min-w-0" delayMs={120}>
            <ShapPanel ml={state.ml} />
          </RevealOnScroll>
          <RevealOnScroll className="min-w-0" delayMs={160}>
            <EfficiencyPanel metrics={metrics} state={state} />
          </RevealOnScroll>
        </div>
      </div>

      {/* Human-approval spotlight — scrims the console while a decision is due. */}
      {state.approval && (
        <ApprovalSpotlight>
          <ApprovalCard
            approval={state.approval}
            onDecision={handleDecision}
            resolved={decided}
            ml={state.ml}
          />
        </ApprovalSpotlight>
      )}
    </div>
  )
}
