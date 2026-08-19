/**
 * The chat-thread state machine, and the five ways it damages a conversation.
 *
 * `threadReducer` is the whole of the multi-chat surface — the rail, the tabs, and which
 * run each event belongs to — and it had no test at all. These are not one test per
 * action. They are the rules whose breakage a person sees: a rail that fills with
 * duplicate empty chats, a stored conversation listed twice, a chat re-pointed at
 * somebody else's conversation, a title that keeps changing under the cursor, and an
 * event landing in the wrong turn.
 *
 * `select_chat` and `restore` are left to the type system on purpose: both are a single
 * field assignment with no ordering, no identity and no cross-turn effect, so a test
 * would only restate the assignment.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  initialThreadState,
  localIdFor,
  threadReducer,
} from '../../src/components/console/threadReducer.ts'

const AT = 1_700_000_000_000

/** A thread holding one unstarted chat, the state the console mounts with. */
const fresh = () => initialThreadState('chat-1', AT)

/** One streamed answer chunk, the smallest event a turn can receive. */
const token = (text) => ({ type: 'token', run_id: 'r1', seq: 1, text })

test('a second new chat reuses the unstarted one instead of littering the rail', () => {
  let state = fresh()

  state = threadReducer(state, { kind: 'new_chat', sessionId: 'chat-2', at: AT })
  assert.equal(state.sessions.length, 1, 'an unstarted chat is the new chat')
  assert.equal(state.activeSessionId, 'chat-1', 'and the rail switches back to it')

  // Once it has a question in it, it is no longer spare and a new chat is a real one.
  state = threadReducer(state, {
    kind: 'ask',
    sessionId: 'chat-1',
    turnId: 't1',
    question: 'What changed?',
    at: AT,
  })
  state = threadReducer(state, { kind: 'new_chat', sessionId: 'chat-2', at: AT })
  assert.deepEqual(
    state.sessions.map((s) => s.id),
    ['chat-2', 'chat-1'],
    'a new chat goes to the top of the rail',
  )
  assert.equal(state.activeSessionId, 'chat-2')
})

test('stored conversations merge once, and a chat already bound is never listed twice', () => {
  let state = fresh()
  state = threadReducer(state, {
    kind: 'bind_session',
    sessionId: 'chat-1',
    serverId: 'srv-a',
  })

  const rows = [
    { id: 'srv-a', title: 'What changed?' },
    { id: 'srv-b', title: 'Older question' },
  ]
  state = threadReducer(state, { kind: 'sync_sessions', rows, at: AT })

  assert.deepEqual(
    state.sessions.map((s) => s.serverId),
    ['srv-a', 'srv-b'],
    'the open chat is the same conversation as srv-a, not a second copy of it',
  )

  // The rail re-reads `GET /sessions` on every token change; a merge that adds nothing
  // must return the same state, or every poll remounts the whole rail.
  const again = threadReducer(state, { kind: 'sync_sessions', rows, at: AT })
  assert.equal(again, state, 'a no-op merge is the same object')
})

test('a chat is bound to its conversation once and never re-pointed', () => {
  let state = fresh()
  state = threadReducer(state, { kind: 'bind_session', sessionId: 'chat-1', serverId: 'srv-a' })
  state = threadReducer(state, { kind: 'bind_session', sessionId: 'chat-1', serverId: 'srv-b' })

  assert.equal(
    state.sessions[0].serverId,
    'srv-a',
    'a second mint would move this chat onto another conversation mid-thread',
  )
})

test('the first question titles the chat, and the next one does not rename it', () => {
  let state = fresh()
  state = threadReducer(state, {
    kind: 'ask',
    sessionId: 'chat-1',
    turnId: 't1',
    question: '  What   changed in   the   release?  ',
    at: AT,
  })

  assert.equal(state.sessions[0].title, 'What changed in the release?', 'trimmed and collapsed')
  assert.equal(state.sessions[0].turns[0].question, '  What   changed in   the   release?  ')
  assert.equal(state.sessions[0].turns[0].run.running, true, 'the composer locks before run_started')

  state = threadReducer(state, {
    kind: 'ask',
    sessionId: 'chat-1',
    turnId: 't2',
    question: 'And the one before it?',
    at: AT,
  })
  assert.equal(state.sessions[0].title, 'What changed in the release?', 'the rail entry holds still')
  assert.equal(state.sessions[0].turns.length, 2)
})

