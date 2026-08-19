/**
 * A failure a person can act on, and a 401 that ends the session.
 *
 * Two defects, one seam. The console rendered `Could not load. GET
 * /memory/facts?subject=user%3A5... failed: 403 Forbidden` into the memory rail and
 * `The run stopped. POST /query failed: 401 Unauthorized` under the composer — strings
 * that name the route and the status and nothing the reader can do. And the 401 case was
 * worse than unhelpful: the header went on showing the username and a Sign out button
 * over a bearer the backend had already stopped accepting, because nothing connected a
 * refusal to the session that caused it.
 *
 * These cover the two rules that would be visibly wrong if they broke:
 *
 * - what a failure **says** (a sentence, never a route), and
 * - what a 401 **does** (ends the session, exactly once).
 *
 * `request` is exercised through the real `client.ts` against a stubbed `fetch`, because
 * a test of `apiMessage` alone would still pass on the day someone re-introduces
 * `throw new Error(\`${method} ${path} failed: …\`)` at the call site — which is the
 * form the defect actually took.
 */

import assert from 'node:assert/strict'
import test, { afterEach, beforeEach } from 'node:test'

import {
  ApiError,
  apiMessage,
  isAuthFailure,
  isExpiredSession,
  statusOf,
} from '../../src/lib/api/apiError.ts'
import {
  getAuthToken,
  reportSessionExpired,
  setAuthToken,
  setSessionExpiredHandler,
} from '../../src/lib/api/authToken.ts'
import { getGraph, getMemoryFacts } from '../../src/lib/api/client.ts'

/** Every status the table names, plus one it does not. */
const STATUSES = [401, 403, 404, 409, 413, 422, 429, 500, 503, 418]

const realFetch = globalThis.fetch
const realConsoleError = console.error

/** A `fetch` that answers every call with one status. */
function answering(status, body = {}) {
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init })
    return {
      ok: status >= 200 && status < 300,
      status,
      statusText: 'Stubbed',
      json: async () => body,
    }
  }
  return calls
}

beforeEach(() => {
  // The failure log line is the point of keeping the route; it is not the point here.
  console.error = () => {}
  setAuthToken(null)
  setSessionExpiredHandler(null)
})

afterEach(() => {
  globalThis.fetch = realFetch
  console.error = realConsoleError
  setAuthToken(null)
  setSessionExpiredHandler(null)
})

test('a failure says what to do, and never quotes the route at the reader', () => {
  for (const status of STATUSES) {
    const sentence = apiMessage(status)
    assert.match(sentence, /^[A-Z].*\.$/s, `${status}: not a sentence — ${sentence}`)
    assert.doesNotMatch(
      sentence,
      /failed:|\b(GET|POST|PUT|DELETE)\b|\/\w|\b[45]\d\d\b/,
      `${status}: leaks the route or the status code into the message — ${sentence}`,
    )
  }

  // The two that decide behaviour elsewhere are worth naming exactly.
  assert.match(apiMessage(401), /sign in again/i)
  assert.match(apiMessage(403), /not allowed/i)
})

test('a refused read reaches the surface as a sentence, not as a route', async () => {
  answering(403)

  await assert.rejects(
    () => getMemoryFacts(null, 'user:5', true),
    (error) => {
      assert.ok(error instanceof ApiError)
      assert.equal(error.status, 403)
      // What the panel renders.
      assert.equal(error.message, apiMessage(403))
      assert.doesNotMatch(error.message, /memory\/facts|403|Forbidden/)
      // What the log line gets to keep.
      assert.match(error.route, /^GET \/memory\/facts\?subject=user%3A5/)
      return true
    },
  )
})

test('a 401 ends the session once, however many panels hit it', async () => {
  answering(401)
  let signedOut = 0
  setAuthToken('a-twelve-hour-jwt')
  setSessionExpiredHandler(() => {
    signedOut += 1
    setAuthToken(null)
  })

  // Six panels mounting at once is the real shape of this: every one of them 401s.
  await Promise.all(
    Array.from({ length: 6 }, () => getGraph(null).catch((error) => error)),
  )

  assert.equal(signedOut, 1, 'a burst of refusals must sign out once, not six times')
  assert.equal(getAuthToken(), null, 'the dead bearer is not held after it is refused')
})

test('only a 401 ends the session — a refusal or an outage does not', async () => {
  for (const status of [403, 500, 404]) {
    answering(status)
    let signedOut = 0
    setAuthToken('a-good-jwt')
    setSessionExpiredHandler(() => {
      signedOut += 1
    })

    await getGraph(null).catch(() => {})

    assert.equal(signedOut, 0, `${status} must not sign anybody out`)
    assert.equal(getAuthToken(), 'a-good-jwt', `${status} must not drop the bearer`)
  }
})

test('a session that has already ended reports nothing further', () => {
  let signedOut = 0
  setSessionExpiredHandler(() => {
    signedOut += 1
  })

  assert.equal(reportSessionExpired(), false, 'nobody is signed in; there is nothing to end')
  assert.equal(signedOut, 0)
})

test('withholding is decided on the caller, not on the weather', () => {
  const refused = new ApiError(403, 'GET', '/memory/facts')
  const expired = new ApiError(401, 'GET', '/memory/facts')
  const broken = new ApiError(500, 'GET', '/memory/facts')
  const offline = new TypeError('Failed to fetch')

  // The memory rail withholds a card on these two: the reading is absent *for this
  // sign-in*, and a panel that renders the refusal instead is the audited defect.
  assert.equal(isAuthFailure(refused), true)
  assert.equal(isAuthFailure(expired), true)

  // And keeps the card on these: the reading is absent right now, not for this person.
  assert.equal(isAuthFailure(broken), false, 'a card must not vanish on a backend hiccup')
  assert.equal(isAuthFailure(offline), false)

  assert.equal(isExpiredSession(refused), false, 'a 403 is not a dead bearer')
  assert.equal(isExpiredSession(expired), true)
  assert.equal(statusOf(offline), 0, 'a request that never landed carries no status')
})
