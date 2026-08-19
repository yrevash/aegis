/**
 * Why there is no answer — read from the run, never guessed from its status.
 *
 * The Answer tab is the default tab, so whatever it says on a stopped run is the first
 * and often only explanation a person gets. It used to key one sentence — *"Rejected at
 * the human gate — no answer generated."* — on `finishedStatus === 'blocked'`, and that
 * status covers a guardrail block, a budget refusal, a rejected gate and anything else
 * the graph terminates on. An audit caught it accusing a human of rejecting a run that
 * the **input rail** stopped because the injection classifier was unreachable — while
 * the real reason travelled on the event, verbatim, and rendered correctly one tab over
 * in Trace.
 *
 * So the reason is read off the run's own log, newest cause first, because the last
 * thing that stopped a run is what stopped it. Every branch carries the run's own words
 * as `detail` rather than paraphrasing them: the backend already said why, and a second
 * copy in this file is a second thing that can drift.
 *
 * Pure, so `web/tests/console/answerAbsence.test.mjs` can script a run and read the
 * sentence without a renderer.
 */

import type { RunState } from '@/state/runReducer'

/** What the Answer tab says in place of an answer. */
export interface AnswerAbsence {
  /**
   * True once the run can no longer produce an answer. The panel reads this to choose
   * its tone: a run still in flight is a promise, a stopped one is a finding.
   */
  stopped: boolean
  /** One sentence: what happened. */
  headline: string
  /** The run's own words for it, or `''` when it carried none to quote. */
  detail: string
}

/** How each rail is named to a person. Sentence case; the wire's word is not English. */
const RAIL: Record<string, string> = {
  input: 'the input rail',
  output: 'the output rail',
  tool_result: 'the tool-output rail',
}

/** The run is still going, or has not started. */
const PENDING: AnswerAbsence = {
  stopped: false,
  headline: 'The answer streams here once the agent responds.',
  detail: '',
}

/**
 * What to show when a run produced no answer text.
 *
 * Only called when `state.answer` is empty — a run that streamed anything at all shows
 * what it streamed, and a partial answer is still an answer.
 *
 * @param state - The reduced run.
 * @returns The sentence to render, and whether the run can still change it.
 */
export function answerAbsence(state: RunState): AnswerAbsence {
  // A fatal error outranks everything: the run did not reach a verdict, it fell over.
  // This is also the backend-went-away case, where the Answer tab used to promise a
  // stream that could never come while the error sat above it.
  if (state.error !== null) {
    return {
      stopped: true,
      headline: 'The run stopped before an answer was generated.',
      detail: state.error,
    }
  }

  if (state.phase === 'blocked' || state.finishedStatus === 'blocked') {
    return { stopped: true, ...blockedBecause(state) }
  }

  if (state.finishedStatus === 'completed') {
    return {
      stopped: true,
      headline: 'The run finished without generating an answer.',
      detail: '',
    }
  }

  // The transport ended with no terminal event — a dropped connection. `running` was
  // cleared by the close, and there is nothing else coming.
  if (!state.running && state.events.length > 0) {
    return {
      stopped: true,
      headline: 'The stream ended before an answer arrived.',
      detail: '',
    }
  }

  return PENDING
}

/** The newest cause on the log that explains a block, or the honest fallback. */
function blockedBecause(state: RunState): { headline: string; detail: string } {
  for (let i = state.events.length - 1; i >= 0; i -= 1) {
    const event = state.events[i]

    if (event.type === 'budget_exceeded') {
      return {
        headline: 'Stopped at the spend cap — no answer generated.',
        detail: event.message,
      }
    }

    if (event.type === 'guardrail' && event.verdict === 'block') {
      const rail = RAIL[event.stage] ?? 'a guardrail'
      const layer = event.layer === null ? '' : ` (${event.layer})`
      return {
        headline: `Blocked by ${rail}${layer} — no answer generated.`,
        detail: event.reason,
      }
    }
  }

  // Nothing on the log explains it, and the run did pause for a person. That is the one
  // case the old copy was right about — kept, now that it is the last resort and not
  // the first guess.
  if (state.awaitedApproval) {
    return {
      headline: 'Rejected at the human gate — no answer generated.',
      detail: '',
    }
  }

  return {
    headline: 'The run was stopped before an answer was generated.',
    detail: 'Open the Trace tab to see which step ended it.',
  }
}
