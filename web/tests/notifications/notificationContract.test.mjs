/**
 * The hand-written `NotificationRow` must not drift from the generated schema.
 *
 * This one is worth pinning even by this repo's standards, because the two halves were
 * written **at the same time by different people against a written contract** rather than
 * one being derived from the other. That is the arrangement in which a field gets renamed
 * on one side and nobody finds out until the bell renders a row of blanks — and it fails
 * silently, because every field the browser reads off a JSON object it did not get is
 * `undefined`, not an error.
 *
 * `web/src/lib/api/generated/schema.d.ts` comes from the backend's own OpenAPI, which
 * comes from its Pydantic models. It is the authority. This reads both files as text —
 * the generated one is a `.d.ts` with no runtime representation — and compares the field
 * names in both directions, for the reason `guardVerdict.test.mjs` gives: a missing field
 * is a blank row, and an extra one implies a backend behaviour that does not exist.
 *
 * If the generated schema has no `NotificationRow` at all, that is not a pass — it means
 * the routes have been withdrawn or the client has not been regenerated, and the test
 * says which.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8')

/** The body of a braced block starting at `open`, balanced. */
function block(source, open) {
  const start = source.indexOf(open)
  if (start === -1) return null
  let depth = 0
  for (let i = start + open.length - 1; i < source.length; i += 1) {
    if (source[i] === '{') depth += 1
    else if (source[i] === '}') {
      depth -= 1
      if (depth === 0) return source.slice(start + open.length, i)
    }
  }
  return null
}

/** Field names declared in a TypeScript object/interface body, comments ignored. */
function fields(body) {
  const bare = body
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/\/\/[^\n]*/g, '')
  return new Set([...bare.matchAll(/^\s*([a-z_][a-z0-9_]*)\??:/gim)].map((m) => m[1]))
}

test('NotificationRow in notifications.ts matches the backend’s generated schema', () => {
  const generatedBody = block(read('../../src/lib/api/generated/schema.d.ts'), 'NotificationRow: {')
  assert.ok(
    generatedBody,
    'the generated schema declares no NotificationRow — regenerate the client (npm run gen:api), ' +
      'or the notification routes have been withdrawn from the backend',
  )
  const handBody = block(
    read('../../src/lib/api/notifications.ts'),
    'export interface NotificationRow {',
  )
  assert.ok(handBody, 'notifications.ts declares no NotificationRow')

  const generated = fields(generatedBody)
  const hand = fields(handBody)
  assert.ok(generated.size >= 8, `the schema scan came back thin (${generated.size} fields)`)

  const missing = [...generated].filter((f) => !hand.has(f))
  const extra = [...hand].filter((f) => !generated.has(f))
  assert.deepEqual(missing, [], `notifications.ts is missing field(s) the backend sends: ${missing}`)
  assert.deepEqual(extra, [], `notifications.ts declares field(s) the backend never sends: ${extra}`)

  // The four the runtime guard keys on, and the one the badge is computed from. Named
  // explicitly so a rename of any of them fails here rather than in a silent blank row.
  for (const field of ['id', 'kind', 'severity', 'title', 'created_at', 'read_at', 'href']) {
    assert.ok(generated.has(field), `the backend no longer sends ${field}`)
  }
})

test('the unread count travels beside the rows, not inside them', () => {
  // The badge is `unread`, counted server-side over the whole scope. If that field ever
  // stops arriving, `notificationReducer` would read `undefined` and the bell would show
  // NaN — so the response shape is pinned too.
  const body = block(read('../../src/lib/api/generated/schema.d.ts'), 'NotificationsResponse: {')
  assert.ok(body, 'the generated schema declares no NotificationsResponse')
  const declared = fields(body)
  assert.ok(declared.has('rows'), 'NotificationsResponse no longer carries rows')
  assert.ok(declared.has('unread'), 'NotificationsResponse no longer carries the unread count')
})
