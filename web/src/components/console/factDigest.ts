/**
 * The memory rail's digest — a count, the most relevant few, and the rest on request.
 *
 * ## Why this exists
 *
 * The rail used to render `SemanticFactsPanel` whole: every fact the store holds, each
 * as its own card, in a 21rem column. That panel is correct where it lives — the Memory
 * section, where the facts *are* the content — and wrong here, where they are context
 * beside a question box. Four facts made the console 900px tall on a 900px screen; a
 * subject with a dozen made the idle console scroll for several viewports with no run,
 * no answer and nothing happening. The rail was the tallest thing on a screen whose only
 * job is to take a question.
 *
 * So the rail shows a **digest**: how many facts are current, the few most likely to
 * matter, and a way to see the rest. This module is the "which few" decision, kept pure
 * so it can be tested without a store or a renderer.
 *
 * ## What "most relevant" means, and why it is not a score we invented
 *
 * Every field used here is the store's own, from `GET /memory/facts`:
 *
 * 1. **Recalled most often** (`access_count`) — the store counts each time a fact was
 *    pulled into a turn's working memory. A fact the agent keeps reaching for is, by the
 *    agent's own behaviour, the relevant one. This is the primary key and it is a
 *    measurement, not a heuristic.
 * 2. **Most important** (`importance`) — the extraction's own salience weight.
 * 3. **Most confident** (`confidence`).
 * 4. **Newest** (`id`), so the order is total and the list never reshuffles between
 *    renders for two facts that tie on everything else.
 *
 * Superseded facts are never in the digest. A belief the store has already replaced is
 * history, and history belongs behind the expansion with its validity window, not in a
 * three-line summary of what the agent currently believes.
 */

import type { MemoryFactRow } from '@/lib/api/memory'

/** How many facts the digest shows before "show all". Three lines, not a list. */
export const DIGEST_SIZE = 3

/** What the rail's header states, all of it counted from the store's own rows. */
export interface FactDigest {
  /** Facts the store still holds as true. */
  current: number
  /** Facts a later belief replaced. Kept, never shown in the summary. */
  superseded: number
  /** The most-recalled current facts, at most {@link DIGEST_SIZE}. */
  top: MemoryFactRow[]
  /** Current facts the digest is not showing — the number behind "show all". */
  hidden: number
  /**
   * How many times the agent has pulled any of these facts into a turn, summed from
   * `access_count`. Zero is a fact about a fresh subject, not a missing figure.
   */
  recalls: number
}

/**
 * Rank current facts by how much the agent has actually used them.
 *
 * Descending on recall count, then importance, then confidence, then newest. Pure and
 * total: two calls on the same rows give the same order.
 */
export function rankFacts(rows: readonly MemoryFactRow[]): MemoryFactRow[] {
  return rows
    .filter((row) => row.is_valid)
    .slice()
    .sort(
      (a, b) =>
        b.access_count - a.access_count ||
        b.importance - a.importance ||
        b.confidence - a.confidence ||
        b.id - a.id,
    )
}

/**
 * The digest for one `GET /memory/facts` reading.
 *
 * @param rows - Every fact the store returned, valid and superseded alike.
 * @param size - How many to show. Defaults to {@link DIGEST_SIZE}.
 */
export function digestFacts(
  rows: readonly MemoryFactRow[],
  size: number = DIGEST_SIZE,
): FactDigest {
  const ranked = rankFacts(rows)
  const top = ranked.slice(0, Math.max(0, size))
  return {
    current: ranked.length,
    superseded: rows.length - ranked.length,
    top,
    hidden: Math.max(0, ranked.length - top.length),
    recalls: ranked.reduce((sum, row) => sum + row.access_count, 0),
  }
}

/**
 * The line under the digest's count, or `null` when there is nothing to add.
 *
 * It never estimates. `0×` means the store counted zero recalls, which is what a subject
 * whose facts were written but never read back looks like, and saying so is more useful
 * than hiding the row.
 */
export function recallLine(digest: FactDigest): string | null {
  if (digest.current === 0) return null
  if (digest.recalls === 0) return 'none recalled into a turn yet'
  return `${digest.recalls.toLocaleString('en-US')} recalls into a turn so far`
}
