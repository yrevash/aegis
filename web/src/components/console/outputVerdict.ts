/**
 * What the output rails said about this answer, as one verdict.
 *
 * The answer panel used to compute `passed = verdict === 'pass'` and print
 * `passed ? 'output checked' : 'output blocked'`. That collapses four verdicts into two,
 * and it puts the words **output blocked** above a fully rendered answer whenever a rail
 * redacted or flagged — three different facts a reader acts on differently:
 *
 * - `flag` — advisory. The rail noted something and let the answer through unchanged.
 * - `redact` — the text on screen **was altered**; something was masked out of it.
 * - `block` — the answer was withheld. There is nothing to read.
 *
 * A run can carry several output-stage rails (content safety and grounding both report
 * here), so the badge shows the **strongest** of them: a `pass` from one rail must never
 * hide a `redact` from another.
 */

import type { Guardrail, GuardVerdict } from '@/lib/stream'

/**
 * Least to most consequential. Higher wins when rails disagree.
 *
 * Keyed by `string`, not `GuardVerdict`, because the verdict arrives over the wire from a
 * backend that can ship a new one before this client is rebuilt — an unknown code must
 * read as `undefined` here rather than as a type-checked zero.
 */
const SEVERITY: Record<string, number | undefined> = { pass: 0, flag: 1, redact: 2, block: 3 }

/** The rank an unrecognised verdict takes: above everything, because it is unknown. */
const UNKNOWN_SEVERITY = Number.MAX_SAFE_INTEGER

/** How each verdict is worded and toned in the answer header. */
export interface VerdictBadge {
  verdict: GuardVerdict | string
  /** The signal token — the reserved status palette, never a new hue. */
  tone: 'ok' | 'risk' | 'block'
  label: string
}

const BADGES: Record<string, Omit<VerdictBadge, 'verdict'> | undefined> = {
  pass: { tone: 'ok', label: 'output checked' },
  flag: { tone: 'risk', label: 'output flagged' },
  redact: { tone: 'risk', label: 'output redacted' },
  block: { tone: 'block', label: 'output blocked' },
}

/**
 * The verdict to show for a run, or `null` when no output rail reported.
 *
 * An unrecognised verdict degrades to its own raw code at the cautious tone rather than
 * crashing or being silently read as a pass — the wire can ship a verdict this build has
 * never heard of, and `GuardrailReveal` learned that the hard way.
 *
 * @param guardrails - Every guardrail verdict the run emitted, in order.
 * @returns The strongest output-stage verdict, or `null` if no rail screened the output.
 */
export function outputVerdict(guardrails: readonly Guardrail[]): VerdictBadge | null {
  const output = guardrails.filter((g) => g.stage === 'output')
  if (output.length === 0) return null

  const rank = (g: Guardrail): number => SEVERITY[g.verdict] ?? UNKNOWN_SEVERITY
  const worst = output.reduce((a, b) => (rank(b) > rank(a) ? b : a))
  const known = BADGES[worst.verdict]
  if (known === undefined) {
    return { verdict: worst.verdict, tone: 'risk', label: `output ${worst.verdict}` }
  }
  return { verdict: worst.verdict, ...known }
}
