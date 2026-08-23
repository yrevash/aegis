/**
 * The four claims the product makes about a run, as four predicates over the run itself.
 *
 * "Every autonomous action is grounded, guarded, approved, and fully traced." Each of
 * these is decidable from the run's own events, and each is drawn as a lit check in the
 * run header. They live here rather than inside `RunStages.tsx` for one reason: a chip
 * that lights when the thing it names did not happen is the worst defect this console can
 * ship, and a predicate in a `.tsx` file beside a `<li>` is a predicate nobody tests.
 */

import type { Signal } from '@/config/signals'
import type { RunState } from '@/state/runReducer'

/** One check in the trust row. */
export interface TrustCheck {
  key: string
  label: string
  signal: Signal
  done: (s: RunState) => boolean
}

/**
 * Whether this answer is grounded in something the run actually retrieved.
 *
 * Two ways this has been wrong, both of them the chip claiming an assurance about
 * nothing:
 *
 * 1. A run stopped at the *input* rail retrieved nothing and generated nothing, so there
 *    is no answer to ground. Hence `answer.length > 0` — without it "Grounded" appeared
 *    directly above "Blocked by the input rail — no answer generated".
 * 2. A run that answered with **zero retrieval** still lit, because the predicate also
 *    accepted `provenance != null` — and a provenance record is emitted on every run,
 *    including one that retrieved nothing. So the strip said `Grounded ✓` while the
 *    Sources panel on the same run said "This run retrieved nothing, so the answer is not
 *    grounded in a document". That disjunct is gone.
 *
 * The authority, when it ran, is the **grounding rail's own verdict** — the backend
 * self-check that judges whether the answer's claims are entailed by the retrieved
 * passages, and which now reports `flag` rather than `pass` when there were no passages
 * at all. Only when no grounding rail ran does this fall back to the weaker structural
 * question: did retrieval return anything for the answer to stand on?
 */
export function isGrounded(s: RunState): boolean {
  if (s.answer.length === 0) return false
  const grounding = s.guardrails.find((g) => g.layer === 'grounding')
  if (grounding !== undefined) return grounding.verdict === 'pass'
  return s.retrievalScores.length > 0
}

export const TRUST_CHECKS: readonly TrustCheck[] = [
  { key: 'grounded', label: 'Grounded', signal: 'graph', done: isGrounded },
  {
    key: 'guarded',
    label: 'Guarded',
    signal: 'block',
    done: (s) => s.guardrails.some((g) => g.stage === 'output') || s.finishedStatus === 'blocked',
  },
  {
    key: 'approved',
    label: 'Human-approved',
    signal: 'risk',
    // Only lights when the run actually paused at the gate AND a human approval let a
    // tool succeed — a rejected action (tool_result ok=false) stays dark.
    done: (s) => s.awaitedApproval && s.toolResults.some((r) => r.ok),
  },
  // `traced` stays true for a blocked run on purpose: the run *was* fully recorded, and
  // that a refusal is traceable is exactly the claim worth making about it.
  { key: 'traced', label: 'Fully traced', signal: 'agent', done: (s) => s.finishedStatus != null },
]
