/**
 * `className="truncate"` on a `<Figure>` is a class that does nothing, silently.
 *
 * `truncate` is `overflow:hidden; text-overflow:ellipsis; white-space:nowrap`, and
 * `text-overflow` renders on a *block container's* own inline content — it does not cross
 * into a flex formatting context. `Figure` is an `inline-flex`, so the class landed on
 * the wrong box: the text was cut **mid-glyph with no ellipsis**, and because the clip is
 * `overflow:hidden` it produced no document overflow either. `DeepSeek-V4-Fl`,
 * `text-embeddin` and `Llama-3.2-90B-Visi` sat on the admin dashboard at 390px like that
 * — four of six rows unreadable at 125% — and an overflow-only responsive sweep walked
 * straight past them, four times.
 *
 * That is the whole reason this is a test and not a comment: the failure has no symptom a
 * measurement was looking for. `Figure`'s `truncate` **prop** puts the clip on an inner
 * box that is blockified, where the ellipsis renders; this fails the build if the class
 * is written on a `Figure` again.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const SRC = fileURLToPath(new URL('../../src/', import.meta.url))

/** Every `.tsx` under `src/` — only JSX can carry the offending attribute. */
function sources(dir = SRC, found = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sources(path, found)
    else if (entry.endsWith('.tsx')) found.push(path)
  }
  return found
}

/** `<Figure … className="… truncate …">`, across however many lines the tag spans. */
const FIGURE_TAG = /<Figure\b[^>]*>/gs
const TRUNCATE_CLASS = /className\s*=\s*(?:"[^"]*\btruncate\b[^"]*"|\{[^}]*\btruncate\b[^}]*\})/s

test('no Figure carries `truncate` as a class — it is a prop, because the class is inert', () => {
  const files = sources()
  assert.ok(files.length > 50, `the source scan came back near-empty (${files.length} files)`)

  const offenders = []
  for (const path of files) {
    const text = readFileSync(path, 'utf8')
    for (const match of text.matchAll(FIGURE_TAG)) {
      if (TRUNCATE_CLASS.test(match[0])) {
        const line = text.slice(0, match.index).split('\n').length
        offenders.push(`${path.slice(SRC.length)}:${line}`)
      }
    }
  }

  assert.deepEqual(
    offenders,
    [],
    'Figure renders an inline-flex, so `truncate` in its className clips without an ' +
      'ellipsis and without any document overflow to measure. Pass `truncate` as a prop.',
  )
})
