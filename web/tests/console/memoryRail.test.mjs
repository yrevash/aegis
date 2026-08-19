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
  openCard,
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
