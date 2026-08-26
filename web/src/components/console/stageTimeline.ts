/**
 * The run as a sequence of stages — what ran, in what order, and what each one cost.
 *
 * The console's own measurement of a real run:
 *
 * ```
 * guard_input      3,142 ms
 * route               10 ms
 * plan_team        1,444 ms
 * agent:research   3,396 ms  ┐ concurrent, inside run_team
 * agent:knowledge  8,605 ms  ┘
 * run_team        12,254 ms
 * synthesize       3,795 ms
 * guard_output     7,789 ms
 * stream               0 ms
 * ```
 *
 * Eleven of those twenty-nine seconds are the two guardrails, and the console spent
 * them showing a spinner — which reads as a slow product rather than a governed one.
 * The wait *is* the story, so this derivation exists to make it legible: every stage
 * the run has entered, the wire's own `duration_ms` once it leaves, and a one-line
 * brief naming what that stage is doing while it takes its time.
 *
 * Three rules hold everything here honest:
 *
 * - **Every duration is the wire's.** `NodeFinished.duration_ms` and nothing else. A
 *   stage still in flight carries `durationMs: null`, and the surface that draws it is
 *   responsible for saying that its live counter is the browser's clock, not the
 *   server's.
 * - **Nesting is read, not guessed.** A fan-out lane's graph nodes are named
 *   `agent:<id>` and its events carry `agent_id` — both are wire facts, and either one
 *   marks a stage as running *inside* the fan-out rather than after it. That is what
 *   stops `run_team`'s 12.2 s being added to its own lanes' 12.0 s to claim a 24-second
 *   run.
 * - **Totals sum top-level stages only**, for the same reason.
 *
 * The briefs are descriptions of what a node does, not claims about what it found —
 * each one is taken from that node's docstring in `aegis/src/aegis/agent/graph.py`, and
 * the two guardrail chains are the literal layer order in
 * `aegis/src/aegis/guardrails/pipeline.py` (`_screen_input` and `check_output`).
 *
 * Pure — no React, no transport — so `web/tests/console/stageTimeline.test.mjs` can
 * script an event log and read the stages back without a renderer.
 */

import type { Signal } from '@/config/signals'
import type { RunState } from '@/state/runReducer'

import { agentIdOf, agentIdOfNode } from './eventViews'

/** What a stage is doing, and which subsystem owns it. */
interface NodeBrief {
  /** One line: what this stage does while it runs. */
  what: string
  /** The subsystem hue, shared with the trace, the flow map and the trust bar. */
  signal: Signal
  /**
   * The rails this stage runs, in order, when it is a guardrail. The wire reports
   * one verdict and at most one deciding `layer` — never per-layer progress — so
   * this is the chain, not a progress bar.
   */
  chain?: readonly string[]
}

/**
 * The input rail's layer chain, in `_screen_input` order.
 *
 * Exported because the idle console draws the same chain before a run exists — the
 * path a question is about to take is the one thing an empty console can say that is
 * true. See `RunPreview`.
 */
export const INPUT_CHAIN = [
  'schema',
  'denylist',
  'PII',
  'prompt injection',
  'content safety',
  'topic',
] as const

/** The output rail's layer chain, in `check_output` order. */
export const OUTPUT_CHAIN = [
  'schema',
  'content filter',
  'denylist',
  'content safety',
  'grounding',
  'PII',
] as const

/**
 * Wire rail id → position in {@link INPUT_CHAIN}.
 *
 * The two chains above are **display labels** — `'PII'`, `'prompt injection'` — and
 * `Guardrail.layer` is the pipeline's own id — `'pii'`, `'injection'`. Nothing mapped
 * one to the other, so the wire could name the deciding rail and no surface could point
 * at it. These two tables are that map, and they are deliberately the only place it
 * exists.
 *
 * Every id is a literal from `aegis/src/aegis/guardrails/pipeline.py`. The tables are
 * **not** exhaustive over what the pipeline can emit, and that is the honest part: an
 * output `'exfiltration'` verdict has no cell in `check_output`'s six-layer chain, so it
 * resolves to `null` and a surface draws no mark rather than marking the wrong rail.
 *
 * `injection_unavailable` — the fail-closed verdict when the injection classifier cannot
 * be reached — maps to the injection rail's position, because that is the rail whose
 * absence produced it.
 */
