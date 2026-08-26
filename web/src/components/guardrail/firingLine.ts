/**
 * The firing line, as arithmetic — no React, no fetch, no clock.
 *
 * The panel above this file fires real red-team payloads at the real input rail one at a
 * time and draws each verdict as it lands. Everything about *what the marks mean* lives
 * here so it can be tested without a DOM: which probes are honest to fire, how a verdict
 * becomes a plotted point, and — the one that matters — what the block rate is a rate
 * **of**.
 *
 * **The block rate is `blocked / fired`, never `blocked / probes`.** A run stopped
 * halfway, or a backend that died on probe four, would otherwise report a rate measured
 * against probes that were never sent: seven blocks out of a battery of forty reads as
 * 18%, when what actually happened is seven blocks out of seven fired. The denominator
 * is the count of probes that got an answer of any kind, and it grows as the line fires.
 *
 * `unchecked` is kept **outside** `blocked`, on exactly the reasoning
 * `RedteamRun.attacksUnchecked` documents: a stream that closed without a verdict
 * refused the probe without examining it, and counting that as a block would let a rail
 * outage report itself as perfect security.
 */

/** One rail card this panel can join a verdict to. Structurally the `INPUT_RAILS` shape. */
export interface FiringRail {
  /** The `layer` string the rail streams on its verdict. */
  layer: string
  name: string
}

/** One adversarial payload, taken verbatim from a stored red-team run. */
export interface FiringProbe {
  id: string
  /** The real payload. Never composed here, never invented. */
  prompt: string
  category: string
  owasp: string
}

/**
 * What came back from firing one probe.
 *
 * Three outcomes, not two. `silent` is the shape of a rail that raised: the
 * demonstrator stream has no RUN_ERROR frame, so a failure looks like a stream that
 * stops after STEP_STARTED. Folding it into `failed` would blame the network for a
 * backend fault; folding it into a verdict would invent one.
 */
export type FiringOutcome =
  | {
      kind: 'verdict'
      verdict: string
      /** The deciding layer, or null when no rail claimed it. */
      layer: string | null
      rationale: string
      /** The rail's own measured milliseconds — `per_rail_timing_ms.total`. */
      totalMs: number
      /** PII kinds masked. Never values. */
      redactions: string[]
    }
  | { kind: 'silent' }
  | { kind: 'failed'; message: string }

/** One probe's result, in the order it landed. */
export interface FiringArrival {
  probeId: string
  outcome: FiringOutcome
}

/** One plotted mark: a probe, what the rail said, and the figure on the y-axis. */
export interface FiringPoint {
  /** 1-based position in the firing order — the x-axis. */
  index: number
  probe: FiringProbe
  outcome: FiringOutcome
  /** The rail's own milliseconds, or null when nothing was measured. */
  totalMs: number | null
  /** The verdict word, or null when no verdict arrived. */
  verdict: string | null
  layer: string | null
  /** Whether this probe was stopped. `silent` and `failed` are never blocked. */
  blocked: boolean
}

/** Everything the panel draws, derived once from the probes and what has landed. */
export interface FiringSummary {
  points: FiringPoint[]
  /** Probes that got an answer of any kind. The block-rate denominator. */
  fired: number
  /** Probes the rail stopped: verdict `block`. */
  blocked: number
  /** Probes refused unexamined — a stream that closed with no verdict, or a failure. */
  unchecked: number
  /** `blocked / fired`, or null before anything has been fired. */
  blockRate: number | null
  /** The slowest measured rail time, or null when nothing measured. */
  peakMs: number | null
  /** How many probes each rail decided, most active first. Joins to a rail card. */
  byRail: { layer: string; name: string; count: number }[]
  /** Probes selected but not yet fired. */
  remaining: number
}

/** Verdicts this build knows how to colour. Anything else degrades to a legible marker. */
const KNOWN_VERDICTS = new Set(['pass', 'block', 'redact', 'flag'])

/** Whether a verdict word is one this build has a treatment for. */
export function isKnownVerdict(verdict: string): boolean {
  return KNOWN_VERDICTS.has(verdict)
}

/**
 * Which probes it is honest to fire standalone at the input rail.
 *
 * Three exclusions, each for a different reason:
 *
 * - `stage !== 'input'` — an output or `tool_result` probe screened by `check_input` is
 *   being measured by the wrong rail, and its verdict would say nothing about either.
 * - `category === 'benign_control'` — a control exists to be *let through*. Mixing
 *   controls into a firing line makes the block rate a blend of two measurements.
 * - a **burst** probe — one whose `prompt` is a single representative query out of a
 *   burst (`RedteamProbe.burstQueries`). The battery stages these as `sequence`, which
 *   the first rule already excludes; the explicit `burstQueries` guard is what keeps the
 *   exclusion true if a burst probe is ever staged `input`. Firing one query alone
 *   measures something the battery never claimed a rail would catch on its own, so a
 *   `pass` here would be read as a leak that is not one.
 *
 * @param attacks - `report.attacks` from a stored run, in run order.
 * @returns The fireable probes, order preserved.
 */
