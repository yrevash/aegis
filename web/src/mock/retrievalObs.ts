/**
 * The retrieval "arsenal observability" contract + an offline sample.
 *
 * A byte-faithful TypeScript mirror of the `observability` block the backend
 * emits on the `retrieval_citations` custom event (see
 * `aegis/src/aegis/retrieval/stream.py::_observability_payload`, built from
 * `RetrievalObservability` in `aegis/src/aegis/retrieval/models.py`). Every field
 * here is *measured* by the Python pipeline, never fabricated: which recall arms
 * fired and how many candidates each produced, that RRF fusion ran (+ the fused
 * pool size), whether rerank ran and its top scores, whether the context was
 * spotlighted, and — when the higher layers wrapped the call — whether a query
 * rewrite and the bounded Self-RAG loop iterated.
 *
 * The web app's simplified `/query` SSE contract (`RetrievalStep` + `Provenance`
 * in `lib/stream.ts`) does NOT carry this block — only the aegis AG-UI
 * `retrieval_citations` event does. So the RAG dashboard renders this rich shape
 * from the {@link SAMPLE_OBSERVABILITY} sample offline, and derives the subset the
 * run stream *does* surface (arms fired, fusion, fused count, rerank scores) when
 * a run happens live. The offline sample is always labelled `sample`.
 */

import type { FusionMethod, RetrievalOrigin, ScoredSource } from '@/lib/stream'

/** One recall arm's measured contribution, before fusion. */
export interface ArmReport {
  /** Retrieval origin(s) this arm represents (usually one). */
  origins: RetrievalOrigin[]
  /** Candidates this arm produced pre-fusion (measured; 0 when not measurable). */
  candidates: number
  /** Whether this arm produced any candidate. */
  fired: boolean
}

/** Whether the LLM reranker ran, and the top scores it produced. */
export interface RerankReport {
  ran: boolean
  /** Fused candidates offered to rerank. */
  input_candidates: number
  /** Candidates that survived into the final sources. */
  kept: number
  /** Survivors' scores in final order. */
  top_scores: number[]
}

/** Whether a context-aware query rewrite ran before retrieval. */
export interface RewriteReport {
  ran: boolean
  changed: boolean
  /** The standalone query retrieval actually ran with, or null. */
  rewritten: string | null
}

/** Whether the bounded Self-RAG loop iterated, and how many times. */
export interface AgenticReport {
  ran: boolean
  used_rounds: number
  max_rounds: number
  round_queries: string[]
}

/** The honest "which arsenal methods ran" record for one retrieval. */
export interface RetrievalObservability {
  /** Per-recall-arm candidate counts (vector / graph / bm25), pre-fusion. */
  arms: ArmReport[]
  /** The fusion method applied to the arms (RRF on the hybrid path). */
  fusion: FusionMethod
  /** The fused wide-recall pool size (the honest N). */
  fused_candidates: number
  /** Whether rerank ran and the top scores it produced. */
  rerank: RerankReport
  /** Whether the answer context was Microsoft-spotlighted. */
  spotlight_applied: boolean
  /** Query-rewrite observability, or null if no rewrite layer ran. */
  rewrite: RewriteReport | null
  /** Self-RAG-loop observability, or null if single-shot. */
  agentic: AgenticReport | null
}

/** The user's original query for the offline sample. */
export const SAMPLE_QUERY =
  'Resolve case #4821 — customer reports a duplicate charge on a premium account'

/** The context-aware standalone rewrite the sample loop ran with. */
const SAMPLE_REWRITTEN =
  'Duplicate $4,200 charge refund eligibility on premium account A-771 under Refund Policy v3'

/**
 * An illustrative, honest-shaped sample of the arsenal observability — the numbers
 * a full hybrid + rerank + Self-RAG run would measure. Mirrors the offline mock
 * console scenario (vector + graph + bm25 fused by RRF, reranked to 5 sources).
 * Always rendered with a `sample` label; never a live measured aggregate.
 */
export const SAMPLE_OBSERVABILITY: RetrievalObservability = {
  arms: [
    { origins: ['vector'], candidates: 6, fired: true },
    { origins: ['graph'], candidates: 4, fired: true },
    { origins: ['bm25'], candidates: 3, fired: true },
  ],
  fusion: 'rrf',
  fused_candidates: 9,
  rerank: {
    ran: true,
    input_candidates: 9,
    kept: 5,
    top_scores: [0.94, 0.89, 0.86, 0.71, 0.52],
  },
  spotlight_applied: true,
  rewrite: {
    ran: true,
    changed: true,
    rewritten: SAMPLE_REWRITTEN,
  },
  agentic: {
    ran: true,
    used_rounds: 2,
    max_rounds: 3,
    round_queries: [SAMPLE_QUERY, SAMPLE_REWRITTEN],
  },
}

/** The reranked sources backing the sample (matches the mock console scenario). */
export const SAMPLE_SOURCES: ScoredSource[] = [
  { id: 'kb-duplicate', label: 'KB: Duplicate Charges', score: 0.94 },
  { id: 'policy-refund', label: 'Refund Policy v3', score: 0.89 },
  { id: 'kb-refund-flow', label: 'KB: Refund Flow', score: 0.86 },
  { id: 'sla-premium', label: 'Premium SLA', score: 0.71 },
  { id: 'proc-stripe', label: 'Payment Processor', score: 0.52 },
]

/** The colour (signal name) each recall origin owns in the provenance donut. */
export const ORIGIN_COLOR = {
  vector: 'graph',
  graph: 'ml',
  bm25: 'ok',
  cache: 'neutral',
} as const

/** A human label for each recall origin. */
export const ORIGIN_LABEL: Record<RetrievalOrigin, string> = {
  vector: 'Vector (dense)',
  graph: 'Graph (entities)',
  bm25: 'BM25 (keyword)',
  cache: 'Cache',
}