const INPUT_LAYERS: Record<string, number | undefined> = {
  schema: 0,
  denylist: 1,
  pii: 2,
  injection: 3,
  injection_unavailable: 3,
  content_safety: 4,
  topical: 5,
}

/** Wire rail id → position in {@link OUTPUT_CHAIN}. `content` is the content filter. */
const OUTPUT_LAYERS: Record<string, number | undefined> = {
  schema: 0,
  content: 1,
  denylist: 2,
  content_safety: 3,
  grounding: 4,
  pii: 5,
}

/**
 * Where a verdict's deciding rail sits in its stage's chain.
 *
 * @param stage - The `Guardrail.stage` the verdict came from.
 * @param layer - The `Guardrail.layer` the wire named, which is often `null`.
 * @returns The index into the stage's chain, or `null` when the wire named no layer,
 *   named one this chain does not contain, or reported a stage with no chain at all
 *   (`tool_result`). **Null means "do not mark a rail"** — a break drawn at the wrong
 *   rail is worse than no break.
 */
export function railIndexOf(stage: string, layer: string | null): number | null {
  if (layer === null || layer === '') return null
  const table = stage === 'input' ? INPUT_LAYERS : stage === 'output' ? OUTPUT_LAYERS : null
  if (table === null) return null
  return table[layer] ?? null
}

/** The display label of the rail a verdict came from, or `null`. See {@link railIndexOf}. */
export function railLabelOf(stage: string, layer: string | null): string | null {
  const index = railIndexOf(stage, layer)
  if (index === null) return null
  return stage === 'input' ? INPUT_CHAIN[index] : OUTPUT_CHAIN[index]
}

/**
 * Per-node briefs. Deliberately partial: a node with no entry still renders, under
 * the label the wire sent it with and the neutral hue, because the graph is allowed
 * to grow a stage without this file hearing about it first.
 */
const NODE_BRIEF: Record<string, NodeBrief> = {
  guard_input: {
    what: 'Screening the question before any model sees it',
    signal: 'block',
    chain: INPUT_CHAIN,
  },
  guard_output: {
    what: 'Screening the finished answer before any of it is released',
    signal: 'block',
    chain: OUTPUT_CHAIN,
  },
  route: { what: 'Classifying the turn and sizing the run', signal: 'agent' },
  plan_team: { what: 'Writing one brief per agent for the team it just sized', signal: 'agent' },
  run_team: { what: 'Every agent running at once, each with its own tools', signal: 'agent' },
  synthesize: {
    what: 'Merging the lanes, naming who is in the answer and who was omitted',
    signal: 'graph',
  },
  answer_memory: { what: 'Answering directly from what it remembers about you', signal: 'graph' },
  recall_memory: { what: 'Recalling this conversation’s durable context', signal: 'graph' },
  persist_memory: { what: 'Writing this turn back to memory', signal: 'graph' },
  retrieve: { what: 'Searching the corpus and the graph, then reranking', signal: 'graph' },
  plan: { what: 'Reasoning over the retrieved context and proposing actions', signal: 'agent' },
  gate: { what: 'Deciding whether a human has to approve the proposed action', signal: 'risk' },
  approval: { what: 'Waiting on a person', signal: 'risk' },
  act: { what: 'Executing the approved actions, auditing each one', signal: 'agent' },
  reflect: { what: 'Judging its own outcome, and re-planning if it has to', signal: 'agent' },
  generate: { what: 'Composing the answer from context and tool results', signal: 'agent' },
  stream: { what: 'Releasing the screened answer to this screen', signal: 'agent' },
}

/** The brief for a fan-out lane — a stage the roster names, not the graph. */
const LANE_BRIEF: NodeBrief = { what: 'A sub-agent working its own brief', signal: 'agent' }

