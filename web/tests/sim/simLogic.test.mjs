/**
 * The Access demo's two claims about a run, and the two ways they were false.
 *
 * The demo is the centrepiece of the RBAC story, so a panel whose evidence
 * contradicts its own headline is worse than no panel. Both defects were exactly that:
 *
 *  - the row headed **Status change** — a write — reported `find_requests denied`, a
 *    *read*, beside a "Human approval" cell saying the run was still parked at its
 *    gate. `toolMark` named `toolCalls[0]` (whatever the run reached for first) and
 *    took its verdict from `toolResults.some(r => !r.ok)` (any failure anywhere).
 *  - the panel headed **What each role was allowed to rank** drew the identical six
 *    documents at identical scores for both roles, under a table row asserting the
 *    retrieval scope differs.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { rankedDivergence, toolMark } from '../../src/components/sim/simLogic.ts'

/** A `RunState`, cut down to the fields these two functions read. */
function state({ toolCalls = [], toolResults = [] } = {}) {
  return { toolCalls, toolResults }
}

const call = (call_id, tool, risk) => ({ type: 'tool_call', call_id, tool, args: {}, risk })
const result = (call_id, ok) => ({ type: 'tool_result', call_id, ok, summary: '' })

test('the outcome names the consequential call, not the first one the run made', () => {
  // The seeded run reads before it writes. Under the old rule the row was named by
  // `find_requests` for the whole run, so a write that executed cleanly was reported
  // under the name of a read.
  const s = state({
    toolCalls: [call('c1', 'find_requests', 'low'), call('c2', 'update_request_status', 'high')],
    toolResults: [result('c1', true), result('c2', true)],
  })
  assert.deepEqual(toolMark(s), { mark: 'allow', label: 'update_request_status executed' })
})

test('a failed read does not mark the write denied', () => {
  // The exact shape the audit saw. `some(r => !r.ok)` made *any* failure the verdict
  // on *the* named call; the verdict now comes from that call's own `call_id`.
  const s = state({
    toolCalls: [call('c1', 'find_requests', 'low'), call('c2', 'update_request_status', 'high')],
    toolResults: [result('c1', false), result('c2', true)],
  })
  assert.deepEqual(toolMark(s), { mark: 'allow', label: 'update_request_status executed' })
})

test('a call with no result yet is proposed, and no call at all is nothing', () => {
  const parked = state({ toolCalls: [call('c2', 'update_request_status', 'high')] })
  assert.deepEqual(parked, parked)
  assert.equal(toolMark(parked).mark, 'gate')
  assert.equal(toolMark(parked).label, 'update_request_status proposed')
  assert.deepEqual(toolMark(state()), { mark: 'none', label: '—' })
})

test('a genuinely denied write is still reported denied', () => {
  const s = state({
    toolCalls: [call('c2', 'update_request_status', 'high')],
    toolResults: [result('c2', false)],
  })
  assert.deepEqual(toolMark(s), { mark: 'deny', label: 'update_request_status denied' })
})

const src = (id) => ({ id, label: id, score: -1 })
const NAMES = ['the operations lead', 'the client']

test('two identical ranked lists are said to be identical, and why that is consistent', () => {
  // The measured case: six ids, the same six, at the same scores. The scope difference
  // is real and it is upstream — it lives in the candidate counts, not in the top-k.
  const both = ['a', 'b', 'c', 'd', 'e', 'f'].map(src)
  const d = rankedDivergence(both, both, 58, 52, NAMES)
  assert.equal(d.same, true)
  assert.equal(d.shared, 6)
  assert.equal(d.onlyA, 0)
  assert.equal(d.onlyB, 0)
  assert.match(d.note, /same 6 sources/)
  assert.match(d.note, /58 candidates/)
  assert.match(d.note, /52/)
})

test('a real divergence is counted per lane rather than asserted', () => {
  const ops = ['a', 'b', 'c'].map(src)
  const cli = ['b', 'c', 'z'].map(src)
  const d = rankedDivergence(ops, cli, 58, 52, NAMES)
  assert.equal(d.same, false)
  assert.equal(d.shared, 2)
  assert.equal(d.onlyA, 1)
  assert.equal(d.onlyB, 1)
  assert.match(d.note, /1 only the operations lead could rank/)
  assert.match(d.note, /1 only the client could rank/)
})

test('nothing ranked yet claims neither sameness nor difference', () => {
  const d = rankedDivergence([], [], 0, 0, NAMES)
  assert.equal(d.same, false)
  assert.match(d.note, /has ranked a source yet/)
})
