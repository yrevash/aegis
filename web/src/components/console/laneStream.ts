/**
 * Per-lane reasoning, so a fan-out's thinking shows up *inside* the lane doing it.
 *
 * `RunState.reasoning` is one accumulated string for the whole run, which is right for a
 * single-pass turn and wrong for a team: four agents thinking concurrently collapse into
 * one paragraph where nobody can tell who said what, and the parallelism — the single
 * most impressive thing the backend does — reads as one slow monologue.
 *
 * Every event carries an optional `agent_id` on `BaseEvent`, so the split is a read, not
 * a guess. Events with no identity belong to the supervisor lane, which is exactly what
 * {@link deriveAgentPanel} does with them, so the two agree by construction.
 *
 * Kept separate from `agentLanes.ts` deliberately: that module is the tested derivation
 * of *lane state*, and a wall of streaming text is presentation with a different
 * lifetime (it is trimmed for the card, and the trace keeps the full log regardless).
 */

import type { RunState } from '@/state/runReducer'

import { SUPERVISOR_LANE } from './agentLanes'
import { agentIdOf } from './eventViews'

/**
 * A chunk shorter than this is treated as a fragment of a step rather than a step.
 *
 * The shipped backend emits one sentence per chunk, all of them far longer than this;
 * the threshold exists only so a future token-level stream re-forms into sentences
 * instead of into one bullet per word.
 */
const FRAGMENT_CHARS = 12

/** Text that has finished a sentence, allowing for a closing quote or bracket. */
const SENTENCE_END = /[.!?]["'”’)\]]?\s*$/

/**
 * The reasoning text each lane has produced so far, keyed by lane id.
 *
 * Returns an empty map when nothing has reasoned yet, so a caller renders no lane body
 * rather than an empty scroller.
 */
export function reasoningByLane(state: RunState): Map<string, string> {
  const out = new Map<string, string>()
  for (const event of state.events) {
    if (event.type !== 'reasoning') continue
    const lane = agentIdOf(event) ?? SUPERVISOR_LANE
    out.set(lane, (out.get(lane) ?? '') + event.text)
  }
  return out
}

/**
 * The reasoning **chunks** each lane produced, in arrival order, keyed by lane id.
 *
 * The chunk boundary is the only reliable step boundary there is, and this is why:
 * the wire does not put separators between chunks. A real run streamed
 *
 * ```
 * '…any knowledge documents that might help answer this question'
 * 'Let me look into this properly.'
 * 'First, I need to check if there are any relevant service requests…'
 * ```
 *
 * and the console concatenated them into `…this questionLet me look into this
 * properly.First, I need to check…` — three decisions welded into one wall, with two of
 * the three joins carrying no whitespace and one carrying no punctuation either. No
 * amount of sentence-splitting recovers a break that is not in the text. The events are.
 */
export function reasoningChunksByLane(state: RunState): Map<string, string[]> {
  const out = new Map<string, string[]>()
  for (const event of state.events) {
    if (event.type !== 'reasoning') continue
    const lane = agentIdOf(event) ?? SUPERVISOR_LANE
    const chunks = out.get(lane)
    if (chunks === undefined) out.set(lane, [event.text])
    else chunks.push(event.text)
  }
  return out
}

/**
 * A lane's reasoning chunks, turned into the steps they describe.
 *
 * One step per `reasoning` event, because that is where the backend put the breaks:
 * `graph.py` splits the plan into sentences before it emits, so a chunk *is* a decision.
 * The console used to concatenate them and render the result as one paragraph, and the
 * owner's verdict on that was the plainest possible — *"the reasoning renders as one
 * run-on wall"*. It did, and no delimiter survived the join to split it back apart.
 *
 * The one thing this must not do is assume that chunking stays sentence-sized. If the
 * backend ever streams reasoning token by token, one bullet per token would be worse
 * than the wall. So a chunk shorter than {@link FRAGMENT_CHARS} is treated as a
 * fragment: it accumulates, and the step closes when the accumulated text finishes a
 * sentence. Sentence-sized chunks are unaffected, token-sized ones re-form into
 * sentences, and nothing in between is edited.
 *
 * @param chunks - The lane's `reasoning` texts, in arrival order.
 * @returns One entry per step, in order. Empty for no chunks.
 */
export function reasoningSteps(chunks: readonly string[]): string[] {
  const steps: string[] = []
  let buffer = ''

  const flush = (): void => {
    const text = buffer.trim()
    if (text !== '') steps.push(text)
    buffer = ''
  }

  for (const chunk of chunks) {
    // A newline inside a chunk is a break its author put there; honour it first.
    const lines = chunk.split(/\n+/)
    lines.forEach((line, index) => {
      buffer += line
      if (index < lines.length - 1) flush()
    })
    const tail = lines.at(-1) ?? ''
    if (tail.trim().length >= FRAGMENT_CHARS) flush()
    // A fragment closes a step only once the step says something. Without the length
    // floor, a chunk carrying the "1." of a numbered list became a step of its own.
    else if (SENTENCE_END.test(buffer) && buffer.trim().length >= FRAGMENT_CHARS) flush()
  }
  flush()
  return steps
}

/**
 * The answer tokens each lane produced, keyed by lane id.
 *
 * A fan-out streams its final answer from the supervisor, so on most runs this holds a
 * single supervisor entry — but a lane that streams its own findings is attributed to
 * it rather than folded into the answer.
 */
export function tokensByLane(state: RunState): Map<string, string> {
  const out = new Map<string, string>()
  for (const event of state.events) {
    if (event.type !== 'token') continue
    const lane = agentIdOf(event)
    if (lane === null) continue
    out.set(lane, (out.get(lane) ?? '') + event.text)
  }
  return out
}
