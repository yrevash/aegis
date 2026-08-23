/**
 * Every technology claim on the public page must resolve to a real file.
 *
 * A stack list is the easiest thing on a landing page to pad: nothing executes it, the
 * names are all real products, and a capability the team *meant* to wire up looks
 * identical to one that is. This is the same defect the compliance table has, and
 * `backend/tests/api/test_compliance.py` answers it the same way — by resolving every
 * reference against the real repository on each run.
 *
 * So each claim's `path` is opened from disk. A module that is renamed, merged or
 * deleted breaks this test rather than leaving a marketing page quietly claiming
 * something that moved. Each `proof` is resolved twice over: the file must exist, and
 * the test function it names must actually be defined in it.
 *
 * Two shape rules come with it, because a claim can also rot without moving:
 *
 * - **No duplicate mechanisms.** Two rows saying the same thing in different words is
 *   how a list of eight becomes a list of thirteen without gaining anything.
 * - **No benchmark figures.** The mechanisms are architectural facts, which a reader
 *   can check by opening the file. A number ("+12.9 pp MRR") is a measurement, it is
 *   true only of one corpus on one day, and this page has nowhere to state the
 *   conditions — so it belongs in the eval report, not in a wordmark grid.
 */

import assert from 'node:assert/strict'
import { existsSync, readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { gridTail } from '../../src/components/landing/gridTail.ts'
import { STACK_CLAIMS } from '../../src/components/landing/stackClaims.ts'

const ROOT = fileURLToPath(new URL('../../../', import.meta.url))

test('every claimed mechanism names a file that exists in this repository', () => {
  for (const claim of STACK_CLAIMS) {
    assert.ok(
      existsSync(new URL(claim.path, `file://${ROOT}`)),
      `${claim.mark} claims ${claim.path}, which is not in the repository`,
    )
  }
})

test('every proof names a test that is really defined in the file it points at', () => {
  const proven = STACK_CLAIMS.filter((claim) => claim.proof != null)
  assert.ok(proven.length > 0, 'at least one claim should carry a test, or the field is decoration')

  for (const claim of proven) {
    const [file, node] = claim.proof.split('::')
    const url = new URL(file, `file://${ROOT}`)
    assert.ok(existsSync(url), `${claim.mark}'s proof names ${file}, which does not exist`)
    assert.ok(
      new RegExp(`def ${node}\\b`).test(readFileSync(url, 'utf8')),
      `${file} does not define ${node}`,
    )
  }
})

test('no two rows make the same claim', () => {
  const marks = STACK_CLAIMS.map((claim) => claim.mark)
  assert.equal(new Set(marks).size, marks.length, 'a duplicated wordmark is padding')

  const mechanisms = STACK_CLAIMS.map((claim) => claim.mechanism.toLowerCase())
  assert.equal(new Set(mechanisms).size, mechanisms.length, 'a duplicated mechanism is padding')
})

test('no mechanism quotes a benchmark figure', () => {
  for (const claim of STACK_CLAIMS) {
    assert.ok(
      !/\d/.test(claim.mechanism),
      `"${claim.mechanism}" carries a figure; a measurement needs its conditions, and this grid has nowhere to state them`,
    )
  }
})

test('however many claims there are, the grid never ends in a tinted hole', () => {
  // The grid paints its rules by showing `bg-border` through `gap-px`, so an unfilled
  // trailing cell is a visible rectangle that reads as a card which failed to render.
  // Adding or removing a claim must not reintroduce it, at either breakpoint.
  for (let count = 1; count <= 24; count += 1) {
    const tail = gridTail(count)
    const sm = /sm:col-span-(\d)/.exec(tail)
    const lg = /lg:col-span-(\d)/.exec(tail)

    // Both breakpoints must state their span. A missing `lg` inherits `sm`'s, which is
    // how three cells came to take two of three columns and leave a bigger hole.
    assert.ok(sm, `${count} cells: no sm span`)
    assert.ok(lg, `${count} cells: no lg span — it would inherit sm's and wrap`)

    assert.equal((count - 1 + Number(sm[1])) % 2, 0, `${count} cells leave a hole in two columns`)
    assert.equal((count - 1 + Number(lg[1])) % 3, 0, `${count} cells leave a hole in three columns`)
  }
})

test('every mechanism is one line, not a paragraph', () => {
  for (const claim of STACK_CLAIMS) {
    assert.ok(
      claim.mechanism.length <= 80,
      `${claim.mark}'s line is ${claim.mechanism.length} characters — the page has a text-bomb problem`,
    )
    assert.ok(!claim.mechanism.includes('.'), `${claim.mark}'s line is a sentence, not a label`)
  }
})
