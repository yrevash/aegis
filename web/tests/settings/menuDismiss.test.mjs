/**
 * The top-bar text-size menu was a no-op with a mouse, and this is the rule that fixes it.
 *
 * The control the owner asked for — raise the type from anywhere, without first finding
 * a settings screen — worked only from the keyboard. Its rows are `sr-only` radios inside
 * `<label>`s, a label takes no focus, so a mouse press moved focus to nothing: `blur`
 * fired with `relatedTarget === null`, the panel unmounted on **mousedown**, and the
 * `click` that would have chosen 125% landed on a popover that no longer existed. Two
 * independent audits found it; a browser reproduction had `mouse.down()` alone leaving
 * `aria-expanded="false"` and zero radios in the DOM.
 *
 * The first test below is that regression. The rest are the behaviours it must not cost:
 * a press outside still dismisses, and tabbing out still dismisses.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  shouldCloseOnBlur,
  shouldCloseOnPointerDown,
} from '../../src/components/settings/menuDismiss.ts'

/** Stand-ins for DOM nodes. Neither predicate inspects them beyond identity. */
const INSIDE = { id: 'a radio inside the panel' }
const OUTSIDE = { id: 'something else on the page' }
const contains = (node) => node === INSIDE

test('a press on a row that takes no focus does not close the menu under the finger', () => {
  // The whole defect: the browser could not name where focus went, which is what a press
  // on a `<label>`, on padding, or on the panel background looks like.
  assert.equal(shouldCloseOnBlur(true, null, contains), false)

  // And the press itself is inside, so the pointer half must not close it either.
  assert.equal(shouldCloseOnPointerDown(true, INSIDE, contains), false)
})

test('tabbing out still closes it, and moving inside it does not', () => {
  assert.equal(shouldCloseOnBlur(true, OUTSIDE, contains), true)
  assert.equal(shouldCloseOnBlur(true, INSIDE, contains), false)
})

test('a press anywhere outside closes it', () => {
  assert.equal(shouldCloseOnPointerDown(true, OUTSIDE, contains), true)
  // An event carrying no target at all is treated as outside — it cannot be shown to be
  // inside, and a menu that will not dismiss is worse than one that dismisses early.
  assert.equal(shouldCloseOnPointerDown(true, null, contains), true)
})

test('neither predicate acts on a menu that is already closed', () => {
  assert.equal(shouldCloseOnBlur(false, OUTSIDE, contains), false)
  assert.equal(shouldCloseOnPointerDown(false, OUTSIDE, contains), false)
})
