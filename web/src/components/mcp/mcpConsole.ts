/**
 * The MCP console's decisions, as pure functions — framework-free and directly tested.
 *
 * Two of them carry real weight and neither belongs inside a component:
 *
 * - {@link gatesAt} decides whether a tier still stops at the human gate, which is what
 *   the console *claims* beside every tier it shows. Deriving it from the deployment's
 *   own floor rather than hardcoding "high stops" is the difference between a sentence
 *   that stays true when a tenant tightens the floor and one that silently starts lying.
 * - {@link consequenceOf} is the sentence an operator reads at the moment they change a
 *   tier. A dropdown that says "low" and nothing else asks somebody to hold the gating
 *   rule in their head; this says what will actually happen to a call.
 *
 * `web/tests/components/mcpConsole.test.mjs` exercises both directly.
 */

import type { McpProbe, McpRisk, McpToolRow } from '@/lib/api/mcp'

/** The three tiers, in the order a reader ranks them. Mirrors `aegis.core.types.RiskLevel`. */
export const RISKS: McpRisk[] = ['low', 'medium', 'high']

/**
 * Whether a tool at `risk` stops at the human gate on a deployment whose floor is `floor`.
 *
 * @param risk - The tier the tool is gated at.
 * @param floor - This deployment's `agent.gate_min_risk`, from the console aggregate.
 * @returns True when a call pauses for a human.
 */
export function gatesAt(risk: McpRisk, floor: McpRisk): boolean {
  const at = RISKS.indexOf(risk)
  const min = RISKS.indexOf(floor)
  if (at < 0 || min < 0) return true // an unknown tier is treated as gated, never as free
  return at >= min
}

/**
 * The sentence shown beside a tier the operator is about to set.
 *
 * Written in the second person and in the present tense, because it describes what the
 * platform will do — not what the field is called. The gated case names the gate; the
 * ungated case says the quiet part out loud, which is the whole reason this function
 * exists rather than a label reading "low".
 *
 * @param risk - The tier being considered.
 * @param floor - This deployment's gate floor.
 * @returns One sentence naming the consequence of that tier.
 */
export function consequenceOf(risk: McpRisk, floor: McpRisk): string {
  if (gatesAt(risk, floor)) {
    return `${risk.toUpperCase()} is at or above this deployment's gate floor (${floor}), so every call stops at the human gate and waits for an approval.`
  }
  return `${risk.toUpperCase()} is below this deployment's gate floor (${floor}), so this runs without a human seeing it first — an agent can call it, and the result enters the answer, unattended.`
}

/**
 * The one-line provenance for a tier: whose decision it was, or that nobody has made one.
 *
 * @param tool - A tool row from the console aggregate.
 * @returns A sentence for the cell under the tier.
 */
export function tierProvenance(tool: Pick<McpToolRow, 'riskIsDefault' | 'reason'>): string {
  if (tool.riskIsDefault) {
    return 'The honest default for code we did not write, reached over a network, that cannot undo itself.'
  }
  return tool.reason || 'Lowered with no stated reason.'
}

/**
 * The four states a declared peer can be in, from this console's point of view.
 *
 * The page used to show only `enabled`/`disabled`, which answers a configuration
 * question and not the one a reader actually has — *is it connected?* Those are
 * different: a peer can be enabled and unreachable, and the old page rendered that as a
 * green badge.
 *
 * `answered` is claimed only where something really answered in this process: either
 * the probe in hand says so, or the peer has tools that could only have arrived from a
 * successful `tools/list`. Absent both, the state is `untested` — never `answered` by
 * optimism.
 */
export type PeerState = 'disabled' | 'answered' | 'unreachable' | 'untested'

/**
 * Which of {@link PeerState} a peer is in.
 *
 * @param server - The peer's row from the console aggregate.
 * @param probe - The result of the most recent Test **on this peer**, or null.
 */
export function peerState(
  server: { enabled: boolean; discoveredTools: number },
  probe: McpProbe | null,
): PeerState {
  if (!server.enabled) return 'disabled'
  if (probe) return probe.reachable ? 'answered' : 'unreachable'
  return server.discoveredTools > 0 ? 'answered' : 'untested'
}

/** What each peer state is called, and what it means for an agent, in one clause. */
export const PEER_STATE_TEXT: Record<PeerState, { label: string; means: string }> = {
  answered: {
    label: 'connected',
    means: 'it answered the protocol handshake and listed its tools',
  },
  unreachable: {
    label: 'not answering',
    means: 'the last Test did not complete, so no tool of its is offered',
  },
  untested: {
    label: 'not tested yet',
    means: 'nothing has been discovered from it — press Test',
  },
  disabled: {
    label: 'disabled',
    means: 'its tools leave the agent’s payload entirely; the configuration stays',
  },
}