/** The neutral brief a node the frontend has not heard of still renders under. */
const UNKNOWN_BRIEF: NodeBrief = { what: '', signal: 'neutral' }

/**
 * The nodes the bounded self-repair loop runs, once per round.
 *
 * A run that judged its own first attempt insufficient shows `plan → gate → act →
 * reflect` twice, and the console rendered it as eight rows of duplicated noise. It is
 * the opposite of noise — it is the agent catching itself — so the rows are grouped
 * into the rounds the wire already numbers.
 */
const LOOP_NODES: ReadonlySet<string> = new Set(['plan', 'gate', 'approval', 'act', 'reflect'])

/** One stage of a run: a `node_started`, and its `node_finished` once it lands. */
export interface Stage {
  /** Stable key — the node name plus the sequence number that opened it. */
  key: string
  /** Node name as the wire sent it, e.g. `guard_input`, `agent:research`. */
  node: string
  /** Human label, as the wire sent it. Never invented here. */
  label: string
  /** What this stage does. `''` when the frontend has no brief for the node. */
  what: string
  /** The rails this stage runs, in order — guardrail stages only. */
  chain: readonly string[]
  signal: Signal
  /** The fan-out lane this stage belongs to, or `null` for a graph-level stage. */
  agentId: string | null
  /**
   * The self-repair round this stage ran in, or `null` for a stage outside the loop.
   *
   * The number is the wire's: `Reflection.iteration` names the round that just closed,
   * so the round a stage belongs to is read rather than counted by watching node names
   * repeat.
   */
  round: number | null
  /** The loop's own budget (`Reflection.max_iterations`), once a round has closed. */
  maxRounds: number | null
  /** True while the stage has no `node_finished`. */
  running: boolean
  /** The wire's own `duration_ms`, or `null` while the stage is still in flight. */
  durationMs: number | null
  /** The wire's marginal cost for this stage, or `null` while in flight. */
  costUsd: number | null
  /** Prompt + completion tokens the stage reported, or `null` while in flight. */
  tokens: number | null
  /** The model deployment the stage used, or `null` (non-LLM node, or in flight). */
  model: string | null
  /**
   * The verdict this stage produced, in the run's own words — a guardrail `reason`
   * on a guardrail stage, and `''` everywhere else. Wire text, quoted, never
   * paraphrased.
   */
  verdict: string
  /** True when that verdict stopped the run. */
  blocked: boolean
}

/** The run's stages, and the figures that can honestly be summed across them. */
export interface RunTiming {
  /** Every stage the run entered, in the order it entered them. */
  stages: Stage[]
  /** The stage still in flight, or `null`. The newest one, if several are. */
  current: Stage | null
  /**
   * Sum of `duration_ms` across finished **top-level** stages. Lanes are excluded
   * because they run inside `run_team`, whose own duration already covers them.
   */
  measuredMs: number
  /** The longest single stage duration seen, for scaling a bar. */
  peakMs: number
  /** Sum of `cost_usd` across finished stages, in USD. */
  costUsd: number
  /** Sum of prompt + completion tokens across finished stages. */
  tokens: number
  /** True once at least one stage has finished and reported a duration. */
  measured: boolean
  /** How many self-repair rounds the loop entered. `0` when it never ran. */
  rounds: number
  /** The loop's configured budget, once a `reflection` has reported one. */
  roundBudget: number | null
}

/** The empty timing, for a turn that has not started. */
const EMPTY: RunTiming = {
  stages: [],
  current: null,
  measuredMs: 0,
  peakMs: 0,
  costUsd: 0,
  tokens: 0,
  measured: false,
  rounds: 0,
  roundBudget: null,
}

/** Whether a stage is one of the two guardrail nodes. */
export function isGuardStage(node: string): boolean {
  return node === 'guard_input' || node === 'guard_output'
}

/** The guardrail stage a verdict belongs to, or `null` for the tool-result rail. */
function guardNodeFor(stage: string): string | null {
  if (stage === 'input') return 'guard_input'
  if (stage === 'output') return 'guard_output'
  return null
}

