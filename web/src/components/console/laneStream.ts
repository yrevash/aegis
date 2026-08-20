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
