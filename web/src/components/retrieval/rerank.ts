/**
 * Pure ranking math for the rerank scoreboard.
 *
 * Extracted from the React component so the sort + normalise logic is
 * unit-testable without rendering. The reranker returns scored sources in
 * arbitrary order; the scoreboard shows them highest-first with each bar scaled
 * relative to the strongest score so the visual reads as a ranking, not an
 * absolute gauge.
 */

/** A reranked source as received (score in `[0, 1]`, higher is more relevant). */
export interface RankInput {
  id: string
  label: string
  score: number
}

/** A ranked source with its display rank and normalised bar fraction. */
export interface RankedSource extends RankInput {
  /** 1-based position after sorting (1 = top match). */
  rank: number
  /** Bar fill fraction in `[0, 1]`, relative to the top score. */
  relative: number
}

/**
 * Sort sources by descending score and normalise each to the top score for the
 * bar width. Ties preserve input order (stable). Non-finite or negative scores
 * are floored to `0` so a bad value can never produce a negative-width bar.
 * When the top score is `0` (or no sources), every bar fraction is `0`.
 *
 * @param sources - The reranked sources in any order.
 * @param topK - Optional cap on how many top results to return.
 * @returns Ranked sources, highest-first, capped to `topK` when provided.
 */
export function rankSources(sources: RankInput[], topK?: number): RankedSource[] {
  const cleaned = sources.map((s) => ({
    ...s,
    score: Number.isFinite(s.score) ? Math.max(0, s.score) : 0,
  }))
  const sorted = [...cleaned].sort((a, b) => b.score - a.score)
  const top = sorted.length > 0 ? sorted[0].score : 0
  const limited = typeof topK === 'number' ? sorted.slice(0, Math.max(0, topK)) : sorted
  return limited.map((s, i) => ({
    ...s,
    rank: i + 1,
    relative: top > 0 ? s.score / top : 0,
  }))
}
