/**
 * The activation chip fires on a skill load, and on nothing else (§10.3).
 *
 * One claim worth a test. The chip is the visible half of progressive disclosure: it is
 * what turns "the agent decided it needed a skill" from an assertion into something a
 * user watches happen. Two ways it could go wrong and both are silent — it never fires
 * (the feature looks identical to not having it) or it fires on every tool call (the
 * trace claims a skill loaded when a status change did).
 */

import assert from 'node:assert/strict'
import { test } from 'node:test'

import { loadedSkillName } from '../../src/components/skills/loadedSkill.ts'

test('a load_skill tool call names the skill it activated', () => {
  assert.equal(
    loadedSkillName({ type: 'tool_call', tool: 'load_skill', args: { name: 'refund_policy' } }),
    'refund_policy',
  )
})

test('no other event activates a skill', () => {
  // The failure that matters: any tool call lighting the chip would make the trace
  // claim a skill was read on a turn that read none.
  assert.equal(
    loadedSkillName({ type: 'tool_call', tool: 'update_request_status', args: { id: 'r-1' } }),
    null,
  )
  assert.equal(loadedSkillName({ type: 'tool_result', ok: true }), null)
  assert.equal(loadedSkillName({ type: 'tool_call', tool: 'load_skill', args: {} }), null)
  assert.equal(loadedSkillName({ type: 'tool_call', tool: 'load_skill' }), null)
})
