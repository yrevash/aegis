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

import type { McpRisk, McpToolRow } from '@/lib/api/mcp'

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
