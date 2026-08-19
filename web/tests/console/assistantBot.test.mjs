/**
 * What the assistant bot has to get right: it stops moving when it is asked to.
 *
 * Reduced motion means no listener at all, and a streaming run means pupils forward — in
 * both cases the component writes {@link PUPIL_CENTRE} once and leaves it there, so the
 * only thing worth asserting is the decision and the clamp behind it.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  PUPIL_CENTRE,
  PUPIL_RANGE,
  pupilOffset,
  tracksPointer,
} from '../../src/components/console/botEyes.ts'

test('the eyes stop for reduced motion and for a running turn', () => {
  assert.equal(tracksPointer({ reducedMotion: true, running: false }), false)
  assert.equal(tracksPointer({ reducedMotion: true, running: true }), false)
  assert.equal(tracksPointer({ reducedMotion: false, running: true }), false)
  assert.equal(tracksPointer({ reducedMotion: false, running: false }), true)
  assert.deepEqual(PUPIL_CENTRE, { x: 0, y: 0 })
})

test('a pupil never leaves its ring, and rests centred on the head', () => {
  assert.deepEqual(pupilOffset({ x: 20, y: 20 }, { x: 20, y: 20 }), { x: 0, y: 0 })

  const far = pupilOffset({ x: 2000, y: 20 }, { x: 20, y: 20 })
  assert.equal(Math.round(far.x * 1e6) / 1e6, PUPIL_RANGE)
  assert.equal(Math.round(far.y * 1e6) / 1e6, 0)

  const diagonal = pupilOffset({ x: -900, y: -900 }, { x: 20, y: 20 })
  assert.ok(Math.hypot(diagonal.x, diagonal.y) <= PUPIL_RANGE + 1e-9)
  assert.ok(diagonal.x < 0 && diagonal.y < 0)
})
