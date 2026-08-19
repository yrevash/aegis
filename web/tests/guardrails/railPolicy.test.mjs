/**
 * The rail-policy screen's one load-bearing claim: **provenance is reported, not guessed.**
 *
 * The badge is the whole reason this screen exists — "the platform decided this and you
 * cannot relax it" and "you tightened this" are different sentences about the same
 * number, and four `guardrails.*` controls once shipped badged "Your setting" while
 * binding nothing. So the two tests that matter are: the badge follows the server's
 * `source` and never the values beside it, and a control this build has never met still
 * renders rather than disappearing.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import {
  addedMembers,
  controlName,
  formatValue,
  provenanceOf,
} from '../../src/components/guardrails/railPolicy.ts'

test('the badge follows the server’s source, not the values beside it', () => {
  // A tenant row that LOST to a stricter platform value: the tenant wrote something,
  // it is not in force, and the server says `platform`. Badging it "your setting" here
  // would be the exact lie the source field exists to prevent.
  assert.deepEqual(provenanceOf({ source: 'platform' }), {
    label: 'platform floor',
    mine: false,
  })
  assert.deepEqual(provenanceOf({ source: 'tenant' }), {
    label: 'your tenant setting',
    mine: true,
  })
  assert.deepEqual(provenanceOf({ source: 'user' }), {
    label: 'your user setting',
    mine: true,
  })
})

test('the screen never recomputes provenance from the values it was handed', () => {
  const source = readFileSync(
    fileURLToPath(new URL('../../src/components/guardrails/railPolicy.ts', import.meta.url)),
    'utf8',
  )
  assert.ok(
    !source.includes('platform_value ==') && !source.includes('=== row.platform_value'),
    'provenance is the server’s answer — it compares the folded policy against the floor, ' +
      'which is the only place both are known; a second comparison here would be a second policy',
  )
})

test('a control this build has never met is still named and rendered', () => {
  assert.equal(controlName('guardrails.pii.block'), 'Personal data')
  assert.equal(controlName('guardrails.something.new'), 'guardrails.something.new')
})

test('a value is rendered in the shape its declared type actually has', () => {
  assert.equal(formatValue(true, 'bool'), 'on')
  assert.equal(formatValue(false, 'bool'), 'off')
  assert.equal(formatValue(1000, 'int'), '1000')
  assert.equal(formatValue([], 'list'), 'none')
  assert.equal(formatValue(['jwt', 'iban'], 'list'), 'jwt, iban')
})

test('the added members are the tenant’s own, and absent means none', () => {
  assert.deepEqual(addedMembers({ added: ['aws_access_key_id'] }), ['aws_access_key_id'])
  assert.deepEqual(addedMembers({ added: null }), [])
})
