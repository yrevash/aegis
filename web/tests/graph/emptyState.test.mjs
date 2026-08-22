/**
 * The graph's empty state must not tell an operator to run a query.
 *
 * It did, in both halves of the screen — "run a query to build an evidence subgraph" and
 * "Run a query to populate it" — and the instruction was false. `viewOf` only ever
 * *narrows* the view to `state.touchedNodes`, which are a subset of what `GET /graph`
 * already returned; across a whole audited run no retrieval event carried a touched node
 * at all. What fills the view is the ingest pipeline's `graph` stage, which projects
 * entities into Neo4j (its stage facts count them as `projected_entities`).
 *
 * The rule is read off the source rather than a list, because the copy is the defect: a
 * paste of the old sentence anywhere in this file fails here with no test edited.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const VIEW = fileURLToPath(new URL('../../src/components/graph/GraphView.tsx', import.meta.url))

/** The rendered copy: JSX text and string literals, with the comments stripped out. */
function copy(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

test('no empty state instructs the reader to run a query', () => {
  const text = copy(readFileSync(VIEW, 'utf8'))
  assert.ok(text.length > 1000, 'the source scan came back near-empty')
  assert.doesNotMatch(
    text,
    /[Rr]un a query to (populate|build)/,
    'an empty state is instructing the reader to do something that cannot fill it',
  )
})

test('the empty state names what actually fills the view', () => {
  const text = copy(readFileSync(VIEW, 'utf8'))
  // Ingestion, not querying — and both empty states must say so, because a reader who
  // lands on either one is asking the same question.
  const mentions = text.match(/[Dd]ocument ingestion/g) ?? []
  assert.ok(
    mentions.length >= 2,
    `both empty states must name ingestion as the source; found ${mentions.length}`,
  )
  // And it must not overclaim: an empty payload is also what an unreadable graph store
  // produces, because the route falls back to the in-process slice when Neo4j is
  // unreachable. Verified live: Neo4j held 122 nodes while `GET /graph` returned none.
  assert.match(text, /graph store is unreadable/)
})
