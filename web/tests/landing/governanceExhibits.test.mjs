/**
 * The two refusals must stay *stated* while staying *short*.
 *
 * This section was rewritten from twelve paragraphs into two diagrams, and that
 * rewrite has exactly two ways to go wrong later — one in each direction — so
 * those are the two tests.
 *
 * **Cutting further can delete a claim.** The prose is gone, which means every
 * remaining word is load-bearing: the risk tier the gate fires on, the two
 * outcomes an approval can have, and the three pieces of Postgres state that
 * make the tenant boundary real. A trim that removes one of those leaves a
 * picture that still *looks* like an argument while no longer making it, and
 * nothing on screen would appear broken.
 *
 * **Editing it back can restore the paragraphs.** The complaint that produced
 * this rewrite was made twice, and the failure mode is a slow one: a sentence
 * added to a caption, a gloss grown into an explanation, and in three edits the
 * section is prose again. So the visible copy has a hard ceiling, well above the
 * current count and well below where it started.
 *
 * The check is on the source rather than on a render because the section is a
 * server component with no state — there is nothing a renderer would reveal that
 * the file does not already say, and a DOM harness would only add a dependency
 * for the privilege of asserting the same strings.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const SOURCE = readFileSync(
  fileURLToPath(new URL('../../src/components/landing/GovernanceSection.tsx', import.meta.url)),
  'utf8',
)

/**
 * The section with its comments removed — what a reader can actually see.
 *
 * The docblock explaining the rewrite is longer than the section it explains,
 * which is correct for a comment and would wreck a word count, so both comment
 * forms come out before anything is counted.
 */
const VISIBLE = SOURCE.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ')

test('every claim the prose used to carry is still on the page', () => {
  const CLAIMS = [
    // The gate fires on a declared tier, and says so.
    'risk: low',
    'risk: high',
    'stops for a person',
    "declared risk tier, never on model confidence",
    // Approving and rejecting are a pair; showing one without the other is the
    // half-truth this section exists to avoid.
    'every call in the batch runs',
    'none of them run',
    // The wall is Postgres state, not a prompt. All three pieces, by name.
    'FORCE ROW LEVEL SECURITY',
    'NOSUPERUSER NOBYPASSRLS',
    "set_config('app.tenant_id')",
    'never returned',
  ]

  const missing = CLAIMS.filter((claim) => !VISIBLE.includes(claim))
  assert.deepEqual(
    missing,
    [],
    'the diagrams are the only carrier of these claims now — a trim that drops ' +
      'one leaves a picture that no longer makes the argument it appears to make',
  )
})

test('the section does not grow back into paragraphs', () => {
  // Every run of prose: JSX text nodes and the string-valued props that render.
  // Counting the whole file would count class names, so this counts what a
  // reader reads — the `lead`, `title` and `caption` props, and the text
  // between tags.
  const props = [...VISIBLE.matchAll(/\b(?:lead|title|caption|term|gloss)=?[:\s]*["'`]([^"'`]+)["'`]/g)]
  const between = [...VISIBLE.matchAll(/>\s*([A-Za-z][^<>{}"']{4,})\s*</g)]

  const words = [...props, ...between]
    .map((match) => match[1].trim())
    .join(' ')
    .split(/\s+/)
    .filter(Boolean).length

  // Measured at 111 after the rewrite, from roughly 300 before it. The ceiling
  // leaves room for a real claim to be added and none for a restored paragraph.
  assert.ok(
    words <= 150,
    `the section renders ${words} words of copy; the two refusals are drawn, and ` +
      'above 150 words they are being described again (the rewrite that removed ' +
      'the twelve paragraphs measured 111)',
  )
})
