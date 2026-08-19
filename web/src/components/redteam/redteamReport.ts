/**
 * Reading a red-team report: the framing rules, and the comparison to last time.
 *
 * Pure, and separate from the view, because two of the three things here are claims
 * about honesty rather than presentation, and a claim about honesty deserves a test:
 *
 * 1. **What an offline block rate is allowed to say.** The battery keeps probes no
 *    deterministic signature can catch by design. Offline they leak, and the headline
 *    is *"our signatures blocked N of M"* — never *"Aegis blocks N%"*. `headline`
 *    writes that sentence from the run's own `mode`, so the screen cannot quietly
 *    upgrade the claim.
 *
 * 2. **A 100% block rate is a finding, not a trophy.** A red team that reports
 *    everything blocked is either over-blocking or testing nothing, and the report has
 *    the evidence to say which: the false-positive rate over the benign controls. So
 *    `verdictNote` says which one it is instead of showing a green tick.
 *
 * 3. **What changed since last time.** A block rate on its own is unreadable — 82% is
 *    good or catastrophic depending on last week — so `compareRuns` returns the deltas
 *    against the previous run of the same suite, and `null` when there is no previous
 *    run rather than a zero somebody would draw an arrow from.
 */

import type { RedteamProbe, RedteamRun } from '@/lib/api/redteam'

/** A signed change between two runs, with the direction that counts as better. */
export interface RunDelta {
  /** Current value, 0–1. */
  value: number
  /** Previous value, 0–1. */
  previous: number
  /** `value - previous`. */
  change: number
  /** True when the change moved in the direction that is better for this measure. */
  improved: boolean
  /** True when nothing moved at all. */
  unchanged: boolean
}

/** The comparison between a run and the one before it. */
export interface RunComparison {
  blockRate: RunDelta
  falsePositiveRate: RunDelta
  /** Attacks that leaked this time and did not last time — the regressions. */
  attacksLeakedDelta: number
  previousRunId: string
  previousAt: string | null
}

function delta(value: number, previous: number, higherIsBetter: boolean): RunDelta {
  const change = value - previous
  return {
    value,
    previous,
    change,
    improved: higherIsBetter ? change > 0 : change < 0,
    unchanged: change === 0,
  }
}

/**
 * Compare a run against the previous run of the same suite.
 *
 * @param run - The run just shown.
 * @param previous - The run before it, or null when this is the first of its suite.
 * @returns The deltas, or `null` when there is nothing to compare against.
 */
export function compareRuns(run: RedteamRun, previous: RedteamRun | null): RunComparison | null {
  if (previous == null) return null
  const leakedNow = run.attacksTotal - run.attacksBlocked
  const leakedBefore = previous.attacksTotal - previous.attacksBlocked
  return {
    blockRate: delta(run.blockRate, previous.blockRate, true),
    falsePositiveRate: delta(run.falsePositiveRate, previous.falsePositiveRate, false),
    attacksLeakedDelta: leakedNow - leakedBefore,
    previousRunId: previous.runId,
    previousAt: previous.startedAt,
  }
}

/**
 * The sentence a block rate is allowed to be reported as, for this run's mode.
 *
 * Offline runs wire no completer, so only the deterministic signatures fired and the
 * claim is about those signatures. A live run drove the model layers too, so the claim
 * is about the stack. The two are different claims about different things and the
 * screen must not blur them.
 */
export function headline(run: Pick<RedteamRun, 'mode' | 'attacksBlocked' | 'attacksTotal'>): string {
  const subject =
    run.mode === 'live' ? 'The full rail stack blocked' : 'The deterministic signatures blocked'
  return `${subject} ${run.attacksBlocked} of ${run.attacksTotal} attacks`
}

/**
 * What the pass/fail verdict actually means, including when it means nothing good.
 *
 * A perfect block rate is reported as a finding: with benign controls being blocked it
 * is over-blocking, and with no attacks at all it is a battery that tested nothing.
 * Only a perfect run that let every benign control through has earned the sentence.
 */
export function verdictNote(
  run: Pick<
    RedteamRun,
    'attacksTotal' | 'blockRate' | 'falsePositiveRate' | 'controlsTotal' | 'passed'
  >,
): string {
  if (run.attacksTotal === 0) {
    return 'No attacks ran, so this reports nothing. A block rate over an empty battery is not a result.'
  }
  if (run.blockRate === 1) {
    if (run.falsePositiveRate > 0) {
      return 'Everything was blocked — and so were benign controls. This is over-blocking, not coverage.'
    }
    if (run.controlsTotal === 0) {
      return 'Everything was blocked and no benign control ran, so there is nothing here that could have failed.'
    }
    return `Everything was blocked and all ${run.controlsTotal} benign controls passed.`
  }
  return run.passed
    ? 'Clears the floor this run was judged against.'
    : 'Below the floor this run was judged against.'
}

/**
 * Group the probes that got through by whether the battery expected them to.
 *
 * A probe marked `needsLlm` has no deterministic signature *by design*, so an offline
 * leak of it is a known gap in the offline configuration rather than a hole in the
 * product. One that is not so marked is a real miss, and merging the two into a single
 * "leaked" count is how a report stops distinguishing them.
 */
export function splitLeaks(probes: RedteamProbe[]): {
  expected: RedteamProbe[]
  unexpected: RedteamProbe[]
} {
  return {
    expected: probes.filter((p) => p.needsLlm),
    unexpected: probes.filter((p) => !p.needsLlm),
  }
}

/** Human label for a snake_case category or stage id. */
export function label(id: string): string {
  return id.replace(/_/g, ' ')
}

/** Round a 0–1 rate to a whole-percent string. */
export function pct(rate: number): string {
  return `${Math.round(rate * 100)}%`
}

/** A signed percentage-point string, e.g. `+4 pts` / `−2 pts` / `no change`. */
export function points(change: number): string {
  const rounded = Math.round(change * 100)
  if (rounded === 0) return 'no change'
  return `${rounded > 0 ? '+' : '−'}${Math.abs(rounded)} pts`
}

/** USD, at the precision an estimate of a few cents actually has. */
export function usd(amount: number): string {
  if (amount === 0) return '$0.00'
  if (amount < 0.01) return `<$0.01`
  return `$${amount.toFixed(2)}`
}
