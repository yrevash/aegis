/**
 * What the signature mark is currently showing, and why.
 *
 * The mark is a six-segment ring around a core. Six because that is the real length of
 * both rail chains — `INPUT_CHAIN.length === OUTPUT_CHAIN.length === 6` — so the geometry
 * is a fact about the system rather than a decoration. This module is the whole of its
 * state machine, kept pure and free of React so the precedence can be asserted directly
 * by `web/tests/console/runMarkState.test.mjs` rather than inferred from a rendered SVG.
 *
 * ## Every state is a named fact already on the wire
 *
 * No moods, no invented progress. `blocked` is a `guardrail` verdict; `gated` is a live
 * `approval_required`; `fanout` is `routing.depth`; `screening` is an open `guard_input`
 * or `guard_output` stage; `settled` is a finished run with text in it. The mark is a
 * **second** channel for facts the screen already states in words — never the only
 * carrier of any of them.
 *
 * ## The precedence, and the one thing it does not say
 *
 * `blocked > gated > fanout > screening > thinking > settled > idle`, explicitly, because
 * a run can satisfy several at once and the order is the whole content of this file.
 *
 * The three middle states — `fanout`, `screening`, `thinking` — are descriptions of work
 * *in progress*, so each additionally requires `state.running`. Without that a finished
 * team run would read `fanout` for ever and never reach `settled`, and every beat-driven
 * animation in the console would keep pulsing after `run_finished` (which updates
 * `lastSignal` like any other event). `blocked` and `gated` deliberately do **not** carry
 * that condition: a run that was blocked stays blocked after it ends, and a run that
 * stopped at the human gate is still waiting on a person.
 */

import type { RunState } from '@/state/runReducer'

import { deriveTiming, isGuardStage, railIndexOf } from './stageTimeline'

/** The seven things the mark can be. See the precedence note above. */
export type MarkState =
  | 'idle'
  | 'screening'
  | 'thinking'
  | 'fanout'
  | 'gated'
  | 'blocked'
  | 'settled'

/** How many segments the ring has when nothing has widened it. The rail-chain length. */
export const SEGMENTS = 6

/** The newest guardrail verdict of any stage, or `null`. */
function lastGuardrail(state: RunState): RunState['guardrails'][number] | null {
  return state.guardrails.at(-1) ?? null
}

/**
 * What the mark should be showing for this run.
 *
 * @param state - The reduced run, or `null` when no run exists at all.
 * @returns The single winning state, by the precedence documented above.
 */
export function markStateOf(state: RunState | null): MarkState {
  if (state === null) return 'idle'

  // 1. Blocked — the run was stopped by a rail, and it stays stopped.
  if (lastGuardrail(state)?.verdict === 'block') return 'blocked'

  // 2. Gated — a person has to answer before anything else happens. `state.approval` is
  //    cleared on the first `token`, so this is the *live* gate, not "was ever gated".
  if (state.approval !== null) return 'gated'

  if (state.running) {
    // 3. Fan-out — the router's own width, not a count of cards that have appeared.
    if (state.routing !== null && state.routing.depth === 'team') return 'fanout'

    // 4. Screening — one of the two guardrail nodes is open right now.
    const current = deriveTiming(state).current
    if (current !== null && isGuardStage(current.node)) return 'screening'

    // 5. Thinking — the residual live state. Something is running and it is neither a
    //    fan-out nor a rail, which is the single-lane middle of the graph.
    return 'thinking'
  }

  // 6. Settled — finished, with an answer to show for it.
  if (state.answer !== '') return 'settled'

  // 7. Idle — a console with no run, or a run that ended with nothing to show.
  return 'idle'
}

/**
 * Which ring segment is missing on a blocked run.
 *
 * The wire names the deciding layer (`Guardrail.layer`), so the gap in the ring is a
 * fact: it sits at that rail's position in its own chain. When the wire named no layer,
 * or named one the chain does not contain, this returns `null` and the caller draws no
 * gap at all — **a break at the wrong rail is worse than no break**, because the ring is
 * the only place the position carries meaning.
 *
 * @param state - The reduced run, or `null`.
 * @returns A segment index in `[0, SEGMENTS)`, or `null`.
 */
export function brokenSegmentOf(state: RunState | null): number | null {
  if (state === null) return null
  const guardrail = lastGuardrail(state)
  if (guardrail === null || guardrail.verdict !== 'block') return null
  return railIndexOf(guardrail.stage, guardrail.layer)
}

/**
 * How many arcs the ring splits into.
 *
 * Six — the rail-chain length — except on a fan-out, where it is the wire's own lane
 * count, so the mark's geometry states the width the router chose. Clamped to a range a
 * 20px mark can still draw as distinct arcs; the number itself is stated in words beside
 * the lanes, as it must be.
 */
export function segmentsOf(state: RunState | null): number {
  if (state === null || markStateOf(state) !== 'fanout') return SEGMENTS
  const fanout = state.routing?.fanout ?? 0
  if (fanout < 2) return SEGMENTS
  return Math.min(fanout, 8)
}

/**
 * How long one revolution takes, per state that has open work.
 *
 * The ring turns for as long as a stage is open and carries **duration, never progress** —
 * there is no percentage on this wire to draw. It was previously reserved for `screening`,
 * on the assumption that the other live states would be animated by their own per-event
 * pulse. That assumption was wrong in the one place it mattered most: agentic retrieval
 * emits nothing between its open and its close, so a 60-second window drew a single pulse
 * and then held perfectly still - the exact spinner-shaped hole this mark exists to fill.
 *
 * The speeds differ so the states stay distinguishable with colour off: screening is brisk
 * because it is a rail and it is quick, thinking is slower and steady, fan-out slower again
 * because more of the ring is moving.
 */
export const SPIN_SECONDS: Partial<Record<MarkState, number>> = {
  screening: 2.4,
  thinking: 3.6,
  fanout: 4.8,
}
