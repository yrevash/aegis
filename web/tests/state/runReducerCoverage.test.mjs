/**
 * The other half of the union parity guard: a variant with nowhere to go.
 *
 * `backend/tests/api/test_stream_union_mirror.py` catches a variant that is on the Python
 * union and missing from the TypeScript one. It cannot catch the next step of the same
 * bug — a variant present on **both** sides with no `case` in {@link runReducer}, which
 * `default: return next` swallows in silence. That is precisely the failure the mirror
 * test's own docstring cites as its reason to exist: `reflection`, `routing` and `memory`
 * were on the wire, in the reducer's input, and thrown away.
 *
 * ## Why this reads the source rather than a list
 *
 * A hand-written array of expected event types would drift the same way the mirror did —
 * the fix and the check would need editing together, and the check is exactly what you
 * forget. So both sides are derived from source at run time:
 *
 * - the union, by reading `export type StreamEvent = | A | B …` out of `stream.ts` and
 *   resolving each named interface to its `type: '…'` discriminant literal;
 * - the branches, by reading the `case '…':` labels out of `runReducer`'s own switch.
 *
 * Adding `ProbeEvent` to the union without a branch fails here with no test edited.
 *
 * Deriving from source has one failure mode of its own — a parse that quietly finds
 * nothing passes vacuously — so the last test guards the guard, the same way the mirror
 * test does. `runReducer` additionally carries a `const unhandled: never = event` in its
 * `default`, which makes the same omission a `tsc` error; this is the half that runs
 * under `npm test`, and it also catches a `case` label that names no variant at all.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const read = (relative) =>
  readFileSync(fileURLToPath(new URL(relative, import.meta.url)), 'utf8')

/** Blank out block and line comments, so prose cannot look like code. */
function stripComments(source) {
  return source
    .replace(/\/\*[\s\S]*?\*\//g, (match) => '\n'.repeat((match.match(/\n/g) ?? []).length))
    .replace(/\/\/[^\n]*/g, '')
}

/** The `type: '…'` discriminant of every interface named in the `StreamEvent` union. */
function unionVariants() {
  const source = stripComments(read('../../src/lib/stream.ts'))

  const block = /export type StreamEvent =\n((?:\s*\|\s*\w+\n)+)/.exec(source)
  assert.ok(block !== null, 'no `export type StreamEvent =` union found in src/lib/stream.ts')
  const names = [...block[1].matchAll(/\|\s*(\w+)/g)].map((m) => m[1])

  const literals = new Map()
  for (const name of names) {
    const body = new RegExp(
      `^export interface ${name} extends BaseEvent \\{([\\s\\S]*?)^\\}`,
      'm',
    ).exec(source)
    assert.ok(body !== null, `${name} is in the StreamEvent union but declares no interface`)
    const discriminant = /^\s*type:\s*'([a-z0-9_]+)'/m.exec(body[1])
    assert.ok(discriminant !== null, `${name} declares no \`type: '…'\` literal`)
    literals.set(discriminant[1], name)
  }
  return literals
}

/** The `case '…':` labels of `runReducer`'s switch. */
function reducerBranches() {
  const source = stripComments(read('../../src/state/runReducer.ts'))
  const body = /export function runReducer\([\s\S]*$/.exec(source)
  assert.ok(body !== null, 'no `export function runReducer` found in src/state/runReducer.ts')
  return new Set([...body[0].matchAll(/^\s*case '([a-z0-9_]+)':/gm)].map((m) => m[1]))
}

test('every StreamEvent variant has a runReducer branch', () => {
  const variants = unionVariants()
  const branches = reducerBranches()

  const dropped = [...variants.keys()]
    .filter((literal) => !branches.has(literal))
    .map((literal) => `${literal} (${variants.get(literal)})`)

  assert.deepEqual(
    dropped,
    [],
    'these variants reach runReducer and fall through to `default`, which discards them ' +
      'without a trace — the reflection/routing/memory bug, again',
  )
})

test('every runReducer branch names a variant that exists', () => {
  const variants = unionVariants()
  const stale = [...reducerBranches()].filter((literal) => !variants.has(literal))

  assert.deepEqual(
    stale,
    [],
    'these `case` labels match no member of the StreamEvent union — dead branches that ' +
      'read as handling something no run can emit',
  )
})

test('the parsers actually find the union and the switch', () => {
  // A check whose subject can silently become empty proves nothing when it passes.
  assert.ok(unionVariants().size >= 15, 'the StreamEvent union parse came back near-empty')
  assert.ok(reducerBranches().size >= 15, 'the runReducer switch parse came back near-empty')
})