test('an event reaches only the turn that owns it, and closing a settled turn changes nothing', () => {
  let state = fresh()
  state = threadReducer(state, {
    kind: 'ask',
    sessionId: 'chat-1',
    turnId: 't1',
    question: 'First',
    at: AT,
  })
  state = threadReducer(state, {
    kind: 'ask',
    sessionId: 'chat-1',
    turnId: 't2',
    question: 'Second',
    at: AT,
  })

  const before = state.sessions[0].turns[1]
  state = threadReducer(state, { kind: 'event', turnId: 't1', event: token('hello') })

  assert.equal(state.sessions[0].turns[0].run.answer, 'hello')
  assert.equal(state.sessions[0].turns[1], before, "the other turn's run is untouched")

  // An event for a turn nobody owns (a late arrival after the chat was dropped) must not
  // rebuild the thread and remount every card.
  assert.equal(
    threadReducer(state, { kind: 'event', turnId: 'gone', event: token('x') }),
    state,
  )

  // The transport always closes, including after a terminal event already cleared
  // `running`. The second clear must leave the turn identical, or the thread re-renders
  // once per finished run for no reason.
  state = threadReducer(state, { kind: 'closed', turnId: 't1' })
  assert.equal(state.sessions[0].turns[0].run.running, false)
  const settled = state.sessions[0].turns[0]
  state = threadReducer(state, { kind: 'closed', turnId: 't1' })
  assert.equal(state.sessions[0].turns[0], settled, 'closing twice is one close')
})

/**
 * A reload must land where the person was.
 *
 * The console opened a fresh empty chat on every load. The conversation being worked on
 * was still in the rail, one click away, but the thread said "Nothing has run yet" —
 * which reads as lost work. The resume rides on the same `GET /sessions` merge that
 * builds the rail, and it is deliberately timid: that merge is async and can land
 * seconds after mount, so it only ever moves a tab that has nothing of its own in it.
 */
test('a reload reopens the conversation this browser was last in', () => {
  const rows = [
    { id: 'srv-a', title: 'What changed?' },
    { id: 'srv-b', title: 'Older question' },
  ]
  const state = threadReducer(fresh(), {
    kind: 'sync_sessions',
    rows,
    at: AT,
    resumeServerId: 'srv-b',
  })

  assert.equal(state.activeSessionId, localIdFor('srv-b'))
  assert.equal(
    state.sessions.find((s) => s.id === state.activeSessionId).serverId,
    'srv-b',
    'the local key a resume looks up must be the one the merge minted',
  )
})

test('a resume never yanks somebody out of a chat they have already started', () => {
  let state = fresh()
  // The sync is async. By the time it lands, this person has typed a question.
  state = threadReducer(state, {
    kind: 'ask',
    sessionId: 'chat-1',
    turnId: 't1',
    question: 'What changed?',
    at: AT,
  })

  const resumed = threadReducer(state, {
    kind: 'sync_sessions',
    rows: [{ id: 'srv-b', title: 'Older question' }],
    at: AT,
    resumeServerId: 'srv-b',
  })

  assert.equal(resumed.activeSessionId, 'chat-1', 'the live chat wins over the remembered one')
})

test('a remembered conversation the server no longer lists leaves the tab where it is', () => {
  const state = threadReducer(fresh(), {
    kind: 'sync_sessions',
    rows: [{ id: 'srv-a', title: 'What changed?' }],
    at: AT,
    resumeServerId: 'srv-deleted',
  })

  assert.equal(state.activeSessionId, 'chat-1', 'a stale id resumes nothing, and breaks nothing')
})
