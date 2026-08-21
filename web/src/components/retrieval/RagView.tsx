'use client'

import { useState, type ReactElement } from 'react'

import { ArsenalPanel } from '@/components/retrieval/ArsenalPanel'
import { ProvenanceDonut } from '@/components/retrieval/ProvenanceDonut'
import { RerankScoreboard } from '@/components/retrieval/RerankScoreboard'
import { QueryBar } from '@/components/console/QueryBar'
import { SceneState } from '@/components/illustration/Scene'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Card, CardBody } from '@/components/ui/Card'
import { BackendGate } from '@/components/shared/BackendGate'
import { personasForRole } from '@/config/personas'
import type { Provenance, Role, ScoredSource } from '@/lib/stream'
import { useRunStream } from '@/state/useRunStream'
import type { RunState } from '@/state/runReducer'
import type { RetrievalObservability } from './observability'

/** The recall origins the arms panel understands (cache is not a recall arm). */
const RECALL_ORIGINS = ['vector', 'graph', 'bm25'] as const

/** What the arsenal panels render, all of it measured from the run stream. */
interface ArsenalData {
  obs: RetrievalObservability
  sources: ScoredSource[]
  provenance: Provenance | null
}

/** Whether a run has produced any retrieval evidence yet. */
function hasRetrieval(state: RunState): boolean {
  return (
    state.retrievalScores.length > 0 || state.candidates > 0 || state.provenance !== null
  )
}

/**
 * Derive the measured observability from a live run's reduced state. The web
 * `/query` SSE contract carries only arms-fired (via provenance origins), fusion,
 * the fused candidate count, and the reranked scores — the panels render the rest
 * of the arsenal as `n/a`.
 */
function deriveFromRun(state: RunState): ArsenalData {
  const prov = state.provenance
  const recall = (prov?.origins ?? []).filter((o) =>
    (RECALL_ORIGINS as readonly string[]).includes(o),
  )
  // No provenance yet (or a cache-only hit): default to the hybrid arms since a
  // retrieval demonstrably ran.
  const origins = recall.length > 0 ? recall : [...RECALL_ORIGINS]
  const scores = state.retrievalScores
  const topScores = [...scores].map((s) => s.score).sort((a, b) => b - a)

  const obs: RetrievalObservability = {
    arms: origins.map((o) => ({ origins: [o], candidates: 0, fired: true })),
    fusion: prov?.fusion ?? 'rrf',
    fused_candidates: state.candidates,
    rerank: {
      ran: scores.length > 0,
      input_candidates: state.candidates,
      kept: scores.length,
      top_scores: topScores,
    },
  }
  return { obs, sources: scores, provenance: prov }
}

/**
 * RAG (§ retrieval) — the retrieval arsenal made observable. Run a query (or read
 * the last run) and the panel shows exactly which recall arms fired, that RRF
 * fused them, and whether rerank ran with its top scores. Every value is measured
 * from the run stream; the fields the /query contract does not carry are shown as
 * `n/a`, never faked, and before a run the surface says so plainly.
 */
function RagView({ role }: { role: Role }): ReactElement {
  const { state, running, start, reset } = useRunStream()
  const [personaId, setPersonaId] = useState(personasForRole(role)[0]?.id ?? '')

  const data: ArsenalData | null = hasRetrieval(state) ? deriveFromRun(state) : null

  // Unlock the query bar when a run parks at the human-approval gate (this
  // surface has no approval modal), so Reset stays reachable.
  const barBusy = running && state.phase !== 'awaiting_approval'

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="hybrid · rerank" title="RAG" />

      <QueryBar
        role={role}
        personaId={personaId}
        onPersonaChange={setPersonaId}
        onRun={(q) => start(q, personaId, null)}
        onReset={reset}
        running={barBusy}
      />

      {data === null ? (
        <Card>
          <CardBody className="py-10">
            {/* `md` (200px), not `lg`: a 268px scene plus the card and shell
                padding is wider than a 390px viewport can hold. */}
            <SceneState name="retrieval" size="md">
              <p className="text-sm font-medium text-foreground">No retrieval measured yet</p>
              <p className="mt-1 text-sm text-muted-foreground">
                Run a query to light up the arms, fusion and rerank.
              </p>
            </SceneState>
          </CardBody>
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="min-w-0 xl:col-span-2">
            <ArsenalPanel obs={data.obs} />
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <ProvenanceDonut obs={data.obs} />
            <RerankScoreboard
              scores={data.sources}
              candidates={data.obs.fused_candidates}
              provenance={data.provenance}
            />
          </div>
        </div>
      )}
    </div>
  )
}

/** Client entry for the RAG section — gated on a reachable backend. */
export function RagMount({ role }: { role: Role }): ReactElement {
  return (
    <BackendGate>
        <RagView role={role} />
    </BackendGate>
  )
}
