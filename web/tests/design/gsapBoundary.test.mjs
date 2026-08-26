/**
 * GSAP is the one animation in this console that `prefers-reduced-motion` cannot reach,
 * and that has to be checkable.
 *
 * `globals.css` zeroes `animation-*` and `transition-*` on `*` under `reduce`. GSAP writes
 * `transform` into the element's inline `style` attribute on every frame, so that rule is
 * not merely weak against it — it is **inapplicable**. Measured in Chromium: under
 * `reducedMotion: 'reduce'`, a `@keyframes` element had already snapped to its 300px end
 * state while a `gsap.to(…, {x: 300, duration: 2})` element sat at 105px — exactly where it
 * sat with reduced motion off.
 *
 * So every tween lives inside `useGSAP` (StrictMode is on, and a raw `useEffect` tween
 * survives the double-mount and fights its own second copy) **and** inside a
 * `gsap.matchMedia()` block carrying an explicit `(prefers-reduced-motion: reduce)`
 * conditional, which is the only reduced-motion boundary this class of animation has.
 *
 * The failure this closes is silent in every other kind of test: the animation works, the
 * page looks right, the CSS audit passes, and a viewer who asked for stillness gets none.
 * Without this test the boundary decays the first time somebody writes a one-line
 * `gsap.to()` in a `useEffect` because it was faster.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const SRC = fileURLToPath(new URL('../../src/', import.meta.url))

/** Every file under `src/` that could import an animation library. */
function sources(dir = SRC, found = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sources(path, found)
    else if (/\.tsx?$/.test(entry)) found.push(path)
  }
  return found
}

/** The files that import gsap at all, with their text. */
function gsapFiles() {
  const hits = []
  for (const file of sources()) {
    const text = readFileSync(file, 'utf8')
    if (/from\s+['"]gsap(\/|['"])/.test(text) || /from\s+['"]@gsap\/react['"]/.test(text)) {
      hits.push({ file: file.replace(SRC, 'src/'), text })
    }
  }
  return hits
}

test('every file that imports gsap uses useGSAP, never a bare effect', () => {
  const files = sources()
  // Anti-vacuity: a scan that walks nothing passes everything.
  assert.ok(files.length > 50, `expected to scan >50 files, saw ${files.length}`)

  const users = gsapFiles()
  // Second guard: if the import path changes, this test must fail loudly rather than
  // silently policing an empty set for ever.
  assert.ok(
    users.length > 0,
    'no file imports gsap — either the boundary is unused or the import pattern changed',
  )

  const offenders = users
    .filter(({ text }) => !text.includes('useGSAP'))
    .map(({ file }) => `${file} — imports gsap without useGSAP`)

  assert.deepEqual(offenders, [], `bare GSAP usage:\n  ${offenders.join('\n  ')}`)
})

test('every gsap tween sits behind a reduced-motion conditional', () => {
  const offenders = []
  for (const { file, text } of gsapFiles()) {
    if (!text.includes('gsap.matchMedia')) {
      offenders.push(`${file} — no gsap.matchMedia()`)
      continue
    }
    if (!text.includes('prefers-reduced-motion: reduce')) {
      offenders.push(`${file} — matchMedia without a (prefers-reduced-motion: reduce) conditional`)
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `GSAP outside the reduced-motion boundary:\n  ${offenders.join('\n  ')}`,
  )
})

test('the reduce branch arrives rather than returning empty-handed', () => {
  // A tween that hides before it reveals, plus an early return under `reduce`, is an
  // element that never appears — an accessibility preference that blanks the page. The
  // reduce branch must set a final state.
  const offenders = []
  for (const { file, text } of gsapFiles()) {
    if (!text.includes('gsap.matchMedia')) continue
    if (!/gsap\.set\(/.test(text)) {
      offenders.push(`${file} — no gsap.set() to establish the reduced-motion end state`)
    }
  }
  assert.deepEqual(
    offenders,
    [],
    `reduce branch may leave content hidden:\n  ${offenders.join('\n  ')}`,
  )
})

test('gsap selectors are scoped to their own subtree', () => {
  // Without `{ scope }`, a selector string inside useGSAP matches the whole document and
  // one component animates another's children.
  const offenders = []
  for (const { file, text } of gsapFiles()) {
    if (!text.includes('useGSAP')) continue
    if (!/\{\s*scope\s*\}/.test(text)) {
      offenders.push(`${file} — useGSAP without { scope }`)
    }
  }
  assert.deepEqual(offenders, [], `unscoped GSAP:\n  ${offenders.join('\n  ')}`)
})
