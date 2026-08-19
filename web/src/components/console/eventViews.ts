/**
 * Small normalising views over the stream events the console groups by.
 *
 * Task 6.1 landed `Reflection`, `RoutingEvent`, `AgentStatus`, `SynthesisEvent` and
 * `MemoryEvent` in `web/src/lib/stream.ts`, so everything here narrows on the real
 * discriminated union — there is no second copy of the wire contract in this file and
 * nothing invents a field the backend does not send.
 *
 * What the views add is the two things the union deliberately does not:
 *
 * - **Attribution.** {@link agentIdOf} and {@link agentIdOfNode} answer "which lane does
 *   this belong to?" from the two places the answer can live — the envelope's optional
 *   `agent_id`, and the `agent:<id>` node-name convention Phase 5 stamps on a lane's
 *   graph nodes. A reader returns `null` rather than guessing, which is what lets the
 *   agent panel degrade instead of inventing cards.
 * - **Roster parsing.** `SynthesisEvent.contributing` / `.omitted` are typed
 *   `Record<string, unknown>[]` on the wire, because the backend declares them as loose
 *   dicts. {@link readSynthesis} turns them into a checked shape once, here, so no
 *   rendering surface has to.
 */

import type { StreamEvent, SynthesisMember as RawSynthesisMember } from '@/lib/stream'

const asString = (value: unknown, fallback = ''): string =>
  typeof value === 'string' ? value : fallback

/** The `node:` id convention Phase 5 stamps on a sub-agent's graph nodes. */
const AGENT_NODE_PREFIX = 'agent:'

/**
 * The sub-agent id an event belongs to, or `null` for supervisor/graph-level events.
 *
 * `agent_id` is declared on `BaseEvent` as of commit `6af14f6`, so this is a typed read
 * — but it is optional on the wire and absent on every single-pass run, which is the
 * case the agent panel has to degrade for rather than assume away.
 */
export function agentIdOf(event: StreamEvent): string | null {
  const id = event.agent_id
  return typeof id === 'string' && id.length > 0 ? id : null
}

/**
 * The sub-agent id encoded in a graph node name, or `null`.
 *
 * Phase 5 names a fan-out lane's nodes `agent:<id>`, so a `node_finished` can be
 * attributed to its lane even when the envelope's `agent_id` did not survive.
 */
export function agentIdOfNode(node: string): string | null {
  if (!node.startsWith(AGENT_NODE_PREFIX)) return null
  const id = node.slice(AGENT_NODE_PREFIX.length)
  return id.length > 0 ? id : null
}

/** One agent named in the fan-out's merge, parsed from its loose wire dict. */
export interface SynthesisMember {
  agentId: string
  role: string
  label: string
  /** Terminal state, present on omitted members (e.g. `timeout`). */
  status: string
  /** Why it produced nothing usable, present on omitted members. */
  reason: string
}

/** The fan-out's merge: who is in the answer, and who is not. */
export interface SynthesisView {
  contributing: SynthesisMember[]
  omitted: SynthesisMember[]
  summary: string
}

function readMember(value: RawSynthesisMember): SynthesisMember | null {
  const agentId = asString(value.agent_id)
  if (agentId === '') return null
  return {
    agentId,
    role: asString(value.role),
    label: asString(value.label) || agentId,
    status: asString(value.status),
    reason: asString(value.reason),
  }
}

function readMembers(values: RawSynthesisMember[]): SynthesisMember[] {
  return values.map(readMember).filter((m): m is SynthesisMember => m !== null)
}

/** Read a `synthesis` merge, or `null` if this is not one. */
export function readSynthesis(event: StreamEvent): SynthesisView | null {
  if (event.type !== 'synthesis') return null
  return {
    contributing: readMembers(event.contributing),
    omitted: readMembers(event.omitted),
    summary: event.summary,
  }
}
