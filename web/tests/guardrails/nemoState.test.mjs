/**
 * Unknown is not false.
 *
 * `GET /security/posture` is platform-only — `require_infra_reader` refuses a
 * tenant-pinned principal outright — so `signals` is null for every tenant's own
 * analyst. The badge read `signals?.nemo_available ?? false`, which turned "you are not
 * allowed to read this" into **NOT INSTALLED**, about a package (`nemoguardrails
 * 0.23.0`) that is installed and that the same endpoint reports as `nemo_available:
 * true` to platform staff at the same instant. Two accounts, one process, contradictory
 * claims about a fact of the deployment.
 *
 * A refusal has to survive as a refusal all the way to the badge, which is the one
 * thing a `??` on a nullable boolean cannot do.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { nemoState } from '../../src/components/guardrail/postureState.ts'

test('no posture is unknown, never "not installed"', () => {
  assert.equal(nemoState(null), 'unknown')
})

test('a posture that answered is reported as it answered', () => {
  assert.equal(nemoState({ nemo_available: true }), 'available')
  // The genuine negative still exists and still reads the same. The fix adds a third
  // answer; it does not turn every false into an unknown.
  assert.equal(nemoState({ nemo_available: false }), 'not installed')
})
