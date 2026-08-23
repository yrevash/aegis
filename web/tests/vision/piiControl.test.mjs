/**
 * A control that did not run found nothing; that is not zero.
 *
 * On a run the injection screen refused, the ladder printed *"Image PII — did not run —
 * not reached, injection_screen refused first"* and the tile beside it printed
 * **"PII regions found 0"** with *"The image-PII control found no regions."* under it.
 * `pii_regions: []` is the control's silence on that run, and the screen was reading it
 * as the control's answer.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { piiControlRan } from '../../src/components/vision/piiControl.ts'

const at = (stage, outcome) => ({ stage, outcome, detail: null })

test('a stage that did not run, or failed closed, did not measure anything', () => {
  assert.equal(piiControlRan([at('injection_screen', 'blocked'), at('image_pii', 'not_run')]), false)
  assert.equal(piiControlRan([at('image_pii', 'failed_closed')]), false)
  // A ladder that never mentions the stage is the same silence, not a pass.
  assert.equal(piiControlRan([at('injection_screen', 'blocked')]), false)
  assert.equal(piiControlRan(null), false)
})

test('a stage that ran did measure, and an empty result is then a real zero', () => {
  assert.equal(piiControlRan([at('image_pii', 'passed')]), true)
  assert.equal(piiControlRan([at('image_pii', 'redacted')]), true)
  assert.equal(piiControlRan([at('image_pii', 'blocked')]), true)
})
