/**
 * What the console can honestly say about a source.
 *
 * Phase 4 gives every chunk a **page number and a bounding box**
 * (`backend/src/app/ingestion/stages.py`), and task 4.14 gives a citation a **verbatim
 * span check** (`aegis/src/aegis/retrieval/citations.py`) whose whole point is that a
 * failed check is *kept and marked*, never dropped and never rendered as though it had
 * been checked.
 *
 * None of that reaches the console yet. `ScoredSource` in `web/src/lib/stream.ts` — and
 * in `backend/src/app/api/schemas.py`, which it mirrors — declares exactly `id`, `label`
 * and `score`, and `graph.py` builds it from those three. So the evidence exists in the
 * store and does not exist on the wire.
 *
 * This module reads the evidence fields **if the retrieval event carries them**, under
 * the names the ingestion pipeline already uses (`page_no`, `bbox`) and the citation
 * checker already uses (`citation_status`, `matched_fraction`), and returns `null` for
 * every one that is absent. It does not re-declare `ScoredSource` and it does not
 * default a page to 1: a source with no recorded page renders with no page, which is the
 * difference between a citation a jury can check and a number we made up.
 *
 * When the retrieval event grows these fields, this file is where they become visible,
 * and the only change needed is deleting the structural reads.
 */

import type { ScoredSource } from '@/lib/stream'

/** The outcome of the verbatim span check, when the run ran one. */
export type VerbatimStatus = 'verified' | 'unverified'

/** One source, with only what was actually reported about it. */
export interface SourceEvidence {
  /** Chunk id — the join key back to the store. */
  id: string
  /** Snippet of the source text, as served. */
  label: string
  /** Rerank relevance in [0, 1]. */
  score: number
  /** The printed page the span sits on, or `null` when the run did not report one. */
  page: number | null
  /** Whether a bounding box was reported, so the span can be located on the page. */
  located: boolean
  /** The span-check result, or `null` when this run carried no check. */
  verbatim: VerbatimStatus | null
  /** How much of the quoted span matched, in [0, 1] — a near-miss is not an invention. */
  matchedFraction: number | null
}

type Raw = Record<string, unknown>

const asPage = (value: unknown): number | null =>
  typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null

const asFraction = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null

/**
 * The span check's verdict, normalised to the two words the console renders.
 *
 * `aegis.retrieval.citations.CitationStatus` is a `StrEnum`, so it arrives as a string.
 * Anything other than a recognised verdict reads as *no check ran*, not as a pass.
 */
function asVerbatim(source: Raw): VerbatimStatus | null {
  const status = source.citation_status
  if (typeof status === 'string') {
    const normalised = status.toLowerCase()
    if (normalised === 'verified') return 'verified'
    if (normalised === 'unverified') return 'unverified'
  }
  return null
}

/** Read one scored source into the evidence the console may show. */
export function readSourceEvidence(source: ScoredSource): SourceEvidence {
  const raw = source as unknown as Raw
  return {
    id: source.id,
    label: source.label,
    score: source.score,
    page: asPage(raw.page_no),
    located: Array.isArray(raw.bbox) && raw.bbox.length === 4,
    verbatim: asVerbatim(raw),
    matchedFraction: asFraction(raw.matched_fraction),
  }
}

/** Read a run's scored sources into evidence rows, in rank order. */
export function readSources(sources: ScoredSource[]): SourceEvidence[] {
  return sources.map(readSourceEvidence)
}

/**
 * Whether any source in this run carried page/bbox provenance.
 *
 * The Sources tab uses this to decide between showing provenance and saying plainly
 * that this run did not record it — rather than drawing an empty column.
 */
export function hasProvenance(sources: SourceEvidence[]): boolean {
  return sources.some((s) => s.page !== null || s.located)
}

/** Whether any source in this run was span-checked. */
export function hasSpanCheck(sources: SourceEvidence[]): boolean {
  return sources.some((s) => s.verbatim !== null)
}
