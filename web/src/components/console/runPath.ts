/**
 * The four beats every turn takes, and where this run has got to.
 *
 * `RunPreview` drew this path as a static strip that an empty console could show
 * truthfully — and then it was unmounted the instant a question was sent, which is the
 * one moment the path becomes *measured* rather than promised. This module is the state
 * that lets the same strip stay on screen and fill in: per beat, whether it is pending,
 * running, passed or blocked, the wire's own duration once it settles, and the one short
 * wire fact worth printing beside it.
 *
 * ## Nothing here is computed that is not already on the wire
 *
 * Every status comes from a `node_started` / `node_finished` pair that
 * {@link deriveTiming} already reduced; every duration is that pair's `duration_ms`;
 * every caption is a field of `routing` or the deciding `layer` of a `guardrail`. There
 * is deliberately **no per-rail progress**: the backend reports
 * `per_rail_timing_ms: {schema: None, pii: None, injection: None, total: n}`, so a chase
 * across six chips would be drawing six durations the platform explicitly declines to
 * claim. A guardrail beat lights as a whole, for its real total, and only the rail the
 * wire *named* as deciding is marked.
 *
 * ## Why the middle is one beat
 *
 * Four beats, not fourteen. Whether this turn answers from memory, retrieves and
 * reasons, or fans out to a team is the router's decision and it has not been made at
 * t=0 — so the middle beat stands for all of them and states the routed width only once
 * `routing` has landed. Before that it says nothing, because before that nothing is
 * known.
 *
 * Pure — no React, no transport — so `web/tests/console/runPath.test.mjs` scripts an
 * event log and reads the beats back without a renderer.
 */

import type { RunState } from '@/state/runReducer'

import { INPUT_CHAIN, OUTPUT_CHAIN, deriveTiming, railIndexOf, railLabelOf } from './stageTimeline'

/** Which of the four beats a stage belongs to. Stable ids, so a surface can key on them. */
export type BeatId = 'input' | 'route' | 'work' | 'output'

/** How far a beat has got. `blocked` is terminal for the whole path, not just the beat. */
export type BeatStatus = 'pending' | 'running' | 'passed' | 'blocked'

/** One beat of the path, and the rails it runs when it is a guardrail. */
export interface PathBeat {
  id: BeatId
  label: string
  /** The layer chain, for a guardrail beat. Empty otherwise. */
  chain: readonly string[]
}

/**
 * The four beats every turn passes through, in graph order.
 *
 * Read from the same two places {@link deriveTiming} reads: the node briefs in
 * `aegis/agent/graph.py` and the two rail chains in `aegis/guardrails/pipeline.py`.
 */
export const BEATS: readonly PathBeat[] = [
  { id: 'input', label: 'Input rail', chain: INPUT_CHAIN },
  { id: 'route', label: 'Route', chain: [] },
  { id: 'work', label: 'Retrieve & answer', chain: [] },
  { id: 'output', label: 'Output rail', chain: OUTPUT_CHAIN },
]

/** A beat, with everything this run has reported about it. */
export interface BeatState extends PathBeat {
  status: BeatStatus
  /**
   * Sum of the wire's `duration_ms` across this beat's finished top-level stages, or
   * `null` while nothing in the beat has landed. Lane stages are excluded — `run_team`'s
   * own duration already covers them.
   */
  durationMs: number | null
  /**
   * One short wire fact, or `''`. The routed width and role for the middle two beats,
   * the deciding rail's display label for the two guardrail beats. Never a description.
   */
  caption: string
  /**
   * Index into {@link PathBeat.chain} of the rail the wire named as deciding, or `null`.
   * `null` covers "no verdict yet", "the wire named no layer" and "the wire named a
   * layer this chain does not contain" — all three of which must draw no mark.
   */
  railIndex: number | null
  /** The raw `Guardrail.verdict` this beat's rail returned, or `null`. Never collapsed. */
  verdict: string | null
}

