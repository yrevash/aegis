/**
 * "output blocked" must not be printed above an answer that is on screen.
 *
 * The answer panel computed `passed = verdict === 'pass'` and rendered
 * `passed ? 'output checked' : 'output blocked'`, collapsing four verdicts into two. A
 * `redact` — the text was altered — and a `flag` — advisory, the text is untouched —
 * both came out as a refusal that never happened, in the block tone, over a fully
 * rendered answer.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { outputVerdict } from '../../src/components/console/outputVerdict.ts'

const rail = (verdict, stage = 'output', layer = null) => ({
  type: 'guardrail',
  run_id: 'r1',
  seq: 1,
  stage,
  verdict,
  layer,
  reason: '',
  redactions: [],
  before_masked: null,
  after: null,
})

test('each verdict is its own fact', () => {
  assert.deepEqual(outputVerdict([rail('pass')]), {
    verdict: 'pass', tone: 'ok', label: 'output checked',
  })
  assert.deepEqual(outputVerdict([rail('flag')]), {
    verdict: 'flag', tone: 'risk', label: 'output flagged',
  })
  assert.deepEqual(outputVerdict([rail('redact')]), {
    verdict: 'redact', tone: 'risk', label: 'output redacted',
  })
  assert.deepEqual(outputVerdict([rail('block')]), {
    verdict: 'block', tone: 'block', label: 'output blocked',
  })
})

test('a pass from one rail never hides a redaction from another', () => {
  // Content safety passes, the grounding rail flags, a PII rail redacts. The badge is
  // the strongest of them — order on the wire must not decide what the user is told.
  const rails = [rail('pass', 'output', 'content_safety'), rail('flag', 'output', 'grounding'), rail('redact', 'output', 'pii')]
  assert.equal(outputVerdict(rails).label, 'output redacted')
  assert.equal(outputVerdict([...rails].reverse()).label, 'output redacted')
})

test('only the output stage speaks for the output', () => {
  assert.equal(outputVerdict([rail('block', 'input')]), null)
  assert.equal(outputVerdict([]), null)
})

test('a verdict this build has never heard of degrades to its own code, not to a pass', () => {
  const unknown = outputVerdict([rail('pass'), rail('quarantine')])
  assert.equal(unknown.verdict, 'quarantine')
  assert.equal(unknown.label, 'output quarantine')
  assert.notEqual(unknown.tone, 'ok')
})
