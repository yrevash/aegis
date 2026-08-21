/**
 * The two honest distributions a knowledge graph can be asked for — and a note
 * about where the numbers come from, because it is the whole point of this file.
 *
 * `GET /graph` returns `{ nodes: [{id, kind, label}], edges: [{relation, source,
 * target}] }` and **not one numeric field**. So every figure this screen draws is
 * computed here, in the browser, from the shape of what came back. That is a
 * legitimate thing to chart — a degree is a fact about the returned edge list, not
 * a claim about the backend's own measurements — but it has to be *said*, which is
 * why every caller of these functions closes its card with a `Receipt` naming the
 * derivation rather than a route.
 *
 * Nothing here invents a series over time. There is no timestamp anywhere in the
 * graph contract, so there is no trend to draw and none is offered.
 */

/** One node's connectivity in the currently-painted view. */
export interface DegreeRow {
  degree: number
}

/** A bar of the degree histogram: how many entities carry this many relations. */
export interface DegreeBar {
  /** The degree, or a range label once the tail is too long to draw one bar each. */
  degree: string
  entities: number
}

/** A bar of the kind breakdown: how many entities of one type are in view. */
export interface KindBar {
  kind: string
  entities: number
}

/** Above this many distinct degrees the histogram buckets instead of listing. */
const MAX_DEGREE_BARS = 12

/** Kinds drawn individually; the rest fold into one named `Other (n)` bar. */
const MAX_KIND_BARS = 6

/**
 * How connected the entities in view are — a count of entities per degree.
 *
 * A degree of zero is kept rather than dropped: an isolated entity is the most
 * interesting bar on this chart, because it is a node the retriever cannot reach
 * by traversal, and a histogram that silently starts at one hides it.
 *
 * Past {@link MAX_DEGREE_BARS} distinct degrees the axis becomes equal-width
 * ranges. Ranges are labelled with both bounds (`13–18`), never with a bare
 * midpoint, so a reader is never shown a number no entity actually has.
 */
export function degreeDistribution(rows: readonly DegreeRow[]): DegreeBar[] {
  if (rows.length === 0) return []
  const max = rows.reduce((m, r) => Math.max(m, r.degree), 0)

  if (max < MAX_DEGREE_BARS) {
    const counts = Array.from({ length: max + 1 }, () => 0)
    for (const r of rows) counts[Math.max(0, r.degree)] += 1
    return counts.map((entities, degree) => ({ degree: String(degree), entities }))
  }

  const width = Math.ceil((max + 1) / MAX_DEGREE_BARS)
  const buckets = Array.from({ length: MAX_DEGREE_BARS }, () => 0)
  for (const r of rows) {
    const i = Math.min(MAX_DEGREE_BARS - 1, Math.floor(Math.max(0, r.degree) / width))
    buckets[i] += 1
  }
  return buckets.map((entities, i) => {
    const lo = i * width
    const hi = Math.min(max, lo + width - 1)
    return { degree: lo === hi ? String(lo) : `${lo}–${hi}`, entities }
  })
}

/**
 * Entities per type, biggest first, with the tail past {@link MAX_KIND_BARS}
 * folded into one stated `Other (n)` bar rather than dropped or drawn in a colour
 * the palette does not have.
 */
export function kindCounts(kinds: readonly string[]): KindBar[] {
  const tally = new Map<string, number>()
  for (const kind of kinds) tally.set(kind, (tally.get(kind) ?? 0) + 1)

  const sorted = [...tally.entries()]
    .map(([kind, entities]) => ({ kind, entities }))
    .sort((a, b) => b.entities - a.entities || a.kind.localeCompare(b.kind))

  if (sorted.length <= MAX_KIND_BARS) return sorted

  const head = sorted.slice(0, MAX_KIND_BARS)
  const tail = sorted.slice(MAX_KIND_BARS)
  const rest = tail.reduce((sum, k) => sum + k.entities, 0)
  return [...head, { kind: `Other (${tail.length})`, entities: rest }]
}
