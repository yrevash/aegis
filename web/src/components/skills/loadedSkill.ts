/**
 * Which skill a trace event activated — the one string tying the browser to the tool.
 *
 * Split out of the chip component because it is the whole of the logic and none of the
 * rendering: kept in a `.tsx` it could only be exercised through a component, and the
 * failure worth catching here is not visual. The chip firing on *every* tool call would
 * make the trace claim a skill was read on a turn that read none, and that is a
 * correctness bug in the glass box, not a styling one.
 *
 * @see backend/src/app/agent/skills_tool.py — `LOAD_SKILL_TOOL`
 */

/** The reserved platform tool name. Must match `app.agent.skills_tool.LOAD_SKILL_TOOL`. */
export const LOAD_SKILL_TOOL = 'load_skill'

/** Return the skill name a `tool_call` event activated, or `null` if it is not one. */
export function loadedSkillName(event: {
  type?: string
  tool?: string
  args?: unknown
}): string | null {
  if (event.type !== 'tool_call' || event.tool !== LOAD_SKILL_TOOL) return null
  const args = event.args as { name?: unknown } | null | undefined
  const name = typeof args?.name === 'string' ? args.name.trim() : ''
  return name === '' ? null : name
}
