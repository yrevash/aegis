/**
 * `stream.ts`'s hand-written unions must not drift from the generated schema.
 *
 * `GuardVerdict` did. `web/src/lib/api/generated/schema.d.ts` — derived from the backend's
 * own OpenAPI, which is derived from the Pydantic models — declares four values:
 *
 *     GuardVerdict: "pass" | "block" | "redact" | "flag"
 *
 * `web/src/lib/stream.ts` declared three. `flag` is the non-blocking advisory an
 * off-topic or ungrounded answer earns, and `aegis.guardrails.pipeline` really emits it,
 * so a `flag` on the wire was a value TypeScript had been told could not exist.
 * `GuardrailReveal` indexed `Record<GuardVerdict, …>` with it, got `undefined`, read
 * `.icon` off that, and the error boundary replaced the entire console the moment anyone
 * opened the Trace tab on a settled run.
 *
 * Nothing caught it: the compiler was satisfied (the narrower union type-checks fine),
 * and no test compared the two declarations. That is the whole failure mode of a
 * hand-maintained copy of a generated type — it fails silently, and only at runtime, and
 * only on the path that happens to receive the missing value.
 *
 * This asserts the copy still matches its source. It reads both files as text rather than
 * importing them, because the generated file is a `.d.ts` with no runtime representation.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf8')

/** Pull a string-literal union's members out of a TypeScript declaration. */
function members(source, pattern) {
  const match = source.match(pattern)
  assert.ok(match, `could not find the declaration matching ${pattern}`)
  return new Set([...match[1].matchAll(/["']([a-z_]+)["']/g)].map((m) => m[1]))
}

test('GuardVerdict in stream.ts matches the generated schema exactly', () => {
  const generated = members(
    read('../../src/lib/api/generated/schema.d.ts'),
    /GuardVerdict:\s*([^;]+);/,
  )
  const handWritten = members(
    read('../../src/lib/stream.ts'),
    /export type GuardVerdict\s*=\s*([^\n]+)/,
  )

  // Both directions matter. A missing value crashes at runtime (this is what shipped);
  // an extra one is dead code that implies a backend behaviour that does not exist.
  const missing = [...generated].filter((v) => !handWritten.has(v))
  const extra = [...handWritten].filter((v) => !generated.has(v))

  assert.deepEqual(missing, [], `stream.ts is missing verdict(s) the backend can send: ${missing}`)
  assert.deepEqual(extra, [], `stream.ts declares verdict(s) the backend never sends: ${extra}`)
  assert.ok(generated.has('flag'), 'the regression this test exists for: flag must be present')
})