/**
 * Reduce a run's event log into its stages.
 *
 * @param state - The reduced run. Only `events` is read, so a scripted log is enough.
 * @returns Every stage the run entered, plus the figures that can be summed.
 */
export function deriveTiming(state: RunState): RunTiming {
  if (state.events.length === 0) return EMPTY

  const stages: Stage[] = []
  /** Indices into `stages` of the stages still open, oldest first. */
  const open: number[] = []
  /** The self-repair round the loop is currently in. Advanced by `reflection`. */
  let round = 1
  let maxRounds: number | null = null

  for (const event of state.events) {
    if (event.type === 'node_started') {
      const brief =
        NODE_BRIEF[event.node] ??
        (agentIdOfNode(event.node) !== null ? LANE_BRIEF : UNKNOWN_BRIEF)
      stages.push({
        key: `${event.node}#${event.seq}`,
        node: event.node,
        label: event.label !== '' ? event.label : event.node,
        what: brief.what,
        chain: brief.chain ?? [],
        signal: brief.signal,
        agentId: agentIdOfNode(event.node) ?? agentIdOf(event),
        round: LOOP_NODES.has(event.node) ? round : null,
        maxRounds,
        running: true,
        durationMs: null,
        costUsd: null,
        tokens: null,
        model: null,
        verdict: '',
        blocked: false,
      })
      open.push(stages.length - 1)
      continue
    }

    if (event.type === 'node_finished') {
      // The oldest still-open stage with this node name. Two lanes of a fan-out are
      // open at once and finish out of order, so this closes by name rather than by
      // popping a stack.
      const slot = open.findIndex((index) => stages[index].node === event.node)
      if (slot === -1) continue
      const stage = stages[open[slot]]
      open.splice(slot, 1)
      stage.running = false
      stage.durationMs = event.duration_ms
      stage.costUsd = event.cost_usd
      stage.tokens = event.prompt_tokens + event.completion_tokens
      stage.model = event.model
      continue
    }

    if (event.type === 'reflection') {
      // The round that just closed, and why it did or did not go round again — the one
      // moment the agent is visibly correcting itself, and it used to render as a
      // duplicate row.
      maxRounds = event.max_iterations
      for (let i = stages.length - 1; i >= 0; i -= 1) {
        if (stages[i].node !== 'reflect') continue
        stages[i].verdict = event.reason
        stages[i].round = event.iteration
        stages[i].maxRounds = event.max_iterations
        break
      }
      if (event.will_retry) round = event.iteration + 1
      continue
    }

    if (event.type === 'guardrail') {
      // The rail's own words, attached to the stage that ran it — so a finished
      // guardrail row reads "passed schema, denylist, PII…" rather than going quiet
      // the moment it is the most interesting thing on screen.
      const node = guardNodeFor(event.stage)
      if (node === null) continue
      for (let i = stages.length - 1; i >= 0; i -= 1) {
        if (stages[i].node !== node) continue
        stages[i].verdict = event.reason
        stages[i].blocked = event.verdict === 'block'
        break
      }
    }
  }

  let measuredMs = 0
  let peakMs = 0
  let costUsd = 0
  let tokens = 0
  let measured = false
  for (const stage of stages) {
    if (stage.durationMs === null) continue
    measured = true
    if (stage.agentId === null) measuredMs += stage.durationMs
    peakMs = Math.max(peakMs, stage.durationMs)
    costUsd += stage.costUsd ?? 0
    tokens += stage.tokens ?? 0
  }

  const openStages = open.map((index) => stages[index])
  const rounds = stages.reduce((most, stage) => Math.max(most, stage.round ?? 0), 0)
  return {
    stages,
    current: openStages.at(-1) ?? null,
    measuredMs,
    peakMs,
    costUsd,
    tokens,
    measured,
    rounds,
    roundBudget: maxRounds,
  }
}

/**
 * A duration, as a person reads it: milliseconds under a second, seconds above.
 *
 * Formatting only — the figure itself is always the wire's.
 */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  return `${(ms / 1000).toFixed(1)} s`
}
