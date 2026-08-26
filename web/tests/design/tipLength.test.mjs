/**
 * An `InfoTip` is a disclosure, not a place to keep an essay.
 *
 * DESIGN.md §4 sends prose off the face of a screen and behind a trigger, and the
 * previous passes did exactly that — into **184 InfoTips carrying 3,385 words**,
 * 56 of them 25 words or more and the worst 95. `03-AI-TEAM-PASS.md` had already
 * named the result: *"a text bomb with a lid on it"*. Relocation is not deletion,
 * and a screen that measures clean still reads heavy when every ⓘ on it opens a
 * paragraph.
 *
 * So there is a ceiling, and it is asserted off the source rather than off a list.
 * **40 words.** Over it, the order of cuts is: the sentence that restates the label
 * the tip hangs on, then the sentence that gives advice, and never the sentence
 * naming a file, a route, a table, a rail or a figure — that one is the glass box
 * and it is the product. If what is left is still over 40 words it is documentation
 * and belongs in `docs/`, with the tip naming the mechanism and linking out.
 *
 * ## Scope: four directories, on purpose
 *
 * `console/` and `guardrail/` are excluded here only because they are the largest
 * trees and are being cut on their own schedule; the rule is the same everywhere,
 * and adding a directory to `ROOTS` is the whole of extending it.
 *
 * ## What the count can and cannot see
 *
 * The measure is static: the literal text between the tags, plus — for a `{…}`
 * expression — the longest string literal inside it, because a conditional renders
 * one branch. A tip whose body is a server value (`{residency.note}`,
 * `{suite.summary}`) therefore measures zero and is not policed here; the length
 * of those is the API's to answer for, not this file's.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const COMPONENTS = fileURLToPath(new URL('../../src/components/', import.meta.url))

/** The four trees this ceiling is enforced on. Adding one extends the rule. */
const ROOTS = ['redteam', 'graph', 'compliance', 'documents']

const MAX_WORDS = 40

function sources(dir, found = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sources(path, found)
    else if (/\.tsx?$/.test(entry)) found.push(path)
  }
  return found
}

/** Word count of a `{…}` expression: the longest branch it can render. */
function longestLiteral(expression) {
  let longest = ''
  for (const [, quoted] of expression.matchAll(/'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"/g)) {
    const text = quoted ?? ''
    if (text.length > longest.length) longest = text
  }
  return longest
}

/** The visible text of one InfoTip body, JSX markup and braces resolved. */
function tipWords(body) {
  let text = ''
  let rest = body
  while (rest.length > 0) {
    const open = rest.indexOf('{')
    if (open < 0) {
      text += ` ${rest}`
      break
    }
    text += ` ${rest.slice(0, open)}`
    // Walk to the matching brace so a nested template or object does not end it early.
    let depth = 0
    let index = open
    for (; index < rest.length; index += 1) {
      if (rest[index] === '{') depth += 1
      else if (rest[index] === '}') {
        depth -= 1
        if (depth === 0) break
      }
    }
    text += ` ${longestLiteral(rest.slice(open + 1, index))}`
    rest = rest.slice(index + 1)
  }
  // Tags and HTML entities are markup, not words.
  return text
    .replace(/<[^>]*>/g, ' ')
    .replace(/&[a-z]+;/g, '')
    .trim()
    .split(/\s+/)
    .filter(Boolean).length
}

/** Every `<InfoTip …>…</InfoTip>` under the policed roots, with its position. */
function tips() {
  const found = []
  for (const root of ROOTS) {
    for (const path of sources(join(COMPONENTS, root))) {
      const source = readFileSync(path, 'utf8')
      for (const match of source.matchAll(/<InfoTip\b/g)) {
        const openEnd = source.indexOf('>', match.index)
        const close = source.indexOf('</InfoTip>', openEnd)
        if (close < 0) continue // self-closing: no body to measure
        found.push({
          where: `${path.slice(COMPONENTS.length)}:${source.slice(0, match.index).split('\n').length}`,
          words: tipWords(source.slice(openEnd + 1, close)),
        })
      }
    }
  }
  return found
}

test(`no InfoTip on the policed screens runs past ${MAX_WORDS} words`, () => {
  const found = tips()
  // A scan whose subject can silently empty out proves nothing when it passes — a
  // renamed directory or a changed component name would report a clean sweep of
  // nothing. Measured at 13 across the four roots when this was written.
  assert.ok(found.length > 8, `the InfoTip scan came back near-empty (${found.length} tips)`)

  const offenders = found
    .filter((tip) => tip.words > MAX_WORDS)
    .map((tip) => `${tip.where} — ${tip.words} words`)

  assert.deepEqual(
    offenders,
    [],
    `an InfoTip past ${MAX_WORDS} words is documentation in a hover. Cut the sentence ` +
      'that restates the label, then the one giving advice; keep the one naming a ' +
      'file, route, table, rail or figure. Still over? It belongs in docs/.',
  )
})