/** Which beat a graph node belongs to. A node with no entry belongs to no beat. */
const NODE_BEAT: Record<string, number | undefined> = {
  guard_input: 0,
  route: 1,
  plan_team: 1,
  retrieve: 2,
  plan: 2,
  gate: 2,
  approval: 2,
  act: 2,
  reflect: 2,
  generate: 2,
  stream: 2,
  answer_memory: 2,
  recall_memory: 2,
  persist_memory: 2,
  run_team: 2,
  synthesize: 2,
  guard_output: 3,
}

/** The guardrail stage each beat screens, or `null` for the two that screen nothing. */
const BEAT_GUARD_STAGE: readonly (string | null)[] = ['input', null, null, 'output']

/** A beat nothing has been reported about. */
function pending(beat: PathBeat): BeatState {
  return { ...beat, status: 'pending', durationMs: null, caption: '', railIndex: null, verdict: null }
}

/**
 * Where this run has got to along the four beats.
 *
 * @param state - The reduced run, or `null` for a console with no run at all. A `null`
 *   state returns four pending beats, which is exactly what an idle console should draw:
 *   the path, promised, with nothing claimed about it.
 * @returns One {@link BeatState} per beat, always four, always in graph order.
 */
export function beatStates(state: RunState | null): BeatState[] {
  if (state === null) return BEATS.map(pending)

  const timing = deriveTiming(state)
  const seen = [0, 0, 0, 0]
  const open = [false, false, false, false]
  const blocked = [false, false, false, false]
  const durations: (number | null)[] = [null, null, null, null]

  for (const stage of timing.stages) {
    // A fan-out lane runs *inside* `run_team`, whose own duration already covers it.
    // Counting both is the double-count `deriveTiming`'s own totals exist to avoid.
    if (stage.agentId !== null) continue
    const index = NODE_BEAT[stage.node]
    if (index === undefined) continue
    seen[index] += 1
    if (stage.running) open[index] = true
    if (stage.blocked) blocked[index] = true
    if (stage.durationMs !== null) durations[index] = (durations[index] ?? 0) + stage.durationMs
  }

  const routing = state.routing
  const beats = BEATS.map((beat, index): BeatState => {
    const guardStage = BEAT_GUARD_STAGE[index]
    // The newest verdict for this beat's stage. A run can report several — content
    // safety and grounding both land on the output — and the last one is the one that
    // decided whether the answer was released.
    const verdict =
      guardStage === null
        ? null
        : (state.guardrails.filter((g) => g.stage === guardStage).at(-1) ?? null)

    const railIndex = verdict === null ? null : railIndexOf(verdict.stage, verdict.layer)
    const railLabel = verdict === null ? null : railLabelOf(verdict.stage, verdict.layer)

    let caption = ''
    if (guardStage !== null) caption = railLabel ?? ''
    // The width is the router's, and it is unknown until the router says so. A caption
    // here before the `routing` event would be this file guessing at the run's shape.
    else if (routing !== null && beat.id === 'route') caption = routing.role
    else if (routing !== null && beat.id === 'work') {
      caption = routing.depth === 'team' ? `Team of ${routing.fanout}` : 'Single lane'
    }

    const status: BeatStatus = blocked[index]
      ? 'blocked'
      : open[index]
        ? 'running'
        : seen[index] > 0
          ? 'passed'
          : 'pending'

    return {
      ...beat,
      status,
      durationMs: durations[index],
      caption,
      railIndex,
      verdict: verdict?.verdict ?? null,
    }
  })

  // A blocked beat ends the path. Everything after it stays pending — never `passed` —
  // because a run stopped at the input rail never reached them, and a strip that greyed
  // them as "done" would claim work no model was ever asked to do.
  const stop = beats.findIndex((b) => b.status === 'blocked')
  if (stop === -1) return beats
  return beats.map((beat, index) =>
    index <= stop ? beat : { ...beat, status: 'pending', durationMs: null, caption: '' },
  )
}