export function selectProbes(
  attacks: {
    id: string
    prompt: string
    category: string
    owasp: string
    stage: string
    burstQueries?: number
  }[],
): FiringProbe[] {
  return attacks
    .filter(
      (a) =>
        a.stage === 'input' &&
        a.category !== 'benign_control' &&
        (a.burstQueries ?? 0) === 0 &&
        typeof a.prompt === 'string' &&
        a.prompt.length > 0,
    )
    .map((a) => ({ id: a.id, prompt: a.prompt, category: a.category, owasp: a.owasp }))
}

/**
 * Choose the firing order from the committed battery extract.
 *
 * Two properties the demo depends on, both of which source order fails:
 *
 * - **Spread.** `ATTACK_BATTERY` is grouped by category, so the first twelve entries are
 *   two or three categories deep and the line would report the rail's behaviour on a
 *   narrow slice of the battery as though it were the battery. Categories are taken
 *   round-robin instead, so twelve probes span as much of it as twelve can.
 * - **Discrimination.** A line on which every probe blocks has not shown a rail that
 *   *decides* — it is indistinguishable from a rail that refuses everything. The
 *   battery's own `benign_control` payloads are the answer it already ships, so one is
 *   folded in after every third adversarial probe and is expected to pass.
 *
 * Deterministic: no clock, no randomness, same input to same order every press.
 *
 * @param all - The committed extract, in its generated order.
 * @param limit - How many to fire.
 * @returns The probes to fire, in order, at most `limit` of them.
 */
export function selectBatteryProbes(
  all: readonly { id: string; prompt: string; category: string; owasp: string; benign: boolean }[],
  limit: number,
): FiringProbe[] {
  const usable = all.filter((p) => typeof p.prompt === 'string' && p.prompt.length > 0)
  const benign = usable.filter((p) => p.benign)
  const groups: { category: string; items: typeof usable }[] = []
  for (const probe of usable) {
    if (probe.benign) continue
    const found = groups.find((g) => g.category === probe.category)
    if (found === undefined) groups.push({ category: probe.category, items: [probe] })
    else found.items.push(probe)
  }

  const out: FiringProbe[] = []
  const shape = (p: (typeof usable)[number]): FiringProbe => ({
    id: p.id,
    prompt: p.prompt,
    category: p.category,
    owasp: p.owasp,
  })
  let benignTaken = 0
  let round = 0
  // Round-robin until the adversarial groups are exhausted or the cap is reached.
  while (out.length < limit && groups.some((g) => round < g.items.length)) {
    for (const group of groups) {
      if (out.length >= limit) break
      const probe = group.items[round]
      if (probe === undefined) continue
      out.push(shape(probe))
      // One control after every third attack, while controls remain.
      if (out.length % 4 === 3 && benignTaken < benign.length && out.length < limit) {
        const control = benign[benignTaken]
        if (control !== undefined) {
          out.push(shape(control))
          benignTaken += 1
        }
      }
    }
    round += 1
  }
  return out.slice(0, limit)
}

/**
 * Fold the probes and whatever has landed so far into the marks and the figures.
 *
 * Pure and total: called on every arrival, and correct at every prefix of the run —
 * which is what makes the numbers safe to show *while* the line is still firing.
 *
 * @param probes - The selected probes, in firing order.
 * @param arrivals - Results so far, in the order they landed.
 * @param rails - The rail cards to join a deciding layer to, for `byRail`.
 * @returns The plotted points and the summary figures.
 */
export function firingLine(
  probes: FiringProbe[],
  arrivals: FiringArrival[],
  rails: FiringRail[] = [],
): FiringSummary {
  const byId = new Map(probes.map((p) => [p.id, p]))
  const railName = new Map(rails.map((r) => [r.layer, r.name]))

  const points: FiringPoint[] = []
  for (const [i, arrival] of arrivals.entries()) {
    const probe = byId.get(arrival.probeId)
    // An arrival with no probe behind it cannot be drawn honestly — there is no payload
    // to name and no category to attribute it to — so it is dropped rather than plotted
    // against a placeholder.
    if (probe === undefined) continue
    const outcome = arrival.outcome
    points.push({
      index: i + 1,
      probe,
      outcome,
      totalMs:
        outcome.kind === 'verdict' && Number.isFinite(outcome.totalMs) ? outcome.totalMs : null,
      verdict: outcome.kind === 'verdict' ? outcome.verdict : null,
      layer: outcome.kind === 'verdict' ? outcome.layer : null,
      blocked: outcome.kind === 'verdict' && outcome.verdict === 'block',
    })
  }

  const fired = points.length
  const blocked = points.filter((p) => p.blocked).length
  const unchecked = points.filter((p) => p.outcome.kind !== 'verdict').length
  const measured = points.map((p) => p.totalMs).filter((ms): ms is number => ms !== null)

  const counts = new Map<string, number>()
  for (const point of points) {
    if (point.layer === null) continue
    counts.set(point.layer, (counts.get(point.layer) ?? 0) + 1)
  }

  return {
    points,
    fired,
    blocked,
    unchecked,
    // Before anything is fired there is no rate — not zero. Zero is a measurement.
    blockRate: fired === 0 ? null : blocked / fired,
    peakMs: measured.length === 0 ? null : Math.max(...measured),
    byRail: [...counts.entries()]
      .map(([layer, count]) => ({ layer, name: railName.get(layer) ?? layer, count }))
      .sort((a, b) => b.count - a.count || a.layer.localeCompare(b.layer)),
    remaining: Math.max(0, probes.length - fired),
  }
}
