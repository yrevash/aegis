#!/usr/bin/env node
/**
 * Recolour Storyset illustrations onto the Aegis blue ramp.
 *
 * **Why this file exists.** The first 46 scenes were recoloured by hand, and
 * `web/public/illustrations/CREDITS.md` recorded the substitutions as a table. A table is
 * not a procedure: when the second batch arrived it carried a *different* Storyset accent
 * (`#407BFF`, their current export default) that appears nowhere in that table, so
 * following the documented mapping would have recoloured nothing and shipped Storyset-blue
 * artwork next to Aegis-blue artwork. The mapping belongs in code that can be re-run.
 *
 * **What it touches, and what it must never touch.** Only the accent family — the blues
 * Storyset uses as its brand colour. The greys, the line work and the figures' own skin and
 * clothing tones are the drawing, not the brand; repainting them flattens scenes that were
 * composed with them. That rule is why the substitution list below is an allowlist of exact
 * hexes rather than a hue-range sweep.
 *
 *   node scripts/recolour-illustrations.mjs <source-dir> [--dry-run]
 *
 * Idempotent: a file already on the Aegis ramp contains none of the source hexes, so a
 * second run rewrites nothing. Re-running over the whole directory is safe.
 */

import { readFileSync, writeFileSync, readdirSync, existsSync } from 'node:fs'
import { join, basename } from 'node:path'

/**
 * Storyset accent -> Aegis ramp.
 *
 * The first three are the same colour in three of Storyset's set-specific exports
 * ("amico"/"pana", "rafiki", "bro"/"cuate"); `#407BFF`/`#407BF6` are their current default.
 * All five collapse onto `--blue-600` because they are all the same *role*: the one accent.
 * The remaining four are secondary accents that only the older sets use.
 */
const SUBSTITUTIONS = [
  ['#547CEF', '#1570ef'], // --blue-600  Storyset primary, "amico"/"pana"
  ['#5585F1', '#1570ef'], // --blue-600  Storyset primary, "rafiki"
  ['#4A80F9', '#1570ef'], // --blue-600  Storyset primary, "bro"/"cuate"
  ['#407BFF', '#1570ef'], // --blue-600  Storyset primary, current export default
  ['#407BF6', '#1570ef'], // --blue-600  ditto, a rounding variant in the same files
  ['#6880C8', '#175cd3'], // --blue-700
  ['#4262C7', '#0b3b8f'], // --blue-900
  ['#7D9BF5', '#60a5fa'], // --blue-400
  ['#92A9F7', '#bfdbfe'], // --blue-200
]

/** Hexes that must never survive a run — the guard that makes this checkable in CI. */
export const FORBIDDEN = SUBSTITUTIONS.map(([from]) => from)

/** Recolour one SVG's text. Returns the new text and how many substitutions landed. */
export function recolour(svg) {
  let out = svg
  let changed = 0
  for (const [from, to] of SUBSTITUTIONS) {
    // Storyset writes the same hex in both cases across the set; match either.
    const pattern = new RegExp(from.replace('#', '#'), 'gi')
    const hits = out.match(pattern)
    if (hits) {
      changed += hits.length
      out = out.replace(pattern, to)
    }
  }
  return { out, changed }
}

function main() {
  const [sourceDir, ...flags] = process.argv.slice(2)
  const dryRun = flags.includes('--dry-run')
  if (!sourceDir || !existsSync(sourceDir)) {
    console.error('usage: node scripts/recolour-illustrations.mjs <source-dir> [--dry-run]')
    process.exit(1)
  }

  const files = readdirSync(sourceDir).filter((f) => f.endsWith('.svg'))
  let touched = 0
  for (const file of files) {
    const path = join(sourceDir, file)
    const { out, changed } = recolour(readFileSync(path, 'utf8'))
    if (changed === 0) continue
    touched += 1
    if (!dryRun) writeFileSync(path, out)
    console.log(`${dryRun ? 'would recolour' : 'recoloured'}  ${basename(file)}  (${changed} swaps)`)
  }
  console.log(`\n${touched}/${files.length} file(s) ${dryRun ? 'would change' : 'changed'}.`)
}

if (import.meta.url === `file://${process.argv[1]}`) main()
