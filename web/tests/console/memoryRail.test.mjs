/**
 * The three claims the memory rail makes about itself.
 *
 * The user asked twice for this surface not to become cluttered, so the cap is code and
 * the code is tested: the rail opens with exactly one card, never holds more than three,
 * and never offers a card whose reading is not there. Everything else about the rail is
 * a render detail.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_OPEN_CARDS,
  availableCards,
  cardToEvict,
  initialOpenCards,
  memoryAvailable,
  openCard,
  railExplanation,
} from '../../src/components/console/memoryCards.ts'

const ALL = { memory: true, budget: true }

test('the rail opens with exactly one card, and it is what the person asked for', () => {
  assert.deepEqual(initialOpenCards(ALL), ['remembered'])
})

test('a fourth card closes the least-recently-opened one, and the menu says which', () => {
  let open = initialOpenCards(ALL)
  open = openCard(open, 'conversations')
  open = openCard(open, 'recall')
  assert.deepEqual(open, ['remembered', 'conversations', 'recall'])

  // The rail is full: the menu names the card the next add will close.
  assert.equal(cardToEvict(open, 'budget'), 'remembered')

  open = openCard(open, 'budget')
  assert.equal(open.length, MAX_OPEN_CARDS)
  assert.deepEqual(open, ['conversations', 'recall', 'budget'])

  // Re-opening something already open is a no-op, not an eviction.
  assert.deepEqual(openCard(open, 'recall'), open)
  assert.equal(cardToEvict(open, 'recall'), null)
})

test('a card whose reading is missing is never offered', () => {
  const ids = (caps) => availableCards(caps).map((card) => card.id)

  // `/me/budget` does not answer for this caller: no budget card, anywhere.
  assert.equal(ids({ memory: true, budget: false }).includes('budget'), false)

  // No memory subject on this bearer: the three memory cards are withheld and the rail
  // opens empty rather than showing panels over a subject it cannot read.
  assert.deepEqual(ids({ memory: false, budget: true }), ['budget'])
  assert.deepEqual(initialOpenCards({ memory: false, budget: true }), [])
})

/**
 * What the rail says when it has no cards up, which is where two audited defects lived.
 *
 * A rail with a readable budget and no `userId` got "0/3 · Every card is closed. Add one
 * to see what the agent has kept…" — blaming the person for closing cards nobody had
 * offered them, because the "not scoped to a memory subject" line was gated on
 * `offered.length === 0` and the budget card made that one. And a subject the store
 * refuses had no sentence at all, because that case was rendered *inside* an open card
 * as `Could not load. GET /memory/facts?subject=user%3A5... failed: 403 Forbidden`.
 */
test('an unscoped sign-in is told it has no subject, not that it closed its own cards', () => {
  // The audited combination: `/me/budget` answers, the bearer carries no user id.
  const line = railExplanation('unscoped', 0, availableCards({ memory: false, budget: true }).length)

  assert.doesNotMatch(line, /closed/i, 'nobody closed anything; nothing was ever offered')
  assert.match(line, /not scoped to a memory subject/)
})

test('a refused subject withholds its cards and says so, instead of printing the 403', () => {
  assert.equal(memoryAvailable('refused'), false, 'a card whose read is refused is not offered')

  const line = railExplanation('refused', 0, 1)
  assert.match(line, /may not read the memory store/)
  assert.doesNotMatch(line, /403|Forbidden|GET |failed:/, 'the rail never quotes the transport')
})

test('a backend hiccup keeps the cards — the reading is absent now, not for this person', () => {
  // `useAsync` reports a 500 the same way it reports a 403; only `isAuthFailure` in
  // MemoryRail separates them, and getting that wrong makes cards vanish on a blip.
  assert.equal(memoryAvailable('readable'), true)
  assert.equal(memoryAvailable('probing'), true, 'no flicker while the first read is in flight')
})

test('"every card is closed" is only said to somebody who actually closed one', () => {
  assert.equal(railExplanation('readable', 1, 4), null, 'a rail with cards up explains nothing')
  assert.match(railExplanation('readable', 0, 4), /Every card is closed/)
})
