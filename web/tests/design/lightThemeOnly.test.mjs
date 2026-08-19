/**
 * The console is light theme only, and that has to be checkable.
 *
 * "No `dark:`" is a standing design rule, and standing rules decay quietly: two
 * `dark:border-t-white/5` variants survived in `KpiHero.tsx` and `BentoGrid.tsx` long
 * enough for two separate audits to find them. Nothing failed — a dark variant on a page
 * that never goes dark simply does nothing, which is exactly why it is invisible to
 * every other kind of test and why it will come back the next time a component is
 * pasted in from somewhere with a dark mode.
 *
 * So the rule is read off the source, not off a list. Adding one anywhere under `src/`
 * fails here with no test edited.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const SRC = fileURLToPath(new URL('../../src/', import.meta.url))

/** Every file under `src/` that can carry a class name. */
function sources(dir = SRC, found = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) {
      sources(path, found)
    } else if (/\.(tsx?|css)$/.test(entry)) {
      found.push(path)
    }
  }
  return found
}

test('no Tailwind dark: variant survives anywhere under src/', () => {
  const files = sources()
  // A scan whose subject can silently become empty proves nothing when it passes.
  assert.ok(files.length > 100, `the source scan came back near-empty (${files.length} files)`)

  const offenders = []
  for (const path of files) {
    const source = readFileSync(path, 'utf8')
    for (const [index, line] of source.split('\n').entries()) {
      // The Tailwind variant, not the word: `dark` in prose or in a token name is fine.
      if (/\bdark:[a-z[-]/.test(line)) {
        offenders.push(`${path.slice(SRC.length)}:${index + 1}`)
      }
    }
  }

  assert.deepEqual(
    offenders,
    [],
    'the console is light theme only — a dark: variant is dead weight that reads as ' +
      'a dark mode somebody can turn on',
  )
})
