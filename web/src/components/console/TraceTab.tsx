'use client'

import { Suspense, lazy, type ReactElement } from 'react'

import { NodeGantt } from '@/components/charts/NodeGantt'
import { GuardrailReveal } from '@/components/guardrail/GuardrailReveal'
import { EfficiencyPanel } from '@/components/metrics/EfficiencyPanel'
import { Absence } from '@/components/primitives/Receipt'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { AgentTracePanel } from '@/components/trace/AgentTracePanel'
import type { GraphResponse, MetricsResponse } from '@/lib/api/types'
import type { RunState } from '@/state/runReducer'

import type { Beat } from './motion'

// The knowledge-graph viz pulls in `react-force-graph-2d` (canvas + d3 forces), so it
// ships as its own async chunk and only loads when this tab is opened.
const KnowledgeGraph = lazy(() =>
  import('@/components/graph/KnowledgeGraph').then((m) => ({ default: m.KnowledgeGraph })),
)

/** One decision line: what the run chose, and why. */
function Decision({
  title,
  detail,
  tone = 'neutral',
}: {
  title: string
  detail: string
  tone?: 'neutral' | 'agent' | 'graph' | 'ok'
}): ReactElement {
  return (
    <li className="flex flex-wrap items-baseline gap-2 border-b border-border/60 py-2 last:border-b-0">
      <Badge tone={tone}>{title}</Badge>
      <span className="min-w-0 flex-1 text-[0.78rem] text-muted-foreground">{detail}</span>
    </li>
  )
}

/**
 * The decisions the run made about itself — the hand-off, the self-repair rounds and
 * what it recalled from earlier turns.
 *
 * These read `state.routing`, `state.reflections` and `state.memory`, three events the
 * backend has been emitting since Phase 5 and the console used to discard. They are the
 * difference between a trace that lists steps and one that explains them.
 *
 * `state.memory` is `null` on every run that carried no `session_id`, and that is a
 * different fact from "recalled nothing" — so a null renders no line at all rather than
 * a zero.
 */
function Decisions({ state }: { state: RunState }): ReactElement | null {
  const { routing, reflections, memory } = state
  if (routing === null && reflections.length === 0 && memory === null) return null

  return (
    <Card>
      <CardHeader title="Decisions" />
      <CardBody>
        <ul className="flex flex-col">
          {routing !== null && (
            <Decision
              tone="agent"
              title={routing.depth === 'team' ? `team of ${routing.fanout}` : routing.role}
              detail={`${routing.reason} · width chosen by ${routing.decided_by.replace('_', ' ')}${
                routing.used_llm ? ' · cheap-model tiebreak consulted' : ''
              }`}
            />
          )}
          {reflections.map((r) => (
            <Decision
              key={r.seq}
              tone={r.done ? 'ok' : 'agent'}
              title={`self-check ${r.iteration}/${r.max_iterations}`}
              detail={`${r.reason}${r.will_retry ? ' · retried' : ''}`}
            />
          ))}
          {memory !== null && (
            <Decision
              tone="graph"
              title="memory"
              detail={
                memory.recalled_fact_count === 0 && memory.recalled_message_count === 0
                  ? 'Nothing recalled from earlier turns.'
                  : `Recalled ${memory.recalled_fact_count} facts and ` +
                    `${memory.recalled_message_count} earlier turns · ${memory.tokens_used} tokens`
              }
            />
          )}
        </ul>
      </CardBody>
    </Card>
  )
}

interface TraceTabProps {
  state: RunState
  /** The base knowledge graph, so the traversal can be drawn against it. */
  graph: GraphResponse
  /** Fleet metrics for the efficiency panel, or null while unmeasured. */
  metrics: MetricsResponse | null
  beat: Beat | null
}

/**
 * The Trace tab — how the answer was produced.
 *
 * Everything a person would only open on purpose: the routing and self-repair decisions,
 * the guardrail glass box, the per-node timing and cost, the raw event log, the graph
 * traversal, and the trace id to correlate with the backend's spans.
 */
export function TraceTab({ state, graph, metrics, beat }: TraceTabProps): ReactElement {
  const measured = state.nodeLedger.length > 0

  /*
   * One height for the graph-and-log pair, from the log's own length.
   *
   * Both panels used to be `h-[380px]`, which DESIGN.md §4 rules out by name: a run
   * with six events sat in 380px with three hundred of them empty, and the same box
   * held a two-hundred-event run. The floor is what the empty state needs, the ceiling
   * is the old fixed height, and between them the pair is as tall as the log has
   * content for. The panel's own scroller owns everything past the ceiling, so a long
   * run is never clipped — the whole change is at the short end, which is where the
   * dead canvas was.
   */
  const paneHeight = `clamp(14rem, ${(7 + state.events.length * 1.5).toFixed(1)}rem, 24rem)`

  return (
    <div className="@container/trace flex flex-col gap-3">
      <Decisions state={state} />

      <GuardrailReveal guardrails={state.guardrails} />

      {measured ? (
        <Card>
          <CardHeader
            title="Per-node timing and cost"
            actions={
              state.traceId !== null ? (
                <Badge tone="neutral">trace {state.traceId.slice(0, 12)}</Badge>
              ) : null
            }
          />
          <CardBody>
            <NodeGantt nodes={state.nodeLedger} />
          </CardBody>
        </Card>
      ) : (
        /* A whole Card to carry one sentence is a panel that exists to hold prose.
           The absence is the same fact in the slot the chart would have occupied. */
        <Absence
          figure="Per-node timing and cost"
          why="No node finished with a measured duration on this run."
        />
      )}

      {/* Container, not `lg:` — this pair sits inside the thread column, and a viewport
          breakpoint firing at a 1024px *window* put two 270px panels side by side. */}
      <div className="grid gap-3 @[52rem]/trace:grid-cols-2">
        <div className="min-w-0" style={{ height: paneHeight }}>
          <Suspense
            fallback={
              <Card className="h-full">
                <div className="h-full animate-pulse rounded-lg bg-surface-2/30" />
              </Card>
            }
          >
            <KnowledgeGraph base={graph} state={state} beat={beat} idle={false} trajectory={false} />
          </Suspense>
        </div>
        <div className="min-w-0" style={{ height: paneHeight }}>
          <AgentTracePanel state={state} />
        </div>
      </div>

      <EfficiencyPanel metrics={metrics} state={state} />
    </div>
  )
}
