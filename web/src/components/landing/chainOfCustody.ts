/**
 * The chain of custody — one question's whole path, as data.
 *
 * This is the landing page's signature, and the only reason it is allowed to exist
 * is that **every string in it is the platform's own**. The node names are the keys
 * of `NODE_LABELS` in `aegis/src/aegis/agent/graph.py`; the rail ids are the `id=`
 * values in `aegis/src/aegis/guardrails/pipeline.py`; the `decided_by` values are
 * the four `aegis.agent.router` reports; the lane labels and their tool remits are
 * `_SUB_AGENTS` in `backend/src/app/adapter/roster.py`; the consent sentence is
 * `summaryFor()` in `components/approval/approvalActions.ts`, verbatim.
 *
 * Nothing here is a figure. A run's numbers — its cost, its latency, its fan-out
 * width — are measured per run and live behind the login, so the chain shows the
 * *shape* of a run and says so in its own receipt. A landing page that printed a
 * plausible-looking token count would be the exact failure this product exists to
 * refuse.
 *
 * Pure and framework-free so the sequence is testable without a renderer: the one
 * thing that must not rot is the correspondence between these ids and the graph.
 */

/** What a link does to the line it sits on. */
export type LinkKind =
  /** A single node on the rail. */
  | 'plain'
  /** The node the line splits under — the concurrent lanes hang off it. */
  | 'team'
  /** The one link that can stop the run and hand it to a person. */
  | 'gate'

/** One concurrent specialist, and what it is allowed to do. */
export interface Lane {
  /** The roster's own label. */
  label: string
  /** Its tool remit, in three words. */
  remit: string
  /** True for the one lane that may request a write. */
  writes?: boolean
}

/** One link in the chain: a stage of a run, and the record it leaves. */
export interface ChainLink {
  id: string
  /** What a reader calls it. Set in Inter — the page's own voice. */
  label: string
  /** What the platform calls it. Set in mono — the system's voice. */
  node: string
  /** One sentence in the page's voice about why this link is here. */
  note: string
  /** The system's own words for what this link records. Mono, every line. */
  record: readonly string[]
  kind: LinkKind
  /** The concurrent lanes, on the one link that has them. */
  lanes?: readonly Lane[]
  /**
   * A sentence the product actually renders at this stage, quoted. Only two links
   * have one, and both are quotes rather than paraphrase.
   */
  quote?: string
}

/**
 * The chain, in order.
 *
 * Eight links because that is how many stages a reader can hold; the graph itself
 * has seventeen nodes and the memory, retrieval, plan, act and reflect nodes are
 * folded into the stages that own them rather than dropped silently — `route`
 * carries the specialist choice, `synthesize` carries the model spend.
 */
export const CHAIN: readonly ChainLink[] = [
  {
    id: 'ask',
    label: 'Ask',
    node: 'POST /v1/query',
    note: 'A question arrives carrying a tenant, a role and a requested width. Nothing in this platform runs unscoped.',
    record: ['tenant_id', 'role', 'depth_mode: auto | single | team'],
    kind: 'plain',
  },
  {
    id: 'input',
    label: 'Input rails',
    node: 'guard_input',
    note: 'Six checks before a single token is spent, and the run names the one that fired.',
    record: ['input_schema', 'denylist', 'pii', 'injection', 'content_safety', 'topical'],
    kind: 'plain',
  },
  {
    id: 'route',
    label: 'Route',
    node: 'route',
    note: 'A supervisor sizes the team, then reports who decided — including the ceiling that clamped it.',
    record: ['decided_by: auto | user | tenant_default | platform_cap'],
    kind: 'plain',
  },
  {
    id: 'team',
    label: 'The team',
    node: 'run_team',
    note: 'Up to four specialists run at once. Three of them hold no write tool at all.',
    record: ['max_parallel_agents: 4', 'model_role: CHEAP on every lane'],
    kind: 'team',
    lanes: [
      { label: 'Research agent', remit: 'read only' },
      { label: 'Knowledge agent', remit: 'read only' },
      { label: 'Data agent', remit: 'one gated write', writes: true },
      { label: 'Policy agent', remit: 'read only' },
    ],
  },
  {
    id: 'synthesize',
    label: 'Synthesis',
    node: 'synthesize',
    note: 'The lanes run on the cheap model. The expensive one is spent once, here.',
    record: ['four findings in', 'one answer out'],
    kind: 'plain',
  },
  {
    id: 'gate',
    label: 'Human gate',
    node: 'gate → approval',
    note: "A consequential call stops on the tool's risk tier, never on model confidence. Approving is a recorded consent, not a dismissed dialog.",
    record: ['risk: high', 'approval_required', 'resolved_by: <person>'],
    quote: 'Approving runs all 3 of these calls. Rejecting ends the run without running any of them.',
    kind: 'gate',
  },
  {
    id: 'output',
    label: 'Output rails',
    node: 'guard_output',
    note: 'Checked on the way out as well as in: shape, grounding, and the tenant’s topic policy.',
    record: ['output_schema', 'grounding', 'custom'],
    kind: 'plain',
  },
  {
    id: 'answer',
    label: 'Answer',
    node: 'stream',
    note: 'Every figure arrives with its origin. One that cannot be sourced is stated as absent.',
    record: ['Source: <table · model · rail · person>', 'Absence: <figure> — <why it is not recorded>'],
    kind: 'plain',
  },
]

/** The link the chain rests on when it is not being driven. */
export const FINAL_INDEX = CHAIN.length - 1

/**
 * The next link to show while the chain plays itself in, or `null` at the end.
 *
 * A separate function because the stop condition is the part worth testing: an
 * auto-advance that wraps turns the hero into an infinite ambient loop, which
 * DESIGN.md §6 rules out by name.
 */
export function nextIndex(current: number): number | null {
  return current >= FINAL_INDEX ? null : current + 1
}
