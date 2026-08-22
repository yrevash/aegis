/**
 * What the live socket does when it drops.
 *
 * The bell holds one connection open for as long as the console is open, which on a
 * demo machine is all day, against a backend that restarts. So the failure mode that
 * matters is not "it does not reconnect" — it is "it reconnects too well": a stream that
 * dies on arrival and is retried immediately becomes a request loop against a backend
 * that is already unwell, from every open tab, and the console is then the reason the
 * platform stays down.
 *
 * These tests pin the two halves of that: the delay ladder itself, and the fact that a
 * stream failing over and over walks *up* the ladder rather than sitting on its first
 * rung.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  isNotificationRow,
  retryDelayMs,
  subscribeNotifications,
} from '../../src/lib/api/notifications.ts'

test('no retry delay can round down to a tight loop, and none exceeds the cap', () => {
  for (let attempt = 0; attempt <= 20; attempt += 1) {
    for (const random of [() => 0, () => 0.5, () => 0.999]) {
      const delay = retryDelayMs(attempt, random)
      assert.ok(delay >= 800, `attempt ${attempt} waited ${delay}ms — that is a hot loop`)
      assert.ok(delay <= 36_000, `attempt ${attempt} waited ${delay}ms — past the 30s cap`)
    }
  }
  // Negative or fractional input is not a licence to retry instantly either.
  assert.ok(retryDelayMs(-5, () => 0) >= 800)
  assert.ok(retryDelayMs(Number.NaN, () => 0) >= 800)
})

test('the ladder is exponential and then flat at the cap', () => {
  const zero = (n) => retryDelayMs(n, () => 0)
  assert.deepEqual([zero(0), zero(1), zero(2), zero(3)], [800, 1600, 3200, 6400])
  // Every step is at least the previous one — a ladder that dips is a ladder that
  // hammers harder the longer the outage lasts.
  for (let n = 1; n <= 20; n += 1) {
    assert.ok(zero(n) >= zero(n - 1), `step ${n} is shorter than step ${n - 1}`)
  }
  assert.equal(zero(20), 24_000)
})

/** A body that is already at end-of-stream — the server accepting and hanging up. */
function closedBody() {
  return new ReadableStream({
    start(controller) {
      controller.close()
    },
  })
}

/** A body carrying one SSE frame, then end-of-stream. */
function frameBody(payload) {
  return new ReadableStream({
    start(controller) {
      controller.enqueue(
        new TextEncoder().encode(`event: notification\r\ndata: ${JSON.stringify(payload)}\r\n\r\n`),
      )
      controller.close()
    },
  })
}

/**
 * Run a subscription against a stubbed `fetch` and a recording `setTimeout`, and return
 * the delays it asked for.
 *
 * The recorder stops scheduling once `stopAfter` retries have been requested, which is
 * what keeps a test of an infinite retry loop finite.
 */
async function retriesAgainst(bodyFor, stopAfter) {
  const realSetTimeout = globalThis.setTimeout
  const realFetch = globalThis.fetch
  const delays = []
  const notifications = []
  let calls = 0

  globalThis.setTimeout = (fn, ms) => {
    delays.push(ms)
    if (delays.length < stopAfter) queueMicrotask(fn)
    return 0
  }
  globalThis.clearTimeout = () => {}
  globalThis.fetch = async () => {
    calls += 1
    return { ok: true, status: 200, body: bodyFor(calls) }
  }

  const subscription = subscribeNotifications('token', {
    onNotification: (row) => notifications.push(row),
  })
  for (let spin = 0; spin < 500 && delays.length < stopAfter; spin += 1) {
    await new Promise((resolve) => realSetTimeout(resolve, 0))
  }
  subscription.close()
  globalThis.setTimeout = realSetTimeout
  globalThis.fetch = realFetch
  return { delays, calls, notifications }
}

test('a stream that dies on arrival reconnects, and backs off as it keeps dying', async () => {
  const { delays, calls } = await retriesAgainst(() => closedBody(), 5)
  assert.equal(delays.length, 5)
  assert.equal(calls, 5, 'each scheduled retry should have opened exactly one connection')
  // The load-bearing assertion: the second failure waits longer than the first. A
  // counter that resets on every successful *open* would give five identical 1s waits
  // here, because opening is exactly what this backend keeps doing before it hangs up.
  for (let i = 1; i < delays.length; i += 1) {
    assert.ok(
      delays[i] > delays[i - 1],
      `retry ${i} waited ${delays[i]}ms after ${delays[i - 1]}ms — the ladder is not climbing`,
    )
  }
  assert.ok(delays[0] >= 800)
})

test('a notification on the wire reaches the subscriber, and junk on it does not', async () => {
  const row = {
    id: 'n1',
    kind: 'job.succeeded',
    severity: 'info',
    title: 'Job 412 finished',
    body: '100 documents ingested.',
    entity_ref: 'job:412',
    href: '/app/platform_admin/jobs',
    created_at: '2026-08-23T10:00:00Z',
    read_at: null,
  }
  const { notifications } = await retriesAgainst(
    (call) => (call === 1 ? frameBody(row) : frameBody({ ping: true })),
    3,
  )
  assert.deepEqual(notifications, [row])
})

test('close() cancels the pending retry — a signed-out session holds no socket', async () => {
  const realSetTimeout = globalThis.setTimeout
  const realFetch = globalThis.fetch
  let pending = null
  let calls = 0
  globalThis.setTimeout = (fn) => {
    pending = fn
    return 0
  }
  globalThis.clearTimeout = () => {
    pending = null
  }
  globalThis.fetch = async () => {
    calls += 1
    return { ok: true, status: 200, body: closedBody() }
  }

  const subscription = subscribeNotifications('token', { onNotification: () => {} })
  for (let spin = 0; spin < 50 && pending === null; spin += 1) {
    await new Promise((resolve) => realSetTimeout(resolve, 0))
  }
  assert.equal(calls, 1)
  subscription.close()
  assert.equal(pending, null, 'the queued reconnect survived close()')

  globalThis.setTimeout = realSetTimeout
  globalThis.fetch = realFetch
})

test('the frame guard refuses anything that is not a notification', () => {
  assert.equal(isNotificationRow({ id: 'a', kind: 'k', title: 't', created_at: 'x' }), true)
  assert.equal(isNotificationRow({ id: 'a', kind: 'k', title: 't' }), false)
  assert.equal(isNotificationRow({ ping: true }), false)
  assert.equal(isNotificationRow(null), false)
  assert.equal(isNotificationRow('n1'), false)
})
