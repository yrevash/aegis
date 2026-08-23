/**
 * The kill switch has to actually kill the band.
 *
 * The project owner asked for framework wordmarks on the public page **and** for a way
 * to take them off at any moment. That request is only met if removal is one edit in
 * one file — not "one edit, plus delete the nav link, plus remove the now-empty
 * heading, plus notice that the section either side changed colour". A switch that is
 * nominal is worse than no switch, because it is trusted.
 *
 * So four things are asserted, and each is a way the switch could rot:
 *
 * 1. **The switch resolves.** `bandEnabled` is the one function both constants go
 *    through, so both directions are exercised directly rather than by mutating
 *    `process.env` and hoping Next.js inlines it the way the test assumed.
 * 2. **The page renders each band only through its switch.** A future edit that drops
 *    `<StandardsBand />` in unguarded would leave a constant nobody reads, which looks
 *    exactly like a working switch until somebody flips it.
 * 3. **Nothing reaches around the switch.** If any other module imported
 *    `StandardsBand`, `standardsSummary` or `lib/api/standards`, flipping the constant
 *    would leave the band's parts rendering somewhere else — or, worse, leave the
 *    public endpoint being called by a page that no longer shows the result.
 * 4. **There is no dangling anchor.** The header nav links to section ids; a nav entry
 *    pointing at `#standards` would scroll to nothing the moment the band came off.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import { STACK_BAND, STANDARDS_BAND, bandEnabled } from '../../src/components/landing/bands.config.ts'

const SRC = fileURLToPath(new URL('../../src/', import.meta.url))
const read = (relative) => readFileSync(join(SRC, relative), 'utf8')

test('the switch is off for every spelling of off, and on for everything else', () => {
  for (const off of ['off', 'OFF', 'false', '0', 'no', ' off ']) {
    assert.equal(bandEnabled(off, true), false, `"${off}" should turn the band off`)
  }
  for (const on of ['on', 'true', '1', 'yes']) {
    assert.equal(bandEnabled(on, false), true, `"${on}" should turn the band on`)
  }
})

test('an unset or blank variable leaves the constant in charge', () => {
  assert.equal(bandEnabled(undefined, true), true)
  assert.equal(bandEnabled(undefined, false), false)
  assert.equal(bandEnabled('', true), true)
  assert.equal(bandEnabled('   ', false), false)
})

test('both bands are on by default, so the page ships with them', () => {
  assert.equal(STANDARDS_BAND, true)
  assert.equal(STACK_BAND, true)
})

test('the page renders each band only behind its own switch', () => {
  const page = read('app/page.tsx')

  // Every render site of each band is guarded by its constant, on the same line.
  for (const [band, flag] of [
    ['StandardsBand', 'STANDARDS_BAND'],
    ['StackBand', 'STACK_BAND'],
  ]) {
    const sites = page.split('\n').filter((line) => line.includes(`<${band} />`))
    assert.equal(sites.length, 1, `${band} should be rendered exactly once`)
    assert.match(
      sites[0],
      new RegExp(`${flag}\\s*\\?\\s*<${band} />\\s*:\\s*null`),
      `${band} must render only when ${flag} is true`,
    )
  }
})

/** Every `.ts`/`.tsx` file under `web/src`, so nothing can hide from the sweep. */
function sources(directory = SRC) {
  const found = []
  for (const entry of readdirSync(directory)) {
    const path = join(directory, entry)
    if (statSync(path).isDirectory()) found.push(...sources(path))
    else if (/\.tsx?$/.test(entry)) found.push(path)
  }
  return found
}

test('nothing outside the band imports its parts, so one switch is the whole removal', () => {
  const OWNED = ['landing/StandardsBand', 'landing/standardsSummary', 'lib/api/standards']
  // The band's own parts may reference each other; `page.tsx` is where the switch is.
  const ALLOWED = [
    'app/page.tsx',
    'components/landing/StandardsBand.tsx',
    'components/landing/standardsSummary.ts',
  ]

  for (const file of sources()) {
    const relative = file.slice(SRC.length)
    if (ALLOWED.includes(relative)) continue
    const body = readFileSync(file, 'utf8')
    for (const owned of OWNED) {
      const name = owned.split('/').at(-1)
      assert.ok(
        !new RegExp(`from '[^']*(?:${owned}|\\./${name})'`).test(body),
        `${relative} imports ${owned}; turning the standards band off would no longer remove it`,
      )
    }
  }
})

test('no nav link points at a section that can be switched off', () => {
  const header = read('components/landing/LandingHeader.tsx')
  for (const anchor of ['#standards', '#stack']) {
    assert.ok(
      !header.includes(anchor),
      `the header links to ${anchor}, which becomes a dangling anchor when the band is off`,
    )
  }
})
