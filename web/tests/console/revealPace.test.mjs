/**
 * The caret must always be behind the wire, never ahead of it — and must never stall.
 *
 * Every character the console types out is a character the backend already sent; the
 * one thing {@link advanceReveal} decides is when it appears. Two failures would matter
 * and both are cheap to assert: revealing past what arrived (which would mean the
 * console is inventing text) and a rate that decays to nothing (which would leave an
 * answer permanently three characters short of finished).
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  advanceReveal,
  MIN_CHARS_PER_SECOND,
  REVEAL_WINDOW_MS,
} from '../../src/components/console/revealPace.ts'

/** Run the curve at 60fps until it catches up, returning the milliseconds it took. */
function drain(total, frameMs = 16) {
  let revealed = 0
  let elapsed = 0
  while (revealed < total) {
    revealed = advanceReveal(revealed, total, frameMs)
    elapsed += frameMs
    assert.ok(elapsed < 60_000, 'the reveal never finished')
  }
  return elapsed
}

test('never reveals a character the wire has not sent', () => {
  assert.equal(advanceReveal(0, 40, 10_000), 40)
  assert.equal(advanceReveal(40, 40, 10_000), 40)
})

test('never goes backwards, and a zero delta moves nothing', () => {
  assert.equal(advanceReveal(12, 100, 0), 12)
  assert.equal(advanceReveal(12, 100, -5), 12)
})

test('a whole answer types out in a couple of seconds, long or short', () => {
  const short = drain(180)
  const long = drain(3000)
  assert.ok(short > 300, `a 180-char answer flashed by in ${short}ms`)
  assert.ok(long < 6000, `a 3000-char answer took ${long}ms`)
  // The window sets the shape: the buffered remainder drains at a rate proportional to
  // itself, so ten times the text is nowhere near ten times the wait.
  assert.ok(long < short * 4, `the curve is not proportional: ${short}ms vs ${long}ms`)
})

test('the floor keeps the tail moving instead of asymptoting', () => {
  // One character left, and a proportional rate would crawl: the floor takes over.
  const stepped = advanceReveal(99, 100, 1000 / MIN_CHARS_PER_SECOND)
  assert.equal(stepped, 100)
  assert.ok(MIN_CHARS_PER_SECOND > 0 && REVEAL_WINDOW_MS > 0)
})

test('text that arrives mid-reveal raises the rate rather than queueing', () => {
  const frame = 16
  const slow = advanceReveal(0, 100, frame)
  const fast = advanceReveal(0, 4000, frame)
  assert.ok(fast > slow, 'a fuller buffer must drain faster')
})

test('a backgrounded tab catches up in one step rather than replaying frames', () => {
  assert.equal(advanceReveal(0, 500, 30_000), 500)
})
