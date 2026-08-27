/**
 * What the RAG dashboard can honestly say about a retrieval, and how it labels it.
 *
 * The backend measures a much richer `observability` block on the AG-UI
 * `retrieval_citations` event (see `aegis/src/aegis/retrieval/models.py`), but the
 * web app's simplified `/query` SSE contract (`RetrievalStep` + `Provenance` in
 * `lib/stream.ts`) carries only a subset of it: which recall arms fired, the
 * fusion method, the fused pool size, and the reranked scores. This type is
 * deliberately that subset and nothing more — spotlighting, query rewrite and the
 * bounded Self-RAG loop have no field here because the stream does not report
 * them, so {@link ArsenalPanel} renders those stages as `n/a` instead of holding a
 * value it would have had to invent.
 */

import type { FusionMethod, RetrievalOrigin } from '@/lib/stream'

/** One recall arm's measured contribution, before fusion. */
export interface ArmReport {
  /** Retrieval origin(s) this arm represents (usually one). */
  origins: RetrievalOrigin[]
  /** Candidates this arm produced pre-fusion (0 when the stream omits the count). */
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

/** The measured "which arsenal methods ran" record for one retrieval. */
export interface RetrievalObservability {
  /** Per-recall-arm candidate counts (vector / graph / bm25), pre-fusion. */
  arms: ArmReport[]
  /** The fusion method applied to the arms (RRF on the hybrid path). */
  fusion: FusionMethod
  /** The fused wide-recall pool size (the honest N). */
  fused_candidates: number
  /** Whether rerank ran and the top scores it produced. */
  rerank: RerankReport
}

/** The colour (signal name) each recall origin owns in the provenance donut. */
/**
 * The ramp step each recall arm is drawn in.
 *
 * `bm25` used to be `ok` — the reserved status green. A recall arm is a subject,
 * not a state, and DESIGN.md §2 keeps the three status hues out of series colour
 * for exactly this reason: green on a retrieval chart reads as a verdict about
 * the retrieval rather than as the name of an arm. All three arms are now steps
 * on the one blue ramp, and they are told apart by their legend label and count
 * first, the step second.
 */
export const ORIGIN_COLOR = {
  vector: 'graph',
  graph: 'ml',
  bm25: 'agent',
  cache: 'neutral',
} as const

/** A human label for each recall origin. */
export const ORIGIN_LABEL: Record<RetrievalOrigin, string> = {
  vector: 'Vector (dense)',
  graph: 'Graph (entities)',
  bm25: 'Keyword (ts_rank)',
  cache: 'Cache',
}
