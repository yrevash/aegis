/**
 * The MCP console's two load-bearing sentences.
 *
 * `gatesAt` is what every "stops at the human gate" claim on the page resolves to, and
 * `consequenceOf` is the sentence an operator reads at the instant they lower a tier.
 * Both are pure, both are asserted here rather than through a rendered tree, and the
 * mutation each is proof against is named in its own test.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

const { RISKS, consequenceOf, gatesAt, tierProvenance } = await import(
  '../../src/components/mcp/mcpConsole.ts'
)

test('a tier at or above the deployment floor gates, and one below it does not', () => {
  // The default posture: floor HIGH, so only HIGH pauses.
  assert.equal(gatesAt('high', 'high'), true)
  assert.equal(gatesAt('medium', 'high'), false)
  assert.equal(gatesAt('low', 'high'), false)

  // A tenant that tightened to MEDIUM gets MEDIUM gated too. Hardcoding "high stops"
  // in the component would keep the page saying the opposite of what the graph does.
  assert.equal(gatesAt('medium', 'medium'), true)
  assert.equal(gatesAt('low', 'medium'), false)
  assert.equal(gatesAt('low', 'low'), true)
})

test('an unrecognised tier is treated as gated, never as free', () => {
  // The screen must never be the thing that says "unattended" about a value it does
  // not understand. Erring the other way is a wrong label; erring this way is a lie
  // about the one control the page exists to explain.
  assert.equal(gatesAt('catastrophic', 'high'), true)
  assert.equal(gatesAt('low', 'unheard-of'), true)
})

test('lowering a tier below the floor says out loud that nobody will see the call', () => {
  const ungated = consequenceOf('low', 'high')
  assert.match(ungated, /without a human seeing it first/)
  assert.match(ungated, /unattended/)
  assert.match(ungated, /LOW/)
  // …and it names the floor it is below, so the sentence is checkable rather than
  // asserted: an operator can see which setting produced it.
  assert.match(ungated, /high/)

  const gated = consequenceOf('high', 'high')
  assert.match(gated, /stops at the human gate/)
  assert.doesNotMatch(gated, /without a human/)
})

test('the consequence follows the floor, not a hardcoded tier', () => {
  // Same tier, two deployments. If MEDIUM read as "unattended" everywhere, an admin on
  // a tightened deployment would be told the opposite of what their gate will do.
  assert.match(consequenceOf('medium', 'high'), /without a human seeing it first/)
  assert.match(consequenceOf('medium', 'medium'), /stops at the human gate/)
})

test('an untouched tier is explained as a default, not as somebody decision', () => {
  assert.match(
    tierProvenance({ riskIsDefault: true, reason: '' }),
    /code we did not write/,
  )
  assert.equal(
    tierProvenance({ riskIsDefault: false, reason: 'read-only corpus search' }),
    'read-only corpus search',
  )
  // A lowered tier with no reason is called out rather than rendered blank: a blank
  // cell reads as "nothing to see", and this is the one row worth looking at.
  assert.match(
    tierProvenance({ riskIsDefault: false, reason: '' }),
    /no stated reason/,
  )
})

test('the tier list is ordered least to most consequential', () => {
  assert.deepEqual(RISKS, ['low', 'medium', 'high'])
})
