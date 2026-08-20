/**
 * The width the composer asks for, and the width the run reports.
 *
 * One claim, and the two ways it becomes a lie. The mode menu writes `depth_mode` and
 * `requested_fanout` onto the wire, and the run answers with a `routing` event carrying
 * `decided_by` — the tenant's `max_parallel_agents` can clamp a five-agent ask to three,
 * and a tenant with no team roster runs a Team request as a single lane. A console that
 * echoed the chosen mode back as the outcome would be wrong on screen in exactly those
 * two cases, which are the two the fan-out demo turns on.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { widthOutcome, wireMode } from '../../src/components/console/runMode.ts'

/** A `routing` event carrying only the fields the outcome reads. */
function routingOf(overrides) {
  return { depth: 'single', fanout: 0, decided_by: 'auto', reason: 'the classifier chose it', ...overrides }
}

test('a clamped team says what was asked for and what actually ran', () => {
  const outcome = widthOutcome(
    { depth: 'team', fanout: 5 },
    routingOf({ depth: 'team', fanout: 3, decided_by: 'platform_cap', reason: 'max_parallel_agents=3' }),
  )
  assert.equal(outcome.ran, 'Team of 3', 'the width shown is the run’s, never the request')
  assert.equal(outcome.differs, true)
  assert.match(outcome.note, /asked for 5 agents/)
  assert.match(outcome.note, /clamped to 3/)
  assert.equal(outcome.decidedByCode, 'platform_cap', 'the receipt names the run’s own code')
})

test('a team request the tenant cannot honour is reported as the single lane it ran as', () => {
  const outcome = widthOutcome(
    { depth: 'team', fanout: null },
    routingOf({ decided_by: 'tenant_default', reason: 'no team roster for this tenant' }),
  )
  assert.equal(outcome.ran, 'Single lane')
  assert.equal(outcome.differs, true)
  assert.match(outcome.note, /went out as a single lane/)
})

test('Auto never claims an override, because nothing was asked for', () => {
  const outcome = widthOutcome({ depth: 'auto', fanout: null }, routingOf({ depth: 'team', fanout: 4 }))
  assert.equal(outcome.differs, false)
  assert.equal(outcome.note, null)
  assert.equal(outcome.ran, 'Team of 4')
  assert.equal(outcome.decidedBy, 'the router')
})

test('an honoured team request is not dressed up as a correction', () => {
  const outcome = widthOutcome(
    { depth: 'team', fanout: 3 },
    routingOf({ depth: 'team', fanout: 3, decided_by: 'user', reason: 'honoured exactly' }),
  )
  assert.equal(outcome.differs, false)
  assert.equal(outcome.note, null)
  assert.equal(outcome.decidedBy, 'you')
})

test('a fanout never travels without the team mode that makes it legal', () => {
  // The server rejects `requested_fanout` in any mode but `team`, so a degree left over
  // from a previous Team selection must be dropped rather than posted and 422'd.
  assert.deepEqual(wireMode({ depth: 'single', fanout: 4 }), {
    depthMode: 'single',
    requestedFanout: null,
  })
  assert.deepEqual(wireMode({ depth: 'auto', fanout: 4 }), {
    depthMode: null,
    requestedFanout: null,
  })
  assert.deepEqual(wireMode({ depth: 'team', fanout: 4 }), {
    depthMode: 'team',
    requestedFanout: 4,
  })
})
