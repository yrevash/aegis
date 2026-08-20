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

/** The chosen width in one word, for a menu chip that has no room for the sentence. */
export function depthLabel(depth: DepthMode): string {
  return DEPTH_CHOICES.find((choice) => choice.id === depth)?.label ?? depth
}

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

/**
 * How the chosen mode reads back on the closed chip.
 *
 * The chip carries **the option's own name** — `Auto`, `Single`, `Team` — and nothing
 * else but the degree, because a dropdown whose closed state renames its options makes
 * the person open it to work out which one is selected. `Auto width` and `Single lane`
 * described the axis rather than naming the choice, and the axis is already named twice
 * over: by the chip's `Mode:` prefix and by the panel's own `How wide this turn runs`.
 *
 * Team keeps its degree, because `Team` and `Team of 3` are two different requests and
 * the difference is the whole reason the degree row exists.
 */
export function describeMode(mode: RunMode): string {
  if (mode.depth === 'auto') return 'Auto'
  if (mode.depth === 'single') return 'Single'
  return mode.fanout === null ? 'Team · auto size' : `Team of ${mode.fanout}`
}

// ── What the run actually did with the request ───────────────────────────────

/**
 * The fields of the run's own `routing` event this module reads.
 *
 * Structural rather than an import of `RoutingEvent`, so the rule below stays a pure
 * module with no dependency on the stream types — and so a caller cannot pass an event
 * that merely looks close enough.
 */
export interface RoutingFacts {
  /** 'single' (one lane) or 'team' (a fan-out). */
  depth: string
  /** How many sub-agents a team turn fanned out to. 0 on a single lane. */
  fanout: number
  /** 'auto' | 'user' | 'tenant_default' | 'platform_cap'. */
  decided_by: string
  /** The run's own sentence for the decision. */
  reason: string
}

/**
 * What the run reports about its own width, and whether that is what was asked for.
 *
 * Every field here is read off the `routing` event — never off the composer's own
 * selection. That is the whole point: the composer's request is a *request*, and the
 * screen has already been wrong about it once by echoing the chosen chip back as
 * though it were the outcome. The router can pick a different width (`decided_by:
 * 'auto'`), the tenant can have no team roster (`tenant_default`), and
 * `max_parallel_agents` can clamp a five-agent request to three (`platform_cap`).
 */
export interface WidthOutcome {
  /** The width the run actually ran at, e.g. `Team of 3` or `Single lane`. */
  ran: string
  /** Who chose it, in words, from `decided_by`. */
  decidedBy: string
  /** `decided_by` verbatim, for the receipt's origin line. */
  decidedByCode: string
  /** The run's own reason string, shown verbatim or not at all. */
  reason: string
  /** Whether the run's width differs from what the composer asked for. */
  differs: boolean
  /**
   * One sentence naming the difference — what was asked, what ran — or `null` when the
   * request was honoured or when nothing was asked for (Auto).
   */
  note: string | null
}

/** `decided_by`, in the words the console uses for it. Unknown codes pass through. */
const DECIDED_BY: Readonly<Record<string, string>> = {
  auto: 'the router',
  user: 'you',
  tenant_default: 'the tenant default',
  platform_cap: 'the tenant’s parallel-agent cap',
}

/** How a routed width reads back: `Single lane`, `Team of 3`, `Team`. */
function ranAs(routing: RoutingFacts): string {
  if (routing.depth !== 'team') return 'Single lane'
  return routing.fanout > 0 ? `Team of ${routing.fanout}` : 'Team'
}

/**
 * Compare the width that was asked for with the width the run reports.
 *
 * Auto never "differs": nothing was asked, so there is nothing to have been overridden,
 * and saying "the router chose Single" is the whole of the story. A Team request that
 * comes back single, or comes back smaller than it asked for, is the case this exists
 * for — the run says so in `decided_by`, and the screen must not quietly show the chip
 * the person picked as if it had been obeyed.
 */
export function widthOutcome(requested: RunMode, routing: RoutingFacts): WidthOutcome {
  const ran = ranAs(routing)
  const outcome: WidthOutcome = {
    ran,
    decidedBy: DECIDED_BY[routing.decided_by] ?? routing.decided_by,
    decidedByCode: routing.decided_by,
    reason: routing.reason,
    differs: false,
    note: null,
  }

  if (requested.depth === 'auto') return outcome

  if (requested.depth === 'single' && routing.depth === 'team') {
    return { ...outcome, differs: true, note: `You asked for a single lane; this run ran as ${ran}.` }
  }

  if (requested.depth === 'team') {
    if (routing.depth !== 'team') {
      return {
        ...outcome,
        differs: true,
        note: 'You asked for a team; this run went out as a single lane.',
      }
    }
    if (requested.fanout !== null && routing.fanout > 0 && routing.fanout !== requested.fanout) {
      const verb = routing.fanout < requested.fanout ? 'clamped to' : 'widened to'
      return {
        ...outcome,
        differs: true,
        note: `You asked for ${requested.fanout} agents; this run was ${verb} ${routing.fanout}.`,
      }
    }
  }

  return outcome
}
