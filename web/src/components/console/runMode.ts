/**
 * The width a turn is asked to run at — the composer's half of `depth_mode`.
 *
 * `QueryRequest` has carried `depth_mode` and `requested_fanout` since Phase 5 and
 * `startRun` has always posted both, but nothing on the screen could set them: every
 * turn went out as `null`, which the run reads as Auto. So the product's most
 * impressive behaviour — a supervisor fanning out to a team of specialists — could
 * only be reached by luck of the classifier.
 *
 * This module is the pure half of exposing it: the choices, and the one rule the
 * server enforces about them. Kept out of the component so the rule is testable and
 * so the composer cannot post a body the API will reject.
 */

/** The three widths `run_agent` accepts. */
export type DepthMode = 'auto' | 'single' | 'team'

/** One selectable width, with the sentence that says what it costs. */
export interface DepthChoice {
  id: DepthMode
  label: string
  /** What choosing it actually does — shown as the chip's title, never as prose. */
  hint: string
}

/**
 * Auto first, because it is the honest default: the classifier decides, and the
 * run's `routing` event reports `decided_by: 'auto'` so the screen can say so.
 */
export const DEPTH_CHOICES: readonly DepthChoice[] = [
  { id: 'auto', label: 'Auto', hint: 'The router picks the width and says who decided.' },
  { id: 'single', label: 'Single', hint: 'One lane. Fastest, cheapest, no fan-out.' },
  { id: 'team', label: 'Team', hint: 'Fan out to concurrent specialists, then synthesise.' },
]

/** The team widths the composer offers. Clamped DOWN by the tenant cap, never up. */
export const FANOUT_CHOICES: readonly number[] = [2, 3, 4, 5]

/** What the composer holds. */
export interface RunMode {
  depth: DepthMode
  /** An explicit team width, or `null` to let the supervisor size the team. */
  fanout: number | null
}

/** The default: let the router decide, and let it size the team. */
export const DEFAULT_RUN_MODE: RunMode = { depth: 'auto', fanout: null }

/** The two wire fields, as `RunRequest` wants them. */
export interface WireMode {
  depthMode: DepthMode | null
  requestedFanout: number | null
}

/**
 * Translate a chosen mode into the two wire fields.
 *
 * `requested_fanout` is **only legal with `depth_mode: 'team'`** — the server rejects
 * it in any other mode rather than ignoring half the request — so a fanout left over
 * from a previous Team selection is dropped here rather than sent and 422'd.
 */
export function wireMode(mode: RunMode): WireMode {
  if (mode.depth === 'auto') return { depthMode: null, requestedFanout: null }
  if (mode.depth === 'single') return { depthMode: 'single', requestedFanout: null }
  return { depthMode: 'team', requestedFanout: mode.fanout }
}

/** How the chosen mode reads back in one line, for the turn's own record. */
export function describeMode(mode: RunMode): string {
  if (mode.depth === 'auto') return 'Auto width'
  if (mode.depth === 'single') return 'Single lane'
  return mode.fanout === null ? 'Team · router sizes it' : `Team of ${mode.fanout}`
}
