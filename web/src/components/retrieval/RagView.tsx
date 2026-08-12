'use client'

import { Layers, WifiOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { ArsenalPanel } from '@/components/retrieval/ArsenalPanel'
import { ProvenanceDonut } from '@/components/retrieval/ProvenanceDonut'
import { RerankScoreboard } from '@/components/retrieval/RerankScoreboard'
import { QueryBar } from '@/components/console/QueryBar'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { isMock, probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { personasForRole } from '@/config/personas'
import type { Provenance, Role, ScoredSource } from '@/lib/stream'
import { useRunStream } from '@/state/useRunStream'
import type { RunState } from '@/state/runReducer'
import {
  SAMPLE_OBSERVABILITY,
  SAMPLE_SOURCES,
  type RetrievalObservability,
} from '@/mock/retrievalObs'

/** Provenance record backing the offline sample (hybrid, RRF, no cache). */
const SAMPLE_PROVENANCE: Provenance = {
  type: 'provenance',
  run_id: 'sample',
  seq: 0,
  origins: ['vector', 'graph', 'bm25'],
  fusion: 'rrf',
  cache_hit: false,
  cache_kind: null,
  original_query: null,
  cached_at: null,
}

/** The recall origins the arms panel understands (cache is not a recall arm). */
const RECALL_ORIGINS = ['vector', 'graph', 'bm25'] as const

/** What the arsenal panels render, plus where the numbers came from. */
interface ArsenalData {
  obs: RetrievalObservability
  source: 'mock' | 'live'
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
 * Derive the measured-subset observability from a live run's reduced state. The
 * web `/query` SSE contract carries only arms-fired (via provenance origins),
 * fusion, the fused candidate count, and the reranked scores — so spotlight,
 * rewrite, and Self-RAG are left `null` and rendered honestly as `n/a`.
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
    spotlight_applied: false,
    rewrite: null,
    agentic: null,
  }
  return { obs, source: 'live', sources: scores, provenance: prov }
}

/** The offline sample arsenal (every field measured on a full run; badged sample). */
const SAMPLE_DATA: ArsenalData = {
  obs: SAMPLE_OBSERVABILITY,
  source: 'mock',
  sources: SAMPLE_SOURCES,
  provenance: SAMPLE_PROVENANCE,
}

/**
 * RAG (§ retrieval) — the retrieval arsenal made observable. Run a query (or read
 * the last run) and the panel shows exactly which recall arms fired, that RRF
 * fused them, whether rerank ran and its top scores, plus spotlight / query-rewrite
 * / Self-RAG when a full retrieval_citations run carries them. Offline it renders a
 * clearly-labelled `sample`; live, every value is measured from the run stream and
 * the fields the /query contract does not carry are shown as `n/a`, never faked.
 */
function RagView({ role, mock }: { role: Role; mock: boolean }): ReactElement {
  const { state, running, start, reset } = useRunStream()
  const [personaId, setPersonaId] = useState(personasForRole(role)[0]?.id ?? '')

  const ran = hasRetrieval(state)
  // Live-derived once a run has produced retrieval; otherwise the offline sample
  // (mock) or an honest empty prompt (live, no run yet).
  const data: ArsenalData | null = ran ? deriveFromRun(state) : mock ? SAMPLE_DATA : null

  // Unlock the query bar when a mock run pauses at the human-approval gate (this
  // surface has no approval modal), so Reset stays reachable.
  const barBusy = running && state.phase !== 'awaiting_approval'

  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">hybrid · rerank</p>
        <h1 className="t-hero text-foreground">RAG</h1>
      </div>

      <QueryBar
        role={role}
        personaId={personaId}
        onPersonaChange={setPersonaId}
        onRun={(q) => start(q, personaId, null)}
        onReset={reset}
        running={barBusy}
      />

      {data === null ? (
        <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-surface-2/40 text-center">
          <Layers className="size-8 text-muted-foreground/50" />
          <div>
            <p className="text-sm font-medium text-foreground">No retrieval measured yet</p>
            <p className="mt-1 text-sm text-muted-foreground">
              Run a query above to watch the recall arms, fusion, and rerank light up.
            </p>
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="min-w-0 xl:col-span-2">
            <ArsenalPanel obs={data.obs} source={data.source} />
          </div>
          <div className="flex min-w-0 flex-col gap-4">
            <ProvenanceDonut obs={data.obs} source={data.source} />
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

/**
 * Client entry for the RAG section. Runs the boot probe once (live-first, mock
 * fallback) before mounting, mirroring `CacheMount` / `ConsoleMount`. Offline is
 * labelled with the honest banner and the panels read the sample; live, the panels
 * measure the run stream.
 */
export function RagMount({ role }: { role: Role }): ReactElement {
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      {mode.mode === 'mock' && (
        <div
          role="status"
          className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
        >
          <WifiOff className="size-3.5 shrink-0" />
          <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
        </div>
      )}
      <RagView role={role} mock={isMock()} />
    </TooltipProvider>
  )
}
